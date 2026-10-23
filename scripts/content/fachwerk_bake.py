#!/usr/bin/env python3
"""The timber-framed upper storey of the lower town's houses (asset market-house) built as a Blender
model and baked into textures, on the shared piece_bake path (docs/remaster-pipeline.md, «Материал из
модели»).

    python3 scripts/content/fachwerk_bake.py layout wall|window|post|gable OUT.json [--seed N]
    python3 scripts/content/fachwerk_bake.py render OUT.json OUTDIR [--street] [--sun E]
    python3 scripts/content/fachwerk_bake.py compose OUT.json OUTDIR/passes.exr MAPS.png --plaster PNG --wood PNG
    python3 scripts/content/fachwerk_bake.py blockout OUT.json OUTDIR/passes.exr OUT.png

wall is NW_CITY_WALL_03: a plastered field with half a post on each edge, the head and sole rails and
two braces from the lower corners. window is NW_CITY_WINDOW_03: the same field with a head rail across,
two jambs and a sill rail round a closed two-leaf shutter of upright boards on strap hinges, braces
under the sill. post is NW_CITY_WALL_04: a field with one post a little left of the middle. gable is
NW_CITY_WALL_05: the top of the gable, a king post and two braces down to the tie beam.

The timber is hewn oak standing out of the plaster, with its grain along each member, a shrinkage gap
where the plaster pulled away and pegs at the joints; the plaster is a hand-smoothed lime render. Its
relief is designed for the normal map (two scales: the members and the plaster's undulation, then the
grain and trowel marks), as docs/remaster-pipeline.md asks.

Positions come from the originals measured by Claude at their own size and on a 4x view
(original_view.py; u from the left, v from the top). No original pixel goes into the model or a
generator; the surfaces are image_gen pictures sampled in compose (--plaster, --wood).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

import piece_bake as pb
import timber_bake as tb

STEP = .0025                 # the grid of heights, metres
# The texture squares on the houses, metres (market-house measurement, city-materials.csv)
PERIODS = dict(wall=(2.41, 2.15), window=(1.93, 2.14), post=(3.83, 2.64), gable=(3.03, 1.30))
PROUD = .028                 # the timber stands out of the plaster face
BRACE_DROP = .004            # a brace lies a little behind the post and rails it is housed into
# Members as (kind, (u0, v0), (u1, v1), width in metres); a post or rail on the square's edge is a
# half member, its other half on the next square.
RAIL_TOP, RAIL_BOTTOM = (0., .078), (.935, 1.)
MEMBERS = {
    'wall': [('brace', (.035, .60), (.33, .955), .14), ('brace', (.965, .62), (.67, .955), .14),
             ('post', (0., 0.), (0., 1.), .21), ('rail', (0., .039), (1., .039), .17),
             ('rail', (0., .967), (1., .967), .145)],
    'window': [('brace', (.035, .73), (.33, .955), .13), ('brace', (.965, .73), (.67, .955), .13),
               ('post', (0., 0.), (0., 1.), .21), ('rail', (0., .039), (1., .039), .17),
               ('rail', (0., .967), (1., .967), .145), ('head', (0., .15), (1., .15), .17),
               ('jamb', (.175, .19), (.175, .63), .135), ('jamb', (.805, .19), (.805, .63), .135),
               ('sill', (0., .675), (1., .675), .19)],
    'post': [('post', (.46, 0.), (.46, 1.), .22), ('rail', (0., .039), (1., .039), .2),
             ('rail', (0., .97), (1., .97), .16)],
    'gable': [('brace', (.17, .90), (.47, .12), .2), ('brace', (.83, .90), (.53, .12), .2),
              ('post', (.50, 0.), (.50, 1.), .2), ('rail', (0., .935), (1., .935), .17)],
}
SHUTTER = dict(rect=(.21, .79, .19, .63), boards=5, gap=.005, z=.006, straps=(.25, .57), strap=(.028, .004))
GAP = (.004, .010, .4)       # the plaster's shrinkage gap at the timber: width, depth, share of the edge
PEG = (.013, .003)           # oak pegs at the joints: radius, how far they stand out
PLASTER_RELIEF = ((.5, .004), (.12, .0022), (.03, .0008))   # median tilt about 5°   # undulation of the render: size, height
SPALLS = (4, (.015, .04), .002)  # places where the whitewash flaked off: count, radius, depth
WOOD_STYLE = dict(adze=tb.STROKE, grain=(.0018, (.003, .009)), pits=((.006, .05), .005, .09, .12),
                  arris=(.018, .012), cup=0.)   # median normal tilt about 8°: the frame beam timber-7 is 6.8°, the hut boards 9°
KIND_IDS = dict(plaster=0, post=1, rail=1, head=1, sill=1, jamb=1, brace=2, shutter=3, iron=4, peg=5)


def _grid(period):
    px, py = period
    n, m = round(px / STEP), round(py / STEP)
    gx, gy = np.meshgrid(np.arange(n + 1) * STEP, np.arange(m + 1) * STEP)
    return gx, gy


def _wrap(field):
    field[-1, :] = field[0, :]
    field[:, -1] = field[:, 0]
    return field


def _local(gx, gy, period, member):
    """Distance along and across a member (metres) for every grid point, the square wrapping in u."""
    px, py = period
    _, (u0, v0), (u1, v1), _ = member
    x0, y0, x1, y1 = u0 * px, (1 - v0) * py, u1 * px, (1 - v1) * py
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    tx, ty = dx / length, dy / length
    rx = (gx - x0 + px / 2) % px - px / 2 if dx == 0 else gx - x0   # an upright member wraps across u
    ry = gy - y0
    return rx * tx + ry * ty, -rx * ty + ry * tx, length


def fachwerk(seed: int, variant: str):
    """Layout of one of the upper storey's textures: (spec, points, grids, labels, members)."""
    rng = np.random.default_rng([seed, 31, list(PERIODS).index(variant)])
    period = PERIODS[variant]
    px, py = period
    gx, gy = _grid(period)
    # the plaster: a hand-smoothed render, undulating at two scales and grained by the trowel
    height = sum(h * pb.noise_metres(rng, gx.shape, period, size) for size, h in PLASTER_RELIEF)
    labels = np.zeros(gx.shape, int)                 # 0: plaster, 1: flaked whitewash, then the parts
    kinds = ['plaster', 'spall']
    for _ in range(SPALLS[0]):                       # flaked whitewash: shallow dips
        cx, cy, r = rng.uniform(0, px), rng.uniform(0, py), rng.uniform(*SPALLS[1])
        d = np.hypot((gx - cx + px / 2) % px - px / 2, (gy - cy + py / 2) % py - py / 2) / r
        d *= 1 + .25 * pb.noise_metres(rng, gx.shape, period, .02)
        dip = np.clip(1.5 - d * 1.5, 0, 1)
        height -= SPALLS[2] * dip
        labels[dip > .5] = 1
    members = MEMBERS[variant]
    timber = np.zeros(gx.shape, bool)
    for index, member in enumerate(members):
        kind, _, _, width = member
        along, across, length = _local(gx, gy, period, member)
        ends = .0 if kind == 'brace' else width       # posts and rails run on past the square's edge
        inside = (np.abs(across) < width / 2) & (along > -ends) & (along < length + ends)
        if kind == 'brace':                           # housed into the post and rail: cut to them
            inside &= ~timber
        w = width + .02 * rng.normal()
        face = tb._hewn(rng, np.clip(across + w / 2, 0, w), along % max(length, .01), w, max(length, .01),
                        (STEP, STEP), **WOOD_STYLE)
        z = PROUD + rng.normal(0, .003) + face + .006 * np.sin(math.pi * np.clip(along / max(length, .01), 0, 1)) * \
            rng.uniform(-1, 1) - (BRACE_DROP if kind == 'brace' else 0.)
        take = inside & (z > height) if kind != 'brace' else inside
        height[take] = z[take]
        labels[take] = len(kinds)
        kinds.append(kind)
        timber |= take
    # the shrinkage gap where the plaster pulled away from the timber, along part of every edge
    from scipy.ndimage import distance_transform_edt
    dist = distance_transform_edt(~timber) * STEP
    along_edge = pb.noise_metres(rng, gx.shape, period, .25)
    broken = along_edge > np.quantile(along_edge, 1 - GAP[2])
    gap = (dist > 0) & (dist < GAP[0] * (1 + .25 * pb.noise_metres(rng, gx.shape, period, .2))) & broken
    height[gap] = np.minimum(height[gap], -GAP[1])
    # pegs at the brace ends
    for member in members:
        if member[0] != 'brace':
            continue
        _, (u0, v0), (u1, v1), _ = member
        for (u, v), toward in (((u0, v0), (u1, v1)), ((u1, v1), (u0, v0))):
            t = .1
            cx = (u + (toward[0] - u) * t) * px
            cy = (1 - (v + (toward[1] - v) * t)) * py
            d = np.hypot((gx - cx + px / 2) % px - px / 2, gy - cy) / PEG[0]
            peg = (d < 1) & timber
            height[peg] = np.maximum(height[peg], PROUD + PEG[1] * np.sqrt(np.clip(1 - d[peg] ** 2, 0, 1)) + .002)
            labels[peg] = len(kinds)
            kinds.append('peg')
    if variant == 'window':
        _shutter(rng, gx, gy, period, height, labels, kinds)
    for field in (height, labels):
        _wrap(field)
    pieces = [pb.grid_piece(0, (0., 0.), (STEP, STEP), [0, 0, 0], 0, [0., 0., 0.],
                            copy_offsets=[[dx, dy] for dx in (-px, 0, px) for dy in (-py, 0, py)], kind='wall')]
    spec = dict(tool='scripts/content/fachwerk_bake.py', texture=TEXTURES[variant], wall=variant, seed=seed,
                period=list(period), camera=dict(tilt=0.), sky=dict(pitch=0., ground=.08),
                ao=dict(distance=.05, crevice=.010),
                surfaces=[dict(name='wall', colour=[.30, .29, .25], metres=1., bump=0.)],
                parts=kinds, members=[[m[0], list(m[1]), list(m[2]), m[3]] for m in members], pieces=pieces)
    return spec, {}, {0: height}, labels


