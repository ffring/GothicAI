#!/usr/bin/env python3
"""Read Coragon movable-vob acceptance, matched normal-game OFF/ON, and fresh save reload."""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def events(directory):
    require('done' in (directory / 'bench.log').read_text().splitlines(), f'{directory}: unfinished scenario')
    rows = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
    errors = [row for row in rows if row['kind'].endswith('_error')]
    require(not errors, f'{directory}: game fixture error: {errors}')
    return rows


def one(rows, kind, **fields):
    found = [row for row in rows if row['kind'] == kind and all(row.get(key) == value for key, value in fields.items())]
    require(len(found) == 1, f'expected one {kind} {fields}, got {len(found)}')
    return found[0]


def bodies(event):
    rows = event['bodies']
    require(rows and len({row['id'] for row in rows}) == len(rows), 'empty or duplicate fixture bodies')
    for row in rows:
        require(len(row['position_cm']) == 3 and len(row['transform']) == 16 and
                all(math.isfinite(x) for x in row['position_cm'] + row['transform'] + row['velocity_cm_s']),
                f'non-finite native transform: {row}')
        require(math.isfinite(row['mass_kg']) and row['mass_kg'] >= 0 and
                (not row['enabled'] or row['mass_kg'] > 0), f'invalid hull mass: {row}')
        require(math.isfinite(row['max_displacement_cm']) and row['max_displacement_cm'] >= 0,
                f'invalid displacement: {row}')
    return {row['id']: row for row in rows}


def setup(rows, enabled):
    loaded = one(rows, 'movable_loaded')
    require(loaded['enabled'] is enabled, 'movable_vobs must be configured in INI before loading, not just toggled in the bench')
    result = one(rows, 'movable_setup')
    require(result['enabled'] is enabled and result['world'] == loaded['world'], 'loaded fixture mode/world mismatch')
    require('CORAGON' in result['host'].upper() or 'KORAGON' in result['host'].upper(), 'fixture is not Coragon tavern')
    require(result['tabletop_utensils'] >= 6, 'tabletop dishes absent')
    require(math.isfinite(loaded['world_load_ms']) and loaded['world_load_ms'] > 0, 'world construction timing absent')
    catalog = bodies(result)
    if 'map_vobs' in result:
        require(result['map_vobs'] == sum(row['from_map'] for row in catalog.values()), 'fixture changed the authored map-vob count')
        require(result['temporary_vobs'] == sum(not row['from_map'] for row in catalog.values()), 'temporary props must be distinguished from map furniture')
    require(catalog[result['table_id']]['class'] == 'table', 'fixture lacks its actual map table')
    require(catalog[result['floor_dish_id']]['class'] == 'utensil', 'fixture floor dish missing')
    require(all(row['enabled'] is enabled for row in catalog.values()), 'native body mode differs from requested setting')
    return result, catalog, loaded['world_load_ms']


def cast(rows):
    one(rows, 'movable_blast', spell_id=23)
    one(rows, 'spell_physics_cast', id=23)
    one(rows, 'movable_cast_setup', spell_id=23)


