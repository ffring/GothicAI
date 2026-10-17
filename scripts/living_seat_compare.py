#!/usr/bin/env python3
"""Check the real NPC seat trigger and its neighboring dynamic bodies."""
import argparse
import json
import math
from pathlib import Path


def check(run):
    assert 'done' in (run / 'bench.log').read_text().splitlines(), 'unfinished scenario'
    data = [json.loads(line) for line in (run / 'events.jsonl').read_text().splitlines()]
    assert not any(e['kind'] == 'ragdoll_error' for e in data), 'fixture error'
    setup = [e for e in data if e['kind'] == 'living_seat_setup']
    attach = [e for e in data if e['kind'] == 'living_seat_attach']
    assert len(setup) == len(attach) == 1 and attach[0]['attached'], 'real seat attachment missing'
    assert setup[0]['scheme'] in ('BENCH', 'CHAIR') and setup[0]['items'] == 2
    frames = [e for e in data if e['kind'] == 'living_seat_frame']
    assert len(frames) > 50 and any(e['seated'] for e in frames), 'NPC never entered real seated state'
    assert any(e['seated'] and e['mob_state'] >= 1 and ('S_' + setup[0]['scheme']) in e['animations']
               for e in frames), 'NPC never completed the ordinary seat transition'
    assert all(e['corpse_parts'] == 24 for e in frames), 'native corpse missing'
    assert all(e['living_parts'] == 0 for e in frames if e['attached']), 'seat interaction retained its contact rig'
    exit_frames = [e for e in frames if e['phase'] == 'stand' and not e['attached'] and
                   ('T_' + setup[0]['scheme'] + '_') in e['animations'] and '_2_STAND' in e['animations']]
    assert exit_frames, 'ordinary detached seat exit was not exercised'
    assert all(e['living_parts'] == 0 for e in exit_frames), 'seat exit retained its contact rig'
    restored = [e for e in frames if e['phase'] == 'stand' and not e['attached'] and e['living_parts'] == 24]
    assert restored and restored[0]['t_game_ms'] > exit_frames[-1]['t_game_ms'], 'walking contact rig did not return after seat exit'
    shifts = [e for e in frames if e['phase'] == 'shift']
    assert shifts and all(e['living_parts'] == 24 for e in shifts), 'short script reposition needs the restored contact rig'
    peak_jump = max(e['native_step_cm'] for e in frames)
    shift_jump = max(e['native_step_cm'] for e in shifts)
    assert shift_jump > 50, ('native teleport guard was never exercised', shift_jump)
    corpse_speed = max(e['corpse_speed_cm_s'] for e in frames)
    item_speed = max(e['item_speed_cm_s'] for e in frames)
    assert all(math.isfinite(v) for v in (peak_jump, corpse_speed, item_speed))
    assert corpse_speed < 50, ('corpse launched', corpse_speed)
    assert item_speed < 50, ('dropped item launched', item_speed)
    item_travel = max(math.dist(frames[0]['items_cm'][i], row['items_cm'][i])
                      for i in range(2) for row in frames)
    assert item_travel < 5, ('dropped item displaced too far', item_travel)
    return {'pass': True, 'frames': len(frames), 'scheme': setup[0]['scheme'],
            'real_attach_jump_cm': attach[0]['jump_cm'], 'max_native_step_cm': peak_jump,
            'exit_frames': len(exit_frames), 'restored_frames': len(restored),
            'max_corpse_speed_cm_s': corpse_speed, 'max_item_speed_cm_s': item_speed,
            'max_item_travel_cm': item_travel}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = check(args.directory)
    except (AssertionError, KeyError, ValueError, OSError, TypeError) as error:
        result = {'pass': False, 'error': str(error)}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
