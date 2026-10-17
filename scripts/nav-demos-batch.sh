#!/usr/bin/env bash
# Short Ryzen collection batches sharing the combat session's simulation lock.
# Example (absolute paths):
# scripts/nav-demos-batch.sh collect --runner /.../gothic-body-runner \
#   --assets /.../assets --save /.../save.sav --cache /.../navmesh-cache --resume
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ "$(uname -s)" == Linux ]] || { echo 'Run nav-demos-batch.sh on Ryzen/Linux' >&2; exit 2; }
[[ $# -gt 0 ]] || { python3 "$ROOT/scripts/collect_nav_demos.py" --help; exit 2; }
mode="$1"
shift
batch_size="${NAV_DEMOS_BATCH_SIZE:-2}"
lock_wait="${RYZEN_LOCK_WAIT:-900}"
pause_seconds="${NAV_DEMOS_PAUSE_SECONDS:-1}"
parallel=1
collector_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --batch-size) batch_size="$2"; shift 2 ;;
    --lock-wait) lock_wait="$2"; shift 2 ;;
    --pause-seconds) pause_seconds="$2"; shift 2 ;;
    --parallel) parallel="$2"; collector_args+=("$1" "$2"); shift 2 ;;
    *) collector_args+=("$1"); shift ;;
  esac
done
[[ "$batch_size" =~ ^[1-9][0-9]*$ && "$lock_wait" =~ ^[0-9]+$ && "$pause_seconds" =~ ^[0-9]+$ ]] || {
  echo 'batch-size must be positive; lock-wait and pause-seconds must be nonnegative seconds' >&2; exit 2;
}
[[ "$parallel" == 1 || "$mode" == performance ]] || {
  echo 'Concurrent runners are reserved for short performance measurements' >&2; exit 2;
}
command -v flock >/dev/null
mkdir -p "$HOME/gothic-ai"
# Resolve all selection before taking the lock; replay keeps original pair indices.
pending="$(python3 "$ROOT/scripts/collect_nav_demos.py" "$mode" --list-indices "${collector_args[@]}")"
read -r -a indices <<< "$pending"
if [[ ${#indices[@]} == 0 ]]; then
  echo 'All selected episodes are already complete.'
  exit 0
fi
if [[ "$mode" == performance ]]; then
  # A single bounded measurement holds the lock for the parallel 10-second spans.
  batch_size="${#indices[@]}"
fi
for ((first = 0; first < ${#indices[@]}; first += batch_size)); do
  batch=("${indices[@]:first:batch_size}")
  joined="$(IFS=,; echo "${batch[*]}")"
  # A subshell releases fd9 even if collection fails. No other session's processes
  # or directories are inspected or changed.
  (
    exec 9>"$HOME/gothic-ai/body-simulation.lock"
    echo "nav-demos: waiting for body-simulation.lock; $mode indices=$joined"
    flock -w "$lock_wait" 9 || { echo 'Ryzen занят' >&2; exit 75; }
    df -h "$HOME"
    python3 -I "$ROOT/scripts/collect_nav_demos.py" "$mode" "${collector_args[@]}" --indices "$joined"
  )
  # Give the waiting combat session a chance to acquire the common lock.
  if ((first + batch_size < ${#indices[@]})); then
    sleep "$pause_seconds"
  fi
done
