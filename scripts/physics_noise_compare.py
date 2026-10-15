#!/usr/bin/env python3
"""Validate native impact hearing/audio and matched noise OFF/ON tavern timing."""
import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from movable_vobs_compare import events, one, percentile, require


def roles(row):
    result = {listener['role']: listener for listener in row['listeners']}
    require(len(result) == len(row['listeners']), 'duplicate listener role')
    return result


def functional(directory):
    rows = events(directory)
    fixture = one(rows, 'physics_noise_setup')
    require(fixture['enabled'], 'noise disabled in functional fixture')
    table = next(body for body in one(rows, 'movable_setup')['bodies'] if body['id'] == fixture['table_id'])
    checks = {}
    cases = {phase: one(rows, 'physics_noise_case', phase=phase)
             for phase in ('table', 'cup', 'burst', 'dialogue')}
    checkpoints = {phase: one(rows, 'physics_noise_checkpoint', phase=phase)
                   for phase in cases}
    impacts = [row for row in rows if row['kind'] == 'physics_noise']
    require(impacts, 'no native impact event')
    require(all(row['body'] > 0 and 0 < row['volume'] <= 1 and row['radius_cm'] > 0 and
                row['impulse_ns'] > 0 and all(math.isfinite(value) for value in row['position_cm'])
                for row in impacts), 'invalid native noise fields')
    checks['audio_material_recorded_on_every_impact'] = all(
        row['sound'] and row['material'] != 'unknown' for row in impacts)
    by_body = defaultdict(list)
    for impact in impacts:
        by_body[impact['body']].append(impact['t_game_ms'])
    intervals = [right-left for ticks in by_body.values() for left, right in zip(ticks, ticks[1:])]
    checks['body_cooldown_at_least_500_ms'] = all(delta >= 500 for delta in intervals)

    table_start, table_end = roles(cases['table']), roles(checkpoints['table'])
    near = table_start['near']
    checks['listener_4_to_6_m_facing_away'] = 400 <= near['distance_cm'] <= 600 and near['facing_error_deg'] >= 150
    turns = [row for row in rows if row['kind'] == 'physics_noise_turn' and
             row['npc_id'] == near['npc_id'] and 0 <= row['elapsed_ms'] <= 2000 and row['facing_error_deg'] < 30]
    checks['turned_to_noise_within_2000_ms'] = bool(turns) and table_end['near']['heard'] >= 1
    checks['turned_to_noise_other_than_hero'] = any(row['hero_error_deg'] > 30 for row in turns)
    unknown_heard = [row for row in rows if row['kind'] == 'physics_noise_heard' and row['phase'] == 'table']
    checks['unknown_culprit_skips_daedalus_callback'] = bool(unknown_heard) and all(
        row['culprit'] is None and row['callback_dispatched'] is False for row in unknown_heard)
    # Snapshot IDs address BodyData; noise IDs are generation-safe native IDs.
    table_impacts = [row for row in impacts if row['phase'] == 'table' and row['what'] == table['visual']]
    table_bodies = {row['body'] for row in table_impacts}
    checks['table_itself_heard_by_near_listener'] = any(
        row['kind'] == 'physics_noise_heard' and row['phase'] == 'table' and
        row['npc_id'] == near['npc_id'] and row['body'] in table_bodies for row in rows)
    checks['actual_table_hit_with_unknown_culprit'] = bool(table_impacts) and all(row['culprit'] is None for row in table_impacts)
    wall_samples = [roles(row)['wall'] | {'raw_radius_cm': row['radius_cm']} for row in table_impacts]
    blocked = [row for row in wall_samples if row['effective_radius_cm'] < row['distance_cm'] < row['raw_radius_cm'] and row['occlusion'] > 0]
    checks['real_wall_blocks_inside_raw_radius'] = bool(blocked) and table_end['wall']['heard'] == 0
    far_samples = [roles(row)['far'] | {'raw_radius_cm': row['radius_cm']} for row in table_impacts]
    checks['beyond_raw_radius_no_hearing'] = bool(far_samples) and all(
        row['distance_cm'] > row['raw_radius_cm'] for row in far_samples) and table_end['far']['heard'] == 0
    pushes = [row for row in rows if row['kind'] == 'physics_noise_push' and row['phase'] == 'table']
    checks['silent_native_push'] = len(pushes) == 1 and pushes[0]['affected'] > 0 and pushes[0]['reaction_affected'] > 0 and pushes[0]['spell'] is False

    cup_impacts = [row for row in impacts if row['phase'] == 'cup' and 'BEER' in row['what'].upper()]
    cup_end = roles(checkpoints['cup'])
    cup_bodies = {row['body'] for row in cup_impacts}
    cup_heard = any(row['kind'] == 'physics_noise_heard' and row['phase'] == 'cup' and
                    row['npc_id'] == cup_end['near']['npc_id'] and row['body'] in cup_bodies for row in rows)
    checks['cup_heard_near_only'] = cup_heard and cup_end['far']['heard'] == 0
    checks['cup_range_is_local'] = bool(cup_impacts) and all(row['radius_cm'] < 600 for row in cup_impacts)
    for phase in cases:
        phase_impacts = [row for row in impacts if row['phase'] == phase]
        checks[f'{phase}_player_audio_allocated'] = any(row['played'] for row in phase_impacts)

    burst_drops = [row for row in rows if row['kind'] == 'physics_noise_drop' and row['phase'] == 'burst']
    burst_impacts = [row for row in impacts if row['phase'] == 'burst' and 'BEER' in row['what'].upper()]
    checks['ten_real_items_fell_together'] = len(burst_drops) == 10 and len({row['t'] for row in burst_drops}) == 1 and len({row['body'] for row in burst_impacts}) >= 10
    burst_counts = checkpoints['burst']['perceptions']
    checks['burst_at_most_one_perception_per_npc_in_2s'] = bool(burst_counts) and all(row['count'] <= 1 for row in burst_counts)
    hearing_times = defaultdict(list)
    for row in rows:
        if row['kind'] == 'physics_noise_heard':
            hearing_times[row['npc_id']].append(row['t_game_ms'])
    hearing_intervals = [right-left for times in hearing_times.values() for left, right in zip(times, times[1:])]
    checks['per_npc_cooldown_across_every_event'] = all(delta > 2000 for delta in hearing_intervals)
    dialogue = one(rows, 'physics_noise_dialogue')
    dialogue_end = roles(checkpoints['dialogue'])['dialogue']
    checks['real_dialogue_immunity'] = dialogue['active'] and dialogue_end['dialogue'] and dialogue_end['heard'] == 0
    checks['actual_coragon_dialogue_host'] = any(name in dialogue_end['npc'].upper() for name in ('CORAGON', 'KORAGON'))
    # A quiet phase with no impact cannot prove immunity.
    checks['dialogue_impact_present'] = any(row['phase'] == 'dialogue' and 'BEER' in row['what'].upper() for row in impacts)

    examples = []
    for name, subset in (('table', table_impacts), ('cup', cup_impacts)):
        if subset:
            sample = max(subset, key=lambda row: row['impulse_ns'])
            examples.append({key: sample[key] for key in ('what', 'material', 'impulse_ns', 'volume', 'radius_cm', 'sound', 'played')} | {'body_kind': name})
    table_tilt = None
    if pushes and checkpoints['table'].get('table_transform'):
        before, after = pushes[0]['transform'], checkpoints['table']['table_transform']
        a, b = before[4:7], after[4:7]
        dot = sum(x*y for x, y in zip(a, b)) / math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
        table_tilt = math.degrees(math.acos(max(-1, min(1, dot))))
    return {'pass': all(checks.values()), 'checks': checks,
            'turn_first_under_30_deg_ms': min((row['elapsed_ms'] for row in turns), default=None),
            'turn_best_deg_within_2s': min((row['facing_error_deg'] for row in rows if row['kind'] == 'physics_noise_turn'), default=None),
            'table_tilt_deg': table_tilt, 'table_listeners': table_end,
            'table_maximum_tilt_deg_within_2s': checkpoints['table']['table_maximum_tilt_deg'],
            'wall_acoustic_examples': blocked[:2], 'volume_examples': examples,
            'native_impact_events': len(impacts), 'audio_allocated_events': sum(row['played'] for row in impacts),
            'minimum_body_interval_ms': min(intervals, default=None),
            'minimum_npc_interval_ms': min(hearing_intervals, default=None),
            'burst_distinct_item_bodies': len({row['body'] for row in burst_impacts}),
            'burst_per_npc': burst_counts, 'dialogue_listener': dialogue_end}


