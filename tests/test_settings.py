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
            self.assertEqual(settings.minimum_fallback_score, 60)
            self.assertEqual(settings.upgrade_min_score_delta, 10)
            self.assertFalse(settings.download_enabled)
            self.assertEqual(settings.youtube_po_provider, "off")
            self.assertEqual(settings.youtube_po_provider_url, "http://lidaclips-pot:4416")
            self.assertEqual(settings.youtube_player_clients, ["mweb", "default"])
            self.assertTrue(settings.youtube_enable_hls_fallback)
            self.assertEqual(settings.socketio_allowed_origins, [])
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
                    "minimum_fallback_score": "55",
                    "upgrade_min_score_delta": "12",
                    "youtube_po_provider": "bgutil_http",
                    "youtube_po_provider_url": "http://pot:4416",
                    "youtube_player_clients": "mweb, default",
                    "youtube_enable_hls_fallback": "false",
                    "socketio_allowed_origins": "https://clips.example.test, http://localhost:5000",
                },
            )

            self.assertEqual(settings.lidarr_address, "http://second")
            self.assertEqual(settings.minimum_clip_score, 70)
            self.assertEqual(settings.clip_output_mode, "sidecar")
            self.assertEqual(settings.sync_schedule, [0, 2, 20])
            self.assertEqual(settings.sync_artist_allowlist, ["The Example Band", "Another Artist"])
            self.assertEqual(settings.max_targets_per_run, 10)
            self.assertEqual(settings.minimum_fallback_score, 55)
            self.assertEqual(settings.upgrade_min_score_delta, 12)
            self.assertTrue(settings.download_enabled)
            self.assertEqual(settings.youtube_po_provider, "bgutil_http")
            self.assertEqual(settings.youtube_po_provider_url, "http://pot:4416")
            self.assertEqual(settings.youtube_player_clients, ["mweb", "default"])
            self.assertFalse(settings.youtube_enable_hls_fallback)
            self.assertEqual(settings.socketio_allowed_origins, ["https://clips.example.test", "http://localhost:5000"])

    def test_empty_environment_values_clear_schedule_and_allowlist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Settings.load(
                config_folder=temp_dir,
                environ={
                    "sync_schedule": "2,20",
                    "sync_artist_allowlist": "Linkin Park",
                },
            )

            settings = Settings.load(
                config_folder=temp_dir,
                environ={
                    "sync_schedule": "",
                    "sync_artist_allowlist": "",
                },
            )

            self.assertEqual(settings.sync_schedule, [])
            self.assertEqual(settings.sync_artist_allowlist, [])


if __name__ == "__main__":
    unittest.main()
