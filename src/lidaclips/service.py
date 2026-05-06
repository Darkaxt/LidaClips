import logging
from typing import Any

from .index import ClipIndex
from .scoring import ClipScorer


class LidaClipsService:
    def __init__(
        self,
        index: ClipIndex,
        lidarr_client: Any,
        candidate_search: Any,
        scorer: ClipScorer,
        downloader: Any,
        navidrome_client: Any | None = None,
        logger: logging.Logger | None = None,
    ):
        self.index = index
        self.lidarr_client = lidarr_client
        self.candidate_search = candidate_search
        self.scorer = scorer
        self.downloader = downloader
        self.navidrome_client = navidrome_client
        self.logger = logger or logging.getLogger(__name__)

    def sync_once(self) -> dict[str, int]:
        summary = {
            "targets": 0,
            "downloaded": 0,
            "no_match": 0,
            "download_errors": 0,
            "navidrome_missing": 0,
        }
        targets = self.lidarr_client.collect_pending_tracks(self.index)
        summary["targets"] = len(targets)
        for target in targets:
            if self.navidrome_client is not None:
                song_id = self.navidrome_client.find_song_id(target.artist, target.album, target.title)
                if not song_id or not self.navidrome_client.is_song_present(song_id):
                    self.index.record_no_match(target.lidarr_track_id, "navidrome_missing")
                    summary["navidrome_missing"] += 1
                    continue
                self.index.upsert_track(target, navidrome_song_id=song_id)

            candidates = self.candidate_search.search(target)
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