def measured(directory, enabled):
    rows = events(directory)
    require(one(rows, 'physics_noise_setup')['enabled'] is enabled, 'noise mode mismatch')
    setup = one(rows, 'movable_setup')
    require(setup['enabled'], 'movable furniture must remain enabled in both noise modes')
    listeners = roles(one(rows, 'physics_noise_case', phase='table'))
    require(set(listeners) == {'near', 'wall', 'far'} and all(
        listener['eligible'] and not listener['external'] and not listener['dialogue']
        for listener in listeners.values()), 'three eligible native listeners required in both noise modes')
    require(400 <= listeners['near']['distance_cm'] <= 600 and listeners['near']['occlusion'] <= .05 and
            listeners['wall']['occlusion'] > .1 and listeners['far']['distance_cm'] > 1200,
            'performance scene lacks its clear, occluded or distant hearing candidate')
    before, after = one(rows, 'movable_measure_before'), one(rows, 'movable_measure_after')
    require(before['enabled'] and not after['overflow'], 'native measurement incomplete')
    require(one(rows, 'physics_noise_push')['affected'] > 0, 'silent measured impulse absent')
    hearing = one(rows, 'physics_noise_measure_after')
    measured_listeners = roles(hearing)
    require(hearing['enabled'] is enabled and set(measured_listeners) == set(listeners), 'measured listener mode/count mismatch')
    require(all(measured_listeners[role]['npc_id'] == listener['npc_id']
                for role, listener in listeners.items()), 'listener identity changed during timing')
    require(hearing['impacts'] > 0 and hearing['perceptions'] > 0 and measured_listeners['near']['heard'] > 0
            if enabled else hearing['impacts'] == 0 and hearing['perceptions'] == 0 and all(
                listener['heard'] == 0 for listener in measured_listeners.values()),
            'noise ON must exercise actual hearing; noise OFF must deliver none')
    require(hearing['native_rays'] > 0, 'native ray work was not observed in the timing window')
    meta = json.loads((directory/'appcadence.csv.json').read_text())
    require(meta['clock'] == 'normal' and meta['complete'] and meta['overflow'] == 0 and
            meta['mode'] == ('on' if enabled else 'off') and meta['window_end_ns']-meta['start_ns'] == 30_000_000_000,
            'not a complete matched 30s normal-game cadence window')
    frames = list(csv.DictReader((directory/'appcadence.csv').open()))
    require(frames and len(frames) == meta['frames'], 'completed frame samples absent')
    prior, intervals = meta['start_ns'], []
    for index, row in enumerate(frames):
        end, delta = int(row['end_ns']), int(row['interval_ns'])
        require(int(row['frame']) == index and end > prior and delta == end-prior, 'non-monotonic app frame sequence')
        require(index == len(frames)-1 or end < meta['window_end_ns'], 'app cadence sampled beyond final frame')
        intervals.append(delta/1e6)
        prior = end
    require(prior == meta['final_end_ns'] and prior >= meta['window_end_ns'], 'final app-frame tail missing')
    native = list(csv.DictReader((directory/'movable-physics.csv').open()))
    require(native, 'native physics samples absent')
    previous = -1
    for index, row in enumerate(native):
        tick = int(row['t_game_ms'])
        require(int(row['sample']) == index and tick > previous and int(row['dt_ms']) > 0, 'invalid native sample sequence')
        previous = tick
    costs = [float(row['step_ms']) for row in native]
    require(all(math.isfinite(value) and value >= 0 for value in costs), 'invalid physics timing')
    return {'mean_fps': len(frames)/((prior-meta['start_ns'])/1e9), 'app_frame_p95_ms': percentile(intervals),
            'physics_mean_ms': statistics.mean(costs), 'physics_p95_ms': percentile(costs),
            'native_samples': len(costs), 'fixture': sorted((body['visual'], body['class']) for body in setup['bodies']),
            'listeners_before': listeners, 'hearing': hearing}


