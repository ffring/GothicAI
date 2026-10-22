#!/usr/bin/env bash
# Submit one bounded phase, then return its gq ID without waiting.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
stage="${1:-}"
[[ $# == 1 ]] || { echo 'Usage: nav-dagger3-remote.sh prepare-detours|baseline-detours' >&2; exit 2; }
case "$stage" in prepare-detours|baseline-detours) ;; *) echo "Unknown phase: $stage" >&2; exit 2 ;; esac
export GQ_OWNER=nav-dagger3
task="$(ssh -o ConnectTimeout=10 ryzen 'printf "%s/gothic-ai/nav-dagger3" "$HOME"')"
ssh ryzen 'df -h "$HOME"'
ssh ryzen "mkdir -p '$task/source' '$task/logs'"
rsync -a --exclude=build --exclude='build-*' --exclude=__pycache__ --exclude=.git \
  "$ROOT/OpenGothic" "$ROOT/scripts" "$ROOT/AGENTS.md" "ryzen:$task/source/"
ssh ryzen "cat > '$task/prepare-detours.sh'" <<'PHASE'
#!/usr/bin/env bash
set -euo pipefail
task="$(cd "$(dirname "$0")" && pwd)"
exec > >(tee -a "$task/logs/prepare-detours.log") 2>&1
export VULKAN_SDK="$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0"
export PATH="$HOME/.local/bin:$VULKAN_SDK/bin:$PATH"
export UV_CACHE_DIR="$HOME/.cache/uv"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 OPENGOTHIC_BODY_TRACE=0
df -h "$HOME"
python3 - "$task" <<'SPACE'
import shutil
import sys
free = shutil.disk_usage(sys.argv[1]).free
if free < 26 * 1024**3:
    raise SystemExit(f"Insufficient build space above 20-GiB reserve: {free} bytes free")
SPACE
cd "$task/source"
if [[ ! -x "$task/.venv/bin/python" ]]; then uv venv "$task/.venv"; fi
uv pip install --python "$task/.venv/bin/python" numpy pytest
"$task/.venv/bin/python" -m pytest -q scripts/tests/test_nav_dagger3_detours.py
cmake -S "$task/source/OpenGothic" -B "$task/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DOPENGOTHIC_JOLT_X86=x86-64-v3 \
  -DCMAKE_CXX_FLAGS="-I$VULKAN_SDK/include" -DCMAKE_EXE_LINKER_FLAGS="-L$VULKAN_SDK/lib"
cmake --build "$task/build" --target Gothic2Notr gothic-body-runner check-navmesh --parallel 3
"$task/.venv/bin/python" -I "$task/source/scripts/nav_dagger3_detours.py" \
  --task "$task" --corpus "$HOME/gothic-ai/nav-demos-v4"
df -h "$HOME"
PHASE
if [[ "$stage" == prepare-detours ]]; then
  "$ROOT/scripts/gq" submit --class batch --threads 12 --cpu-quota 1200% --mem 8G --max 2h \
    --name "nav dagger3 prepare detours" -- bash "$task/prepare-detours.sh"
  exit
fi
# Freezing is a small transformation of existing geometry, with no runner,
# training or policy call. Both cohorts are immutable before queue submission.
ssh ryzen "'$task/.venv/bin/python' -I '$task/source/scripts/nav_dagger3_detours.py' freeze --task '$task' --corpus '${task%/nav-dagger3}/nav-demos-v4'"
ssh ryzen "cat > '$task/baseline-detours.sh'" <<'BASELINE'
#!/usr/bin/env bash
set -euo pipefail
task="$(cd "$(dirname "$0")" && pwd)"
corpus="$HOME/gothic-ai/nav-demos-v4"
exec > >(tee -a "$task/logs/baseline-detours.log") 2>&1
export UV_CACHE_DIR="$HOME/.cache/uv"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENGOTHIC_BODY_TRACE=0 PYTHONUNBUFFERED=1
python="$task/.venv/bin/python"
cd "$task/source"
"$python" -m pytest -q scripts/tests/test_nav_dagger3_detours.py scripts/tests/test_nav_rl.py
for policy in bc3 it35; do
  "$python" -I -c 'import runpy,sys; runpy.run_path(sys.argv[1])["require_room"](sys.argv[2])' \
    "$task/source/scripts/nav_dagger3_detours.py" "$task"
  case "$policy" in
    bc3) weights="$HOME/gothic-ai/nav-bc3/run/it35/policy.navbc" ;;
    it35) weights="$HOME/gothic-ai/nav-ppo/run/iter-000035/update/policy.navbc" ;;
  esac
  output="$task/detour-baseline/$policy"
  cache="$task/baseline-cache/$policy"
  mkdir -p "$cache/ordinary"
  cp -n "$corpus/recording-cache/v3/"*.navmesh "$cache/ordinary/"
  sha256sum "$weights" "$task/detours/detour-val.jsonl" "$task/detours/detour-test.jsonl"
  "$python" -I scripts/nav_rl.py drive \
    --detour-scenarios "$task/detours/detour-val.jsonl" \
    --runner "$task/build/opengothic/gothic-body-runner" \
    --assets "$HOME/gothic-ai/game" --save "$corpus/input/save.sav" \
    --cache "$cache" --links "$corpus/input/accepted-links.txt" --roads "$corpus/input/places.json" \
    --weights "$weights" --output "$output" --physics cv --parallel 8 \
    --action-mode sample --action-seeds 18102027,18102028,18102029 \
    --limit-ms 240000 --watchdog-seconds 900 --resume
  "$python" -I scripts/nav_rl.py summarize --source "$output" --output "$output"
  "$python" - "$task/detours/detour-val.jsonl" "$output/metrics.json" <<'CHECK'
import json
from pathlib import Path
import sys
cases = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
metrics = json.loads(Path(sys.argv[2]).read_text())
expected = {(case["id"], seed) for case in cases for seed in (18102027, 18102028, 18102029)}
episodes = metrics["episodes"]
if len(episodes) != len(expected) or {(ep["id"], ep["action_seed"]) for ep in episodes} != expected:
    raise ValueError("Baseline detour validation is incomplete")
if any(ep["teacher_control_decisions"] != 0 or ep["recording"] != "text_only" for ep in episodes):
    raise ValueError("Baseline control or recording differs")
print(json.dumps(dict(event="baseline_policy_complete", episodes=len(episodes), **metrics["all"])))
CHECK
done
df -h "$HOME"
BASELINE
"$ROOT/scripts/gq" submit --class batch --threads 24 --cpu-quota 2400% --mem 12G --max 2h \
  --name "nav dagger3 bc3 it35 detour baseline" -- bash "$task/baseline-detours.sh"
