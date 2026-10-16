#!/usr/bin/env bash
# Run the current worktree on Ryzen. No system packages or training files are changed.
# Usage: scripts/ryzen.sh setup|e2e|pytest [pytest args...]|clean
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK_WAIT="${RYZEN_LOCK_WAIT:-900}"
[[ "$LOCK_WAIT" =~ ^[0-9]+$ ]] || { echo 'RYZEN_LOCK_WAIT must be seconds' >&2; exit 2; }

toolchain() {
  export VULKAN_SDK="$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0"
  export PATH="$HOME/.local/bin:$VULKAN_SDK/bin:$PATH"
  local tool
  for tool in uv cmake ninja gcc g++ glslangValidator rsync flock python3; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
  done
  # Check BEFORE configure: a failed shader compiler lookup can remove build.ninja.
  [[ -r "$VULKAN_SDK/include/vulkan/vulkan.h" && -r "$VULKAN_SDK/lib/libvulkan.so" ]] || {
    echo "Incomplete user Vulkan SDK: $VULKAN_SDK" >&2; exit 1;
  }
}

take_lock() {
  mkdir -p "$HOME/gothic-ai"
  exec 9>"$HOME/gothic-ai/body-simulation.lock"
  local started=$SECONDS monitor waiter status
  echo "Ryzen: waiting for body-simulation.lock (up to ${LOCK_WAIT}s)"
  # Without a PTY ssh disconnects do not send SIGHUP. EOF cancels our own waiter,
  # so an interrupted Mac command cannot later acquire the lock unattended.
  (IFS= read -r unused || kill -TERM "$$") <&0 &
  monitor=$!
  flock -w "$LOCK_WAIT" 9 &
  waiter=$!
  trap 'kill "$monitor" "$waiter" 2>/dev/null || true; exit 143' HUP INT TERM
  wait "$waiter" && status=0 || status=$?
  kill "$monitor" 2>/dev/null || true
  wait "$monitor" 2>/dev/null || true
  trap - HUP INT TERM
  [[ "$status" == 0 ]] || { echo 'Ryzen занят' >&2; exit 75; }
  echo "Ryzen: lock acquired in $((SECONDS - started))s"
}

desktop() {
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  local auth socket
  command -v xdpyinfo >/dev/null || { echo 'Need xdpyinfo to find the Xwayland display' >&2; exit 1; }
  # Connecting also wakes GNOME's on-demand Xwayland. Never use xhost or change the session.
  for auth in "$XDG_RUNTIME_DIR"/.mutter-Xwaylandauth.* "$HOME/.Xauthority"; do
    [[ -r "$auth" ]] || continue
    for socket in /tmp/.X11-unix/X*; do
      [[ -S "$socket" ]] || continue
      if DISPLAY=":${socket##*/X}" XAUTHORITY="$auth" xdpyinfo >/dev/null 2>&1; then
        export DISPLAY=":${socket##*/X}" XAUTHORITY="$auth"
        export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/nvidia_icd.json
        export VK_ICD_FILENAMES="$VK_DRIVER_FILES"
        [[ -r "$VK_DRIVER_FILES" ]] || { echo 'Missing NVIDIA Vulkan ICD' >&2; exit 1; }
        export ALSOFT_DRIVERS=null TEMPEST_BACKGROUND=1
        echo "Ryzen: Xwayland $DISPLAY, NVIDIA Vulkan"
        return
      fi
    done
  done
  echo 'No accessible Xwayland display. Need a running user desktop or a GPU-capable headless compositor.' >&2
  exit 1
}

source_digest() {
  # Content and paths include additions/deletions and uncommitted files, not just HEAD.
  python3 - "$ROOT/OpenGothic" <<'PY'
import hashlib
import sys
from pathlib import Path
root = Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(root.rglob('*')):
    if 'build' in path.relative_to(root).parts:
        continue
    if path.is_file():
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
print(digest.hexdigest())
PY
}

