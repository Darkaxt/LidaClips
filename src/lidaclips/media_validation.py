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
        sample_interval_seconds: int = 6,
        ffmpeg_timeout_seconds: int = 45,
        min_average_delta: float = 0.01,
        min_max_delta: float = 0.02,
    ):
        self.frame_sampler = frame_sampler
        self.sample_count = int(sample_count)
        self.frame_width = int(frame_width)
        self.frame_height = int(frame_height)
        self.min_frames = int(min_frames)
        self.sample_interval_seconds = int(sample_interval_seconds)
        self.ffmpeg_timeout_seconds = int(ffmpeg_timeout_seconds)
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

        frame_size = self.frame_width * self.frame_height
        if frame_size <= 0:
            return []
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    path,
                    "-vf",
                    (
                        f"fps=1/{self.sample_interval_seconds},"
                        f"scale={self.frame_width}:{self.frame_height}:"
                        "force_original_aspect_ratio=decrease,"
                        f"pad={self.frame_width}:{self.frame_height}:(ow-iw)/2:(oh-ih)/2,"
                        "format=gray"
                    ),
                    "-frames:v",
                    str(self.sample_count),
                    "-f",
                    "rawvideo",
                    "pipe:1",
                ],
                check=True,
                capture_output=True,
                timeout=self.ffmpeg_timeout_seconds,
            )
        except Exception:
            return []

        frames = []
        for offset in range(0, len(result.stdout), frame_size):
            frame = result.stdout[offset:offset + frame_size]
            if len(frame) == frame_size:
                frames.append(frame)
        return frames

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
