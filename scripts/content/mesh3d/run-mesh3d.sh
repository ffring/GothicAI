#!/usr/bin/env bash
# Run from the Mac worktree after setup-dev-ubuntu.sh; one window for both models.
# Usage: run-mesh3d.sh --image RGBA.png --seed N [--model both|trellis2|trellis]
set -euo pipefail

image=
seed=
model=both
while (( $# > 0 )); do
  case "$1" in
    --image|--seed|--model)
      (( $# >= 2 )) || exit 2
      case "$1" in
        --image) image="$2" ;;
        --seed) seed="$2" ;;
        --model) model="$2" ;;
      esac
      shift 2 ;;
    --help|-h)
      echo 'Usage: run-mesh3d.sh --image RGBA.png --seed N [--model both|trellis2|trellis]'
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ -f "$image" && "$seed" =~ ^[0-9]+$ ]] || { echo 'An RGBA PNG and a nonnegative seed are required' >&2; exit 2; }
case "$model" in both|trellis2|trellis) ;; *) exit 2 ;; esac
script_dir="$(cd "$(dirname "$0")" && pwd)"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-seed${seed}-$$"

check_mac_game() {
  local game_status
  set +e
  # Exact process name excludes an agent whose prompt mentions the game binary.
  LC_ALL=C pgrep -lx Gothic2Notr
  game_status=$?
  set -e
  [[ "$game_status" == 1 ]] || { echo "Mac game preflight failed (pgrep exit $game_status); no services stopped" >&2; exit 75; }
}
check_mac_game
ssh -T dev-ubuntu mkdir -p gothic-ai/mesh3d/inputs
scp "$script_dir/generate_trellis.py" "$script_dir/adapter.py" dev-ubuntu:gothic-ai/mesh3d/
scp "$image" "dev-ubuntu:gothic-ai/mesh3d/inputs/$run_id.png"
# Repeat after transfer, immediately before allowing service downtime.
check_mac_game

ssh -T dev-ubuntu bash -s -- "$run_id" "$seed" "$model" <<'REMOTE'
set -euo pipefail
run_id="$1"
seed="$2"
model="$3"
cd "$HOME/gothic-ai/mesh3d"
export HF_HOME="$PWD/hf" HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export TORCH_HOME="$PWD/cache/torch" XDG_CACHE_HOME="$PWD/cache" TMPDIR="$PWD/tmp"
export CUDA_HOME="$PWD/toolchain" TORCH_CUDA_ARCH_LIST=8.6 ATTN_BACKEND=xformers
export CC="$CUDA_HOME/bin/x86_64-conda-linux-gnu-gcc" CXX="$CUDA_HOME/bin/x86_64-conda-linux-gnu-g++"
export CUDACXX="$CUDA_HOME/bin/nvcc" FLEX_GEMM_AUTOSAVE_AUTOTUNE_CACHE=0
export PATH="$CUDA_HOME/bin:$PATH"
export CPATH="$CUDA_HOME/targets/x86_64-linux/include${CPATH:+:$CPATH}"
export LIBRARY_PATH="$CUDA_HOME/lib:$CUDA_HOME/targets/x86_64-linux/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"
export LD_LIBRARY_PATH="$CUDA_HOME/lib:$CUDA_HOME/targets/x86_64-linux/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
mkdir -p "$TMPDIR" "runs/$run_id"

models=(trellis2 trellis)
[[ "$model" == both ]] || models=("$model")
for current in "${models[@]}"; do
  [[ -x ".venv-$current/bin/python" ]] || { echo "Missing environment for $current; run setup first" >&2; exit 1; }
done
if [[ "$model" != trellis ]]; then
  [[ -s models/dinov3/config.json && ( -s models/dinov3/model.safetensors || -s models/dinov3/model.safetensors.index.json ) ]] || {
    echo 'DINOv3 weights are unavailable; finish the authorized download before opening the GPU window' >&2
    exit 1
  }
fi
# Reject opaque or empty inputs before stopping any service. This uses no GPU.
".venv-${models[0]}/bin/python" - "$PWD/inputs/$run_id.png" <<'PY'
import sys
from PIL import Image
with Image.open(sys.argv[1]) as image:
    if image.format != "PNG" or image.mode != "RGBA":
        raise SystemExit("Input must be an RGBA PNG")
    alpha_min, alpha_max = image.getchannel("A").getextrema()
    if alpha_min == 255 or alpha_max == 0:
        raise SystemExit("Input must have both visible content and a transparent background")
PY

exec 9>"$HOME/gothic-ai/materials/gpu.lock"
flock -n 9 || { echo 'The shared materials/mesh3d GPU window is occupied' >&2; exit 75; }
log_event() {
  printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$run_id" "$1" "$2" >> events.tsv
}
listener_url() {
  local port="$1" path="$2" endpoint host
  endpoint=$(timeout 3 ss -ltnH "sport = :$port" 2>/dev/null | awk '{print $4}' | sort -u) || return 1
  [[ -n "$endpoint" && "$endpoint" != *$'\n'* && "$endpoint" == *":$port" ]] || return 1
  host="${endpoint%:*}"
  case "$host" in
    '*'|0.0.0.0|'[::]'|'::') host=127.0.0.1 ;;
    \[*\]) ;;
    *:*) host="[$host]" ;;
  esac
  [[ -n "$host" ]] || return 1
  printf 'http://%s:%s%s' "$host" "$port" "$path"
}
unit_responds() {
  local unit="$1" url="$2" code
  if [[ "$unit" == gothic-gemma ]]; then
    curl --fail --silent --max-time 2 "$url" >/dev/null
  else
    # An HTTP upgrade rejection also proves that the WebSocket service responds.
    code=$(curl --silent --output /dev/null --max-time 2 --write-out '%{http_code}' "$url" || true)
    [[ -n "$code" && "$code" != 000 ]]
  fi
}
free_memory() {
  local value
  value=$(timeout 5 nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits)
  [[ "$value" =~ ^[0-9]+$ ]] || { echo 'Expected one GPU with numeric free memory' >&2; return 1; }
  printf '%s' "$value"
}

