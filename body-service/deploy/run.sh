#!/usr/bin/env bash
# Run from the isolated ~/gothic-ai/body-net deployment, beside scripts/.
set -euo pipefail
PACKAGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_ROOT="$(dirname "$PACKAGE_ROOT")"
ENV_FILE="$HOME/gothic-ai/gothic-ai.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  . "$ENV_FILE"
  set +a
fi
: "${BODY_HOST:?set BODY_HOST in ~/gothic-ai/gothic-ai.env}"
cd "$DEPLOY_ROOT"
exec .venv/bin/gothic-body serve-net \
  --checkpoint "${BODY_CHECKPOINT:-$HOME/gothic-ai/body/models/working/best.pt}" \
  --host "$BODY_HOST" --port "${BODY_PORT:-47835}" \
  --device cpu --inference-cpu-threads 2 "$@"
