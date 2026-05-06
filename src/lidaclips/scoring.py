import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable

try:
    from thefuzz import fuzz
except ModuleNotFoundError:
    class _FallbackFuzz:
        def ratio(self, left: str, right: str) -> int:
            return int(round(100 * SequenceMatcher(None, left, right).ratio()))

        def partial_ratio(self, left: str, right: str) -> int:
            if not left or not right:
                return 0
            short, long = sorted((left, right), key=len)
            if short in long:
                return 100
            return self.ratio(short, long)

        def token_set_ratio(self, left: str, right: str) -> int:
            left_tokens = set(left.split())
            right_tokens = set(right.split())
            if not left_tokens or not right_tokens:
                return 0
            common = left_tokens & right_tokens
            if common and (common == left_tokens or common == right_tokens):
                return 100
            left_joined = " ".join(sorted(left_tokens))
            right_joined = " ".join(sorted(right_tokens))
            common_joined = " ".join(sorted(common))
            return max(self.ratio(left_joined, right_joined), self.ratio(common_joined, left_joined), self.ratio(common_joined, right_joined))

    fuzz = _FallbackFuzz()

from .text import normalize_text


BLOCKED_KEYWORDS = {
    "audio",
    "behind the scenes",
    "cover",
    "karaoke",
    "live",
    "lyrics",
    "lyric",
    "performance",
    "reaction",
    "remix",
    "shorts",
    "topic",
    "visualiser",
    "visualizer",
}

OFFICIAL_KEYWORDS = {
    "official music video",
    "official video",
    "music video",
}


@dataclass(frozen=True)
class Candidate:
    video_id: str
    title: str
    webpage_url: str
    channel: str = ""
    uploader: str = ""
    duration: int | None = None
    view_count: int | None = None
    channel_follower_count: int | None = None
    channel_is_verified: bool | None = None
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchDecision:
    accepted: bool
    score: float
    reasons: tuple[str, ...]
    rejection_reason: str | None = None

    def to_evidence(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "score": self.score,
            "reasons": list(self.reasons),
            "rejection_reason": self.rejection_reason,
        }


class ClipScorer:
    def __init__(self, minimum_score: float = 75.0):
        self.minimum_score = float(minimum_score)

    def score(self, artist: str, title: str, expected_duration: int | None, candidate: Candidate) -> MatchDecision:
        blocked = self._blocked_reason(candidate)
        if blocked:
            return MatchDecision(False, 0.0, tuple(sorted(blocked)), "blocked_keyword")

        artist_norm = normalize_text(artist)
        title_norm = normalize_text(title)
        expected_norm = normalize_text(f"{artist} {title}")
        candidate_title_norm = normalize_text(candidate.title)
        channel_norm = normalize_text(candidate.channel or candidate.uploader)

        title_ratio = max(
            fuzz.token_set_ratio(title_norm, candidate_title_norm),
            fuzz.partial_ratio(title_norm, candidate_title_norm),
        )
        combined_ratio = fuzz.token_set_ratio(expected_norm, candidate_title_norm)
        artist_ratio = max(
            fuzz.token_set_ratio(artist_norm, channel_norm),
            fuzz.token_set_ratio(artist_norm, candidate_title_norm),
        )
        duration_score = self._duration_score(expected_duration, candidate.duration)

        reasons: list[str] = []
        score = 0.0
        score += title_ratio * 0.24
        score += combined_ratio * 0.22
        score += artist_ratio * 0.22
        score += duration_score * 0.12

        if self._looks_official(candidate):
            score += 12
            reasons.append("official")
        if candidate.channel_is_verified:
            score += 6
            reasons.append("verified_channel")
        if self._looks_like_artist_channel(artist_norm, channel_norm, candidate):
            score += 6
            reasons.append("artist_channel")
        if (candidate.channel_follower_count or 0) >= 100_000:
            score += 4
            reasons.append("substantial_channel")
        if (candidate.view_count or 0) >= 1_000_000:
            score += 3
            reasons.append("popular_video")

        score = min(round(score, 2), 100.0)

        if artist_ratio < 55:
            return MatchDecision(False, score, tuple(reasons), "low_score")
        if title_ratio < 70:
            return MatchDecision(False, score, tuple(reasons), "low_score")
        if score < self.minimum_score:
            return MatchDecision(False, score, tuple(reasons), "low_score")
        return MatchDecision(True, score, tuple(reasons), None)

    def choose_best(self, artist: str, title: str, expected_duration: int | None, candidates: Iterable[Candidate]) -> tuple[Candidate | None, MatchDecision | None]:
        best_candidate: Candidate | None = None
        best_decision: MatchDecision | None = None
        for candidate in candidates:
            decision = self.score(artist, title, expected_duration, candidate)
            if best_decision is None or decision.score > best_decision.score:
                best_candidate = candidate
                best_decision = decision
        if best_decision and best_decision.accepted:
            return best_candidate, best_decision
        return None, best_decision

    def _blocked_reason(self, candidate: Candidate) -> set[str]:
        haystacks = [
            normalize_text(candidate.title),
            normalize_text(candidate.channel),
            normalize_text(candidate.uploader),
        ]
        reasons: set[str] = set()
        for haystack in haystacks:
            if not haystack:
                continue
            if haystack.endswith(" topic") or " topic " in haystack:
                reasons.add("topic")
            for keyword in BLOCKED_KEYWORDS:
                if keyword == "audio":
                    if "official audio" in haystack or haystack.endswith(" audio"):
                        reasons.add(keyword)
                elif self._contains_word_or_phrase(haystack, keyword):
                    reasons.add(keyword)
        return reasons

    def _looks_official(self, candidate: Candidate) -> bool:
        title_norm = normalize_text(candidate.title)
        channel_norm = normalize_text(candidate.channel or candidate.uploader)
        if any(keyword in title_norm for keyword in OFFICIAL_KEYWORDS):
            return True
        return channel_norm.endswith("vevo")

    def _looks_like_artist_channel(self, artist_norm: str, channel_norm: str, candidate: Candidate) -> bool:
        if not artist_norm or not channel_norm:
            return False
        if artist_norm == channel_norm:
            return True
        if channel_norm == f"{artist_norm} vevo":
            return True
        return bool(candidate.channel_is_verified and fuzz.token_set_ratio(artist_norm, channel_norm) >= 85)

    def _duration_score(self, expected: int | None, actual: int | None) -> float:
        if not expected or not actual:
            return 60.0
        difference = abs(int(expected) - int(actual))
        if difference <= 30:
            return 100.0
        if difference <= 90:
            return 82.0
        if difference <= 150:
            return 55.0
        return 15.0

    def _contains_word_or_phrase(self, haystack: str, keyword: str) -> bool:
        if " " in keyword:
            return keyword in haystack
        return bool(re.search(rf"\b{re.escape(keyword)}\b", haystack))
