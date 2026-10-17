#!/usr/bin/env bash
# Lines in one voice on the GPU server: clone.sh <reference wav> <lines.json> <out dir> [--design <json>]
# (clone.py; with --design a missing reference is designed from a description first).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
[ $# -eq 3 ] || [ $# -eq 5 ] || { sed -n '2,3p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
exec "$here/.venv/bin/python" "$here/clone.py" "$@"
