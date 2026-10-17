#!/usr/bin/env bash
# Stage 9.3: recurrent PPO iterations on the model host, next to body_rl_collect.py.
# Each iteration collects with the current policy at temperature 1 (a share of the duels against
# a self-play pool, a share at walls), then body_rl_ppo.py update. The first PPO_WARMUP iterations
# fit only the critic. Every PPO_SNAPSHOT_EVERY iterations the policy joins the pool; every
# PPO_EVAL_EVERY iterations a greedy evaluation on fixed held-out seeds (native open, native wall,
# and the first pool member). Finished steps are skipped, so a rerun resumes.
# With PPO_LOCK set, every simulation step holds that flock (the Ryzen's body-simulation.lock).
#
# Required: PPO_WORK PPO_CODE PPO_RUNNER PPO_START PPO_REFERENCE PPO_POOL ("a.pt b.pt ...")
# Optional: PPO_ITERATIONS(40) PPO_EPISODES(1024) PPO_WORKERS(10) PPO_WARMUP(2) PPO_SNAPSHOT_EVERY(5)
#           PPO_EVAL_EVERY(5) PPO_EVAL_EPISODES(256) PPO_NATIVE_SHARE(0.5) PPO_WALL_SHARE(0.5)
#           PPO_THREADS(10) PPO_UPDATE_ARGS PPO_ASSETS PPO_SAVE PPO_BASELINE PPO_PYTHON PPO_LOCK
set -euo pipefail
: "${PPO_WORK:?}" "${PPO_CODE:?}" "${PPO_RUNNER:?}" "${PPO_START:?}" "${PPO_REFERENCE:?}" "${PPO_POOL:?}"
iterations=${PPO_ITERATIONS:-40} episodes=${PPO_EPISODES:-1024} workers=${PPO_WORKERS:-10}
warmup=${PPO_WARMUP:-2} snapshot_every=${PPO_SNAPSHOT_EVERY:-5} eval_every=${PPO_EVAL_EVERY:-5}
eval_episodes=${PPO_EVAL_EPISODES:-256} threads=${PPO_THREADS:-10}
baseline=${PPO_BASELINE:-$HOME/gothic-ai/body-baseline}
assets=${PPO_ASSETS:-$baseline/assets}
save=${PPO_SAVE:-$baseline/runs/melee-reference-stage1-v4/run/save_slot_1.sav}
python=${PPO_PYTHON:-$HOME/gothic-ai/body/.venv/bin/python}
export PYTHONPATH=$PPO_CODE/body-service/src:$PPO_CODE/scripts
mkdir -p "$PPO_WORK"
cd "$PPO_WORK"
[[ -f pool.txt ]] || printf '%s\n' $PPO_POOL > pool.txt

locked() {
  if [[ -n "${PPO_LOCK:-}" ]]; then flock -w 21600 "$PPO_LOCK" "$@"; else "$@"; fi
}
collect() {
  locked nice -n 10 "$python" "$PPO_CODE/scripts/body_rl_collect.py" --runner "$PPO_RUNNER" --assets "$assets" --save "$save" \
    --reference "$baseline/runs/melee-reference-stage1-v4" --scenario "$PPO_CODE/scripts/bench/body-arena.bench" \
    --workers "$workers" "$@" 2>&1 | tail -1 | cut -c1-400
}
evaluate() {  # policy, iteration directory
  local policy=$1 out=$2/eval first
  first=$(head -1 pool.txt)
  mkdir -p "$out"
  [[ -d $out/open ]] || collect --checkpoint "$policy" --output "$PWD/$out/open" --episodes "$eval_episodes" --seed-start 110000 --temperature 0
  [[ -d $out/wall ]] || collect --checkpoint "$policy" --output "$PWD/$out/wall" --episodes "$eval_episodes" --seed-start 110000 --temperature 0 --geometry near_wall
  [[ -d $out/pool ]] || collect --checkpoint "$policy" --output "$PWD/$out/pool" --episodes "$eval_episodes" --seed-start 110000 --temperature 0 \
    --opponent "$first" --native-share 0 --opponent-temperature 0
}

policy=$PPO_START critic=()
for ((i = 1; i <= iterations; i++)); do
  it=$(printf 'iter-%03d' "$i")
  mkdir -p "$it"
  if [[ ! -f $it/update/best.pt ]]; then
    opponents=()
    while read -r path; do [[ -n $path ]] && opponents+=(--opponent "$path"); done < pool.txt
    if [[ ! -f $it/collect/summary.json ]]; then
      rm -rf "$it/collect"
      echo "== $it collect $(date +%T)"
      collect --checkpoint "$policy" --output "$PWD/$it/collect" --episodes "$episodes" --seed-start $((200000+i*episodes)) \
        --temperature 1.0 --sampling-seed "$i" "${opponents[@]}" --native-share "${PPO_NATIVE_SHARE:-0.5}" \
        --wall-share "${PPO_WALL_SHARE:-0.5}"
    fi
    policy_epochs=()
    ((i > warmup)) || policy_epochs=(--policy-epochs 0)
    echo "== $it update $(date +%T)"
    rm -rf "$it/update"
    nice -n 5 "$python" "$PPO_CODE/scripts/body_rl_ppo.py" update --policy "$policy" --reference "$PPO_REFERENCE" "${critic[@]}" \
      --collect "$it/collect" --output "$it/update" --threads "$threads" "${policy_epochs[@]}" ${PPO_UPDATE_ARGS:-} 2>&1 | tail -1 | cut -c1-500
  fi
  policy=$PWD/$it/update/best.pt critic=(--critic "$PWD/$it/update/critic.pt")
  if ((i % snapshot_every == 0)) && ! grep -qx "$policy" pool.txt; then echo "$policy" >> pool.txt; fi
  if ((i % eval_every == 0)); then echo "== $it eval $(date +%T)"; evaluate "$policy" "$it"; fi
done
echo "PPO_DONE $(date +%T)"