TEXTURES = dict(wall='NW_CITY_WALL_03.TGA', window='NW_CITY_WINDOW_03.TGA', post='NW_CITY_WALL_04.TGA',
                gable='NW_CITY_WALL_05.TGA')


def _shutter(rng, gx, gy, period, height, labels, kinds):
    """The closed two-leaf shutter of upright boards between the jambs, with strap hinges."""
    px, py = period
    u0, u1, v0, v1 = SHUTTER['rect']
    x0, x1, y0, y1 = u0 * px, u1 * px, (1 - v1) * py, (1 - v0) * py
    inside = (gx > x0) & (gx < x1) & (gy > y0) & (gy < y1)
    height[inside] = -.015                           # the dark behind the boards' gaps
    labels[inside] = len(kinds)
    kinds.append('dark')
    middle = (x0 + x1) / 2
    for leaf, (a, b) in enumerate(((x0, middle), (middle, x1))):
        width = (b - a) / SHUTTER['boards']
        for k in range(SHUTTER['boards']):
            bx0 = a + k * width + SHUTTER['gap'] / 2
            bx1 = a + (k + 1) * width - SHUTTER['gap'] / 2
            board = inside & (gx > bx0) & (gx < bx1)
            local = np.clip(gx - bx0, 0, bx1 - bx0)
            style = dict(adze=None, grain=(.0010, (.002, .007)), pits=((.005, .05), .002, .08, .12),
                         arris=(.006, .004), cup=.003)
            face = tb._hewn(rng, local, gy - y0, bx1 - bx0, y1 - y0, (STEP, STEP), **style)
            z = SHUTTER['z'] + face + rng.normal(0, .0015) + rng.normal(0, .01) * (gx - (bx0 + bx1) / 2)
            height[board] = z[board]
            labels[board] = len(kinds)
            kinds.append('shutter')
        # strap hinges from the outer edge two thirds across the leaf, nailed
        width_s, thick = SHUTTER['strap']
        outer = a if leaf == 0 else b
        for v in SHUTTER['straps']:
            sy = (1 - v) * py
            reach = (b - a) * .78
            span = (gx > min(outer, outer + (reach if leaf == 0 else -reach))) & \
                (gx < max(outer, outer + (reach if leaf == 0 else -reach)))
            taper = 1 - .4 * np.clip(np.abs(gx - outer) / reach, 0, 1)
            strap = inside & span & (np.abs(gy - sy) < width_s / 2 * taper)
            height[strap] = np.maximum(height[strap], SHUTTER['z'] + thick + .002)
            labels[strap] = len(kinds)
            kinds.append('iron')


