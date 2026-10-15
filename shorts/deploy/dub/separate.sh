#!/usr/bin/env bash
# One separation on the GPU server: separate.sh <wav> <out dir> -> <out dir>/voices.wav, rest.wav (separate.py).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
[ $# -eq 2 ] || { sed -n '2p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
exec "$here/.venv/bin/python" "$here/separate.py" "$1" "$2"