def performance(directory):
    off, on = measured(directory/'off', False), measured(directory/'on', True)
    require(off['fixture'] == on['fixture'], 'noise OFF/ON fixtures differ')
    require(all(math.dist(off['listeners_before'][role]['position_cm'], on['listeners_before'][role]['position_cm']) <= 1 and
                off['listeners_before'][role]['process_policy'] == on['listeners_before'][role]['process_policy']
                for role in ('near', 'wall', 'far')), 'OFF/ON listener positions or process modes differ')
    require(off['physics_mean_ms'] > 0 and off['physics_p95_ms'] > 0, 'zero noise-OFF timing baseline')
    fps_drop = 100*(1-on['mean_fps']/off['mean_fps'])
    step_change = 100*(on['physics_mean_ms']/off['physics_mean_ms']-1)
    p95_change = 100*(on['physics_p95_ms']/off['physics_p95_ms']-1)
    return {'pass': fps_drop <= 5 and step_change <= 5 and p95_change <= 5,
            'fps_drop_percent': fps_drop, 'physics_mean_change_percent': step_change,
            'physics_p95_change_percent': p95_change, 'off': off, 'on': on,
            'measurement': 'Matched movable-vobs meshes, camera, hero and three eligible listeners (clear/occluded/distant), silent native impulse, 30s normal-game completed app frames and native physics steps. Real hearing is required ON and absent OFF; only cheap counters run during timing, with impact/turn/acoustic trace logging disabled.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('functional', 'performance'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = (functional if args.mode == 'functional' else performance)(args.directory)
    except (ValueError, KeyError, TypeError, OSError, ZeroDivisionError) as error:
        result = {'pass': False, 'error': str(error)}
    text = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
    if args.output:
        args.output.write_text(text)
    print(text, end='')
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
