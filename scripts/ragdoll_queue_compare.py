#!/usr/bin/env python3
"""Check normal midair KO, native settling and queued AI_Wait/AI_StandUp handoff."""
import argparse
import json
from pathlib import Path
from ragdoll_compare import read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'schema_version': 1, 'pass': False, 'scope': 'ordinary airborne movement, native settling, actual queued AI_Wait then AI_StandUp; controlled state-end cleanup only after that native handoff'}
    try:
        metrics, all_frames, events = read(args.run)
        if 'done' not in (args.run / 'bench.log').read_text().splitlines():
            raise ValueError('focused game scenario did not finish')
        downs = [e for e in events if e.get('kind') == 'ragdoll_down']
        queued = [e for e in events if e.get('kind') == 'ragdoll_queued_standup']
        if len(downs) != 1 or downs[0].get('in_air_before') is not True or downs[0].get('death') is not False or downs[0].get('parts') != 24:
            raise ValueError('KO was not triggered after a normal airborne tick with a complete body')
        if len(queued) != 1 or queued[0].get('delay_ms') != 1000:
            raise ValueError('normal wait/standup queue coverage differs')
        frames = [f for (segment, index), f in sorted(all_frames.items()) if segment == 'queued_midair']
        if len(frames) < 30 or not any(f['in_air'] and not f['part_count'] for f in frames):
            raise ValueError('ordinary airborne frames before the physical KO are absent')
        q = queued[0]
        physical_before_queue = [f for f in frames if f['part_count'] and f['t_game_ms'] <= q['t_game_ms']]
        if not physical_before_queue or not physical_before_queue[-1]['sleeping']:
            raise ValueError('the queue probe did not start from a truly settled native body')
        waits = [f for f in frames if f['t_game_ms'] >= q['t_game_ms'] and f['ai_wait_ms'] > 0]
        if not waits or max(f['ai_wait_ms'] for f in waits) < 966:
            raise ValueError('the real AI_Wait deadline was never observed')
        deadline = waits[0]['t_game_ms'] + waits[0]['ai_wait_ms']
        if any(f['t_game_ms'] + f['ai_wait_ms'] != deadline for f in waits):
            raise ValueError('observed native AI_Wait deadline changed')
        protected = [f for f in frames if waits[0]['t_game_ms'] <= f['t_game_ms'] <= deadline]
        if len(protected) < 29 or any(f['part_count'] != 24 or f['recovering'] for f in protected):
            raise ValueError('queued recovery bypassed the full ordinary AI_Wait interval')
        handoffs = [i for i in range(1, len(frames)) if frames[i-1]['part_count'] and not frames[i]['part_count']]
        if len(handoffs) != 1 or frames[handoffs[0]]['t_game_ms'] <= deadline:
            raise ValueError('there is no unique native handoff after the wait deadline')
        cleanup = [e for e in events if e.get('kind') == 'ragdoll_standup']
        if len(cleanup) != 1 or cleanup[0]['t_game_ms'] < frames[handoffs[0]]['t_game_ms']:
            raise ValueError('controlled state-end cleanup bypassed the queued native handoff')
        tail = frames[handoffs[0]:]
        if not any('WOUNDED' in f['animations'].upper() and 'STAND' in f['animations'].upper() for f in tail):
            raise ValueError('normal wounded-to-stand MAN transition was not selected')
        final = frames[-1]
        if final['unconscious'] or final['dead'] or final['part_count'] or final['recovering'] or final['in_air']:
            raise ValueError('queued standup did not finish in an alive grounded animation state')
        result.update(pass_=True, frames=len(frames), wait_deadline_ms=deadline,
                      observed_wait_frames=len(protected), handoff_game_ms=frames[handoffs[0]]['t_game_ms'],
                      final_hp=final['hp'])
        result['pass'] = result.pop('pass_')
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as error:
        result['error'] = str(error)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('PASS' if result['pass'] else 'FAIL')
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
