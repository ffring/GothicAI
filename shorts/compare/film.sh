#!/usr/bin/env bash
# Films a compare scenario twice on the test bench: a part of our work off (OpenGothic as it was; by default the
# animation work, or compare/<name>.json "off_env") and on.
#   shorts/compare/film.sh bodies            -> .shorts/compare/bodies/{off,on}/<segment>.mp4|.wav
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="${1:?usage: film.sh <scenario in shorts/compare>}"
OUT="$ROOT/.shorts/compare/$NAME"
mkdir -p "$OUT"
export TEMPEST_WINDOW_SIZE="${SIZE:-1080x960}" TEMPEST_AUDIO_LOOPBACK=48000 SOUND="${SOUND:-1}" MUSIC="${MUSIC:-0.3}"
export OPENGOTHIC_BENCH_ENCODER="-c:v h264_videotoolbox -b:v 24M" GOTHIC_AI=0 NO_BUILD="${NO_BUILD:-1}"
export BENCH_HOST=local
# what the "off" take switches off: "off_env" of compare/<name>.json, else the animation work
OFF_ENV="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("off_env",""))' \
           "$ROOT/shorts/compare/$NAME.json" 2>/dev/null || true)"
[[ -n "$OFF_ENV" ]] || OFF_ENV="OPENGOTHIC_FOOTIK=0 OPENGOTHIC_GAZE=0 OPENGOTHIC_LEAN=0 OPENGOTHIC_AIM=0 OPENGOTHIC_FLINCH=0
  OPENGOTHIC_ANIBLEND=0 OPENGOTHIC_LIPSYNC=0"
for take in off on; do
  rm -rf "$OUT/$take"
  # the scenario's `off:` / `on:` lines run in that take only
  if [[ $take == off ]]; then
    # shellcheck disable=SC2086
    env OPENGOTHIC_BENCH_TAKE=off $OFF_ENV "$ROOT/scripts/bench.sh" "$ROOT/shorts/compare/$NAME.bench" "$OUT/$take" | head -3
  else
    env OPENGOTHIC_BENCH_TAKE=on "$ROOT/scripts/bench.sh" "$ROOT/shorts/compare/$NAME.bench" "$OUT/$take" | head -3
  fi
done
