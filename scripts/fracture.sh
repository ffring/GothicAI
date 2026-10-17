#!/usr/bin/env bash
# Offline fixed-seed fracture of stock-world crates, barrels, pots and fences.
# Requires a legally installed Gothic II. The cache contains game geometry and
# stays outside git, just like HD textures. No game, GPU or model is started.
#
# scripts/fracture.sh [all|extract|cut|verify]
# env: GOTHIC_DIR (game installation), OPENGOTHIC_FRACTURE (cache directory),
#      FRACTURE_SEED=601, FRACTURE_PIECES=12 (8–20).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GOTHIC_DIR="${GOTHIC_DIR:-/Volumes/DevSSD/Dev/games/Gothic II}"
FRACTURE_DIR="${OPENGOTHIC_FRACTURE:-$HOME/.local/share/gothic-ai/fracture}"

extract() {
  local bin="$FRACTURE_DIR/bin/fracture-extract" build="$ROOT/OpenGothic/build"
  mkdir -p "$FRACTURE_DIR/bin" "$FRACTURE_DIR/source"
  if [ ! -x "$bin" ] || [ "$ROOT/scripts/fracture/extract.cpp" -nt "$bin" ]; then
    if [ -f "$build/lib/ZenKit/libzenkit.a" ]; then
      c++ -std=c++20 -O2 -DZKNO_REM=1 -D_ZK_WITH_MMAP=1 \
        -I"$ROOT/OpenGothic/lib/ZenKit/include" \
        "$ROOT/scripts/fracture/extract.cpp" "$build/lib/ZenKit/libzenkit.a" \
        "$build/lib/ZenKit/vendor/libsquish/libsquish.a" "$build/libminiz.a" -o "$bin"
    else
      cmake -S "$ROOT/scripts/fracture" -B "$FRACTURE_DIR/build" -DCMAKE_BUILD_TYPE=Release
      cmake --build "$FRACTURE_DIR/build" --target fracture-extract -j2
      cp "$FRACTURE_DIR/build/fracture-extract" "$bin"
    fi
  fi
  "$bin" "$GOTHIC_DIR" "$FRACTURE_DIR/source" "$ROOT/OpenGothic/data/destructible-vobs.csv"
}

cut() {
  uv run --quiet --no-project --with numpy==2.3.5 --with scipy==1.16.3 \
    python "$ROOT/scripts/fracture/fracture.py" "$FRACTURE_DIR" \
    --seed "${FRACTURE_SEED:-601}" --pieces "${FRACTURE_PIECES:-12}" "$@"
}

case "${1:-all}" in
  all) extract; cut; cut --verify ;;
  extract) extract ;;
  cut) cut ;;
  verify) cut --verify ;;
  *) echo "usage: scripts/fracture.sh [all|extract|cut|verify]" >&2; exit 2 ;;
esac
