#!/bin/sh
# Runs inside the Playwright container (see `make demo-video`): record, then convert.
set -eu
pip install -q playwright==1.49.0 2>&1 | grep -v -i "notice\|warning" || true
apt-get update -qq >/dev/null && apt-get install -y -qq ffmpeg >/dev/null
export OUT=/work/out
mkdir -p "$OUT"
find "$OUT" -name '*.webm' -delete
python /work/record.py
ffmpeg -y -loglevel error -i "$OUT/demo.webm" -vf scale=1280:-2 -c:v libx264 -preset slow -crf 27 \
  -pix_fmt yuv420p -movflags +faststart "$OUT/demo.mp4"
ls -la "$OUT"
