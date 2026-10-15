#!/usr/bin/env python3
"""Read native arrow-reaction evidence and the unchanged aim scenarios."""
import argparse
import collections
import json
import math
import re
import statistics
from pathlib import Path


def events(run):
    assert 'done' in (run / 'bench.log').read_text().splitlines(), f'{run}: unfinished'
    result = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
    assert not any(e['kind'] == 'ragdoll_error' for e in result), f'{run}: fixture error'
    return result


def unit(v):
    size = math.sqrt(sum(x*x for x in v))
    assert math.isfinite(size) and size > 1e-6
    return [x/size for x in v]


def basis(matrix):
    assert len(matrix) == 16 and all(math.isfinite(x) for x in matrix)
    return [unit(matrix[i:i+3]) for i in (0, 4, 8)]


def local_basis(matrix, parent):
    child, root = basis(matrix), basis(parent)
    return [[sum(a*b for a, b in zip(axis, ref)) for ref in root] for axis in child]


def pose_error(bone):
    native, animation = bone['native'], bone['animation']
    if 'native_parent' in bone:
        n = local_basis(native, bone['native_parent'])
        a = local_basis(animation, bone['animation_parent'])
    else:
        n, a = basis(native), basis(animation)
    cosine = (sum(sum(x*y for x, y in zip(na, aa)) for na, aa in zip(n, a))-1)/2
    angle = math.degrees(math.acos(max(-1, min(1, cosine))))
    return angle, math.dist(native[12:15], animation[12:15])


def chest_recovery(active, rows):
    """Recover the dynamic spine while head/arms retain the ready world pose."""
    groups = {'spine': [], 'head': [], 'left_arm': [], 'right_arm': []}
    previous = {row['t_game_ms']: before for before, row in zip(rows, rows[1:])}
    for bone in active[-1]['bones']:
        name = bone['name'].upper().replace('_', ' ')
        if 'SPINE' in name:
            group = 'spine'
        elif any(token in name for token in ('NECK', 'HEAD', 'JAW', 'KIEFER')):
            group = 'head'
        elif any(token in name for token in ('CLAVICLE', 'UPPERARM', 'FOREARM', 'HAND', 'FINGER')):
            group = 'left_arm' if ' L ' in name else 'right_arm' if ' R ' in name else None
        else:
            group = None
        if group is None:
            continue
        values = [pose_error(b) for row in active for b in row['bones'] if b['id'] == bone['id']]
        peak_angle, peak_cm = max(v[0] for v in values), max(v[1] for v in values)
        last_angle, last_cm = values[-1]
        dynamic = any(bone['id'] in row['reaction_bones'] for row in active)
        entry = {'name': bone['name'], 'peak_angle_deg': peak_angle, 'peak_cm': peak_cm,
                 'last_angle_deg': last_angle, 'last_cm': last_cm, 'active': dynamic}
        if group == 'spine':
            assert last_angle < peak_angle*.6+.5 and last_cm < peak_cm*.6+.5, (
                'chest spine did not recover before reset', bone['name'], peak_angle, peak_cm, last_angle, last_cm)
        else:
            assert not dynamic, ('chest activated head or arms', bone['name'])
            # Physics consumes the last completed animation solve. Compare to
            # that ready WORLD frame, without a rotating physical parent basis.
            # The current row's animation is already queued for the next step.
            world_errors = []
            for row in active:
                before = previous.get(row['t_game_ms'])
                if before is None:
                    continue
                native = next(b['native'] for b in row['bones'] if b['id'] == bone['id'])
                target = next(b['animation'] for b in before['bones'] if b['id'] == bone['id'])
                world_errors.append(pose_error({'native': native, 'animation': target}))
            assert world_errors, ('missing ready animation reference', bone['name'])
            angle = max(v[0] for v in world_errors)
            distance = max(v[1] for v in world_errors)
            assert angle < 1 and distance < .05, ('chest head/arm world pose sag', bone['name'], angle, distance)
            entry.update(world_kinematic_angle_deg=angle, world_kinematic_error_cm=distance)
        groups[group].append(entry)
    assert all(groups.values()), ('chest upper-body groups missing', groups)
    return groups


