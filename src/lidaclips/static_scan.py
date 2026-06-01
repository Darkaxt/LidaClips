import argparse
import json
import os
import shutil
from typing import Any

from .index import ClipIndex
from .media_validation import MotionValidator, StaticVideoError


def scan_active_static_clips(
    index: ClipIndex,
    validator: Any,
    quarantine_path: str | None = None,
    dry_run: bool = False,
    limit: int = 100000,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "scanned": 0,
        "missing_files": 0,
        "rejected": 0,
        "items": [],
    }
    for clip in index.all_clips(limit=limit):
        file_path = clip.get("file_path") or ""
        if not os.path.exists(file_path):
            result["missing_files"] += 1
            continue
        result["scanned"] += 1
        try:
            validator.validate(file_path)
        except StaticVideoError as exc:
            item = _reject_static_clip(index, clip, exc, quarantine_path, dry_run)
            result["items"].append(item)
            result["rejected"] += 1
    return result


def _reject_static_clip(
    index: ClipIndex,
    clip: dict[str, Any],
    exc: StaticVideoError,
    quarantine_path: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    item = {
        "clip_id": clip["id"],
        "lidarr_track_id": clip["lidarr_track_id"],
        "artist": clip.get("artist"),
        "album": clip.get("album"),
        "track": clip.get("track_title"),
        "video_id": clip.get("video_id"),
        "file_path": clip.get("file_path"),
        "metrics": exc.metrics,
    }
    if dry_run:
        item["dry_run"] = True
        return item

    evidence = dict(clip.get("evidence") or {})
    evidence.update(
        {
            "accepted": False,
            "quality_tier": "rejected",
            "rejection_reason": exc.reason,
            "validation": exc.metrics,
            "previous_clip_id": clip["id"],
        }
    )
    index.record_candidate(
        lidarr_track_id=int(clip["lidarr_track_id"]),
        video_id=clip["video_id"],
        source_url=clip["source_url"],
        title=clip["title"],
        score=float(clip.get("score") or 0),
        accepted=False,
        evidence=evidence,
        quality_tier="rejected",
    )
    index.mark_clip_rejected(int(clip["id"]), exc.reason)
    if quarantine_path:
        item["quarantine_path"] = _quarantine_file(clip["file_path"], quarantine_path, int(clip["id"]))
    return item


def _quarantine_file(file_path: str, quarantine_path: str, clip_id: int) -> str:
    os.makedirs(quarantine_path, exist_ok=True)
    destination = os.path.join(quarantine_path, f"{clip_id} - {os.path.basename(file_path)}")
    base, extension = os.path.splitext(destination)
    suffix = 1
    while os.path.exists(destination):
        destination = f"{base}.{suffix}{extension}"
        suffix += 1
    shutil.move(file_path, destination)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan active LidaClips files and reject static album-art videos.")
    parser.add_argument("--db", default="/lidaclips/config/lidaclips.db")
    parser.add_argument("--quarantine-path", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=100000)
    args = parser.parse_args(argv)

    index = ClipIndex(args.db)
    try:
        result = scan_active_static_clips(
            index,
            MotionValidator(),
            quarantine_path=args.quarantine_path or None,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    finally:
        index.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
