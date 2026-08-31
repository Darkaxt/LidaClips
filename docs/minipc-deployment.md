# MiniPC Deployment Notes

LidaClips runs in the MiniPC `media-apps` LXC, not Oracle. The registered public
hostname is `clips.remaxku.eu`.

## Current Topology

- MiniPC media LXC: `100.110.134.13` (Proxmox CT120)
- deployment root: `/opt/lidaclips`
- application port: `127.0.0.1:5000`
- public ingress: MiniPC Caddy under `/opt/home-ingress`
- public UI: Caddy Basic Auth
- public API prefixes: `/api*` and `/rest*`, authenticated by the LidaClips API
  key rather than Caddy Basic Auth

The old Oracle layout, `aiostreams_default` network, Traefik labels,
`/mnt/gdrive-music`, and Oracle AIOStreams proxy are retired for this service.
Do not recreate them from historical documentation.

## Deployment Guardrails

1. Read the live `/opt/lidaclips/compose.yaml` before changing image, mounts,
   environment, or egress behavior. This repository does not store live
   secrets or claim that historical volume mappings remain current.
2. Build and test the intended source commit, then publish or stage an immutable
   image for that commit.
3. Update only the LidaClips compose services. Do not restart Docker, Caddy, or
   unrelated media containers.
4. Verify the container is healthy, `GET /api/v1/ping` returns `200`, the public
   UI has the expected Basic Auth response, and a known API request succeeds.
5. Preserve `/opt/lidaclips/config`, staging state, cookie jars, API keys, and
   existing clip files across recreation.

The live compose currently sets `GUNICORN_CMD_ARGS=--no-control-socket`; keep
that setting unless a newer Gunicorn deployment is proven not to hang before
worker boot.

## Ingress Policy

Caddy routes `clips.remaxku.eu` to `127.0.0.1:5000`. The UI remains behind
Basic Auth while client API traffic uses the application API key. Do not widen
the public path exceptions beyond the API routes required by supported clients.

## Uptime Kuma

Maintain these checks:

- public UI route, expecting the configured Basic Auth response
- public API ping, expecting `200`
- `Internal: LidaClips`, probing the MiniPC service path

Do not use searches or YouTube downloads as heartbeat checks; they depend on
external providers and create noisy failures.
