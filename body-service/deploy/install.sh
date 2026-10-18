#!/usr/bin/env bash
# Prepare an isolated CPU environment and install the systemd user unit.
# --live-cores also pins the unit to the live cores (Ryzen, docs/compute.md).
# This deliberately leaves service start/enable to the operator after review.
set -euo pipefail
if [ "$(uname -s)" != Linux ]; then
  echo "Install the body policy service on Ryzen or dev-ubuntu, never on the Mac" >&2
  exit 1
fi
PACKAGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_ROOT="$(dirname "$PACKAGE_ROOT")"
UV="${UV:-$HOME/.local/bin/uv}"
PY="$DEPLOY_ROOT/.venv/bin/python"
if [ ! -f "$DEPLOY_ROOT/scripts/body_trajectory_dataset.py" ]; then
  echo "Copy scripts/ beside body-service/ before installing" >&2
  exit 1
fi
[ -x "$PY" ] || "$UV" venv --python 3.12 "$DEPLOY_ROOT/.venv"
"$UV" pip install --python "$PY" 'torch>=2.5,<3' --index-url https://download.pytorch.org/whl/cpu
"$UV" pip install --python "$PY" -e "$PACKAGE_ROOT"

mkdir -p "$HOME/.config/systemd/user"
cp "$PACKAGE_ROOT/deploy/gothic-body.service" "$HOME/.config/systemd/user/"
if [ "${1:-}" = --live-cores ]; then
  mkdir -p "$HOME/.config/systemd/user/gothic-body.service.d"
  cp "$PACKAGE_ROOT/deploy/live-cores.conf" "$HOME/.config/systemd/user/gothic-body.service.d/"
fi
systemctl --user daemon-reload
echo "installed; start explicitly with: systemctl --user enable --now gothic-body.service"
