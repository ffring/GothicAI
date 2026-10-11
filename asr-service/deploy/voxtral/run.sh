#!/usr/bin/env bash
# Voxtral Mini 4B Realtime (Q4_K) on audio.cpp's HTTP server, for the Gothic AI ASR service.
# Listens on VOXTRAL_HOST only (a VPN address, set in ~/gothic-ai/gothic-ai.env). ~3.5 GB of GPU memory;
# streamed push-to-talk audio is transcribed ~0.15 s after the player stops speaking.
set -euo pipefail
cd "$(dirname "$0")"
# busy_timeout_ms: a live request holds the model for the whole utterance (push-to-talk caps it at 30 s),
# so only a request stuck longer than that makes the next one fail fast instead of queueing.
cat > server.json <<EOF
{
  "host": "${VOXTRAL_HOST:-127.0.0.1}",
  "port": ${VOXTRAL_PORT:-47830},
  "backend": "cuda",
  "device": 0,
  "threads": 4,
  "lazy_load": false,
  "busy_timeout_ms": 45000,
  "models": [
    {"id": "voxtral-realtime", "family": "voxtral_realtime", "task": "asr", "mode": "streaming",
     "path": "$PWD/models/voxtral-q4_k.gguf"}
  ]
}
EOF
exec audio.cpp/build/linux-cuda-release/bin/audiocpp_server --config server.json
