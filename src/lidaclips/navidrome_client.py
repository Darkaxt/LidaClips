from typing import Any

import httpx

from .text import normalize_text


class NavidromeError(RuntimeError):
    pass


class NavidromeClient:
    def __init__(
        self,
        address: str,
        user: str,
        token_or_password: str,
        timeout: float = 60.0,
        client_name: str = "LidaClips",
        session: Any | None = None,
    ):
        self.address = address.rstrip("/")
        self.user = user
        self.token_or_password = token_or_password
        self.timeout = timeout
        self.client_name = client_name
        self.session = session or httpx.Client()

    def find_song_id(self, artist: str, album: str, title: str) -> str | None:
        payload = self._get("/rest/search3.view", {"query": title, "songCount": 20, "albumCount": 0, "artistCount": 0})
        songs = payload.get("subsonic-response", {}).get("searchResult3", {}).get("song", []) or []
        artist_norm = normalize_text(artist)
        album_norm = normalize_text(album)
        title_norm = normalize_text(title)
        for song in songs:
            if normalize_text(song.get("artist")) != artist_norm:
                continue
            if album_norm and normalize_text(song.get("album")) != album_norm:
                continue
            if normalize_text(song.get("title")) == title_norm:
                return song.get("id")
        return None

    def is_song_present(self, song_id: str) -> bool:
        payload = self._get("/rest/getSong.view", {"id": song_id})
        response = payload.get("subsonic-response", {})
        return response.get("status") == "ok" and bool(response.get("song"))

    def ping(self) -> dict[str, Any]:
        try:
            payload = self._get("/rest/ping.view", {})
            response = payload.get("subsonic-response", {})
            return {"ok": response.get("status") == "ok", "address": self.address}
        except Exception as exc:
            return {"ok": False, "address": self.address, "error": str(exc)}

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        request_params = dict(params)
        request_params.update(
            {
                "u": self.user,
                "p": self.token_or_password,
                "v": "1.16.1",
                "c": self.client_name,
                "f": "json",
            }
        )
        response = self.session.get(f"{self.address}{path}", params=request_params, timeout=self.timeout)
        if response.status_code != 200:
            raise NavidromeError(f"Navidrome API error {response.status_code}: {response.text}")
        return response.json()