def functional(run):
    data = events(run)
    cases = ('off', 'weak_shoulder', 'strong_shoulder', 'chest', 'leg', 'repeated', 'ko', 'death')
    assert [e['case'] for e in data if e['kind'] == 'arrow_reaction_case'] == list(cases)
    # Feature/setup commands between recordings may still carry the old case
    # label. A case's evidence is its explicitly recorded segment only.
    frames = [e for e in data if e['kind'] == 'arrow_reaction_frame' and e['seg'] == e['case']]
    result = {}
    for name in cases:
        rows = [e for e in frames if e['case'] == name]
        assert len(rows) > 20, (name, 'missing native frames')
        assert all(b['t_game_ms'] > a['t_game_ms'] for a, b in zip(rows, rows[1:]))
        notes = [e.get('text', '') for e in data if e['kind'] == 'note' and e['seg'] == name]
        actual = [s for s in notes if s.startswith('arrow hit BAU_904_BAUER:')]
        assert len(actual) == (2 if name == 'repeated' else 1), (name, 'real arrow hits', actual)
        assert all(not (r['living_parts'] and r['corpse_parts']) for r in rows), (name, 'duplicate rigs')
        if name == 'off':
            assert all(not r['feature'] and r['weight'] == 0 and r['kinematic'] for r in rows)
            assert any(r['flinch_active'] for r in rows), 'OFF lost the original flinch'
            result[name] = {'real_hits': len(actual), 'old_flinch': True}
            continue
        start_hits = next(e['hits_before'] for e in data if e['kind'] == 'arrow_reaction_case' and e['case'] == name)
        delivered = rows[-1]['hits'] - start_hits
        assert delivered == len(actual), (name, 'hit metadata differs from projectile result', delivered)
        assert rows[-1]['hits'] == rows[-1]['applied'], (name, 'pending hit lost or duplicated')
        if name in ('ko', 'death'):
            down = [r for r in rows if r['ko'] or r['dead']]
            assert down and down[-1]['corpse_parts'] == 24
            assert all(r['living_parts'] == 0 and r['corpse_parts'] in (0, 24) for r in down)
            assert all(r['corpse_parts'] == 24 for r in down if r['corpse_impulses']), (name, 'delivered impulse without limp body')
            assert rows[-1]['corpse_impulses'] == 1, (name, 'limp impulse must be delivered once')
            assert down[-1]['ko' if name == 'ko' else 'dead'], (name, 'wrong down state')
            result[name] = {'real_hits': len(actual), 'corpse_impulses': 1,
                            'bench_forced_ko': name == 'ko', 'parts': 24}
            continue
        assert all(not r['ko'] and not r['dead'] and r['living_parts'] == 24 and r['corpse_parts'] == 0 for r in rows)
        assert len({r['world_bodies'] for r in rows}) == 1, (name, 'native body count changed')
        active = [r for r in rows if r['weight'] > 0 and not r['kinematic'] and r['reaction_bones']]
        assert active, (name, 'no actual native dynamic chain')
        anatomical = (('SPINE',)
                      if name == 'chest' else ('CALF', 'THIGH', 'FOOT') if name in ('leg', 'repeated')
                      else ('UPPERARM', 'FOREARM', 'CLAVICLE'))
        upper = None
        if name == 'chest':
            assert any('SPINE' in b['name'].upper() for row in active for b in row['bones'] if b['id'] == row['hit_bone']), 'real chest arrow did not hit a spine capsule'
            assert all(not any(('PELVIS' in b['name'].upper() or any(t in b['name'].upper() for t in ('THIGH', 'CALF', 'FOOT', 'TOE')))
                               and b['id'] in row['reaction_bones'] for b in row['bones']) for row in active), 'chest made pelvis or legs dynamic'
            assert all(all('SPINE' in b['name'].upper() for b in row['bones'] if b['id'] in row['reaction_bones']) for row in active), 'chest activated a non-spine subtree'
            upper = chest_recovery(active, rows)
        errors = []
        for row in active:
            affected = [b for b in row['bones'] if b['id'] in row['reaction_bones'] and any(t in b['name'].upper() for t in anatomical)]
            assert affected, (name, 'wrong native chain')
            errors.append((row['t_game_ms'], max(pose_error(b)[0] for b in affected), max(pose_error(b)[1] for b in affected)))
        peak_angle = max(e[1] for e in errors)
        peak_cm = max(e[2] for e in errors)
        assert peak_angle > .2 and peak_cm > .05, (name, 'native pose never departed from ordinary animation', peak_angle, peak_cm)
        assert errors[-1][1] < peak_angle*.6+.5 and errors[-1][2] < peak_cm*.6+.5, (name, 'motors did not return the native chain before purekin reset', errors[-1], peak_angle, peak_cm)
        assert all(r['weight'] == 0 and r['kinematic'] and not r['reaction_bones'] for r in rows[-3:]), (name, 'did not return to purekin')
        final_errors = [pose_error(b) for b in rows[-1]['bones']]
        assert max(e[1] for e in final_errors) < 1, (name, 'native pose did not return', max(e[1] for e in final_errors))
        steps = [math.dist(a['root_cm'], b['root_cm']) for a, b in zip(rows, rows[1:])]
        root_travel = max(math.dist(rows[0]['root_cm'], r['root_cm']) for r in rows)
        assert max(steps) < 40 and root_travel < 120, (name, 'root teleported', max(steps), root_travel)
        result[name] = {'real_hits': len(actual), 'peak_weight': max(r['weight'] for r in active),
                        'peak_local_angle_deg': peak_angle, 'peak_native_displacement_cm': peak_cm,
                        'last_active_local_angle_deg': errors[-1][1], 'last_active_displacement_cm': errors[-1][2],
                        'root_max_step_cm': max(steps), 'root_travel_cm': root_travel,
                        'purekin_after': True, 'world_bodies': rows[-1]['world_bodies']}
        if upper is not None:
            result[name]['upper_body_recovery'] = upper
    assert result['strong_shoulder']['peak_weight'] > result['weak_shoulder']['peak_weight'] + .03, 'draw strengths did not change native weight'
    cleanup = [e for e in data if e['kind'] == 'arrow_reaction_cleanup']
    assert len(cleanup) == 2 and all(e['parts'] == 24 and e['before']-e['after'] == 24 for e in cleanup), cleanup
    assert len([e for e in data if e['kind'] == 'arrow_reaction_forced_ko']) == 1
    step = {}
    for mode in ('off', 'on'):
        costs = [r['physics_ms'] for r in frames if r['feature'] == (mode == 'on')]
        assert costs and all(math.isfinite(v) and v >= 0 for v in costs)
        step[mode] = {'samples': len(costs), 'mean_ms': statistics.mean(costs),
                      'p95_ms': sorted(costs)[math.ceil(len(costs)*.95)-1], 'max_ms': max(costs)}
    return {'pass': True, 'cases': result, 'cleanup': cleanup, 'physics_step': step,
            'measurement': 'Post-world native physical matrices versus ordinary animation; local basis removes parent rotation. KO is forced by the bench only after a real arrow queues its pending hit.'}


