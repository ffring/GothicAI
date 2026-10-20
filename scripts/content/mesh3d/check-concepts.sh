#!/bin/sh
# Narrow, offline CPU checks. No generator, game, GPU or network is invoked.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=${PYTHON:-python3}
exec "$python" -m pytest -q -p no:cacheprovider \
  "$script_dir/test_concepts.py" "$script_dir/test_measure_targets.py" "$script_dir/../test_registry.py"
