import os
import tempfile
import unittest
from unittest.mock import Mock

from lidaclips.index import ClipIndex
from lidaclips.models import ClipTarget
from lidaclips.scoring import Candidate, ClipScorer
from lidaclips.service import LidaClipsService
from lidaclips.storage import ClipStorage


class FakeLidarr:
    def __init__(self, targets):
        self.targets = targets

    def collect_present_tracks(self):
        return self.targets

    def collect_pending_tracks(self, index):
        for target in self.targets:
            index.upsert_track(target, navidrome_song_id="nav-song-42")
        return [target for target in self.targets if not index.has_completed_clip(target.lidarr_track_id)]


class FakeSearch:
    def __init__(self, candidates):
        self.candidates = candidates

    def search(self, target):
        return self.candidates


class TrackingSearch:
    def __init__(self):
        self.searched_ids = []

    def search(self, target):
        self.searched_ids.append(target.lidarr_track_id)
        return [
            Candidate(
                video_id=f"accepted-{target.lidarr_track_id}",
                title=f"{target.artist} - {target.title} (Official Music Video)",
                webpage_url=f"https://example.test/{target.lidarr_track_id}",
            )
        ]


class TitleMissingNavidrome:
    def __init__(self, missing_titles):
        self.missing_titles = set(missing_titles)

    def find_song_id(self, _artist, _album, title):
        return title

    def is_song_present(self, song_id):
        return song_id not in self.missing_titles


class FailingThenSuccessfulSearch:
    def search(self, target):
        if target.lidarr_track_id == 42:
            raise RuntimeError("youtube bot challenge")
        return [
            Candidate(
                video_id="accepted",
                title=f"{target.artist} - {target.title} (Official Music Video)",
                webpage_url="https://example.test/accepted",
            )
        ]


class ProxyFailingSearch:
    def search(self, target):
        raise RuntimeError(
            "ERROR: Unable to download API page: ('Unable to connect to proxy', "
            "NewConnectionError(\"HTTPSConnection(host='aiostreams-tailscale', port=8888): "
            "Failed to establish a new connection: [Errno 111] Connection refused\"))"
        )


class AuthBlockedSearch:
    def search(self, target):
        if target.lidarr_track_id == 42:
            raise RuntimeError(
                "ERROR: [youtube] abc123: Sign in to confirm your age. "
                "This video may be inappropriate for some users. "
                "Use --cookies-from-browser or --cookies for the authentication."
            )
        return [
            Candidate(
                video_id="accepted",
                title=f"{target.artist} - {target.title} (Official Music Video)",
                webpage_url="https://example.test/accepted",
            )
        ]


class RotatingSearch:
    def search(self, target):
        if target.lidarr_track_id == 101:
            return []
        return [
            Candidate(
                video_id=f"accepted-{target.lidarr_track_id}",
                title=f"{target.artist} - {target.title} (Official Music Video)",
                webpage_url=f"https://example.test/{target.lidarr_track_id}",
            )
        ]


class FakeDownloader:
    def __init__(self, file_path):
        self.file_path = file_path
        self.downloads = []

    def download(self, target, candidate):
        self.downloads.append((target, candidate))
        with open(self.file_path, "wb") as handle:
            handle.write(b"video")
        return {"file_path": self.file_path, "mime_type": "video/mp4"}


class AuthBlockedThenSuccessfulDownloader:
    def __init__(self):
        self.downloads = []

    def download(self, target, candidate):
        self.downloads.append((target, candidate))
        if target.lidarr_track_id != 42:
            return {"file_path": "/unused/clip.mp4", "mime_type": "video/mp4"}
        raise RuntimeError(
            "ERROR: [youtube] abc123: Sign in to confirm you’re not a bot. "
            "Use --cookies-from-browser or --cookies for the authentication."
        )


class FakeStorageDownloader(FakeDownloader):
    def __init__(self, file_path, storage):
        super().__init__(file_path)
        self.storage = storage


class FakePoDownloader(FakeDownloader):
    def __init__(self, file_path, po_health):
        super().__init__(file_path)
        self.po_health = po_health

    def po_provider_health(self):
        return self.po_health


