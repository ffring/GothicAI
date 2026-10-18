#!/usr/bin/env python3
"""Read native corpse-water acceptance, cold restore and matched ON/OFF timing."""
import argparse
import csv
import json
import math
from pathlib import Path

from water_compare import events, one, percentile, require


def checkpoint(rows, label):
    return one(rows, 'water_bodies_checkpoint', label=label)


def vector(value, size, name):
    require(isinstance(value, list) and len(value) == size and
            all(isinstance(x, (int, float)) and math.isfinite(x) for x in value),
            f'{name}: expected {size} finite native values')
    return value


def catalog(snapshot):
    actors, parts, water = {}, {}, {}
    for actor in snapshot['actors']:
        actor_id = actor['actor_id']
        require(isinstance(actor_id, int) and 0 <= actor_id < 2**32-1 and actor_id not in actors,
                f'invalid/duplicate actor identity {actor_id}')
        require(actor['dead'] and actor['symbol'] and actor['part_count'] > 0,
                f'{actor_id}: fixture must contain an actual dead NPC with native parts')
        require(actor['part_count'] == len(actor['parts']), f'{actor_id}: native corpse part count differs')
        actors[actor_id] = actor
        for part in actor['parts']:
            key = (actor_id, part['bone'])
            require(isinstance(key[1], int) and key[1] >= 0 and key not in parts,
                    f'invalid/duplicate native actor/bone {key}')
            vector(part['transform'], 16, f'{key} transform')
            vector(part['center_of_mass_meters'], 3, f'{key} native COM')
            vector(part['rotationxyzw'], 4, f'{key} native rotation')
            vector(part['linear_velocity_meters'], 3, f'{key} native velocity')
            parts[key] = part
    require(actors, 'fixture has no actual corpse actors')
    for body in snapshot['bodies']:
        key = (body['actor_id'], body['bone'])
        require(key in parts and key not in water and body['category'] == 7,
                f'water part {key} is missing, duplicated or outside the corpse collision layer')
        vector(body['transform'], 16, f'{key} water transform')
        vector(body['position_cm'], 3, f'{key} water position')
        vector(body['velocity_cm_s'], 3, f'{key} water velocity')
        require(math.isfinite(body['mass_kg']) and body['mass_kg'] > 0 and
                math.isfinite(body['submerged_fraction']) and 0 <= body['submerged_fraction'] <= 1.001 and
                math.isfinite(body['max_displacement_cm']) and body['max_displacement_cm'] >= 0,
                f'{key}: invalid native mass, immersion or displacement')
        water[key] = body
    require(water.keys() == parts.keys(), 'water diagnostics do not cover every corpse part exactly once')
    return actors, parts, water


def asleep(actors, parts, water):
    require(all(actor['sleeping'] for actor in actors.values()) and
            all(part['sleeping'] for part in parts.values()) and
            all(body['sleeping'] for body in water.values()),
            'one or more native corpse parts are still awake')


def immersion(snapshot, floating):
    actors, parts, water = catalog(snapshot)
    asleep(actors, parts, water)
    result = []
    for actor_id, actor in actors.items():
        bodies = [body for key, body in water.items() if key[0] == actor_id]
        torso = [body for body in bodies if any(name in body['bone_name'].upper()
                                               for name in ('PELVIS', 'SPINE'))]
        require(torso, f'{actor_id}: no native torso capsules were measured')
        fraction = sum(body['submerged_fraction'] * body['mass_kg'] for body in torso) / sum(
            body['mass_kg'] for body in torso)
        reported = actor['torso_submerged_fraction']
        require(reported is not None and math.isfinite(reported) and abs(reported-fraction) <= .0001,
                f'{actor_id}: actor torso immersion differs from its native part measurements')
        visible = any(body['water_y_cm'] is not None and math.isfinite(body['world_top_cm']) and
                      body['world_top_cm'] > body['water_y_cm'] + .1 for body in bodies)
        floats = floating and actor['role'] != 'skeleton'
        if floats:
            require(.78 <= fraction <= 1 and visible,
                    f'{actor["role"]}: torso fraction={fraction:.6f}, visible={visible}; corpse did not float mostly submerged')
        else:
            require(all(body['submerged_fraction'] >= .99 for body in bodies),
                    f'{actor["role"]}: corpse did not sink fully under water')
        for body in bodies:
            name = body['bone_name'].upper()
            target = (1.5 if actor['role'] == 'skeleton' else .9 if any(
                word in name for word in ('PELVIS', 'SPINE')) else .95 if any(
                word in name for word in ('HEAD', 'NECK', 'JAW', 'KIEFER')) else 1)
            require(math.isfinite(body['target_fraction']) and abs(body['target_fraction']-target) <= .00001,
                    f'{actor_id}/{body["bone"]}: unexpected anatomy displacement target')
        sleep_ms = actor['first_sleep_ms']
        require(isinstance(sleep_ms, int) and 0 <= sleep_ms <= snapshot['elapsed_ms'],
                f'{actor_id}: missing first native sleep time')
        result.append({'actor_id': actor_id, 'role': actor['role'], 'symbol': actor['symbol'],
                       'parts': actor['part_count'], 'behavior': 'floats' if floats else 'sinks',
                       'torso_submerged_fraction': fraction, 'visible': visible,
                       'first_sleep_ms': sleep_ms})
    return result


