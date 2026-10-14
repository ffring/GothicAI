#!/usr/bin/env bash
# Stage 2 CPU-only runner on dev-ubuntu. No game/model processes run on the Mac.
# Usage: scripts/body-headless-remote.sh preflight|sync|build|run|replay|collect|all [run-name]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${GOTHIC_BODY_SSH:-dev-ubuntu}"
REMOTE="gothic-ai/body-headless"
REFERENCE="gothic-ai/body-baseline/runs/melee-reference-stage1-v4"
ACTION="${1:-preflight}"
RUN_NAME="${2:-headless-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$RUN_NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]] || { echo 'Invalid run name' >&2; exit 2; }

remote_script() {
  local remote_args="" arg
  for arg in "$@"; do
    printf -v remote_args '%s %q' "$remote_args" "$arg"
  done
  ssh -o BatchMode=yes "$HOST" "bash -s --$remote_args"
}

preflight() {
  remote_script "$REMOTE" "$REFERENCE" <<'REMOTE_SCRIPT'
set -euo pipefail
task_root="$HOME/$1"
reference="$HOME/$2"
[[ -f "$reference/manifest.json" && -f "$reference/events.jsonl" && \
   -f "$reference/run/save_slot_1.sav" && -x "$reference/reference/Gothic2Notr" ]] || {
  echo 'Stage 1 archived reference is missing' >&2; exit 1;
}
[[ -x "$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0/bin/glslangValidator" ]] || {
  echo 'Stage 1 user-local toolchain is missing' >&2; exit 1;
}
df -h "$HOME/gothic-ai"
free -h
nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu --format=csv,noheader
systemctl --user is-active gothic-voxtral gothic-voxcpm
if pgrep -x 'Gothic2Notr|gothic-body-run' >/dev/null; then
  echo 'Another game/runner is active; wait for its owner' >&2; exit 1
fi
echo "Reference: $reference"
echo "Runner workspace: $task_root"
REMOTE_SCRIPT
}

sync_sources() {
  remote_script "$REMOTE" <<'REMOTE_SCRIPT'
set -euo pipefail
task_root="$HOME/$1"
mkdir -p "$task_root/source/scripts/bench" "$task_root/source/evals/npc-body" \
         "$task_root/provenance" "$task_root/runs"
[[ ! -e "$task_root/build-active" ]] || { echo 'Build owns this source tree' >&2; exit 1; }
if pgrep -x 'Gothic2Notr|gothic-body-run' >/dev/null; then
  echo 'Game/runner is active; do not replace its source/provenance' >&2; exit 1
fi
free_kib=$(df -Pk "$task_root" | awk 'NR==2 {print $4}')
[[ "$free_kib" -ge 5242880 ]] || { echo 'Less than 5 GiB free for headless build' >&2; exit 1; }
REMOTE_SCRIPT
  rsync -a --delete --exclude=build/ --exclude=.git/ --exclude=.DS_Store \
    --exclude='*.o' --exclude='*.a' "$ROOT/OpenGothic/" "$HOST:$REMOTE/source/OpenGothic/"
  rsync -a "$ROOT/scripts/body-headless-remote.sh" "$ROOT/scripts/body_headless_suite.py" \
    "$ROOT/scripts/body_headless_ipc_probe.py" \
    "$ROOT/scripts/body_baseline_report.py" "$ROOT/scripts/body_headless_parity.py" \
    "$HOST:$REMOTE/source/scripts/"
  rsync -a "$ROOT/scripts/bench/body-melee.bench" "$ROOT/scripts/bench/body-headless-senses.bench" \
    "$HOST:$REMOTE/source/scripts/bench/"
  rsync -a --delete --exclude=__pycache__/ "$ROOT/evals/npc-body/" "$HOST:$REMOTE/source/evals/npc-body/"
  local source_head source_patch
  source_head="$(git -C "$ROOT" rev-parse HEAD)"
  source_patch="$(mktemp)"
  git -C "$ROOT" diff --binary HEAD -- OpenGothic > "$source_patch"
  rsync -a "$source_patch" "$HOST:$REMOTE/provenance/source.patch"
  rm "$source_patch"
  remote_script "$REMOTE" "$source_head" <<'REMOTE_SCRIPT'
set -euo pipefail
printf '%s\n' "$2" > "$HOME/$1/provenance/source-head.txt"
REMOTE_SCRIPT
}

build_runner() {
  remote_script "$REMOTE" <<'REMOTE_SCRIPT'
set -euo pipefail
task_root="$HOME/$1"
mkdir -p "$task_root/provenance"
exec 9>"$task_root/build.lock"
flock -n 9 || { echo 'Another build owns this workspace' >&2; exit 1; }
[[ ! -e "$task_root/build-active" ]] || { echo 'Inspect stale/active build marker first' >&2; exit 1; }
touch "$task_root/build-active"
trap 'rm -f "$task_root/build-active"' EXIT
export VULKAN_SDK="$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0"
export PATH="$VULKAN_SDK/bin:$PATH"
nice -n 10 cmake -S "$task_root/source/OpenGothic" -B "$task_root/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DCMAKE_CXX_FLAGS="-I$VULKAN_SDK/include" -DCMAKE_EXE_LINKER_FLAGS="-L$VULKAN_SDK/lib" \
  > "$task_root/provenance/configure.log" 2>&1
nice -n 10 cmake --build "$task_root/build" --target gothic-body-runner Gothic2Notr --parallel 2 \
  > "$task_root/provenance/build.log" 2>&1 || {
  tail -80 "$task_root/provenance/build.log" >&2; exit 1;
}
tail -8 "$task_root/provenance/build.log"
REMOTE_SCRIPT
}

