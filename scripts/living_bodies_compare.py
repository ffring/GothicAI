#!/usr/bin/env python3
"""Check living-body capture, physics flag toggles, or matched OFF/ON measurements."""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def percentile(values, q=.95):
    assert values and all(math.isfinite(v) and v >= 0 for v in values)
    return sorted(values)[math.ceil(len(values) * q) - 1]


def events(run):
    data = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
    assert 'done' in (run / 'bench.log').read_text().splitlines(), 'scenario unfinished'
    assert not any(e['kind'] == 'ragdoll_error' for e in data), 'game reported a fixture error'
    return data


def check_counts(e, mode):
    ko = mode == 'ko'
    on = mode in ('on', 'ko')
    assert e['models'] == 10 and e['alive'] == (9 if ko else 10), e
    assert e['duplicates'] == 0, e
    assert e['kinematic'] == (9 if ko else 10) * on, e
    assert e['parts'] == (216 if ko else 240) * on, e
    assert e['dynamic'] == int(ko) and e['dynamic_parts'] == 24 * ko, e


def travel(points):
    assert points
    return max(math.dist(points[0], p) for p in points)


def functional(run):
    data = events(run)
    checks = [e for e in data if e['kind'] == 'living_check']
    modes = ['off', 'on', 'far', 'on', 'off', 'on', 'ko', 'on']
    assert [e['mode'] for e in checks] == modes, checks
    for e, mode in zip(checks, modes):
        check_counts(e, mode)
        assert e['pass'] and e['ordinary_hits'] == e['alive'], e
    assert checks[0]['world_bodies'] == checks[4]['world_bodies'], 'OFF retained native bodies'
    assert checks[1]['world_bodies'] == checks[5]['world_bodies'] == checks[6]['world_bodies'] == checks[7]['world_bodies'], 'handoff leaked or duplicated bodies'
    frames = [e for e in data if e['kind'] == 'living_frame']
    assert frames and all(e['models'] == 10 and e['duplicates'] == 0 for e in frames)
    assert all(e['parts'] == e['kinematic'] * 24 and e['dynamic_parts'] == e['dynamic'] * 24 for e in frames)
    motion = {}
    for segment in ('living_off', 'living_on'):
        recorded = [e for e in frames if e['seg'] == segment]
        assert recorded and (run / f'{segment}.mp4').is_file()
        motion[segment] = []
        for index in range(4):
            actors = [e['actors'][index] for e in recorded]
            moved = travel([a['position_cm'] for a in actors])
            assert moved > 50, (segment, index, 'walker did not actually move', moved)
            motion[segment].append(moved)
            if segment == 'living_on':
                native = [a['native_pelvis_cm'] for a in actors if 'native_pelvis_cm' in a]
                assert travel(native) > 50, (index, 'kinematic body did not follow movement')
    return {'pass': True, 'checks': checks, 'walker_displacement_cm': motion,
            'note': 'Overlay shows gameplay hit zones; counts are actual native kinematic parts.'}


def physics_flags(data):
    names = ('ragdoll', 'ragdoll_quest_guard', 'living_bodies',
             'arrow_physics', 'arrow_sticking', 'spell_physics')
    rows = [e for e in data if e['kind'] == 'physics_flags_check']
    expected = [(name, phase) for name in names for phase in ('off', 'on', 'restore')]
    assert [(e['flag'], e['phase']) for e in rows] == expected, 'missing or unordered flag toggles'
    assert len({e['t'] for e in rows}) == 1, 'flags check advanced the game clock'
    for row in rows:
        assert row['pass'] and row['prepared'] and row['same_dispatch'], row
        assert type(row['cached']) is bool and row['cached'] == (row['configured'] != 0), row
        if row['phase'] == 'restore':
            assert row['configured'] == row['original'], row
        else:
            assert row['configured'] == int(row['phase'] == 'on'), row
    return rows


def flags(run):
    living = functional(run)
    return {'pass': True, 'functional': living, 'flags': physics_flags(events(run))}