def displacement(before, after):
    old_actors, old_parts, _ = catalog(before)
    new_actors, new_parts, new_water = catalog(after)
    require(old_actors.keys() == new_actors.keys() and old_parts.keys() == new_parts.keys(),
            'continuation changed the actor/bone identity set')
    require(all((actor['symbol'], actor['role'], actor['part_count']) ==
                (new_actors[key]['symbol'], new_actors[key]['role'], new_actors[key]['part_count'])
                for key, actor in old_actors.items()), 'continuation changed corpse species, role or part count')
    maximum, native_maximum, rotation_maximum = 0, 0, 0
    for key, part in old_parts.items():
        other = new_parts[key]
        distance = math.dist(part['transform'][12:15], other['transform'][12:15])
        native = math.dist(part['center_of_mass_meters'], other['center_of_mass_meters']) * 100
        maximum, native_maximum = max(maximum, distance), max(native_maximum, native)
        rotation_maximum = max(rotation_maximum, max(abs(a-b) for a, b in zip(
            part['rotationxyzw'], other['rotationxyzw'])))
        require(distance <= 1 and native <= 1,
                f'{key}: native part displaced {distance:.6f} cm at bone / {native:.6f} cm at COM')
        require(part['sleeping'] == other['sleeping'] == new_water[key]['sleeping'],
                f'{key}: sleep changed during restore/continuation')
    require(all(actor['sleeping'] == new_actors[key]['sleeping'] for key, actor in old_actors.items()),
            'whole corpse sleep changed during restore/continuation')
    return {'actors': len(new_actors), 'parts': len(new_parts), 'maximum_bone_displacement_cm': maximum,
            'maximum_native_com_displacement_cm': native_maximum,
            'maximum_quaternion_component_change': rotation_maximum}


def stable(rows, beginning='motion-reference', ending='stable'):
    first, last = checkpoint(rows, beginning), checkpoint(rows, ending)
    elapsed = last['elapsed_ms'] - first['elapsed_ms']
    require(30_000 <= elapsed <= 30_100, f'stability observation was {elapsed} ms, expected 30 seconds')
    result = displacement(first, last)
    _, _, water = catalog(last)
    maximum = max(body['max_displacement_cm'] for body in water.values())
    require(maximum <= 1, f'part moved {maximum:.6f} cm within the 30 second observation')
    result.update(observation_ms=elapsed, maximum_observed_displacement_cm=maximum)
    return result


def deep(directory):
    rows = events(directory)
    setup = one(rows, 'water_bodies_setup', case='deep')
    expected = {actor['actor_id'] for actor in setup['actors']}
    settled = checkpoint(rows, 'settled')
    actors, _, _ = catalog(settled)
    require(settled['buoyancy_enabled'] and len(actors) == len(expected) == 3 and
            actors.keys() == expected and {actor['role'] for actor in actors.values()} == {'human', 'wolf', 'skeleton'},
            'deep fixture needs the original human, wolf and skeleton with buoyancy enabled')
    return {'pass': True, 'actors': immersion(settled, True), 'stable': stable(rows)}


def shallow(directory):
    rows = events(directory)
    setup = one(rows, 'water_bodies_setup', case='shallow')
    settled = checkpoint(rows, 'settled')
    actors, parts, water = catalog(settled)
    require(settled['buoyancy_enabled'] and len(actors) == 1 and
            actors.keys() == {actor['actor_id'] for actor in setup['actors']} and
            next(iter(actors.values()))['role'] == 'human', 'shallow fixture needs its original human corpse')
    asleep(actors, parts, water)
    require(any(0 < body['submerged_fraction'] < .99 for body in water.values()),
            'shallow corpse has no genuinely partly immersed native part')
    return {'pass': True, 'actors': len(actors), 'parts': len(parts), 'stable': stable(rows)}


def reload(saved_directory, loaded_directory):
    saved_rows, loaded_rows = events(saved_directory), events(loaded_directory)
    saved, restored = checkpoint(saved_rows, 'saved'), checkpoint(loaded_rows, 'restored')
    continued = checkpoint(loaded_rows, 'continued')
    require(saved['buoyancy_enabled'] and restored['buoyancy_enabled'] and continued['buoyancy_enabled'],
            'cold floating restore requires buoyancy enabled throughout')
    actors, parts, water = catalog(saved)
    asleep(actors, parts, water)
    require(len(actors) == 3 and {actor['role'] for actor in actors.values()} == {'human', 'wolf', 'skeleton'},
            'reload source must contain the three accepted deep-water corpses')
    archive = one(saved_rows, 'movable_session_save')
    source = saved_directory / archive['archive']
    require(source.stat().st_size == archive['bytes'] and archive['bytes'] > 1000 and
            source.read_bytes() == (loaded_directory / 'run/save_slot_1.sav').read_bytes(),
            'fresh process did not load the exact complete corpse-water save archive')
    return {'pass': True, 'archive_bytes': archive['bytes'], 'initial': displacement(saved, restored),
            'after_30_seconds': displacement(saved, continued),
            'stable': stable(loaded_rows, 'restored', 'continued')}


