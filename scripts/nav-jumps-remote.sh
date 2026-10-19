#!/usr/bin/env bash
# Submit one bounded phase and return immediately. No training or PPO paths.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
stage="${1:?Usage: nav-jumps-remote.sh build|bootstrap|pilot|recheck|candidates|discover|routes|record}"
export GQ_OWNER=nav-jumps
host=ryzen
task="$(ssh -o ConnectTimeout=10 "$host" 'printf "%s/gothic-ai/nav-jumps" "$HOME"')"
source_dir="$task/source"
case "$stage" in
  build|bootstrap|pilot)
    ssh "$host" "mkdir -p '$source_dir' '$task/input'"
    rsync -a --exclude=build --exclude='build-*' --exclude=__pycache__ \
      "$ROOT/OpenGothic" "$ROOT/scripts" "$ROOT/AGENTS.md" "$host:$source_dir/"
    rsync -a "$ROOT/reports/navigation/2026-10-16-teacher/raw/accepted-links.txt" \
      "$host:$task/input/accepted-links-legacy.txt"
    rsync -a "$ROOT/server/src/gothic_ai/gamedata/places.json" "$host:$task/input/places.json"
    # Shared game assets and the historical save are read-only inputs. Copy the save.
    ssh "$host" "test -f '$task/input/save.sav' || cp \"\$HOME/gothic-ai/nav-mesh/save_slot_1.sav\" '$task/input/save.sav'"
    ssh "$host" "cat > '$task/build.sh'" <<'BUILD'
#!/usr/bin/env bash
set -euo pipefail
task="$(cd "$(dirname "$0")" && pwd)"
export VULKAN_SDK="$HOME/gothic-ai/body-baseline/toolchain/vulkan-1.4.341.0"
export PATH="$HOME/.local/bin:$VULKAN_SDK/bin:$PATH"
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1
df -h "$HOME"
if [[ ! -x "$task/.venv/bin/python" ]]; then
  uv venv "$task/.venv"
fi
uv pip install --python "$task/.venv/bin/python" numpy pytest
cmake -S "$task/source/OpenGothic" -B "$task/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DOPENGOTHIC_JOLT_X86=x86-64-v3 \
  -DCMAKE_CXX_FLAGS="-I$VULKAN_SDK/include" -DCMAKE_EXE_LINKER_FLAGS="-L$VULKAN_SDK/lib"
cmake --build "$task/build" --target Gothic2Notr gothic-body-runner check-navmesh --parallel 3
cd "$task/source"
tests=(scripts/tests/test_nav_jumps.py)
if [[ "${1:-}" != pilot ]]; then
  tests+=(scripts/tests/test_nav_jump_routes.py scripts/tests/test_nav_jump_observability.py scripts/tests/test_nav_demos.py)
fi
"$task/.venv/bin/python" -m pytest -q "${tests[@]}"
if [[ "${1:-}" == bootstrap ]]; then
  for phase in recheck candidates; do
    args=(--runner "$task/build/opengothic/gothic-body-runner" --assets "$HOME/gothic-ai/game"
      --save "$task/input/save.sav" --cache "$task/cache-ground" --output "$task/$phase")
    if [[ "$phase" == recheck ]]; then
      args+=(--links "$task/input/accepted-links-legacy.txt")
    else
      args+=(--site-budget 1200)
    fi
    "$task/.venv/bin/python" -I "$task/source/scripts/nav_jumps.py" "$phase" "${args[@]}"
  done
elif [[ "${1:-}" == pilot ]]; then
  "$task/.venv/bin/python" -I "$task/source/scripts/nav_jumps.py" pilot \
    --runner "$task/build/opengothic/gothic-body-runner" --assets "$HOME/gothic-ai/game" \
    --save "$task/input/save.sav" --cache "$task/cache-ground" --output "$task/pilot-grounded" \
    --links "$task/candidates/candidates.txt" --exclude-links "$task/recheck/accepted-links-cv.txt"
