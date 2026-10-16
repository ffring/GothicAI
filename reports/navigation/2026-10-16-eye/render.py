#!/usr/bin/env python3
"""Collect runner evidence and draw ray calibration (stdlib + matplotlib for PNG)."""
import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def collect(source, output):
    output.mkdir(parents=True, exist_ok=True)
    spans, events, snapshots = [], [], []
    for directory in sorted(source.iterdir()):
        if not directory.is_dir() or not (directory / 'eye-spans.jsonl').is_file():
            continue
        spans.extend(rows(directory / 'eye-spans.jsonl'))
        events.extend(rows(directory / 'eye-events.jsonl'))
        # One open calibration view per scene, plus the two wall orientations.
        for item in rows(directory / 'eye-snapshots.jsonl'):
            if item['tag'].endswith('-10') or item['tag'].startswith('wall-'):
                snapshots.append(item)
        summary = json.loads((directory / 'runner-summary.json').read_text())
        assert summary['mode'] == 'lockstep'
        assert not summary['window_created'] and not summary['graphics_device_created']
        assert not summary['audio_device_created']
        events.append({'kind': 'runner_status', 'run': directory.name, **summary})
    if not spans:
        raise SystemExit('No runner spans found')
    fence = source / 'lobart-fence' / 'eye-snapshots.jsonl'
    assert fence.is_file(), 'Missing calibration outside the pasture fence'
    snapshots = [item for item in snapshots if item['scene'] != 'lobart'] + rows(fence)
    enabled = [span for span in spans if span['enabled']]
    assert len(enabled) == 54, f'Expected 54 measured configurations, got {len(enabled)}'
    assert len([span for span in spans if not span['enabled']]) == 6
    for span in enabled:
        assert span['living_rigs'] == span['actors'], span['tag']
        assert 9.8 <= span['achieved_hz'] <= 10.2, span['tag']
        ratio = span['full_updates']['samples'] / span['all_updates']['samples']
        assert .18 <= ratio <= .22, (span['tag'], ratio)
        assert span['near_only_updates']['far_casts'] == 0, span['tag']
    wall = [event for event in events if event['kind'] == 'eye_wall']
    assert len(wall) == 1 and wall[0]['pass']
    assert wall[0]['back_wall_hits'] == 0 and wall[0]['front_wall_hits'] > 0
    assert 90 <= wall[0]['wall_distance_cm'] <= 110
    assert 90 <= wall[0]['front_wall_distance_cm'] <= 110
    for snapshot in snapshots:
        assert snapshot['animated_head'], snapshot['tag']
        assert len(snapshot['rays']) == snapshot['ray_count']
        for ray in snapshot['rays']:
            assert math.isfinite(ray['distance_cm']) and 0 <= ray['distance_cm'] <= ray['max_range_cm'] + 2
            assert -1 <= ray['normal_y'] <= 1 and ray['complete']
    for name, items in [('spans', spans), ('events', events), ('snapshots', snapshots)]:
        (output / f'{name}.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n' for row in items))
    table = []
    for span in spans:
        if not span['enabled']:
            continue
        baseline = next(s for s in spans if s['scene'] == span['scene'] and s['actors'] == span['actors'] and not s['enabled'])
        duration = span['simulated_ms'] / 1000
        cost = span['total_eye_us'] / span['eye_updates']
        # Aggregate cost uses every 10 Hz decision, including the 2 Hz continuations.
        core_pct = span['total_eye_us'] / (duration * 10000)
        slowdown = (span['wall_us'] / span['simulated_ms']) / (baseline['wall_us'] / baseline['simulated_ms'])
        record = dict(scene=span['scene'], actors=span['actors'], rays=span['ray_count'], range_m=span['center_range_cm'] / 100,
                      median_us=span['all_updates']['median_us'], p95_us=span['all_updates']['p95_us'], mean_us=cost, core_percent=core_pct,
                      slowdown=slowdown, actual_hz=span['eye_updates'] / span['actors'] / duration)
        table.append(record)
    with (output / 'cost.csv').open('w') as out:
        writer = csv.DictWriter(out, fieldnames=table[0].keys(), lineterminator='\n')
        writer.writeheader()
        writer.writerows(table)
    lines = ['| Место | NPC | Лучей | Центр, м | Медиана, мкс | p95, мкс | Среднее, мкс | % ядра | Runner × |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for item in table:
        lines.append('| {scene} | {actors} | {rays} | {range_m:.0f} | {median_us:.1f} | {p95_us:.1f} | {mean_us:.1f} | {core_percent:.2f} | {slowdown:.3f} |'.format(**item))
    (output / 'cost-table.md').write_text('\n'.join(lines) + '\n')
    return snapshots


def plot(snapshot, target):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon, Wedge
    from matplotlib.collections import LineCollection

    origin = snapshot['origin_cm']
    # Fixed world-up views. Head-local pitch/roll remain visible in the rays.
    forward = snapshot['forward']
    norm = math.hypot(forward[0], forward[2])
    forward = [forward[0] / norm, 0, forward[2] / norm]
    right = [-forward[2], 0, forward[0]]

    def local(point):
        delta = [(point[i] - origin[i]) / 100 for i in range(3)]
        return sum(a * b for a, b in zip(delta, right)), sum(a * b for a, b in zip(delta, forward)), delta[1]

    colors = {'world': '#346eb3', 'static_vob': '#c27a18', 'movable_vob': '#9744bc', 'npc': '#d44444', 'water': '#00aaba', 'none': '#b8c5cf'}
    rays = snapshot['rays']
    fig, axes = plt.subplots(1, 2, figsize=(15, 8), constrained_layout=True)
    for axis in axes:
        axis.set_facecolor('#fafcff')
        axis.grid(alpha=.18)
        axis.axhline(0, color='#c3cbd1', lw=.6)
        axis.axvline(0, color='#c3cbd1', lw=.6)
    top, side = axes
    max_m = snapshot['center_range_cm'] / 100
    edge_m = min(30, max_m)
    # Projections of nominal horizontal and vertical fields of view.
    top.add_patch(Wedge((0, 0), max_m, 30, 150, facecolor='#88bbee', edgecolor='#427cb4', alpha=.13))
    top.add_patch(Wedge((0, 0), edge_m, -10, 30, facecolor='#88bbee', edgecolor='#427cb4', alpha=.13))
    top.add_patch(Wedge((0, 0), edge_m, 150, 190, facecolor='#88bbee', edgecolor='#427cb4', alpha=.13))
    side.add_patch(Wedge((0, 0), max_m, -70, 60, facecolor='#88bbee', edgecolor='#427cb4', alpha=.13))
    hit_count = Counter()
    for kind, color in colors.items():
        chosen = [r for r in rays if r['hit_kind'] == kind]
        hits = [local(r['point_cm']) for r in chosen if r['hit']]
        hit_count[kind] = len(hits)
        for ax, indices in [(top, (0, 1)), (side, (1, 2))]:
            lines = []
            for ray in chosen:
                start, end = local(ray['origin_cm']), local(ray['point_cm'])
                lines.append([(start[indices[0]], start[indices[1]]), (end[indices[0]], end[indices[1]])])
            ax.add_collection(LineCollection(lines, colors=color, linewidths=.32, alpha=.16 if kind != 'none' else .045))
            if hits:
                ax.scatter([p[indices[0]] for p in hits], [p[indices[1]] for p in hits], c=color, s=10, alpha=.85, label=f'{kind}: {len(hits)}')
    for ax in axes:
        ax.scatter([0], [0], marker='^', color='#161f2c', s=60, zorder=10, label='head')
        ax.set_aspect('equal', adjustable='box')
    top.set(xlabel='Вправо / влево, м', ylabel='Вперёд, м', title='Вид сверху · поле зрения 200°', xlim=(-max_m * .96, max_m * .96), ylim=(-max_m * .15, max_m * 1.05))
    side.set(xlabel='Вперёд, м', ylabel='Высота относительно головы, м', title='Вид сбоку · +60° / −70°', xlim=(-max_m * .15, max_m * 1.05), ylim=(-max_m * .96, max_m * .96))
    # A near-field inset preserves the fence / foot placement scale alongside 100 m depth.
    inset = top.inset_axes([.02, .04, .42, .35])
    inset.set_facecolor('white')
    for ray in rays:
        if not ray['hit']:
            continue
        x, y, _ = local(ray['point_cm'])
        inset.scatter([x], [y], s=7, color=colors[ray['hit_kind']], alpha=.7)
    inset.scatter([0], [0], marker='^', s=30, color='#161f2c')
    inset.set(xlim=(-8, 8), ylim=(-2, 14), title='Вблизи, м')
    inset.set_aspect('equal')
    inset.grid(alpha=.2)
    detail = side.inset_axes([.02, .04, .42, .35])
    detail.set_facecolor('white')
    for ray in rays:
        if ray['hit']:
            _, x, y = local(ray['point_cm'])
            detail.scatter([x], [y], s=7, color=colors[ray['hit_kind']], alpha=.7)
    detail.scatter([0], [0], marker='^', s=30, color='#161f2c')
    detail.set(xlim=(-2, 14), ylim=(-4, 8), title='Вблизи, м')
    detail.set_aspect('equal')
    detail.grid(alpha=.2)
    if snapshot['scene'] == 'lobart':
        fence = [r for r in rays if r['hit'] and r['hit_kind'] == 'static_vob'
                 and abs(r['normal_y']) < .3 and abs(r['yaw_deg']) <= 60 and r['distance_cm'] < 1200]
        if fence:
            ray = min(fence, key=lambda r: r['distance_cm'])
            x, y, z = local(ray['point_cm'])
            for ax, point in [(inset, (x, y)), (detail, (y, z))]:
                ax.annotate(f"Забор: {ray['distance_cm'] / 100:.1f} м", xy=point, xytext=(.08, .85),
                            textcoords='axes fraction', fontsize=8,
                            arrowprops={'arrowstyle': '->', 'color': '#985b16'})
    side.legend(loc='lower right', fontsize=8)
    far = [r for r in rays if r['hit'] and r['distance_cm'] > 3000 and abs(r['normal_y']) < .65]
    if far:
        ray = max(far, key=lambda r: r['distance_cm'])
        p = local(ray['point_cm'])
        side.annotate(f"Вертикальная поверхность: {ray['distance_cm'] / 100:.1f} м", xy=(p[1], p[2]), xytext=(.3, .9), textcoords='axes fraction', arrowprops={'arrowstyle': '->', 'color': '#252f3b'}, fontsize=9)
    name = {'market': 'Рынок Хориниса', 'lobart': 'Ферма Лобарта · взгляд на пастбище'}[snapshot['scene']]
    fig.suptitle(f"{name} · {len(rays)} лучей · центр {max_m:.0f} м / края {edge_m:.0f} м\nТочки — попадания Jolt; свободные лучи показаны без точек", fontsize=13)
    fig.savefig(target, dpi=150)
    plt.close(fig)
    print(snapshot['scene'], 'hits', dict(hit_count), 'vertical >30m', len(far), 'maximum_m', max((r['distance_cm'] / 100 for r in rays if r['hit']), default=0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='matrix output root')
    parser.add_argument('output', type=Path, help='report raw directory')
    args = parser.parse_args()
    snapshots = collect(args.source, args.output)
    for snapshot in snapshots:
        if not snapshot['tag'].startswith('wall-'):
            plot(snapshot, args.output.parent / f"{snapshot['scene']}.png")


if __name__ == '__main__':
    main()