# ---------------------------------------------------------------- weathering (compose)
# Targets: the plaster and the oak as the user accepted them on house-2 (2026-10-21; its plaster is
# cleaner than the original's, the accepted deviation), the shutter as the original's weathered boards.
PLASTER = (156, 156, 134)
OAK = (41, 34, 20)
SHUTTER_TONE = (63, 58, 47)
IRON = (35, 31, 23)
SPALL_TONE = (142, 136, 112)           # the older grey-ochre coat where the whitewash flaked
DAMP = (.28, .22)                      # soft grey-green damp running down from each rail: reach in metres, darkening
DAMP_TINT = (1., 1.02, .93)           # the damp greys and greens the whitewash a little
CLOUD = (.35, .07)                     # broad soft clouding of the whitewash: size in metres, log spread
GREEN = ((92, 100, 70), .12, .06)      # a faint green film above the sole rail: colour, reach, share
OCCLUSION = (1.6, 1.5)
ROUGH = dict(plaster=.93, wood=.88, iron=.72)


def _linear(srgb) -> np.ndarray:
    return (np.asarray(srgb, dtype=np.float64) / 255) ** 2.2


def _sample(image: np.ndarray, a, b, metres: float) -> np.ndarray:
    """An image tiled every METRES, read at the coordinates a (across) and b (along), bilinear."""
    h, w = image.shape[:2]
    fx = (a / metres % 1) * w
    fy = (b / metres % 1) * h
    x0, y0 = np.floor(fx).astype(int) % w, np.floor(fy).astype(int) % h
    x1, y1 = (x0 + 1) % w, (y0 + 1) % h
    tx, ty = (fx - np.floor(fx))[..., None], (fy - np.floor(fy))[..., None]
    return (image[y0, x0] * (1 - tx) * (1 - ty) + image[y0, x1] * tx * (1 - ty) +
            image[y1, x0] * (1 - tx) * ty + image[y1, x1] * tx * ty)