def radius(run):
    data = events(run)
    rows = [e for e in data if e['kind'] == 'living_radius_check']
    distances = (1950, 2010, 2190, 2210, 2100, 2010, 1990, 2010)
    present = (True, True, True, False, False, False, True, True)
    assert len(rows) == len(distances), 'missing radius checks'
    for row, distance, expected in zip(rows, distances, present):
        assert abs(row['distance_cm'] - distance) < 1, row
        assert row['expected'] == row['present'] == expected, row
    return {'pass': True, 'checks': rows, 'enter_radius_cm': 2000, 'remove_radius_cm': 2200}


def measured(run, mode):
    data = events(run)
    before = [e for e in data if e['kind'] == 'living_measure_before']
    after = [e for e in data if e['kind'] == 'living_measure_after']
    assert len(before) == len(after) == 1
    check_counts(before[0], mode)
    check_counts(after[0], mode)
    assert not after[0]['overflow']
    meta = json.loads((run / 'appcadence.csv.json').read_text())
    assert meta['mode'] == mode and meta['clock'] == 'normal'
    assert meta['complete'] and meta['overflow'] == 0
    assert meta['window_end_ns'] - meta['start_ns'] == 30_000_000_000
    rows = list(csv.DictReader((run / 'appcadence.csv').open()))
    assert rows and len(rows) == meta['frames']
    prior = meta['start_ns']
    intervals = []
    for i, row in enumerate(rows):
        end, delta = int(row['end_ns']), int(row['interval_ns'])
        assert int(row['frame']) == i and end > prior and delta == end - prior
        if i + 1 < len(rows):
            assert end < meta['window_end_ns']
        intervals.append(delta / 1_000_000)
        prior = end
    assert prior == meta['final_end_ns'] and prior >= meta['window_end_ns']
    physics = list(csv.DictReader((run / 'living-physics.csv').open()))
    assert physics
    times = [int(row['t_game_ms']) for row in physics]
    assert all(b > a for a, b in zip(times, times[1:]))
    assert all(int(row['sample']) == i and int(row['dt_ms']) > 0 for i, row in enumerate(physics))
    costs = [float(row['step_ms']) for row in physics]
    return {'frames': len(rows), 'mean_fps': len(rows) / ((prior - meta['start_ns']) / 1e9),
            'frame_p95_ms': percentile(intervals), 'frame_max_ms': max(intervals),
            'physics_samples': len(costs), 'physics_mean_ms': statistics.mean(costs),
            'physics_p95_ms': percentile(costs), 'physics_max_ms': max(costs),
            'before': before[0], 'after': after[0]}


def performance(directory):
    pairs = []
    for i in range(1, 4):
        off = measured(directory / f'run{i}-off', 'off')
        on = measured(directory / f'run{i}-on', 'on')
        pairs.append({'pair': i, 'off': off, 'on': on,
                      'fps_drop_percent': (1 - on['mean_fps'] / off['mean_fps']) * 100})
    off_fps = statistics.median(p['off']['mean_fps'] for p in pairs)
    on_fps = statistics.median(p['on']['mean_fps'] for p in pairs)
    drop = (1 - on_fps / off_fps) * 100
    physics_p95 = statistics.median(p['on']['physics_p95_ms'] for p in pairs)
    return {'pass': drop <= 5 and physics_p95 <= 2, 'pairs': pairs,
            'median_off_fps': off_fps, 'median_on_fps': on_fps,
            'fps_drop_percent': drop, 'median_on_physics_p95_ms': physics_p95,
            'measurement': 'Normal-game app-frame completion cadence; native CollisionWorld tick cost per positive world tick. Maxima informational.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('functional', 'performance', 'radius', 'flags'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = {'functional': functional, 'performance': performance, 'radius': radius,
                  'flags': flags}[args.mode](args.directory)
    except (AssertionError, KeyError, ValueError, OSError, TypeError) as error:
        result = {'pass': False, 'error': str(error)}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
