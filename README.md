# LidaClips

LidaClips is a public GPL-3.0 fork of [TheWicklowWolf/LidaTube](https://github.com/TheWicklowWolf/LidaTube) that builds a music-video clip index for songs already present in a Lidarr-managed music library.

Instead of downloading missing audio tracks, it:

- reads existing tracks from Lidarr where `hasFile=true`
- optionally verifies matching songs through Navidrome/OpenSubsonic
- searches YouTube for strict official music-video candidates
- downloads clips with `yt-dlp`
- writes completed videos to a separate clips lane by default
- exposes a small clip lookup and streaming API for clients such as Feishin or Aonsoku forks

## Run With Docker Compose

```yaml
services:
  lidaclips:
    image: your-dockerhub-user/lidaclips:latest
    container_name: lidaclips
    environment:
      - PUID=1000
      - PGID=1000
      - lidarr_address=http://lidarr:8686
      - lidarr_api_key=change-me
      - navidrome_address=http://navidrome:4533
      - navidrome_user=navidrome-user
      - navidrome_token_or_password=navidrome-password
      - clip_output_mode=clips_lane
      - clip_output_path=/lidaclips/clips
      - api_key=change-me-too
    volumes:
      - /path/to/config:/lidaclips/config
      - /path/to/clips:/lidaclips/clips
      - /path/to/staging:/lidaclips/staging
      - /etc/localtime:/etc/localtime:ro
    ports:
      - 5000:5000
    restart: unless-stopped
```

For your Navidrome stack, a typical host mapping would be:

```yaml
      - /mnt/gdrive-music/Clips:/lidaclips/clips
      - /opt/lidaclips/staging:/lidaclips/staging
```

Oracle/Traefik deployment notes for `clips.remaxku.eu` are in [docs/oracle-deployment.md](docs/oracle-deployment.md).

## Configuration

Environment variables override `config/settings_config.json`.

| Variable | Default | Purpose |
|---|---:|---|
| `PUID` / `PGID` | `1000` | Container runtime user and group. |
| `lidarr_address` | `http://192.168.1.2:8686` | Lidarr base URL. |
| `lidarr_api_key` | empty | Lidarr API key. |
| `lidarr_api_timeout` | `120` | Lidarr request timeout in seconds. |
| `navidrome_address` | empty | Optional Navidrome/OpenSubsonic base URL. |
| `navidrome_user` | empty | Optional OpenSubsonic user. |
| `navidrome_token_or_password` | empty | Optional OpenSubsonic password/token value. |
| `thread_limit` | `1` | Reserved worker limit. |
| `sleep_interval` | `0` | Reserved per-item pause. |
| `sync_schedule` | empty | Comma-separated hours, e.g. `2,20`. |
| `clip_output_mode` | `clips_lane` | `clips_lane` or `sidecar`. |
| `clip_output_path` | `/lidaclips/clips` | Root folder for `clips_lane` output. |
| `staging_path` | `/lidaclips/staging` | Local staging folder for partial downloads. |
| `minimum_clip_score` | `75` | Minimum automated match score. |
| `max_resolution` | `1080` | Maximum video height passed to `yt-dlp`. |
| `preferred_container` | `mp4` | Final merged container. |
| `search_limit` | `10` | Number of YouTube candidates to inspect per track. |
| `api_key` | empty | Optional API key for clip lookup and stream endpoints. |
| `ytdlp_binary` | empty | Optional local `yt-dlp` binary path for non-container runs. |

Place a YouTube `cookies.txt` file in `/lidaclips/config` if your setup needs cookies for `yt-dlp`.

## API

If `api_key` is configured, pass it as `X-Api-Key` or `apiKey`.

- `GET /api/v1/ping`
- `GET /api/v1/clips?artist=&album=&track=`
- `GET /api/v1/tracks/{lidarr_track_id}/clip`
- `GET /api/v1/navidrome/{song_id}/clip`
- `GET /api/v1/stream/{clip_id}`

OpenSubsonic-style video compatibility endpoints:

- `GET /rest/getVideos.view?f=json`
- `GET /rest/getVideoInfo.view?id={clip_id}&f=json`
- `GET /rest/stream.view?id={clip_id}`

## Matching Policy

The default scorer is deliberately strict. It rejects topic/audio uploads, lyric videos, visualizers, covers, live versions, remixes, shorts, interviews, and low-confidence matches. Candidate scoring uses title/artist similarity, expected duration, official-video wording, verified channel metadata, channel follower count, view count, and artist/VEVO channel signals.

Tracks with no accepted candidate are recorded as `no_match` and can be retried by later scheduled runs.

## Development

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -v
```

For a local Windows `yt-dlp` binary installed by Scoop:

```powershell
$env:ytdlp_binary='C:\Users\darka\scoop\apps\yt-dlp\current'
```

## License And Attribution

LidaClips is licensed under GPL-3.0. It is derived from [TheWicklowWolf/LidaTube](https://github.com/TheWicklowWolf/LidaTube), which is also GPL-3.0 licensed.
