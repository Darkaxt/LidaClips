# LidaClips

LidaClips is a GPL-3.0 public fork of [TheWicklowWolf/LidaTube](https://github.com/TheWicklowWolf/LidaTube). It builds a local music-video clip index for tracks that already exist in a Lidarr-managed music library.

LidaTube downloads missing songs as audio. LidaClips does the parallel job for video clips: it looks at the songs you already have, finds strict official music-video candidates, downloads the selected clip with `yt-dlp`, and exposes a small API that music clients can use for a video tab.

> Status: early public fork. The backend, API, Docker runtime, UI shell, and tests are in place. Validate with a small artist subset before running it against a full library.

## What It Does

- Reads Lidarr artists, albums, and tracks where `hasFile=true`.
- Creates one clip target per existing track.
- Optionally verifies that the track is also playable through Navidrome/OpenSubsonic.
- Searches YouTube through `yt-dlp`.
- Scores candidates automatically and prefers official music videos.
- Downloads to local staging first, then moves completed files into the configured output.
- Stores durable state in SQLite at `/lidaclips/config/lidaclips.db`.
- Exposes clip lookup and stream endpoints for clients such as Feishin or Aonsoku forks.

## What It Does Not Do

- It does not fill missing Lidarr audio files.
- It does not modify Navidrome.
- It does not require a manual review queue by default.
- It does not accept low-confidence matches just because a video is popular.

## Storage Model

The default output mode is a separate clips lane:

```text
/lidaclips/staging  -> local temporary download and merge area
/lidaclips/clips    -> completed video clips
/lidaclips/config   -> settings, cookies, and SQLite database
```

Sidecar output beside audio files is supported by `clip_output_mode=sidecar`, but the recommended default is `clip_output_mode=clips_lane`.

For a Navidrome library backed by a mounted music drive, a typical mapping is:

```yaml
volumes:
  - /opt/lidaclips/config:/lidaclips/config
  - /opt/lidaclips/staging:/lidaclips/staging
  - /mnt/gdrive-music/Clips:/lidaclips/clips
```

Keep staging on local disk so partial downloads and failed merges do not appear in the final clips folder.

## Quick Start

```powershell
git clone https://github.com/Darkaxt/LidaClips.git
cd LidaClips
Copy-Item .env.example .env
```

Edit `.env`, then start the service:

```powershell
docker compose up -d --build
```

The included [compose.yaml](compose.yaml) builds the image locally and exposes the app on `http://localhost:5000`.

Minimum useful `.env` values:

```env
lidarr_address=http://lidarr:8686
lidarr_api_key=change-me
clip_output_mode=clips_lane
clip_output_path=/lidaclips/clips
staging_path=/lidaclips/staging
minimum_clip_score=75
max_resolution=1080
preferred_container=mp4
sync_artist_allowlist=
max_targets_per_run=25
download_enabled=false
api_key=change-me-too
```

Optional Navidrome verification:

```env
navidrome_address=http://navidrome:4533
navidrome_user=navidrome-user
navidrome_token_or_password=navidrome-password-or-token
```

Place a YouTube `cookies.txt` file in `/lidaclips/config` if your setup needs cookies for `yt-dlp`.

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
| `sync_schedule` | empty | Comma-separated hours, for example `2,20`. |
| `sync_artist_allowlist` | empty | Comma-separated artist names to limit a dry run or first rollout. |
| `max_targets_per_run` | `25` | Maximum pending tracks to process in one sync run. |
| `download_enabled` | `false` | When false, accepted candidates are recorded without downloading clips. |
| `clip_output_mode` | `clips_lane` | `clips_lane` or `sidecar`. |
| `clip_output_path` | `/lidaclips/clips` | Root folder for `clips_lane` output. |
| `staging_path` | `/lidaclips/staging` | Local staging folder for partial downloads. |
| `minimum_clip_score` | `75` | Minimum automated match score. |
| `max_resolution` | `1080` | Maximum video height passed to `yt-dlp`. |
| `preferred_container` | `mp4` | Final merged container. |
| `search_limit` | `10` | Number of YouTube candidates to inspect per track. |
| `api_key` | empty | Optional API key for clip lookup and stream endpoints. |
| `ytdlp_binary` | empty | Optional local `yt-dlp` binary path for non-container runs. |

For local Windows development with Scoop, `ytdlp_binary` may point at the Scoop app directory:

```powershell
$env:ytdlp_binary='C:\Users\darka\scoop\apps\yt-dlp\current'
```

LidaClips resolves that directory to `yt-dlp.exe`.

## API

`GET /api/v1/ping` is public. If `api_key` is configured, all lookup and stream endpoints require `X-Api-Key`, `apiKey`, or `api_key`.

Custom clip API:

- `GET /api/v1/ping`
- `GET /api/v1/health`
- `GET /api/v1/clips?artist=&album=&track=`
- `GET /api/v1/tracks/{lidarr_track_id}/clip`
- `GET /api/v1/navidrome/{song_id}/clip`
- `GET /api/v1/stream/{clip_id}`

OpenSubsonic-style video compatibility endpoints:

- `GET /rest/getVideos.view?f=json`
- `GET /rest/getVideoInfo.view?id={clip_id}&f=json`
- `GET /rest/stream.view?id={clip_id}`

`/api/v1/health` checks the SQLite DB, staging path, clips path, Lidarr, and optional Navidrome. It is API-key protected. The OpenSubsonic-style endpoints return familiar video-shaped responses, but authentication is still the LidaClips API key rather than full Subsonic token auth.

## Matching Policy

The default scorer is deliberately strict. It rejects topic/audio uploads, lyric videos, visualizers, covers, live versions, remixes, shorts, interviews, and low-confidence matches.

Candidate scoring uses:

- title and artist similarity
- expected duration tolerance
- official music-video wording
- verified-channel metadata when available
- channel follower or subscriber count when available
- view count
- artist-channel and VEVO-style channel signals
- negative keywords and source penalties

If no candidate passes the configured threshold, the target is recorded as `no_match` and can be retried by later scheduled runs.

## Oracle Deployment Notes

Personal Oracle/Traefik deployment notes for `clips.remaxku.eu` are in [docs/oracle-deployment.md](docs/oracle-deployment.md). They cover the intended Traefik UI/API split and Uptime Kuma monitor pattern.

## Development

Run tests from the repository root:

```powershell
$env:PYTHONPATH='src'
python -m unittest discover -s tests -v
```

Build and smoke-test the container:

```powershell
docker build -t lidaclips:verification .
docker run -d --rm --name lidaclips-verification -p 127.0.0.1:15000:5000 -e api_key=verify lidaclips:verification
```

Then check:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:15000/api/v1/ping
docker stop lidaclips-verification
```

## License And Attribution

LidaClips is licensed under GPL-3.0.

This project is derived from [TheWicklowWolf/LidaTube](https://github.com/TheWicklowWolf/LidaTube), which is also GPL-3.0 licensed. The fork keeps upstream attribution while changing the purpose from missing-song audio downloads to official music-video clips for songs already present in the library.