def measured(directory, enabled):
    rows = events(directory)
    setup = one(rows, 'water_bodies_setup', case='perf')
    settled, measured = checkpoint(rows, 'settled'), checkpoint(rows, 'measured')
    actors, parts, water = catalog(settled)
    final_actors, final_parts, _ = catalog(measured)
    require(settled['buoyancy_enabled'] is enabled and measured['buoyancy_enabled'] is enabled,
            'performance buoyancy setting differs from its requested mode')
    require(len(actors) == len(setup['actors']) == len(final_actors) == 10 and
            actors.keys() == {actor['actor_id'] for actor in setup['actors']} == final_actors.keys() and
            parts.keys() == final_parts.keys() and all(actor['role'].startswith('human_') for actor in actors.values()),
            'performance requires the same ten actual human corpses and every native part throughout the window')
    immersion(settled, enabled)
    meta = json.loads((directory / 'appcadence.csv.json').read_text())
    require(meta['clock'] == 'normal' and meta['mode'] == ('on' if enabled else 'off') and
            meta['complete'] and meta['overflow'] == 0 and
            meta['window_end_ns'] - meta['start_ns'] == 30_000_000_000,
            'performance requires a complete 30 second normal-game cadence window')
    with (directory / 'appcadence.csv').open() as source:
        frames = list(csv.DictReader(source))
    require(frames and len(frames) == meta['frames'], 'performance app-frame sample count differs')
    previous, frame_ms = meta['start_ns'], []
    for index, frame in enumerate(frames):
        end, interval = int(frame['end_ns']), int(frame['interval_ns'])
        require(int(frame['frame']) == index and end > previous and interval == end-previous and
                (index == len(frames)-1 or end < meta['window_end_ns']), 'invalid app-frame timestamps')
        frame_ms.append(interval / 1e6)
        previous = end
    require(previous == meta['final_end_ns'] and previous >= meta['window_end_ns'], 'final app-frame tail is missing')
    with (directory / 'movable-physics.csv').open() as source:
        native = list(csv.DictReader(source))
    require(native, 'native physics samples are missing')
    previous = -1
    for index, sample in enumerate(native):
        tick = int(sample['t_game_ms'])
        require(int(sample['sample']) == index and tick > previous and int(sample['dt_ms']) > 0 and
                int(sample['native_bodies']) >= len(parts), 'invalid native physics sample/part coverage')
        previous = tick
    signature = sorted((actor['role'], actor['symbol'], actor['part_count']) for actor in actors.values())
    result = {'actors': len(actors), 'native_parts': len(parts), 'physics_samples': len(native), 'frames': len(frames),
              'fps': len(frames) / ((meta['final_end_ns'] - meta['start_ns']) / 1e9),
              'frame_p95_ms': percentile(frame_ms),
              'physics_p95_ms': percentile([float(sample['step_ms']) for sample in native]),
              'sleeping_parts': sum(body['sleeping'] for body in water.values())}
    return result, signature


def performance(on_directory, off_directory):
    on, on_signature = measured(on_directory, True)
    off, off_signature = measured(off_directory, False)
    require(on_signature == off_signature, 'ON/OFF timing fixtures have different actors or native part counts')
    result = {'pass': on['physics_p95_ms'] <= 2, 'on': on, 'off': off,
              'fps_change_percent': (on['fps'] / off['fps'] - 1) * 100,
              'physics_p95_delta_ms': on['physics_p95_ms'] - off['physics_p95_ms']}
    if not result['pass']:
        result['error'] = f'physics p95 {on["physics_p95_ms"]:.6f} ms exceeds the 2 ms budget'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    for mode in ('deep', 'shallow'):
        commands.add_parser(mode).add_argument('directory', type=Path)
    restore = commands.add_parser('reload')
    restore.add_argument('saved_directory', type=Path)
    restore.add_argument('loaded_directory', type=Path)
    perf = commands.add_parser('perf')
    perf.add_argument('on_directory', type=Path)
    perf.add_argument('off_directory', type=Path)
    args = parser.parse_args()
    try:
        if args.mode == 'reload':
            result = reload(args.saved_directory, args.loaded_directory)
        elif args.mode == 'perf':
            result = performance(args.on_directory, args.off_directory)
        else:
            result = {'deep': deep, 'shallow': shallow}[args.mode](args.directory)
    except (ValueError, KeyError, TypeError, OSError, IndexError, ArithmeticError) as error:
        result = {'pass': False, 'error': str(error)}
    print(json.dumps({'mode': args.mode, **result}, ensure_ascii=False, allow_nan=False))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
