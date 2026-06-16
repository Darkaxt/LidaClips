import os
import tempfile
import unittest

from lidaclips.index import ClipIndex
from lidaclips.models import ClipTarget
from lidaclips.web import create_app


class FakeHealthService:
    def health_check(self):
        return {
            "status": "ok",
            "checks": {
                "database": {"ok": True},
                "staging": {"ok": True},
                "clips": {"ok": True},
                "lidarr": {"ok": True},
                "navidrome": {"ok": True},
            },
        }


class FakeQueueService(FakeHealthService):
    def __init__(self, targets):
        self.targets = targets
        self.collect_calls = 0

    def collect_planned_targets(self):
        self.collect_calls += 1
        return self.targets


class FakeRuntime:
    def __init__(self, targets=None, sync_status="idle"):
        self.last_targets = list(targets or [])
        self.sync_status = sync_status


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.index = ClipIndex(":memory:")
        self.clip_path = os.path.join(self.temp_dir.name, "clip.mp4")
        with open(self.clip_path, "wb") as handle:
            handle.write(b"video")
        self.target = ClipTarget(
            lidarr_track_id=42,
            artist_id=1,
            album_id=10,
            artist="The Example Band",
            album="Neon Nights",
            album_year=2020,
            title="Bright Lights",
            track_number="1",
            absolute_track_number=1,
            duration=240,
            source_file_path="/music/The Example Band/Neon Nights/01 - Bright Lights.flac",
        )
        self.index.upsert_track(self.target, navidrome_song_id="nav-song-42")
        self.clip_id = self.index.record_clip(
            lidarr_track_id=42,
            video_id="abc123",
            source_url="https://www.youtube.com/watch?v=abc123",
            title="The Example Band - Bright Lights (Official Music Video)",
            file_path=self.clip_path,
            mime_type="video/mp4",
            score=91.0,
            evidence={"official": True},
        )
        self.app = create_app(self.index, api_key="secret", service=FakeHealthService())
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def headers(self):
        return {"X-Api-Key": "secret"}

    def test_ping(self):
        response = self.client.get("/api/v1/ping")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_health_requires_api_key_and_returns_dependency_checks(self):
        unauthorized = self.client.get("/api/v1/health")
        self.assertEqual(unauthorized.status_code, 401)

        response = self.client.get("/api/v1/health", headers=self.headers())
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["checks"]["database"]["ok"])

    def test_search_clips_and_track_lookup_require_api_key(self):
        unauthorized = self.client.get("/api/v1/clips?artist=Example")
        self.assertEqual(unauthorized.status_code, 401)

        response = self.client.get("/api/v1/clips?artist=Example&track=Bright", headers=self.headers())
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["clips"]), 1)
        self.assertEqual(payload["clips"][0]["lidarr_track_id"], 42)
        self.assertEqual(payload["clips"][0]["file_name"], "clip.mp4")

        track_response = self.client.get("/api/v1/tracks/42/clip", headers=self.headers())
        self.assertEqual(track_response.status_code, 200)
        self.assertEqual(track_response.get_json()["clip"]["video_id"], "abc123")

    def test_dashboard_requires_api_key_and_returns_public_clip_payload(self):
        unauthorized = self.client.get("/api/v1/dashboard")
        self.assertEqual(unauthorized.status_code, 401)

        self.index.set_sync_paused(True)
        response = self.client.get("/api/v1/dashboard", headers=self.headers())
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["active_clips"], 1)
        self.assertEqual(payload["official_clips"], 1)
        self.assertEqual(payload["fallback_clips"], 0)
        self.assertTrue(payload["sync_paused"])
        self.assertIn("created_at", payload["recent_clips"][0])
        self.assertEqual(payload["recent_clips"][0]["file_name"], "clip.mp4")
        self.assertNotIn("file_path", payload["recent_clips"][0])

    def test_dashboard_uses_cached_queue_without_collecting_live_lidarr(self):
        queue_service = FakeQueueService([self.target])
        app = create_app(self.index, api_key="secret", service=queue_service)
        app.config["LIDACLIPS_RUNTIME"] = FakeRuntime([self.target])
        client = app.test_client()

        response = client.get("/api/v1/dashboard?include_queue=true", headers=self.headers())
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(queue_service.collect_calls, 0)
        self.assertEqual(
            [
                {
                    "lidarr_track_id": 42,
                    "artist": "The Example Band",
                    "album": "Neon Nights",
                    "track": "Bright Lights",
                    "title": "Bright Lights",
                    "duration": 240,
                    "status": "queued",
                }
            ],
            payload["download_queue"],
        )
        self.assertNotIn("source_file_path", payload["download_queue"][0])

    def test_dashboard_queue_falls_back_to_bounded_db_preview(self):
        missing_target = ClipTarget(
            lidarr_track_id=43,
            artist_id=1,
            album_id=10,
            artist="The Example Band",
            album="Neon Nights",
            album_year=2020,
            title="City Glow",
            track_number="2",
            absolute_track_number=2,
            duration=180,
            source_file_path="/music/The Example Band/Neon Nights/02 - City Glow.flac",
        )
        self.index.upsert_track(missing_target)
        queue_service = FakeQueueService([missing_target])
        app = create_app(self.index, api_key="secret", service=queue_service)
        client = app.test_client()

        response = client.get("/api/v1/dashboard?include_queue=true&queue_limit=1", headers=self.headers())
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(queue_service.collect_calls, 0)
        self.assertEqual(len(payload["download_queue"]), 1)
        self.assertEqual(payload["download_queue"][0]["lidarr_track_id"], 43)
        self.assertEqual(payload["download_queue"][0]["status"], "missing")

    def test_control_requires_api_key_and_toggles_sync_pause(self):
        unauthorized = self.client.get("/api/v1/control")
        self.assertEqual(unauthorized.status_code, 401)

        initial = self.client.get("/api/v1/control", headers=self.headers())
        self.assertEqual(initial.status_code, 200)
        self.assertFalse(initial.get_json()["sync_paused"])
        self.assertFalse(initial.get_json()["sync_running"])

        paused = self.client.post("/api/v1/control", json={"sync_paused": True}, headers=self.headers())
        self.assertEqual(paused.status_code, 200)
        self.assertTrue(paused.get_json()["sync_paused"])
        self.assertTrue(self.index.get_sync_paused())

        invalid = self.client.post("/api/v1/control", json={"sync_paused": "yes"}, headers=self.headers())
        self.assertEqual(invalid.status_code, 400)

        resumed = self.client.post("/api/v1/control", json={"sync_paused": False}, headers=self.headers())
        self.assertEqual(resumed.status_code, 200)
        self.assertFalse(resumed.get_json()["sync_paused"])
        self.assertFalse(self.index.get_sync_paused())

    def test_navidrome_lookup_returns_clip(self):
        response = self.client.get("/api/v1/navidrome/nav-song-42/clip", headers=self.headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["clip"]["id"], self.clip_id)

    def test_stream_endpoint_returns_file(self):
        response = self.client.get(f"/api/v1/stream/{self.clip_id}", headers=self.headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"video")
        self.assertEqual(response.content_type, "video/mp4")

    def test_open_subsonic_style_video_endpoints(self):
        videos = self.client.get("/rest/getVideos.view?f=json&apiKey=secret")
        video_info = self.client.get(f"/rest/getVideoInfo.view?id={self.clip_id}&f=json&apiKey=secret")
        stream = self.client.get(f"/rest/stream.view?id={self.clip_id}&apiKey=secret")

        self.assertEqual(videos.status_code, 200)
        self.assertEqual(videos.get_json()["subsonic-response"]["videos"]["video"][0]["id"], str(self.clip_id))
        self.assertEqual(video_info.status_code, 200)
        self.assertEqual(video_info.get_json()["subsonic-response"]["videoInfo"]["id"], str(self.clip_id))
        self.assertEqual(stream.status_code, 200)
        self.assertEqual(stream.data, b"video")


if __name__ == "__main__":
    unittest.main()
