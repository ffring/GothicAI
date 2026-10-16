#!/usr/bin/env bash
# Execute on Ryzen from this checkout, serially under the shared simulation lock.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
: "${RUNNER:?Set RUNNER to the built gothic-body-runner}"
: "${SAVE:?Set SAVE to the chapter-one save_slot_1.sav}"
: "${GOTHIC_DIR:?Set GOTHIC_DIR to the game assets}"
: "${OUT:?Set OUT to an absolute fresh output directory}"
[[ "$RUNNER" == /* && "$SAVE" == /* && "$GOTHIC_DIR" == /* && "$OUT" == /* ]]
exec 9>"$HOME/gothic-ai/body-simulation.lock"
flock -w "${RYZEN_LOCK_WAIT:-900}" 9 || { echo 'Ryzen занят' >&2; exit 75; }
df -h "$HOME"
exec python3 "$ROOT/scripts/navmesh_measure.py" run \
  --runner "$RUNNER" --save "$SAVE" --assets "$GOTHIC_DIR" --output "$OUT" --cache "${CACHE:-$OUT/cache}" "$@"