def aim_signature(run):
    data = events(run)
    counts = collections.Counter()
    for e in data:
        text = e.get('text', '')
        if e['kind'] != 'note' or not text.endswith('by PC_HERO'):
            continue
        match = re.match(r'arrow hit ([^:]+): (head|body|limb),', text)
        if match:
            counts[(e['seg'], 'manual', match[1], match[2])] += 1
        match = re.match(r'arrow dice ([^:]+): .*?, (hit|miss), by PC_HERO', text)
        if match:
            counts[(e['seg'], 'dice', match[1], match[2])] += 1
        if text.startswith('arrow missed:'):
            counts[(e['seg'], 'manual', '-', 'miss')] += 1
    return {'|'.join(key): value for key, value in sorted(counts.items())}


def regression(directory):
    result = {}
    for scenario in ('aim', 'manual-aim'):
        before = aim_signature(directory / f'before-{scenario}')
        after = aim_signature(directory / f'after-{scenario}')
        assert before and after
        result[scenario] = {'pass': before == after, 'before': before, 'after': after}
    return {'pass': all(r['pass'] for r in result.values()), 'scenarios': result,
            'measurement': 'Actual projectile outcomes and hit zones per segment; continuous animation/timing metrics are informational.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('functional', 'regression'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = (functional if args.mode == 'functional' else regression)(args.directory)
    except (AssertionError, KeyError, ValueError, OSError, TypeError) as error:
        result = {'pass': False, 'error': str(error)}
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
