import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from lidaclips.candidate_search import YtDlpCandidateSearch
from lidaclips.models import ClipTarget


class FakeYtDlp:
    def __init__(self, options):
        self.options = options
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def extract_info(self, query, download=False):
        self.queries.append((query, download))
        return {
            "entries": [
                {
                    "id": "abc123",
                    "title": "The Example Band - Bright Lights (Official Music Video)",
                    "webpage_url": "https://www.youtube.com/watch?v=abc123",
                    "channel": "The Example Band",
                    "uploader": "The Example Band",
                    "duration": 242,
                    "view_count": 2000000,
                    "channel_follower_count": 900000,
                    "channel_is_verified": True,
                    "tags": ["music"],
                }
            ]
        }


class CandidateSearchTests(unittest.TestCase):
    def test_extracts_candidates_from_yt_dlp_search(self):
        created = []

        def factory(options):
            instance = FakeYtDlp(options)
            created.append(instance)
            return instance

        target = ClipTarget(
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
            source_file_path="/music/song.flac",
        )

        candidates = YtDlpCandidateSearch(limit=7, ytdlp_factory=factory).search(target)

        self.assertEqual(created[0].queries, [("ytsearch7:The Example Band Bright Lights official music video", False)])
        self.assertTrue(created[0].options.get("extract_flat"))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].video_id, "abc123")
        self.assertEqual(candidates[0].channel_follower_count, 900000)
        self.assertTrue(candidates[0].channel_is_verified)

    def test_binary_search_uses_flat_playlist(self):
        target = ClipTarget(
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
            source_file_path="/music/song.flac",
        )
        entry = {"id": "abc123", "title": "The Example Band - Bright Lights"}

        with patch("lidaclips.candidate_search.subprocess.run") as run:
            run.return_value = SimpleNamespace(stdout=json.dumps(entry) + "\n")

            candidates = YtDlpCandidateSearch(
                ytdlp_binary="yt-dlp",
                js_runtime_path=None,
            ).search(target)

        command = run.call_args.args[0]
        self.assertIn("--flat-playlist", command)
        self.assertEqual(candidates[0].video_id, "abc123")

    def test_enables_configured_node_runtime_for_youtube_extraction(self):
        created = []

        def factory(options):
            instance = FakeYtDlp(options)
            created.append(instance)
            return instance

        target = ClipTarget(
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
            source_file_path="/music/song.flac",
        )

        YtDlpCandidateSearch(ytdlp_factory=factory, js_runtime_path="/usr/bin/node").search(target)

        self.assertEqual(created[0].options["js_runtimes"], {"node": {"path": "/usr/bin/node"}})

    def test_passes_po_provider_args_to_candidate_extraction(self):
        created = []

        def factory(options):
            instance = FakeYtDlp(options)
            created.append(instance)
            return instance

        target = ClipTarget(
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
            source_file_path="/music/song.flac",
        )

        YtDlpCandidateSearch(
            ytdlp_factory=factory,
            youtube_po_provider="bgutil_http",
            youtube_po_provider_url="http://lidaclips-pot:4416",
            youtube_player_clients=["mweb", "default"],
        ).search(target)

        self.assertEqual(
            created[0].options["extractor_args"],
            {
                "youtube": {"player_client": ["mweb", "default"]},
                "youtubepot-bgutilhttp": {"base_url": ["http://lidaclips-pot:4416"]},
            },
        )


if __name__ == "__main__":
    unittest.main()
