#!/bin/sh
# Narrow CPU checks. uv may fetch test dependencies if Python has no pytest.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=${PYTHON:-python3}
set -- -q -p no:cacheprovider \
  "$script_dir/test_concepts.py" "$script_dir/test_measure_targets.py" "$script_dir/../test_registry.py"
if "$python" -c 'import pytest' >/dev/null 2>&1; then
  exec "$python" -m pytest "$@"
fi
exec uv run --with pytest --with pillow --with numpy python -m pytest "$@"
