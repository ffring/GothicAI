#!/usr/bin/env python3
"""Check the water bench's native measurements and fresh-process save restore."""
import argparse
import csv
import json
import math
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def events(directory):
    require('done' in (directory / 'bench.log').read_text().splitlines(),
            f'{directory}: scenario did not finish')
    rows = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
    require(not any(row['kind'].endswith('_error') for row in rows),
            f'{directory}: fixture preparation failed')
    return rows


def checkpoint(rows, label):
    found = [row for row in rows if row['kind'] == 'water_checkpoint' and row['label'] == label]
    require(len(found) == 1, f'expected one water checkpoint {label}, got {len(found)}')
    return found[0]


def resting(row):
    bodies = row['bodies']
    require(len(bodies) >= 7, 'water fixture needs crate, barrel, table and four genuine Items')
    result = []
    for body in bodies:
        density = body['density_kg_m3']
        fraction = body['submerged_fraction']
        require(math.isfinite(density) and density > 0 and 0 <= fraction <= 1.001,
                f'invalid water measurement: {body}')
        floating = density < 1000
        if floating and row['case'] == 'deep':
            require(body['world_bottom_cm'] < body['water_y_cm'] < body['world_top_cm'],
                    f'{body["label"]}: wooden shell is not floating across the surface')
            require(abs(fraction - density / 1000) < .06,
                    f'{body["label"]}: displaced volume does not match density')
        elif not floating and row['case'] == 'deep':
            require(fraction >= .99, f'{body["label"]}: dense shell did not sink')
        require(body['sleeping'], f'{body["label"]}: still moving after settling')
        result.append({'what': body['label'], 'density_kg_m3': density,
                       'behaviour': 'floats' if floating else 'sinks',
                       'draft_cm': (max(0, body['water_y_cm'] - body['world_bottom_cm'])
                                    if body['water_y_cm'] is not None else 0),
                       'submerged_fraction': fraction,
                       'sleep_ms': body['first_sleep_ms']})
    return result


def identity(body):
    if body['category'] == 6:
        return ('item', body['visual'])
    return (body['category'], body['zen_id'], body['piece'])


def restored(saved, loaded):
    before = {identity(body): body for body in saved['bodies']}
    after = {identity(body): body for body in loaded['bodies']}
    require(len(before) == len(saved['bodies']), 'save fixture has ambiguous body identities')
    require(before.keys() <= after.keys(), 'saved water body missing in fresh process')
    maximum = 0
    for key, body in before.items():
        delta = math.dist(body['position_cm'], after[key]['position_cm'])
        maximum = max(maximum, delta)
        require(delta <= 1, f'{key}: fresh-process displacement {delta:.6f} cm exceeds 1 cm')
        require(after[key]['sleeping'], f'{key}: restored sleeper woke up')
    return {'bodies': len(before), 'maximum_displacement_cm': maximum}


def percentile(values):
    require(values and all(math.isfinite(value) and value >= 0 for value in values),
            'missing or invalid timing samples')
    return sorted(values)[math.ceil(.95 * len(values)) - 1]


def measured(directory):
    rows = events(directory)
    setup = [row for row in rows if row['kind'] == 'water_setup']
    require(len(setup) == 1 and setup[0]['case'] == 'perf' and len(setup[0]['bodies']) == 30,
            'performance requires thirty native fixture bodies')
    settled = checkpoint(rows, 'settled')
    require(len(settled['bodies']) == 30, 'performance fixture lost a barrel before the timing window')
    require(not settled['buoyancy_enabled'] or all(body['submerged_fraction'] > 0 for body in settled['bodies']),
            'enabled performance fixture is not floating in the real water')
    meta = json.loads((directory / 'appcadence.csv.json').read_text())
    require(meta['clock'] == 'normal' and meta['complete'] and meta['overflow'] == 0,
            'performance requires complete normal-game cadence')
    require(meta['window_end_ns'] - meta['start_ns'] == 30_000_000_000,
            'performance window must be thirty seconds')
    with (directory / 'appcadence.csv').open() as source:
        frames = list(csv.DictReader(source))
    require(len(frames) == meta['frames'] and frames, 'incomplete app-frame samples')
    with (directory / 'movable-physics.csv').open() as source:
        physics = list(csv.DictReader(source))
    return {'fps': len(frames) / ((meta['final_end_ns'] - meta['start_ns']) / 1e9),
            'frame_p95_ms': percentile([int(frame['interval_ns']) / 1e6 for frame in frames]),
            'physics_p95_ms': percentile([float(row['step_ms']) for row in physics]),
            'physics_samples': len(physics), 'frames': len(frames),
            'fixture_bodies': len(settled['bodies']),
            'sleeping_bodies': sum(body['sleeping'] for body in settled['bodies'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('functional', type=Path, nargs='?')
    parser.add_argument('--shallow', type=Path)
    parser.add_argument('--deep-label', default='settled')
    parser.add_argument('--shallow-label', default='settled')
    parser.add_argument('--reload', type=Path)
    parser.add_argument('--saved-label', default='saved')
    parser.add_argument('--reload-label', default='continued')
    parser.add_argument('--perf-on', type=Path)
    parser.add_argument('--perf-off', type=Path)
    args = parser.parse_args()
    result = {}
    if args.functional:
        rows = events(args.functional)
        result['deep'] = resting(checkpoint(rows, args.deep_label))
        if args.reload:
            loaded = events(args.reload)
            saved = checkpoint(rows, args.saved_label)
            result['restore'] = {'initial': restored(saved, checkpoint(loaded, 'restored')),
                                 'after_30_seconds': restored(saved, checkpoint(loaded, args.reload_label))}
    if args.shallow:
        result['shallow'] = resting(checkpoint(events(args.shallow), args.shallow_label))
    if args.perf_on or args.perf_off:
        require(args.perf_on and args.perf_off, 'performance needs both ON and OFF')
        on, off = measured(args.perf_on), measured(args.perf_off)
        result['performance'] = {'on': on, 'off': off,
                                 'fps_change_percent': (on['fps'] / off['fps'] - 1) * 100,
                                 'physics_p95_delta_ms': on['physics_p95_ms'] - off['physics_p95_ms']}
        require(on['physics_p95_ms'] <= 2, 'water physics p95 exceeds the two millisecond budget')
    require(result, 'choose a functional or performance measurement')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