def compose(passes: dict, spec: dict, labels: np.ndarray, plaster_img: np.ndarray, wood_img: np.ndarray,
            seed: int = 0) -> dict:
    """Albedo (linear RGB) and masks of one of the upper storey's textures."""
    rng = np.random.default_rng([seed, 37])
    rows, cols = passes['piece'].shape
    period = spec['period']
    px, py = period
    x, y = passes['x'] % px, passes['y'] % py
    v = 1 - y / py
    n = labels.shape[1] - 1
    part = labels[np.rint(y / STEP).astype(int) % (labels.shape[0] - 1), np.rint(x / STEP).astype(int) % n]
    kinds = np.array(spec['parts'])[part]
    spall = (kinds == 'spall').astype(float)
    plaster = np.isin(kinds, ['plaster', 'spall'])
    shutter = kinds == 'shutter'
    iron = kinds == 'iron'
    wood = np.isin(kinds, ['post', 'rail', 'head', 'sill', 'jamb', 'brace', 'peg'])
    out = np.zeros((rows, cols, 3))
    # the plaster: its picture, softly clouded
    pl = _sample(plaster_img, x, y, .6)
    out[plaster] = pl[plaster]
    # the oak: its picture read along each member's grain; every member a little its own tone
    members = spec['members']
    member_ids = [i for i, k in enumerate(spec['parts']) if k in ('post', 'rail', 'head', 'sill', 'jamb', 'brace')]
    for index, member in enumerate(members):
        mask = part == member_ids[index]
        if not mask.any():
            continue
        along, across, _ = _local(x, y, period, (member[0], tuple(member[1]), tuple(member[2]), member[3]))
        out[mask] = _sample(wood_img, across[mask] + index * .37, along[mask], 1.) * math.exp(rng.normal(0, .12))
    pegs = kinds == 'peg'
    out[pegs] = _sample(wood_img, x[pegs] * 3, y[pegs] * 3, 1.) * .8          # end grain, darker
    if shutter.any():
        ids = np.unique(part[shutter])
        for i in ids:
            mask = part == i
            out[mask] = _sample(wood_img, x[mask] + i * .21, y[mask], 1.) * math.exp(rng.normal(0, .15))
    out[iron] = 1.
    dark = kinds == 'dark'
    lum = out @ tb.LUM
    for mask in (plaster, wood, shutter):            # each part's own light around 1
        if mask.any():
            out[mask] /= np.median(lum[mask])
    out[wood | shutter] = (out[wood | shutter] @ tb.LUM)[:, None] ** 1.3 * np.array([1., .86, .62])   # grain, olive brown

    # plaster weathering: by form only, nothing that marks one place of the square
    out = out * (1 - spall[..., None]) + _linear(SPALL_TONE) / _linear(PLASTER) * spall[..., None]
    timber_px = ~plaster & ~(kinds == 'shutter')
    reach, depth = DAMP
    # damp under a rail: how far the nearest timber straight above is
    r_idx = np.arange(rows)[:, None] * np.ones((1, cols))
    last = np.maximum.accumulate(np.where(timber_px, r_idx, -1e6), axis=0)
    rails_above = (r_idx - last) * (py / rows)
    # ragged at its lower edge: runs further down in some places than others
    ragged = reach * (1 + .45 * pb.noise_metres(rng, (rows, cols), period, .09))
    damp = np.clip(1 - rails_above / np.maximum(ragged, .02), 0, 1) ** 1.5 * plaster
    damp *= np.clip(.6 + .5 * pb.noise_metres(rng, (rows, cols), period, .06), 0, 1)
    out *= (1 - depth * damp)[..., None] * (1 + (np.array(DAMP_TINT) - 1) * damp[..., None])
    out[plaster] *= np.exp(CLOUD[1] * pb.noise_metres(rng, (rows, cols), period, CLOUD[0]))[plaster][:, None]
    green_srgb, g_reach, g_share = GREEN
    nxt = np.minimum.accumulate(np.where(timber_px, r_idx, 1e6)[::-1], axis=0)[::-1]
    rails_below = (nxt - r_idx) * (py / rows)
    film = np.clip(1 - rails_below / g_reach, 0, 1) * plaster
    film *= np.clip(pb.noise_metres(rng, (rows, cols), period, .05) + 1 - g_share * 8, 0, 1)
    tint = _linear(green_srgb) / _linear(PLASTER)
    out = out * (1 - .5 * film[..., None]) + out * tint * .5 * film[..., None]

    occlusion = np.clip(passes['ao'], 0, 1) ** OCCLUSION[0] * np.clip(passes['crevice'], 0, 1) ** OCCLUSION[1]
    out *= occlusion[..., None]
    shade = pb.overhang_shade(passes['height'], py / rows, (30., 40., 55.), .04, softness=.004)
    out *= (1 - .45 * shade)[..., None]

    out[dark] = _linear((12, 10, 8))
    for mask, srgb in ((plaster, PLASTER), (wood, OAK), (shutter, SHUTTER_TONE), (iron, IRON)):
        if mask.any():
            out[mask] *= _linear(srgb) / np.maximum(out[mask].mean(axis=0), 1e-6)

    normal = passes['normal'] / (np.linalg.norm(passes['normal'], axis=-1, keepdims=True) + 1e-9)
    rough = np.where(plaster, ROUGH['plaster'], np.where(iron, ROUGH['iron'], ROUGH['wood']))
    rough = np.where(damp > .3, rough - .04, rough)
    return dict(albedo=np.clip(out, 0, 1), roughness=rough, normal=normal)


