#!/usr/bin/env python3
"""Validate native melee, the farm breach, and fresh-process debris archives."""
import argparse
import json
import math
from pathlib import Path


def require(ok, message):
    if not ok:
        raise ValueError(message)


def events(directory):
    require('done' in (directory / 'bench.log').read_text().splitlines(), f'{directory}: incomplete scenario')
    rows = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
    errors = [row for row in rows if row['kind'].endswith('_error')]
    require(not errors, f'{directory}: native fixture error: {errors[:1]}')
    return rows


def one(rows, kind, **fields):
    found = [row for row in rows if row['kind'] == kind and all(row.get(k) == v for k, v in fields.items())]
    require(len(found) == 1, f'expected one {kind} {fields}, found {len(found)}')
    return found[0]


def snapshot(rows, label):
    return one(rows, 'destruction2_snapshot', label=label)


def keyed(parts):
    result = {(part['zen_id'], part['piece']): part for part in parts}
    require(len(result) == len(parts), 'duplicate native map debris identity')
    require(all(key[0] != 2**32 - 1 for key in result), 'fixture-only debris cannot prove GameSession persistence')
    return result


def matrix_error(before, after):
    require(len(before) == len(after) == 16, 'missing full native transform')
    require(all(math.isfinite(v) for v in before + after), 'nonfinite native transform')
    # Tempest matrices are column-major. Test all corners of a two-metre cube;
    # this detects rotation changes as well as movement of the piece's origin.
    def point(matrix, xyz):
        return [sum(matrix[col * 4 + row] * xyz[col] for col in range(3)) + matrix[12 + row]
                for row in range(3)]
    return max(math.dist(point(before, xyz), point(after, xyz))
               for xyz in ((0, 0, 0), *[(x, y, z) for x in (-100, 100)
                                       for y in (-100, 100) for z in (-100, 100)]))


def same_state(before, after, *, disabled=False):
    require(before['world'] == after['world'], 'archive world changed')
    old_bodies = {body['zen_id']: body for body in before['bodies']}
    new_bodies = {body['zen_id']: body for body in after['bodies']}
    require(old_bodies.keys() == new_bodies.keys(), 'map-vob identity changed after a full GameSession load')
    if disabled:
        require(not after['enabled'] and not after['fragments'], 'destruction=0 loaded debris')
        require(all(not body['broken'] for body in after['bodies']), 'destruction=0 retained a broken shell')
        return {'bodies': len(new_bodies), 'fragments': 0}
    require(before['enabled'] and after['enabled'], 'destruction was disabled during archive comparison')
    broken = [body['zen_id'] for body in before['bodies'] if body['broken']]
    require(broken, 'save has no broken stable map vob')
    for zen_id, body in old_bodies.items():
        require(body['broken'] == new_bodies[zen_id]['broken'], f'{zen_id}: intact shell returned after load')
        if body['broken'] or body['health'] < body['max_health']:
            require(abs(body['health'] - new_bodies[zen_id]['health']) <= .001, f'{zen_id}: health was not restored')
    old, new = keyed(before['fragments']), keyed(after['fragments'])
    require(old.keys() == new.keys(), 'live piece indices changed, or a removed piece returned after load')
    error = 0.0
    for identity, part in old.items():
        restored = new[identity]
        require(part['permanent'] == restored['permanent'], f'{identity}: permanence changed after load')
        require(part['sleeping'] and restored['sleeping'], f'{identity}: archived debris did not remain asleep')
        require(part['asleep_since_min'] == restored['asleep_since_min'], f'{identity}: cleanup age reset after load')
        error = max(error, matrix_error(part['transform'], restored['transform']))
    require(error <= 1, f'piece transform error {error:.6f} cm exceeds 1 cm')
    return {'broken_zen_ids': broken, 'fragments': len(new), 'max_control_point_error_cm': error}


