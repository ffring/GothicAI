#!/usr/bin/env bash
# Installs Qwen3 Forced Aligner 0.6B (Q8_0 GGUF on audio.cpp) on the GPU server: known text -> word timings.
# No service: align.sh runs the command line tool once per job (`gothic-shorts align` on the Mac calls it).
# Server needs an NVIDIA GPU with ~2 GB free while a job runs, CUDA toolkit in /usr/local/cuda, cmake,
# ninja, git. Run from the repository: scripts/deploy-gpu.sh aligner (copies this directory, then runs this
# script on the server). Needs no root and no Docker changes. Its own audio.cpp checkout and build, so
# the Voxtral one (~/gothic-ai/voxtral) and its running service are never touched. Builds audio.cpp
# (~6 min) and downloads the model (1.1 GB).
set -euo pipefail
cd "$(dirname "$0")"
AUDIOCPP_COMMIT=73c5554950896ce36864b6da2fb3e129c429a8a7  # the same commit as voxtral/install.sh
MODEL_URL=https://huggingface.co/audio-cpp/audio.cpp-gguf/resolve/main/Qwen3-ForcedAligner-0.6B-GGUF/qwen3-forced-aligner-0.6b-q8_0.gguf
export CUDA_HOME=/usr/local/cuda
export PATH="$CUDA_HOME/bin:$PATH"

if [ ! -d audio.cpp/.git ]; then
  git init -q audio.cpp
  git -C audio.cpp remote add origin https://github.com/0xShug0/audio.cpp
fi
git -C audio.cpp fetch -q --depth 1 origin "$AUDIOCPP_COMMIT"
git -C audio.cpp checkout -q --force FETCH_HEAD
arch="${CUDA_ARCH:-$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d .)}"
# the aligner's model family pulls in qwen3_asr (its tokenizer and audio encoder)
(cd audio.cpp && scripts/build_linux.sh --backend cuda --cuda-arch "$arch" \
  --model-set custom --models qwen3_forced_aligner --target audiocpp_cli -j "${JOBS:-8}")

mkdir -p models
if [ ! -s models/qwen3-forced-aligner-q8_0.gguf ]; then
  curl -fL --retry 5 -C - -o models/qwen3-forced-aligner-q8_0.gguf.part "$MODEL_URL"
  mv models/qwen3-forced-aligner-q8_0.gguf.part models/qwen3-forced-aligner-q8_0.gguf
fi
echo "installed; align with ~/gothic-ai/aligner/align.sh <16 kHz mono wav> <text file> <language> <words.json>"
