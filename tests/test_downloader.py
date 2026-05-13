import os
import socketserver
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from lidaclips.downloader import ClipDownloader
from lidaclips.models import ClipTarget
from lidaclips.scoring import Candidate
from lidaclips.storage import ClipStorage


class FakeYtDlp:
    def __init__(self, options):
        self.options = options
        self.downloaded = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def download(self, urls):
        self.downloaded.extend(urls)
        output_path = self.options["outtmpl"]
        final_path = output_path.replace("%(ext)s", self.options["merge_output_format"])
        with open(final_path, "wb") as handle:
            handle.write(b"video")


class FailingYtDlp(FakeYtDlp):
    def download(self, urls):
        self.downloaded.extend(urls)
        output_path = self.options["outtmpl"].replace("%(ext)s", "mp4")
        with open(f"{output_path}.part", "wb") as handle:
            handle.write(b"partial")
        raise RuntimeError("HTTP Error 403: Forbidden")


class ProxyHealthHandler(socketserver.BaseRequestHandler):
    def handle(self):
        request = self.request.recv(4096)
        if request.startswith(b"CONNECT "):
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")


class DownloaderTests(unittest.TestCase):
    def test_download_uses_video_format_and_finalizes_to_storage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
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
                source_file_path="/music/The Example Band/Neon Nights/01 - Bright Lights.flac",
            )
            candidate = Candidate(
                video_id="abc123",
                title="The Example Band - Bright Lights (Official Music Video)",
                webpage_url="https://www.youtube.com/watch?v=abc123",
            )
            storage = ClipStorage(
                output_mode="clips_lane",
                output_path=os.path.join(temp_dir, "clips"),
                staging_path=os.path.join(temp_dir, "staging"),
            )
            downloader = ClipDownloader(storage=storage, preferred_container="mp4", max_resolution=720, ytdlp_factory=factory)

            result = downloader.download(target, candidate)

            self.assertEqual(created[0].downloaded, ["https://www.youtube.com/watch?v=abc123"])
            self.assertIn("height<=720", created[0].options["format"])
            self.assertIn("vcodec^=avc1", created[0].options["format"])
            self.assertIn("acodec^=mp4a", created[0].options["format"])
            self.assertEqual(created[0].options["merge_output_format"], "mp4")
            self.assertEqual(result["mime_type"], "video/mp4")
            self.assertTrue(result["file_path"].endswith(os.path.join("Neon Nights (2020)", "01 - Bright Lights.mp4")))
            with open(result["file_path"], "rb") as handle:
                self.assertEqual(handle.read(), b"video")

    def test_download_enables_configured_node_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
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
            candidate = Candidate(video_id="abc123", title="Official", webpage_url="https://www.youtube.com/watch?v=abc123")
            storage = ClipStorage(
                output_mode="clips_lane",
                output_path=os.path.join(temp_dir, "clips"),
                staging_path=os.path.join(temp_dir, "staging"),
            )
            downloader = ClipDownloader(storage=storage, ytdlp_factory=factory, js_runtime_path="/usr/bin/node")

            downloader.download(target, candidate)

            self.assertEqual(created[0].options["js_runtimes"], {"node": {"path": "/usr/bin/node"}})

    def test_download_falls_back_to_hls_when_primary_format_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            created = []

            def factory(options):
                instance = FailingYtDlp(options) if not created else FakeYtDlp(options)
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
            candidate = Candidate(video_id="abc123", title="Official", webpage_url="https://www.youtube.com/watch?v=abc123")
            storage = ClipStorage(
                output_mode="clips_lane",
                output_path=os.path.join(temp_dir, "clips"),
                staging_path=os.path.join(temp_dir, "staging"),
            )
            downloader = ClipDownloader(storage=storage, ytdlp_factory=factory, max_resolution=720)

            result = downloader.download(target, candidate)

            self.assertEqual(len(created), 2)
            self.assertIn("vcodec^=avc1", created[0].options["format"])
            self.assertIn("acodec^=mp4a", created[0].options["format"])
            self.assertIn("protocol*=m3u8", created[1].options["format"])
            self.assertTrue(os.path.exists(result["file_path"]))

    def test_download_transcodes_non_compatible_mp4_before_finalizing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            created = []

            def factory(options):
                instance = FakeYtDlp(options)
                created.append(instance)
                return instance

            def fake_run(command, check, capture_output=False, text=False):
                if command[0] == "ffprobe":
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        stdout='{"streams":[{"codec_type":"video","codec_name":"av1"},{"codec_type":"audio","codec_name":"opus"}]}',
                        stderr="",
                    )
                if command[0] == "ffmpeg":
                    output_path = command[-1]
                    with open(output_path, "wb") as handle:
                        handle.write(b"compatible")
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                raise AssertionError(command)

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
            candidate = Candidate(video_id="abc123", title="Official", webpage_url="https://www.youtube.com/watch?v=abc123")
            storage = ClipStorage(
                output_mode="clips_lane",
                output_path=os.path.join(temp_dir, "clips"),
                staging_path=os.path.join(temp_dir, "staging"),
            )
            downloader = ClipDownloader(storage=storage, ytdlp_factory=factory, preferred_container="mp4")

            with patch("lidaclips.downloader.subprocess.run", side_effect=fake_run):
                result = downloader.download(target, candidate)

            with open(result["file_path"], "rb") as handle:
                self.assertEqual(handle.read(), b"compatible")

    def test_download_passes_po_token_args_only_to_primary_dash_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            created = []

            def factory(options):
                instance = FailingYtDlp(options) if not created else FakeYtDlp(options)
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
            candidate = Candidate(video_id="abc123", title="Official", webpage_url="https://www.youtube.com/watch?v=abc123")
            storage = ClipStorage(
                output_mode="clips_lane",
                output_path=os.path.join(temp_dir, "clips"),
                staging_path=os.path.join(temp_dir, "staging"),
            )
            downloader = ClipDownloader(
                storage=storage,
                ytdlp_factory=factory,
                max_resolution=720,
                youtube_po_provider="bgutil_http",
                youtube_po_provider_url="http://lidaclips-pot:4416",
                youtube_player_clients=["mweb", "default"],
            )

            downloader.download(target, candidate)

            self.assertEqual(
                created[0].options["extractor_args"],
                {
                    "youtube": {"player_client": ["mweb", "default"]},
                    "youtubepot-bgutilhttp": {"base_url": ["http://lidaclips-pot:4416"]},
                },
            )
            self.assertNotIn("extractor_args", created[1].options)

    def test_download_can_disable_hls_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            created = []

            def factory(options):
                instance = FailingYtDlp(options)
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
            candidate = Candidate(video_id="abc123", title="Official", webpage_url="https://www.youtube.com/watch?v=abc123")
            storage = ClipStorage(
                output_mode="clips_lane",
                output_path=os.path.join(temp_dir, "clips"),
                staging_path=os.path.join(temp_dir, "staging"),
            )
            downloader = ClipDownloader(storage=storage, ytdlp_factory=factory, youtube_enable_hls_fallback=False)

            with self.assertRaisesRegex(RuntimeError, "HTTP Error 403"):
                downloader.download(target, candidate)

            self.assertEqual(len(created), 1)

    def test_download_passes_configured_proxy_to_yt_dlp(self):
        with tempfile.TemporaryDirectory() as temp_dir:
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
            candidate = Candidate(video_id="abc123", title="Official", webpage_url="https://www.youtube.com/watch?v=abc123")
            storage = ClipStorage(
                output_mode="clips_lane",
                output_path=os.path.join(temp_dir, "clips"),
                staging_path=os.path.join(temp_dir, "staging"),
            )
            downloader = ClipDownloader(
                storage=storage,
                ytdlp_factory=factory,
                youtube_proxy_url="http://youtube-proxy:8888",
            )

            downloader.download(target, candidate)

            self.assertEqual(created[0].options["proxy"], "http://youtube-proxy:8888")

    def test_youtube_proxy_health_reports_success_for_connect_proxy(self):
        with socketserver.ThreadingTCPServer(("127.0.0.1", 0), ProxyHealthHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address
            downloader = ClipDownloader(
                storage=ClipStorage("clips_lane", "/unused", "/unused"),
                youtube_proxy_url=f"http://{host}:{port}",
            )

            result = downloader.youtube_proxy_health()

            self.assertTrue(result["ok"])
            self.assertEqual(result["address"], f"http://{host}:{port}")
            server.shutdown()

    def test_youtube_proxy_health_reports_connection_failure(self):
        downloader = ClipDownloader(
            storage=ClipStorage("clips_lane", "/unused", "/unused"),
            youtube_proxy_url="http://127.0.0.1:9",
        )

        result = downloader.youtube_proxy_health()

        self.assertFalse(result["ok"])
        self.assertEqual(result["address"], "http://127.0.0.1:9")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
