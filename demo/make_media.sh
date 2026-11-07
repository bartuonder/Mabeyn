#!/bin/sh
# Runs inside the Playwright container (see `make demo-media`): record, take screenshots, convert.
set -eu
pip install -q playwright==1.49.0 >/dev/null 2>&1 || pip install playwright==1.49.0
apt-get update -qq >/dev/null && apt-get install -y -qq ffmpeg >/dev/null
export OUT=/work/out
mkdir -p "$OUT"
if [ -z "${SKIP_RECORD:-}" ]; then
  find "$OUT" -name '*.webm' -delete
  python /work/record.py
fi
python /work/screenshots.py
ffmpeg -y -loglevel error -i "$OUT/demo.webm" -c:v libx264 -preset slow -crf 24 -pix_fmt yuv420p \
  -movflags +faststart "$OUT/demo.mp4"

# The README clip is chapter 4 (the refund and the human decision), a little faster than real time.
eval "$(python - <<'PY'
import json
marks = {m["chapter"]: m["start"] for m in json.load(open("/work/out/chapters.json"))}
print(f"START={marks[4] + 3.0} LENGTH={marks[5] - marks[4] - 3.0}")
PY
)"
ffmpeg -y -loglevel error -ss "$START" -t "$LENGTH" -i "$OUT/demo.mp4" \
  -vf "setpts=PTS/1.5,fps=10,scale=1000:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
  -loop 0 "$OUT/demo.gif"
ls -la "$OUT" "$OUT/screenshots"
