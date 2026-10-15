#!/usr/bin/env python3
"""Compare recorded aim outcomes and the actual per-step projectile trace."""
import argparse
import collections
import csv
import json
import math
from pathlib import Path

from arrow_reaction_compare import ordered_aim_signature, recording_clock


def load(run):
    notes = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
    shots = [e for e in notes if e.get('kind') == 'note'
             and (e.get('text', '').startswith('dice shot: by PC_HERO ')
                  or e.get('text', '').startswith('arrow shot:')
                  and e['text'].endswith('by PC_HERO'))]
    groups = collections.defaultdict(list)
    with (run / 'arrow-flight.csv').open() as source:
        for row in csv.DictReader(source):
            if row['origin'] == 'PC_HERO' and row['spell'] == '0':
                groups[int(row['id'])].append(row)
    flights = sorted(groups.values(), key=lambda rows: (int(rows[0]['step']), int(rows[0]['id'])))
    assert len(shots) == len(flights), (run, 'shot/flight counts differ', len(shots), len(flights))
    result = {}
    counts = collections.Counter()
    for shot, rows in zip(shots, flights):
        kind = 'dice' if shot['text'].startswith('dice shot:') else 'manual'
        assert (rows[0]['precise'] == '0') == (kind == 'dice'), (run, 'shot order differs', shot, rows[0])
        # During physics the bench still holds the preceding completed frame's
        # time. This invariant rejects accidental associations across releases.
        trace_time = (int(rows[0]['step']) - 1) * 1000 // 30
        assert abs(trace_time - shot['t']) <= 34, (run, 'shot/trace clock differs', trace_time, shot)
        identity = (shot['seg'], kind)
        counts[identity] += 1
        result[(*identity, counts[identity])] = (shot, rows)
    return result


def vector(row, columns):
    return [float(row[c]) for c in columns]


def delta(a, b, columns):
    return math.dist(vector(a, columns), vector(b, columns))


def first_difference(before, after, phase):
    aa, bb = ([r for r in rows if r['phase'] == phase] for rows in (before, after))
    fields = ('x', 'y', 'z', 'dx', 'dy', 'dz', 'path_cm', 'bounces', 'range_cm',
              'target', 'target_x', 'target_y', 'target_z', 'npc', 'npc_x', 'npc_y', 'npc_z',
              'has_hit', 'fraction', 'hit_x', 'hit_y', 'hit_z', 'nx', 'ny', 'nz', 'material', 'category')
    for i, (a, b) in enumerate(zip(aa, bb)):
        changes = {}
        for key in fields:
            try:
                changed = abs(float(a[key]) - float(b[key])) > 1e-5
            except ValueError:
                changed = a[key] != b[key]
            if changed:
                changes[key] = [a[key], b[key]]
        if changes:
            return {'flight_frame': i, 'steps': [a['step'], b['step']], 'changes': changes}
    return None if len(aa) == len(bb) else {'common_frames': min(len(aa), len(bb)), 'lengths': [len(aa), len(bb)]}


def compare(before, after, frames):
    for key in (('shoot', 'dice', 2), ('gallery', 'dice', 1)):
        assert key in before and key in after, ('missing requested arrow', key)
        sa, aa = before[key]
        sb, bb = after[key]
        begin = [[r for r in rows if r['phase'] == 'begin'] for rows in (aa, bb)]
        initial = [rows[0] for rows in begin]
        queries = [[r for r in rows if r['phase'] == 'world_query' and r['has_hit'] == '1'] for rows in (aa, bb)]
        collisions = [[{k: r[k] for k in ('phase', 'step', 'npc', 'npc_x', 'npc_y', 'npc_z', 'bounces', 'path_cm')}
                       for r in rows if r['phase'] in ('npc_collision', 'moving_npc', 'moving_npc_hit', 'moving_npc_pass')]
                      for rows in (aa, bb)]
        summary = {'arrow': key, 'shot_ms': [sa['t'], sb['t']],
                   'trace_ids': [r['id'] for r in initial],
                   'launch_delta_cm': delta(*initial, ('x', 'y', 'z')),
                   'launch_direction_delta_cm_ms': delta(*initial, ('dx', 'dy', 'dz')),
                   'launch_target_delta_cm': delta(*initial, ('target_x', 'target_y', 'target_z')),
                   'flight_frames': [len(rows) for rows in begin],
                   'max_common_position_delta_cm': max((delta(a, b, ('x', 'y', 'z')) for a, b in zip(*begin)), default=0),
                   'first_begin_difference': first_difference(aa, bb, 'begin'),
                   'first_query_difference': first_difference(aa, bb, 'world_query'),
                   'world_contacts': [[{k: r[k] for k in ('step', 'material', 'category', 'fraction', 'reflect_threshold', 'bounces')}
                                       for r in rows] for rows in queries],
                   'npc_collisions': collisions}
        print(json.dumps(summary, ensure_ascii=False))
        if frames:
            print('flight_frame,before_step,after_step,position_delta_cm,direction_delta_cm_ms,before_target,after_target')
            for i, (a, b) in enumerate(zip(*begin)):
                print(f"{i},{a['step']},{b['step']},{delta(a,b,('x','y','z')):.9g},"
                      f"{delta(a,b,('dx','dy','dz')):.9g},{a['target']},{b['target']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--frames', action='store_true')
    args = parser.parse_args()
    signatures = [ordered_aim_signature(run) for run in (args.before, args.after)]
    matches = sum(a == b for a, b in zip(*signatures))
    print(json.dumps({'outcome_matches': matches, 'outcome_counts': [len(s) for s in signatures],
                      'recording_clock_equal': recording_clock(args.before) == recording_clock(args.after),
                      'outcome_differences': [{'index': i + 1, 'before': a, 'after': b}
                                              for i, (a, b) in enumerate(zip(*signatures)) if a != b]}))
    compare(load(args.before), load(args.after), args.frames)


if __name__ == '__main__':
    main()
