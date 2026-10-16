#!/usr/bin/env bash
# Repeat one short, exclusively locked batch on Ryzen. Every runner path is absolute.
set -euo pipefail

if [[ ${1:-} == --help || $# == 0 ]]; then
  cat <<'USAGE'
RUNNER=/absolute/gothic-body-runner SAVE=/absolute/save GOTHIC_DIR=/absolute/game \
OUT=/absolute/results CACHE=/absolute/cache [CV=0|1] [LINKS=/absolute/accepted-links.txt] \
  ./run.sh teacher [START [COUNT]] [--resume]
  ./run.sh probe [START [COUNT]] [--resume]
  ./run.sh coverage

START is zero-based in the original 204 pairs or 34 missing edges. Defaults:
teacher START=0 COUNT=34; probe START=0 COUNT=6. Already completed samples are
skipped automatically (--resume is also accepted). Use separate OUT directories
for CV=0 and CV=1, and for each teacher implementation or set of LINKS.

Each invocation holds ~/gothic-ai/body-simulation.lock for one batch, then exits.
LOCK_WAIT_SECONDS defaults to 60; failure to acquire the lock exits with code 75.
Probe writes OUT/transitions.jsonl and deduplicated OUT/accepted-links.txt. Pass
that accepted-links.txt as LINKS to coverage and the subsequent teacher batches.
USAGE
  exit 0
fi

if [[ $(uname -s) != Linux ]]; then
  echo 'Run measurements on Ryzen/Linux, not on the Mac.' >&2
  exit 2
fi
for name in RUNNER SAVE GOTHIC_DIR OUT CACHE; do
  value=${!name:-}
  if [[ $value != /* ]]; then
    echo "$name must be an absolute path" >&2
    exit 2
  fi
done
if [[ -n ${LINKS:-} && $LINKS != /* ]]; then
  echo 'LINKS must be an absolute path' >&2
  exit 2
fi
export CV=${CV:-0}
export NAV_TEACHER_REPO
NAV_TEACHER_REPO=$(cd -- "$(dirname -- "$0")/../../.." && pwd)
mkdir -p -- "$OUT" "$CACHE" "$HOME/gothic-ai"

flock -w "${LOCK_WAIT_SECONDS:-60}" -E 75 "$HOME/gothic-ai/body-simulation.lock" \
  python3 - "$@" <<'PY'
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

repo = Path(os.environ['NAV_TEACHER_REPO'])
sys.path.insert(0, str(repo / 'scripts'))
from navmesh_measure import Runner, edge_fields, pair_fields, rows, scenario_file

parser = argparse.ArgumentParser(description='One short navigation-teacher batch')
parser.add_argument('stage', choices=('teacher', 'probe', 'coverage'))
parser.add_argument('start', nargs='?', type=int, default=0)
parser.add_argument('count', nargs='?', type=int)
parser.add_argument('--resume', action='store_true', help='accepted; resume is automatic')
options = parser.parse_args()
if options.start < 0 or (options.count is not None and options.count <= 0):
    parser.error('START must be nonnegative and COUNT must be positive')
cv = int(os.environ['CV'])
if cv not in (0, 1):
    parser.error('CV must be 0 or 1')

out = Path(os.environ['OUT'])
cache = Path(os.environ['CACHE'])
links = Path(os.environ['LINKS']) if os.environ.get('LINKS') else None
for path in (cache, links):
    if path is not None and any(char.isspace() for char in str(path)):
        parser.error('CACHE and LINKS cannot contain whitespace (bench command syntax)')
baseline = repo / 'reports/navigation/2026-10-16-navmesh/raw'
pairs_path = baseline / 'pairs.jsonl'
coverage_path = baseline / 'coverage.jsonl'
npc = 'BAU_954_MALETH'
seed = 7
limit = 90000
probe_limit = 6000

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path else None

settings = dict(cv=cv, seed=seed, npc=npc, limit_ms=limit,
                pairs_sha256=digest(pairs_path), coverage_sha256=digest(coverage_path),
                links_sha256=digest(links), runner_sha256=digest(Path(os.environ['RUNNER'])))

def append(path, value):
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(',', ':')) + '\n')

def check_previous(path):
    previous = list(rows(path)) if path.exists() else []
    for row in previous:
        if row.get('settings') != settings:
            raise RuntimeError(f'{path}: settings changed; use a fresh OUT directory')
    return previous

# Reject accidental mixed runs before starting a process. All settings are kept
# with the measurements, rather than in a separate run manifest.
trials_path = out / 'trials.jsonl'
probes_path = out / 'probes.jsonl'
previous_trials = check_previous(trials_path)
previous_probes = check_previous(probes_path)
ini = out / 'run.ini'
ini.write_text(f'[GAME]\nnpc_character_virtual={cv}\nmovable_vobs=0\n', encoding='utf-8')
args = argparse.Namespace(output=out, runner=Path(os.environ['RUNNER']),
                          save=Path(os.environ['SAVE']), assets=Path(os.environ['GOTHIC_DIR']),
                          cache=cache, ini=ini, seed=seed)
build_command = f'navmesh build {cache}' + (f' {links}' if links else '')
prepare = scenario_file(args, 'cache-load', [build_command])

def stuck_count(sample):
    path = out / 'stuck.jsonl'
    return sum(row.get('type') == 'npc_stuck' and row.get('sample') == sample
               and row.get('npc') == npc for row in rows(path)) if path.exists() else 0

def aggregate_probes():
    # A probe's IDs are local. Publish stable IDs across the full resumed batch,
    # deduplicating identical directed endpoints and movement types first.
    unique = {}
    probes = sorted(rows(probes_path), key=lambda row: row['edge']) if probes_path.exists() else []
    with (out / 'transitions.jsonl').open('w', encoding='utf-8') as transitions:
        for probe in probes:
            output = out / 'probes' / probe['sample']
            metadata = {key: probe[key] for key in ('sample', 'gap', 'edge', 'source',
                        'start', 'goal', 'npc', 'cv', 'seed', 'probe_limit_ms', 'settings')}
            for transition in rows(output / 'transitions.jsonl'):
                transition.update(metadata)
                transitions.write(json.dumps(transition, ensure_ascii=False, separators=(',', ':')) + '\n')
            path = output / 'accepted-links.txt'
            if not path.exists():
                continue
            for line in path.read_text(encoding='utf-8').splitlines():
                fields = line.split()
                if not fields:
                    continue
                if len(fields) != 8:
                    raise RuntimeError(f'Invalid off-mesh link in {path}: {line}')
                key = tuple(float(value) for value in fields[:6]) + (int(fields[6]),)
                unique.setdefault(key, fields[:7])
    path = out / 'accepted-links.txt'
    with path.open('w', encoding='utf-8') as stream:
        for index, fields in enumerate(unique.values(), 1):
            stream.write(' '.join(fields + [str(index)]) + '\n')

runner = Runner(args, prepare)
try:
    # The immutable mesh survives fresh-world resets within this one process.
    runner.scenario(prepare, out / 'cache-load', seed, timeout=720)
    if options.stage == 'coverage':
        scenario = scenario_file(args, 'coverage', ['navmesh coverage'])
        runner.scenario(scenario, out / 'coverage', seed, timeout=720)
        coverage = list(rows(out / 'coverage' / 'coverage.jsonl'))
        complete = sum(bool(edge_fields(row)[2]) for row in coverage)
        print(f'coverage: {complete}/{len(coverage)} ({100 * complete / len(coverage):.2f}%)', flush=True)
    elif options.stage == 'teacher':
        pairs = list(rows(pairs_path))
        if len(pairs) != 204:
            raise RuntimeError(f'Expected the exact original 204 pairs, got {len(pairs)}')
        count = options.count if options.count is not None else 34
        completed = {row['pair'] for row in previous_trials}
        for index in range(options.start, min(options.start + count, len(pairs))):
            if index in completed:
                continue
            region, start, goal = pair_fields(pairs[index])
            sample = f'pair-{index:03d}-teacher-cv{cv}'
            scenario = scenario_file(args, sample, [f'goto {start}',
                f'stuck begin {sample} 1000000',
                f'navmesh start teacher {npc} {start} {goal} {limit}',
                'navmesh finish', 'stuck end'])
            output = out / 'trials' / sample
            before = stuck_count(sample)
            runner.scenario(scenario, output, seed)
            trial = json.loads((output / 'trial.json').read_text(encoding='utf-8'))
            trial.update(sample=sample, pair=index, region=region, mode='teacher',
                         start=start, goal=goal, npc=npc, cv=cv, seed=seed, limit_ms=limit,
                         stuck_events=stuck_count(sample) - before, settings=settings)
            append(trials_path, trial)
            print(f'{sample}: success={trial.get("success")} elapsed_ms={trial.get("elapsed_ms")} '
                  f'stuck={trial["stuck_events"]}', flush=True)
        trials = list(rows(trials_path))
        print(f'teacher CV={cv}: {sum(bool(row.get("success")) for row in trials)}/{len(trials)}; '
              f'stuck={sum(row["stuck_events"] for row in trials)}', flush=True)
    else:
        gaps = [(index, row) for index, row in enumerate(rows(coverage_path)) if not edge_fields(row)[2]]
        if len(gaps) != 34:
            raise RuntimeError(f'Expected the original 34 missing edges, got {len(gaps)}')
        count = options.count if options.count is not None else 6
        completed = {row['edge'] for row in previous_probes}
        for gap in range(options.start, min(options.start + count, len(gaps))):
            edge, source = gaps[gap]
            if edge in completed:
                continue
            start, goal, *_ = edge_fields(source)
            sample = f'gap-{gap:02d}-edge-{edge:04d}'
            scenario = scenario_file(args, sample, [f'goto {start}',
                f'stuck begin {sample} 1000000',
                f'navmesh probe {npc} {start} {goal} {probe_limit}', 'stuck end'])
            output = out / 'probes' / sample
            before = stuck_count(sample)
            runner.scenario(scenario, output, seed)
            metadata = dict(sample=sample, gap=gap, edge=edge, source=source,
                            start=start, goal=goal, npc=npc, cv=cv, seed=seed,
                            probe_limit_ms=probe_limit, settings=settings)
            result = json.loads((output / 'probe.json').read_text(encoding='utf-8'))
            # Read before recording completion: a missing/invalid native output
            # must remain eligible for a fresh-world retry on the next batch.
            list(rows(output / 'transitions.jsonl'))
            result.update(metadata, stuck_events=stuck_count(sample) - before)
            append(probes_path, result)
            aggregate_probes()
            print(f'{sample}: {json.dumps(result, ensure_ascii=False, separators=(",", ":"))}', flush=True)
        aggregate_probes()
finally:
    if sys.exc_info()[0] is None:
        runner.close()
    else:
        try:
            runner.close()
        except Exception as exc:
            print(f'Runner cleanup after failure: {exc}', file=sys.stderr)
PY
