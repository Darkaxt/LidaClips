import json
import subprocess
from typing import Callable


class StaticVideoError(RuntimeError):
    def __init__(self, reason: str = "static_visuals", metrics: dict | None = None):
        self.reason = reason
        self.metrics = metrics or {}
        super().__init__(reason)


class MotionValidator:
    def __init__(
        self,
        frame_sampler: Callable[[str], list[bytes]] | None = None,
        sample_count: int = 8,
        frame_width: int = 96,
        frame_height: int = 54,
        min_frames: int = 4,
        min_duration: float = 30.0,
        min_average_delta: float = 0.01,
        min_max_delta: float = 0.02,
    ):
        self.frame_sampler = frame_sampler
        self.sample_count = int(sample_count)
        self.frame_width = int(frame_width)
        self.frame_height = int(frame_height)
        self.min_frames = int(min_frames)
        self.min_duration = float(min_duration)
        self.min_average_delta = float(min_average_delta)
        self.min_max_delta = float(min_max_delta)

    def validate(self, path: str) -> dict:
        frames = self._sample_frames(path)
        metrics = self._motion_metrics(frames)
        if metrics.get("static"):
            raise StaticVideoError("static_visuals", metrics)
        return metrics

    def _sample_frames(self, path: str) -> list[bytes]:
        if self.frame_sampler is not None:
            return self.frame_sampler(path)

        duration = self._duration(path)
        if duration < self.min_duration:
            return []

        offsets = [
            duration * (index + 1) / (self.sample_count + 1)
            for index in range(self.sample_count)
        ]
        frames = []
        for offset in offsets:
            frame = self._sample_frame(path, offset)
            if frame:
                frames.append(frame)
        return frames

    def _duration(self, path: str) -> float:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    path,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout or "{}")
            return float((payload.get("format") or {}).get("duration") or 0)
        except Exception:
            return 0.0

    def _sample_frame(self, path: str, offset: float) -> bytes:
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    f"{offset:.3f}",
                    "-i",
                    path,
                    "-frames:v",
                    "1",
                    "-vf",
                    (
                        f"scale={self.frame_width}:{self.frame_height}:"
                        "force_original_aspect_ratio=decrease,"
                        f"pad={self.frame_width}:{self.frame_height}:(ow-iw)/2:(oh-ih)/2,"
                        "format=gray"
                    ),
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                check=True,
                capture_output=True,
            )
            expected_size = self.frame_width * self.frame_height
            return result.stdout if len(result.stdout) == expected_size else b""
        except Exception:
            return b""

    def _motion_metrics(self, frames: list[bytes]) -> dict:
        frame_count = len(frames)
        if frame_count < self.min_frames:
            return {
                "static": False,
                "skipped": True,
                "frame_count": frame_count,
                "reason": "not_enough_frames",
            }

        deltas = []
        for previous, current in zip(frames, frames[1:]):
            if not previous or len(previous) != len(current):
                continue
            total_delta = sum(abs(left - right) for left, right in zip(previous, current))
            deltas.append(total_delta / (len(previous) * 255))

        if not deltas:
            return {
                "static": False,
                "skipped": True,
                "frame_count": frame_count,
                "reason": "not_enough_comparable_frames",
            }

        average_delta = sum(deltas) / len(deltas)
        max_delta = max(deltas)
        return {
            "static": average_delta < self.min_average_delta and max_delta < self.min_max_delta,
            "skipped": False,
            "frame_count": frame_count,
            "average_delta": round(average_delta, 6),
            "max_delta": round(max_delta, 6),
            "pair_deltas": [round(delta, 6) for delta in deltas],
        }
