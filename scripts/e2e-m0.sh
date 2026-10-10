#!/usr/bin/env bash
# End-to-end M0 check: starts the AI server with the m0 scenario, then a new game in OpenGothic.
# The scenario takes Xardas over, walks him around and returns him to his routine.
# A new game first plays the intro video (~80 s), so the whole run takes ~2 min.
#
#   GOTHIC_DIR=/path/to/Gothic\ II scripts/e2e-m0.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GOTHIC_DIR="${GOTHIC_DIR:-/Volumes/shared/projects/Gothic II}"
GAME_BIN="${GAME_BIN:-$ROOT/OpenGothic/build/opengothic/Gothic2Notr}"
LOG_DIR="${LOG_DIR:-$(mktemp -d -t gothic-e2e)}"
TIMEOUT="${TIMEOUT:-240}"

(cd "$ROOT/server" && exec uv run gothic-ai serve --scenario m0 --exit-after --no-console) >"$LOG_DIR/server.log" 2>&1 &
SRV=$!
GAME=""
# `|| true`: with set -e a failing kill of an already finished process would change the exit code
trap 'if [ -n "$GAME" ]; then kill "$GAME" 2>/dev/null || true; fi; kill "$SRV" 2>/dev/null || true' EXIT

sleep 2
if ! kill -0 "$SRV" 2>/dev/null; then
  cat "$LOG_DIR/server.log"
  exit 1
fi

(cd "$LOG_DIR" && exec "$GAME_BIN" -g "$GOTHIC_DIR" -nomenu -devmode) >"$LOG_DIR/game.log" 2>&1 &
GAME=$!

for _ in $(seq 1 "$TIMEOUT"); do
  kill -0 "$SRV" 2>/dev/null || break
  if ! kill -0 "$GAME" 2>/dev/null; then
    echo "game exited unexpectedly, see $LOG_DIR/game.log"
    exit 1
  fi
  sleep 1
done

if kill -0 "$SRV" 2>/dev/null; then
  echo "timeout after ${TIMEOUT}s, logs in $LOG_DIR"
  exit 1
fi
wait "$SRV" && rc=0 || rc=$?

grep -E "scenarios|attached|detached|ERROR" "$LOG_DIR/server.log" || true
grep -E "^\[ai\]" "$LOG_DIR/game.log" || true
echo "logs: $LOG_DIR"
exit "$rc"
