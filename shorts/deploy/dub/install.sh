#!/usr/bin/env bash
# Installs the voice-cloning job of `gothic-shorts dub` on the GPU server: clone.sh voices lines in a recorded
# voice through the VoxCPM2 engine of gothic-voxcpm (tts-service/deploy/voxcpm, which has to be installed and
# running). No service: the Mac copies a job's files over, runs it and takes the result back. Run from the repository:
# scripts/deploy-gpu.sh dub (copies this directory, then runs this script on the server). Needs no root and
# no Docker changes; clone.py uses only Python's standard library and no GPU memory of its own.
set -euo pipefail
cd "$(dirname "$0")"
UV="${UV:-$HOME/.local/bin/uv}"

[ -x .venv/bin/python ] || "$UV" venv -q --python 3.12 .venv
echo "installed; clone with ~/gothic-ai/dub/clone.sh <reference wav> <lines.json> <out dir> [--design <json>]"
