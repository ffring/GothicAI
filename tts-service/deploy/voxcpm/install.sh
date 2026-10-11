#!/usr/bin/env bash
# Installs VoxCPM2 TTS on the GPU server as a systemd user service (no root, no Docker changes).
# Run from the repository: scripts/deploy-gpu.sh voxcpm (copies this directory and the settings,
# VOXCPM_HOST/VOXCPM_PORT in ~/gothic-ai/gothic-ai.env, then runs this script on the server).
# First start downloads the model (~5 GB). Logs: journalctl --user -u gothic-voxcpm -f
set -euo pipefail
cd "$(dirname "$0")"
UV="${UV:-$HOME/.local/bin/uv}"

"$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python -U voxcpm websockets soundfile --torch-backend=auto

mkdir -p ~/.config/systemd/user
cp gothic-voxcpm.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable gothic-voxcpm.service
systemctl --user restart gothic-voxcpm.service
echo "started; ready when the log says 'listening'"
