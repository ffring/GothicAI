#!/usr/bin/env python3
"""Compare native NPC CharacterVirtual OFF/ON evidence; absent data stays incomplete."""

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


WINDOWS = ('market-7', 'market-20', 'harbour-7', 'harbour-20',
           'onar-7', 'onar-20', 'lobart-7', 'lobart-20')
ROUTES = ('stairs', 'doorway', 'tavern', 'seat')
BOOLEAN_COLUMNS = {'cv_enabled', 'cv_active', 'moving', 'in_air', 'interactive',
                   'captured', 'normal_clock'}
FRAME_COLUMNS = {'phase', 'frame', 't_game_ms', 'dt_ms', 'npc_id', 'x_cm', 'y_cm',
                 'z_cm', 'visual_x_cm', 'visual_y_cm', 'visual_z_cm', 'step_ms',
                 'wall_ms', 'nearby_humans', 'nearby_cv'} | BOOLEAN_COLUMNS
OPTIONAL_FRAME_COLUMNS = {'moving_humans', 'moving_cv'}


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
    if isinstance(value, dict):
        value = [value.get(key) for key in ('x', 'y', 'z')]
    require(isinstance(value, (list, tuple)) and len(value) == 3,
            f'invalid position: {value!r}')
    return [number(part) for part in value]


def combined(checks):
    if any(check is False for check in checks):
        return False
    return True if checks and all(check is True for check in checks) else None


def percentile(values, fraction=.95):
    return sorted(values)[math.ceil(fraction * len(values)) - 1] if values else None


