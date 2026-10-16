#!/usr/bin/env python3
"""Validate the real candle, flame sprite and backing render-light motion."""
import argparse
import json
import math
from pathlib import Path


def validate(directory):
    rows = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()]
    setup = next(row for row in rows if row['kind'] == 'movable_candle_setup')
    end = next(row for row in rows if row['kind'] == 'movable_candle_checkpoint')
    pushes = [row for row in rows if row['kind'] == 'physics_noise_push' and row['phase'] == 'candle']
    flames = [row for row in end['attachments'] if 'FIRE_COMPLETE_A0.TGA' in row['visual']]
    lights = [row for row in end['attachments'] if row['light']]
    checks = {
        'authored_tavern_table': setup['table_zen_id'] in (21133, 21134),
        'native_impulse_couple': len(pushes) == 1 and pushes[0]['affected'] > 0 and pushes[0]['reaction_affected'] > 0,
        'table_tilt_above_45_deg': end['table_maximum_tilt_deg'] > 45,
        'candle_left_tabletop': end['candle_left_tabletop'] and end['candle_maximum_displacement_cm'] > 5,
        'real_flame_and_one_or_two_lights': len(flames) == 1 and 1 <= len(lights) <= 2,
        'every_native_step_observed': end['samples'] >= 100 and end['elapsed_ms'] >= 4000,
        'render_snapshots_complete': not end['missing_render_snapshot'],
        'attachments_within_1_cm': all(math.isfinite(row['max_local_offset_error_cm']) and
                                       row['max_local_offset_error_cm'] <= 1 for row in end['attachments']),
        'fire_and_actual_lights_moved': all(row['max_displacement_cm'] > 5 for row in end['attachments']),
        'tip_frame_saved': end['tip_frame_captured'] and (directory / 'movable-candle-tip.png').is_file(),
    }
    return {'pass': all(checks.values()), 'checks': checks,
            'table_zen_id': setup['table_zen_id'], 'candle_zen_id': setup['candle_zen_id'],
            'clearance_cm': setup['clearance_cm'], 'table_mass_kg': setup['table_mass_kg'],
            'candle_mass_kg': setup['candle_mass_kg'],
            'table_maximum_tilt_deg': end['table_maximum_tilt_deg'],
            'candle_maximum_displacement_cm': end['candle_maximum_displacement_cm'],
            'max_local_offset_error_cm': end['max_local_offset_error_cm'],
            'samples': end['samples'], 'elapsed_ms': end['elapsed_ms'],
            'attachments': end['attachments'], 'frame': 'movable-candle-tip.png'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    result = validate(args.directory)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    (args.directory / 'movable-candle-result.json').write_text(encoded)
    print(encoded, end='')
    raise SystemExit(0 if result['pass'] else 1)


if __name__ == '__main__':
    main()
