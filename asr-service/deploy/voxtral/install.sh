#!/usr/bin/env bash
# Installs Voxtral Mini 4B Realtime (Q4_K GGUF on audio.cpp) on the GPU server as a systemd user service.
# Server needs an NVIDIA GPU with ~4 GB free, CUDA toolkit in /usr/local/cuda, cmake, ninja, git.
# Run from the repository: scripts/deploy-gpu.sh voxtral (copies this directory and the settings,
# VOXTRAL_HOST/VOXTRAL_PORT in ~/gothic-ai/gothic-ai.env, then runs this script on the server).
# Needs no root and no Docker changes. Builds audio.cpp (~7 min) and downloads the model (2.9 GB).
# Logs: journalctl --user -u gothic-voxtral -f
set -euo pipefail
cd "$(dirname "$0")"
AUDIOCPP_COMMIT=73c5554950896ce36864b6da2fb3e129c429a8a7  # audio.cpp is young: its server API may change
MODEL_URL=https://huggingface.co/mistral-experimental/AudioCPP-Voxtral-Mini-4B-Realtime-2602-GGUF/resolve/main/voxtral-mini-4b-realtime-2602-q4_k.gguf
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
  --model-set custom --models voxtral_realtime --target audiocpp_server -j "${JOBS:-8}")

mkdir -p models
if [ ! -s models/voxtral-q4_k.gguf ]; then
  curl -fL --retry 5 -C - -o models/voxtral-q4_k.gguf.part "$MODEL_URL"
  mv models/voxtral-q4_k.gguf.part models/voxtral-q4_k.gguf
fi

mkdir -p ~/.config/systemd/user
cp gothic-voxtral.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable gothic-voxtral.service
systemctl --user restart gothic-voxtral.service
echo "started; ready when GET /health answers on port ${VOXTRAL_PORT:-47830}"
