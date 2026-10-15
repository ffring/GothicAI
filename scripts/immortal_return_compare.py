#!/usr/bin/env python3
"""Check real melee KO, human return eligibility and exclusion controls."""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def distance(a, b):
    assert len(a) == len(b) == 3 and all(math.isfinite(v) for v in a + b)
    return math.dist(a, b)


def percentile(values, q):
    return sorted(values)[math.ceil(len(values) * q) - 1]


def validate(run):
    events = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
    assert not [e for e in events if e['kind'] in {'ragdoll_error', 'immortal_guard_error'}], 'game reported an error'
    assert 'done' in (run / 'bench.log').read_text().splitlines(), 'scenario did not finish'
    rows = list(csv.DictReader((run / 'metrics.csv').open()))
    expected = {'mortal_cliff': (False, True, 1), 'mortal_water': (False, True, 2),
                'safe_ko': (False, True, 0), 'guard_off_cliff': (False, False, 1)}
    result = {}
    for label, (immortal, enabled, hazard) in expected.items():
        case = [e for e in events if e.get('case') == label]
        setup = [e for e in case if e['kind'] == 'immortal_guard_case']
        launch = [e for e in case if e['kind'] == 'immortal_guard_launch']
        end = [e for e in case if e['kind'] == 'immortal_guard_end']
        trace = [e for e in case if e['kind'] == 'immortal_guard_frame']
        returns = [e for e in case if e['kind'] == 'immortal_guard_return']
        assert len(setup) == len(launch) == len(end) == 1, (label, 'missing case boundary')
        setup, launch, end = setup[0], launch[0], end[0]
        assert setup['immortal'] is immortal and setup['enabled'] is enabled, label
        assert launch['immortal'] is immortal, label
        damage = [e for e in case if e['kind'] == 'immortal_guard_damage']
        assert len(damage) == 1 and damage[0]['melee'] and damage[0]['hp_before'] == 2 and damage[0]['hp_after'] == 1
        assert damage[0]['unconscious'] and not damage[0]['dead'], (label, 'real melee did not produce KO')
        assert any(e['kind'] == 'note' and e['seg'] == label and ', melee, by PC_HERO' in e['text'] for e in events), (label, 'missing production melee damage')
        assert trace and any(e['unconscious'] and e['parts'] == 24 for e in trace), (label, 'no real human native KO')
        assert all(e['immortal'] is immortal and e['enabled'] is enabled for e in trace), label
        standing = [e for e in case if e['kind'] == 'immortal_guard_standing']
        assert len(standing) == 1 and standing[0]['standing'] and end['standing'], (label, 'actual standing not reached')
        assert not end['dead'] and not end['unconscious'] and end['parts'] == 0, (label, 'did not get up')
        stable = launch['stable_cm']
        hazard_events = [e for e in case if e['kind'] == 'immortal_guard_hazard']
        assert len(hazard_events) == (1 if hazard else 0), (label, 'physical hazard checkpoint count')
        witness = hazard_events[0] if hazard else next(e for e in trace if e['parts'])
        assert witness['unconscious'] and witness['parts'] == 24, (label, 'hazard was not a native KO')
        actual_drop = max(stable[1] - e['pelvis_cm'][1] for e in trace + [witness] if e['parts'])
        if hazard == 1:
            assert stable[1] - witness['pelvis_cm'][1] > 1000 and end['drop_observed'], (label, 'no 10 m fall')
        elif hazard == 2:
            assert end['water_observed'], (label, 'body did not enter real water')
            assert witness['water_y_cm'] is not None and witness['pelvis_cm'][1] < witness['water_y_cm'] - 5
        checkpoints = [e for e in case if e['kind'] == 'immortal_guard_checkpoint']
        if hazard and enabled:
            assert end['standing'] and not end['armed'], (label, 'return did not finish standing/disarmed')
            assert distance(end['position_cm'], stable) <= 3, (label, 'completed get-up moved away from anchor', distance(end['position_cm'], stable))
            assert len(returns) == 1 and end['returns'] == 1, (label, 'missing or repeated return')
            returned = returns[0]
            assert distance(stable, returned['anchor_cm']) <= 3
            assert distance(returned['to_cm'], returned['anchor_cm']) <= 3, (label, 'returned away from anchor')
            assert distance(returned['from_cm'], returned['to_cm']) > 100, (label, 'no actual relocation')
            assert len(checkpoints) == 2 and [e['label'] for e in checkpoints] == ['before_save', 'after_load']
            before, after = checkpoints
            assert before['has_anchor'] and before['armed'] and before['hazards'] & hazard, label
            for field in ('anchor_cm', 'angle', 'has_anchor', 'armed', 'hazards'):
                assert before[field] == after[field], (label, 'save/load lost ' + field)
            assert returned['reason'] in ({'drop', 'drop+water'} if hazard == 1 else {'water', 'drop+water'}), label
        else:
            assert not returns and end['returns'] == 0, (label, 'unguarded KO returned')
            if hazard:
                assert distance(end['position_cm'], stable) > 500, (label, 'control returned to initial support')
            else:
                assert not end['drop_observed'] and not end['water_observed'], (label, 'safe KO became hazardous')
        recorded = [row for row in rows if row['seg'] == label]
        assert recorded and len(recorded) == len(trace), (label, 'CSV/trace coverage differs')
        assert [int(e['frame']) for e in trace] == list(range(len(trace))), label
        for row, frame in zip(recorded, trace):
            assert int(row['frame']) == frame['frame']
            assert int(row['ig_returns']) == frame['returns'] and int(row['ig_hazards']) == frame['hazards']
            assert row['ig_reason'] == frame['reason']
            assert distance([float(row['ig_' + axis]) for axis in 'xyz'], frame['position_cm']) < .01
        physics = [float(row['ph_ms']) for row in recorded if 'ph_ms' in row]
        wall = [float(row['ig_wall_ms']) for row in recorded if float(row['ig_wall_ms']) > 0]
        assert all(math.isfinite(v) and v >= 0 for v in physics + wall)
        result[label] = {'frames': len(trace), 'immortal': immortal, 'enabled': enabled,
                         'max_drop_cm': actual_drop, 'stable_cm': stable, 'final_cm': end['position_cm'],
                         'hp_before': damage[0]['hp_before'], 'hp_after': damage[0]['hp_after'],
                         'returns': len(returns), 'reason': returns[0]['reason'] if returns else 'none',
                         'ph_ms_mean': statistics.mean(physics) if physics else None,
                         'ph_ms_p95': percentile(physics, .95) if physics else None,
                         'ph_ms_max': max(physics) if physics else None}
    for label, hero in (('hero_control', True), ('monster_control', False)):
        case = [e for e in events if e.get('case') == label and e['kind'] == 'immortal_guard_excluded']
        assert len(case) == 1, (label, 'missing exclusion probe')
        probe = case[0]
        assert probe['hero'] is hero and (probe['human'] if hero else probe['monster']), label
        assert not probe['has_anchor'] and not probe['armed'] and probe['returns'] == 0, (label, 'excluded actor protected')
        result[label] = {'human': probe['human'], 'hero': hero, 'monster': probe['monster'], 'has_anchor': False, 'armed': False, 'returns': 0}
    return {'pass': True, 'cases': result, 'timing_note': 'native step timing unavailable when OPENGOTHIC_RAGDOLL_TRACE is disabled; timing is informative'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = validate(args.run)
    except (AssertionError, KeyError, ValueError, TypeError, OSError) as error:
        result = {'pass': False, 'error': str(error)}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
