#!/usr/bin/env bash
# Sharper textures of the game's characters. The originals are small (a face 256x128, bodies and armors
# mostly 512x512): Real-ESRGAN on the GPU server makes them 4 times as large, then they go down to at
# most MAX pixels a side. The game loads them instead of the originals from OPENGOTHIC_TEXTURES; the
# launcher sets it when $HD_DIR/textures exists. Made from the game's own textures: they stay on this
# machine and never go into git.
#
#   scripts/hd-textures.sh [all|extract|upscale|finish]     (all: the three in turn)
#
#   extract  the textures of the human models with their face and skin variants -> $HD_DIR/src (PNG)
#   upscale  src -> the GPU server -> 4x -> $HD_DIR/x4
#   finish   x4 -> at most MAX a side, the colors of the originals, DXT with mipmaps -> $HD_DIR/textures
#            (DDS files), what the game loads
#
# env: GOTHIC_DIR   game data (default: /Volumes/DevSSD/Dev/games/Gothic II)
#      HD_DIR       work and output folder (default: ~/.local/share/gothic-ai/hd-textures)
#      GOTHIC_GPU_SSH  ssh destination of the GPU server (default: from .env, as scripts/deploy-gpu.sh)
#      CREATURES=1  the textures of the creatures too
#      MODEL        the Real-ESRGAN model (default: realesrgan-x4plus)
#      MAX          the largest side of a texture (default: 1024)
#      KEEP=1       keep the 4x ones (x4/, about 1 GB, for another finish) and the finished PNG (png/)
# The GPU server needs ~/gothic-ai/upscale/bin/realesrgan-ncnn-vulkan (the Real-ESRGAN release
# realesrgan-ncnn-vulkan-20220424-ubuntu.zip unpacked there).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GOTHIC_DIR="${GOTHIC_DIR:-/Volumes/DevSSD/Dev/games/Gothic II}"
HD_DIR="${HD_DIR:-$HOME/.local/share/gothic-ai/hd-textures}"
MODEL="${MODEL:-realesrgan-x4plus}"
MAX="${MAX:-1024}"
REMOTE=gothic-ai/upscale

# the GPU server: the environment, else the nearest .env at or above the repository
gpu_ssh() {
  if [ -n "${GOTHIC_GPU_SSH:-}" ]; then echo "$GOTHIC_GPU_SSH"; return; fi
  local d="$ROOT"
  while [ "$d" != "/" ]; do
    if [ -f "$d/.env" ]; then
      sed -n -E 's/^[[:space:]]*(export[[:space:]]+)?GOTHIC_GPU_SSH=(.*)$/\2/p' "$d/.env" | head -1 \
        | sed -E "s/^[\"']//; s/[\"']$//"
      return
    fi
    d="$(dirname "$d")"
  done
}

# a tool of scripts/hd/ against the engine's build of ZenKit (ZKNO_REM, mmap: other flags read the
# archives wrong), squish and miniz
tool() {
  local name=$1 build="$ROOT/OpenGothic/build" bin="$HD_DIR/bin/$1" src="$ROOT/scripts/hd/$1.cpp"
  [ -f "$build/lib/ZenKit/libzenkit.a" ] || { echo "build the engine first: scripts/build-engine.sh" >&2; exit 1; }
  mkdir -p "$HD_DIR/bin"
  if [ ! -x "$bin" ] || [ "$src" -nt "$bin" ]; then
    c++ -std=c++20 -O2 -DZKNO_REM=1 -D_ZK_WITH_MMAP=1 -w \
      -I"$ROOT/OpenGothic/lib/ZenKit/include" -I"$build/lib/ZenKit/include" -I"$ROOT/OpenGothic/lib/miniz" \
      -I"$ROOT/OpenGothic/lib/ZenKit/vendor/libsquish" -I"$build/lib/ZenKit/vendor/libsquish" \
      -I"$ROOT/OpenGothic/lib/Tempest/Engine/thirdparty" \
      "$src" "$build/lib/ZenKit/libzenkit.a" "$build/lib/ZenKit/vendor/libsquish/libsquish.a" "$build/libminiz.a" \
      -o "$bin" || exit 1  # inside $(tool ...): set -e does not stop it there
  fi
  echo "$bin"
}

extract() {
  local bin
  bin="$(tool texdump)"
  rm -rf "$HD_DIR/src"
  "$bin" "$GOTHIC_DIR" "$HD_DIR/src" ${CREATURES:+--creatures}
}

upscale() {
  local ssh_dest
  ssh_dest="$(gpu_ssh)"
  [ -n "$ssh_dest" ] || { echo "no GPU server: set GOTHIC_GPU_SSH" >&2; exit 1; }
  ssh "$ssh_dest" "test -x $REMOTE/bin/realesrgan-ncnn-vulkan" \
    || { echo "no $REMOTE/bin/realesrgan-ncnn-vulkan on the GPU server (see the top of this script)" >&2; exit 1; }
  ssh "$ssh_dest" "rm -rf $REMOTE/work && mkdir -p $REMOTE/work/src $REMOTE/work/x4"
  rsync -a "$HD_DIR/src/" "$ssh_dest:$REMOTE/work/src/"
  # one process for all: the model loads once (a 3090 does a face in a fraction of a second)
  ssh "$ssh_dest" "cd $REMOTE && bin/realesrgan-ncnn-vulkan -i work/src -o work/x4 -m bin/models -n $MODEL -s 4 -f png" \
    > "$HD_DIR/upscale.log" 2>&1 || { tail -20 "$HD_DIR/upscale.log" >&2; exit 1; }
  rm -rf "$HD_DIR/x4"
  mkdir -p "$HD_DIR/x4"
  rsync -a "$ssh_dest:$REMOTE/work/x4/" "$HD_DIR/x4/"
  ssh "$ssh_dest" "rm -rf $REMOTE/work"
}

finish() {
  # 4x, but at most MAX a side (a face 1024x512, a 512x512 armor 1024x1024 from the 4x one, sharper than
  # 2x), with the colors of the originals (scripts/hd/finish.py); then DXT with mipmaps, as the game's
  # own (scripts/hd/texpack.cpp): as RGBA they took a gigabyte more memory in a town
  local pack
  pack="$(tool texpack)"
  rm -rf "$HD_DIR/png" "$HD_DIR/textures.new"
  uv run --quiet --no-project --with numpy --with pillow python "$ROOT/scripts/hd/finish.py" \
    "$HD_DIR/src" "$HD_DIR/x4" "$HD_DIR/png" "$MAX"
  "$pack" "$HD_DIR/png" "$HD_DIR/textures.new"
  rm -rf "$HD_DIR/textures"
  mv "$HD_DIR/textures.new" "$HD_DIR/textures"
  [ -n "${KEEP:-}" ] || rm -rf "$HD_DIR/png"
}

case "${1:-all}" in
  extract) extract ;;
  upscale) upscale ;;
  finish)  finish ;;
  all)     extract; upscale; finish
           # the 4x ones are only for another finish (MAX): ~1 GB
           [ -n "${KEEP:-}" ] || rm -rf "$HD_DIR/x4" ;;
  *) echo "usage: scripts/hd-textures.sh [all|extract|upscale|finish]" >&2; exit 2 ;;
esac
