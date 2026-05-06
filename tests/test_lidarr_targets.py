import unittest

from lidaclips.index import ClipIndex
from lidaclips.lidarr_client import LidarrClient


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params or {}, timeout))
        if url.endswith("/api/v1/album"):
            return FakeResponse(
                [
                    {
                        "id": 10,
                        "artistId": 1,
                        "title": "Neon Nights",
                        "releaseDate": "2020-01-01T00:00:00Z",
                        "genres": ["synthpop"],
                        "artist": {
                            "id": 1,
                            "artistName": "The Example Band",
                            "path": "/music/The Example Band",
                        },
                    }
                ]
            )
        if url.endswith("/api/v1/track"):
            album_id = params["albumId"]
            if album_id != 10:
                raise AssertionError(f"unexpected album id {album_id}")
            return FakeResponse(
                [
                    {
                        "id": 42,
                        "title": "Bright Lights",
                        "trackNumber": "1",
                        "absoluteTrackNumber": 1,
                        "duration": 240,
                        "hasFile": True,
                        "mediumNumber": 1,
                        "audioFile": {"path": "/music/The Example Band/Neon Nights/01 - Bright Lights.flac"},
                    },
                    {
                        "id": 43,
                        "title": "Missing Song",
                        "trackNumber": "2",
                        "absoluteTrackNumber": 2,
                        "duration": 200,
                        "hasFile": False,
                    },
                ]
            )
        raise AssertionError(f"unexpected URL {url}")


class TrackFileSession:
    def __init__(self, trackfile_payload=None, track_by_id_payload=None):
        self.calls = []
        self.trackfile_payload = trackfile_payload or []
        self.track_by_id_payload = track_by_id_payload

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params or {}, timeout))
        if url.endswith("/api/v1/album"):
            return FakeResponse(
                [
                    {
                        "id": 10,
                        "artistId": 1,
                        "title": "Neon Nights",
                        "releaseDate": "2020-01-01T00:00:00Z",
                        "artist": {"id": 1, "artistName": "The Example Band"},
                    }
                ]
            )
        if url.endswith("/api/v1/track"):
            return FakeResponse(
                [
                    {
                        "id": 42,
                        "title": "Bright Lights",
                        "trackNumber": "1",
                        "absoluteTrackNumber": 1,
                        "duration": 240,
                        "hasFile": True,
                        "trackFileId": 100,
                    }
                ]
            )
        if url.endswith("/api/v1/trackfile"):
            self.trackfile_params = params or {}
            return FakeResponse(self.trackfile_payload)
        if url.endswith("/api/v1/track/42"):
            return FakeResponse(self.track_by_id_payload)
        raise AssertionError(f"unexpected URL {url}")


class LidarrTargetTests(unittest.TestCase):
    def test_collects_only_tracks_that_already_have_files(self):
        client = LidarrClient("http://lidarr:8686", "key", session=FakeSession())

        targets = client.collect_present_tracks()

        self.assertEqual(len(targets), 1)
        target = targets[0]
        self.assertEqual(target.lidarr_track_id, 42)
        self.assertEqual(target.artist, "The Example Band")
        self.assertEqual(target.album, "Neon Nights")
        self.assertEqual(target.title, "Bright Lights")
        self.assertEqual(target.source_file_path, "/music/The Example Band/Neon Nights/01 - Bright Lights.flac")

    def test_collects_source_file_path_from_trackfile_batch_lookup(self):
        session = TrackFileSession(
            trackfile_payload=[
                {
                    "id": 100,
                    "path": "/data/music/The Example Band/Neon Nights/01 - Bright Lights.flac",
                }
            ]
        )
        client = LidarrClient("http://lidarr:8686", "key", session=session)

        targets = client.collect_present_tracks()

        self.assertEqual(targets[0].source_file_path, "/data/music/The Example Band/Neon Nights/01 - Bright Lights.flac")
        self.assertEqual(session.trackfile_params["trackFileIds"], [100])

    def test_trackfile_lookup_uses_repeated_query_values_for_multiple_ids(self):
        session = TrackFileSession(
            trackfile_payload=[
                {"id": 100, "path": "/music/one.flac"},
                {"id": 101, "path": "/music/two.flac"},
            ]
        )
        client = LidarrClient("http://lidarr:8686", "key", session=session)

        track_files = client._get_track_files([100, 101])

        self.assertEqual(sorted(track_files), [100, 101])
        self.assertEqual(session.trackfile_params["trackFileIds"], [100, 101])

    def test_collects_source_file_path_from_track_by_id_fallback(self):
        session = TrackFileSession(
            trackfile_payload=[],
            track_by_id_payload={
                "id": 42,
                "trackFile": {
                    "id": 100,
                    "path": "/data/music/The Example Band/Neon Nights/01 - Bright Lights.flac",
                },
            },
        )
        client = LidarrClient("http://lidarr:8686", "key", session=session)

        targets = client.collect_present_tracks()

        self.assertEqual(targets[0].source_file_path, "/data/music/The Example Band/Neon Nights/01 - Bright Lights.flac")
        self.assertTrue(any(call[0].endswith("/api/v1/track/42") for call in session.calls))

    def test_skips_tracks_with_completed_clip_in_index(self):
        client = LidarrClient("http://lidarr:8686", "key", session=FakeSession())
        index = ClipIndex(":memory:")
        index.upsert_track(client.collect_present_tracks()[0], navidrome_song_id="nav-song-42")
        index.record_clip(
            lidarr_track_id=42,
            video_id="abc123",
            source_url="https://www.youtube.com/watch?v=abc123",
            title="The Example Band - Bright Lights (Official Music Video)",
            file_path="/clips/The Example Band/Neon Nights/01 - Bright Lights [abc123].mp4",
            mime_type="video/mp4",
            score=91.0,
            evidence={"official": True},
        )

        pending = client.collect_pending_tracks(index)

        self.assertEqual(pending, [])


if __name__ == "__main__":
    unittest.main()
