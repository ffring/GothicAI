#!/usr/bin/env bash
# Installs the quick model of fights (npc lines at once, server/src/gothic_ai/dialogue.py): Gemma 4 12B,
# Google's QAT Q4_0 GGUF, on llama.cpp, as a systemd user service on the GPU server.
# Server needs an NVIDIA GPU with ~8 GB free, CUDA toolkit in /usr/local/cuda, cmake, git.
# Run from the repository: scripts/deploy-gpu.sh gemma (copies this directory and the settings,
# GEMMA_HOST/GEMMA_PORT in ~/gothic-ai/gothic-ai.env, then runs this script on the server).
# Needs no root and no Docker changes. Builds llama.cpp (~12 min) and downloads the model (7 GB).
# Logs: journalctl --user -u gothic-gemma -f
set -euo pipefail
cd "$(dirname "$0")"
LLAMA_CPP_TAG=v0.6.0
MODEL=gemma-4-12b-it-qat-q4_0.gguf
MODEL_URL=https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf/resolve/main/$MODEL
export CUDA_HOME=/usr/local/cuda
export PATH="$CUDA_HOME/bin:$PATH"

if [ ! -d llama.cpp/.git ]; then
  git clone -q --depth 1 --branch "$LLAMA_CPP_TAG" https://github.com/ggml-org/llama.cpp.git
fi
git -C llama.cpp fetch -q --depth 1 origin tag "$LLAMA_CPP_TAG"
git -C llama.cpp checkout -q --force "$LLAMA_CPP_TAG"
arch="${CUDA_ARCH:-$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d .)}"
cmake -S llama.cpp -B llama.cpp/build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES="$arch" \
  -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build llama.cpp/build -j "${JOBS:-10}" --target llama-server

mkdir -p models
if [ ! -s "models/$MODEL" ]; then
  curl -fL --retry 5 -C - -o "models/$MODEL.part" "$MODEL_URL"
  mv "models/$MODEL.part" "models/$MODEL"
fi

mkdir -p ~/.config/systemd/user
cp gothic-gemma.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable gothic-gemma.service
systemctl --user restart gothic-gemma.service
echo "started; ready when GET /health answers on port ${GEMMA_PORT:-47834}"