fi
BUILD
    "$ROOT/scripts/gq" submit --class batch --threads 4 --mem 6G --max 2h \
      --name "nav jumps $stage engine runner tests" -- bash "$task/build.sh" "$stage"
    ;;
  recheck|candidates|discover)
    ssh "$host" 'df -h "$HOME"'
    output="$task/$stage"
    [[ "$stage" != discover ]] || output="$task/discover-grounded"
    args=("$task/.venv/bin/python" -I "$source_dir/scripts/nav_jumps.py" "$stage"
      --runner "$task/build/opengothic/gothic-body-runner"
      --assets "${task%/nav-jumps}/game" --save "$task/input/save.sav"
      --cache "$task/cache-ground" --output "$output")
    if [[ "$stage" == recheck ]]; then
      args+=(--links "$task/input/accepted-links-legacy.txt")
    elif [[ "$stage" == discover ]]; then
      args+=(--links "$task/candidates/candidates.txt" --exclude-links "$task/recheck/accepted-links-cv.txt")
    else
      args+=(--site-budget 1200)
    fi
    "$ROOT/scripts/gq" submit --class batch --threads 2 --mem 6G --max 2h \
      --name "nav jumps $stage CV" -- env OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 "${args[@]}"
    ;;
  routes)
    # This phase uses the verified runner; only the changed route selector is synced.
    ssh "$host" "mkdir -p '$source_dir/scripts/tests'"
    rsync -a "$ROOT/scripts/nav_jump_routes.py" "$host:$source_dir/scripts/"
    rsync -a "$ROOT/scripts/tests/test_nav_jump_routes.py" "$host:$source_dir/scripts/tests/"
    ssh "$host" "cat > '$task/routes.sh'" <<'ROUTES'
#!/usr/bin/env bash
set -euo pipefail
task="$(cd "$(dirname "$0")" && pwd)"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1
df -h "$HOME"
cd "$task/source"
"$task/.venv/bin/python" -m pytest -q scripts/tests/test_nav_jump_routes.py
"$task/.venv/bin/python" -I - "$task" <<'PY'
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys
task = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("nav_jumps_merge", task / "source/scripts/nav_jumps.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
old = probe.links(task / "recheck/accepted-links-cv.txt")
new = probe.links(task / "discover-grounded/accepted-links-cv.txt")
combined = old + new
if len(combined) != 121 or len({row["id"] for row in combined}) != len(combined):
    raise RuntimeError("Expected 121 verified links with unique IDs")
destination = task / "accepted-links-cv.txt"
if destination.exists() and probe.links(destination) != combined:
    raise RuntimeError("Refusing to replace the verified transition set")
probe.write_links(destination, combined)
print(json.dumps(dict(verified_links=len(combined), old=len(old), new=len(new),
                      by_type=dict(Counter(probe.TYPES[row["type"]] for row in combined)))), flush=True)
PY
"$task/.venv/bin/python" -I "$task/source/scripts/nav_jump_routes.py" run \
  --runner "$task/build/opengothic/gothic-body-runner" --assets "$HOME/gothic-ai/game" \
  --save "$task/input/save.sav" --cache "$task/cache-transitions-cv" \
  --links "$task/accepted-links-cv.txt" --roads "$task/input/places.json" --places "$task/input/places.json" \
  --output "$task/routes" --per-link 3
ROUTES
    "$ROOT/scripts/gq" submit --class batch --threads 2 --mem 6G --max 2h \
      --name "nav jumps routes v2 max3 per transition" -- bash "$task/routes.sh"
    ;;
  record)
    # Record the approved route set with the existing CV runner and private cache.
    ssh "$host" "mkdir -p '$source_dir/scripts/tests'"
    rsync -a "$ROOT/scripts/nav_jump_record.py" "$host:$source_dir/scripts/"
    rsync -a "$ROOT/scripts/tests/test_nav_jump_record.py" "$host:$source_dir/scripts/tests/"
    ssh "$host" "cat > '$task/record.sh'" <<'RECORD'
#!/usr/bin/env bash
set -euo pipefail
task="$(cd "$(dirname "$0")" && pwd)"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1
df -h "$HOME"
cd "$task/source"
"$task/.venv/bin/python" -m pytest -q scripts/tests/test_nav_jump_record.py
"$task/.venv/bin/python" -I "$task/source/scripts/nav_jump_record.py" \
  --runner "$task/build/opengothic/gothic-body-runner" --assets "$HOME/gothic-ai/game" \
  --save "$task/input/save.sav" --cache "$task/cache-transitions-cv" \
  --links "$task/accepted-links-cv.txt" --roads "$task/input/places.json" \
  --routes "$task/routes/routes.jsonl" --output "$HOME/gothic-ai/nav-demos-jumps" --count 267
"$task/.venv/bin/python" -I "$task/source/scripts/nav_demos.py" check "$HOME/gothic-ai/nav-demos-jumps"
RECORD
    "$ROOT/scripts/gq" submit --class batch --threads 2 --mem 6G --max 2h \
      --name "nav jumps record 267 teacher v2 CV 240s" -- bash "$task/record.sh"
    ;;
  *) echo "Unknown phase: $stage" >&2; exit 2 ;;
esac
