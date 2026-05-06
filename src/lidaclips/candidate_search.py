import json
import os
import shutil
import subprocess
from typing import Callable

from .models import ClipTarget
from .scoring import Candidate


class YtDlpCandidateSearch:
    def __init__(
        self,
        limit: int = 10,
        cookies_path: str | None = None,
        ytdlp_factory: Callable | None = None,
        ytdlp_binary: str = "",
        js_runtime_path: str | None = None,
    ):
        self.limit = int(limit)
        self.cookies_path = cookies_path
        self.ytdlp_factory = ytdlp_factory
        self.ytdlp_binary = ytdlp_binary
        self.js_runtime_path = js_runtime_path if js_runtime_path is not None else shutil.which("node")

    def search(self, target: ClipTarget) -> list[Candidate]:
        query = f"ytsearch{self.limit}:{target.artist} {target.title} official music video"
        if self.ytdlp_binary and self.ytdlp_factory is None:
            return self._search_with_binary(query)
        options = {
            "quiet": True,
            "extract_flat": False,
            "skip_download": True,
            "noplaylist": True,
        }
        if self.cookies_path:
            options["cookiefile"] = self.cookies_path
        if self.js_runtime_path:
            options["js_runtimes"] = {"node": {"path": self.js_runtime_path}}
        with self._factory()(options) as ydl:
            result = ydl.extract_info(query, download=False)
        entries = (result or {}).get("entries") or []
        return [self._candidate_from(entry) for entry in entries if entry]

    def _search_with_binary(self, query: str) -> list[Candidate]:
        binary = self._resolve_binary()
        command = [binary, "--dump-json", "--skip-download", "--no-playlist", query]
        if self.cookies_path:
            command.extend(["--cookies", self.cookies_path])
        if self.js_runtime_path:
            command.extend(["--js-runtimes", f"node:{self.js_runtime_path}"])
        result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
        entries = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        return [self._candidate_from(entry) for entry in entries if entry]

    def _candidate_from(self, entry: dict) -> Candidate:
        video_id = entry.get("id") or entry.get("display_id") or ""
        webpage_url = entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
        tags = entry.get("tags") or ()
        return Candidate(
            video_id=video_id,
            title=entry.get("title") or "",
            webpage_url=webpage_url,
            channel=entry.get("channel") or "",
            uploader=entry.get("uploader") or "",
            duration=entry.get("duration"),
            view_count=entry.get("view_count"),
            channel_follower_count=entry.get("channel_follower_count"),
            channel_is_verified=entry.get("channel_is_verified"),
            description=entry.get("description") or "",
            tags=tuple(tags),
            raw=entry,
        )

    def _factory(self):
        if self.ytdlp_factory is not None:
            return self.ytdlp_factory
        import yt_dlp

        return yt_dlp.YoutubeDL

    def _resolve_binary(self) -> str:
        if os.path.isdir(self.ytdlp_binary):
            exe = os.path.join(self.ytdlp_binary, "yt-dlp.exe")
            if os.path.exists(exe):
                return exe
            return os.path.join(self.ytdlp_binary, "yt-dlp")
        return self.ytdlp_binary
