#!/bin/bash
# the speed probe of docs/navigation.md, stage 0: one runner per crowd size, lockstep, no window
T=$HOME/gothic-ai/nav-probe
R=$T/build/opengothic/gothic-body-runner
A=$HOME/gothic-ai/body-baseline/assets
S=$HOME/gothic-ai/body-baseline/runs/melee-reference-stage1-v4/run/save_slot_1.sav
exec 9>"$HOME/gothic-ai/body-simulation.lock"
flock -n 9 || { echo "the body simulation lock is taken" >&2; exit 1; }
one() {  # one <name> <bench>
  local out="$T/runs/$1"
  rm -rf "$out"; mkdir -p "$out"
  env -u DISPLAY -u WAYLAND_DISPLAY VK_DRIVER_FILES=/nonexistent VK_ICD_FILENAMES=/nonexistent \
    nice -n 10 "$R" -g "$A" -g2 -devmode --save "$S" --scenario "$2" --output "$out" --mode lockstep \
    > "$out/stdout.log" 2> "$out/stderr.log"
  echo "$1 exit $?"
}
case "${1:-sizes}" in
  sizes) for N in 0 20 50 100; do one "n$N" "$T/bench/n$N.bench"; done ;;
  parallel)  # parallel <k> <N>: k runners at once, each with N walkers
    for i in $(seq 1 "$2"); do one "p$2-$3-$i" "$T/bench/n$3.bench" & done; wait ;;
esac
