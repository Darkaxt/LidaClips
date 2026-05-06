import logging
from typing import Any

from .index import ClipIndex
from .scoring import ClipScorer
from .text import normalize_text


class LidaClipsService:
    def __init__(
        self,
        index: ClipIndex,
        lidarr_client: Any,
        candidate_search: Any,
        scorer: ClipScorer,
        downloader: Any,
        navidrome_client: Any | None = None,
        sync_artist_allowlist: list[str] | None = None,
        max_targets_per_run: int | None = None,
        download_enabled: bool = True,
        logger: logging.Logger | None = None,
    ):
        self.index = index
        self.lidarr_client = lidarr_client
        self.candidate_search = candidate_search
        self.scorer = scorer
        self.downloader = downloader
        self.navidrome_client = navidrome_client
        self.sync_artist_allowlist = {
            normalize_text(artist) for artist in (sync_artist_allowlist or []) if normalize_text(artist)
        }
        self.max_targets_per_run = max_targets_per_run
        self.download_enabled = bool(download_enabled)
        self.logger = logger or logging.getLogger(__name__)

    def sync_once(self) -> dict[str, int]:
        summary = {
            "targets": 0,
            "downloaded": 0,
            "no_match": 0,
            "download_errors": 0,
            "navidrome_missing": 0,
            "skipped_by_allowlist": 0,
            "limited": 0,
            "processed": 0,
            "download_disabled": 0,
            "search_errors": 0,
        }
        targets = self.lidarr_client.collect_pending_tracks(self.index)
        summary["targets"] = len(targets)
        targets = self._filter_targets(targets, summary)
        summary["processed"] = len(targets)
        for target in targets:
            if self.navidrome_client is not None:
                song_id = self.navidrome_client.find_song_id(target.artist, target.album, target.title)
                if not song_id or not self.navidrome_client.is_song_present(song_id):
                    self.index.record_no_match(target.lidarr_track_id, "navidrome_missing")
                    summary["navidrome_missing"] += 1
                    continue
                self.index.upsert_track(target, navidrome_song_id=song_id)

            try:
                candidates = self.candidate_search.search(target)
            except Exception as exc:
                self.logger.exception("Candidate search failed for %s - %s", target.artist, target.title)
                self.index.record_no_match(target.lidarr_track_id, f"candidate_search_error: {exc}")
                summary["search_errors"] += 1
                continue

            best_candidate = None
            best_decision = None
            for candidate in candidates:
                decision = self.scorer.score(target.artist, target.title, target.duration, candidate)
                self.index.record_candidate(
                    lidarr_track_id=target.lidarr_track_id,
                    video_id=candidate.video_id,
                    source_url=candidate.webpage_url,
                    title=candidate.title,
                    score=decision.score,
                    accepted=decision.accepted,
                    evidence=decision.to_evidence(),
                )
                if decision.accepted and (best_decision is None or decision.score > best_decision.score):
                    best_candidate = candidate
                    best_decision = decision

            if best_candidate is None or best_decision is None:
                self.index.record_no_match(target.lidarr_track_id, "no_match")
                summary["no_match"] += 1
                continue

            if not self.download_enabled:
                self.index.record_no_match(target.lidarr_track_id, "download_disabled")
                summary["download_disabled"] += 1
                continue

            try:
                download_result = self.downloader.download(target, best_candidate)
            except Exception as exc:
                self.logger.exception("Clip download failed for %s - %s", target.artist, target.title)
                self.index.record_no_match(target.lidarr_track_id, f"download_error: {exc}")
                summary["download_errors"] += 1
                continue

            self.index.record_clip(
                lidarr_track_id=target.lidarr_track_id,
                video_id=best_candidate.video_id,
                source_url=best_candidate.webpage_url,
                title=best_candidate.title,
                file_path=download_result["file_path"],
                mime_type=download_result.get("mime_type") or "video/mp4",
                score=best_decision.score,
                evidence=best_decision.to_evidence(),
            )
            summary["downloaded"] += 1
        return summary

    def _filter_targets(self, targets: list[Any], summary: dict[str, int]) -> list[Any]:
        filtered = targets
        if self.sync_artist_allowlist:
            filtered = [
                target for target in filtered if normalize_text(target.artist) in self.sync_artist_allowlist
            ]
            summary["skipped_by_allowlist"] = len(targets) - len(filtered)

        if self.max_targets_per_run is not None and self.max_targets_per_run >= 0:
            limit = int(self.max_targets_per_run)
            if len(filtered) > limit:
                summary["limited"] = len(filtered) - limit
                filtered = filtered[:limit]
        return filtered

    def health_check(self) -> dict[str, Any]:
        checks: dict[str, Any] = {
            "database": self.index.check_writable(),
            "lidarr": self._dependency_check(self.lidarr_client),
        }
        storage = getattr(self.downloader, "storage", None)
        if storage is not None and hasattr(storage, "check_paths"):
            checks.update(storage.check_paths())
        else:
            checks["staging"] = {"ok": False, "error": "downloader storage is not configured"}
            checks["clips"] = {"ok": False, "error": "downloader storage is not configured"}

        if self.navidrome_client is None:
            checks["navidrome"] = {"ok": True, "skipped": True}
        else:
            checks["navidrome"] = self._dependency_check(self.navidrome_client)

        status = "ok" if all(check.get("ok") for check in checks.values()) else "degraded"
        return {"status": status, "checks": checks}

    def _dependency_check(self, client: Any) -> dict[str, Any]:
        if hasattr(client, "ping"):
            return client.ping()
        return {"ok": False, "error": "client has no ping method"}
