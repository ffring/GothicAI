#!/usr/bin/env bash
# Release the common Ryzen lock between small policy-drive batches.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ "$(uname -s)" == Linux ]] || { echo 'Run nav-drive-batch.sh on Ryzen/Linux' >&2; exit 2; }
batch_size="${NAV_DRIVE_BATCH_SIZE:-2}"
lock_wait="${RYZEN_LOCK_WAIT:-900}"
pause_seconds="${NAV_DRIVE_PAUSE_SECONDS:-1}"
collector_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --batch-size) batch_size="$2"; shift 2 ;;
    --lock-wait) lock_wait="$2"; shift 2 ;;
    --pause-seconds) pause_seconds="$2"; shift 2 ;;
    *) collector_args+=("$1"); shift ;;
  esac
done
[[ "$batch_size" =~ ^[23]$ && "$lock_wait" =~ ^[0-9]+$ && "$pause_seconds" =~ ^[0-9]+$ ]] || {
  echo 'batch-size must be 2 or 3; lock-wait/pause-seconds nonnegative seconds' >&2; exit 2;
}
command -v flock >/dev/null
mkdir -p "$HOME/gothic-ai"
pending="$(python3 -I "$ROOT/scripts/nav_drive.py" collect --list-indices "${collector_args[@]}")"
read -r -a indices <<< "$pending"
if [[ ${#indices[@]} == 0 ]]; then
  echo 'All selected episodes are complete.'
  exit 0
fi
for ((first = 0; first < ${#indices[@]}; first += batch_size)); do
  batch=("${indices[@]:first:batch_size}")
  joined="$(IFS=,; echo "${batch[*]}")"
  (
    exec 9>"$HOME/gothic-ai/body-simulation.lock"
    echo "nav-drive: waiting for body-simulation.lock; indices=$joined"
    flock -w "$lock_wait" 9 || { echo 'Ryzen занят' >&2; exit 75; }
    df -h "$HOME"
    python3 -I "$ROOT/scripts/nav_drive.py" collect "${collector_args[@]}" --indices "$joined"
  )
  if ((first + batch_size < ${#indices[@]})); then
    sleep "$pause_seconds"
  fi
done
