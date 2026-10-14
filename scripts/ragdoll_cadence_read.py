#!/usr/bin/env python3
"""Read three matched natural-death OFF/ON runs using real app-frame timestamps."""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def read_case(path, mode):
    meta = json.loads((path / 'appcadence.csv.json').read_text())
    assert meta['mode'] == mode and meta['clock'] == 'normal'
    assert meta['complete'] and meta['overflow'] == 0
    assert meta['window_end_ns'] - meta['start_ns'] == 30_000_000_000
    assert meta['before'] == {'models': 20, 'dead': 0, 'parts': 0}
    final = meta['after']
    assert final['models'] == final['dead'] == final['down'] == 20, final
    assert final['parts'] == (480 if mode == 'on' else 0), final
    rows = list(csv.DictReader((path / 'appcadence.csv').open()))
    assert rows and len(rows) == meta['frames']
    prior = meta['start_ns']
    intervals = []
    for i, row in enumerate(rows):
        end = int(row['end_ns'])
        delta = int(row['interval_ns'])
        assert int(row['frame']) == i and end > prior and delta == end - prior
        if i < len(rows) - 1:
            assert end < meta['window_end_ns']
        intervals.append(delta / 1_000_000)
        prior = end
    assert prior == meta['final_end_ns'] and prior >= meta['window_end_ns']
    span = (prior - meta['start_ns']) / 1_000_000_000
    return {'mode': mode, 'frames': len(rows), 'elapsed_s': span,
            'mean_fps': len(rows) / span,
            'p95_ms': sorted(intervals)[math.ceil(len(intervals) * .95) - 1],
            'max_ms': max(intervals), 'after': final}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    pairs = []
    for i in range(1, 4):
        off = read_case(args.directory / f'run{i}-off', 'off')
        on = read_case(args.directory / f'run{i}-on', 'on')
        pairs.append({'pair': i, 'off': off, 'on': on,
                      'fps_drop_percent': (1 - on['mean_fps'] / off['mean_fps']) * 100})
    off_fps = statistics.median(p['off']['mean_fps'] for p in pairs)
    on_fps = statistics.median(p['on']['mean_fps'] for p in pairs)
    drop = (1 - on_fps / off_fps) * 100
    p95 = statistics.median(p['on']['p95_ms'] for p in pairs)
    off_p95 = statistics.median(p['off']['p95_ms'] for p in pairs)
    result = {'pairs': pairs, 'median_off_fps': off_fps, 'median_on_fps': on_fps,
              'median_on_p95_ms': p95, 'median_off_p95_ms': off_p95,
              'p95_change_percent': (p95 / off_p95 - 1) * 100,
              'fps_drop_percent': drop, 'pass': drop <= 5,
              'measurement': 'CPU app-frame completion cadence, creation and final tail included'}
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
