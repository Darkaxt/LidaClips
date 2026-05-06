# Oracle Deployment Notes

These notes mirror the existing Oracle-hosted media services: Traefik owns the public route, the UI can sit behind Basic Auth, API traffic is protected by the LidaClips `api_key`, and Uptime Kuma monitors the Traefik route with explicit Host headers.

The registered public hostname for this service is `clips.remaxku.eu`.

## Suggested Layout

```text
/opt/lidaclips/
  compose.yaml
  .env
  config/
  staging/
```

Use the separate clips lane for the completed videos:

```text
/mnt/gdrive-music/Clips -> /lidaclips/clips
```

Keep staging on local disk so partial downloads and failed merges do not appear in the Google Drive mount.

## Compose Example

```yaml
services:
  lidaclips:
    image: ghcr.io/your-user/lidaclips:latest
    container_name: lidaclips
    env_file:
      - .env
    volumes:
      - /opt/lidaclips/config:/lidaclips/config
      - /opt/lidaclips/staging:/lidaclips/staging
      - /mnt/gdrive-music/Clips:/lidaclips/clips
      - /etc/localtime:/etc/localtime:ro
    networks:
      - aiostreams_default
    labels:
      - traefik.enable=true
      - traefik.docker.network=aiostreams_default
      - traefik.http.routers.lidaclips-api.rule=Host(`clips.remaxku.eu`) && (PathPrefix(`/api`) || PathPrefix(`/rest`))
      - traefik.http.routers.lidaclips-api.entrypoints=websecure
      - traefik.http.routers.lidaclips-api.tls=true
      - traefik.http.routers.lidaclips-api.priority=100
      - traefik.http.routers.lidaclips-api.service=lidaclips
      - traefik.http.routers.lidaclips-ui.rule=Host(`clips.remaxku.eu`)
      - traefik.http.routers.lidaclips-ui.entrypoints=websecure
      - traefik.http.routers.lidaclips-ui.tls=true
      - traefik.http.routers.lidaclips-ui.middlewares=lidaclips-auth
      - traefik.http.routers.lidaclips-ui.service=lidaclips
      - traefik.http.middlewares.lidaclips-auth.basicauth.users=${CLIPS_BASIC_AUTH_HASH}
      - traefik.http.services.lidaclips.loadbalancer.server.port=5000
    restart: unless-stopped

networks:
  aiostreams_default:
    external: true
```

The API router intentionally does not use Traefik Basic Auth so clients can call `GET /api/v1/tracks/{lidarr_track_id}/clip`, `GET /api/v1/navidrome/{song_id}/clip`, and `GET /api/v1/stream/{clip_id}` with the LidaClips API key. Keep `api_key` set to a high-entropy value in `.env`.

## Environment

```env
lidarr_address=http://lidarr:8686
lidarr_api_key=change-me
navidrome_address=http://navidrome:4533
navidrome_user=change-me
navidrome_token_or_password=change-me
clip_output_mode=clips_lane
clip_output_path=/lidaclips/clips
staging_path=/lidaclips/staging
minimum_clip_score=75
max_resolution=1080
preferred_container=mp4
sync_schedule=2,20
api_key=change-me-too
CLIPS_BASIC_AUTH_HASH=change-me
```

## Uptime Kuma

Use the same pattern as the other Traefik-backed services in `uptime.remaxku.eu`.

Recommended monitors:

- `Route: LidaClips UI`: HTTPS monitor against Traefik, URL `https://traefik/`, Host header `clips.remaxku.eu`, accepted status `401` if Basic Auth is enabled.
- `Route: LidaClips API Ping`: HTTPS monitor against Traefik, URL `https://traefik/api/v1/ping`, Host header `clips.remaxku.eu`, expected status `200`.
- `Internal: LidaClips Container`: HTTP monitor from the Docker network, URL `http://lidaclips:5000/api/v1/ping`, expected status `200`.

For the stream endpoint, monitor only a known fixture clip if one exists. Do not point Kuma at arbitrary searches or yt-dlp-driven workflows; those depend on YouTube and can create noisy alerts.
