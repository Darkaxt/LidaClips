import os
import tempfile
import unittest

from lidaclips.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_loads_defaults_and_saves_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.load(config_folder=temp_dir, environ={})

            self.assertEqual(settings.clip_output_mode, "clips_lane")
            self.assertEqual(settings.clip_output_path, "/lidaclips/clips")
            self.assertEqual(settings.preferred_container, "mp4")
            self.assertEqual(settings.sync_schedule, [])
            self.assertEqual(settings.sync_artist_allowlist, [])
            self.assertEqual(settings.max_targets_per_run, 25)
            self.assertFalse(settings.download_enabled)
            self.assertTrue(os.path.exists(os.path.join(temp_dir, "settings_config.json")))

    def test_environment_overrides_config_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Settings.load(
                config_folder=temp_dir,
                environ={
                    "lidarr_address": "http://first",
                    "minimum_clip_score": "70",
                },
            )

            settings = Settings.load(
                config_folder=temp_dir,
                environ={
                    "lidarr_address": "http://second",
                    "clip_output_mode": "sidecar",
                    "sync_schedule": "2, 20, 27",
                    "sync_artist_allowlist": "The Example Band, Another Artist",
                    "max_targets_per_run": "10",
                    "download_enabled": "true",
                },
            )

            self.assertEqual(settings.lidarr_address, "http://second")
            self.assertEqual(settings.minimum_clip_score, 70)
            self.assertEqual(settings.clip_output_mode, "sidecar")
            self.assertEqual(settings.sync_schedule, [0, 2, 20])
            self.assertEqual(settings.sync_artist_allowlist, ["The Example Band", "Another Artist"])
            self.assertEqual(settings.max_targets_per_run, 10)
            self.assertTrue(settings.download_enabled)


if __name__ == "__main__":
    unittest.main()
