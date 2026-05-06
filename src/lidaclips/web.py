import os
from functools import wraps
from typing import Any, Callable

from flask import Flask, Response, jsonify, request, send_file

from .index import ClipIndex


def create_app(index: ClipIndex, api_key: str = "", service: Any | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config["LIDACLIPS_API_KEY"] = api_key or ""
    app.config["LIDACLIPS_INDEX"] = index
    app.config["LIDACLIPS_SERVICE"] = service

    def authorized() -> bool:
        configured_key = app.config["LIDACLIPS_API_KEY"]
        if not configured_key:
            return True
        supplied = request.headers.get("X-Api-Key") or request.args.get("apiKey") or request.args.get("api_key")
        return supplied == configured_key

    def require_api_key(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not authorized():
                return jsonify({"error": "unauthorized"}), 401
            return func(*args, **kwargs)

        return wrapper

    @app.get("/api/v1/ping")
    def ping():
        return jsonify({"status": "ok", "service": "LidaClips"})

    @app.get("/api/v1/health")
    @require_api_key
    def health():
        service = app.config.get("LIDACLIPS_SERVICE")
        if service is None or not hasattr(service, "health_check"):
            return jsonify({"status": "degraded", "checks": {"service": {"ok": False, "error": "service unavailable"}}}), 503
        payload = service.health_check()
        return jsonify(payload), 200 if payload.get("status") == "ok" else 503

    @app.get("/api/v1/clips")
    @require_api_key
    def clips():
        rows = index.search_clips(
            artist=request.args.get("artist"),
            album=request.args.get("album"),
            track=request.args.get("track") or request.args.get("title"),
        )
        return jsonify({"clips": [_public_clip(row) for row in rows]})

    @app.get("/api/v1/tracks/<int:lidarr_track_id>/clip")
    @require_api_key
    def clip_by_track(lidarr_track_id: int):
        row = index.get_clip_by_track(lidarr_track_id)
        if row is None:
            return jsonify({"error": "clip_not_found"}), 404
        return jsonify({"clip": _public_clip(row)})

    @app.get("/api/v1/navidrome/<path:song_id>/clip")
    @require_api_key
    def clip_by_navidrome(song_id: str):
        row = index.get_clip_by_navidrome_song_id(song_id)
        if row is None:
            return jsonify({"error": "clip_not_found"}), 404
        return jsonify({"clip": _public_clip(row)})

    @app.get("/api/v1/stream/<int:clip_id>")
    @require_api_key
    def stream_clip(clip_id: int):
        row = index.get_clip_by_id(clip_id)
        return _send_clip(row)

    @app.get("/rest/getVideos.view")
    @app.get("/rest/getVideos")
    @require_api_key
    def rest_get_videos():
        videos = [_subsonic_video(row) for row in index.all_clips()]
        return jsonify({"subsonic-response": {"status": "ok", "version": "1.16.1", "type": "lidaclips", "videos": {"video": videos}}})

    @app.get("/rest/getVideoInfo.view")
    @app.get("/rest/getVideoInfo")
    @require_api_key
    def rest_get_video_info():
        clip_id = request.args.get("id", type=int)
        if clip_id is None:
            return jsonify({"subsonic-response": {"status": "failed", "error": {"code": 10, "message": "id is required"}}}), 400
        row = index.get_clip_by_id(clip_id)
        if row is None:
            return jsonify({"subsonic-response": {"status": "failed", "error": {"code": 70, "message": "video not found"}}}), 404
        return jsonify({"subsonic-response": {"status": "ok", "version": "1.16.1", "type": "lidaclips", "videoInfo": _subsonic_video(row)}})

    @app.get("/rest/stream.view")
    @app.get("/rest/stream")
    @require_api_key
    def rest_stream():
        clip_id = request.args.get("id", type=int)
        if clip_id is None:
            return Response("id is required", status=400)
        return _send_clip(index.get_clip_by_id(clip_id))

    def _send_clip(row: dict[str, Any] | None):
        if row is None:
            return jsonify({"error": "clip_not_found"}), 404
        file_path = row["file_path"]
        if not os.path.exists(file_path):
            return jsonify({"error": "clip_file_missing"}), 404
        return send_file(file_path, mimetype=row.get("mime_type") or "video/mp4", conditional=True)

    return app


def _public_clip(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "lidarr_track_id": row["lidarr_track_id"],
        "navidrome_song_id": row.get("navidrome_song_id"),
        "video_id": row["video_id"],
        "source_url": row["source_url"],
        "title": row["title"],
        "artist": row["artist"],
        "album": row["album"],
        "track": row["track_title"],
        "duration": row.get("duration"),
        "mime_type": row["mime_type"],
        "score": row["score"],
        "stream_url": row["stream_url"],
        "file_name": os.path.basename(row["file_path"]),
        "evidence": row["evidence"],
    }


def _subsonic_video(row: dict[str, Any]) -> dict[str, Any]:
    size = os.path.getsize(row["file_path"]) if os.path.exists(row["file_path"]) else 0
    return {
        "id": str(row["id"]),
        "title": row["track_title"],
        "album": row["album"],
        "artist": row["artist"],
        "contentType": row.get("mime_type") or "video/mp4",
        "suffix": os.path.splitext(row["file_path"])[1].lstrip(".") or "mp4",
        "duration": row.get("duration") or 0,
        "size": size,
        "path": row["file_path"],
        "coverArt": str(row["id"]),
        "created": row.get("created_at"),
    }
