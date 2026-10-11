#!/usr/bin/env bash
# Voxtral Mini 4B Realtime behind vLLM's realtime WebSocket API, for the Gothic AI ASR service.
# Listens on the Tailscale address only; ~55% of the 3090 (BF16 weights ~9 GB + cache).
# 2048 tokens per request: one push-to-talk utterance (80 ms of audio per token) fits easily.
set -euo pipefail
cd "$(dirname "$0")"
export VLLM_DISABLE_COMPILE_CACHE=1
# FlashInfer JIT-compiles kernels on first start; it must find the toolkit's libcudart
export CUDA_HOME=/usr/local/cuda
export PATH="$CUDA_HOME/bin:$PATH"
exec .venv/bin/vllm serve mistralai/Voxtral-Mini-4B-Realtime-2602 \
  --tokenizer-mode mistral \
  --compilation_config '{"cudagraph_mode": "PIECEWISE"}' \
  --host "${VOXTRAL_HOST:-127.0.0.1}" --port "${VOXTRAL_PORT:-47830}" \
  --max-model-len 2048 \
  --gpu-memory-utilization 0.55
