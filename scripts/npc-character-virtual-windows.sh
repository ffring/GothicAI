#!/usr/bin/env bash
# Matched passive routine windows from the navigation baseline, one game at a time.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# RUNNER optionally uses the existing CPU-only body runner instead of a rendered game.
# SAVE, GOTHIC_DIR and RUNNER must be absolute when this mode is selected.
OUT="${1:-$ROOT/.bench/npc-character-virtual/windows}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
for mode in off on; do
  value=0
  [[ "$mode" != on ]] || value=1
  for spot in market harbour onar lobart; do
    case "$spot" in
      market) anchor=NW_CITY_MERCHANT_PATH_20 ;;
      harbour) anchor=NW_CITY_HABOUR_06_B ;;
      onar) anchor=NW_BIGFARM_HOUSE_OUT_04 ;;
      lobart) anchor=NW_FARM1_ENTRANCE_01 ;;
    esac
    for hour in 7 20; do
      name="$spot-$hour"
      run="$OUT/$mode/$name"
      mkdir -p "$run"
      [[ ! -e "$run/events.jsonl" ]] || { echo "Already measured: $run" >&2; exit 2; }
      scenario="$run/window.bench"
      cat >"$scenario" <<EOF
seed 7
cheat god
set time $((hour-1)) 59
goto $anchor
wait 4000
pos
stuck begin $name 3000
npccharacter observe begin $name
wait 248300
pos
npccharacter observe end
stuck end
label done
EOF
      while pgrep -f '^[^ ]*Gothic2Notr[^ /]*( |$)' >/dev/null; do sleep 5; done
      if [[ -n "${RUNNER:-}" ]]; then
        : "${SAVE:?Set SAVE to the absolute source save}"
        : "${GOTHIC_DIR:?Set GOTHIC_DIR to the absolute game assets}"
        [[ "$RUNNER" == /* && "$SAVE" == /* && "$GOTHIC_DIR" == /* ]]
        mkdir -p "$run/run"
        printf '[GAME]\nnpc_character_virtual=%s\n' "$value" >"$run/run/Gothic.ini"
        (cd "$run/run" && GOTHIC_AI=0 OPENGOTHIC_STUCK_LOG="$run/stuck.jsonl" \
          "$RUNNER" -g "$GOTHIC_DIR" -g2 -devmode --save "$SAVE" \
          --scenario "$scenario" --output "$run" --mode lockstep) >"$run/runner.log" 2>&1
      else
        GOTHIC_AI=0 NO_BUILD=1 NPC_CHARACTER_VIRTUAL="$value" TIMEOUT=600 \
          OPENGOTHIC_STUCK_LOG="$run/stuck.jsonl" \
          "$ROOT/scripts/bench.sh" "$scenario" "$run" >"$run/runner.log" 2>&1
      fi
      if ! rg -q '^done$' "$run/bench.log" || rg -q 'failed|unknown command|goto failed' "$run/bench.log"; then
        echo "Window failed: $run" >&2
        exit 1
      fi
      echo "$mode $name complete"
    done
  done
done