build_storygraph() {
  local state="$1" digest="$2" started=$SECONDS
  local build="$state/storygraph-build"
  if [[ ! -x "$build/storygraph" || ! -f "$build/source.sha256" ]] || \
     [[ "$(cat "$build/source.sha256")" != "$digest" ]]; then
    mkdir -p "$build"
    cmake -S "$ROOT/OpenGothic/ai_bridge/storygraph" -B "$build" -G Ninja \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
      >"$build/configure.log" 2>&1 || { cat "$build/configure.log"; return 1; }
    cmake --build "$build" --target storygraph --parallel "${RYZEN_JOBS:-12}" \
      >"$build/build.log" 2>&1 || { tail -80 "$build/build.log"; return 1; }
    tail -3 "$build/build.log"
    printf '%s\n' "$digest" >"$build/source.sha256"
    echo "Ryzen: storygraph configure + build $((SECONDS - started))s"
  fi
  # Both the server and its native-tool tests look for this conventional path.
  mkdir -p "$ROOT/OpenGothic/ai_bridge/storygraph/build"
  ln -sfn "$build/storygraph" "$ROOT/OpenGothic/ai_bridge/storygraph/build/storygraph"
}

build_engine() {
  local state="$1" digest="$2" started=$SECONDS
  if [[ -x "$state/build/opengothic/Gothic2Notr" && -f "$state/build/source.sha256" ]] && \
     [[ "$(cat "$state/build/source.sha256")" == "$digest" ]]; then
    echo "Ryzen: engine unchanged, build skipped ($((SECONDS - started))s)"
    return
  fi
  local jobs="${RYZEN_JOBS:-12}"
  [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo 'RYZEN_JOBS must be positive' >&2; exit 2; }
  mkdir -p "$state/build"
  # Reconfigure on tree changes: OpenGothic's source globs need this for new .cpp files.
  cmake -S "$ROOT/OpenGothic" -B "$state/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_CXX_FLAGS="-I$VULKAN_SDK/include" -DCMAKE_EXE_LINKER_FLAGS="-L$VULKAN_SDK/lib" \
    >"$state/build/configure.log" 2>&1 || { cat "$state/build/configure.log"; return 1; }
  cmake --build "$state/build" --target Gothic2Notr --parallel "$jobs" \
    >"$state/build/build.log" 2>&1 || { tail -80 "$state/build/build.log"; return 1; }
  tail -5 "$state/build/build.log"
  printf '%s\n' "$digest" >"$state/build/source.sha256"
  echo "Ryzen: engine configure + build $((SECONDS - started))s"
}

remote_run() {
  local action="$1" state="$2" run="$3"
  shift 3
  toolchain
  export GOTHIC_DIR="$HOME/gothic-ai/game"
  local started=$SECONDS rc digest
  [[ "${RYZEN_JOBS:-12}" =~ ^[1-9][0-9]*$ ]] || { echo 'RYZEN_JOBS must be positive' >&2; return 2; }
  digest="$(source_digest)"
  if [[ "$action" == pytest ]]; then
    build_storygraph "$state" "$digest"
    cd "$ROOT/server"
    uv run pytest "$@" && rc=0 || rc=$?
    echo "Ryzen: pytest $((SECONDS - started))s, exit $rc"
    return "$rc"
  fi
  export LOG_DIR="$state/runs/$run"
  mkdir -p "$LOG_DIR"
  [[ -d "$GOTHIC_DIR/Data" ]] || { echo 'Run scripts/ryzen.sh setup first' >&2; return 1; }
  # This version is compiled into the only binary we ever launch, from this snapshot.
  python3 - "$ROOT" <<'PY'
import re
import sys
from pathlib import Path
root = Path(sys.argv[1])
server = int(re.search(r'^PROTOCOL_VERSION\s*=\s*(\d+)',
                      (root / 'server/src/gothic_ai/protocol.py').read_text(), re.M)[1])
engine = int(re.search(r'\bProtocolVersion\s*=\s*(\d+)\s*;',
                      (root / 'OpenGothic/ai_bridge/aibridge.cpp').read_text())[1])
if server != engine:
    raise SystemExit(f'Protocol mismatch: server={server}, engine={engine}')
print(f'Ryzen: protocol {engine} (engine = server)', flush=True)
PY
  build_engine "$state" "$digest"
  build_storygraph "$state" "$digest"
  started=$SECONDS
  desktop
  # Resolve dependencies under the same lock, before the e2e server's two-second startup wait.
  (cd "$ROOT/server" && uv sync --frozen)
  case "${SCENARIO:-m0}" in
    tts|gossip|day|group|interject|programs) (cd "$ROOT/tts-service" && uv sync --frozen) ;;
  esac
  case "${SCENARIO:-m0}" in
    m3|m5e) (cd "$ROOT/asr-service" && uv sync --frozen) ;;
  esac
  export RYZEN_GAME_BIN="$state/build/opengothic/Gothic2Notr"
  # Windowed on the logged-in desktop; never take it fullscreen or use a software display.
  cat >"$LOG_DIR/game.sh" <<'WRAPPER'
