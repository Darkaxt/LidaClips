import glob
import os
import shutil
from typing import Callable

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
    ):
        self.storage = storage
        self.preferred_container = preferred_container.lstrip(".")
        self.max_resolution = int(max_resolution)
        self.cookies_path = cookies_path
        self.ytdlp_factory = ytdlp_factory
        self.js_runtime_path = js_runtime_path if js_runtime_path is not None else shutil.which("node")

    def download(self, target: ClipTarget, candidate: Candidate) -> dict[str, str]:
        staged_template = self.storage.staging_file(candidate.video_id, f".%(ext)s")
        errors = []
        for format_selector in self._format_selectors():
            self._clear_staged_files(staged_template)
            options = self._options(staged_template, format_selector)
            try:
                with self._factory()(options) as ydl:
                    ydl.download([candidate.webpage_url])
                break
            except Exception as exc:
                errors.append(exc)
        else:
            raise errors[-1]

        staged_path = self._find_staged_file(staged_template)
        final_path = self.storage.final_path(target, candidate.video_id, f".{self.preferred_container}")
        file_path = self.storage.finalize(staged_path, final_path)
        return {"file_path": file_path, "mime_type": f"video/{self.preferred_container}"}

    def _format_selectors(self) -> list[str]:
        return [
            f"bv*[height<={self.max_resolution}]+ba/b[height<={self.max_resolution}]/best",
            f"best[protocol*=m3u8][height<={self.max_resolution}]/best[height<={self.max_resolution}]/best",
        ]

    def _options(self, staged_template: str, format_selector: str) -> dict:
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
        if self.js_runtime_path:
            options["js_runtimes"] = {"node": {"path": self.js_runtime_path}}
        return options

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
