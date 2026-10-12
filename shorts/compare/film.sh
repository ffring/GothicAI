#!/usr/bin/env bash
# Films a compare scenario twice on the test bench: the animation work off (OpenGothic as it was) and on.
#   shorts/compare/film.sh bodies            -> .shorts/compare/bodies/{off,on}/<segment>.mp4|.wav
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="${1:?usage: film.sh <scenario in shorts/compare>}"
OUT="$ROOT/.shorts/compare/$NAME"
mkdir -p "$OUT"
export TEMPEST_WINDOW_SIZE="${SIZE:-1080x960}" TEMPEST_AUDIO_LOOPBACK=48000 SOUND="${SOUND:-1}" MUSIC="${MUSIC:-0.3}"
export OPENGOTHIC_BENCH_ENCODER="-c:v h264_videotoolbox -b:v 24M" GOTHIC_AI=0 NO_BUILD="${NO_BUILD:-1}"
for take in off on; do
  rm -rf "$OUT/$take"
  if [[ $take == off ]]; then
    env OPENGOTHIC_FOOTIK=0 OPENGOTHIC_GAZE=0 OPENGOTHIC_LEAN=0 OPENGOTHIC_AIM=0 OPENGOTHIC_FLINCH=0 \
        OPENGOTHIC_ANIBLEND=0 OPENGOTHIC_LIPSYNC=0 \
        "$ROOT/scripts/bench.sh" "$ROOT/shorts/compare/$NAME.bench" "$OUT/$take" | head -3
  else
    "$ROOT/scripts/bench.sh" "$ROOT/shorts/compare/$NAME.bench" "$OUT/$take" | head -3
  fi
done
