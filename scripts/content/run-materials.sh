#!/usr/bin/env bash
# Run one GPU stage on dev-ubuntu. Invoke from the Mac worktree, never remotely.
# Usage: scripts/content/run-materials.sh sdxl|normals|appearance|stablematerials
set -euo pipefail
STAGE="${1:?stage required}"
case "$STAGE" in sdxl|normals|appearance|stablematerials) ;; *) exit 2 ;; esac
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
started=$SECONDS
while pgrep -fl Gothic2Notr; do
  (( SECONDS - started < 3600 )) || { echo 'Mac game still running after 60 minutes; no units stopped' >&2; exit 75; }
  echo 'Waiting for the Mac game before temporarily stopping a model service'
  sleep 30
done
scp "$ROOT/scripts/content/bakeoff.py" "$ROOT/scripts/content/stable_materials_adapter.py" dev-ubuntu:gothic-ai/materials/
# registry.jsonl is copied once at preparation. Preserve remote stage progress.
ssh -T dev-ubuntu bash -s -- "$STAGE" <<'REMOTE'
set -euo pipefail
stage="$1"
cd "$HOME/gothic-ai/materials"
export HF_HOME="$PWD/hf" HF_HUB_DISABLE_IMPLICIT_TOKEN=1
export XDG_CACHE_HOME="$PWD/cache" TMPDIR="$PWD/tmp"
mkdir -p "$TMPDIR"
exec 9>gpu.lock
flock -n 9 || { echo 'our material generation is already running' >&2; exit 75; }
# No file or weight may be installed outside the task directory.
bytes=$(du -sb . | cut -f1)
(( bytes < 80000000000 )) || { echo '80 GB task limit reached' >&2; exit 1; }
nvidia-smi
stopped=()
stop_epoch=0
log_event() {
  printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" >> units.tsv
}
restore() {
  rc=$?
  trap - EXIT HUP INT TERM
  for unit in "${stopped[@]}"; do
    timeout 10 systemctl --user start --no-block "$unit" || rc=1
    log_event start "$unit"
  done
  for unit in "${stopped[@]}"; do
    healthy=0
    for attempt in {1..45}; do
      if systemctl --user is-active --quiet "$unit"; then
        if [[ "$unit" == gothic-gemma ]]; then
          curl --fail --silent --max-time 2 http://127.0.0.1:47834/v1/models > health-gemma.json && healthy=1 || true
        else
          # HTTP upgrade rejection is also a response from the WS server.
          code=$(curl --silent --output /dev/null --max-time 2 --write-out '%{http_code}' http://127.0.0.1:47832/ || true)
          [[ "$code" != 000 && -n "$code" ]] && healthy=1
        fi
      fi
      [[ "$healthy" == 1 ]] && break
      sleep 2
    done
    systemctl --user is-active "$unit" || true
    log_event health "$unit:$healthy"
    if [[ "$healthy" != 1 ]]; then
      echo "SERVICE RESTORE FAILED: $unit" >&2
      rc=1
    fi
  done
  if (( stop_epoch > 0 )); then
    elapsed=$(( $(date +%s) - stop_epoch ))
    printf '%s\t%s\n' "$stage" "$elapsed" >> downtime.tsv
    (( elapsed <= 3600 )) || { echo 'SERVICE DOWNTIME LIMIT EXCEEDED' >&2; rc=1; }
  fi
  exit "$rc"
}
trap restore EXIT
trap 'exit 130' HUP INT TERM
free_mib=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits)
# Keep 8.5 GiB free before loading, including StableMaterials' float32 decoder.
for unit in gothic-gemma gothic-decider; do
  (( free_mib >= 8500 )) && break
  systemctl --user is-active --quiet "$unit" || continue
  # The Mac preflight immediately above authorized this GPU window.
  (( stop_epoch > 0 )) || stop_epoch=$(date +%s)
  stopped+=("$unit")
  log_event stop-request "$unit"
  timeout 35 systemctl --user stop "$unit"
  log_event stop "$unit"
  free_mib=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits)
done
nvidia-smi
prior=0
if [[ -f downtime.tsv ]]; then prior=$(awk '{s+=$2} END {print s+0}' downtime.tsv); fi
budget=$(( 3000 - prior ))
if (( stop_epoch > 0 )); then budget=$(( budget - $(date +%s) + stop_epoch )); fi
(( budget > 60 )) || { echo 'Cumulative stopped-unit budget exhausted' >&2; exit 75; }
# Hard wall deadline reserves at least 600 seconds for bounded recovery.
timeout -k 10 "$budget" .venv/bin/python -u bakeoff.py "$stage" --registry registry.jsonl --root "$PWD" > "$stage.log" 2>&1
REMOTE