run_suite() {
  remote_script "$REMOTE" "$REFERENCE" "$RUN_NAME" "${1:-full}" <<'REMOTE_SCRIPT'
set -euo pipefail
task_root="$HOME/$1"
reference="$HOME/$2"
run_name="$3"
suite_mode="$4"
suite_args=()
if [[ "$suite_mode" == replay ]]; then
  suite_args+=(--replay-only)
elif [[ "$suite_mode" != full ]]; then
  echo 'Invalid suite mode' >&2; exit 2
fi
binary="$task_root/build/opengothic/gothic-body-runner"
rendered="$task_root/build/opengothic/Gothic2Notr"
out="$task_root/runs/$run_name"
[[ -x "$binary" && -x "$rendered" ]] || { echo 'Build both runner and rendered engine first' >&2; exit 1; }
[[ ! -e "$task_root/build-active" ]] || { echo 'Wait for the build to finish' >&2; exit 1; }
# Every stage 2 invocation takes the same single-simulation lock. Existing baseline
# launchers also have a process check; we never stop processes owned by another job.
exec 9>"$HOME/gothic-ai/body-simulation.lock"
flock -n 9 || { echo 'Another simulation owns the shared runner lock' >&2; exit 1; }
if pgrep -x 'Gothic2Notr|gothic-body-run' >/dev/null; then
  echo 'Another game/runner is active; wait for its owner' >&2; exit 1
fi
[[ ! -e "$out" ]] || { echo 'Run directory exists; choose a fresh name' >&2; exit 1; }
mkdir -p "$out/run" "$out/reference"
# The original assets remain in the stage 1 workspace. No multi-GiB duplicate,
# asset mutation, save overwrite, or renderer/model process is needed here.
assets="$HOME/gothic-ai/body-baseline/assets"
save="$reference/run/save_slot_1.sav"
cp "$binary" "$out/reference/gothic-body-runner"
cp "$rendered" "$out/reference/Gothic2Notr"
cp "$task_root/provenance/source-head.txt" "$task_root/provenance/source.patch" \
   "$task_root/provenance/configure.log" "$task_root/provenance/build.log" "$out/"
nice -n 10 tar -czf "$out/source.tar.gz" -C "$task_root/source" OpenGothic
unit="gothic-body-headless-$run_name"
trap 'systemctl --user stop "$unit.service" >/dev/null 2>&1 || true' INT TERM
set +e
systemd-run --user --wait --pipe --collect --unit="$unit" \
  --property=Nice=10 --property=CPUQuota=200% --property=MemoryMax=12G \
  --property=TasksMax=128 --property=RuntimeMaxSec=1800 \
  --working-directory="$out/run" \
  --setenv="DISPLAY=" --setenv="WAYLAND_DISPLAY=" --setenv="GOTHIC_AI=0" \
  --setenv="VK_DRIVER_FILES=/nonexistent/body-headless-no-vulkan.json" \
  --setenv="VK_ICD_FILENAMES=/nonexistent/body-headless-no-vulkan.json" \
  --setenv="OPENGOTHIC_TEXTURES=" --setenv="OPENGOTHIC_BODY_TRACE=1" \
  --setenv="OPENGOTHIC_BODY_ANIMATION_RNG=isolated" \
  /usr/bin/time -v -o "$out/resources.txt" \
  python3 "$task_root/source/scripts/body_headless_suite.py" \
    --runner "$out/reference/gothic-body-runner" --rendered "$out/reference/Gothic2Notr" \
    --assets "$assets" --save "$save" \
    --reference "$reference" --source "$task_root/source/OpenGothic" \
    --engine-library "$task_root/build/libOpenGothic.a" \
    --config "$task_root/source/evals/npc-body/melee-v1.json" \
    --senses-scenario "$task_root/source/scripts/bench/body-headless-senses.bench" --output "$out" \
    "${suite_args[@]}" \
    > "$out/launcher.log" 2>&1
result=$?
set -e
trap - INT TERM
if [[ "$result" -ne 0 ]]; then
  tail -80 "$out/launcher.log" >&2
  echo "Headless suite failed ($result); evidence remains in $out" >&2
  exit "$result"
fi
echo "Headless suite saved: $out"
REMOTE_SCRIPT
}

collect_suite() {
  local local_out="$ROOT/data/npc-body/headless/$RUN_NAME"
  mkdir -p "$local_out"
  rsync -a --exclude=run/ --exclude=reference/ --exclude=source.tar.gz \
    "$HOST:$REMOTE/runs/$RUN_NAME/" "$local_out/"
  echo "Collected headless evidence: $local_out"
}

case "$ACTION" in
  preflight) preflight ;;
  sync) sync_sources ;;
  build) build_runner ;;
  run) run_suite ;;
  replay) run_suite replay ;;
  collect) collect_suite ;;
  all) preflight; sync_sources; build_runner; run_suite; collect_suite ;;
  *) echo 'Usage: body-headless-remote.sh preflight|sync|build|run|replay|collect|all [run-name]' >&2; exit 2 ;;
esac
