#!/usr/bin/env bash
# Installs the jobs of `gothic-shorts dub` on the GPU server: Demucs (htdemucs_ft) splits a take's game sound
# into the voices and the rest (separate.sh), and clone.sh voices lines in a recorded voice through the VoxCPM2
# engine of gothic-voxcpm (tts-service/deploy/voxcpm, which has to be installed and running). No service: the
# Mac copies a job's files over, runs it and takes the result back. Run from the repository:
# scripts/deploy-gpu.sh dub (copies this directory, then runs this script on the server). Needs no root and
# no Docker changes; a venv of its own with PyTorch (~3 GB download) and the Demucs model (~320 MB, in
# ~/.cache/torch). A separation takes ~2 GB of GPU memory while it runs.
set -euo pipefail
cd "$(dirname "$0")"
UV="${UV:-$HOME/.local/bin/uv}"

[ -x .venv/bin/python ] || "$UV" venv -q --python 3.12 .venv
"$UV" pip install -q --python .venv/bin/python torch==2.5.1 torchaudio==2.5.1 demucs==4.0.1 soundfile==0.13.1
.venv/bin/python separate.py --fetch
echo "installed; separate with ~/gothic-ai/dub/separate.sh <wav> <out dir>, clone with ~/gothic-ai/dub/clone.sh"