def melee(directory):
    rows = events(directory)
    result = []
    for material in ('crate', 'barrel', 'fence'):
        for weapon in ('fist', '1h', '2h'):
            case = f'{material}-{weapon}'
            setup = one(rows, 'destruction2_melee_setup', case=case)
            final = one(rows, 'destruction2_melee_complete', case=case)
            require(setup['class'] == material and setup['weapon'] == weapon and setup['actor'] == 'hero',
                    f'{case}: wrong native setup')
            require(setup['strength'] == 100, f'{case}: strength was not controlled')
            hit_events = [row for row in rows if row['kind'] == 'destruction2_melee_hit'
                          and row['case'] == case and row['affected']]
            require(len(hit_events) == final['hits'] > 0, f'{case}: animation hit callbacks missing')
            require(len(setup['bodies']) == len(final['bodies']) == 1, f'{case}: native prop missing')
            body = setup['bodies'][0]
            require(body['destructible'] and not body['broken'], f'{case}: cache-backed intact prop absent')
            health = body['health']
            damages = []
            for hit in hit_events:
                current = hit['bodies'][0]['health']
                require(0 <= current < health, f'{case}: accepted hit failed to decrease health')
                damages.append(health - current)
                health = current
            require(health == 0 and final['bodies'][0]['broken'], f'{case}: native hit did not break its prop')
            one(rows, 'destruction_break', case=case)
            require(final['attempts'] >= final['hits'], f'{case}: hit count exceeds animation attempts')
            require(8 <= len(final['fragments']) <= 20, f'{case}: fractured mesh missing')
            require((directory / f'destruction2-{material}-{weapon}.mp4').is_file(), f'{case}: video missing')
            result.append({'class': material, 'weapon': weapon, 'strength': setup['strength'],
                           'weapon_damage': setup['weapon_damage'], 'hits_to_break': final['hits'],
                           'attempts': final['attempts'], 'health_damage_per_hit': damages})
    npc = one(rows, 'destruction2_melee_complete', case='crate-1h-npc')
    require(npc['hits'] > 0 and npc['bodies'][0]['broken'], 'NPC animation did not break the crate')
    return {'cases': result, 'npc_sword_hits': npc['hits']}


def farm(directory):
    rows = events(directory)
    setup = one(rows, 'destruction2_farm_setup')
    before = one(rows, 'destruction2_farm_checkpoint', phase='before')
    final = one(rows, 'destruction2_farm_checkpoint', phase='after')
    require(setup['from_map'] and setup['zen_id'] == 19355, 'not the authored Lobart fence')
    require(not setup['intact_passable'], 'intact fence does not block native walking')
    require(not before['bodies'][0]['broken'] and final['bodies'][0]['broken'], 'farm fence was not broken by sword')
    require(final['broken_passable'], 'broken fence still blocks native walking')
    start, goal, hero = setup['start_cm'], setup['goal_cm'], final['hero_cm']
    direction = [goal[axis] - start[axis] for axis in (0, 2)]
    length = math.hypot(*direction)
    crossed = sum((hero[axis] - start[axis]) * direction[i] for i, axis in enumerate((0, 2))) / length
    require(crossed >= length * .65, f'hero failed to walk through the native breach: {crossed:.2f}/{length:.2f} cm')
    routes = [one(rows, 'destruction2_farm_npc_begin', phase=phase) for phase in ('before', 'after')]
    require(routes[0]['start_waypoint'] == routes[1]['start_waypoint'] and
            routes[0]['goal_waypoint'] == routes[1]['goal_waypoint'], 'NPC before/after used different native route')
    require(setup['npc_routines'] and setup['npc_routines'] == before['npc_routines'] == final['npc_routines'],
            'existing NPC routine entries changed')
    for phase, checkpoint in (('before', before), ('after', final)):
        require(checkpoint['npc_travel_cm'] >= 200 and checkpoint['npc_best_goal_distance_cm'] <= 110,
                f'{phase}: routine NPC failed nearby native route: {checkpoint["npc_travel_cm"]} cm travel, '
                f'{checkpoint["npc_best_goal_distance_cm"]} cm best distance')
    for name in ('destruction2-farm-before.png', 'destruction2-farm-breach.png', 'destruction2-farm-sword.mp4',
                 'session-step2-scattered.sav'):
        require((directory / name).is_file(), f'farm artifact missing: {name}')
    saved = snapshot(rows, 'saved-step2-scattered')
    parts = [part for part in saved['fragments'] if part['zen_id'] == setup['zen_id']]
    require(8 <= len(parts) <= 20 and all(part['sleeping'] for part in parts), 'farm save lacks settled live debris')
    require(sum(part['permanent'] for part in parts) == 3, 'farm save did not designate three permanent pieces')
    return {'zen_id': setup['zen_id'], 'hero_progress_cm': crossed, 'pieces_before_cleanup': len(parts),
            'npc': setup['npc'], 'native_route': [routes[0]['start_waypoint'], routes[0]['goal_waypoint']],
            'npc_before_best_goal_cm': before['npc_best_goal_distance_cm'],
            'npc_after_best_goal_cm': final['npc_best_goal_distance_cm']}


