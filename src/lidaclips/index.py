import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .models import ClipTarget


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClipIndex:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.initialize()

    def initialize(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    lidarr_track_id INTEGER PRIMARY KEY,
                    artist_id INTEGER NOT NULL,
                    album_id INTEGER NOT NULL,
                    artist TEXT NOT NULL,
                    album TEXT NOT NULL,
                    album_year INTEGER,
                    title TEXT NOT NULL,
                    track_number TEXT,
                    absolute_track_number INTEGER,
                    duration INTEGER,
                    source_file_path TEXT,
                    navidrome_song_id TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS clips (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lidarr_track_id INTEGER NOT NULL,
                    video_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    score REAL NOT NULL,
                    quality_tier TEXT NOT NULL DEFAULT 'fallback',
                    evidence_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'completed',
                    replaced_by_clip_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(lidarr_track_id) REFERENCES tracks(lidarr_track_id)
                );

                CREATE INDEX IF NOT EXISTS idx_clips_track_status ON clips(lidarr_track_id, status);
                CREATE INDEX IF NOT EXISTS idx_tracks_navidrome_song_id ON tracks(navidrome_song_id);

                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lidarr_track_id INTEGER NOT NULL,
                    video_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    score REAL NOT NULL,
                    quality_tier TEXT NOT NULL DEFAULT 'rejected',
                    accepted INTEGER NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(lidarr_track_id) REFERENCES tracks(lidarr_track_id)
                );

                CREATE TABLE IF NOT EXISTS failures (
                    lidarr_track_id INTEGER PRIMARY KEY,
                    reason TEXT NOT NULL,
                    retry_after TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(lidarr_track_id) REFERENCES tracks(lidarr_track_id)
                );

                CREATE TABLE IF NOT EXISTS control_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    sync_paused INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS queue_state (
                    name TEXT PRIMARY KEY,
                    last_sort_key TEXT NOT NULL,
                    last_lidarr_track_id INTEGER,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._migrate_schema()

    def close(self) -> None:
        self.connection.close()

    def check_writable(self) -> dict[str, Any]:
        try:
            with self.connection:
                self.connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS health_checks (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        checked_at TEXT NOT NULL
                    )
                    """
                )
                self.connection.execute(
                    """
                    INSERT INTO health_checks (id, checked_at)
                    VALUES (1, ?)
                    ON CONFLICT(id) DO UPDATE SET checked_at = excluded.checked_at
                    """,
                    (utc_now(),),
                )
            return {"ok": True, "path": self.db_path}
        except Exception as exc:
            return {"ok": False, "path": self.db_path, "error": str(exc)}

    def upsert_track(self, target: ClipTarget, navidrome_song_id: str | None = None) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO tracks (
                    lidarr_track_id, artist_id, album_id, artist, album, album_year,
                    title, track_number, absolute_track_number, duration, source_file_path,
                    navidrome_song_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lidarr_track_id) DO UPDATE SET
                    artist_id = excluded.artist_id,
                    album_id = excluded.album_id,
                    artist = excluded.artist,
                    album = excluded.album,
                    album_year = excluded.album_year,
                    title = excluded.title,
                    track_number = excluded.track_number,
                    absolute_track_number = excluded.absolute_track_number,
                    duration = excluded.duration,
                    source_file_path = excluded.source_file_path,
                    navidrome_song_id = COALESCE(excluded.navidrome_song_id, tracks.navidrome_song_id),
                    updated_at = excluded.updated_at
                """,
                (
                    target.lidarr_track_id,
                    target.artist_id,
                    target.album_id,
                    target.artist,
                    target.album,
                    target.album_year,
                    target.title,
                    target.track_number,
                    target.absolute_track_number,
                    target.duration,
                    target.source_file_path,
                    navidrome_song_id,
                    utc_now(),
                ),
            )

    def has_completed_clip(self, lidarr_track_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM clips WHERE lidarr_track_id = ? AND status = 'completed' LIMIT 1",
            (lidarr_track_id,),
        ).fetchone()
        return row is not None

    def has_active_official_clip(self, lidarr_track_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM clips WHERE lidarr_track_id = ? AND status = 'completed' AND quality_tier = 'official' LIMIT 1",
            (lidarr_track_id,),
        ).fetchone()
        return row is not None

    def record_clip(
        self,
        lidarr_track_id: int,
        video_id: str,
        source_url: str,
        title: str,
        file_path: str,
        mime_type: str,
        score: float,
        evidence: dict[str, Any],
        quality_tier: str | None = None,
    ) -> int:
        now = utc_now()
        resolved_tier = quality_tier or evidence.get("quality_tier") or self._tier_from_evidence(evidence, accepted=True)
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO clips (
                    lidarr_track_id, video_id, source_url, title, file_path, mime_type,
                    score, quality_tier, evidence_json, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?)
                """,
                (
                    lidarr_track_id,
                    video_id,
                    source_url,
                    title,
                    file_path,
                    mime_type,
                    score,
                    resolved_tier,
                    json.dumps(evidence, sort_keys=True),
                    now,
                    now,
                ),
            )
            self.connection.execute("DELETE FROM failures WHERE lidarr_track_id = ?", (lidarr_track_id,))
            return int(cursor.lastrowid)

    def record_candidate(
        self,
        lidarr_track_id: int,
        video_id: str,
        source_url: str,
        title: str,
        score: float,
        accepted: bool,
        evidence: dict[str, Any],
        quality_tier: str | None = None,
    ) -> None:
        resolved_tier = quality_tier or evidence.get("quality_tier") or self._tier_from_evidence(evidence, accepted=accepted)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO candidates (lidarr_track_id, video_id, source_url, title, score, quality_tier, accepted, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (lidarr_track_id, video_id, source_url, title, score, resolved_tier, 1 if accepted else 0, json.dumps(evidence, sort_keys=True), utc_now()),
            )

    def record_no_match(self, lidarr_track_id: int, reason: str = "no_match", retry_after: str | None = None) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO failures (lidarr_track_id, reason, retry_after, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(lidarr_track_id) DO UPDATE SET
                    reason = excluded.reason,
                    retry_after = excluded.retry_after,
                    updated_at = excluded.updated_at
                """,
                (lidarr_track_id, reason, retry_after, utc_now()),
            )

    def get_failure(self, lidarr_track_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM failures WHERE lidarr_track_id = ?",
            (lidarr_track_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def get_clip_by_id(self, clip_id: int, include_replaced: bool = False) -> dict[str, Any] | None:
        status_condition = "" if include_replaced else "AND clips.status = 'completed'"
        row = self.connection.execute(
            f"""
            SELECT clips.*, tracks.artist, tracks.album, tracks.album_year, tracks.title AS track_title,
                   tracks.track_number, tracks.absolute_track_number, tracks.duration, tracks.source_file_path,
                   tracks.navidrome_song_id
            FROM clips
            JOIN tracks ON tracks.lidarr_track_id = clips.lidarr_track_id
            WHERE clips.id = ? {status_condition}
            """,
            (clip_id,),
        ).fetchone()
        return self._clip_row_to_dict(row)

    def get_clip_by_track(self, lidarr_track_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT clips.*, tracks.artist, tracks.album, tracks.album_year, tracks.title AS track_title,
                   tracks.track_number, tracks.absolute_track_number, tracks.duration, tracks.source_file_path,
                   tracks.navidrome_song_id
            FROM clips
            JOIN tracks ON tracks.lidarr_track_id = clips.lidarr_track_id
            WHERE clips.lidarr_track_id = ? AND clips.status = 'completed'
            ORDER BY clips.updated_at DESC, clips.id DESC
            LIMIT 1
            """,
            (lidarr_track_id,),
        ).fetchone()
        return self._clip_row_to_dict(row)

    def get_clip_by_navidrome_song_id(self, song_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT clips.*, tracks.artist, tracks.album, tracks.album_year, tracks.title AS track_title,
                   tracks.track_number, tracks.absolute_track_number, tracks.duration, tracks.source_file_path,
                   tracks.navidrome_song_id
            FROM clips
            JOIN tracks ON tracks.lidarr_track_id = clips.lidarr_track_id
            WHERE tracks.navidrome_song_id = ? AND clips.status = 'completed'
            ORDER BY clips.updated_at DESC, clips.id DESC
            LIMIT 1
            """,
            (song_id,),
        ).fetchone()
        return self._clip_row_to_dict(row)

    def search_clips(self, artist: str | None = None, album: str | None = None, track: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        conditions = ["clips.status = 'completed'"]
        params: list[Any] = []
        if artist:
            conditions.append("tracks.artist LIKE ?")
            params.append(f"%{artist}%")
        if album:
            conditions.append("tracks.album LIKE ?")
            params.append(f"%{album}%")
        if track:
            conditions.append("tracks.title LIKE ?")
            params.append(f"%{track}%")
        params.append(limit)
        rows = self.connection.execute(
            f"""
            SELECT clips.*, tracks.artist, tracks.album, tracks.album_year, tracks.title AS track_title,
                   tracks.track_number, tracks.absolute_track_number, tracks.duration, tracks.source_file_path,
                   tracks.navidrome_song_id
            FROM clips
            JOIN tracks ON tracks.lidarr_track_id = clips.lidarr_track_id
            WHERE {' AND '.join(conditions)}
            ORDER BY tracks.artist, tracks.album, tracks.absolute_track_number, tracks.title
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._clip_row_to_dict(row) for row in rows if row is not None]

    def all_clips(self, limit: int = 1000) -> list[dict[str, Any]]:
        return self.search_clips(limit=limit)

    def get_sync_paused(self) -> bool:
        row = self.connection.execute(
            "SELECT sync_paused FROM control_state WHERE id = 1"
        ).fetchone()
        return bool(row["sync_paused"]) if row is not None else False

    def set_sync_paused(self, paused: bool) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO control_state (id, sync_paused, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    sync_paused = excluded.sync_paused,
                    updated_at = excluded.updated_at
                """,
                (1 if paused else 0, utc_now()),
            )

    def get_queue_cursor(self, name: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM queue_state WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        try:
            payload["last_sort_key"] = json.loads(payload["last_sort_key"])
        except (TypeError, json.JSONDecodeError):
            payload["last_sort_key"] = None
        return payload

    def set_queue_cursor(self, name: str, last_sort_key: list[Any], lidarr_track_id: int) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO queue_state (name, last_sort_key, last_lidarr_track_id, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    last_sort_key = excluded.last_sort_key,
                    last_lidarr_track_id = excluded.last_lidarr_track_id,
                    updated_at = excluded.updated_at
                """,
                (name, json.dumps(last_sort_key, sort_keys=True), lidarr_track_id, utc_now()),
            )

    def dashboard_summary(self, recent_limit: int = 50) -> dict[str, Any]:
        active_by_tier = {
            row["quality_tier"]: int(row["count"])
            for row in self.connection.execute(
                """
                SELECT quality_tier, COUNT(*) AS count
                FROM clips
                WHERE status = 'completed'
                GROUP BY quality_tier
                """
            ).fetchall()
        }
        status_by_name = {
            row["status"]: int(row["count"])
            for row in self.connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM clips
                GROUP BY status
                """
            ).fetchall()
        }
        failure_by_reason = {
            row["reason"]: int(row["count"])
            for row in self.connection.execute(
                """
                SELECT reason, COUNT(*) AS count
                FROM failures
                GROUP BY reason
                """
            ).fetchall()
        }
        recent_clips = self.connection.execute(
            """
            SELECT clips.*, tracks.artist, tracks.album, tracks.album_year, tracks.title AS track_title,
                   tracks.track_number, tracks.absolute_track_number, tracks.duration, tracks.source_file_path,
                   tracks.navidrome_song_id
            FROM clips
            JOIN tracks ON tracks.lidarr_track_id = clips.lidarr_track_id
            WHERE clips.status = 'completed'
            ORDER BY clips.created_at DESC, clips.id DESC
            LIMIT ?
            """,
            (recent_limit,),
        ).fetchall()
        recent_failures = self.connection.execute(
            """
            SELECT failures.lidarr_track_id, failures.reason, failures.retry_after, failures.updated_at,
                   tracks.artist, tracks.album, tracks.title AS track_title
            FROM failures
            LEFT JOIN tracks ON tracks.lidarr_track_id = failures.lidarr_track_id
            ORDER BY failures.updated_at DESC
            LIMIT ?
            """,
            (recent_limit,),
        ).fetchall()
        active_clips = sum(active_by_tier.values())
        tracked_tracks = self.connection.execute("SELECT COUNT(*) AS count FROM tracks").fetchone()["count"]
        coverage_percent = round((active_clips / tracked_tracks) * 100, 1) if tracked_tracks else 0.0
        return {
            "tracked_tracks": tracked_tracks,
            "active_clips": active_clips,
            "coverage_percent": coverage_percent,
            "official_clips": active_by_tier.get("official", 0),
            "fallback_clips": active_by_tier.get("fallback", 0),
            "replaced_clips": status_by_name.get("replaced", 0),
            "failures": sum(failure_by_reason.values()),
            "no_match": failure_by_reason.get("no_match", 0),
            "proxy_unavailable": failure_by_reason.get("proxy_unavailable", 0),
            "sync_paused": self.get_sync_paused(),
            "recent_clips": [self._clip_row_to_dict(row) for row in recent_clips if row is not None],
            "recent_failures": [dict(row) for row in recent_failures if row is not None],
        }

    def path_conflicts(self, file_path: str, lidarr_track_id: int, exclude_clip_id: int | None = None) -> bool:
        conditions = [
            "file_path = ?",
            "status = 'completed'",
            "lidarr_track_id != ?",
        ]
        params: list[Any] = [file_path, lidarr_track_id]
        if exclude_clip_id is not None:
            conditions.append("id != ?")
            params.append(exclude_clip_id)
        row = self.connection.execute(
            f"SELECT 1 FROM clips WHERE {' AND '.join(conditions)} LIMIT 1",
            params,
        ).fetchone()
        return row is not None

    def update_clip_file_path(self, clip_id: int, file_path: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE clips
                SET file_path = ?, updated_at = ?
                WHERE id = ?
                """,
                (file_path, utc_now(), clip_id),
            )

    def active_fallback_clips(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT clips.*, tracks.artist, tracks.album, tracks.album_year, tracks.title AS track_title,
                   tracks.track_number, tracks.absolute_track_number, tracks.duration, tracks.source_file_path,
                   tracks.navidrome_song_id
            FROM clips
            JOIN tracks ON tracks.lidarr_track_id = clips.lidarr_track_id
            WHERE clips.status = 'completed' AND clips.quality_tier = 'fallback'
            ORDER BY tracks.artist, tracks.album, tracks.absolute_track_number, tracks.title
            """
        ).fetchall()
        return [self._clip_row_to_dict(row) for row in rows if row is not None]

    def mark_clip_replaced(self, clip_id: int, replaced_by_clip_id: int) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE clips
                SET status = 'replaced', replaced_by_clip_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (replaced_by_clip_id, utc_now(), clip_id),
            )

    def _clip_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        payload["evidence"] = json.loads(payload.pop("evidence_json") or "{}")
        payload["stream_url"] = f"/api/v1/stream/{payload['id']}"
        return payload

    def _migrate_schema(self) -> None:
        self._ensure_column("clips", "quality_tier", "TEXT NOT NULL DEFAULT 'fallback'")
        self._ensure_column("clips", "replaced_by_clip_id", "INTEGER")
        self._ensure_column("candidates", "quality_tier", "TEXT NOT NULL DEFAULT 'rejected'")
        self.connection.execute(
            """
            UPDATE clips
            SET quality_tier = 'official'
            WHERE evidence_json LIKE '%official%'
            """
        )
        self.connection.execute(
            """
            UPDATE candidates
            SET quality_tier = 'official'
            WHERE accepted = 1 AND evidence_json LIKE '%official%'
            """
        )
        self.connection.execute(
            """
            UPDATE candidates
            SET quality_tier = 'fallback'
            WHERE accepted = 1 AND quality_tier NOT IN ('official', 'fallback')
            """
        )
        self.connection.execute(
            """
            UPDATE candidates
            SET quality_tier = 'rejected'
            WHERE accepted = 0 AND quality_tier NOT IN ('official', 'fallback', 'rejected')
            """
        )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _tier_from_evidence(self, evidence: dict[str, Any], accepted: bool) -> str:
        if not accepted:
            return "rejected"
        quality_tier = evidence.get("quality_tier")
        if quality_tier in {"official", "fallback", "rejected"}:
            return quality_tier
        reasons = evidence.get("reasons") or []
        if isinstance(reasons, list) and "official" in reasons:
            return "official"
        if evidence.get("official"):
            return "official"
        return "fallback"
