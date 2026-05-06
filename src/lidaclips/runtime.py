import logging
import os
import threading
import time

from flask import render_template
from flask_socketio import SocketIO

from .candidate_search import YtDlpCandidateSearch
from .downloader import ClipDownloader
from .index import ClipIndex
from .lidarr_client import LidarrClient
from .navidrome_client import NavidromeClient
from .scoring import ClipScorer
from .service import LidaClipsService
from .settings import Settings
from .storage import ClipStorage
from .web import create_app


class Runtime:
    def __init__(self, settings: Settings, index: ClipIndex, service: LidaClipsService):
        self.settings = settings
        self.index = index
        self.service = service
        self.logger = logging.getLogger(__name__)
        self.app = create_app(index, api_key=settings.api_key, service=service)
        self.socketio = SocketIO(self.app, async_mode="threading")
        self.targets_status = "idle"
        self.sync_status = "idle"
        self.last_targets = []
        self.last_summary = {}
        self.stop_event = threading.Event()
        self._register_routes()
        self._register_socket_events()
        self._start_scheduler()

    def _register_routes(self) -> None:
        @self.app.get("/")
        def home():
            return render_template("base.html")

    def _register_socket_events(self) -> None:
        @self.socketio.on("connect")
        def connect():
            self._emit_state()

        @self.socketio.on("load_settings")
        def load_settings():
            self.socketio.emit("settings_loaded", self._settings_payload())

        @self.socketio.on("refresh_targets")
        def refresh_targets():
            threading.Thread(target=self.refresh_targets, daemon=True).start()

        @self.socketio.on("start_sync")
        def start_sync():
            threading.Thread(target=self.sync_once, daemon=True).start()

    def refresh_targets(self):
        try:
            self.targets_status = "busy"
            self._emit_state()
            targets = self.service.lidarr_client.collect_pending_tracks(self.index)
            self.last_targets = [
                {
                    "lidarr_track_id": target.lidarr_track_id,
                    "artist": target.artist,
                    "album": target.album,
                    "title": target.title,
                    "duration": target.duration,
                }
                for target in targets
            ]
            self.targets_status = "complete"
        except Exception as exc:
            self.logger.exception("Target refresh failed")
            self.targets_status = "error"
            self.socketio.emit("new_toast_msg", {"title": "Target refresh failed", "message": str(exc)})
        finally:
            self._emit_state()

    def sync_once(self):
        try:
            self.sync_status = "running"
            self._emit_state()
            self.last_summary = self.service.sync_once()
            self.sync_status = "complete"
        except Exception as exc:
            self.logger.exception("Clip sync failed")
            self.sync_status = "error"
            self.socketio.emit("new_toast_msg", {"title": "Clip sync failed", "message": str(exc)})
        finally:
            self._emit_state()

    def _start_scheduler(self):
        thread = threading.Thread(target=self._schedule_checker, name="Schedule_Thread", daemon=True)
        thread.start()

    def _schedule_checker(self):
        while not self.stop_event.is_set():
            current_hour = time.localtime().tm_hour
            if current_hour in self.settings.sync_schedule:
                self.sync_once()
                self.stop_event.wait(3600)
            else:
                self.stop_event.wait(600)

    def _emit_state(self):
        self.socketio.emit(
            "state_update",
            {
                "targets_status": self.targets_status,
                "sync_status": self.sync_status,
                "targets": self.last_targets,
                "summary": self.last_summary,
            },
        )

    def _settings_payload(self):
        return {
            "lidarr_address": self.settings.lidarr_address,
            "navidrome_address": self.settings.navidrome_address,
            "clip_output_mode": self.settings.clip_output_mode,
            "clip_output_path": self.settings.clip_output_path,
            "minimum_clip_score": self.settings.minimum_clip_score,
            "max_resolution": self.settings.max_resolution,
            "preferred_container": self.settings.preferred_container,
            "sync_schedule": self.settings.sync_schedule,
            "sync_artist_allowlist": self.settings.sync_artist_allowlist,
            "max_targets_per_run": self.settings.max_targets_per_run,
            "download_enabled": self.settings.download_enabled,
        }


def build_runtime(config_folder: str = "config") -> Runtime:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    settings = Settings.load(config_folder=config_folder)
    cookies_path = os.path.join(config_folder, "cookies.txt")
    if not os.path.exists(cookies_path):
        cookies_path = None
    index = ClipIndex(os.path.join(config_folder, "lidaclips.db"))
    storage = ClipStorage(settings.clip_output_mode, settings.clip_output_path, settings.staging_path)
    lidarr_client = LidarrClient(settings.lidarr_address, settings.lidarr_api_key, timeout=settings.lidarr_api_timeout)
    navidrome_client = None
    if settings.navidrome_address and settings.navidrome_user and settings.navidrome_token_or_password:
        navidrome_client = NavidromeClient(settings.navidrome_address, settings.navidrome_user, settings.navidrome_token_or_password)
    service = LidaClipsService(
        index=index,
        lidarr_client=lidarr_client,
        candidate_search=YtDlpCandidateSearch(
            limit=settings.search_limit,
            cookies_path=cookies_path,
            ytdlp_binary=settings.ytdlp_binary,
        ),
        scorer=ClipScorer(settings.minimum_clip_score),
        downloader=ClipDownloader(
            storage=storage,
            preferred_container=settings.preferred_container,
            max_resolution=settings.max_resolution,
            cookies_path=cookies_path,
        ),
        navidrome_client=navidrome_client,
        sync_artist_allowlist=settings.sync_artist_allowlist,
        max_targets_per_run=settings.max_targets_per_run,
        download_enabled=settings.download_enabled,
    )
    return Runtime(settings, index, service)
