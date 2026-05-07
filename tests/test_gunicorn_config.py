import os
import runpy
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GunicornConfigTests(unittest.TestCase):
    def test_default_worker_timeout_allows_long_sync_runs(self):
        config = runpy.run_path(str(PROJECT_ROOT / "gunicorn_config.py"))

        self.assertEqual(config["timeout"], 0)

    def test_worker_timeout_can_be_overridden_by_environment(self):
        previous = os.environ.get("GUNICORN_TIMEOUT")
        os.environ["GUNICORN_TIMEOUT"] = "900"
        try:
            config = runpy.run_path(str(PROJECT_ROOT / "gunicorn_config.py"))
        finally:
            if previous is None:
                os.environ.pop("GUNICORN_TIMEOUT", None)
            else:
                os.environ["GUNICORN_TIMEOUT"] = previous

        self.assertEqual(config["timeout"], 900)


if __name__ == "__main__":
    unittest.main()
