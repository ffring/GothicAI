#!/usr/bin/env bash
# One forced alignment on the GPU server: align.sh <16 kHz mono wav> <text file> <language> <words.json>
# The text file holds the exact words spoken; the language is a name or a code (Russian, ru). Writes
# audio.cpp's word list: [{"start_sample", "end_sample", "word", "confidence"}], samples of the wav.
# The model loads per run (~1.5 GB of GPU memory while it runs) and is gone when the job ends.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
[ $# -eq 4 ] || { sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
exec "$here/audio.cpp/build/linux-cuda-release/bin/audiocpp_cli" --task align \
  --family qwen3_forced_aligner --model "$here/models/qwen3-forced-aligner-q8_0.gguf" \
  --backend cuda --audio "$1" --text "$(cat "$2")" --language "$3" \
  --request-option clamp_timestamps_to_audio=true --words-out "$4" --metrics
