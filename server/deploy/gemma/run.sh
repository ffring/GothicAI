#!/usr/bin/env bash
# Starts the quick model of fights. Listens on GEMMA_HOST only (a VPN address, set in
# ~/gothic-ai/gothic-ai.env by scripts/deploy-gpu.sh from GOTHIC_QUICK_LLM_URL), like the other services.
# - the alias is the model name the AI server asks for (gothic_ai.llm.QUICK_MODEL);
# - one slot: lines go one after another; the slot's prompt (an npc's instructions and history, warmed by the
#   AI server) is kept, and the others' in RAM (--cache-ram, 8 GB by default);
# - 16384 tokens of context: a line's prompt is 2-5k, a long talk's grows past 6k (6144 overflowed, 2026-10-22);
#   only the global layers' KV cache grows with it, q8_0 keeps the GPU's use at ~7.5 GB;
# - no thinking: the template's <|think|> is left out and the reasoning budget is 0.
set -euo pipefail
cd "$(dirname "$0")"
exec llama.cpp/build/bin/llama-server -m models/gemma-4-12b-it-qat-q4_0.gguf --alias gemma-4-12b \
  --host "${GEMMA_HOST:-127.0.0.1}" --port "${GEMMA_PORT:-47834}" \
  -ngl 99 -c 16384 -np 1 -fa on -ctk q8_0 -ctv q8_0 \
  --jinja --reasoning-budget 0 --chat-template-kwargs '{"enable_thinking":false}' "$@"