prior=0
if [[ -f downtime.tsv ]]; then
  prior=$(awk '{s+=$3} END {print s+0}' downtime.tsv)
fi
[[ "$prior" =~ ^[0-9]+$ ]] || { echo 'Invalid downtime history' >&2; exit 1; }
# The final 600 seconds are reserved for bounded service recovery.
budget=$(( 3000 - prior ))
(( budget > 60 )) || { echo 'Cumulative stopped-service budget exhausted; no services stopped' >&2; exit 75; }
deadline=$(( $(date +%s) + budget ))
stopped=()
health_urls=()
stop_epoch=0
run_pid=
restore() {
  local rc=$? index unit healthy attempt elapsed total
  trap - EXIT HUP INT TERM
  set +e
  if [[ -n "$run_pid" ]]; then
    # setsid below isolates only this run's process group, including its children.
    kill -TERM -- "-$run_pid" 2>/dev/null
    for attempt in {1..10}; do
      kill -0 "$run_pid" 2>/dev/null || break
      sleep 1
    done
    kill -KILL -- "-$run_pid" 2>/dev/null
    wait "$run_pid" 2>/dev/null
  fi
  # Start every stopped unit before waiting for either model to become ready.
  for unit in ${stopped[@]+"${stopped[@]}"}; do
    timeout 10 systemctl --user start --no-block "$unit" || rc=1
    log_event start "$unit"
  done
  for index in "${!stopped[@]}"; do
    unit="${stopped[$index]}"
    healthy=0
    # Two services x 30 x (3s status + 2s HTTP + 2s sleep), within the 600s reserve.
    for attempt in {1..30}; do
      if timeout 3 systemctl --user is-active --quiet "$unit" && unit_responds "$unit" "${health_urls[$index]}"; then
        healthy=1
        break
      fi
      sleep 2
    done
    log_event health "$unit:$healthy"
    if [[ "$healthy" != 1 ]]; then
      echo "SERVICE RESTORE FAILED: $unit" >&2
      rc=1
    fi
  done
  if (( stop_epoch > 0 )); then
    elapsed=$(( $(date +%s) - stop_epoch ))
    total=$(( prior + elapsed ))
    printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$run_id" "$elapsed" >> downtime.tsv
    log_event downtime "window_seconds=$elapsed cumulative_seconds=$total"
    (( total <= 3600 )) || { echo 'CUMULATIVE SERVICE DOWNTIME LIMIT EXCEEDED' >&2; rc=1; }
  fi
  timeout 5 nvidia-smi > "runs/$run_id/nvidia-restored.txt" || rc=1
  log_event window-end "exit=$rc"
  exit "$rc"
}
trap restore EXIT
trap 'exit 130' HUP INT TERM

log_event window-start "$model seed=$seed"
timeout 5 nvidia-smi > "runs/$run_id/nvidia-before.txt"
free_mib=$(free_memory)
# Require the memory available after Gemma/Decider; actual TRELLIS.2 fit is a pilot result.
min_free_mib=16000
# These are the only services this window may stop. Voxtral/VoxCPM stay running.
for unit in gothic-gemma gothic-decider; do
  (( free_mib >= min_free_mib )) && break
  timeout 3 systemctl --user is-active --quiet "$unit" || continue
  if [[ "$unit" == gothic-gemma ]]; then
    url=$(listener_url 47834 /v1/models) || { echo "Cannot identify $unit listener; refusing to stop it" >&2; exit 1; }
  else
    url=$(listener_url 47832 /) || { echo "Cannot identify $unit listener; refusing to stop it" >&2; exit 1; }
  fi
  unit_responds "$unit" "$url" || { echo "$unit does not respond before stop; refusing to stop it" >&2; exit 1; }
  health_urls+=("$url")
  stopped+=("$unit")
  (( stop_epoch > 0 )) || stop_epoch=$(date +%s)
  log_event stop-request "$unit"
  timeout 35 systemctl --user stop "$unit"
  log_event stop "$unit"
  free_mib=$(free_memory)
done
timeout 5 nvidia-smi > "runs/$run_id/nvidia-ready.txt"
(( free_mib >= min_free_mib )) || { echo "Insufficient GPU memory: $free_mib MiB free, require $min_free_mib MiB" >&2; exit 75; }

for current in "${models[@]}"; do
  budget=$(( deadline - $(date +%s) ))
  (( budget > 0 )) || { echo 'Generation deadline reached' >&2; exit 124; }
  output="$PWD/runs/$run_id/$current"
  log_event generation-start "$current budget_seconds=$budget"
  setsid timeout --kill-after=10 "$budget" ".venv-$current/bin/python" -u generate_trellis.py \
    --root "$PWD" --image "$PWD/inputs/$run_id.png" --model "$current" \
    --seed "$seed" --output "$output" > "runs/$run_id/$current.log" 2>&1 &
  run_pid=$!
  set +e
  wait "$run_pid"
  rc=$?
  run_pid=
  set -e
  log_event generation-end "$current exit=$rc"
  (( rc == 0 )) || { cat "runs/$run_id/$current.log" >&2; exit "$rc"; }
done
printf 'Results: ~/gothic-ai/mesh3d/runs/%s\n' "$run_id"
REMOTE