def load_run(directory, enabled, require_frames=True):
    run = {'directory': str(directory), 'enabled': enabled, 'events': [],
           'frames': [], 'errors': [], 'missing': [], 'complete': None}
    required_files = ('events.jsonl', 'bench.log') + (('npc-character-frames.csv',) if require_frames else ())
    for filename in required_files:
        if not (directory / filename).is_file():
            run['missing'].append(filename)
    event_path = directory / 'events.jsonl'
    if event_path.is_file():
        try:
            for index, line in enumerate(event_path.read_text().splitlines(), 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                require(isinstance(row, dict) and isinstance(row.get('kind'), str),
                        f'{event_path}:{index}: invalid event')
                if row['kind'].endswith('_error'):
                    run['errors'].append(f'native error: {row}')
                if row['kind'].startswith('npc_character_'):
                    if 'cv_enabled' in row:
                        require(flag(row['cv_enabled']) is enabled,
                                f'{event_path}:{index}: mode differs from directory')
                    run['events'].append(row)
        except (OSError, ValueError, TypeError) as error:
            run['errors'].append(str(error))
    frame_path = directory / 'npc-character-frames.csv'
    if frame_path.is_file():
        try:
            prior = {}
            with frame_path.open(newline='') as source:
                reader = csv.DictReader(source)
                require(FRAME_COLUMNS <= set(reader.fieldnames or []),
                        f'{frame_path}: missing columns {sorted(FRAME_COLUMNS - set(reader.fieldnames or []))}')
                for index, raw in enumerate(reader, 2):
                    row = {key: (value if key == 'phase' else flag(value) if key in BOOLEAN_COLUMNS
                                 else number(value)) for key, value in raw.items()
                           if key in FRAME_COLUMNS | OPTIONAL_FRAME_COLUMNS}
                    require(row['phase'] and row['dt_ms'] > 0 and row['step_ms'] >= 0 and row['wall_ms'] >= 0,
                            f'{frame_path}:{index}: invalid phase or timing')
                    require(row['cv_enabled'] is enabled and (enabled or not row['cv_active']),
                            f'{frame_path}:{index}: native controller mode mismatch')
                    key = (row['phase'], row['npc_id'])
                    last = prior.get(key)
                    require(last is None or (row['frame'] > last['frame'] and row['t_game_ms'] > last['t_game_ms']),
                            f'{frame_path}:{index}: non-monotonic actor samples')
                    prior[key] = row
                    run['frames'].append(row)
        except (OSError, ValueError, TypeError) as error:
            run['errors'].append(str(error))
    log = directory / 'bench.log'
    if log.is_file():
        run['complete'] = 'done' in log.read_text().splitlines()
    run['pass'] = combined([False if run['errors'] else True,
                            None if run['missing'] else True,
                            True if run['complete'] is True else None])
    return run


def select(run, kind):
    return [row for row in run['events'] if row['kind'] == kind]


def unique_event(run, kind, key=None, value=None):
    rows = [row for row in select(run, kind) if key is None or row.get(key) == value]
    require(len(rows) <= 1, f"{run['directory']}: duplicate {kind} {value or ''}")
    return rows[0] if rows else None


def activity(frames, enabled):
    moving = [row for row in frames if row['moving'] and not row['interactive']]
    active = sum(row['cv_active'] for row in moving)
    return {'frames': len(frames), 'moving_frames': len(moving), 'cv_active_moving_frames': active,
            'cv_active_frames': sum(row['cv_active'] for row in frames),
            'pass': (active > 0 if enabled else active == 0) if moving else None}


def route_result(run, name):
    end = unique_event(run, 'npc_character_route_end', 'route', name)
    frames = [row for row in run['frames'] if row['phase'] == name]
    result = {'success': None, 'elapsed_ms': None, 'stalls': None,
              'max_visual_jump_cm': None, 'max_physical_jump_cm': None,
              'max_above_floor_cm': None, 'native_activity': activity(frames, run['enabled']),
              'pass': None}
    if end is None:
        result['reason'] = 'Missing native route end.'
        return result
    for key in ('elapsed_ms', 'window_ms', 'stalls', 'max_visual_jump_cm',
                'max_physical_jump_cm', 'max_above_floor_cm'):
        if key in end:
            result[key] = number(end[key])
            require(result[key] >= 0, f'{name}: negative {key}')
    if 'success' in end:
        result['success'] = flag(end['success'])
    for key in ('sat', 'standing', 'mug_native_contact'):
        if key in end:
            result[key] = flag(end[key])
    for key in ('mug_mass_kg', 'mug_max_displacement_cm'):
        if key in end:
            result[key] = number(end[key])
    for key in ('start_cm', 'goal_cm', 'end_cm'):
        if key in end:
            result[key] = vector(end[key])
    by_actor = defaultdict(list)
    for row in frames:
        by_actor[row['npc_id']].append(row)
    deltas = [abs(b['visual_y_cm'] - a['visual_y_cm']) for rows in by_actor.values()
              for a, b in zip(rows, rows[1:])]
    result['csv_max_visual_jump_cm'] = max(deltas, default=None)
    result['native_event'] = end
    if name == 'seat':
        active = result['native_activity']['cv_active_frames']
        result['native_activity']['pass'] = (active > 0 if run['enabled'] else active == 0) if frames else None
        result['seating_pass'] = combined([result.get('sat'), result.get('standing')])
    if name == 'tavern':
        result['mug_push_pass'] = (result['mug_native_contact'] and result['mug_max_displacement_cm'] > 1 and
                                   0 < result['mug_mass_kg'] < 10) if all(key in result for key in
                                   ('mug_native_contact', 'mug_max_displacement_cm', 'mug_mass_kg')) else None
    required = ('success', 'elapsed_ms', 'window_ms', 'stalls', 'max_visual_jump_cm',
                'max_physical_jump_cm', 'start_cm', 'goal_cm', 'end_cm')
    checks = [run['pass'], True if all(result.get(key) is not None for key in required) else None,
              True if frames else None, result['native_activity']['pass']]
    if name == 'seat':
        checks.append(result['seating_pass'])
    if name == 'tavern' and run['enabled']:
        checks.append(result['mug_push_pass'])
    result['pass'] = combined(checks)
    return result


def compare_routes(off, on, required_routes=None):
    names = sorted(set(required_routes or ROUTES) | {row['route'] for run in (off, on)
                   for row in select(run, 'npc_character_route_end')})
    result = []
    for name in names:
        before, after = route_result(off, name), route_result(on, name)
        matched = None
        if all(all(key in row for key in ('start_cm', 'goal_cm', 'window_ms')) for row in (before, after)):
            matched = (math.dist(before['start_cm'], after['start_cm']) <= 1 and
                       math.dist(before['goal_cm'], after['goal_cm']) <= 1 and
                       before['window_ms'] == after['window_ms'])
        progress = None
        if all(row['success'] is not None and row['stalls'] is not None for row in (before, after)):
            progress = after['success'] and after['stalls'] <= before['stalls']
        result.append({'route': name, 'off': before, 'on': after,
                       'matched_fixture': matched, 'movement_pass': progress,
                       'pass': combined([before['pass'], after['pass'], matched, progress])})
    return {'routes': result, 'pass': combined([row['pass'] for row in result])}


def cadence(directory, enabled):
    metadata = directory / 'appcadence.csv.json'
    csv_path = directory / 'appcadence.csv'
    if not metadata.is_file() or not csv_path.is_file():
        return {'pass': None, 'reason': 'Missing native application-frame cadence CSV or metadata.'}
    meta = json.loads(metadata.read_text())
    require(meta.get('clock') == 'normal' and meta.get('complete') is True and meta.get('overflow') == 0,
            f'{metadata}: incomplete normal-clock cadence')
    require(meta.get('mode') == ('on' if enabled else 'off'), f'{metadata}: cadence mode mismatch')
    start, window_end = int(meta['start_ns']), int(meta['window_end_ns'])
    require(window_end - start == 30_000_000_000, f'{metadata}: expected 30-second window')
    with csv_path.open(newline='') as source:
        rows = list(csv.DictReader(source))
    require(rows and len(rows) == int(meta['frames']), f'{csv_path}: sample count mismatch')
    prior = start
    for index, row in enumerate(rows):
        end, interval = int(row['end_ns']), int(row['interval_ns'])
        require(int(row['frame']) == index and end > prior and interval == end - prior,
                f'{csv_path}: non-monotonic cadence')
        require(index + 1 == len(rows) or end < window_end, f'{csv_path}: extra frame after window')
        prior = end
    require(prior == int(meta['final_end_ns']) and prior >= window_end, f'{metadata}: missing final frame tail')
    return {'pass': True, 'app_frames': len(rows), 'app_window_ms': (prior - start) / 1e6,
            'mean_fps': 1e9 * len(rows) / (prior - start)}


def performance(run, minimum_nearby):
    all_frames = [row for row in run['frames'] if row['normal_clock'] and not row['captured']]
    result = {'pass': None, 'native_activity': activity(all_frames, run['enabled'])}
    if not all_frames:
        result['reason'] = 'No uncaptured normal-clock physics samples.'
        return result
    unique = {}
    same = ('step_ms', 'wall_ms', 'nearby_humans', 'nearby_cv', 'moving_humans', 'moving_cv')
    for row in all_frames:
        key = (row['frame'], row['t_game_ms'])
        require(key not in unique or all(unique[key].get(field) == row.get(field) for field in same),
                f"{run['directory']}: per-actor copies disagree on world-frame measurement")
        unique[key] = row
    frames = list(unique.values())
    if all('moving_cv' in row and 'moving_humans' in row for row in frames):
        moving = sum(row['moving_humans'] for row in frames)
        active = sum(row['moving_cv'] for row in frames)
        require(all(0 <= row['moving_cv'] <= row['moving_humans'] and
                    row['moving_cv'] <= row['nearby_cv'] for row in frames),
                f"{run['directory']}: invalid native moving-actor counts")
        result['native_activity'].update(moving_actor_frames=moving, cv_active_moving_actor_frames=active,
                                         **{'pass': (active > 0 if run['enabled'] else active == 0)
                                            if moving else None})
    near = [row['nearby_humans'] for row in frames]
    enough = sum(count >= minimum_nearby for count in near) / len(near)
    costs = [row['step_ms'] for row in frames]
    app = cadence(Path(run['directory']), run['enabled'])
    result.update(samples=len(frames), physics_p95_ms=percentile(costs), physics_mean_ms=statistics.mean(costs),
                  nearby_humans_min=min(near), nearby_humans_mean=statistics.mean(near), nearby_humans_max=max(near),
                  nearby_humans_minimum=minimum_nearby, nearby_sufficient_sample_fraction=enough,
                  nearby_cv_max=max(row['nearby_cv'] for row in frames), app_cadence=app)
    if 'mean_fps' in app:
        result['mean_fps'] = app['mean_fps']
    result['pass'] = combined([run['pass'], app['pass'], result['native_activity']['pass'], enough >= .95])
    return result


def compare_performance(off, on, minimum_nearby):
    before, after = performance(off, minimum_nearby), performance(on, minimum_nearby)
    result = {'off': before, 'on': after, 'physics_p95_limit_delta_ms': .5,
              'fps_drop_limit_percent': 5, 'pass': None}
    if all('mean_fps' in row and 'physics_p95_ms' in row for row in (before, after)):
        delta = after['physics_p95_ms'] - before['physics_p95_ms']
        loss = 100 * (1 - after['mean_fps'] / before['mean_fps'])
        result.update(physics_p95_delta_ms=delta, fps_drop_percent=loss,
                      physics_pass=delta <= .5, fps_pass=loss <= 5)
        result['pass'] = combined([before['pass'], after['pass'], delta <= .5, loss <= 5])
    return result


def stuck_observation(directory, label):
    summaries = sorted(directory.glob('*stuck*.summary.json'))
    if not summaries:
        summaries = sorted(directory.glob('*.summary.json'))
    observations = []
    for path in summaries:
        summary = json.loads(path.read_text())
        observations += [(path, row) for row in summary.get('observations', []) if row.get('sample') == label]
    require(len(observations) <= 1, f'{directory}: duplicate stuck observation {label}')
    if not observations:
        return {'pass': None, 'reason': f'Missing same-sample stuck summary for {label}.'}
    path, row = observations[0]
    logfile = Path(str(path)[:-len('.summary.json')])
    if not logfile.is_file():
        return {'pass': None, 'summary': row, 'reason': f'Missing stuck event log {logfile.name}.'}
    events = []
    for line in logfile.read_text().splitlines():
        if line.strip():
            event = json.loads(line)
            if event.get('type', event.get('kind')) == 'npc_stuck' and event.get('sample') == label:
                events.append(event)
    count = int(row['stuck_events'])
    censored = int(row['censored_events'])
    require(count >= 0 and 0 <= censored <= count, f'{path}: invalid stuck counts')
    require(len(events) == count and sum(flag(event['censored']) for event in events) == censored,
            f'{path}: stuck summary/event counts differ')
    require(row['end_reason'] == 'observation_end' and number(row['simulation_duration_ms']) > 0,
            f'{path}: observation did not finish normally')
    return {'pass': True, 'summary': row, 'events': events, 'summary_file': str(path),
            'stuck_events': count, 'censored_events': censored, 'unique_walkers': int(row['unique_walkers'])}


def window_result(directory, label, enabled):
    run = load_run(directory, enabled, require_frames=False)
    stuck = stuck_observation(directory, label)
    observe = unique_event(run, 'npc_character_observe_end', 'window', label)
    result = {'directory': str(directory), 'run': run_status(run), 'stuck': stuck,
              'native_observe': observe, 'pass': None}
    if observe is not None:
        required = ('window_ms', 'unique_walkers', 'reached_goals', 'frames', 'cv_actor_frames', 'max_nearby_humans')
        require(all(number(observe[key]) >= 0 for key in required), f'{directory}: negative observe metric')
        native = number(observe['frames']) > 0 and (number(observe['cv_actor_frames']) > 0 if enabled
                                                   else number(observe['cv_actor_frames']) == 0)
        summary = stuck.get('summary')
        same_sample = None
        if summary is not None:
            # The same name is required above; permit at most one native tick at each edge.
            same_sample = (abs(number(observe['window_ms']) - number(summary['simulation_duration_ms'])) <= 500 and
                           number(observe['radius_cm']) == number(summary['radius_cm']))
        result['same_sample'] = same_sample
        result['pass'] = combined([run['pass'], stuck['pass'], native, same_sample])
    return result


def compare_windows(directory):
    rows = []
    for label in WINDOWS:
        before = window_result(directory / 'off' / label, label, False)
        after = window_result(directory / 'on' / label, label, True)
        matched = progress = None
        a, b = before['stuck'].get('summary'), after['stuck'].get('summary')
        if a is not None and b is not None:
            matched = (a['sample'] == b['sample'] and a['world'] == b['world'] and
                       math.dist(vector(a['center']), vector(b['center'])) <= 1 and
                       number(a['radius_cm']) == number(b['radius_cm']) and
                       abs(number(a['start_game_time_ms']) - number(b['start_game_time_ms'])) <= 1000 and
                       abs(number(a['end_game_time_ms']) - number(b['end_game_time_ms'])) <= 1000 and
                       abs(number(a['simulation_duration_ms']) - number(b['simulation_duration_ms'])) <= 500)
        native_off, native_on = before['native_observe'], after['native_observe']
        if native_off is not None and native_on is not None and a is not None and b is not None:
            progress = (number(b['stuck_events']) <= number(a['stuck_events']) and
                        number(native_on['reached_goals']) >= number(native_off['reached_goals']))
        rows.append({'window': label, 'off': before, 'on': after, 'matched_window': matched,
                     'progress_pass': progress,
                     'pass': combined([before['pass'], after['pass'], matched, progress])})
    return {'windows': rows, 'pass': combined([row['pass'] for row in rows])}


def run_status(run):
    return {key: run[key] for key in ('directory', 'complete', 'missing', 'errors', 'pass')}


def display(value):
    if value is None:
        return '—'
    if isinstance(value, bool):
        return 'да' if value else 'нет'
    if isinstance(value, float):
        return f'{value:.3f}'.rstrip('0').rstrip('.')
    return str(value).replace('|', '\\|')


def state(value):
    return {True: 'PASS', False: 'FAIL', None: 'НЕПОЛНО'}[value]


def markdown(result):
    lines = [f"NPC CharacterVirtual ON/OFF: **{state(result['pass'])}**. «—» означает отсутствие данных."]
    if 'error' in result:
        return '\n'.join(lines + ['', result['error']]) + '\n'
    lines += ['', '| Маршрут | Дошёл OFF / ON | Время, мс OFF / ON | Застревания OFF / ON | Макс. ΔY модели за кадр, см OFF / ON | Кадры CV ON всего / в движении | Итог |',
              '|---|---|---:|---:|---:|---:|---|']
    for row in result['routes']['routes']:
        before, after = row['off'], row['on']
        pair = lambda key: f'{display(before.get(key))} / {display(after.get(key))}'
        native = after['native_activity']
        lines.append(f"| {display(row['route'])} | {pair('success')} | {pair('elapsed_ms')} | {pair('stalls')} | {pair('max_visual_jump_cm')} | {display(native['cv_active_frames'])} / {display(native['cv_active_moving_frames'])} | {state(row['pass'])} |")
    tavern = next((row for row in result['routes']['routes'] if row['route'] == 'tavern'), None)
    if tavern is not None:
        lines += ['', '| Кружка в таверне | OFF | ON |', '|---|---:|---:|']
        for label, key in (('Масса, кг', 'mug_mass_kg'), ('Макс. смещение, см', 'mug_max_displacement_cm'),
                           ('Нативный контакт NPC', 'mug_native_contact'), ('Толкнул', 'mug_push_pass'),
                           ('Макс. высота корня над полом, см', 'max_above_floor_cm')):
            lines.append(f"| {label} | {display(tavern['off'].get(key))} | {display(tavern['on'].get(key))} |")
        lines += ['', 'Высота над полом приведена как измерение; она сама по себе не доказывает отсутствие опоры на кружку.']
    lines += ['', '| Окно | Застревания OFF / ON | Цензурированные OFF / ON | Уникальные идущие (детектор) OFF / ON | Уникальные идущие (native observe) OFF / ON | Достигнутые цели OFF / ON | CV actor-frames ON | Та же выборка | Итог |',
              '|---|---:|---:|---:|---:|---:|---:|---|---|']
    for row in result['windows']['windows']:
        before, after = row['off'], row['on']
        stuck_pair = lambda key: f"{display(before['stuck'].get(key))} / {display(after['stuck'].get(key))}"
        native_pair = lambda key: f"{display((before['native_observe'] or {}).get(key))} / {display((after['native_observe'] or {}).get(key))}"
        lines.append(f"| {row['window']} | {stuck_pair('stuck_events')} | {stuck_pair('censored_events')} | {stuck_pair('unique_walkers')} | {native_pair('unique_walkers')} | {native_pair('reached_goals')} | {display((after['native_observe'] or {}).get('cv_actor_frames'))} | {display(row['matched_window'])} | {state(row['pass'])} |")
    perf = result['performance']
    a, b = perf['off'], perf['on']
    lines += ['', '| Производительность | OFF | ON | Изменение | Лимит |', '|---|---:|---:|---:|---:|',
              f"| Шаг физики p95, мс | {display(a.get('physics_p95_ms'))} | {display(b.get('physics_p95_ms'))} | {display(perf.get('physics_p95_delta_ms'))} | +0,5 мс |",
              f"| Средний FPS приложения | {display(a.get('mean_fps'))} | {display(b.get('mean_fps'))} | {display(perf.get('fps_drop_percent'))}% потери | 5% |",
              f"| NPC-люди рядом, мин / среднее / макс | {' / '.join(display(a.get('nearby_humans_' + key)) for key in ('min', 'mean', 'max'))} | {' / '.join(display(b.get('nearby_humans_' + key)) for key in ('min', 'mean', 'max'))} | — | ≥{result['minimum_nearby_humans']} в ≥95% кадров |",
              '', 'FPS — завершённые кадры приложения за обычное 30-секундное окно; физика — только кадры без видеозаписи и с обычными часами. Флаг ON сам по себе не доказывает работу контроллера.',
              '', 'Детектор учитывает всех подходящих NPC, native observe — людей; счётчики уникальных идущих независимы и не означают число дошедших. Достигнутые цели берутся только из native observe. Цензурированные застревания включены в общее число.']
    for section in ('routes', 'performance'):
        for mode in ('off', 'on'):
            run = result['runs'][section][mode]
            if run['missing'] or run['errors'] or run['complete'] is not True:
                lines += ['', f"{section} {mode.upper()}: {state(run['pass'])}; завершён: {display(run['complete'])}; отсутствуют: {', '.join(run['missing']) or '—'}; ошибки: {'; '.join(run['errors']) or '—'}."]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, nargs='?', default=Path('.bench/npc-character-virtual'))
    parser.add_argument('--routes-off', type=Path)
    parser.add_argument('--routes-on', type=Path)
    parser.add_argument('--performance-off', '--perf-off', type=Path)
    parser.add_argument('--performance-on', '--perf-on', type=Path)
    parser.add_argument('--windows', type=Path)
    parser.add_argument('--required-route', action='append', default=[], help='Repeat to require a fixture even when no route end was recorded')
    parser.add_argument('--min-nearby-humans', type=int, default=18)
    parser.add_argument('--output', type=Path, help='Markdown output and JSON beside it; .json selects the JSON destination')
    args = parser.parse_args()
    try:
        require(args.min_nearby_humans > 0, 'minimum nearby humans must be positive')
        root = args.directory
        routes_off = load_run(args.routes_off or root / 'routes-off', False)
        routes_on = load_run(args.routes_on or root / 'routes-on', True)
        perf_off = load_run(args.performance_off or root / 'performance-off', False)
        perf_on = load_run(args.performance_on or root / 'performance-on', True)
        routes = compare_routes(routes_off, routes_on, args.required_route)
        perf = compare_performance(perf_off, perf_on, args.min_nearby_humans)
        windows = compare_windows(args.windows or root / 'windows')
        result = {'pass': combined([routes['pass'], perf['pass'], windows['pass']]),
                  'minimum_nearby_humans': args.min_nearby_humans, 'routes': routes,
                  'performance': perf, 'windows': windows,
                  'runs': {'routes': {'off': run_status(routes_off), 'on': run_status(routes_on)},
                           'performance': {'off': run_status(perf_off), 'on': run_status(perf_on)}},
                  'notes': ['null means insufficient evidence, never a pass.',
                            'This analyzer covers route, natural-window and performance evidence. Other native fixtures, save/reload, m5b and follower e2e require their own checks.']}
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'pass': False, 'error': str(error)}
    report = markdown(result)
    print(report, end='')
    if args.output:
        md_path = args.output.with_suffix('.md') if args.output.suffix == '.json' else args.output
        json_path = args.output if args.output.suffix == '.json' else args.output.with_suffix('.json')
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(report)
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    return 0 if result['pass'] is True else 1 if result['pass'] is False else 2


if __name__ == '__main__':
    raise SystemExit(main())