def cleanup(rows, label):
    original = snapshot(rows, 'restored')
    cleaned = snapshot(rows, label)
    old, new = keyed(original['fragments']), keyed(cleaned['fragments'])
    require(old and new.keys() < old.keys(), 'native cleanup did not remove any pieces')
    permanent = {key for key, part in old.items() if part['permanent']}
    require(new.keys() == permanent, 'cleanup retained wrong piece indices')
    parents = {identity[0] for identity in old}
    require(all(sum(identity[0] == parent for identity in new) == 3 for parent in parents),
            'cleanup must retain three largest native pieces per broken object')
    require(all(matrix_error(old[key]['transform'], new[key]['transform']) <= 1 for key in new),
            'permanent debris moved during cleanup')
    return {'pieces_before': len(old), 'pieces_after': len(new), 'removed': len(old) - len(new),
            'retained': [list(key) for key in sorted(new)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--melee', type=Path)
    parser.add_argument('--farm', type=Path)
    parser.add_argument('--reload', type=Path)
    parser.add_argument('--time', type=Path)
    parser.add_argument('--distance', type=Path)
    parser.add_argument('--time-reload', type=Path)
    parser.add_argument('--distance-reload', type=Path)
    parser.add_argument('--disabled', type=Path)
    args = parser.parse_args()
    require(any(vars(args).values()), 'at least one bench directory is required')
    result = {}
    if args.melee:
        result['melee'] = melee(args.melee)
    if args.farm:
        result['farm'] = farm(args.farm)
    if args.reload or args.disabled:
        require(args.farm, '--reload/--disabled requires --farm for the saved-state reference')
        saved = snapshot(events(args.farm), 'saved-step2-scattered')
        if args.reload:
            loaded = events(args.reload)
            result['fresh_process'] = same_state(saved, snapshot(loaded, 'restored'))
            same_state(saved, snapshot(loaded, 'continued'))
        if args.disabled:
            loaded = events(args.disabled)
            result['disabled'] = same_state(saved, snapshot(loaded, 'disabled'), disabled=True)
            same_state(saved, snapshot(loaded, 'disabled-continued'), disabled=True)
    if args.time:
        rows = events(args.time)
        original, age29 = snapshot(rows, 'restored'), snapshot(rows, 'age29')
        same_state(original, age29)
        result['time_cleanup'] = cleanup(rows, 'age30')
        result['time_cleanup']['not_removed_at_age29'] = len(age29['fragments'])
    if args.distance:
        rows = events(args.distance)
        require(one(rows, 'destruction2_far')['distance_cm'] > 3000, 'hero did not leave the 30m radius')
        result['distance_cleanup'] = cleanup(rows, 'distant')
    for flag, source, label in (('time_reload', args.time, 'step2-time-cleaned'),
                                ('distance_reload', args.distance, 'step2-distance-cleaned')):
        directory = getattr(args, flag)
        if directory:
            require(source, f'--{flag.replace("_", "-")} needs its cleanup bench reference')
            saved = snapshot(events(source), f'saved-{label}')
            restored = events(directory)
            result[flag] = same_state(saved, snapshot(restored, 'cleaned-restored'))
            same_state(saved, snapshot(restored, 'cleaned-continued'))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
