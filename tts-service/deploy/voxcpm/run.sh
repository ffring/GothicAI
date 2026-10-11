#!/usr/bin/env bash
# VoxCPM2 TTS (server.py) for the Gothic AI TTS service. Tailscale address only; ~6 GB of the 3090.
set -euo pipefail
cd "$(dirname "$0")"
export CUDA_HOME=/usr/local/cuda
export PATH="$CUDA_HOME/bin:$PATH"
exec .venv/bin/python server.py --host "${VOXCPM_HOST:-127.0.0.1}" --port "${VOXCPM_PORT:-47831}"
