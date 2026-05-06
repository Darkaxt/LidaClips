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
                    evidence_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'completed',
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
                """
            )

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
    ) -> int:
        now = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                """
                INSERT INTO clips (
                    lidarr_track_id, video_id, source_url, title, file_path, mime_type,
                    score, evidence_json, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?)
                """,
                (
                    lidarr_track_id,
                    video_id,
                    source_url,
                    title,
                    file_path,
                    mime_type,
                    score,
                    json.dumps(evidence, sort_keys=True),
                    now,
                    now,
                ),
            )
            self.connection.execute("DELETE FROM failures WHERE lidarr_track_id = ?", (lidarr_track_id,))
            return int(cursor.lastrowid)

    def record_candidate(self, lidarr_track_id: int, video_id: str, source_url: str, title: str, score: float, accepted: bool, evidence: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO candidates (lidarr_track_id, video_id, source_url, title, score, accepted, evidence_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (lidarr_track_id, video_id, source_url, title, score, 1 if accepted else 0, json.dumps(evidence, sort_keys=True), utc_now()),
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

    def get_clip_by_id(self, clip_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT clips.*, tracks.artist, tracks.album, tracks.album_year, tracks.title AS track_title,
                   tracks.track_number, tracks.absolute_track_number, tracks.duration, tracks.navidrome_song_id
            FROM clips
            JOIN tracks ON tracks.lidarr_track_id = clips.lidarr_track_id
            WHERE clips.id = ? AND clips.status = 'completed'
            """,
            (clip_id,),
        ).fetchone()
        return self._clip_row_to_dict(row)

    def get_clip_by_track(self, lidarr_track_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT clips.*, tracks.artist, tracks.album, tracks.album_year, tracks.title AS track_title,
                   tracks.track_number, tracks.absolute_track_number, tracks.duration, tracks.navidrome_song_id
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
                   tracks.track_number, tracks.absolute_track_number, tracks.duration, tracks.navidrome_song_id
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
                   tracks.track_number, tracks.absolute_track_number, tracks.duration, tracks.navidrome_song_id
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

    def _clip_row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        payload["evidence"] = json.loads(payload.pop("evidence_json") or "{}")
        payload["stream_url"] = f"/api/v1/stream/{payload['id']}"
        return payload
