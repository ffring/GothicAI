#!/usr/bin/env bash
# Submit one phase and return its queue ID without waiting or chaining jobs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ $# == 1 && ( "$1" == --help || "$1" == -h ) ]]; then
  echo 'Usage: bash scripts/nav-bc4-remote.sh smoke|train'
  exit 0
fi
[[ $# == 1 ]] || { echo 'Usage: bash scripts/nav-bc4-remote.sh smoke|train' >&2; exit 2; }
stage="$1"
case "$stage" in smoke|train) ;; *) echo "Unknown phase: $stage" >&2; exit 2 ;; esac
export GQ_OWNER=nav-bc4
task="$(ssh -o ConnectTimeout=10 ryzen 'printf "%s/gothic-ai/nav-bc4" "$HOME"')"
archive=/mnt/shared/gothic-ai/nav-bc4
ssh ryzen "bash -s -- '$task' '$archive'" <<'INPUTS'
set -euo pipefail
task="$1"; archive="$2"
df -h "$HOME" /mnt/shared
python3 -c 'import pathlib,shutil; assert shutil.disk_usage(pathlib.Path.home()).free >= 22*1024**3, "Less than 22 GiB free"'
mkdir -p "$task/source" "$task/input/hd" "$archive/logs"
# Other experiments and v5 are read-only input sources.
copy_input() { test -f "$2" || cp "$1" "$2"; }
for name in last.pt policy.navbc; do
  copy_input "$HOME/gothic-ai/nav-dagger3/hd-run/$name" "$task/input/hd/$name"
done
for name in save.sav places.json accepted-links.txt familiar.bin; do
  copy_input "$HOME/gothic-ai/nav-demos-v5/input/$name" "$task/input/$name"
done
INPUTS
rsync -a --no-links --exclude=build --exclude='build-*' --exclude=__pycache__ --exclude=.git \
  "$ROOT/OpenGothic" "$ROOT/scripts" "$ROOT/AGENTS.md" "ryzen:$task/source/"
rsync -rt --no-links "$ROOT/reports/navigation/2026-10-22-demos-v5/manifest.json" \
  "ryzen:$task/input/v5-compact.json"
ssh ryzen "cat > '$task/phase.sh'" <<'PHASE'
#!/usr/bin/env bash
set -euo pipefail
task="$(cd "$(dirname "$0")" && pwd)"
archive=/mnt/shared/gothic-ai/nav-bc4
corpus=/mnt/shared/gothic-ai/nav-demos-v5/recordings
stage="${1:?phase}"
exec > >(tee -a "$archive/logs/$stage.log") 2>&1
export VULKAN_SDK="$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0"
export PATH="$HOME/.local/bin:$VULKAN_SDK/bin:$PATH"
export UV_CACHE_DIR="$HOME/.cache/uv"
export OMP_NUM_THREADS=6 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export OPENGOTHIC_BODY_TRACE=0 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# The policy metadata selects memory; the teacher's familiar shortcut stays off.
unset OPENGOTHIC_NAV_JOURNEY_MEMORY OPENGOTHIC_NAV_FAMILIAR_MAP
cd "$task/source"
df -h "$HOME" /mnt/shared
python3 -c 'import pathlib,shutil; assert shutil.disk_usage(pathlib.Path.home()).free >= 22*1024**3, "Less than 22 GiB free"'
python="$task/.venv/bin/python"
if [[ "$stage" == smoke ]]; then
  rm -f -- "$archive/smoke/passed"
  if [[ ! -x "$python" ]]; then uv venv --python 3.12 "$task/.venv"; fi
  uv pip install --python "$python" numpy pytest
  uv pip install --python "$python" 'torch==2.9.1+cu128' --index-url https://download.pytorch.org/whl/cu128
  "$python" -B -m pytest -q -p no:cacheprovider \
    scripts/tests/test_nav_bc_data.py scripts/tests/test_nav_bc_model.py \
    scripts/tests/test_nav_bc_train.py scripts/tests/test_nav_bc_contract.py scripts/tests/test_nav_rl.py
  cmake -S "$task/source/OpenGothic" -B "$task/build" -G Ninja \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DOPENGOTHIC_JOLT_X86=x86-64-v3 \
    -DCMAKE_CXX_FLAGS="-I$VULKAN_SDK/include" -DCMAKE_EXE_LINKER_FLAGS="-L$VULKAN_SDK/lib"
  cmake --build "$task/build" --target gothic-body-runner gothic-navpolicy-contract --parallel 3
  for version in 1 2 3; do
    demo=2
    if [[ "$version" == 1 ]]; then demo=1; fi
    "$python" -B -I scripts/check_nav_policy.py --weight-version "$version" --navdemo-version "$demo" \
      --output "$archive/smoke/legacy-v$version" --steps 8
    "$task/build/opengothic/gothic-navpolicy-contract" \
      "$archive/smoke/legacy-v$version/fixture.navbc" "$archive/smoke/legacy-v$version/parity.bin.gz"
  done
  "$python" -B -I - "$task" "$archive" "$corpus" <<'INITIALIZE'
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / "scripts"))
import nav_bc_data as data
import nav_demos as demos
from nav_bc_model import NavPolicy, export_weights
from nav_bc_train import initialize_bc3_hd
task, archive, corpus = map(Path, sys.argv[1:])
manifest, entries = data.load_training_manifest(task / "input/v5-compact.json", corpus)
if len(entries) != 700 or sum(entry["decisions"] for entry in entries) != 405599:
    raise ValueError("V5 training cohort differs from 700 episodes / 405599 decisions")
