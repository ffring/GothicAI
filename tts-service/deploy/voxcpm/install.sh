#!/usr/bin/env bash
# Installs VoxCPM2 TTS (Q8_0 GGUF on audio.cpp, plus server.py, the WebSocket bridge the TTS service
# talks to) on the GPU server as a systemd user service. Server needs an NVIDIA GPU with ~3.5 GB free,
# CUDA toolkit in /usr/local/cuda, cmake, ninja, git. Run from the repository: scripts/deploy-gpu.sh voxcpm
# (copies this directory and the settings, VOXCPM_HOST/VOXCPM_PORT in ~/gothic-ai/gothic-ai.env, then runs
# this script on the server). Needs no root and no Docker changes. Its own audio.cpp checkout and build,
# as the aligner's, so the Voxtral one (~/gothic-ai/voxtral) and its running service are never touched.
# Builds audio.cpp (~15 min the first time) and downloads the model (3 GB).
# Logs: journalctl --user -u gothic-voxcpm -f
set -euo pipefail
cd "$(dirname "$0")"
AUDIOCPP_COMMIT=73c5554950896ce36864b6da2fb3e129c429a8a7  # the same commit as voxtral/install.sh
MODEL_URL=https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/main/VoxCPM2-GGUF/voxcpm2-q8_0.gguf
UV="${UV:-$HOME/.local/bin/uv}"
export CUDA_HOME=/usr/local/cuda
export PATH="$CUDA_HOME/bin:$PATH"

if [ ! -d audio.cpp/.git ]; then
  git init -q audio.cpp
  git -C audio.cpp remote add origin https://github.com/0xShug0/audio.cpp
fi
git -C audio.cpp fetch -q --depth 1 origin "$AUDIOCPP_COMMIT"
git -C audio.cpp checkout -q --force FETCH_HEAD
arch="${CUDA_ARCH:-$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d .)}"
(cd audio.cpp && scripts/build_linux.sh --backend cuda --cuda-arch "$arch" \
  --model-set custom --models voxcpm2 --target audiocpp_server -j "${JOBS:-8}")

mkdir -p models
if [ ! -s models/voxcpm2-q8_0.gguf ]; then
  curl -fL --retry 5 -C - -o models/voxcpm2-q8_0.gguf.part "$MODEL_URL"
  mv models/voxcpm2-q8_0.gguf.part models/voxcpm2-q8_0.gguf
fi

# the bridge needs no torch: a small venv, made anew (the PyTorch one of the old server goes with it)
rm -rf .venv
"$UV" venv -q --python 3.12 .venv
"$UV" pip install -q --python .venv/bin/python websockets httpx

mkdir -p ~/.config/systemd/user
cp gothic-voxcpm.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable gothic-voxcpm.service
systemctl --user restart gothic-voxcpm.service
echo "started; ready when the log says 'listening'"
