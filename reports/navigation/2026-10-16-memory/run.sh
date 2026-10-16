#!/usr/bin/env bash
# One short lock acquisition per action. All outputs belong to nav-memory.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ACTION="${1:-fence}"
TASK="${TASK:-$HOME/gothic-ai/nav-memory}"
BUILD="${BUILD:-$TASK/build}"
OUT="${OUT:-$TASK/runs/$ACTION}"
SAVE="${SAVE:-$TASK/save_slot_1.sav}"
GOTHIC_DIR="${GOTHIC_DIR:-$HOME/gothic-ai/game}"
RUNNER="${RUNNER:-$BUILD/opengothic/gothic-body-runner}"
for path in "$BUILD" "$OUT" "$SAVE" "$GOTHIC_DIR" "$RUNNER"; do
  [[ "$path" == /* ]] || { echo "Absolute path required: $path" >&2; exit 2; }
done
if [[ "$(uname -s)" == Linux ]]; then
  exec 9>"$HOME/gothic-ai/body-simulation.lock"
  echo 'Waiting for body-simulation.lock (one batch)'
  flock -w "${RYZEN_LOCK_WAIT:-60}" 9 || { echo 'Ryzen занят' >&2; exit 75; }
  export VULKAN_SDK="$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0"
  export PATH="$VULKAN_SDK/bin:$PATH"
else
  [[ "$ACTION" == build || "$ACTION" == mac ]] || exit 2
fi
if [[ "$ACTION" == build ]]; then
  mkdir -p "$BUILD"
  FLAGS=()
  if [[ "$(uname -s)" == Linux ]]; then
    FLAGS=(-DCMAKE_POLICY_VERSION_MINIMUM=3.5 "-DCMAKE_CXX_FLAGS=-I$VULKAN_SDK/include" "-DCMAKE_EXE_LINKER_FLAGS=-L$VULKAN_SDK/lib")
  fi
  cmake -S "$ROOT/OpenGothic" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo "${FLAGS[@]}" >"$BUILD/configure.log" 2>&1
  TARGETS=(Gothic2Notr gothic-body-runner)
  [[ "${CONTRACT:-1}" == 0 ]] || TARGETS+=(check-navigation-memory)
  # Resume a partial Ninja build in the next batch, releasing the shared lock.
  if [[ "$(uname -s)" == Linux ]]; then
    timeout --signal=INT "${BUILD_BATCH_SECONDS:-90}" nice -n 10 cmake --build "$BUILD" --target "${TARGETS[@]}" --parallel "${JOBS:-10}" >"$BUILD/build.log" 2>&1 || {
      code=$?; tail -30 "$BUILD/build.log"; exit "$code";
    }
  else
    cmake --build "$BUILD" --target "${TARGETS[@]}" --parallel "${JOBS:-2}" >"$BUILD/build.log" 2>&1 || { tail -60 "$BUILD/build.log"; exit 1; }
  fi
  tail -8 "$BUILD/build.log"
  exit
fi
[[ ! -e "$OUT" ]] || { echo "Output already exists: $OUT" >&2; exit 2; }
mkdir -p "$OUT"
if [[ "$ACTION" == mac ]]; then
  if pgrep -f '^[^ ]*Gothic2Notr -g' >"$OUT/mac-game-pids.txt"; then
    echo 'Mac measurement skipped: game running' | tee "$OUT/mac-skipped.txt"
    exit
  fi
fi
case "$ACTION" in
  fence) cp "$ROOT/reports/navigation/2026-10-16-memory/fence.bench" "$OUT/scenario.bench" ;;
  perf20|perf50|mac)
    count=20; [[ "$ACTION" != perf50 ]] || count=50
    cat >"$OUT/scenario.bench" <<SCENARIO
seed 7
cheat god
set time 12 0
eye setup lobart $count
wait 2000
memory on all
wait 2000
memory measure begin $ACTION
wait 10000
memory measure end
memory off all
label done
SCENARIO
    ;;
  *) echo 'Usage: run.sh build|fence|perf20|perf50|mac' >&2; exit 2 ;;
esac
cd "$OUT"
env -u DISPLAY -u WAYLAND_DISPLAY GOTHIC_AI=0 OPENGOTHIC_TEXTURES= OPENGOTHIC_BODY_TRACE=0 \
  VK_DRIVER_FILES=/nonexistent VK_ICD_FILENAMES=/nonexistent \
  nice -n 10 "$RUNNER" -g "$GOTHIC_DIR" -g2 -devmode \
  --save "$SAVE" --scenario "$OUT/scenario.bench" --output "$OUT" --mode lockstep \
  >"$OUT/stdout.log" 2>"$OUT/stderr.log"
if grep -nE '"(memory|eye|keys)_error"' "$OUT/events.jsonl"; then exit 1; fi
if grep -nE 'marvin failed: (memory|eye|keys)' "$OUT/bench.log"; then exit 1; fi
if [[ "$ACTION" == fence ]]; then
  [[ -s "$OUT/memory-snapshots.jsonl" ]] || exit 1
else
  [[ -s "$OUT/memory-spans.jsonl" ]] || exit 1
fi
echo "$ACTION complete: $OUT"
