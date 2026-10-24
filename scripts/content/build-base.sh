#!/usr/bin/env bash
# Builds the converted base (docs/asset-migration.md): four world meshes, every texture and
# every static mesh of the installed Gothic II, each checked against its source.
#
#   scripts/content/build-base.sh [out dir]      # default ~/.local/share/gothic-ai/base/v1
#   GOTHIC_DIR=... scripts/content/build-base.sh
#
# The output is Gothic content: it stays outside git. On Ryzen pass an explicit folder of
# your task: other sessions' runs read ~/.local/share/gothic-ai/base when OPENGOTHIC_BASE is unset.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GOTHIC_DIR="${GOTHIC_DIR:-/Volumes/DevSSD/Dev/games/Gothic II}"
OUT="${1:-$HOME/.local/share/gothic-ai/base/v1}"
BUILD="${CONVERT_BUILD:-$HOME/.cache/gothic-ai/content-convert-build}"
generator=(); command -v ninja >/dev/null && generator=(-G Ninja)
cmake -S "$ROOT/scripts/convert" -B "$BUILD" "${generator[@]}" -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build "$BUILD" --parallel 4 >/dev/null
tool="$BUILD/content-convert"
"$tool" --self-test
for world in NEWWORLD OLDWORLD ADDONWORLD DRAGONISLAND; do
  rm -rf "$OUT/worlds/$world"
  "$tool" world "$GOTHIC_DIR" "$world.ZEN" "$OUT/worlds/$world"
done
rm -rf "$OUT/textures" "$OUT/meshes"
"$tool" textures "$GOTHIC_DIR" "$OUT/textures"
"$tool" meshes "$GOTHIC_DIR" "$OUT/meshes"
du -sh "$OUT"
