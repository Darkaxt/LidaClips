#!/bin/sh

echo -e "\033[1;32mLidaClips\033[0m"
echo "Initializing app..."

cat << 'EOF'
_____________________________________

 LidaClips
 Official music-video clip index
 for existing Lidarr/Navidrome songs
_____________________________________

Forked from TheWicklowWolf/LidaTube.
Licensed under GPL-3.0.

EOF

echo "-----------------"
echo -e "\033[1mInstalled Versions\033[0m"
echo -n "yt-dlp: "
pip show yt-dlp | grep Version: | awk '{print $2}'
echo -n "FFmpeg: "
ffmpeg -version | head -n 1 | awk '{print $3}'
echo "-----------------"

PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo "-----------------"
echo -e "\033[1mRunning with:\033[0m"
echo "PUID=${PUID}"
echo "PGID=${PGID}"
echo "-----------------"

echo "Setting up directories.."
mkdir -p /lidaclips/clips /lidaclips/config /lidaclips/cache /lidaclips/staging
chown ${PUID}:${PGID} /lidaclips
chown -R ${PUID}:${PGID} /lidaclips/config /lidaclips/cache /lidaclips/staging

export XDG_CACHE_HOME=/lidaclips/cache

echo "Running LidaClips..."
exec su-exec ${PUID}:${PGID} gunicorn src.LidaClips:app -c gunicorn_config.py
