#!/usr/bin/env bash
# Submit one teacher-v3b build phase and return its queue ID without waiting.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
stage="${1:?Usage: nav-teacher-v3b-remote.sh build}"
[[ "$stage" == build && $# == 1 ]] || { echo "Expected one build phase" >&2; exit 2; }
export GQ_OWNER=nav-teacher-v3b
task="$(ssh -o ConnectTimeout=10 ryzen 'printf "%s/gothic-ai/nav-teacher-v3b" "$HOME"')"
results=/mnt/shared/gothic-ai/nav-teacher-v3b
ssh ryzen "TASK='$task' RESULTS='$results' bash -s" <<'PREPARE'
set -euo pipefail
df -h "$HOME" /mnt/shared
python3 -c 'import pathlib,shutil; assert shutil.disk_usage(pathlib.Path.home()).free >= 20*1024**3, "Less than 20 GiB free"'
mkdir -p "$TASK/source" "$RESULTS/input"
# Existing experiment inputs are read-only; freeze ordinary copies on storage.
for name in expanded-scenarios.jsonl detour-val.jsonl fixtures.json save.sav accepted-links.txt places.json familiar.bin; do
  if [[ ! -f "$RESULTS/input/$name" ]]; then
    cp "$HOME/gothic-ai/nav-memory-v4/input/$name" "$RESULTS/input/$name"
  fi
done
if [[ ! -f "$RESULTS/input/detour-test.jsonl" && -f "$HOME/gothic-ai/nav-dagger3/detours/detour-test.jsonl" ]]; then
  cp "$HOME/gothic-ai/nav-dagger3/detours/detour-test.jsonl" "$RESULTS/input/detour-test.jsonl"
fi
python3 - "$RESULTS/input" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
for name in ("detour-val.jsonl", "detour-test.jsonl"):
    path = root / name
    if not path.exists() and name == "detour-test.jsonl":
        continue
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert rows and (name != "detour-val.jsonl" or len(rows) == 121), (name, len(rows))
    assert len({row["id"] for row in rows}) == len(rows), name
    assert all(row["goal_kind"] in ("point", "object") and isinstance(row["seed"], int) for row in rows), name
    print(f"Frozen {name}: {len(rows)} cases")
PY
PREPARE
rsync -a --exclude=build --exclude='build-*' --exclude=__pycache__ \
  "$ROOT/OpenGothic" "$ROOT/scripts" "ryzen:$task/source/"
ssh ryzen "cat > '$task/phase.sh'" <<'PHASE'
#!/usr/bin/env bash
set -euo pipefail
task="$(cd "$(dirname "$0")" && pwd)"
results=/mnt/shared/gothic-ai/nav-teacher-v3b
exec > >(tee "$results/build-$(date -u +%Y%m%dT%H%M%SZ).log") 2>&1
df -h "$HOME" /mnt/shared
python3 -c 'import pathlib,shutil; assert shutil.disk_usage(pathlib.Path.home()).free >= 20*1024**3, "Less than 20 GiB free"'
export VULKAN_SDK="$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0"
export PATH="$HOME/.local/bin:$VULKAN_SDK/bin:$PATH"
export UV_CACHE_DIR="$HOME/.cache/uv"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 OPENGOTHIC_BODY_TRACE=0
cd "$task/source"
if [[ ! -x "$task/.venv/bin/python" ]]; then uv venv "$task/.venv"; fi
uv pip install --python "$task/.venv/bin/python" numpy pytest
cmake -S "$task/source/OpenGothic" -B "$task/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DOPENGOTHIC_JOLT_X86=x86-64-v3 \
  -DCMAKE_CXX_FLAGS="-I$VULKAN_SDK/include" -DCMAKE_EXE_LINKER_FLAGS="-L$VULKAN_SDK/lib"
cmake --build "$task/build" --target gothic-body-runner \
  check-navigation-memory check-navmemoryteacher --parallel 3
"$task/.venv/bin/python" -m pytest -q -o cache_dir="$results/pytest-cache" \
  --basetemp="$results/pytest-tmp" scripts/tests/test_nav_memory_v4.py scripts/tests/test_nav_teacher_v3.py
PHASE
"$ROOT/scripts/gq" submit --class batch --threads 12 --mem 8G --max 2h --cpu-quota 1200% \
  --name "nav teacher v3b build" -- bash "$task/phase.sh"
