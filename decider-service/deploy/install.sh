#!/usr/bin/env bash
# Installs the NPC decision service on the GPU server as a systemd user service.
# Server needs an NVIDIA GPU with ~5 GB free, CUDA toolkit in /usr/local/cuda, uv in ~/.local/bin.
# Run from the repository: scripts/deploy-gpu.sh decider (copies decider-service and the settings,
# DECIDER_HOST/DECIDER_PORT in ~/gothic-ai/gothic-ai.env, then runs this script on the server).
# Needs no root and no Docker changes. causal-conv1d has no wheels for torch cu130 and is built from
# source (~7 min the first time, then cached by uv). The first start downloads the model (~4.5 GB)
# and warms up (~1 min). Logs: journalctl --user -u gothic-decider -f
set -euo pipefail
cd "$(dirname "$0")/.."
UV="${UV:-$HOME/.local/bin/uv}"
PY=.venv/bin/python

[ -x "$PY" ] || "$UV" venv --python 3.12 .venv
"$UV" pip install --python "$PY" -e ".[gpu]"
# fused kernels for the Gated DeltaNet layers of Qwen3.5 (without them: reference code, 1.3x slower)
export CUDA_HOME=/usr/local/cuda PATH=/usr/local/cuda/bin:$PATH MAX_JOBS=6 TORCH_CUDA_ARCH_LIST=8.6
"$UV" pip install --python "$PY" ninja packaging wheel setuptools
"$UV" pip install --python "$PY" --no-build-isolation causal-conv1d
"$PY" -c "import fla, causal_conv1d; print('kernels ok')"

mkdir -p ~/.config/systemd/user
cp deploy/gothic-decider.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gothic-decider.service
echo "started; ready when the log says 'listening on'"
