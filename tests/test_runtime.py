import unittest
import sys
import types


try:
    import flask_socketio  # noqa: F401
except ModuleNotFoundError:
    fake_socketio = types.ModuleType("flask_socketio")

    class FakeSocketIO:
        def __init__(self, *args, **kwargs):
            self.handlers = {}
            self.emitted = []

        def on(self, event):
            def decorator(func):
                self.handlers[event] = func
                return func

            return decorator

        def emit(self, event, payload=None):
            self.emitted.append((event, payload))

    fake_socketio.SocketIO = FakeSocketIO
    sys.modules["flask_socketio"] = fake_socketio

from lidaclips.index import ClipIndex
from lidaclips.runtime import Runtime
from lidaclips.settings import Settings


class FakeRuntimeService:
    def __init__(self):
        self.sync_calls = 0

    def sync_once(self):
        self.sync_calls += 1
        return {"downloaded": 1}

    def collect_planned_targets(self):
        return []


class RuntimeControlTests(unittest.TestCase):
    def make_runtime(self):
        index = ClipIndex(":memory:")
        service = FakeRuntimeService()
        settings = Settings(api_key="client-secret", sync_schedule=[])
        runtime = Runtime(settings, index, service)
        return runtime, index, service

    def test_sync_once_skips_service_when_sync_is_paused(self):
        runtime, index, service = self.make_runtime()
        index.set_sync_paused(True)

        runtime.sync_once()

        self.assertEqual(service.sync_calls, 0)
        self.assertEqual(runtime.sync_status, "paused")
        self.assertEqual(runtime.last_summary["skipped_paused"], 1)

    def test_sync_once_runs_service_when_sync_is_resumed(self):
        runtime, index, service = self.make_runtime()
        index.set_sync_paused(False)

        runtime.sync_once()

        self.assertEqual(service.sync_calls, 1)
        self.assertEqual(runtime.sync_status, "complete")
        self.assertEqual(runtime.last_summary["downloaded"], 1)

    def test_runtime_reports_sync_running_to_control_api(self):
        runtime, _index, _service = self.make_runtime()
        runtime.sync_status = "running"

        response = runtime.app.test_client().get("/api/v1/control", headers={"X-Api-Key": "client-secret"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["sync_running"])

    def test_settings_payload_hides_api_key_until_requested(self):
        runtime, _index, _service = self.make_runtime()

        self.assertNotIn("api_key", runtime._settings_payload())

        runtime.socketio.handlers["load_api_key"]()

        self.assertIn(("api_key_loaded", {"api_key": "client-secret"}), runtime.socketio.emitted)


if __name__ == "__main__":
    unittest.main()
