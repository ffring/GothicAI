#!/usr/bin/env bash
# Submit only the geometry preparation phase, then return its gq ID.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ "${1:-}" == prepare-detours && $# == 1 ]] || {
  echo 'Usage: nav-dagger3-remote.sh prepare-detours' >&2; exit 2;
}
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
"$ROOT/scripts/gq" submit --class batch --threads 12 --cpu-quota 1200% --mem 8G --max 2h \
  --name "nav dagger3 prepare detours" -- bash "$task/prepare-detours.sh"
