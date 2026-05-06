import logging
import os
from dataclasses import dataclass
from typing import Any

from .index import ClipIndex
from .models import ClipTarget
from .scoring import Candidate, ClipScorer, MatchDecision
from .text import normalize_text


@dataclass(frozen=True)
class PlannedTarget:
    target: ClipTarget
    mode: str
    existing_clip: dict[str, Any] | None = None


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
        upgrade_min_score_delta: float = 10.0,
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
        self.upgrade_min_score_delta = float(upgrade_min_score_delta)
        self.logger = logger or logging.getLogger(__name__)

    def sync_once(self) -> dict[str, int]:
        summary = {
            "targets": 0,
            "downloaded": 0,
            "fallback_downloaded": 0,
            "official_downloaded": 0,
            "no_match": 0,
            "download_errors": 0,
            "navidrome_missing": 0,
            "skipped_by_allowlist": 0,
            "limited": 0,
            "processed": 0,
            "upgrade_targets": 0,
            "upgraded": 0,
            "no_upgrade": 0,
            "download_disabled": 0,
            "search_errors": 0,
            "reconciled": 0,
            "reconcile_errors": 0,
        }
        targets = self._collect_present_targets()
        reconciled, reconcile_errors = self._reconcile_completed_clip_paths()
        summary["reconciled"] = reconciled
        summary["reconcile_errors"] = reconcile_errors
        summary["targets"] = len(targets)
        planned_targets = self._plan_targets(targets)
        planned_targets = self._filter_planned_targets(planned_targets, summary)
        summary["processed"] = len(planned_targets)
        summary["upgrade_targets"] = sum(1 for item in planned_targets if item.mode == "upgrade")
        for planned in planned_targets:
            target = planned.target
            if self.navidrome_client is not None:
                song_id = self.navidrome_client.find_song_id(target.artist, target.album, target.title)
                if not song_id or not self.navidrome_client.is_song_present(song_id):
                    if planned.mode == "missing":
                        self.index.record_no_match(target.lidarr_track_id, "navidrome_missing")
                    summary["navidrome_missing"] += 1
                    continue
                self.index.upsert_track(target, navidrome_song_id=song_id)

            try:
                candidates = self.candidate_search.search(target)
            except Exception as exc:
                self.logger.exception("Candidate search failed for %s - %s", target.artist, target.title)
                if planned.mode == "missing":
                    self.index.record_no_match(target.lidarr_track_id, f"candidate_search_error: {exc}")
                summary["search_errors"] += 1
                continue

            best_candidate, best_decision = self._score_candidates(target, candidates)

            if best_candidate is None or best_decision is None:
                if planned.mode == "upgrade":
                    summary["no_upgrade"] += 1
                else:
                    self.index.record_no_match(target.lidarr_track_id, "no_match")
                    summary["no_match"] += 1
                continue

            if planned.mode == "upgrade" and not self._should_upgrade(planned.existing_clip, best_decision):
                summary["no_upgrade"] += 1
                continue

            if not self.download_enabled:
                if planned.mode == "missing":
                    self.index.record_no_match(target.lidarr_track_id, "download_disabled")
                summary["download_disabled"] += 1
                continue

            try:
                download_result = self.downloader.download(target, best_candidate)
            except Exception as exc:
                self.logger.exception("Clip download failed for %s - %s", target.artist, target.title)
                if planned.mode == "missing":
                    self.index.record_no_match(target.lidarr_track_id, f"download_error: {exc}")
                summary["download_errors"] += 1
                continue

            clip_id = self.index.record_clip(
                lidarr_track_id=target.lidarr_track_id,
                video_id=best_candidate.video_id,
                source_url=best_candidate.webpage_url,
                title=best_candidate.title,
                file_path=download_result["file_path"],
                mime_type=download_result.get("mime_type") or "video/mp4",
                score=best_decision.score,
                evidence=best_decision.to_evidence(),
                quality_tier=best_decision.quality_tier,
            )
            summary["downloaded"] += 1
            if best_decision.quality_tier == "official":
                summary["official_downloaded"] += 1
            elif best_decision.quality_tier == "fallback":
                summary["fallback_downloaded"] += 1
            if planned.mode == "upgrade" and planned.existing_clip is not None:
                self._mark_replaced_and_delete_old(planned.existing_clip, clip_id, download_result["file_path"])
                summary["upgraded"] += 1
        return summary

    def collect_planned_targets(self) -> list[ClipTarget]:
        summary = {"skipped_by_allowlist": 0, "limited": 0}
        return [planned.target for planned in self._filter_planned_targets(self._plan_targets(self._collect_present_targets()), summary)]

    def _collect_present_targets(self) -> list[ClipTarget]:
        if hasattr(self.lidarr_client, "collect_present_tracks"):
            targets = self.lidarr_client.collect_present_tracks()
            for target in targets:
                self.index.upsert_track(target)
            return targets
        return self.lidarr_client.collect_pending_tracks(self.index)

    def _plan_targets(self, targets: list[ClipTarget]) -> list[PlannedTarget]:
        missing: list[PlannedTarget] = []
        upgrades: list[PlannedTarget] = []
        for target in targets:
            active_clip = self.index.get_clip_by_track(target.lidarr_track_id)
            if active_clip is None:
                missing.append(PlannedTarget(target=target, mode="missing"))
            elif active_clip.get("quality_tier") == "fallback":
                upgrades.append(PlannedTarget(target=target, mode="upgrade", existing_clip=active_clip))
        return missing + upgrades

    def _filter_planned_targets(self, planned_targets: list[PlannedTarget], summary: dict[str, int]) -> list[PlannedTarget]:
        filtered = planned_targets
        if self.sync_artist_allowlist:
            filtered = [
                planned for planned in filtered if normalize_text(planned.target.artist) in self.sync_artist_allowlist
            ]
            summary["skipped_by_allowlist"] = len(planned_targets) - len(filtered)

        if self.max_targets_per_run is not None and self.max_targets_per_run >= 0:
            limit = int(self.max_targets_per_run)
            if len(filtered) > limit:
                summary["limited"] = len(filtered) - limit
                filtered = filtered[:limit]
        return filtered

    def _score_candidates(self, target: ClipTarget, candidates: list[Candidate]) -> tuple[Candidate | None, MatchDecision | None]:
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
                quality_tier=decision.quality_tier,
            )
            if decision.accepted and (best_decision is None or self._decision_better_than(decision, best_decision)):
                best_candidate = candidate
                best_decision = decision
        return best_candidate, best_decision

    def _decision_better_than(self, candidate: MatchDecision, current: MatchDecision) -> bool:
        ranks = {"rejected": 0, "fallback": 1, "official": 2}
        candidate_rank = ranks.get(candidate.quality_tier, 0)
        current_rank = ranks.get(current.quality_tier, 0)
        if candidate_rank != current_rank:
            return candidate_rank > current_rank
        return candidate.score > current.score

    def _should_upgrade(self, existing_clip: dict[str, Any] | None, decision: MatchDecision) -> bool:
        if existing_clip is None:
            return False
        if existing_clip.get("quality_tier") == "official":
            return False
        if decision.quality_tier == "official":
            return True
        if decision.quality_tier != existing_clip.get("quality_tier"):
            return False
        return float(decision.score) >= float(existing_clip.get("score") or 0) + self.upgrade_min_score_delta

    def _mark_replaced_and_delete_old(self, old_clip: dict[str, Any], new_clip_id: int, new_file_path: str) -> None:
        self.index.mark_clip_replaced(int(old_clip["id"]), new_clip_id)
        old_file_path = old_clip.get("file_path")
        if not old_file_path:
            return
        if os.path.normcase(os.path.abspath(old_file_path)) == os.path.normcase(os.path.abspath(new_file_path)):
            return
        try:
            if os.path.exists(old_file_path):
                os.remove(old_file_path)
        except OSError:
            self.logger.exception("Failed to delete replaced clip file %s", old_file_path)

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

        if hasattr(self.downloader, "po_provider_health"):
            po_provider_check = self.downloader.po_provider_health()
            if not po_provider_check.get("skipped"):
                checks["po_provider"] = po_provider_check

        status = "ok" if all(check.get("ok") for check in checks.values()) else "degraded"
        return {"status": status, "checks": checks}

    def _dependency_check(self, client: Any) -> dict[str, Any]:
        if hasattr(client, "ping"):
            return client.ping()
        return {"ok": False, "error": "client has no ping method"}

    def _reconcile_completed_clip_paths(self) -> tuple[int, int]:
        storage = getattr(self.downloader, "storage", None)
        if storage is None or not hasattr(storage, "final_path") or not hasattr(storage, "move_existing"):
            return 0, 0

        reconciled = 0
        errors = 0
        for row in self.index.all_clips():
            target = self._target_from_clip_row(row)
            old_path = row["file_path"]
            extension = os.path.splitext(old_path)[1] or f".{getattr(self.downloader, 'preferred_container', 'mp4')}"
            try:
                expected_path = storage.final_path(
                    target,
                    row["video_id"],
                    extension,
                    conflict_checker=lambda path, clip=row, item=target: self.index.path_conflicts(
                        path,
                        item.lidarr_track_id,
                        exclude_clip_id=clip["id"],
                    ),
                )
                if os.path.normcase(os.path.abspath(old_path)) == os.path.normcase(os.path.abspath(expected_path)):
                    continue
                if os.path.exists(expected_path):
                    if not os.path.exists(old_path):
                        self.index.update_clip_file_path(row["id"], expected_path)
                        reconciled += 1
                    continue
                if not os.path.exists(old_path):
                    continue
                moved_path = storage.move_existing(old_path, expected_path)
                self.index.update_clip_file_path(row["id"], moved_path)
                reconciled += 1
            except Exception:
                self.logger.exception("Clip path reconciliation failed for clip %s", row["id"])
                errors += 1
        return reconciled, errors

    def _target_from_clip_row(self, row: dict[str, Any]) -> ClipTarget:
        return ClipTarget(
            lidarr_track_id=int(row["lidarr_track_id"]),
            artist_id=0,
            album_id=0,
            artist=row["artist"],
            album=row["album"],
            album_year=row.get("album_year"),
            title=row["track_title"],
            track_number=row.get("track_number") or "",
            absolute_track_number=int(row.get("absolute_track_number") or 0),
            duration=row.get("duration"),
            source_file_path=row.get("source_file_path"),
        )
