#!/usr/bin/env python3
"""Compare native CharacterVirtual OFF/ON bench evidence without inventing missing results."""

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path


FRAME_FIELDS = {
    'route', 'frame', 't_game_ms', 'dt_ms', 'x_cm', 'y_cm', 'z_cm',
    'move_held', 'in_air', 'falling', 'swimming', 'cv_enabled', 'cv_active',
    'ground_vx_cm_s', 'ground_vy_cm_s', 'ground_vz_cm_s', 'step_ms',
    'wall_ms', 'captured', 'normal_clock',
}

WALKING_ROUTES = {'stairs_up', 'stairs_down', 'steep_slope', 'port_edge', 'doorway', 'tavern'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    result = float(value)
    require(math.isfinite(result), f'non-finite number: {value!r}')
    return result


def flag(value):
    if value in (True, 1, '1', 'true', 'True'):
        return True
    if value in (False, 0, '0', 'false', 'False'):
        return False
    raise ValueError(f'invalid boolean: {value!r}')


def vector(value):
    require(isinstance(value, (list, tuple)) and len(value) == 3, f'invalid vector: {value!r}')
    return [number(x) for x in value]


def distance(a, b):
    return math.dist(vector(a), vector(b))


def position(frame):
    return [frame[key] for key in ('x_cm', 'y_cm', 'z_cm')]


def combined_pass(checks):
    if False in checks:
        return False
    return True if checks and all(check is True for check in checks) else None


def load(directory, enabled):
    path = directory / 'events.jsonl'
    events = []
    for index, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        require(isinstance(row, dict) and isinstance(row.get('kind'), str), f'{path}:{index}: invalid event')
        if row['kind'].startswith('character_'):
            require(not row['kind'].endswith('_error'), f'{path}:{index}: native fixture error: {row}')
            events.append(row)
    frames = defaultdict(list)
    frame_path = directory / 'character-frames.csv'
    if frame_path.exists():
        read_frames(frame_path, enabled, frames)
    log = directory / 'bench.log'
    complete = 'done' in log.read_text().splitlines() if log.exists() else None
    return {'directory': str(directory), 'events': events, 'frames': dict(frames), 'complete': complete, 'enabled': enabled}


def read_frames(frame_path, enabled, frames):
    with frame_path.open(newline='') as source:
        reader = csv.DictReader(source)
        require(FRAME_FIELDS <= set(reader.fieldnames or []), f'{frame_path}: missing columns: {sorted(FRAME_FIELDS - set(reader.fieldnames or []))}')
        booleans = {'move_held', 'in_air', 'falling', 'swimming', 'cv_enabled', 'cv_active', 'captured', 'normal_clock'}
        for index, raw in enumerate(reader, 2):
            row = {key: value if key == 'route' else flag(value) if key in booleans else number(value)
                   for key, value in raw.items() if key in FRAME_FIELDS}
            require(row['route'] and row['dt_ms'] > 0 and row['step_ms'] >= 0 and row['wall_ms'] >= 0,
                    f'{frame_path}:{index}: invalid timing or route')
            require(row['cv_enabled'] is enabled, f'{frame_path}:{index}: configured mode differs from directory')
            require(enabled or not row['cv_active'], f'{frame_path}:{index}: native CV active in OFF baseline')
            prior = frames[row['route']]
            require(not prior or (row['frame'] > prior[-1]['frame'] and row['t_game_ms'] > prior[-1]['t_game_ms']),
                    f'{frame_path}:{index}: non-monotonic route frame')
            prior.append(row)


def select(run, kind, route=None):
    return [event for event in run['events'] if event['kind'] == kind and
            (route is None or event.get('route') == route)]


def virtual_activity(run, labels, held=False):
    """The configured flag is never evidence that the native controller ran."""
    checks = {}
    for label in sorted(set(labels)):
        rows = run['frames'].get(label, [])
        eligible = [row for row in rows if row['move_held']] if held else rows
        active = sum(row['cv_active'] for row in eligible)
        checks[label] = {'frames': len(rows), 'eligible_frames': len(eligible),
                         'held_movement_required': held, 'cv_active_frames': active,
                         'pass': active > 0 if eligible else None}
    return {'fixtures': checks, 'pass': combined_pass([row['pass'] for row in checks.values()])}


def stalls(frames):
    """Count continuous held-input periods that stay within a 5cm horizontal radius."""
    found, anchor, last = [], None, None

    def finish():
        if anchor is not None and last is not None and last['t_game_ms'] - anchor['t_game_ms'] > 1000:
            found.append({'start_ms': anchor['t_game_ms'], 'end_ms': last['t_game_ms'],
                          'duration_ms': last['t_game_ms'] - anchor['t_game_ms']})

    for row in frames:
        if not row['move_held']:
            finish()
            anchor = last = None
            continue
        if anchor is None:
            anchor = row
        elif math.hypot(row['x_cm'] - anchor['x_cm'], row['z_cm'] - anchor['z_cm']) >= 5:
            finish()
            anchor = row
        last = row
    finish()
    return found


def route_result(run, name):
    frames = run['frames'].get(name, [])
    starts = select(run, 'character_route_begin', name)
    ends = select(run, 'character_route_end', name)
    require(len(starts) <= 1 and len(ends) <= 1, f'duplicate route: {name}')
    end = ends[0] if ends else {}
    success = flag(end['success']) if 'success' in end else None
    elapsed = number(end['elapsed_ms']) if 'elapsed_ms' in end else None
    interruptions = stalls(frames)
    vertical_deltas = [abs(b['y_cm'] - a['y_cm']) for a, b in zip(frames, frames[1:])]
    if starts and frames:
        vertical_deltas.append(abs(frames[0]['y_cm'] - vector(starts[0]['start_cm'])[1]))
    result = {'success': success, 'elapsed_ms': elapsed, 'frames': len(frames),
              'stalls_over_1s': len(interruptions) if frames else None, 'stalls': interruptions,
              'maximum_vertical_frame_delta_cm': max(vertical_deltas, default=None),
              'cv_active_frames': sum(row['cv_active'] for row in frames),
              'pass': success if starts and frames and elapsed is not None else None}
    if 'saw_slide' in end:
        result['saw_slide'] = flag(end['saw_slide'])
    if starts:
        result['start_cm'] = vector(starts[0]['start_cm'])
        result['goal_cm'] = vector(starts[0]['goal_cm'])
        result['tolerance_cm'] = number(starts[0]['tolerance_cm'])
    return result


def native_falls(run, name):
    rows = select(run, 'character_fall', name)
    ends = select(run, 'character_route_end', name)
    # A native count establishes that zero events really means zero observed falls.
    count = ends[0].get('fall_count') if len(ends) == 1 else None
    if count is not None:
        require(int(count) == count and count >= 0, f'{name}: invalid fall count')
        require(len(rows) == count, f'{name}: native fall count differs from events')
    return [vector(row.get('position_cm', row.get('root_cm'))) for row in rows], count is not None or bool(rows)


def compare_falls(off, on, name):
    before, complete_off = native_falls(off, name)
    after, complete_on = native_falls(on, name)
    remaining = list(after)
    unmatched_off = []
    for pos in before:
        candidates = [(distance(pos, other), index) for index, other in enumerate(remaining)]
        if candidates and min(candidates)[0] <= 100:
            remaining.pop(min(candidates)[1])
        else:
            unmatched_off.append(pos)
    complete = complete_off and complete_on
    return {'off_count': len(before) if complete_off else None, 'on_count': len(after) if complete_on else None,
            'off_only_positions_cm': unmatched_off if complete else None,
            'on_only_positions_cm': remaining if complete else None, 'match_radius_cm': 100,
            'pass': not remaining if complete else None}


def performance(run):
    rows = [row for route in run['frames'].values() for row in route]
    normal = [row for row in rows if row['normal_clock'] and not row['captured']]
    if not normal:
        return {'pass': None, 'reason': 'No uncaptured normal-clock frame samples; capture/fixed-clock rate is not game FPS.'}
    costs = sorted(row['step_ms'] for row in normal)
    result = {'samples': len(normal), 'physics_p95_ms': costs[math.ceil(.95 * len(costs)) - 1],
            'routes': sorted({row['route'] for row in normal}),
            'measurement': 'Native physics cost and completed app-frame wall duration, uncaptured normal-clock samples only.'}
    directory = Path(run['directory'])
    metadata = directory / 'appcadence.csv.json'
    if metadata.exists():
        meta = json.loads(metadata.read_text())
        require(meta.get('clock') == 'normal' and meta.get('complete') is True and meta.get('overflow') == 0,
                f'{metadata}: incomplete normal-clock cadence window')
        require(meta.get('mode') in ('off', 'on') and meta['mode'] == ('on' if normal[0]['cv_enabled'] else 'off'),
                f'{metadata}: cadence mode mismatch')
        start, end = int(meta['start_ns']), int(meta['window_end_ns'])
        require(end - start == 30_000_000_000, f'{metadata}: cadence window must cover 30 seconds')
        with (directory / 'appcadence.csv').open(newline='') as source:
            cadence = list(csv.DictReader(source))
        require(cadence and len(cadence) == meta['frames'], 'cadence sample count differs from metadata')
        prior = start
        for index, row in enumerate(cadence):
            frame_end, delta = int(row['end_ns']), int(row['interval_ns'])
            require(int(row['frame']) == index and frame_end > prior and delta == frame_end - prior,
                    'non-monotonic app frame cadence')
            require(index + 1 == len(cadence) or frame_end < end, 'cadence continued after its final frame')
            prior = frame_end
        require(prior == meta['final_end_ns'] and prior >= end, 'cadence final frame tail missing')
        result.update(mean_fps=len(cadence) * 1e9 / (prior - start), app_frames=len(cadence),
                      app_window_ms=(prior - start) / 1e6, fps_source='appcadence.csv')
    else:
        wall = [row['wall_ms'] for row in normal]
        require(all(value > 0 for value in wall), 'normal-clock wall frame duration must be positive')
        result.update(mean_fps=1000 * len(wall) / sum(wall), fps_source='character-frames.csv wall_ms')
    return result


def compare_performance(off, on):
    before, after = performance(off), performance(on)
    active = virtual_activity(on, ['performance'], held=True)
    result = {'native_activity': active, 'off': before, 'on': after, 'physics_p95_limit_delta_ms': .3, 'fps_drop_limit_percent': 5, 'pass': None}
    if 'mean_fps' in before and 'mean_fps' in after:
        require(before['routes'] == after['routes'], 'normal-clock OFF/ON performance routes differ')
        delta = after['physics_p95_ms'] - before['physics_p95_ms']
        drop = 100 * (1 - after['mean_fps'] / before['mean_fps'])
        result.update(physics_p95_delta_ms=delta, fps_drop_percent=drop, passed_physics=delta <= .3,
                      passed_fps=drop <= 5, **{'pass': combined_pass([delta <= .3, drop <= 5, active['pass']])})
    return result


def props(run):
    rows = select(run, 'character_prop_checkpoint')
    result = []
    for row in rows:
        item = {key: row[key] for key in ('role', 'id')}
        item.update(mass_kg=number(row['mass_kg']), position_cm=vector(row['position_cm']),
                    velocity_cm_s=vector(row['velocity_cm_s']), max_displacement_cm=number(row['max_displacement_cm']))
        require(item['mass_kg'] >= 0 and item['max_displacement_cm'] >= 0, f'invalid native prop data: {row}')
        for key in ('hero_cm', 'initial_cm', 'hero_initial_cm'):
            if key in row:
                item[key] = vector(row[key])
        if 'initial_cm' in item:
            item['horizontal_displacement_cm'] = math.hypot(item['position_cm'][0] - item['initial_cm'][0],
                                                          item['position_cm'][2] - item['initial_cm'][2])
        for key in ('overlap', 'contact', 'hero_contact', 'native_contact', 'blocks_walking', 'passed_through', 'hero_crossed_prop'):
            if key in row:
                item[key] = flag(row[key])
        for key in ('near_plane_cm', 'far_plane_cm'):
            if key in row:
                item[key] = number(row[key])
        if 'elapsed_ms' in row:
            item['elapsed_ms'] = number(row['elapsed_ms'])
            if item['elapsed_ms'] > 0 and 'horizontal_displacement_cm' in item:
                item['horizontal_cm_per_s'] = 1000 * item['horizontal_displacement_cm'] / item['elapsed_ms']
        result.append(item)
    return result


def compare_props(off, on):
    before, after = props(off), props(on)
    by_role = {row['role']: row for row in after}
    mug = by_role.get('mug', by_role.get('dish'))
    barrel, table = by_role.get('barrel'), by_role.get('table')
    checks = {'item_moved': None, 'mug_moved': None, 'barrel_slower_than_mug': None, 'table_blocks': None}
    for role, prop in (('item', by_role.get('item')), ('mug', mug)):
        if prop is None:
            continue
        contact = prop.get('native_contact', prop.get('hero_contact', prop.get('contact')))
        if contact is False:
            checks[role + '_moved'] = False
        elif contact is True and 'horizontal_displacement_cm' in prop:
            checks[role + '_moved'] = prop['horizontal_displacement_cm'] > 1
            prop['movement_evidence'] = 'native contact'
    if mug is not None and barrel is not None:
        contact = barrel.get('native_contact', barrel.get('hero_contact', barrel.get('contact')))
        mug_contact = mug.get('native_contact', mug.get('hero_contact', mug.get('contact')))
        if contact is False or mug_contact is False:
            checks['barrel_slower_than_mug'] = False
        elif contact is True and mug_contact is True and 'horizontal_cm_per_s' in mug and 'horizontal_cm_per_s' in barrel:
            checks['barrel_slower_than_mug'] = 0 < barrel['horizontal_cm_per_s'] < mug['horizontal_cm_per_s'] and barrel['mass_kg'] > mug['mass_kg']
    if table is not None:
        # A non-overlap at a single instant cannot establish that a table was not crossed.
        crossed = table.get('passed_through', table.get('hero_crossed_prop'))
        contact = table.get('native_contact', table.get('hero_contact', table.get('contact')))
        if crossed is not None and contact is True and table.get('blocks_walking') is True:
            checks['table_blocks'] = not crossed and not table.get('overlap', False)
    activity = virtual_activity(on, by_role, held=True)
    return {'off': before, 'on': after, 'checks': checks, 'native_activity': activity,
            'pass': combined_pass(list(checks.values()) + [activity['pass']])}


def rides(run):
    rows = select(run, 'character_ride_checkpoint')
    groups = defaultdict(list)
    for row in rows:
        support = row.get('support_id', row.get('support_kind', row.get('type', row.get('id', 'support'))))
        groups[str(support)].append(row)
    result = []
    for support, samples in groups.items():
        item = {'support_id': support, 'samples': len(samples), 'pass': None}
        first_kind = samples[0].get('support_kind', samples[0].get('type'))
        activity = virtual_activity(run, ['ride_' + str(first_kind)]) if first_kind else {'pass': None}
        item['native_activity'] = activity
        if all('support_cm' in row and 'hero_cm' in row for row in samples) and (len(samples) >= 2 or all(key in samples[0] for key in ('support_initial_cm', 'hero_initial_cm'))):
            first, last = samples[0], samples[-1]
            support_start = first.get('support_initial_cm', first['support_cm'])
            hero_start = first.get('hero_initial_cm', first['hero_cm'])
            support_delta = [b - a for a, b in zip(vector(support_start), vector(last['support_cm']))]
            hero_delta = [b - a for a, b in zip(vector(hero_start), vector(last['hero_cm']))]
            item.update(support_displacement_cm=math.dist([0, 0, 0], support_delta),
                        hero_displacement_cm=math.dist([0, 0, 0], hero_delta),
                        relative_drift_cm=math.dist(support_delta, hero_delta))
            velocities = [vector(row['ground_velocity_cm_s']) for row in samples if 'ground_velocity_cm_s' in row]
            if velocities:
                item['maximum_ground_speed_cm_s'] = max(math.dist([0, 0, 0], value) for value in velocities)
            if 'falling' in last and 'in_air' in last:
                item.update(falling=flag(last['falling']), in_air=flag(last['in_air']))
            if item['support_displacement_cm'] > 50 and velocities:
                if item.get('falling') or item.get('in_air'):
                    item['outcome'] = 'fall observed; correct loss of support requires visual review'
                else:
                    item['pass'] = item['relative_drift_cm'] <= 5 and item['maximum_ground_speed_cm_s'] > 0
                    item['outcome'] = 'ride measured'
        if run.get('enabled'):
            item['pass'] = combined_pass([item['pass'], activity['pass']])
        result.append(item)
    return result


def snapshot(run, operation):
    rows = [row for row in select(run, 'character_snapshot') if row.get('operation') == operation]
    require(len(rows) <= 1, f'duplicate {operation} snapshot')
    return vector(rows[0]['root_cm']) if rows else None


def compare_reload(off, on, reload_off, reload_on):
    results = {}
    for mode, source, restored in (('off', off, reload_off), ('on', on, reload_on)):
        saved = snapshot(source, 'saved')
        initial = snapshot(restored, 'initial') if restored is not None else None
        result = {'saved_root_cm': saved, 'restored_root_cm': initial, 'pass': None}
        if saved is not None and initial is not None:
            error = distance(saved, initial)
            result.update(error_cm=error, **{'pass': error <= 1})
        results[mode] = result
    results['tolerance_cm'] = 1
    results['pass'] = combined_pass([results[mode]['pass'] for mode in ('off', 'on')])
    if all(results[mode]['saved_root_cm'] is not None and results[mode]['restored_root_cm'] is not None for mode in ('off', 'on')):
        results['off_on_saved_distance_cm'] = distance(results['off']['saved_root_cm'], results['on']['saved_root_cm'])
        results['off_on_restored_distance_cm'] = distance(results['off']['restored_root_cm'], results['on']['restored_root_cm'])
        results['pass'] = results['pass'] and results['off_on_saved_distance_cm'] <= 1 and results['off_on_restored_distance_cm'] <= 1
    return results


def compare(off, on, reload_off=None, reload_on=None, perf_off=None, perf_on=None,
            props_off=None, props_on=None, save_off=None, save_on=None):
    names = sorted({row['route'] for run in (off, on) for row in select(run, 'character_route_begin')})
    routes = []
    for name in names:
        before, after = route_result(off, name), route_result(on, name)
        matched = None
        if all('start_cm' in row for row in (before, after)):
            matched = distance(before['start_cm'], after['start_cm']) <= 1 and distance(before['goal_cm'], after['goal_cm']) <= 1 and before['tolerance_cm'] == after['tolerance_cm']
        falls = compare_falls(off, on, name)
        activity = virtual_activity(on, [name], held=True) if name in WALKING_ROUTES else {'pass': True, 'reason': 'Legacy jump/swim handoff route.'}
        after['native_activity'] = activity
        routes.append({'route': name, 'off': before, 'on': after, 'matched_route': matched, 'falls': falls,
                       'pass': combined_pass([before['pass'], after['pass'], matched, falls['pass'], activity['pass']])})
    perf = compare_performance(perf_off or off, perf_on or on)
    prop = compare_props(props_off or off, props_on or on)
    ride = {'off': rides(props_off or off), 'on': rides(props_on or on)}
    ride['pass'] = combined_pass([row['pass'] for row in ride['on']])
    reload = compare_reload(save_off or off, save_on or on, reload_off, reload_on)
    checks = [row['pass'] for row in routes] + [perf['pass'], prop['pass'], ride['pass'], reload['pass']]
    checks += [off['complete'], on['complete'], True if 6 <= len(routes) <= 8 else None]
    checks += [run['complete'] for run in (reload_off, reload_on, perf_off, perf_on, props_off, props_on, save_off, save_on) if run is not None]
    return {'pass': combined_pass(checks), 'off_directory': off['directory'], 'on_directory': on['directory'],
            'routes': routes, 'performance': perf, 'props': prop, 'ride': ride, 'reload': reload,
            'notes': ['null means insufficient evidence, never a pass.',
                      'Stall: held movement remains within a 5cm horizontal radius for more than 1000ms.',
                      'Falls are native events paired spatially within 100cm; a native fall_count establishes zero falls.',
                      'ON walking, each prop/support case and uncaptured performance require positive cv_active native frames; the flag alone never passes.',
                      'Foot IK screenshot, route videos, and a human movement-feel trial require visual review.']}


def display(value, digits=2):
    if value is None:
        return '—'
    if isinstance(value, bool):
        return 'да' if value else 'нет'
    if isinstance(value, float):
        return f'{value:.{digits}f}'
    return str(value).replace('|', '\\|')


def markdown(result):
    state = {True: 'PASS', False: 'FAIL', None: 'НЕПОЛНО'}[result.get('pass')]
    lines = [f'CharacterVirtual: **{state}**. Пропущенные данные обозначены «—».']
    if 'error' in result:
        return '\n'.join(lines + ['', result['error']]) + '\n'
    lines += ['', '| Маршрут | Дошёл OFF / ON | Время, мс OFF / ON | Застревания >1с OFF / ON | Макс. ΔY, см OFF / ON | Новые падения ON | Кадры native CV ON |',
              '|---|---|---:|---:|---:|---:|---:|']
    for row in result['routes']:
        before, after = row['off'], row['on']
        pair = lambda key: f'{display(before[key])} / {display(after[key])}'
        falls = row['falls']['on_only_positions_cm']
        lines.append(f"| {display(row['route'])} | {pair('success')} | {pair('elapsed_ms')} | {pair('stalls_over_1s')} | {pair('maximum_vertical_frame_delta_cm')} | {display(len(falls) if falls is not None else None)} | {after['cv_active_frames']} |")
    perf = result['performance']
    lines += ['', f"Физика p95 OFF / ON: {display(perf['off'].get('physics_p95_ms'))} / {display(perf['on'].get('physics_p95_ms'))} мс; Δ {display(perf.get('physics_p95_delta_ms'))} мс (лимит +0,3). FPS OFF / ON: {display(perf['off'].get('mean_fps'))} / {display(perf['on'].get('mean_fps'))}; падение {display(perf.get('fps_drop_percent'))}% (лимит 5%). Учитываются только кадры без записи видео с обычными часами."]
    lines += ['', '| Предмет ON | Масса, кг | Гориз. смещение, см | Время, мс | Гориз. смещение / время, см/с | Контакт героя |', '|---|---:|---:|---:|---:|---|']
    for row in result['props']['on']:
        lines.append(f"| {display(row['role'])} #{display(row['id'])} | {display(row['mass_kg'])} | {display(row.get('horizontal_displacement_cm'))} | {display(row.get('elapsed_ms'))} | {display(row.get('horizontal_cm_per_s'))} | {display(row.get('native_contact', row.get('hero_contact', row.get('contact'))))} |")
    for mode in ('off', 'on'):
        for row in result['ride'][mode]:
            lines += ['', f"Опора {mode.upper()} #{row['support_id']}: путь опоры / героя {display(row.get('support_displacement_cm'))} / {display(row.get('hero_displacement_cm'))} см, относительный дрейф {display(row.get('relative_drift_cm'))} см, скорость опоры из контакта {display(row.get('maximum_ground_speed_cm_s'))} см/с."]
    lines += ['', f"Восстановление корня OFF / ON: {display(result['reload']['off'].get('error_cm'))} / {display(result['reload']['on'].get('error_cm'))} см (допуск 1 см).",
              '', 'Кадр foot IK, видео каждого маршрута и проба руками проверяются отдельно. Полные численные данные — в JSON.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('off_directory', type=Path)
    parser.add_argument('on_directory', type=Path)
    parser.add_argument('--reload-off', type=Path)
    parser.add_argument('--reload-on', type=Path)
    parser.add_argument('--perf-off', type=Path, help='Separate uncaptured normal-clock OFF run')
    parser.add_argument('--perf-on', type=Path, help='Separate uncaptured normal-clock ON run')
    parser.add_argument('--props-off', type=Path, help='Separate OFF prop/ride fixture run')
    parser.add_argument('--props-on', type=Path, help='Separate ON prop/ride fixture run')
    parser.add_argument('--save-off', type=Path, help='Separate OFF save source run')
    parser.add_argument('--save-on', type=Path, help='Separate ON save source run')
    parser.add_argument('--output', type=Path, help='Write Markdown here and JSON beside it; a .json suffix reverses these destinations')
    args = parser.parse_args()
    try:
        off, on = load(args.off_directory, False), load(args.on_directory, True)
        require(bool(args.perf_off) == bool(args.perf_on), '--perf-off and --perf-on must be supplied together')
        require(bool(args.props_off) == bool(args.props_on), '--props-off and --props-on must be supplied together')
        require(bool(args.save_off) == bool(args.save_on), '--save-off and --save-on must be supplied together')
        result = compare(off, on, load(args.reload_off, False) if args.reload_off else None,
                         load(args.reload_on, True) if args.reload_on else None,
                         load(args.perf_off, False) if args.perf_off else None,
                         load(args.perf_on, True) if args.perf_on else None,
                         load(args.props_off, False) if args.props_off else None,
                         load(args.props_on, True) if args.props_on else None,
                         load(args.save_off, False) if args.save_off else None,
                         load(args.save_on, True) if args.save_on else None)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'pass': False, 'error': str(error)}
    report = markdown(result)
    print(report, end='')
    if args.output is not None:
        md = args.output.with_suffix('.md') if args.output.suffix == '.json' else args.output
        data = args.output if args.output.suffix == '.json' else args.output.with_suffix('.json')
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(report)
        data.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    return 0 if result.get('pass') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
