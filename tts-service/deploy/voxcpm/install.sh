#!/usr/bin/env bash
# Installs VoxCPM2 TTS on the GPU server as a systemd user service (no root, no Docker changes).
# Run from the repository: scripts/deploy-gpu.sh voxcpm (copies this directory and the settings,
# VOXCPM_HOST/VOXCPM_PORT in ~/gothic-ai/gothic-ai.env, then runs this script on the server).
# First start downloads the model (~5 GB). Logs: journalctl --user -u gothic-voxcpm -f
set -euo pipefail
cd "$(dirname "$0")"
UV="${UV:-$HOME/.local/bin/uv}"

[ -x .venv/bin/python ] || "$UV" venv --python 3.12 .venv  # a redeploy keeps it (and its torch)
# torch, torchaudio and torchcodec must be of one CUDA build: a redeploy upgrades voxcpm only (`-U` once
# moved torch to a newer CUDA build and left torchaudio behind, and the server did not start)
"$UV" pip install --python .venv/bin/python voxcpm websockets soundfile --upgrade-package voxcpm --torch-backend=auto

mkdir -p ~/.config/systemd/user
cp gothic-voxcpm.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable gothic-voxcpm.service
systemctl --user restart gothic-voxcpm.service
echo "started; ready when the log says 'listening'"
