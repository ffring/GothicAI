#!/usr/bin/env python3
"""Validate one real WindFist cliff throw of a living mortal human."""
import argparse
import json
import math
import re
from pathlib import Path


def validate(run):
    events = [json.loads(line) for line in (run/'events.jsonl').read_text().splitlines()]
    assert 'done' in (run/'bench.log').read_text().splitlines(), 'unfinished scenario'
    errors = [e for e in events if e['kind'] == 'ragdoll_error']
    assert not errors, errors
    setup = [e for e in events if e['kind'] == 'spell_guard_setup']
    assert len(setup) == 1, 'missing cliff setup'
    setup = setup[0]
    stable = setup['stable_cm']
    assert setup['support_margin_cm'] >= 50 and stable[1]-setup['cliff_floor_cm'][1] > 1200
    segment = [e for e in events if e['seg'] == 'wind_cliff']
    assert len([e for e in segment if e['kind'] == 'rec_stop']) == 1, 'incomplete capture'
    casts = [e for e in segment if e['kind'] == 'spell_physics_cast']
    assert len(casts) == 1 and casts[0]['id'] == 26 and casts[0]['begin_result'] in (2, 3), 'real WindFist cast rejected'
    rune = next(e for e in events if e['kind'] == 'spell_physics_rune' and e['id'] == 26)
    assert rune['projectile'] and casts[0]['active_item'] == rune['class_id'], 'wrong projectile rune'
    notes = [e['text'] for e in segment if e['kind'] == 'note']
    impacts = [text for text in notes if text.startswith('spell physics impact id 26 ')]
    assert len(impacts) == 1 and int(impacts[0].split(' npc ')[1].split()[0]) >= 1, 'no actual projectile wind impact'
    assert any(', spell, by PC_HERO' in text and 'damage BAU_904_BAUER:' in text for text in notes), 'no real spell damage'
    impulses = [text for text in notes if text.startswith('spell physics wind BAU_904_BAUER mass ')]
    assert len(impulses) == 1, 'missing actual target impulse'
    match = re.search(r'mass (\S+) impulse (\S+) (\S+) (\S+)', impulses[0]);assert match
    mass = float(match[1]);impulse = [float(match[i]) for i in (2, 3, 4)]
    assert abs(mass-75) < .1 and math.sqrt(sum(x*x for x in impulse)) > 0
    rows = [e for e in segment if e['kind'] == 'spell_guard_frame']
    assert rows and all(e['human'] and not e['hero'] and not e['immortal'] and not e['dead'] and not e['unconscious'] and e['hp'] > 1 for e in rows), 'actor was not always a living mortal human'
    assert rows[0]['has_anchor'] and not rows[0]['armed'] and math.dist(rows[0]['anchor_cm'], stable) <= 3, 'no pre-hit stable anchor'
    assert any(e['in_air'] and e['armed'] for e in rows), 'spell did not arm a real airborne throw'
    before = [e for e in rows if e['returns'] == 0]
    drop = max(stable[1]-e['position_cm'][1] for e in before)
    assert drop > 1000 and any(e['in_air'] and e['hazards'] & 1 for e in before), ('no real 10 m fall', drop)
    landings = [e for e in segment if e['kind'] == 'spell_guard_landing']
    assert len(landings) == 1 and landings[0]['returns'] == 1, 'missing or repeated return'
    landing = landings[0]
    assert landing['has_floor'] and landing['floor_normal'][1] > .5, 'return source has no landing surface'
    floor_error = abs(landing['from_cm'][1]-landing['floor_cm'][1])
    assert floor_error <= 30, ('returned before reaching ground', floor_error)
    assert stable[1]-landing['from_cm'][1] > 1000 and math.dist(landing['to_cm'], stable) <= 3
    last = rows[-1]
    assert max(e['returns'] for e in rows) == last['returns'] == 1 and last['standing'] and not last['in_air'] and not last['armed'], 'return did not finish standing once'
    error = math.dist(last['position_cm'], stable)
    assert error <= 3, ('return moved away from stable anchor', error)
    return {'pass': True, 'samples': len(rows), 'spell_id': 26, 'mass_kg': mass, 'impulse_ns': impulse,
            'max_drop_cm': drop, 'returns': 1, 'landing_floor_error_cm': floor_error,
            'final_anchor_error_cm': error, 'standing': True, 'hp_final': last['hp']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path);parser.add_argument('--output', required=True, type=Path)
    args=parser.parse_args()
    try: result=validate(args.directory)
    except (AssertionError, KeyError, ValueError, OSError, StopIteration) as error:
        result={'pass': False, 'error': str(error)}
    args.output.write_text(json.dumps(result, indent=2)+'\n');print(json.dumps(result, indent=2))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
