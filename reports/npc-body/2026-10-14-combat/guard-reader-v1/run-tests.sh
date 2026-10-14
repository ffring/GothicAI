#!/usr/bin/env bash
set -euo pipefail
out=/home/kaifat/gothic-ai/body/data/stage8-guard-reader-v1
python=/home/kaifat/gothic-ai/body/.venv/bin/python
exec 9>/home/kaifat/gothic-ai/body-simulation.lock
if ! flock -w 60 9; then
  echo 'Shared body lock unavailable: no test process started' > "$out/lock-refusal.txt"
  exit 75
fi
"$python" - "$out" <<'PY'
import hashlib,json,sys
from pathlib import Path
out=Path(sys.argv[1]);manifest=json.loads((out/'manifest.json').read_text())
for name,expected in manifest['files'].items():
 assert hashlib.sha256((out/name).read_bytes()).hexdigest()==expected,'Snapshot changed: '+name
PY
snapshot() {
"$python" - "$out" "$1" <<'PY'
import datetime,json,subprocess,sys
from pathlib import Path
out=Path(sys.argv[1]);names=['gothic-voxtral','gothic-voxcpm','gothic-decider','gothic-gemma']
v={'recorded_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'services':{name:subprocess.check_output(['systemctl','--user','show',name,'-p','ActiveState','-p','SubState','-p','MainPID'],text=True).strip() for name in names},'gpu':subprocess.check_output(['nvidia-smi','--query-gpu=memory.used,memory.free,utilization.gpu','--format=csv,noheader'],text=True).strip()}
(out/('services-'+sys.argv[2]+'.json')).write_text(json.dumps(v,indent=2)+'\n')
PY
}
snapshot before
set +e
systemd-run --user --wait --pipe --collect --unit=gothic-body-guard-reader-v1-tests \
 --property=CPUQuota=200% --property=MemoryMax=6G --property=Nice=10 --property=TasksMax=128 --property=RuntimeMaxSec=180 \
 --working-directory="$out" --setenv=CUDA_VISIBLE_DEVICES= --setenv="PYTHONPATH=$out/code/scripts:$out/code/body-service/src" \
 --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=DISPLAY= --setenv=WAYLAND_DISPLAY= \
 /usr/bin/time -v -o "$out/resources.txt" "$python" -m pytest -p no:cacheprovider \
 "$out/code/scripts/tests/test_body_final_evaluation.py" "$out/code/scripts/tests/test_body_combat_report.py" \
 "$out/code/scripts/tests/test_body_stage8_final_report.py" -q --junitxml="$out/junit.xml" > "$out/launcher.log" 2>&1
result=$?
set -e
snapshot after
printf '%s\n' "$result" > "$out/exit-status.txt"
exit "$result"
