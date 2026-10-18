#!/usr/bin/env bash
# Training stand-in for the P30 load run: lockstep collection as a gq batch job on cores 2-15.
set -euo pipefail
C=$HOME/gothic-ai/body-net; B=$HOME/gothic-ai/body-baseline; out=$HOME/gothic-ai/p30-load/collect-${1:-1}
export GQ_OWNER=claude-p30-load PYTHONPATH=$C/body-service/src:$C/scripts
rm -rf "$out"
"$HOME/gothic-ai/bin/gq" submit --class batch --threads 25 --mem 20G --max 25m --name "p30-load lockstep collect" -- \
  /usr/bin/nice -n 10 "$HOME/gothic-ai/body/.venv/bin/python" "$C/scripts/body_rl_collect.py" \
  --runner "$HOME/gothic-ai/body-api-merged/build/opengothic/gothic-body-runner" --assets "$B/assets" \
  --save "$B/runs/melee-reference-stage1-v4/run/save_slot_1.sav" --reference "$B/runs/melee-reference-stage1-v4" \
  --scenario "$C/scripts/bench/body-arena.bench" --workers 24 --episodes 20000 \
  --checkpoint "$HOME/gothic-ai/body/models/working/best.pt" --output "$out" --temperature 0 | tail -1
