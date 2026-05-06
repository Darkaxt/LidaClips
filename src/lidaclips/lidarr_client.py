from typing import Any

import httpx

from .index import ClipIndex
from .models import ClipTarget
from .text import parse_year


class LidarrError(RuntimeError):
    pass


class LidarrClient:
    def __init__(self, address: str, api_key: str, timeout: float = 120.0, session: Any | None = None):
        self.address = address.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or httpx.Client()

    def collect_pending_tracks(self, index: ClipIndex) -> list[ClipTarget]:
        targets = self.collect_present_tracks()
        for target in targets:
            index.upsert_track(target)
        return [target for target in targets if not index.has_completed_clip(target.lidarr_track_id)]

    def collect_present_tracks(self) -> list[ClipTarget]:
        targets: list[ClipTarget] = []
        for album in self._get_albums():
            if album.get("statistics", {}).get("trackFileCount") == 0:
                continue
            for track in self._get_tracks(album["id"]):
                if not track.get("hasFile", False):
                    continue
                targets.append(self._target_from(album, track))
        targets.sort(key=lambda item: (item.artist.lower(), item.album.lower(), item.absolute_track_number, item.title.lower()))
        return targets

    def _get_albums(self) -> list[dict[str, Any]]:
        return self._get("/api/v1/album", {"includeArtist": "true"})

    def _get_tracks(self, album_id: int) -> list[dict[str, Any]]:
        return self._get("/api/v1/track", {"albumId": album_id})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        request_params = dict(params or {})
        request_params["apikey"] = self.api_key
        response = self.session.get(f"{self.address}{path}", params=request_params, timeout=self.timeout)
        if response.status_code != 200:
            raise LidarrError(f"Lidarr API error {response.status_code}: {response.text}")
        return response.json()

    def _target_from(self, album: dict[str, Any], track: dict[str, Any]) -> ClipTarget:
        artist = album.get("artist") or {}
        audio_file = track.get("audioFile") or track.get("trackFile") or {}
        return ClipTarget(
            lidarr_track_id=int(track["id"]),
            artist_id=int(album.get("artistId") or artist.get("id") or 0),
            album_id=int(album["id"]),
            artist=artist.get("artistName") or album.get("artistName") or "",
            album=album.get("title") or "",
            album_year=parse_year(album.get("releaseDate")),
            title=track.get("title") or "",
            track_number=str(track.get("trackNumber") or ""),
            absolute_track_number=int(track.get("absoluteTrackNumber") or track.get("trackNumber") or 0),
            duration=self._duration_seconds(track.get("duration")),
            source_file_path=audio_file.get("path") or track.get("path"),
        )

    def _duration_seconds(self, value: Any) -> int | None:
        if value in ("", None):
            return None
        try:
            duration = int(float(value))
        except (TypeError, ValueError):
            return None
        if duration > 20_000:
            return int(duration / 1000)
        return duration
