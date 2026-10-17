#!/usr/bin/env bash
# Stage 9.3: the held-out evaluation of one checkpoint, greedy, in lockstep next to the collector.
#   native   2048 open and 2048 near_wall duels, seeds 100000+ (as the 9.2 rounds)
#   h2h      head-to-head against each RIVAL: 1024 seeds 120000+, once in each seat
#   tasks    336 short tasks (distance, attack, block), seeds 95000+
# Usage: body-rl-ppo-eval.sh NAME CHECKPOINT OUT_DIR [RIVAL_NAME=CHECKPOINT ...]
# Environment as body-rl-ppo-loop.sh: PPO_CODE PPO_RUNNER [PPO_WORKERS PPO_BASELINE PPO_PYTHON PPO_LOCK].
# Finished parts are skipped. Compare with scripts/body_rl_compare.py.
# Copy gq-job.sh beside this script or into PPO_CODE/scripts; each collect is a batch request.
set -euo pipefail
: "${PPO_CODE:?}" "${PPO_RUNNER:?}"
name=$1 checkpoint=$2 out=$3
shift 3
workers=${PPO_WORKERS:-10}
baseline=${PPO_BASELINE:-$HOME/gothic-ai/body-baseline}
python=${PPO_PYTHON:-$HOME/gothic-ai/body/.venv/bin/python}
export PYTHONPATH=$PPO_CODE/body-service/src:$PPO_CODE/scripts
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for helper in "$script_dir/gq-job.sh" "$PPO_CODE/scripts/gq-job.sh"; do
  if [[ -r "$helper" ]]; then source "$helper"; break; fi
done
if ! declare -F gq_available >/dev/null; then
  if command -v "${GQ_BIN:-$HOME/gothic-ai/bin/gq}" >/dev/null 2>&1; then
    echo 'Copy gq-job.sh beside this script or into PPO_CODE/scripts' >&2
    exit 2
  fi
  gq_available() { return 1; }
fi
if gq_available && [[ -n "${PPO_LOCK:-}" ]]; then
  echo 'gq: PPO_LOCK is ignored; each phase is a separate queue request.'
fi
# One driver plus the collectors; measured ~0.7 GiB per worker plus Python/model base.
collect_threads=$((workers + 1))
collect_mem="$(((workers * 7 + 9) / 10 + 2))G"
mkdir -p "$out"
out=$(cd "$out" && pwd)
if gq_available && [[ -z "${GQ_OWNER:-}" && "$out" == "$HOME/gothic-ai/"* ]]; then
  task_path="${out#"$HOME/gothic-ai/"}"
  export GQ_OWNER="${task_path%%/*}"
fi
locked() {
  if [[ -n "${PPO_LOCK:-}" ]]; then flock -w 21600 "$PPO_LOCK" "$@"; else "$@"; fi
}
collect() {
  local dir=$1
  shift
  [[ -f $dir/summary.json ]] && return 0
  rm -rf "$dir"
  local cmd=(nice -n 10 "$python" "$PPO_CODE/scripts/body_rl_collect.py" --runner "$PPO_RUNNER" --assets "$baseline/assets" \
    --save "$baseline/runs/melee-reference-stage1-v4/run/save_slot_1.sav" --reference "$baseline/runs/melee-reference-stage1-v4" \
    --scenario "$PPO_CODE/scripts/bench/body-arena.bench" --workers "$workers" --checkpoint "$checkpoint" --output "$dir" \
    --temperature 0 "$@")
  if gq_available; then
    local log="$dir.launcher.log" result
    if gq_job --class batch --threads "$collect_threads" --mem "$collect_mem" --max 30m \
      --name "body PPO eval $(basename "$dir")" -- "${cmd[@]}" > "$log" 2>&1; then
      result=0
    else
      result=$?
    fi
    tail -1 "$log" | cut -c1-300
    return "$result"
  else
    locked "${cmd[@]}" 2>&1 | tail -1 | cut -c1-300
  fi
}
collect "$out/$name-open" --episodes 2048 --seed-start 100000
collect "$out/$name-wall" --episodes 2048 --seed-start 100000 --geometry near_wall
collect "$out/$name-tasks" --episodes 336 --seed-start 95000 --tasks distance,attack,block
for rival in "$@"; do
  for seat in primary opponent; do
    collect "$out/$name-vs-${rival%%=*}-$seat" --episodes 1024 --seed-start 120000 --opponent "${rival#*=}" \
      --native-share 0 --opponent-temperature 0 --learner-seat "$seat"
  done
done
echo "EVAL_DONE $name $(date +%T)"
