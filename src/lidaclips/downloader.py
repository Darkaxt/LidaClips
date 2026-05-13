import glob
import json
import os
import shutil
import socket
import subprocess
from typing import Callable
from urllib.parse import urlsplit

import httpx

from .models import ClipTarget
from .scoring import Candidate
from .storage import ClipStorage


class ClipDownloader:
    def __init__(
        self,
        storage: ClipStorage,
        preferred_container: str = "mp4",
        max_resolution: int = 1080,
        cookies_path: str | None = None,
        ytdlp_factory: Callable | None = None,
        js_runtime_path: str | None = None,
        youtube_po_provider: str = "off",
        youtube_po_provider_url: str = "http://lidaclips-pot:4416",
        youtube_player_clients: list[str] | None = None,
        youtube_enable_hls_fallback: bool = True,
        youtube_proxy_url: str = "",
        path_conflict_checker: Callable[[str, ClipTarget], bool] | None = None,
    ):
        self.storage = storage
        self.preferred_container = preferred_container.lstrip(".")
        self.max_resolution = int(max_resolution)
        self.cookies_path = cookies_path
        self.ytdlp_factory = ytdlp_factory
        self.js_runtime_path = js_runtime_path if js_runtime_path is not None else shutil.which("node")
        self.youtube_po_provider = youtube_po_provider
        self.youtube_po_provider_url = youtube_po_provider_url.rstrip("/")
        self.youtube_player_clients = youtube_player_clients or ["mweb", "default"]
        self.youtube_enable_hls_fallback = bool(youtube_enable_hls_fallback)
        self.youtube_proxy_url = youtube_proxy_url.strip()
        self.path_conflict_checker = path_conflict_checker

    def download(self, target: ClipTarget, candidate: Candidate) -> dict[str, str]:
        staged_template = self.storage.staging_file(candidate.video_id, f".%(ext)s")
        errors = []
        for format_selector, use_po_provider in self._download_attempts():
            self._clear_staged_files(staged_template)
            options = self._options(staged_template, format_selector, use_po_provider)
            try:
                with self._factory()(options) as ydl:
                    ydl.download([candidate.webpage_url])
                break
            except Exception as exc:
                errors.append(exc)
        else:
            raise errors[-1]

        staged_path = self._ensure_container_compatibility(self._find_staged_file(staged_template))
        conflict_checker = None
        if self.path_conflict_checker is not None:
            conflict_checker = lambda path: self.path_conflict_checker(path, target)
        final_path = self.storage.final_path(target, candidate.video_id, f".{self.preferred_container}", conflict_checker=conflict_checker)
        file_path = self.storage.finalize(staged_path, final_path)
        return {"file_path": file_path, "mime_type": f"video/{self.preferred_container}"}

    def _download_attempts(self) -> list[tuple[str, bool]]:
        attempts = [
            f"bv*[vcodec^=avc1][height<={self.max_resolution}]+ba[acodec^=mp4a]/b[vcodec^=avc1][acodec^=mp4a][height<={self.max_resolution}]/best[vcodec^=avc1][height<={self.max_resolution}]/best[height<={self.max_resolution}]/best",
        ]
        if self.youtube_enable_hls_fallback:
            attempts.append(f"best[protocol*=m3u8][vcodec^=avc1][acodec^=mp4a][height<={self.max_resolution}]/best[protocol*=m3u8][height<={self.max_resolution}]/best[height<={self.max_resolution}]/best")
        return [(format_selector, index == 0) for index, format_selector in enumerate(attempts)]

    def _options(self, staged_template: str, format_selector: str, use_po_provider: bool = False) -> dict:
        options = {
            "quiet": False,
            "noplaylist": True,
            "format": format_selector,
            "merge_output_format": self.preferred_container,
            "outtmpl": staged_template,
            "postprocessors": [{"key": "FFmpegMetadata"}],
        }
        if self.cookies_path:
            options["cookiefile"] = self.cookies_path
        if self.youtube_proxy_url:
            options["proxy"] = self.youtube_proxy_url
        if self.js_runtime_path:
            options["js_runtimes"] = {"node": {"path": self.js_runtime_path}}
        extractor_args = self._extractor_args(use_po_provider)
        if extractor_args:
            options["extractor_args"] = extractor_args
        return options

    def _extractor_args(self, use_po_provider: bool) -> dict | None:
        if not use_po_provider or self.youtube_po_provider != "bgutil_http":
            return None
        return {
            "youtube": {"player_client": list(self.youtube_player_clients)},
            "youtubepot-bgutilhttp": {"base_url": [self.youtube_po_provider_url]},
        }

    def po_provider_health(self) -> dict[str, str | bool | int]:
        if self.youtube_po_provider != "bgutil_http":
            return {"ok": True, "skipped": True}
        try:
            response = httpx.get(self.youtube_po_provider_url, timeout=10)
            return {
                "ok": response.status_code < 500,
                "address": self.youtube_po_provider_url,
                "status_code": response.status_code,
            }
        except Exception as exc:
            return {"ok": False, "address": self.youtube_po_provider_url, "error": str(exc)}

    def youtube_proxy_health(self) -> dict[str, str | bool]:
        if not self.youtube_proxy_url:
            return {"ok": True, "skipped": True}

        parsed = urlsplit(self.youtube_proxy_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return {"ok": False, "address": self.youtube_proxy_url, "error": "unsupported or invalid proxy URL"}

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        target = "www.youtube.com:443"
        request = (
            f"CONNECT {target} HTTP/1.1\r\n"
            f"Host: {target}\r\n"
            "Proxy-Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        try:
            with socket.create_connection((parsed.hostname, port), timeout=10) as connection:
                if parsed.scheme == "https":
                    return {"ok": False, "address": self.youtube_proxy_url, "error": "HTTPS proxy health checks are not supported"}
                connection.sendall(request)
                response = connection.recv(128).decode("iso-8859-1", errors="replace")
            status_line = response.splitlines()[0] if response else ""
            return {
                "ok": status_line.startswith("HTTP/") and " 200 " in status_line,
                "address": self.youtube_proxy_url,
                "target": target,
                "status_line": status_line,
            }
        except Exception as exc:
            return {"ok": False, "address": self.youtube_proxy_url, "target": target, "error": str(exc)}

    def _factory(self):
        if self.ytdlp_factory is not None:
            return self.ytdlp_factory
        import yt_dlp

        return yt_dlp.YoutubeDL

    def _clear_staged_files(self, staged_template: str) -> None:
        pattern = staged_template.replace("%(ext)s", "*")
        for path in glob.glob(pattern):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def _find_staged_file(self, staged_template: str) -> str:
        if "%(ext)s" not in staged_template:
            if os.path.exists(staged_template):
                return staged_template
            raise FileNotFoundError(staged_template)
        pattern = staged_template.replace("%(ext)s", "*")
        matches = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        if not matches:
            raise FileNotFoundError(f"No staged download matched {pattern}")
        return matches[0]

    def _ensure_container_compatibility(self, staged_path: str) -> str:
        if self.preferred_container != "mp4":
            return staged_path
        if not self._needs_mp4_transcode(staged_path):
            return staged_path

        base, _ = os.path.splitext(staged_path)
        compatible_path = f"{base}.compatible.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                staged_path,
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                compatible_path,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        os.remove(staged_path)
        return compatible_path

    def _needs_mp4_transcode(self, staged_path: str) -> bool:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=codec_type,codec_name",
                    "-of",
                    "json",
                    staged_path,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout or "{}")
        except Exception:
            return False
        streams = payload.get("streams") or []
        video_codecs = {stream.get("codec_name") for stream in streams if stream.get("codec_type") == "video"}
        audio_codecs = {stream.get("codec_name") for stream in streams if stream.get("codec_type") == "audio"}
        if not video_codecs:
            return True
        if video_codecs - {"h264"}:
            return True
        return bool(audio_codecs and audio_codecs - {"aac"})
