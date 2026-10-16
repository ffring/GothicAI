#!/usr/bin/env bash
# Runs a test-bench scenario inside the game: video with a debug overlay, per-frame metrics, a report.
# See scripts/bench/README.md.
#
#   scripts/bench.sh <scenario file | name from scripts/bench/> [out dir]
#
# env: GOTHIC_DIR    game data (default: /Volumes/DevSSD/Dev/games/Gothic II)
#      SAVE          save to start from (default: save_slot_1.sav in the main checkout)
#      NO_BUILD=1    do not rebuild the engine first
#      TIMEOUT=300   seconds before the game is killed
#      KEEP=1        the game stays open after the scenario
#      SOUND, MUSIC  volumes 0..1 (default 0); with TEMPEST_AUDIO_LOOPBACK=48000 the sound of a segment
#                    goes into <segment>.wav instead of the speakers
#      MOVABLE_VOBS  optional initial GAME movable_vobs setting (0 or 1), before world loading
#      ARROW_STICKING optional initial GAME arrow_sticking setting (0 or 1), before world loading
#      PHYSICS_NOISE optional initial GAME physics_noise setting (0 or 1), before world loading
#      CHARACTER_VIRTUAL optional initial GAME character_virtual setting (0 or 1), before world loading
#      OPENGOTHIC_TEXTURES  sharper textures (default: those of scripts/hd-textures.sh, when made; empty: none)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GOTHIC_DIR="${GOTHIC_DIR:-/Volumes/DevSSD/Dev/games/Gothic II}"
BIN="$ROOT/OpenGothic/build/opengothic/Gothic2Notr"

SCEN="${1:?usage: scripts/bench.sh <scenario> [out dir]}"
[[ -f "$SCEN" ]] || SCEN="$ROOT/scripts/bench/$SCEN.bench"
[[ -f "$SCEN" ]] || { echo "no scenario: $1" >&2; exit 2; }
SCEN="$(cd "$(dirname "$SCEN")" && pwd)/$(basename "$SCEN")"
NAME="$(basename "$SCEN" .bench)"
OUT="${2:-$ROOT/.bench/$NAME-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

if [[ -z "${SAVE:-}" ]]; then
  # worktrees share the save of the main checkout
  COMMON="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)"
  SAVE="$(dirname "$COMMON")/save_slot_1.sav"
fi
[[ -f "$SAVE" ]] || { echo "no save: $SAVE (set SAVE=...)" >&2; exit 2; }

if [[ "${NO_BUILD:-}" != 1 ]]; then
  cmake --build "$ROOT/OpenGothic/build" --target Gothic2Notr >"$OUT/build.log" 2>&1 \
    || { tail -30 "$OUT/build.log"; exit 1; }
fi

# the game reads Gothic.ini and saves from its working directory
RUN="$OUT/run"
mkdir -p "$RUN"
cp "$SAVE" "$RUN/save_slot_1.sav"
cat >"$RUN/Gothic.ini" <<EOF
[INTERNAL]
vidResIndex=2
[SOUND]
soundVolume=${SOUND:-0}
musicVolume=${MUSIC:-0}
EOF
if [[ -n "${MOVABLE_VOBS:-}${ARROW_STICKING:-}${PHYSICS_NOISE:-}${CHARACTER_VIRTUAL:-}" ]]; then
  printf '\n[GAME]\n' >>"$RUN/Gothic.ini"
  if [[ -n "${MOVABLE_VOBS:-}" ]]; then
    [[ "$MOVABLE_VOBS" == 0 || "$MOVABLE_VOBS" == 1 ]] || { echo "MOVABLE_VOBS must be 0 or 1" >&2; exit 2; }
    printf 'movable_vobs=%s\n' "$MOVABLE_VOBS" >>"$RUN/Gothic.ini"
  fi
  if [[ -n "${ARROW_STICKING:-}" ]]; then
    [[ "$ARROW_STICKING" == 0 || "$ARROW_STICKING" == 1 ]] || { echo "ARROW_STICKING must be 0 or 1" >&2; exit 2; }
    printf 'arrow_sticking=%s\n' "$ARROW_STICKING" >>"$RUN/Gothic.ini"
  fi
  if [[ -n "${CHARACTER_VIRTUAL:-}" ]]; then
    [[ "$CHARACTER_VIRTUAL" == 0 || "$CHARACTER_VIRTUAL" == 1 ]] || { echo "CHARACTER_VIRTUAL must be 0 or 1" >&2; exit 2; }
    printf 'character_virtual=%s\n' "$CHARACTER_VIRTUAL" >>"$RUN/Gothic.ini"
  fi
  if [[ -n "${PHYSICS_NOISE:-}" ]]; then
    [[ "$PHYSICS_NOISE" == 0 || "$PHYSICS_NOISE" == 1 ]] || { echo "PHYSICS_NOISE must be 0 or 1" >&2; exit 2; }
    printf 'physics_noise=%s\n' "$PHYSICS_NOISE" >>"$RUN/Gothic.ini"
  fi
fi

# the sharper textures the launcher gives the game too (scripts/hd-textures.sh)
HD_TEXTURES="$HOME/.local/share/gothic-ai/hd-textures/textures"
if [[ -z "${OPENGOTHIC_TEXTURES+set}" && -d "$HD_TEXTURES" ]]; then
  export OPENGOTHIC_TEXTURES="$HD_TEXTURES"
fi

START=$(date +%s)
(cd "$RUN" && exec env TEMPEST_BACKGROUND=1 OPENGOTHIC_BENCH="$SCEN" OPENGOTHIC_BENCH_OUT="$OUT" ${KEEP:+OPENGOTHIC_BENCH_KEEP=1} \
  "$BIN" -g "$GOTHIC_DIR" -window -devmode -save 1) >"$OUT/game.log" 2>&1 &
PID=$!
for ((i = 0; i < ${TIMEOUT:-300} * 4; i++)); do
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.25
done
if kill -0 "$PID" 2>/dev/null; then
  echo "bench: timeout, game killed" >&2
  kill "$PID"
fi
wait "$PID" || true
echo "bench: game ran $(( $(date +%s) - START )) s, output in $OUT"
grep -E "^\[bench\] (scenario|can not|unknown|marvin failed|goto failed|walkto: no|foreach|waitarrive)" "$OUT/game.log" || true

python3 "$ROOT/scripts/bench_report.py" "$OUT"
