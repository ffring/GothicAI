#!/usr/bin/env python3
"""Read actual targeted wind/vortex casts and adjacent NPC controls."""
import argparse
import json
import math
from pathlib import Path
from spell_physics_compare import cost


def magnitude(v):
    return math.sqrt(sum(x*x for x in v))


def validate(run):
    data = [json.loads(line) for line in (run/'events.jsonl').read_text().splitlines()]
    assert 'done' in (run/'bench.log').read_text().splitlines(), 'unfinished scenario'
    errors = [e for e in data if e['kind'] == 'ragdoll_error']
    assert not errors, errors
    result = {}
    for name, spell_id, target in (('wind_target',26,'human'),('wind_troll',26,'troll'),('whirl_target',71,'human')):
        segment = [e for e in data if e['seg'] == name]
        frames = [e for e in segment if e['kind'] == 'spell_physics_frame']
        assert frames and len([e for e in segment if e['kind'] == 'rec_stop']) == 1, (name,'incomplete capture')
        assert all(b['t_game_ms'] > a['t_game_ms'] for a,b in zip(frames,frames[1:])), (name,'clock did not advance')
        assert all(not (a['living_parts'] and a['corpse_parts']) for e in frames for a in e['actors']), (name,'duplicate actor bodies')
        casts = [e for e in segment if e['kind'] == 'spell_physics_cast']
        assert len(casts) == 1 and casts[0]['id'] == spell_id and casts[0]['begin_result'] in (2,3), (name,'real cast rejected')
        rune = next(e for e in data if e['kind'] == 'spell_physics_rune' and e['id'] == spell_id)
        assert casts[0]['active_item'] == rune['class_id'] and rune['projectile'] == (spell_id==26)
        assert all(e['actors'][0]['role'] == target for e in frames), (name,'wrong actual target')
        roles = [a['role'] for a in frames[0]['actors']]
        actors = {role:[next(a for a in e['actors'] if a['role'] == role) for e in frames] for role in roles}
        travel = {role:max(math.dist(rows[0]['position_cm'],a['position_cm']) for a in rows) for role,rows in actors.items()}
        speed = {role:max(magnitude(a['spell_velocity_cm_s']) for a in rows) for role,rows in actors.items()}
        notes = [e['text'] for e in segment if e['kind'] == 'note']
        assert travel['companion'] <= .1 and speed['companion'] <= .1, (name,'adjacent companion moved',travel['companion'],speed['companion'])
        assert not any(e.startswith('spell physics ') and 'BDT_1013_BANDIT_L' in e for e in notes), (name,'companion received impulse')
        human, companion = actors['human'][0]['position_cm'],actors['companion'][0]['position_cm']
        assert math.dist(human,companion) < 350 and abs(human[1]-companion[1]) < 30, (name,'companion outside original area/level')
        if spell_id == 26:
            impacts = [e for e in notes if e.startswith('spell physics impact id 26 ')]
            assert len(impacts) == 1, (name,'missing actual projectile impact')
            symbol = 'TROLL' if target == 'troll' else 'BAU_904_BAUER'
            assert any(e.startswith('damage '+symbol+':') and ', spell, by PC_HERO' in e for e in notes), (name,'projectile hit another actor')
            assert all(travel[r] <= .1 and speed[r] <= .1 for r in roles if r != target), (name,'non-target NPC moved',travel,speed)
            if target == 'human':
                assert abs(actors[target][0]['mass_kg']-75) < .1 and speed[target] > 100 and travel[target] > 10
                assert any(e.startswith('spell physics wind BAU_904_BAUER mass ') for e in notes), 'human target received no physical impulse'
            else:
                assert abs(actors[target][0]['mass_kg']-600) < .1 and speed[target] <= .1 and travel[target] <= .1, ('troll was pushed',speed[target],travel[target])
                assert not any(e.startswith('spell physics wind ') for e in notes), 'resisted troll hit pushed an NPC'
        else:
            starts = [e for e in segment if e['kind']=='note' and e['text'].startswith('spell physics field start id 71 ')]
            ends = [e for e in segment if e['kind']=='note' and e['text'].startswith('spell physics field end id 71 ')]
            assert len(starts)==len(ends)==1 and 9900 <= ends[0]['t']-starts[0]['t'] <= 10100, 'real vortex field lifetime missing'
            assert frames[-1]['fields'] == 0, 'vortex field did not expire'
            up = max(a['spell_velocity_cm_s'][1] for a in actors[target])
            assert up > 50 and travel[target] > 10, ('vortex did not lift its target',up,travel[target])
            items = [item for e in frames if e['fields'] for item in e['items']]
            assert items and max(magnitude(p['velocity_cm_s']) for p in items) > 50, 'vortex did not move actual props'
            center = [float(x) for x in starts[0]['text'].split(' at ')[1].split(' duration ')[0].split()]
            item_up = max(p['velocity_cm_s'][1] for p in items)
            tangent = max(abs((p['position_cm'][0]-center[0])*p['velocity_cm_s'][2]-(p['position_cm'][2]-center[2])*p['velocity_cm_s'][0]) for p in items)
            assert item_up > 50 and tangent > 100, ('props lack upward/tangent motion',item_up,tangent)
            result[name] = {'field_ms':ends[0]['t']-starts[0]['t'],'target_up_cm_s':up,'item_up_cm_s':item_up,'item_tangent_cm2_s':tangent}
        result.setdefault(name,{}).update({'samples':len(frames),'target':target,'travel_cm':travel,'peak_cm_s':speed,'physics_step':cost(frames)})
    return {'pass':True,'cases':result}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try: result=validate(args.directory)
    except (AssertionError,KeyError,ValueError,OSError,StopIteration) as error: result={'pass':False,'error':str(error)}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    return 0 if result['pass'] else 1


if __name__=='__main__':
    raise SystemExit(main())