def functional(directory):
    rows = events(directory)
    initial, catalog, load_ms = setup(rows, True)
    start = bodies(one(rows, 'movable_begin', phase='untouched'))
    require(start.keys() == catalog.keys(), 'untouched baseline lost bodies')
    require(all(row['sleeping'] for row in start.values()), 'a vob was created awake before the untouched interval')
    untouched = one(rows, 'movable_checkpoint', phase='untouched')
    require(60_000 <= untouched['elapsed_ms'] <= 60_100, 'untouched interval must observe all 60 simulation seconds')
    resting = bodies(untouched)
    require(resting.keys() == catalog.keys(), 'untouched checkpoint lost fixture bodies')
    touched = set(untouched['npc_touched_ids'])
    spontaneous = [row for key, row in resting.items() if key not in touched and row['max_displacement_cm'] > 1]
    require(not spontaneous, f'spontaneous drift >1cm without native NPC contact: {spontaneous}')
    npc_moved = [row for key, row in resting.items() if key in touched and row['max_displacement_cm'] > 1]
    walk = one(rows, 'movable_walk_setup')
    require(walk['off_passable'] is False and walk['on_passable'] is False, 'table does not block the same native walking query OFF and ON')
    walked = one(rows, 'movable_checkpoint', phase='walk')
    dish = bodies(walked)[initial['floor_dish_id']]
    require(dish['max_displacement_cm'] > 1 and dish['npc_contact'], f'walking NPC did not physically push floor dish: {dish}')
    origin, target = initial['walk_start_cm'], initial['walk_end_cm']
    displacement = [b-a for a, b in zip(origin, target)]
    progress = sum((b-a)*d for a, b, d in zip(origin, walked['walker_cm'], displacement)) / math.dist(origin, target)
    require(progress > math.dist(origin, target) * .65, f'NPC did not cross the floor dish, progress={progress}cm')
    cast(rows)
    blast_start = bodies(one(rows, 'movable_begin', phase='blast'))
    blast = one(rows, 'movable_checkpoint', phase='blast')
    require(15_000 <= blast['elapsed_ms'] <= 15_100, 'sleep must be sampled at 15 seconds, without an extended wait')
    final = bodies(blast)
    require(final.keys() == blast_start.keys(), 'fireball lost native vob bodies')
    moved = [row for row in final.values() if row['max_displacement_cm'] > 20]
    require(len(moved) >= 5 and any(row['class'] in ('table', 'barrel') for row in moved),
            f'fireball moved only {len(moved)} things >20cm, or no table/barrel')
    awake = [row for row in final.values() if not row['sleeping']]
    require(not awake, f'bodies still awake 15s after actual fireball: {awake}')
    saved = one(rows, 'movable_session_save')
    archive = directory / saved['archive']
    require(archive.is_file() and archive.stat().st_size == saved['bytes'] and saved['bytes'] > 1000, 'complete GameSession archive absent/truncated')
    masses = initial['mass_examples']
    require(masses and len({row['class'] for row in masses}) == len(masses), 'mass catalog is empty or duplicates classes')
    require(all(math.isfinite(row['kg']) and row['kg'] > 0 for row in masses), 'mass catalog contains invalid hull mass')
    return {'pass': True, 'world_load_ms': load_ms, 'observed_vobs': len(catalog),
            'map_vobs': initial.get('map_vobs'), 'map_classes': initial.get('map_classes'),
            'noncolliding_map_classes': initial.get('noncolliding_map_classes'),
            'untouched_ms': untouched['elapsed_ms'],
            'maximum_unprovoked_drift_cm': max((row['max_displacement_cm'] for key, row in resting.items() if key not in touched), default=0),
            'npc_caused_moved_vobs': npc_moved, 'npc_walk_progress_cm': progress,
            'floor_dish_displacement_cm': dish['max_displacement_cm'], 'fireball_moved_over_20cm': moved,
            'asleep_at_ms': blast['elapsed_ms'], 'archive_bytes': saved['bytes'], 'mass_examples': masses,
            'note': 'Native contact attribution includes propagation from NPC-touched movable bodies. Temporary real-mesh test dishes are not save-format entries.'}


def percentile(values):
    require(values and all(math.isfinite(v) and v >= 0 for v in values), 'timing samples absent or non-finite')
    return sorted(values)[math.ceil(len(values)*.95)-1]


