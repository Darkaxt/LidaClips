import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass
class Settings:
    lidarr_address: str = "http://192.168.1.2:8686"
    lidarr_api_key: str = ""
    lidarr_api_timeout: float = 120.0
    navidrome_address: str = ""
    navidrome_user: str = ""
    navidrome_token_or_password: str = ""
    thread_limit: int = 1
    sleep_interval: float = 0.0
    sync_schedule: list[int] = None
    clip_output_mode: str = "clips_lane"
    clip_output_path: str = "/lidaclips/clips"
    staging_path: str = "/lidaclips/staging"
    minimum_clip_score: float = 75.0
    max_resolution: int = 1080
    preferred_container: str = "mp4"
    api_key: str = ""
    search_limit: int = 10
    ytdlp_binary: str = ""

    def __post_init__(self):
        if self.sync_schedule is None:
            self.sync_schedule = []

    @classmethod
    def load(cls, config_folder: str = "config", environ: Mapping[str, str] | None = None) -> "Settings":
        environ = dict(os.environ if environ is None else environ)
        os.makedirs(config_folder, exist_ok=True)
        config_path = os.path.join(config_folder, "settings_config.json")
        data = asdict(cls())

        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            for key, value in loaded.items():
                if key in data:
                    data[key] = value

        for key in list(data):
            if key in environ and environ[key] != "":
                data[key] = cls._coerce_value(key, environ[key])
            upper_key = key.upper()
            if upper_key in environ and environ[upper_key] != "":
                data[key] = cls._coerce_value(key, environ[upper_key])

        data["sync_schedule"] = cls.parse_sync_schedule(data.get("sync_schedule", []))
        data["thread_limit"] = int(data["thread_limit"])
        data["search_limit"] = int(data["search_limit"])
        data["max_resolution"] = int(data["max_resolution"])
        data["sleep_interval"] = float(data["sleep_interval"])
        data["lidarr_api_timeout"] = float(data["lidarr_api_timeout"])
        data["minimum_clip_score"] = float(data["minimum_clip_score"])
        settings = cls(**data)
        settings.save(config_path)
        return settings

    def save(self, config_path: str) -> None:
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle, indent=4)

    @staticmethod
    def parse_sync_schedule(value) -> list[int]:
        if value in ("", None):
            return []
        if isinstance(value, list):
            raw_values = value
        else:
            raw_values = str(value).split(",")
        parsed: list[int] = []
        for raw in raw_values:
            if raw in ("", None):
                continue
            match = re.search(r"\d+", str(raw))
            if not match:
                continue
            hour = int(match.group(0))
            parsed.append(0 if hour < 0 or hour > 23 else hour)
        return sorted(set(parsed))

    @staticmethod
    def _coerce_value(key: str, value: str):
        if key == "sync_schedule":
            return Settings.parse_sync_schedule(value)
        if key in {"thread_limit", "max_resolution", "search_limit"}:
            return int(value)
        if key in {"sleep_interval", "lidarr_api_timeout", "minimum_clip_score"}:
            return float(value)
        return value
