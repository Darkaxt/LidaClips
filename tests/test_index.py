import json
import os
import sqlite3
import tempfile
import unittest

from lidaclips.index import ClipIndex
from lidaclips.models import ClipTarget


class IndexMigrationTests(unittest.TestCase):
    def make_target(self, lidarr_track_id=42, title="Bright Lights"):
        return ClipTarget(
            lidarr_track_id=lidarr_track_id,
            artist_id=1,
            album_id=10,
            artist="The Example Band",
            album="Neon Nights",
            album_year=2020,
            title=title,
            track_number="1",
            absolute_track_number=1,
            duration=240,
            source_file_path="/music/song.flac",
        )

    def test_migrates_existing_clip_tiers_from_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "lidaclips.db")
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE tracks (
                    lidarr_track_id INTEGER PRIMARY KEY,
                    artist_id INTEGER NOT NULL,
                    album_id INTEGER NOT NULL,
                    artist TEXT NOT NULL,
                    album TEXT NOT NULL,
                    album_year INTEGER,
                    title TEXT NOT NULL,
                    track_number TEXT,
                    absolute_track_number INTEGER,
                    duration INTEGER,
                    source_file_path TEXT,
                    navidrome_song_id TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE clips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lidarr_track_id INTEGER NOT NULL,
                    video_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    score REAL NOT NULL,
                    evidence_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'completed',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lidarr_track_id INTEGER NOT NULL,
                    video_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    score REAL NOT NULL,
                    accepted INTEGER NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE failures (
                    lidarr_track_id INTEGER PRIMARY KEY,
                    reason TEXT NOT NULL,
                    retry_after TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            now = "2026-05-06T00:00:00+00:00"
            connection.execute(
                "INSERT INTO tracks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (42, 1, 10, "The Example Band", "Neon Nights", 2020, "Bright Lights", "1", 1, 240, "/music/song.flac", None, now),
            )
            connection.execute(
                "INSERT INTO tracks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (43, 1, 10, "The Example Band", "Neon Nights", 2020, "City Glow", "2", 2, 240, "/music/city.flac", None, now),
            )
            connection.execute(
                "INSERT INTO clips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, 42, "official123", "https://example.test/official", "Official", "/clips/official.mp4", "video/mp4", 95.0, json.dumps({"reasons": ["official"]}), "completed", now, now),
            )
            connection.execute(
                "INSERT INTO clips VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (2, 43, "fallback123", "https://example.test/fallback", "Fallback", "/clips/fallback.mp4", "video/mp4", 88.0, json.dumps({"reasons": ["verified_channel"]}), "completed", now, now),
            )
            connection.commit()
            connection.close()

            index = ClipIndex(db_path)

            self.assertEqual(index.get_clip_by_track(42)["quality_tier"], "official")
            self.assertEqual(index.get_clip_by_track(43)["quality_tier"], "fallback")
            columns = {row["name"] for row in index.connection.execute("PRAGMA table_info(clips)")}
            self.assertIn("quality_tier", columns)
            self.assertIn("replaced_by_clip_id", columns)
            index.close()

    def test_replaced_clips_are_not_returned_by_public_lookups(self):
        index = ClipIndex(":memory:")
        target = self.make_target()
        index.upsert_track(target)
        old_id = index.record_clip(
            lidarr_track_id=42,
            video_id="fallback-old",
            source_url="https://example.test/old",
            title="Old",
            file_path="/clips/old.mp4",
            mime_type="video/mp4",
            score=80.0,
            evidence={"quality_tier": "fallback"},
            quality_tier="fallback",
        )
        new_id = index.record_clip(
            lidarr_track_id=42,
            video_id="official-new",
            source_url="https://example.test/new",
            title="New",
            file_path="/clips/new.mp4",
            mime_type="video/mp4",
            score=95.0,
            evidence={"quality_tier": "official", "reasons": ["official"]},
            quality_tier="official",
        )

        index.mark_clip_replaced(old_id, new_id)

        self.assertEqual(index.get_clip_by_track(42)["id"], new_id)
        self.assertIsNone(index.get_clip_by_id(old_id))
        self.assertEqual(index.get_clip_by_id(old_id, include_replaced=True)["status"], "replaced")

    def test_dashboard_summary_counts_active_clips_and_recent_failures(self):
        index = ClipIndex(":memory:")
        official_target = self.make_target(lidarr_track_id=42, title="Bright Lights")
        fallback_target = self.make_target(lidarr_track_id=43, title="City Glow")
        failed_target = self.make_target(lidarr_track_id=44, title="Static")
        index.upsert_track(official_target)
        index.upsert_track(fallback_target)
        index.upsert_track(failed_target)
        index.record_clip(
            lidarr_track_id=42,
            video_id="official-new",
            source_url="https://example.test/new",
            title="New",
            file_path="/clips/new.mp4",
            mime_type="video/mp4",
            score=95.0,
            evidence={"quality_tier": "official", "reasons": ["official"]},
            quality_tier="official",
        )
        old_id = index.record_clip(
            lidarr_track_id=43,
            video_id="fallback-old",
            source_url="https://example.test/old",
            title="Old",
            file_path="/clips/old.mp4",
            mime_type="video/mp4",
            score=70.0,
            evidence={"quality_tier": "fallback"},
            quality_tier="fallback",
        )
        new_id = index.record_clip(
            lidarr_track_id=43,
            video_id="fallback-new",
            source_url="https://example.test/fallback",
            title="Fallback",
            file_path="/clips/fallback.mp4",
            mime_type="video/mp4",
            score=82.0,
            evidence={"quality_tier": "fallback"},
            quality_tier="fallback",
        )
        index.mark_clip_replaced(old_id, new_id)
        index.record_no_match(44, "no_match")

        summary = index.dashboard_summary()

        self.assertEqual(summary["tracked_tracks"], 3)
        self.assertEqual(summary["active_clips"], 2)
        self.assertEqual(summary["coverage_percent"], 66.7)
        self.assertEqual(summary["official_clips"], 1)
        self.assertEqual(summary["fallback_clips"], 1)
        self.assertEqual(summary["replaced_clips"], 1)
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["no_match"], 1)
        self.assertEqual(summary["recent_failures"][0]["track_title"], "Static")
        self.assertEqual({clip["id"] for clip in summary["recent_clips"]}, {1, new_id})

    def test_dashboard_summary_treats_navidrome_missing_as_deferred_not_failure(self):
        index = ClipIndex(":memory:")
        no_match_target = self.make_target(lidarr_track_id=44, title="Static")
        deferred_target = self.make_target(lidarr_track_id=45, title="Not Indexed Yet")
        index.upsert_track(no_match_target)
        index.upsert_track(deferred_target)
        index.record_no_match(44, "no_match")
        index.record_no_match(45, "navidrome_missing")

        summary = index.dashboard_summary()

        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["no_match"], 1)
        self.assertEqual(summary["navidrome_missing"], 1)
        self.assertEqual(index.get_failure(45)["reason"], "navidrome_missing")
        self.assertEqual([row["lidarr_track_id"] for row in summary["recent_failures"]], [44])

    def test_dashboard_summary_reports_zero_coverage_without_tracks(self):
        index = ClipIndex(":memory:")

        summary = index.dashboard_summary()

        self.assertEqual(summary["tracked_tracks"], 0)
        self.assertEqual(summary["active_clips"], 0)
        self.assertEqual(summary["coverage_percent"], 0.0)

    def test_sync_control_state_defaults_to_running_and_persists_pause(self):
        index = ClipIndex(":memory:")

        self.assertFalse(index.get_sync_paused())

        index.set_sync_paused(True)

        self.assertTrue(index.get_sync_paused())
        self.assertTrue(index.dashboard_summary()["sync_paused"])

        index.set_sync_paused(False)

        self.assertFalse(index.get_sync_paused())

    def test_dashboard_summary_returns_enough_recent_clips_for_full_page_table(self):
        index = ClipIndex(":memory:")

        for lidarr_track_id in range(1, 21):
            target = self.make_target(lidarr_track_id=lidarr_track_id, title=f"Song {lidarr_track_id}")
            index.upsert_track(target)
            index.record_clip(
                lidarr_track_id=lidarr_track_id,
                video_id=f"video-{lidarr_track_id}",
                source_url=f"https://example.test/{lidarr_track_id}",
                title=f"Song {lidarr_track_id}",
                file_path=f"/clips/song-{lidarr_track_id}.mp4",
                mime_type="video/mp4",
                score=90.0,
                evidence={"quality_tier": "official"},
                quality_tier="official",
            )

        summary = index.dashboard_summary()

        self.assertEqual(len(summary["recent_clips"]), 20)


if __name__ == "__main__":
    unittest.main()
