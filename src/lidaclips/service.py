import logging
import os
from dataclasses import dataclass
from typing import Any

from .index import ClipIndex
from .media_validation import StaticVideoError
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
            "youtube_searches": 0,
            "upgrade_targets": 0,
            "upgraded": 0,
            "no_upgrade": 0,
            "download_disabled": 0,
            "search_errors": 0,
            "youtube_auth_blocked": 0,
            "static_rejected": 0,
            "proxy_unavailable": 0,
            "reconciled": 0,
            "reconcile_errors": 0,
            "queue_wrapped": 0,
        }
        targets = self._collect_present_targets()
        reconciled, reconcile_errors = self._reconcile_completed_clip_paths()
        summary["reconciled"] = reconciled
        summary["reconcile_errors"] = reconcile_errors
        summary["targets"] = len(targets)
        planned_targets = self._plan_targets(targets)
        planned_targets = self._filter_planned_targets(planned_targets, summary, apply_limit=False)
        youtube_searches = 0
        for index, planned in enumerate(planned_targets):
            if self._search_cap_reached(youtube_searches):
                summary["limited"] = len(planned_targets) - index
                break
            summary["processed"] += 1
            if planned.mode == "upgrade":
                summary["upgrade_targets"] += 1
            target = planned.target
            if self.navidrome_client is not None:
                song_id = self.navidrome_client.find_song_id(target.artist, target.album, target.title)
                if not song_id or not self.navidrome_client.is_song_present(song_id):
                    if planned.mode == "missing":
                        self.index.record_no_match(target.lidarr_track_id, "navidrome_missing")
                    summary["navidrome_missing"] += 1
                    self._advance_queue_cursor(planned)
                    continue
                self.index.upsert_track(target, navidrome_song_id=song_id)

            try:
                youtube_searches += 1
                summary["youtube_searches"] = youtube_searches
                candidates = self.candidate_search.search(target)
            except Exception as exc:
                if self._is_proxy_unavailable(exc):
                    self.logger.exception("YouTube proxy unavailable while searching for %s - %s", target.artist, target.title)
                    summary["proxy_unavailable"] += 1
                    self.index.set_sync_paused(True)
                    break
                self.logger.exception("Candidate search failed for %s - %s", target.artist, target.title)
                if planned.mode == "missing":
                    self.index.record_no_match(target.lidarr_track_id, f"candidate_search_error: {exc}")
                summary["search_errors"] += 1
                if self._is_youtube_auth_block(exc):
                    summary["youtube_auth_blocked"] += 1
                self._advance_queue_cursor(planned)
                continue

            ranked_candidates = self._score_candidates(target, candidates)

            if not ranked_candidates:
                if planned.mode == "upgrade":
                    summary["no_upgrade"] += 1
                else:
                    self.index.record_no_match(target.lidarr_track_id, "no_match")
                    summary["no_match"] += 1
                self._advance_queue_cursor(planned)
                continue

            if planned.mode == "upgrade":
                ranked_candidates = [
                    (candidate, decision)
                    for candidate, decision in ranked_candidates
                    if self._should_upgrade(planned.existing_clip, decision)
                ]

            if not ranked_candidates:
                summary["no_upgrade"] += 1
                self._advance_queue_cursor(planned)
                continue

            if not self.download_enabled:
                if planned.mode == "missing":
                    self.index.record_no_match(target.lidarr_track_id, "download_disabled")
                summary["download_disabled"] += 1
                self._advance_queue_cursor(planned)
                continue

            completed_download = False
            static_rejections_for_target = 0
            abort_sync = False
            for best_candidate, best_decision in ranked_candidates:
                try:
                    download_result = self.downloader.download(target, best_candidate)
                except StaticVideoError as exc:
                    self.logger.warning("Rejected static visual clip for %s - %s: %s", target.artist, target.title, best_candidate.video_id)
                    self._record_static_candidate(target, best_candidate, best_decision, exc)
                    summary["static_rejected"] += 1
                    static_rejections_for_target += 1
                    continue
                except Exception as exc:
                    if self._is_proxy_unavailable(exc):
                        self.logger.exception("YouTube proxy unavailable while downloading %s - %s", target.artist, target.title)
                        summary["proxy_unavailable"] += 1
                        self.index.set_sync_paused(True)
                        abort_sync = True
                        break
                    self.logger.exception("Clip download failed for %s - %s", target.artist, target.title)
                    if planned.mode == "missing":
                        self.index.record_no_match(target.lidarr_track_id, f"download_error: {exc}")
                    summary["download_errors"] += 1
                    if self._is_youtube_auth_block(exc):
                        summary["youtube_auth_blocked"] += 1
                    self._advance_queue_cursor(planned)
                    completed_download = True
                    break

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
                self._advance_queue_cursor(planned)
                completed_download = True
                break
            if abort_sync:
                break
            if completed_download:
                continue
            if static_rejections_for_target:
                if planned.mode == "upgrade":
                    summary["no_upgrade"] += 1
                else:
                    self.index.record_no_match(target.lidarr_track_id, "static_visuals")
                    summary["no_match"] += 1
                self._advance_queue_cursor(planned)
        return summary

    def collect_planned_targets(self) -> list[ClipTarget]:
        summary = {"skipped_by_allowlist": 0, "limited": 0, "queue_wrapped": 0}
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
        return missing or upgrades

    def _filter_planned_targets(
        self,
        planned_targets: list[PlannedTarget],
        summary: dict[str, int],
        apply_limit: bool = True,
    ) -> list[PlannedTarget]:
        filtered = planned_targets
        if self.sync_artist_allowlist:
            filtered = [
                planned for planned in filtered if normalize_text(planned.target.artist) in self.sync_artist_allowlist
            ]
            summary["skipped_by_allowlist"] = len(planned_targets) - len(filtered)

        filtered = self._rotate_planned_targets(filtered, summary)

        if apply_limit and self.max_targets_per_run is not None and self.max_targets_per_run >= 0:
            limit = int(self.max_targets_per_run)
            if len(filtered) > limit:
                summary["limited"] = len(filtered) - limit
                filtered = filtered[:limit]
        return filtered

    def _search_cap_reached(self, youtube_searches: int) -> bool:
        if self.max_targets_per_run is None or self.max_targets_per_run < 0:
            return False
        return youtube_searches >= int(self.max_targets_per_run)

    def _rotate_planned_targets(self, planned_targets: list[PlannedTarget], summary: dict[str, int]) -> list[PlannedTarget]:
        if not planned_targets:
            return []
        sorted_targets = sorted(planned_targets, key=self._planned_sort_key)
        cursor = self.index.get_queue_cursor("acquisition") if hasattr(self.index, "get_queue_cursor") else None
        cursor_key = cursor.get("last_sort_key") if cursor else None
        if not isinstance(cursor_key, list):
            return sorted_targets
        for index, planned in enumerate(sorted_targets):
            if self._planned_sort_key(planned) > cursor_key:
                return sorted_targets[index:] + sorted_targets[:index]
        summary["queue_wrapped"] = summary.get("queue_wrapped", 0) + 1
        return sorted_targets

    def _advance_queue_cursor(self, planned: PlannedTarget) -> None:
        if hasattr(self.index, "set_queue_cursor"):
            self.index.set_queue_cursor(
                "acquisition",
                self._planned_sort_key(planned),
                planned.target.lidarr_track_id,
            )

    def _planned_sort_key(self, planned: PlannedTarget) -> list[Any]:
        target = planned.target
        mode_rank = 0 if planned.mode == "missing" else 1
        return [
            mode_rank,
            normalize_text(target.artist),
            normalize_text(target.album),
            int(target.absolute_track_number or 0),
            normalize_text(target.title),
            int(target.lidarr_track_id),
        ]

    def _score_candidates(self, target: ClipTarget, candidates: list[Candidate]) -> list[tuple[Candidate, MatchDecision]]:
        static_rejections = set()
        if hasattr(self.index, "rejected_video_ids"):
            static_rejections = self.index.rejected_video_ids(target.lidarr_track_id, "static_visuals")
        ranked_candidates: list[tuple[Candidate, MatchDecision]] = []
        for candidate in candidates:
            if candidate.video_id in static_rejections:
                continue
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
            if decision.accepted:
                ranked_candidates.append((candidate, decision))
        ranked_candidates.sort(key=lambda item: self._decision_sort_key(item[1]), reverse=True)
        return ranked_candidates

    def _decision_better_than(self, candidate: MatchDecision, current: MatchDecision) -> bool:
        return self._decision_sort_key(candidate) > self._decision_sort_key(current)

    def _decision_sort_key(self, decision: MatchDecision) -> tuple[int, float]:
        ranks = {"rejected": 0, "fallback": 1, "official": 2}
        return ranks.get(decision.quality_tier, 0), float(decision.score)

    def _record_static_candidate(
        self,
        target: ClipTarget,
        candidate: Candidate,
        decision: MatchDecision,
        exc: StaticVideoError,
    ) -> None:
        evidence = decision.to_evidence()
        evidence.update(
            {
                "accepted": False,
                "quality_tier": "rejected",
                "rejection_reason": exc.reason,
                "validation": exc.metrics,
            }
        )
        self.index.record_candidate(
            lidarr_track_id=target.lidarr_track_id,
            video_id=candidate.video_id,
            source_url=candidate.webpage_url,
            title=candidate.title,
            score=decision.score,
            accepted=False,
            evidence=evidence,
            quality_tier="rejected",
        )

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

        if hasattr(self.downloader, "youtube_proxy_health"):
            youtube_proxy_check = self.downloader.youtube_proxy_health()
            if not youtube_proxy_check.get("skipped"):
                checks["youtube_proxy"] = youtube_proxy_check

        status = "ok" if all(check.get("ok") for check in checks.values()) else "degraded"
        return {"status": status, "checks": checks}

    def _dependency_check(self, client: Any) -> dict[str, Any]:
        if hasattr(client, "ping"):
            return client.ping()
        return {"ok": False, "error": "client has no ping method"}

    def _is_youtube_auth_block(self, exc: Exception) -> bool:
        message = str(exc).lower()
        if "youtube" not in message:
            return False
        return (
            "sign in to confirm" in message
            or "not a bot" in message
            or "use --cookies-from-browser" in message
        )

    def _is_proxy_unavailable(self, exc: Exception) -> bool:
        message = str(exc).lower()
        if "proxy" not in message:
            return False
        return (
            "unable to connect to proxy" in message
            or "connection refused" in message
            or "failed to establish a new connection" in message
            or "newconnectionerror" in message
            or "proxyerror" in message
        )

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
