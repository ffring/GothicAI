#!/usr/bin/env bash
# Repeat the eye benchmark. On Ryzen: own source/build/output under ~/gothic-ai/nav-eye.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
ACTION="${1:-matrix}"
TASK="${TASK:-$HOME/gothic-ai/nav-eye}"
OUT="${OUT:-$TASK/runs/eye}"
BUILD="${BUILD:-$TASK/build}"
RUNNER="${RUNNER:-$BUILD/opengothic/gothic-body-runner}"
SAVE="${SAVE:-$TASK/save_slot_1.sav}"
GOTHIC_DIR="${GOTHIC_DIR:-$HOME/gothic-ai/game}"
[[ "$OUT" == /* && "$BUILD" == /* && "$RUNNER" == /* && "$SAVE" == /* && "$GOTHIC_DIR" == /* ]]
if [[ "$(uname -s)" != Linux && "$ACTION" != build && "$ACTION" != mac ]]; then
  echo 'The full matrix runs on Ryzen/Linux; use mac for the short Mac measurement.' >&2
  exit 2
fi

if [[ "$(uname -s)" == Linux ]]; then
  mkdir -p "$HOME/gothic-ai"
  exec 9>"$HOME/gothic-ai/body-simulation.lock"
  echo "Waiting for body-simulation.lock"
  flock -w "${RYZEN_LOCK_WAIT:-900}" 9 || { echo 'Ryzen занят' >&2; exit 75; }
  df -h "$HOME"
fi

if [[ "$ACTION" == build || "$ACTION" == all ]]; then
  if [[ "$(uname -s)" == Linux ]]; then
    export VULKAN_SDK="$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0"
    export PATH="$VULKAN_SDK/bin:$PATH"
    FLAGS=(-DCMAKE_POLICY_VERSION_MINIMUM=3.5 "-DCMAKE_CXX_FLAGS=-I$VULKAN_SDK/include" "-DCMAKE_EXE_LINKER_FLAGS=-L$VULKAN_SDK/lib")
    DEFAULT_JOBS=12
  else
    FLAGS=()
    DEFAULT_JOBS=2
  fi
  mkdir -p "$BUILD"
  cmake -S "$ROOT/OpenGothic" -B "$BUILD" -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo "${FLAGS[@]}" >"$BUILD/configure.log" 2>&1
  nice -n 10 cmake --build "$BUILD" --target Gothic2Notr gothic-body-runner check-navigation-eye --parallel "${JOBS:-$DEFAULT_JOBS}" >"$BUILD/build.log" 2>&1 || { tail -80 "$BUILD/build.log"; exit 1; }
  tail -5 "$BUILD/build.log"
  [[ "$ACTION" != build ]] || exit
fi

if [[ "$ACTION" == mac ]]; then
  [[ "$(uname -s)" == Darwin ]]
  mkdir -p "$OUT"
  if LC_ALL=C pgrep -fl Gothic2Notr >"$OUT/mac-process-check.txt"; then
    echo 'Mac benchmark skipped: pgrep -fl Gothic2Notr is nonempty.' | tee "$OUT/mac-skipped.txt"
    exit
  fi
fi

run() {
  local name="$1" scene="$2" count="$3" scenario="$OUT/$1.bench" output="$OUT/$1"
  local expected=1
  [[ ! -e "$output" ]] || { echo "Output already exists: $output" >&2; exit 2; }
  mkdir -p "$output"
  {
    echo 'seed 7'
    echo 'cheat god'
    echo 'set time 12 0'
    echo "eye setup $scene $count"
    echo 'wait 2000'
    echo "eye begin 500 100 off $name-baseline"
    echo 'wait 12000'
    echo 'eye end'
    for layout in ${LAYOUTS:-250 500 1000}; do
      for range in ${RANGES:-20 50 100}; do
        expected=$((expected+1))
        echo "eye begin $layout $range on $name-$layout-$range"
        echo 'wait 12000'
        echo 'eye end'
        if [[ "$layout" == 500 && "$range" == 100 ]]; then
          echo "eye snapshot $name 0"
        fi
      done
    done
    if [[ "$count" == 10 && "$scene" == market ]]; then echo 'eye wall'; fi
    echo 'label done'
  } >"$scenario"
  (
    cd "$output"
    env -u DISPLAY -u WAYLAND_DISPLAY GOTHIC_AI=0 OPENGOTHIC_TEXTURES= OPENGOTHIC_BODY_TRACE=0 \
      VK_DRIVER_FILES=/nonexistent VK_ICD_FILENAMES=/nonexistent \
      nice -n 10 "$RUNNER" -g "$GOTHIC_DIR" -g2 -devmode \
      --save "$SAVE" --scenario "$scenario" --output "$output" --mode lockstep \
      >"$output/stdout.log" 2>"$output/stderr.log"
  )
  if grep -Eq 'eye.*(failed|error)|unknown command|goto failed' "$output/bench.log"; then
    cat "$output/bench.log" >&2
    exit 1
  fi
  if grep -q '"eye_error"' "$output/events.jsonl"; then
    tail -5 "$output/events.jsonl" >&2
    exit 1
  fi
  [[ -f "$output/eye-spans.jsonl" && "$(wc -l <"$output/eye-spans.jsonl")" -eq "$expected" ]] || {
    echo "Incomplete eye spans in $output" >&2; exit 1;
  }
  echo "$name complete"
}
mkdir -p "$OUT"
case "$ACTION" in
  all|matrix)
    if [[ "$ACTION" == all ]]; then
      OUT="$OUT-smoke" LAYOUTS=500 RANGES=100 run smoke-market-10 market 10
    fi
    for scene in market lobart; do
      for count in 10 20 50; do run "$scene-$count" "$scene" "$count"; done
    done ;;
  mac) LAYOUTS=500 RANGES=100 run mac-market-20 market 20 ;;
  smoke) LAYOUTS=500 RANGES=100 run smoke-market-10 market 10 ;;
  calibrate) : ;;
  *) echo 'Usage: run.sh all|build|matrix|smoke|mac|calibrate' >&2; exit 2 ;;
esac

# Calibration stands outside the pasture; the performance pool above stays in the field.
if [[ "$ACTION" == all || "$ACTION" == matrix || "$ACTION" == calibrate ]]; then
  nice -n 10 cmake --build "$BUILD" --target Gothic2Notr gothic-body-runner --parallel "${JOBS:-12}" >"$BUILD/calibration-build.log" 2>&1 || { tail -80 "$BUILD/calibration-build.log"; exit 1; }
  calibration="$OUT/lobart-fence"
  [[ ! -e "$calibration" ]] || { echo "Output already exists: $calibration" >&2; exit 2; }
  mkdir -p "$calibration"
  cat >"$OUT/lobart-fence.bench" <<'SCENARIO'
seed 7
cheat god
set time 12 0
eye setup lobart-fence 1
wait 2000
eye snapshot lobart-fence 0
label done
SCENARIO
  (
    cd "$calibration"
    env -u DISPLAY -u WAYLAND_DISPLAY GOTHIC_AI=0 OPENGOTHIC_TEXTURES= OPENGOTHIC_BODY_TRACE=0 \
      VK_DRIVER_FILES=/nonexistent VK_ICD_FILENAMES=/nonexistent \
      nice -n 10 "$RUNNER" -g "$GOTHIC_DIR" -g2 -devmode \
      --save "$SAVE" --scenario "$OUT/lobart-fence.bench" --output "$calibration" --mode lockstep \
      >"$calibration/stdout.log" 2>"$calibration/stderr.log"
  )
  [[ -s "$calibration/eye-snapshots.jsonl" ]] || { tail -10 "$calibration/bench.log" >&2; exit 1; }
  echo 'lobart-fence calibration complete'
fi
