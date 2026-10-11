#!/usr/bin/env bash
# Installs Voxtral Mini 4B Realtime (vLLM) on the GPU server as a systemd user service.
# Run on the server (dev-ubuntu: RTX 3090, CUDA toolkit in /usr/local/cuda, uv in ~/.local/bin):
#   scp -r asr-service/deploy/voxtral dev-ubuntu:gothic-ai/ && ssh dev-ubuntu gothic-ai/voxtral/install.sh
# Needs no root and no Docker changes. First start downloads the model (~9 GB) and JIT-compiles
# FlashInfer kernels (~2 min). Logs: journalctl --user -u gothic-voxtral -f
set -euo pipefail
cd "$(dirname "$0")"
UV="${UV:-$HOME/.local/bin/uv}"

"$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python -U vllm --torch-backend=auto
"$UV" pip install --python .venv/bin/python -U "mistral-common[audio]>=1.9.0" transformers

mkdir -p ~/.config/systemd/user
cp gothic-voxtral.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gothic-voxtral.service
echo "started; ready when the log says 'Application startup complete'"
