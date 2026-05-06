import os
import tempfile
import unittest

from lidaclips.models import ClipTarget
from lidaclips.storage import ClipStorage


class ClipStorageTests(unittest.TestCase):
    def make_target(self):
        return ClipTarget(
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

    def test_clips_lane_path_is_grouped_by_artist_and_album(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ClipStorage(output_mode="clips_lane", output_path=temp_dir, staging_path=os.path.join(temp_dir, ".staging"))

            final_path = storage.final_path(self.make_target(), "abc123", ".mp4")

            self.assertEqual(
                final_path,
                os.path.join(temp_dir, "The Example Band", "Neon Nights (2020)", "01 - Bright Lights.mp4"),
            )

    def test_clips_lane_falls_back_to_track_title_without_video_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ClipStorage(output_mode="clips_lane", output_path=temp_dir, staging_path=os.path.join(temp_dir, ".staging"))
            target = self.make_target()
            target = type(target)(
                lidarr_track_id=target.lidarr_track_id,
                artist_id=target.artist_id,
                album_id=target.album_id,
                artist=target.artist,
                album=target.album,
                album_year=target.album_year,
                title=target.title,
                track_number=target.track_number,
                absolute_track_number=target.absolute_track_number,
                duration=target.duration,
                source_file_path=None,
            )

            final_path = storage.final_path(target, "abc123", ".mp4")

            self.assertEqual(
                final_path,
                os.path.join(temp_dir, "The Example Band", "Neon Nights (2020)", "01 - Bright Lights.mp4"),
            )

    def test_clips_lane_appends_lidarr_id_when_expected_path_conflicts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ClipStorage(output_mode="clips_lane", output_path=temp_dir, staging_path=os.path.join(temp_dir, ".staging"))

            final_path = storage.final_path(self.make_target(), "abc123", ".mp4", conflict_checker=lambda path: True)

            self.assertEqual(
                final_path,
                os.path.join(temp_dir, "The Example Band", "Neon Nights (2020)", "01 - Bright Lights [lidarr-42].mp4"),
            )

    def test_sidecar_path_is_written_beside_audio_file(self):
        storage = ClipStorage(output_mode="sidecar", output_path="/unused", staging_path="/tmp/staging")

        final_path = storage.final_path(self.make_target(), "abc123", ".mp4")

        self.assertEqual(final_path, "/music/The Example Band/Neon Nights/01 - Bright Lights.mp4")

    def test_finalize_moves_staged_file_atomically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ClipStorage(output_mode="clips_lane", output_path=os.path.join(temp_dir, "clips"), staging_path=os.path.join(temp_dir, "staging"))
            staged = os.path.join(temp_dir, "staging", "download.tmp.mp4")
            os.makedirs(os.path.dirname(staged), exist_ok=True)
            with open(staged, "wb") as handle:
                handle.write(b"video")
            final_path = storage.final_path(self.make_target(), "abc123", ".mp4")

            moved_path = storage.finalize(staged, final_path)

            self.assertEqual(moved_path, final_path)
            self.assertFalse(os.path.exists(staged))
            with open(final_path, "rb") as handle:
                self.assertEqual(handle.read(), b"video")


if __name__ == "__main__":
    unittest.main()
