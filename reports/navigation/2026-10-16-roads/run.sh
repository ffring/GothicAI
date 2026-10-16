#!/usr/bin/env bash
# Run on Ryzen from the task checkout; each invocation owns one short lock batch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ACTION="${1:-build}"
TASK="${TASK:-$HOME/gothic-ai/nav-roads}"
BUILD="${BUILD:-$TASK/build}"
OUT="${OUT:-$TASK/runs/$ACTION}"
SAVE="${SAVE:-$TASK/save_slot_1.sav}"
PLACES="${PLACES:-$TASK/places.json}"
GOTHIC_DIR="${GOTHIC_DIR:-$HOME/gothic-ai/game}"
for path in "$TASK" "$BUILD" "$OUT" "$SAVE" "$PLACES" "$GOTHIC_DIR"; do
  [[ "$path" == /* ]] || { echo "Absolute path required: $path" >&2; exit 2; }
done
if [[ "$(uname -s)" == Linux ]]; then
  exec 9>"$HOME/gothic-ai/body-simulation.lock"
  flock -w "${RYZEN_LOCK_WAIT:-45}" 9 || { echo 'Ryzen занят' >&2; exit 75; }
  export VULKAN_SDK="$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0"
  export PATH="$VULKAN_SDK/bin:$PATH"
else
  [[ "$ACTION" == build ]] || { echo 'Mac: compile only' >&2; exit 2; }
fi
if [[ "$ACTION" == build ]]; then
  mkdir -p "$BUILD"
  FLAGS=(-DCMAKE_BUILD_TYPE=RelWithDebInfo)
  if [[ "$(uname -s)" == Linux ]]; then
    FLAGS+=(-DCMAKE_POLICY_VERSION_MINIMUM=3.5 "-DCMAKE_CXX_FLAGS=-I$VULKAN_SDK/include" "-DCMAKE_EXE_LINKER_FLAGS=-L$VULKAN_SDK/lib")
  fi
  cmake -S "$ROOT/OpenGothic" -B "$BUILD" -G Ninja "${FLAGS[@]}" >"$BUILD/configure.log" 2>&1 || { tail -40 "$BUILD/configure.log"; exit 1; }
  TARGETS=(Gothic2Notr)
  if [[ "$(uname -s)" == Linux ]]; then
    TARGETS+=(gothic-body-runner)
    [[ "${CONTRACT:-1}" == 0 ]] || TARGETS+=(check-navigation-roads)
    timeout --signal=INT "${BUILD_BATCH_SECONDS:-90}" nice -n 10 cmake --build "$BUILD" --target "${TARGETS[@]}" --parallel "${JOBS:-10}" >"$BUILD/build.log" 2>&1 || {
      code=$?; tail -30 "$BUILD/build.log"; exit "$code";
    }
  else
    cmake --build "$BUILD" --target "${TARGETS[@]}" --parallel "${JOBS:-2}" >"$BUILD/build.log" 2>&1 || { tail -50 "$BUILD/build.log"; exit 1; }
  fi
  tail -10 "$BUILD/build.log"
  exit
fi
[[ "$ACTION" == contract ]] && exec cmake --build "$BUILD" --target check-navigation-roads --parallel 2
[[ "$ACTION" == run && $# == 2 ]] || { echo 'Usage: run.sh build|contract|run scenario.bench' >&2; exit 2; }
[[ ! -e "$OUT" && "$2" == /* ]] || { echo 'Need fresh OUT and absolute scenario path' >&2; exit 2; }
mkdir -p "$OUT"
awk -v places="$PLACES" '{if($0=="roads load @PLACES_JSON@") print "roads load " places; else print}' "$2" >"$OUT/scenario.bench"
cd "$OUT"
env -u DISPLAY -u WAYLAND_DISPLAY GOTHIC_AI=0 OPENGOTHIC_TEXTURES= OPENGOTHIC_BODY_TRACE=0 \
  VK_DRIVER_FILES=/nonexistent VK_ICD_FILENAMES=/nonexistent \
  nice -n 10 "$BUILD/opengothic/gothic-body-runner" -g "$GOTHIC_DIR" -g2 -devmode \
  --save "$SAVE" --scenario "$OUT/scenario.bench" --output "$OUT" --mode lockstep \
  >"$OUT/stdout.log" 2>"$OUT/stderr.log"
[[ -s "$OUT/roads-events.jsonl" ]] || { echo 'No roads commands executed' >&2; exit 1; }
if grep -nE 'marvin failed: roads' "$OUT/bench.log"; then exit 1; fi
if [[ -f "$OUT/events.jsonl" ]] && grep -n '"roads_error"' "$OUT/events.jsonl"; then exit 1; fi
echo "Complete: $OUT"