def write_layout(path: Path, spec, points, grids, labels) -> None:
    pb.write_layout(path, spec, points, grids)
    np.savez_compressed(path.with_name(path.stem + '-labels.npz'), labels=labels.astype(np.int32))


def _image(path) -> np.ndarray:
    from PIL import Image
    return np.asarray(Image.open(path).convert('RGB'), dtype=np.float64) / 255


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('layout')
    p.add_argument('variant', choices=sorted(PERIODS))
    p.add_argument('out', type=Path)
    p.add_argument('--seed', type=int, default=0)
    p = sub.add_parser('render')
    p.add_argument('layout', type=Path)
    p.add_argument('out_dir', type=Path)
    p.add_argument('--resolution', type=int, default=1536)
    p.add_argument('--samples', type=int, default=32)
    p.add_argument('--street', action='store_true')
    p.add_argument('--sun', type=float, default=0.)
    p = sub.add_parser('compose')
    p.add_argument('layout', type=Path)
    p.add_argument('passes', type=Path)
    p.add_argument('out', type=Path)
    p.add_argument('--plaster', type=Path, required=True)
    p.add_argument('--wood', type=Path, required=True)
    p.add_argument('--seed', type=int, default=0)
    p = sub.add_parser('blockout')
    p.add_argument('layout', type=Path)
    p.add_argument('passes', type=Path)
    p.add_argument('out', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'layout':
        write_layout(args.out, *fachwerk(args.seed, args.variant))
        print(args.out)
    elif args.command == 'render':
        spec = json.loads(args.layout.read_text())
        px, py = spec['period']
        options = (dict(view='street', pitch='90', eaves='.0', stretch='1', close=f'{px * .5},{py * .55}')
                   if args.street else {})
        if args.sun:
            options['sun'] = str(args.sun)
        print(pb.render(args.layout, args.out_dir, {}, args.resolution, args.samples, options))
    elif args.command == 'compose':
        spec = json.loads(args.layout.read_text())
        passes = pb.read_passes(args.passes, spec['period'])
        labels = np.load(args.layout.with_name(args.layout.stem + '-labels.npz'))['labels']
        result = compose(passes, spec, labels, _image(args.plaster), _image(args.wood), args.seed)
        px, py = spec['period']
        size = (1024, 1024) if args.out else None
        pb.write_maps(args.out, size, result['albedo'], result['normal'], {'roughness': result['roughness']})
        print(args.out)
    elif args.command == 'blockout':
        spec = json.loads(args.layout.read_text())
        passes = pb.read_passes(args.passes, spec['period'])
        width = passes['piece'].shape[1]
        pb.write_preview(passes, args.out, (width, passes['piece'].shape[0]))
        print(args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