def measured(directory, enabled):
    rows = events(directory)
    fixture, catalog, load_ms = setup(rows, enabled)
    cast(rows)
    before = one(rows, 'movable_measure_before')
    after = one(rows, 'movable_measure_after')
    require(before['enabled'] is enabled and not after['overflow'], 'measurement mode mismatch or overflow')
    require(bodies(before).keys() == bodies(after).keys() == catalog.keys(), 'measurement changed fixture body set')
    meta = json.loads((directory / 'appcadence.csv.json').read_text())
    require(meta['mode'] == ('on' if enabled else 'off') and meta['clock'] == 'normal', 'fixed simulation dt is not game FPS')
    require(meta['complete'] and meta['overflow'] == 0 and meta['window_end_ns'] - meta['start_ns'] == 30_000_000_000,
            'normal-game 30 second cadence window incomplete')
    frames = list(csv.DictReader((directory / 'appcadence.csv').open()))
    require(frames and len(frames) == meta['frames'], 'cadence row count differs')
    prior, intervals = meta['start_ns'], []
    for index, row in enumerate(frames):
        end, delta = int(row['end_ns']), int(row['interval_ns'])
        require(int(row['frame']) == index and end > prior and delta == end-prior, 'non-monotonic app-frame timestamp')
        require(index == len(frames)-1 or end < meta['window_end_ns'], 'cadence continued after final frame')
        intervals.append(delta/1e6)
        prior = end
    require(prior == meta['final_end_ns'] and prior >= meta['window_end_ns'], 'final frame tail missing')
    native = list(csv.DictReader((directory / 'movable-physics.csv').open()))
    require(native, 'native physics samples absent')
    previous = -1
    for index, row in enumerate(native):
        tick = int(row['t_game_ms'])
        require(int(row['sample']) == index and tick > previous and int(row['dt_ms']) > 0, 'invalid native step sequence')
        previous = tick
    costs = [float(row['step_ms']) for row in native]
    return {'mean_fps': len(frames)/((prior-meta['start_ns'])/1e9), 'app_frame_p95_ms': percentile(intervals),
            'physics_p95_ms': percentile(costs), 'physics_mean_ms': statistics.mean(costs),
            'native_samples': len(native), 'world_load_ms': load_ms,
            'fixture': sorted((row['visual'], row['class']) for row in catalog.values())}


def performance(directory):
    off, on = measured(directory/'off', False), measured(directory/'on', True)
    require(off['fixture'] == on['fixture'], 'OFF and ON contain different real mesh fixtures')
    drop = (1-on['mean_fps']/off['mean_fps'])*100
    return {'pass': on['physics_p95_ms'] <= 2 and drop <= 5, 'off': off, 'on': on, 'fps_drop_percent': drop,
            'measurement': '30s normal-game completed app frames, including real fireball and final frame tail; native physics tick samples. World load is World constructor duration, excluding GameSession deserialization.'}


def reload(directory, saved_directory):
    rows = events(directory)
    loaded = one(rows, 'movable_loaded')
    saved = one(events(saved_directory), 'movable_session_save')
    checks = [row for row in rows if row['kind'] == 'movable_session_reload']
    require(len(checks) == 2 and checks[1]['t'] - checks[0]['t'] >= 1000, 'fresh save reload needs a complete one-second continuation')
    require(all(row['world'] == saved['world'] and row['tables'] > 0 and row['bodies'] > 0 for row in checks), 'save reload lost world or map furniture')
    source, actual = saved_directory/saved['archive'], directory/'run/save_slot_1.sav'
    require(source.read_bytes() == actual.read_bytes(), 'reload did not load the exact scattered-scene GameSession archive')
    require(loaded['enabled'] is True, 'reload must construct native movable bodies')
    return {'pass': True, 'world': saved['world'], 'world_load_ms': loaded['world_load_ms'],
            'continued_ms': checks[1]['t']-checks[0]['t'], 'archive_bytes': source.stat().st_size,
            'note': 'Original map vobs may return to initial transforms; moved-vob persistence is outside this step.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('functional', 'performance', 'reload'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--saved', type=Path, help='functional output directory for reload exact archive comparison')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'reload':
            require(args.saved is not None, '--saved is required for reload')
            result = reload(args.directory, args.saved)
        else:
            result = {'functional': functional, 'performance': performance}[args.mode](args.directory)
    except (ValueError, KeyError, TypeError, OSError) as error:
        result = {'pass': False, 'error': str(error)}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
