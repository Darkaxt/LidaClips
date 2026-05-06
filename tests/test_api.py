import os
import tempfile
import unittest

from lidaclips.index import ClipIndex
from lidaclips.models import ClipTarget
from lidaclips.web import create_app


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
        self.app = create_app(self.index, api_key="secret")
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def headers(self):
        return {"X-Api-Key": "secret"}

    def test_ping(self):
        response = self.client.get("/api/v1/ping")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_search_clips_and_track_lookup_require_api_key(self):
        unauthorized = self.client.get("/api/v1/clips?artist=Example")
        self.assertEqual(unauthorized.status_code, 401)

        response = self.client.get("/api/v1/clips?artist=Example&track=Bright", headers=self.headers())
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["clips"]), 1)
        self.assertEqual(payload["clips"][0]["lidarr_track_id"], 42)

        track_response = self.client.get("/api/v1/tracks/42/clip", headers=self.headers())
        self.assertEqual(track_response.status_code, 200)
        self.assertEqual(track_response.get_json()["clip"]["video_id"], "abc123")

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
