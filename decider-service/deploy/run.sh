#!/usr/bin/env bash
# Starts the decision service. Listens on DECIDER_HOST only (a VPN address, set in ~/gothic-ai/gothic-ai.env
# by scripts/deploy-gpu.sh from GOTHIC_DECIDER_URL), like the Voxtral service.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
exec .venv/bin/gothic-decider serve \
  --host "${DECIDER_HOST:-127.0.0.1}" --port "${DECIDER_PORT:-47832}" "$@"
