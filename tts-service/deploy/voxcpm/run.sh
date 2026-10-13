#!/usr/bin/env bash
# VoxCPM2 TTS for the Gothic AI TTS service: audio.cpp's server runs the model (Q8_0, ~3.2 GB of GPU memory)
# on 127.0.0.1 only, and server.py, the WebSocket bridge the TTS service talks to, listens on VOXCPM_HOST
# (a VPN address, set in ~/gothic-ai/gothic-ai.env). Either one exiting stops the unit, and systemd
# restarts both.
set -uo pipefail
cd "$(dirname "$0")"
ENGINE_PORT="${VOXCPM_ENGINE_PORT:-47836}"
# prompt_cache_slots: encoded reference clips kept between lines, one per npc voice; a miss costs ~0.15 s
cat > server.json <<EOF
{
  "host": "127.0.0.1",
  "port": $ENGINE_PORT,
  "backend": "cuda",
  "device": 0,
  "threads": 4,
  "lazy_load": false,
  "busy_timeout_ms": 60000,
  "models": [
    {"id": "voxcpm2", "family": "voxcpm2", "task": "tts", "mode": "streaming",
     "path": "$PWD/models/voxcpm2-q8_0.gguf",
     "session_options": {"voxcpm2.prompt_cache_slots": 64}}
  ]
}
EOF
audio.cpp/build/linux-cuda-release/bin/audiocpp_server --config server.json --no-ui &
.venv/bin/python server.py --host "${VOXCPM_HOST:-127.0.0.1}" --port "${VOXCPM_PORT:-47831}" \
  --engine "http://127.0.0.1:$ENGINE_PORT" &
status=0
wait -n || status=$?
kill $(jobs -p) 2>/dev/null
exit "$((status ? status : 1))"
