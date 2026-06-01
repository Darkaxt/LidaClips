import os
import tempfile
import unittest

from lidaclips.index import ClipIndex
from lidaclips.media_validation import StaticVideoError
from lidaclips.models import ClipTarget
from lidaclips.static_scan import scan_active_static_clips


class PathBasedValidator:
    def __init__(self, static_path):
        self.static_path = static_path

    def validate(self, path):
        if path == self.static_path:
            raise StaticVideoError("static_visuals", {"static": True, "average_delta": 0.0})
        return {"static": False, "average_delta": 0.2}


class StaticScanTests(unittest.TestCase):
    def make_target(self, lidarr_track_id, title):
        return ClipTarget(
            lidarr_track_id=lidarr_track_id,
            artist_id=1,
            album_id=10,
            artist="The Example Band",
            album="Neon Nights",
            album_year=2020,
            title=title,
            track_number=str(lidarr_track_id),
            absolute_track_number=lidarr_track_id,
            duration=240,
            source_file_path=f"/music/{title}.flac",
        )

    def test_scan_rejects_static_active_clip_and_quarantines_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            static_path = os.path.join(temp_dir, "static.mp4")
            moving_path = os.path.join(temp_dir, "moving.mp4")
            with open(static_path, "wb") as handle:
                handle.write(b"static")
            with open(moving_path, "wb") as handle:
                handle.write(b"moving")

            static_target = self.make_target(42, "Static")
            moving_target = self.make_target(43, "Moving")
            index.upsert_track(static_target)
            index.upsert_track(moving_target)
            static_clip_id = index.record_clip(
                lidarr_track_id=42,
                video_id="static-art",
                source_url="https://example.test/static",
                title="Static",
                file_path=static_path,
                mime_type="video/mp4",
                score=92.0,
                evidence={"quality_tier": "fallback"},
                quality_tier="fallback",
            )
            index.record_clip(
                lidarr_track_id=43,
                video_id="moving-video",
                source_url="https://example.test/moving",
                title="Moving",
                file_path=moving_path,
                mime_type="video/mp4",
                score=92.0,
                evidence={"quality_tier": "fallback"},
                quality_tier="fallback",
            )

            result = scan_active_static_clips(
                index,
                PathBasedValidator(static_path),
                quarantine_path=os.path.join(temp_dir, "rejected"),
            )

            self.assertEqual(result["scanned"], 2)
            self.assertEqual(result["rejected"], 1)
            self.assertIsNone(index.get_clip_by_track(42))
            self.assertIsNotNone(index.get_clip_by_track(43))
            self.assertEqual(index.get_clip_by_id(static_clip_id, include_replaced=True)["status"], "rejected")
            self.assertEqual(index.get_failure(42)["reason"], "static_visuals")
            self.assertEqual(index.rejected_video_ids(42, "static_visuals"), {"static-art"})
            self.assertFalse(os.path.exists(static_path))
            self.assertTrue(os.path.exists(result["items"][0]["quarantine_path"]))


if __name__ == "__main__":
    unittest.main()
