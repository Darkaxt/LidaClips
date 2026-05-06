import errno
import os
import shutil
from typing import Any, Callable

from .models import ClipTarget
from .text import compact_track_number, safe_filename


class ClipStorage:
    def __init__(self, output_mode: str, output_path: str, staging_path: str):
        if output_mode not in {"clips_lane", "sidecar"}:
            raise ValueError("output_mode must be 'clips_lane' or 'sidecar'")
        self.output_mode = output_mode
        self.output_path = output_path
        self.staging_path = staging_path

    def final_path(
        self,
        target: ClipTarget,
        video_id: str,
        extension: str,
        conflict_checker: Callable[[str], bool] | None = None,
    ) -> str:
        clean_extension = extension if extension.startswith(".") else f".{extension}"
        if self.output_mode == "sidecar":
            source_path = target.source_file_path
            if not source_path:
                raise ValueError("sidecar output requires target.source_file_path")
            base, _ = os.path.splitext(source_path)
            expected_path = f"{base}{clean_extension}"
            return self._apply_conflict_suffix(expected_path, target, clean_extension, conflict_checker)

        artist_folder = safe_filename(target.artist, "Unknown Artist")
        album_label = safe_filename(target.album, "Unknown Album")
        if target.album_year:
            album_label = f"{album_label} ({target.album_year})"
        filename = f"{self._clip_basename(target)}{clean_extension}"
        expected_path = os.path.join(self.output_path, artist_folder, album_label, filename)
        return self._apply_conflict_suffix(expected_path, target, clean_extension, conflict_checker)

    def staging_file(self, video_id: str, extension: str) -> str:
        clean_extension = extension if extension.startswith(".") else f".{extension}"
        os.makedirs(self.staging_path, exist_ok=True)
        return os.path.join(self.staging_path, f"{safe_filename(video_id, 'video')}.download{clean_extension}")

    def finalize(self, staged_path: str, final_path: str) -> str:
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        try:
            os.replace(staged_path, final_path)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            temp_final_path = f"{final_path}.partial"
            shutil.copy2(staged_path, temp_final_path)
            os.replace(temp_final_path, final_path)
            os.remove(staged_path)
        return final_path

    def move_existing(self, source_path: str, final_path: str) -> str:
        if os.path.normcase(os.path.abspath(source_path)) == os.path.normcase(os.path.abspath(final_path)):
            return final_path
        return self.finalize(source_path, final_path)

    def check_paths(self) -> dict[str, dict[str, Any]]:
        checks = {
            "staging": self._check_writable_directory(self.staging_path),
        }
        if self.output_mode == "clips_lane":
            checks["clips"] = self._check_writable_directory(self.output_path)
        else:
            checks["clips"] = {"ok": True, "path": self.output_mode, "skipped": True}
        return checks

    def _check_writable_directory(self, path: str) -> dict[str, Any]:
        try:
            os.makedirs(path, exist_ok=True)
            probe_path = os.path.join(path, ".lidaclips-healthcheck.tmp")
            with open(probe_path, "w", encoding="utf-8") as handle:
                handle.write("ok")
            os.remove(probe_path)
            return {"ok": True, "path": path}
        except Exception as exc:
            return {"ok": False, "path": path, "error": str(exc)}

    def _clip_basename(self, target: ClipTarget) -> str:
        if target.source_file_path:
            source_basename = os.path.splitext(os.path.basename(target.source_file_path))[0]
            if source_basename:
                return safe_filename(source_basename)
        track_no = compact_track_number(target.track_number, target.absolute_track_number)
        return f"{track_no} - {safe_filename(target.title)}"

    def _apply_conflict_suffix(
        self,
        expected_path: str,
        target: ClipTarget,
        extension: str,
        conflict_checker: Callable[[str], bool] | None,
    ) -> str:
        if conflict_checker is None or not conflict_checker(expected_path):
            return expected_path
        base, _ = os.path.splitext(expected_path)
        return f"{base} [lidarr-{target.lidarr_track_id}]{extension}"
