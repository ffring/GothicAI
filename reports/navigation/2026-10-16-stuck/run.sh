#!/usr/bin/env bash
# Run from the repository; all simulation output paths must be absolute.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
: "${RUNNER:?Set RUNNER to the built gothic-body-runner}"
: "${SAVE:?Set SAVE to the chapter-one save_slot_1.sav}"
: "${GOTHIC_DIR:?Set GOTHIC_DIR to the game assets}"
: "${OUT:?Set OUT to an absolute output directory}"
[[ "$OUT" == /* && "$RUNNER" == /* && "$SAVE" == /* && "$GOTHIC_DIR" == /* ]]
exec 9>"$HOME/gothic-ai/body-simulation.lock"
flock -w "${RYZEN_LOCK_WAIT:-900}" 9 || { echo 'Ryzen занят' >&2; exit 75; }
df -h "$HOME"
mkdir -p "$OUT"
run() {
  local name="$1" scenario="$2" output="$OUT/$1"
  [[ ! -e "$output/stuck.jsonl" ]] || { echo "Already measured: $output" >&2; exit 2; }
  mkdir -p "$output"
  env -u DISPLAY -u WAYLAND_DISPLAY GOTHIC_AI=0 \
    OPENGOTHIC_STUCK_LOG="$output/stuck.jsonl" \
    VK_DRIVER_FILES=/nonexistent VK_ICD_FILENAMES=/nonexistent \
    nice -n 10 "$RUNNER" -g "$GOTHIC_DIR" -g2 -devmode \
      --save "$SAVE" --scenario "$scenario" --output "$output" --mode lockstep \
      >"$output/stdout.log" 2>"$output/stderr.log"
  if grep -Eq 'stuck .*failed|goto failed|unknown command|npcgo: no|freeze: no' "$output/bench.log"; then
    cat "$output/bench.log" >&2
    exit 1
  fi
  echo "$name complete"
}
if [[ "${1:-ordinary}" == fixture ]]; then
  run intentional "$ROOT/reports/navigation/2026-10-16-stuck/intentional.bench"
  exit
fi
for spot in market harbour onar lobart; do
  case "$spot" in
    market) anchor=NW_CITY_MERCHANT_PATH_20 ;;
    harbour) anchor=NW_CITY_HABOUR_06_B ;;
    onar) anchor=NW_BIGFARM_HOUSE_OUT_04 ;;
    lobart) anchor=NW_FARM1_ENTRANCE_01 ;;
  esac
  for hour in 7 20; do
    name="$spot-$hour"
    scenario="$OUT/$name.bench"
    cat >"$scenario" <<EOF
seed 7
cheat god
set time $((hour-1)) 59
goto $anchor
wait 4000
pos
stuck begin $name 3000
wait 248300
pos
stuck end
label done
EOF
    run "$name" "$scenario"
  done
done
