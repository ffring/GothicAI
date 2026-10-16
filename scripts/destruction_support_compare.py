#!/usr/bin/env python3
"""Summarize native fireball supports; validate the repaired 6–19 second interval."""
import argparse
import json
import math
import re
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite_vector(value, label):
    require(isinstance(value, list) and len(value) == 3 and
            all(isinstance(v, (int, float)) and math.isfinite(v) for v in value),
            f'{label}: invalid native vector')


def read(directory, start_ms, end_ms, max_gap_cm, strict):
    require('done' in (directory / 'bench.log').read_text().splitlines(), f'{directory}: incomplete scenario')
    rows = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
    errors = [row for row in rows if row['kind'].endswith('_error')]
    require(not errors, f'{directory}: fixture error: {errors[:1]}')
    setups = [row for row in rows if row['kind'] == 'destruction_setup']
    require(len(setups) == 1 and setups[0]['case'] == 'fireball' and
            setups[0]['location'] == 'coragon_tavern' and setups[0]['enabled'],
            f'{directory}: expected the native Coragon fireball fixture')
    setup = setups[0]
    require(sorted(body['class'] for body in setup['bodies']) == ['barrel', 'crate'],
            f'{directory}: native barrel/crate pair missing')
    beginnings = [row for row in rows if row['kind'] == 'destruction_begin']
    require(len(beginnings) == 1 and beginnings[0]['spell_target'] == 'BAU_904_BAUER',
            f'{directory}: wrong native fireball target')
    begin = beginnings[0]
    all_samples = [row for row in rows if row['kind'] == 'destruction_support_sample']
    samples = [row for row in all_samples if start_ms <= row['elapsed_ms'] <= end_ms + 100]
    require(samples, f'{directory}: no support observations in requested interval')
    times = sorted({row['elapsed_ms'] for row in samples})
    required_end = end_ms - 500 if strict else end_ms - 1000
    require(times[0] <= start_ms + 500 and times[-1] >= required_end,
            f'{directory}: incomplete {start_ms}..{end_ms} ms support interval: {times}')
    require(all(b - a <= 1500 for a, b in zip(times, times[1:])),
            f'{directory}: missing one-second native support observations')
    problems = []
    handoffs = [row for row in rows if row['kind'] == 'movement_impulse_handoff' and
                row.get('actor') == 'BAU_904_BAUER' and row.get('actor_id') == begin['spell_target_npc_id']]
    for handoff in handoffs:
        finite_vector(handoff['impulse_velocity_cm_s'], 'movement impulse handoff')
    # MoveAlgo::State is Run=0, InAir=1. The real projectile arrives inside the
    # walking callback; the old result must hand control to that upward impulse.
    accepted_handoffs = [row for row in handoffs if row['from_state'] == 0 and row['to_state'] == 1 and
                         row['impulse_velocity_cm_s'][1] > 0 and row['backend'] in ('virtual', 'legacy')]
    if strict and not accepted_handoffs:
        problems.append('real fireball has no preserved Run→InAir upward-impulse handoff')
    supports = {}
    observations = []
    gaps = []
    skin_gaps = []
    rig_kinds = set()
    for sample in samples:
        time = sample['elapsed_ms']
        require(sample['actor'] == 'BAU_904_BAUER' and sample['actor_id'] == begin['spell_target_npc_id'],
                f'{directory}: support snapshot addresses another NPC')
        finite_vector(sample['position_cm'], f'{time} position')
        dynamic_ragdoll = sample['rig_kind'] == 'ragdoll' and not sample['kinematic']
        if strict:
            animations = re.findall(r'[A-Z][A-Z0-9_]*', sample.get('animations', '').upper())
            if 'S_FALLB' in animations:
                problems.append(f'{time}: airborne S_FALLB loop remains active')
            if not sample.get('down') and not any('FALLEN' in name for name in animations):
                problems.append(f'{time}: target has no landed lying pose')
        if strict and not sample.get('body_drawable'):
            problems.append(f'{time}: target body has no visible drawable')
        skin = sample.get('rendered_skin')
        if skin is None:
            if strict:
                problems.append(f'{time}: exact rendered-skin observation missing')
        else:
            require(skin['indexed_vertices'] > 0, f'{time}: rendered skin has no indexed vertices')
            finite_vector(skin['lowest_world_cm'], f'{time} rendered skin')
            if not skin['support_found'] or skin['gap_cm'] is None:
                problems.append(f'{time}: rendered skin has no visible surface below it')
            else:
                finite_vector(skin['support_world_cm'], f'{time} rendered skin support')
                gap = skin['gap_cm']
                require(math.isfinite(gap) and
                        abs(skin['lowest_world_cm'][1] - skin['support_world_cm'][1] - gap) < .02,
                        f'{time}: rendered-skin gap differs from source witness points')
                skin_gaps.append(gap)
                if gap > max_gap_cm:
                    problems.append(f'{time}: rendered skin gap {gap:.3f} cm > {max_gap_cm:g} cm')
                if gap < -max_gap_cm:
                    problems.append(f'{time}: rendered skin penetrates visible support by {-gap:.3f} cm > {max_gap_cm:g} cm')
        rig_kinds.add(sample['rig_kind'])
        weight_contacts = []
        for contact in sample['contacts']:
            for key in ('normal', 'part_contact_cm', 'support_contact_cm'):
                finite_vector(contact[key], f'{time} {key}')
            require(math.isfinite(contact['depth_cm']), f'{time}: nonfinite penetration depth')
            require(isinstance(contact['part_body_id'], int) and isinstance(contact['support_body_id'], int),
                    f'{time}: native body identity missing')
            # Jolt's penetration axis moves body 2 out of body 1. The exported
            # normal is its inverse and points up from a weight-bearing support.
            weight = contact['normal'][1] > .25
            require(weight == contact['supports_weight'], f'{time}: support-normal classification differs')
            if not weight:
                continue
            weight_contacts.append(contact)
            key = (contact['support_body_id'], contact['layer'], contact['visual'],
                   contact['zen_id'], contact['piece'])
            entry = supports.setdefault(key, {
                'body_id': contact['support_body_id'], 'layer': contact['layer'],
                'visual': contact['visual'], 'zen_id': contact['zen_id'], 'piece': contact['piece'],
                'kind': contact['kind'], 'broken': contact['broken'], 'visible': contact['visible'],
                'first_ms': time, 'last_ms': time, 'contacts': 0, 'maximum_visible_gap_cm': None,
                'unknown_geometry_contacts': 0,
            })
            entry['last_ms'] = max(entry['last_ms'], time)
            entry['contacts'] += 1
            if not contact['visible'] and (dynamic_ragdoll or contact['kind'] in ('hull', 'query')):
                problems.append(f'{time}: invisible support body {key[0]} ({key[2]})')
            if contact['broken'] and contact['kind'] in ('hull', 'query'):
                problems.append(f'{time}: broken intact {contact["kind"]} body {key[0]} survives')
            gap = contact['visible_gap_cm']
            if gap is None:
                entry['unknown_geometry_contacts'] += 1
                if dynamic_ragdoll:
                    problems.append(f'{time}: unknown visible geometry of body {key[0]} ({key[2]})')
            else:
                require(isinstance(gap, (int, float)) and math.isfinite(gap),
                        f'{time}: invalid visible gap')
                finite_vector(contact['visible_point_cm'], f'{time} visible point')
                distance = contact.get('visible_distance_cm', abs(gap))
                require(math.isfinite(distance) and distance >= 0 and
                        abs(math.dist(contact['part_contact_cm'], contact['visible_point_cm']) - distance) < .02,
                        f'{time}: visible gap differs from native witness points')
                gaps.append(gap)
                previous = entry['maximum_visible_gap_cm']
                entry['maximum_visible_gap_cm'] = gap if previous is None else max(previous, gap)
                if dynamic_ragdoll and gap > max_gap_cm:
                    problems.append(f'{time}: body {key[0]} ({key[2]}) gap {gap:.3f} cm > {max_gap_cm:g} cm')
        if sample['rig_kind'] == 'none':
            problems.append(f'{time}: native rig missing')
        if dynamic_ragdoll and not weight_contacts:
            problems.append(f'{time}: no native weight-bearing contact')
        observations.append({'elapsed_ms': time, 'rig_kind': sample['rig_kind'],
                             'kinematic': sample['kinematic'], 'sleeping': sample['sleeping'],
                             'dynamic_ragdoll': dynamic_ragdoll,
                             'rendered_skin_gap_cm': skin['gap_cm'] if skin is not None else None,
                             'supports': len(weight_contacts),
                             'maximum_visible_gap_cm': max((c['visible_gap_cm'] for c in weight_contacts
                                                            if c['visible_gap_cm'] is not None), default=None)})
    result = {
        'pass': not problems, 'directory': str(directory), 'strict': strict,
        'interval_ms': [times[0], times[-1]], 'samples': len(samples), 'rig_kinds': sorted(rig_kinds),
        'maximum_visible_gap_cm': max(gaps, default=None), 'allowed_visible_gap_cm': max_gap_cm,
        'maximum_rendered_skin_gap_cm': max(skin_gaps, default=None),
        'movement_impulse_handoffs': handoffs,
        'supports': sorted(supports.values(), key=lambda row: row['body_id']),
        'observations': observations, 'problems': list(dict.fromkeys(problems)),
    }
    return result, {'world': setup['world'], 'location': setup['location'],
                    'spell_target_cm': begin['spell_target_cm']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, help='diagnostic baseline; geometry failures are reported')
    parser.add_argument('--after', type=Path, help='repaired run; any unsupported/invisible/gapped body fails')
    parser.add_argument('--start-ms', type=int, default=6000)
    parser.add_argument('--end-ms', type=int, default=19000)
    parser.add_argument('--max-gap-cm', type=float, default=5)
    args = parser.parse_args()
    require(args.before or args.after, 'provide --before and/or --after')
    require(0 <= args.start_ms < args.end_ms and math.isfinite(args.max_gap_cm) and args.max_gap_cm > 0,
            'invalid interval or gap limit')
    output = {}
    fixtures = []
    for phase, directory in (('before', args.before), ('after', args.after)):
        if directory is None:
            continue
        output[phase], fixture = read(directory, args.start_ms, args.end_ms, args.max_gap_cm, phase == 'after')
        fixtures.append(fixture)
    if len(fixtures) == 2:
        require(fixtures[0]['world'] == fixtures[1]['world'] and fixtures[0]['location'] == fixtures[1]['location'] and
                math.dist(fixtures[0]['spell_target_cm'], fixtures[1]['spell_target_cm']) <= 1,
                'before/after fireball fixture moved by more than 1 cm')
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if 'after' not in output or output['after']['pass'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f'destruction support: {error}')