class FakeProxyHealthDownloader(FakeDownloader):
    def __init__(self, file_path, proxy_health):
        super().__init__(file_path)
        self.proxy_health = proxy_health

    def youtube_proxy_health(self):
        return self.proxy_health


class FakeDecision:
    def __init__(self, accepted, score, quality_tier="fallback"):
        self.accepted = accepted
        self.score = score
        self.quality_tier = quality_tier

    def to_evidence(self):
        return {"accepted": self.accepted, "score": self.score, "quality_tier": self.quality_tier}


class FakeScorer:
    def score(self, artist, title, expected_duration, candidate):
        if candidate.video_id == "rejected-high":
            return FakeDecision(False, 99, "rejected")
        if candidate.video_id.startswith("official"):
            return FakeDecision(True, 90, "official")
        if candidate.video_id.startswith("fallback-high"):
            return FakeDecision(True, 95, "fallback")
        if candidate.video_id.startswith("fallback-low"):
            return FakeDecision(True, 80, "fallback")
        return FakeDecision(True, 80, "fallback")


class ServiceTests(unittest.TestCase):
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
            source_file_path="/music/song.flac",
        )

    def make_other_target(self):
        return ClipTarget(
            lidarr_track_id=43,
            artist_id=2,
            album_id=11,
            artist="Other Artist",
            album="Other Album",
            album_year=2021,
            title="Other Song",
            track_number="1",
            absolute_track_number=1,
            duration=180,
            source_file_path="/music/other.flac",
        )

    def make_named_target(self, lidarr_track_id, artist, album="Album", title="Song", track_number=1):
        return ClipTarget(
            lidarr_track_id=lidarr_track_id,
            artist_id=lidarr_track_id,
            album_id=lidarr_track_id,
            artist=artist,
            album=album,
            album_year=2024,
            title=title,
            track_number=str(track_number),
            absolute_track_number=track_number,
            duration=180,
            source_file_path=f"/music/{artist}/{title}.flac",
        )

    def test_sync_once_records_downloaded_official_clip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            clip_path = os.path.join(temp_dir, "clip.mp4")
            downloader = FakeDownloader(clip_path)
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr([self.make_target()]),
                candidate_search=FakeSearch(
                    [
                        Candidate(
                            video_id="abc123",
                            title="The Example Band - Bright Lights (Official Music Video)",
                            webpage_url="https://www.youtube.com/watch?v=abc123",
                            channel="The Example Band",
                            uploader="The Example Band",
                            duration=242,
                            view_count=2000000,
                            channel_follower_count=900000,
                            channel_is_verified=True,
                        )
                    ]
                ),
                scorer=ClipScorer(minimum_score=75),
                downloader=downloader,
            )

            summary = service.sync_once()

            self.assertEqual(summary["downloaded"], 1)
            self.assertEqual(summary["no_match"], 0)
            self.assertEqual(len(downloader.downloads), 1)
            clip = index.get_clip_by_track(42)
            self.assertIsNotNone(clip)
            self.assertEqual(clip["video_id"], "abc123")
            self.assertEqual(clip["quality_tier"], "official")

    def test_sync_once_downloads_best_fallback_when_no_official_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            downloader = FakeDownloader(os.path.join(temp_dir, "clip.mp4"))
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr([self.make_target()]),
                candidate_search=FakeSearch(
                    [
                        Candidate(video_id="fallback-low", title="Bright Lights", webpage_url="https://example.test/low"),
                        Candidate(video_id="fallback-high", title="Bright Lights", webpage_url="https://example.test/high"),
                    ]
                ),
                scorer=FakeScorer(),
                downloader=downloader,
            )

            summary = service.sync_once()

            self.assertEqual(summary["downloaded"], 1)
            self.assertEqual(summary["fallback_downloaded"], 1)
            self.assertEqual(summary["official_downloaded"], 0)
            self.assertEqual(downloader.downloads[0][1].video_id, "fallback-high")
            clip = index.get_clip_by_track(42)
            self.assertEqual(clip["video_id"], "fallback-high")
            self.assertEqual(clip["quality_tier"], "fallback")

    def test_sync_once_records_no_match_when_all_candidates_are_rejected(self):
        index = ClipIndex(":memory:")
        service = LidaClipsService(
            index=index,
            lidarr_client=FakeLidarr([self.make_target()]),
            candidate_search=FakeSearch(
                [
                    Candidate(
                        video_id="topic123",
                        title="Bright Lights",
                        webpage_url="https://www.youtube.com/watch?v=topic123",
                        channel="The Example Band - Topic",
                        uploader="The Example Band - Topic",
                        duration=240,
                        view_count=9000000,
                        channel_follower_count=900000,
                        channel_is_verified=True,
                    )
                ]
            ),
            scorer=ClipScorer(minimum_score=75),
            downloader=FakeDownloader("/unused/clip.mp4"),
        )

        summary = service.sync_once()

        self.assertEqual(summary["downloaded"], 0)
        self.assertEqual(summary["no_match"], 1)
        self.assertFalse(index.has_completed_clip(42))
        self.assertEqual(index.get_failure(42)["reason"], "no_match")

    def test_sync_once_chooses_best_accepted_candidate_not_highest_rejected_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            downloader = FakeDownloader(os.path.join(temp_dir, "clip.mp4"))
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr([self.make_target()]),
                candidate_search=FakeSearch(
                    [
                        Candidate(video_id="rejected-high", title="Wrong", webpage_url="https://example.test/rejected"),
                        Candidate(video_id="accepted-lower", title="Right", webpage_url="https://example.test/accepted"),
                    ]
                ),
                scorer=FakeScorer(),
                downloader=downloader,
            )

            summary = service.sync_once()

            self.assertEqual(summary["downloaded"], 1)
            self.assertEqual(downloader.downloads[0][1].video_id, "accepted-lower")
            self.assertEqual(index.get_clip_by_track(42)["video_id"], "accepted-lower")

    def test_sync_once_upgrades_existing_fallback_to_official_and_deletes_old_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            old_path = os.path.join(temp_dir, "old.mp4")
            with open(old_path, "wb") as handle:
                handle.write(b"old")
            target = self.make_target()
            index.upsert_track(target)
            old_clip_id = index.record_clip(
                lidarr_track_id=42,
                video_id="fallback-old",
                source_url="https://example.test/old",
                title="Bright Lights",
                file_path=old_path,
                mime_type="video/mp4",
                score=80.0,
                evidence={"accepted": True, "quality_tier": "fallback", "score": 80.0},
                quality_tier="fallback",
            )
            new_path = os.path.join(temp_dir, "new.mp4")
            downloader = FakeDownloader(new_path)
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr([target]),
                candidate_search=FakeSearch(
                    [Candidate(video_id="official-new", title="Bright Lights", webpage_url="https://example.test/new")]
                ),
                scorer=FakeScorer(),
                downloader=downloader,
            )

            summary = service.sync_once()

            self.assertEqual(summary["upgrade_targets"], 1)
            self.assertEqual(summary["upgraded"], 1)
            self.assertFalse(os.path.exists(old_path))
            active = index.get_clip_by_track(42)
            self.assertEqual(active["video_id"], "official-new")
            self.assertEqual(active["quality_tier"], "official")
            replaced = index.get_clip_by_id(old_clip_id, include_replaced=True)
            self.assertEqual(replaced["status"], "replaced")
            self.assertEqual(replaced["replaced_by_clip_id"], active["id"])

    def test_sync_once_does_not_replace_fallback_with_lower_or_equal_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            old_path = os.path.join(temp_dir, "old.mp4")
            with open(old_path, "wb") as handle:
                handle.write(b"old")
            target = self.make_target()
            index.upsert_track(target)
            index.record_clip(
                lidarr_track_id=42,
                video_id="fallback-old",
                source_url="https://example.test/old",
                title="Bright Lights",
                file_path=old_path,
                mime_type="video/mp4",
                score=90.0,
                evidence={"accepted": True, "quality_tier": "fallback", "score": 90.0},
                quality_tier="fallback",
            )
            downloader = FakeDownloader(os.path.join(temp_dir, "new.mp4"))
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr([target]),
                candidate_search=FakeSearch(
                    [Candidate(video_id="fallback-low", title="Bright Lights", webpage_url="https://example.test/low")]
                ),
                scorer=FakeScorer(),
                downloader=downloader,
            )

            summary = service.sync_once()

            self.assertEqual(summary["upgrade_targets"], 1)
            self.assertEqual(summary["no_upgrade"], 1)
            self.assertEqual(summary["upgraded"], 0)
            self.assertEqual(downloader.downloads, [])
            self.assertEqual(index.get_clip_by_track(42)["video_id"], "fallback-old")

    def test_sync_once_skips_existing_official_clip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            target = self.make_target()
            index.upsert_track(target)
            index.record_clip(
                lidarr_track_id=42,
                video_id="official-old",
                source_url="https://example.test/old",
                title="Bright Lights (Official Music Video)",
                file_path=os.path.join(temp_dir, "old.mp4"),
                mime_type="video/mp4",
                score=95.0,
                evidence={"accepted": True, "quality_tier": "official", "score": 95.0},
                quality_tier="official",
            )
            downloader = FakeDownloader(os.path.join(temp_dir, "new.mp4"))
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr([target]),
                candidate_search=FakeSearch(
                    [Candidate(video_id="official-new", title="Bright Lights", webpage_url="https://example.test/new")]
                ),
                scorer=FakeScorer(),
                downloader=downloader,
            )

            summary = service.sync_once()

            self.assertEqual(summary["processed"], 0)
            self.assertEqual(summary["upgrade_targets"], 0)
            self.assertEqual(downloader.downloads, [])

    def test_sync_once_respects_artist_allowlist_and_target_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            downloader = FakeDownloader(os.path.join(temp_dir, "clip.mp4"))
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr([self.make_other_target(), self.make_target()]),
                candidate_search=FakeSearch(
                    [
                        Candidate(
                            video_id="accepted",
                            title="The Example Band - Bright Lights (Official Music Video)",
                            webpage_url="https://example.test/accepted",
                        )
                    ]
                ),
                scorer=FakeScorer(),
                downloader=downloader,
                sync_artist_allowlist=["The Example Band"],
                max_targets_per_run=1,
            )

            summary = service.sync_once()

            self.assertEqual(summary["targets"], 2)
            self.assertEqual(summary["skipped_by_allowlist"], 1)
            self.assertEqual(summary["limited"], 0)
            self.assertEqual(summary["processed"], 1)
            self.assertEqual(summary["downloaded"], 1)
            self.assertEqual(downloader.downloads[0][0].artist, "The Example Band")

    def test_sync_once_does_not_count_navidrome_missing_toward_youtube_search_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            downloader = FakeDownloader(os.path.join(temp_dir, "clip.mp4"))
            search = TrackingSearch()
            targets = [
                self.make_named_target(101, "Alpha Artist", title="Missing From Navidrome"),
                self.make_named_target(102, "Bravo Artist", title="Present One"),
                self.make_named_target(103, "Charlie Artist", title="Present Two"),
            ]
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr(targets),
                candidate_search=search,
                scorer=FakeScorer(),
                downloader=downloader,
                navidrome_client=TitleMissingNavidrome({"Missing From Navidrome"}),
                max_targets_per_run=1,
                logger=Mock(),
            )

            summary = service.sync_once()

            self.assertEqual(summary["navidrome_missing"], 1)
            self.assertEqual(summary["youtube_searches"], 1)
            self.assertEqual(summary["processed"], 2)
            self.assertEqual(summary["limited"], 1)
            self.assertEqual(search.searched_ids, [102])
            self.assertEqual(downloader.downloads[0][0].lidarr_track_id, 102)

    def test_sync_once_rotates_past_failed_targets_until_full_queue_wraps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            downloader = FakeDownloader(os.path.join(temp_dir, "clip.mp4"))
            targets = [
                self.make_named_target(103, "Charlie Artist", title="Third"),
                self.make_named_target(101, "Alpha Artist", title="First"),
                self.make_named_target(102, "Bravo Artist", title="Second"),
            ]
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr(targets),
                candidate_search=RotatingSearch(),
                scorer=FakeScorer(),
                downloader=downloader,
                max_targets_per_run=1,
                logger=Mock(),
            )

            first = service.sync_once()
            second = service.sync_once()
            third = service.sync_once()
            fourth = service.sync_once()

            self.assertEqual(first["processed"], 1)
            self.assertEqual(first["no_match"], 1)
            self.assertEqual(index.get_failure(101)["reason"], "no_match")
            self.assertEqual(second["downloaded"], 1)
            self.assertEqual(downloader.downloads[0][0].lidarr_track_id, 102)
            self.assertEqual(third["downloaded"], 1)
            self.assertEqual(downloader.downloads[1][0].lidarr_track_id, 103)
            self.assertEqual(fourth["no_match"], 1)
            self.assertEqual(index.get_failure(101)["reason"], "no_match")

    def test_sync_once_records_accepted_candidate_without_download_when_disabled(self):
        index = ClipIndex(":memory:")
        downloader = FakeDownloader("/unused/clip.mp4")
        service = LidaClipsService(
            index=index,
            lidarr_client=FakeLidarr([self.make_target()]),
            candidate_search=FakeSearch(
                [
                    Candidate(
                        video_id="accepted",
                        title="The Example Band - Bright Lights (Official Music Video)",
                        webpage_url="https://example.test/accepted",
                    )
                ]
            ),
            scorer=FakeScorer(),
            downloader=downloader,
            download_enabled=False,
        )

        summary = service.sync_once()

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["download_disabled"], 1)
        self.assertEqual(summary["downloaded"], 0)
        self.assertEqual(downloader.downloads, [])
        self.assertFalse(index.has_completed_clip(42))
        self.assertEqual(index.get_failure(42)["reason"], "download_disabled")

    def test_sync_once_records_candidate_search_errors_and_continues(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            downloader = FakeDownloader(os.path.join(temp_dir, "clip.mp4"))
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr([self.make_target(), self.make_other_target()]),
                candidate_search=FailingThenSuccessfulSearch(),
                scorer=FakeScorer(),
                downloader=downloader,
                logger=Mock(),
            )

            summary = service.sync_once()

            self.assertEqual(summary["processed"], 2)
            self.assertEqual(summary["search_errors"], 1)
            self.assertEqual(summary["downloaded"], 1)
            self.assertEqual(len(downloader.downloads), 1)
            self.assertEqual(downloader.downloads[0][0].lidarr_track_id, 43)
            self.assertFalse(index.has_completed_clip(42))
            self.assertTrue(index.get_failure(42)["reason"].startswith("candidate_search_error: "))

    def test_sync_once_pauses_on_proxy_failure_without_track_failure_spam(self):
        index = ClipIndex(":memory:")
        service = LidaClipsService(
            index=index,
            lidarr_client=FakeLidarr([self.make_target(), self.make_other_target()]),
            candidate_search=ProxyFailingSearch(),
            scorer=FakeScorer(),
            downloader=FakeDownloader("/unused/clip.mp4"),
            logger=Mock(),
        )

        summary = service.sync_once()

        self.assertEqual(summary["proxy_unavailable"], 1)
        self.assertEqual(summary["search_errors"], 0)
        self.assertTrue(index.get_sync_paused())
        self.assertIsNone(index.get_failure(42))
        self.assertIsNone(index.get_failure(43))

    def test_sync_once_records_youtube_auth_block_download_error_without_pausing(self):
        index = ClipIndex(":memory:")
        downloader = AuthBlockedThenSuccessfulDownloader()
        service = LidaClipsService(
            index=index,
            lidarr_client=FakeLidarr([self.make_target(), self.make_other_target()]),
            candidate_search=FakeSearch(
                [
                    Candidate(
                        video_id="abc123",
                        title="The Example Band - Bright Lights",
                        webpage_url="https://www.youtube.com/watch?v=abc123",
                    )
                ]
            ),
            scorer=FakeScorer(),
            downloader=downloader,
            logger=Mock(),
        )

        summary = service.sync_once()

        self.assertEqual(summary["download_errors"], 1)
        self.assertEqual(summary["youtube_auth_blocked"], 1)
        self.assertEqual(summary["downloaded"], 1)
        self.assertFalse(index.get_sync_paused())
        self.assertEqual(len(downloader.downloads), 2)
        self.assertTrue(index.get_failure(42)["reason"].startswith("download_error: "))
        self.assertIsNone(index.get_failure(43))

    def test_sync_once_records_youtube_auth_block_search_error_without_pausing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            downloader = FakeDownloader(os.path.join(temp_dir, "clip.mp4"))
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr([self.make_target(), self.make_other_target()]),
                candidate_search=AuthBlockedSearch(),
                scorer=FakeScorer(),
                downloader=downloader,
                logger=Mock(),
            )

            summary = service.sync_once()

            self.assertEqual(summary["processed"], 2)
            self.assertEqual(summary["search_errors"], 1)
            self.assertEqual(summary["youtube_auth_blocked"], 1)
            self.assertEqual(summary["downloaded"], 1)
            self.assertFalse(index.get_sync_paused())
            self.assertTrue(index.get_failure(42)["reason"].startswith("candidate_search_error: "))
            self.assertIsNone(index.get_failure(43))

    def test_sync_once_reconciles_completed_clip_to_audio_matching_filename(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = ClipIndex(":memory:")
            storage = ClipStorage(
                output_mode="clips_lane",
                output_path=os.path.join(temp_dir, "clips"),
                staging_path=os.path.join(temp_dir, "staging"),
            )
            old_path = os.path.join(temp_dir, "clips", "The Example Band", "Neon Nights (2020)", "01 - Bright Lights [abc123].mp4")
            os.makedirs(os.path.dirname(old_path), exist_ok=True)
            with open(old_path, "wb") as handle:
                handle.write(b"video")
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
                source_file_path="/music/The Example Band/Neon Nights/01 - Bright Lights.flac",
            )
            index.upsert_track(target)
            index.record_clip(
                lidarr_track_id=42,
                video_id="abc123",
                source_url="https://www.youtube.com/watch?v=abc123",
                title="The Example Band - Bright Lights (Official Music Video)",
                file_path=old_path,
                mime_type="video/mp4",
                score=91.0,
                evidence={"official": True},
            )
            service = LidaClipsService(
                index=index,
                lidarr_client=FakeLidarr([target]),
                candidate_search=FakeSearch([]),
                scorer=FakeScorer(),
                downloader=FakeStorageDownloader("/unused/clip.mp4", storage),
            )

            summary = service.sync_once()

            expected_path = os.path.join(temp_dir, "clips", "The Example Band", "Neon Nights (2020)", "01 - Bright Lights.mp4")
            self.assertEqual(summary["reconciled"], 1)
            self.assertFalse(os.path.exists(old_path))
            self.assertTrue(os.path.exists(expected_path))
            self.assertEqual(index.get_clip_by_track(42)["file_path"], expected_path)

    def test_health_reports_po_provider_check_when_downloader_exposes_it(self):
        index = ClipIndex(":memory:")
        service = LidaClipsService(
            index=index,
            lidarr_client=FakeLidarr([]),
            candidate_search=FakeSearch([]),
            scorer=FakeScorer(),
            downloader=FakePoDownloader("/unused/clip.mp4", {"ok": True, "address": "http://lidaclips-pot:4416"}),
        )

        payload = service.health_check()

        self.assertTrue(payload["checks"]["po_provider"]["ok"])
        self.assertEqual(payload["checks"]["po_provider"]["address"], "http://lidaclips-pot:4416")

    def test_health_reports_youtube_proxy_check_when_downloader_exposes_it(self):
        index = ClipIndex(":memory:")
        service = LidaClipsService(
            index=index,
            lidarr_client=FakeLidarr([]),
            candidate_search=FakeSearch([]),
            scorer=FakeScorer(),
            downloader=FakeProxyHealthDownloader("/unused/clip.mp4", {"ok": False, "address": "http://proxy:8888"}),
        )

        payload = service.health_check()

        self.assertEqual(payload["status"], "degraded")
        self.assertFalse(payload["checks"]["youtube_proxy"]["ok"])
        self.assertEqual(payload["checks"]["youtube_proxy"]["address"], "http://proxy:8888")


if __name__ == "__main__":
    unittest.main()