layout = demos.ray_layout(entries[0]["directory"])
model = NavPolicy(data.VECTOR_SIZE, format_version=4, auxiliary=True)
initialization = initialize_bc3_hd(model, task / "input/hd/last.pt", dict(eye_layout=layout.tolist()))
header = export_weights(model, archive / "smoke/initial.navbc",
                        dict(data.BC4_PREPROCESSING, eye_layout=layout.tolist()))
print(json.dumps(dict(event="bc4_initialization", version=header["version"],
    memory_version=header["preprocessing"]["navigation_memory_version"], **initialization)))
INITIALIZE
  "$python" -B -I scripts/check_nav_policy.py --weights "$archive/smoke/initial.navbc" \
    --source "$corpus" --output "$archive/smoke/parity" --steps 1000 --torch
  "$task/build/opengothic/gothic-navpolicy-contract" \
    "$archive/smoke/initial.navbc" "$archive/smoke/parity/parity.bin.gz"
  "$task/build/opengothic/gothic-navpolicy-contract" --memory "$archive/smoke/initial.navbc"
  "$python" -B -I - "$task" "$archive" "$corpus" <<'DRIVE'
import json
from pathlib import Path
import sys
from types import SimpleNamespace
sys.path.insert(0, str(Path.cwd() / "scripts"))
import nav_bc_data as data
import nav_rl as rl
task, archive, corpus = map(Path, sys.argv[1:])
_, entries = data.load_training_manifest(task / "input/v5-compact.json", corpus)
entries = [entry for entry in entries if entry["family"] == "point"][:2]
args = SimpleNamespace(output=archive / "smoke/drive", cache=task / "smoke-cache",
    runner=task / "build/opengothic/gothic-body-runner", assets=Path.home() / "gothic-ai/game",
    save=task / "input/save.sav", links=task / "input/accepted-links.txt", roads=task / "input/places.json",
    weights=archive / "smoke/initial.navbc", familiar_map=task / "input/familiar.bin",
    ini=None, physics="cv", seed=17, watchdog_seconds=900)
rl.checked_paths(args, policy=True)
runner = rl.new_runner(args, "bc4-smoke")
try:
    for index, entry in enumerate(entries):
        value = entry["scenario"]
        npc = value["npc"]
        residency = "on" if value["local"] else "stranger"
        commands = [f"roads load {args.roads}", f"roads familiar-load {args.familiar_map}",
            f"roads {residency} {npc}", "navdemos record off",
            "navdemos perception-mode gaze", "navdemos oracle off",
            f"navdemos drive {npc} {value['start']} {value['goal']} 10000 {args.weights} 0 18102027 sample",
            "navdemos wait"]
        directory = args.output / str(index)
        rl.scenario(args, runner, f"bc4-smoke-{index}", commands, directory)
        ep = rl.read_json(directory / "episode.json")
        if (ep["demo_version"] != 3 or ep["navigation_memory_version"] != 4
                or not ep["journey_memory"] or ep["record"] or ep["teacher_control_decisions"] != 0
                or ep["action_mode"] != "sample" or ep["decisions"] <= 0
                or ep["familiar_map"] != value["local"]):
            raise ValueError(f"BC4 drive identity differs: {directory}")
        print(json.dumps(dict(event="bc4_smoke_episode", id=entry["id"],
            decisions=ep["decisions"], memory_version=ep["navigation_memory_version"])))
finally:
    runner.close()
(archive / "smoke/passed").write_text("BC4 contracts and two train drive episodes passed\n")
DRIVE
elif [[ "$stage" == train ]]; then
  test -f "$archive/smoke/passed"
  test -x "$python"
  test ! -e "$archive/bc4/last.pt"
  nvidia-smi
  "$python" -B -I scripts/nav_bc_data.py prepare --source "$corpus" \
    --manifest "$task/input/v5-compact.json" --output "$task/cache" --input-variant hd-familiar
  "$python" -B -I scripts/nav_bc_train.py train --cache "$task/cache" --output "$archive/bc4" \
    --init "$task/input/hd/last.pt" --epochs 20 --batch 8 --segment 64 \
    --lr .0003 --seed 17 --device cuda --threads 6
  "$python" -B -I scripts/check_nav_policy.py --weights "$archive/bc4/policy.navbc" \
    --source "$corpus" --output "$archive/bc4/parity" --steps 1000 --torch
  "$task/build/opengothic/gothic-navpolicy-contract" \
    "$archive/bc4/policy.navbc" "$archive/bc4/parity/parity.bin.gz"
  rm -rf -- "$task/cache"
else
  echo "Unknown phase: $stage" >&2; exit 2
fi
df -h "$HOME" /mnt/shared
PHASE
if [[ "$stage" == smoke ]]; then
  "$ROOT/scripts/gq" submit --class quick --threads 12 --cpu-quota 1200% --mem 12G --max 45m \
    --name 'nav bc4 format and two train smoke episodes' -- bash "$task/phase.sh" smoke
else
  "$ROOT/scripts/gq" submit --class batch --threads 6 --cpu-quota 600% --mem 10G --gpu-mem 4G --max 2h \
    --name 'nav bc4 v5 training and recorded parity' -- bash "$task/phase.sh" train
fi