#!/usr/bin/env bash
exec "$RYZEN_GAME_BIN" "$@" -g2 -window
WRAPPER
  chmod +x "$LOG_DIR/game.sh"
  export GAME_BIN="$LOG_DIR/game.sh"
  echo "Ryzen: prepare/start overhead $((SECONDS - started))s"
  started=$SECONDS
  bash "$ROOT/scripts/e2e.sh" && rc=0 || rc=$?
  echo "Ryzen: e2e $((SECONDS - started))s, exit $rc"
  grep -E 'GPU = ' "$LOG_DIR/game.log" || true
  return "$rc"
}

# Internal entry point inherits fd 9 from the lock-holding SSH session.
if [[ "${1:-}" == __remote ]]; then
  shift
  remote_run "$@"
  exit $?
fi

ACTION="${1:-}"
case "$ACTION" in setup|e2e|pytest|clean) shift ;; *) echo 'Usage: scripts/ryzen.sh setup|e2e|pytest [args...]|clean' >&2; exit 2 ;; esac
[[ "$ACTION" == pytest || $# == 0 ]] || { echo 'Only pytest accepts arguments' >&2; exit 2; }

remote() {
  local command='' arg
  for arg in "$@"; do printf -v command '%s %q' "$command" "$arg"; done
  ssh -o BatchMode=yes -o ConnectTimeout=10 ryzen "$command"
}

# Hold one SSH session open across upload and execution. This protects the snapshot
# even from concurrent invocations in the same worktree (including clean/setup).
SESSION="$(mktemp -d "${TMPDIR:-/tmp}/gothic-ryzen-control.XXXXXX")"
SSH_PID=''
cleanup() {
  exec 8>&- || true
  if [[ -n "$SSH_PID" ]]; then kill "$SSH_PID" 2>/dev/null || true; wait "$SSH_PID" 2>/dev/null || true; fi
  rm -rf -- "$SESSION"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkfifo "$SESSION/input"
exec 8<>"$SESSION/input"
OUTPUT="$SESSION/output"
if [[ "$ACTION" == e2e ]]; then
  LOG_DIR="${LOG_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/gothic-ryzen.XXXXXX")}"
  mkdir -p "$LOG_DIR"
  LOG_DIR="$(cd "$LOG_DIR" && pwd)"
  OUTPUT="$LOG_DIR/launcher.log"
fi
bootstrap="$(declare -f take_lock)
set -euo pipefail
LOCK_WAIT=$LOCK_WAIT
take_lock
exec bash -se"
printf -v bootstrap_command 'bash -c %q' "$bootstrap"
: >"$OUTPUT"
ssh -o BatchMode=yes -o ConnectTimeout=10 ryzen "$bootstrap_command" \
  8>&- <"$SESSION/input" > >(exec 8>&-; tee "$OUTPUT") 2>&1 &
SSH_PID=$!
until [[ -f "$OUTPUT" ]] && grep -q '^Ryzen: lock acquired ' "$OUTPUT"; do
  if ! kill -0 "$SSH_PID" 2>/dev/null; then
    wait "$SSH_PID" && rc=0 || rc=$?
    SSH_PID=''
    exit "$rc"
  fi
  sleep 0.1
done

run_locked() {
  local arg
  printf 'exec' >&8
  for arg in "$@"; do printf ' %q' "$arg" >&8; done
  printf '\n' >&8
  exec 8>&-
  wait "$SSH_PID" && rc=0 || rc=$?
  SSH_PID=''
  return "$rc"
}

if [[ "$ACTION" == setup ]]; then
  remote bash -s <<'REMOTE'
set -euo pipefail
mkdir -p "$HOME/gothic-ai/game"
REMOTE
  # Run the same preflight without needing a checkout or configuring a build.
  { declare -f toolchain; echo 'set -euo pipefail; toolchain; echo "Ryzen: toolchain OK"'; } | remote bash -s
  GOTHIC_DIR="${GOTHIC_DIR:-/Volumes/DevSSD/Dev/games/Gothic II}"
  [[ -d "$GOTHIC_DIR/Data" ]] || { echo "No game Data directory: $GOTHIC_DIR" >&2; exit 1; }
  started=$SECONDS
  rsync -a --exclude=.DS_Store "$GOTHIC_DIR/" ryzen:gothic-ai/game/
  echo "Ryzen: game data ready, sync $((SECONDS - started))s"
  run_locked true
  exit
fi

# Codex worktrees are named .../worktrees/<name>/<repo>; ordinary worktrees use their basename.
WORKTREE="$(basename "$ROOT")"
if [[ "$ROOT" == */worktrees/*/* ]]; then WORKTREE="$(basename "$(dirname "$ROOT")")"; fi
[[ "$WORKTREE" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || { echo 'Unsafe worktree name' >&2; exit 2; }
REMOTE="gothic-ai/e2e/$WORKTREE"
REMOTE_HOME="$(remote printenv HOME)"
STATE="$REMOTE_HOME/$REMOTE"
if [[ "$ACTION" == clean ]]; then
  run_locked rm -rf -- "$STATE"
  echo "Ryzen: removed $REMOTE"
  exit
fi

RUN="$(date -u +%Y%m%dT%H%M%SZ)-$$"
remote mkdir -p "$STATE/source" "$STATE/inputs/$RUN"
started=$SECONDS
rsync -a --checksum --delete \
  --exclude=.git --exclude=build/ --exclude='build-*/' --exclude=cmake-build-\*/ \
  --exclude=.venv/ --exclude=__pycache__/ --exclude=.pytest_cache/ --exclude=.ruff_cache/ --exclude=.mypy_cache/ \
  --exclude=.claude/ --exclude=.codex/ --exclude=.DS_Store --exclude=node_modules/ \
  --exclude='.env*' --exclude=secrets/ --exclude='*.local.toml' --exclude='*.sqlite*' --exclude='*.log' \
  --exclude='*.sav' --exclude='*.vdf' --exclude='*.VDF' --exclude='*.o' --exclude='*.a' \
  --exclude=/.bench/ --exclude=/.shorts/ --exclude=/.ryzen/ --exclude=/game/ --exclude=/data/ \
  --exclude=/tts-samples/ --exclude=/site/_build/ \
  "$ROOT/" "ryzen:$REMOTE/source/"

ENV_ARGS=("RYZEN_LOCK_WAIT=$LOCK_WAIT" "RYZEN_JOBS=${RYZEN_JOBS:-12}")
# Carry e2e options, including scenario-specific GOTHIC_* settings; paths are remapped below.
while IFS= read -r name; do
  case "$name" in
    GOTHIC_DIR|GOTHIC_AI_URL) ;;
    SCENARIO|LLM|ASR|TTS|TIMEOUT|PORT|ASR_PORT|TTS_PORT|DECIDER_URL|QUICK_URL|GOTHIC_*|OPENGOTHIC_*|CHATGPT_*|OPENAI_*)
      ENV_ARGS+=("$name=${!name}") ;;
  esac
done < <(compgen -e)
for name in SAVE VOICE_FILE; do
  if [[ -n "${!name:-}" ]]; then
    [[ -f "${!name}" ]] || { echo "Missing $name: ${!name}" >&2; exit 2; }
    rsync -a -- "${!name}" "ryzen:$REMOTE/inputs/$RUN/$name"
    ENV_ARGS+=("$name=$STATE/inputs/$RUN/$name")
  fi
done
echo "Ryzen: upload $((SECONDS - started))s ($WORKTREE)"

if [[ "$ACTION" == e2e ]]; then
  run_locked env "${ENV_ARGS[@]}" bash "$STATE/source/scripts/ryzen.sh" __remote e2e "$STATE" "$RUN" && rc=0 || rc=$?
  # Collect after failures too. A lock timeout has no remote run directory yet.
  if remote test -d "$STATE/runs/$RUN"; then
    rsync -a "ryzen:$REMOTE/runs/$RUN/" "$LOG_DIR/" || echo 'Could not collect all Ryzen logs' >&2
  fi
  echo "logs: $LOG_DIR"
  exit "$rc"
fi
run_locked env "${ENV_ARGS[@]}" bash "$STATE/source/scripts/ryzen.sh" __remote pytest "$STATE" "$RUN" "$@"
