#!/usr/bin/env python3
"""City timber (asset city-timber) built as Blender models and baked into textures, on the shared
piece_bake path (docs/remaster-pipeline.md, «Материал из модели»).

    python3 scripts/content/timber_bake.py layout beam02 OUT.json [--seed N]
    python3 scripts/content/timber_bake.py render OUT.json OUTDIR [--wood PNG] [--street]
    python3 scripts/content/timber_bake.py compose OUT.json OUTDIR/passes.exr MAPS.png [--seed N]
    python3 scripts/content/timber_bake.py blockout OUT.json OUTDIR/passes.exr OUT.png

beam02 is NW_CITY_WOODBEAM_02, the dark frame beam of the harbour huts and the market houses: one face
of a hewn oak beam. Its face is a grid of heights (piece_bake.grid_piece): adze scallops along the
beam, grain, pits, worn arrises and the original's three cracks, deep enough to be black at the
bottom. An iron strap with two nails crosses it near the top.

The model is laid out in the texture's own proportions, 0.5 × 1.0 "texture metres": the game stretches
this texture along the beam about 3.4 times (intent.md), the same way it stretches the original, so
the nails stay round in the texture as they are in the original. Positions come from the original
measured by Claude on its preview (texture u from the left, v from the top); no original pixel goes
into the model or a generator.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

import piece_bake as pb

# ---------------------------------------------------------------- beam02: layout

PERIOD = (.5, 1.)            # texture metres: one face across, the texture's length along
STEP = (.001, .001)          # the face grid: across and along the beam, metres
STRETCH = 3.4                # the game stretches the texture this much along the beam (0.5 × 3.4 m face)
STROKE = ((.09, .07), ((.06, .10), (.045, .075)), (.0015, .004))   # adze strokes: spacing across, along;
                             # half width and half length ranges; depth range, metres
GRAIN = (.0005, (.004, .012))   # fine grain ridges: depth, wavelengths across the beam
PITS = ((.008, .06), .003, .06, .12)   # weathered pockets along the grain: size across and along, depth, share,
                             # and the size of the patches they keep to
ARRIS = (.02, .010)          # the worn corner of the beam at both long edges: width, drop
# The original's cracks, measured on its preview: (v, u) points from the top, a width at the widest in
# metres and a depth. The main crack runs the whole length past the texture's seam with a step near its
# lower end; the second one wanders through the upper half; the third is short, top left.
CRACKS = [
    dict(path=[(.84, .61), (.98, .59), (1.02, .59), (1.05, .53), (1.14, .53), (1.21, .50), (1.28, .49), (1.38, .46),
               (1.47, .46), (1.56, .48), (1.66, .50), (1.75, .48), (1.87, .45)], width=.036, depth=.055),
    dict(path=[(.80, .34), (.87, .33), (.94, .30), (1.0, .35), (1.07, .39), (1.12, .36), (1.21, .33), (1.30, .31),
               (1.40, .30)], width=.021, depth=.045),
    dict(path=[(.93, .12), (1.0, .17), (1.04, .20), (1.10, .20)], width=.010, depth=.025),
    dict(path=[(.99, .66), (1.04, .65), (1.09, .67)], width=.005, depth=.012),
]
WIGGLE = (.0015, .03)        # cracks wander by this much over this length, metres
STRAP = (.219, .266, .006)   # the iron strap: v of its top and bottom edge, thickness in metres
NAILS = ((.16, .81), .0075, .004)  # nails: u of each on the strap's middle line, head radius, dome height
SURFACES = [  # name, plain colour of the block-out (linear), metres per surface image, bump in metres
    dict(name='oak', colour=[.24, .21, .17], metres=1., bump=.0006),
    dict(name='iron', colour=[.08, .08, .08], metres=.5, bump=.0003),
]
OAK, IRON = 0, 1


def _noise(rng, shape, size_x: float, size_y: float, step) -> np.ndarray:
    """Smooth noise wrapping in both axes, unit spread, with features about size_x by size_y metres."""
    rows, cols = shape
    return pb.periodic_noise(rng, shape, cols * step[0] / size_x, rows * step[1] / size_y)


def _cracks(gx, gy, rng) -> tuple[np.ndarray, np.ndarray]:
    """Depth of every crack at the grid (positive down) and a 0..1 map of the crack lips."""
    from scipy.spatial import cKDTree
    px, py = PERIOD
    depth = np.zeros_like(gx)
    lips = np.zeros_like(gx)
    for crack in CRACKS:
        path = np.asarray(crack['path'])
        v = np.interp(np.linspace(0, 1, 2000), np.linspace(0, 1, len(path)), path[:, 0])
        u = np.interp(np.linspace(0, 1, 2000), np.linspace(0, 1, len(path)), path[:, 1])
        x, y = u * px, (1 - v) * py
        s = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
        length = s[-1]
        # the line wanders: a smooth random sideways offset, a few wavelengths of WIGGLE[1]
        wander = sum(rng.uniform(.4, 1) * np.sin(2 * math.pi * s / (WIGGLE[1] * k) + rng.uniform(0, 2 * math.pi))
                     for k in (.6, 1., 1.7))
        x = x + WIGGLE[0] * wander / 2
        t = s / length
        taper = np.clip(np.minimum(t, 1 - t) / .12, 0, 1) ** .7          # closes at both ends
        swell = 1 + .35 * sum(np.sin(2 * math.pi * t * k + rng.uniform(0, 2 * math.pi)) for k in (2.3, 5.1)) / 2
        half = crack['width'] / 2 * taper * swell
        floor = crack['depth'] * np.sqrt(taper)
        points = np.stack([x, y], axis=1)
        tree = cKDTree(np.concatenate([points + [0, dy] for dy in (-py, 0, py)]))
        dist, index = tree.query(np.stack([gx.ravel(), gy.ravel()], axis=1), distance_upper_bound=.03)
        near = np.isfinite(dist)
        i = index[near] % len(points)
        d = dist[near]
        h = np.maximum(half[i], 1e-6)
        inside = np.clip(1 - d / h, 0, 1)
        flat_depth = depth.ravel()
        flat_depth[near] = np.maximum(flat_depth[near], floor[i] * inside ** .45)    # steep walls, sharp at the bottom
        depth = flat_depth.reshape(gx.shape)
        flat_lips = lips.ravel()
        flat_lips[near] = np.maximum(flat_lips[near], np.clip(1 - (d - h) / .004, 0, 1) * (d >= h) * taper[i])
        lips = flat_lips.reshape(gx.shape)
    return depth, lips


def beam02(seed: int):
    """Layout of NW_CITY_WOODBEAM_02: (spec, points, grids)."""
    rng = np.random.default_rng([seed, 2])
    px, py = PERIOD
    cols, rows = round(px / STEP[0]) + 1, round(py / STEP[1])
    gx, gy = np.meshgrid(np.arange(cols) * STEP[0], np.arange(rows) * STEP[1])

    # adze: every stroke leaves a shallow dish; the face is what all strokes left (their minimum)
    (sx, sy), ((w0, w1), (l0, l1)), (d0, d1) = STROKE
    face = np.full(gx.shape, np.inf)
    for cy in np.arange(0, py, sy):
        offset = rng.uniform(0, sx)
        for cx in np.arange(-sx + offset, px + sx, sx):
            x0, y0 = cx + rng.normal(0, sx * .2), cy + rng.normal(0, sy * .25)
            half_w, half_l, deep = rng.uniform(w0, w1), rng.uniform(l0, l1), rng.uniform(d0, d1)
            dy = (gy - y0 + py / 2) % py - py / 2                     # the strokes wrap along the beam
            face = np.minimum(face, -deep + deep * (((gx - x0) / half_w) ** 2 + (dy / half_l) ** 2))
    face = np.minimum(face, 0) - face.max()

    # fine grain along the beam, a little wavy
    grain = _noise(rng, gx.shape, GRAIN[1][0], .25, STEP) * .6 + _noise(rng, gx.shape, GRAIN[1][1], .5, STEP) * .4
    face += GRAIN[0] * np.tanh(grain)

    # weathered pockets: the soft wood between the late-wood ridges washed out, long along the grain
    pits = _noise(rng, gx.shape, *PITS[0], STEP) + .5 * _noise(rng, gx.shape, PITS[0][0] * .5, PITS[0][1] * .4, STEP)
    pit = np.clip((pits - np.quantile(pits, 1 - PITS[2])) / .9, 0, 1)
    patches = np.clip(_noise(rng, gx.shape, PITS[3], PITS[3] * 1.5, STEP) * 1.5 + .3, 0, 1)
    face -= PITS[1] * pit * patches

    # worn arrises at both long edges, unevenly
    wear = 1 + .4 * _noise(rng, gx.shape, .5, .12, STEP)
    edge = np.minimum(gx, px - gx)
    face -= ARRIS[1] * wear * np.clip(1 - edge / (ARRIS[0] * wear), 0, 1) ** 2

    crack, lips = _cracks(gx, gy, rng)
    face -= crack + .0012 * lips                                       # the lips are a little eroded

    # the strap: a thin sheet over the face's smooth shape, sitting on its high points
    top, bottom = (1 - STRAP[0]) * py, (1 - STRAP[1]) * py
    r0, r1 = int(bottom / STEP[1]) - 2, int(top / STEP[1]) + 3
    base = face[r0:r1] + crack[r0:r1] + .0012 * lips[r0:r1]          # the strap bridges the cracks
    from scipy.ndimage import maximum_filter, uniform_filter
    bed = uniform_filter(maximum_filter(base, size=(1, 41)), size=(1, 21))
    ys = gy[r0:r1]
    ragged = .0006 * _noise(rng, gx.shape, .03, .5, STEP)[r0:r1]
    inside = np.minimum(ys - bottom - ragged, top + ragged - ys)
    dents = .0003 * _noise(rng, gx.shape, .015, .015, STEP)[r0:r1]
    strap = bed + STRAP[2] + dents - .002 * np.clip(1 - inside / .004, 0, 1) ** 2
    strap = np.where(inside > 0, strap, bed - .01)                     # off the strap: under the face

    pieces, points, grids = [], {}, {}
    pieces.append(pb.grid_piece(0, (0, 0), STEP, [0, 0, 0], OAK, [rng.uniform(0, 1), rng.uniform(0, 1), 0.],
                                copy_offsets=[[0, -py], [0, 0], [0, py]], kind='face'))
    grids[0] = np.concatenate([face, face[:1]])                        # the last row closes the period
    pieces.append(pb.grid_piece(1, (0, r0 * STEP[1]), STEP, [0, 0, 0], IRON, [0, 0, 0], copy_offsets=[[0, 0]],
                                kind='strap'))
    grids[1] = strap

    from shapely.geometry import Point
    middle = (top + bottom) / 2
    for k, u in enumerate(NAILS[0]):
        x = u * px
        row = int(middle / STEP[1]) - r0
        z = strap[row, int(x / STEP[0])]
        head = Point(x, middle).buffer(NAILS[1], 16)
        index = 2 + k
        pieces.append(pb.piece(index, head, [z - .003, 0, 0], [z + NAILS[2] - .001, 0, 0], IRON,
                               [rng.uniform(0, 1), rng.uniform(0, 1), 0],
                               [dict(kind='round', width=NAILS[1] * .9, drop=NAILS[2] * .8)], [[0, 0]],
                               kind='nail', closed=True))
        points[index] = pb.mesh_points(head, None, .0012, .0012, rings=(.001, .003, .006))

    spec = dict(tool='scripts/content/timber_bake.py', texture=TEXTURES['beam02'], seed=seed,
                period=list(PERIOD), camera=dict(tilt=0.), sky=dict(pitch=0., ground=.08),
                ao=dict(distance=.05, crevice=.012), surfaces=SURFACES,
                strap=dict(v=[STRAP[0], STRAP[1]]), nails=list(NAILS[0]),
                cracks=[dict(path=c['path'], width=c['width'], depth=c['depth']) for c in CRACKS], pieces=pieces)
    return spec, points, grids


# ---------------------------------------------------------------- beam02: weathering (compose)
# The original, measured on its preview: very dark olive-brown (sRGB 41/35/21, G/R .85, B/R .49), no
# green at all; blotchy damp (block spread of 16 px blocks 9.6 against a pixel spread of 15.4); darker
# above the strap than below it; 7% of the face almost black, the cracks; the strap in the wood's own
# dark olive, told apart by its edges, with light worn nail heads. Colours are linear.
LUM = np.array([.2126, .7152, .0722])
OLIVE = np.array([1., .70, .21])       # channel ratios of the original's olive brown, linear
GREY_OLIVE = np.array([1., .77, .30])  # the light streaks, weathered grey-olive (sRGB 1 : .89 : .58)
PALE_OLIVE = np.array([1., .80, .33])  # the pale weathered patches (sRGB 1 : .90 : .60); the colour
                                       # correction to the original's target adds blue, bluer read as smoke
WOOD_DESATURATE = .55                  # share of the generated oak's own colour removed
GRAIN_CONTRAST = .55                   # power on the oak's own light: its grain is stripier than the original
GREY_LIGHT = .55                       # the lighter wood is weathered grey-olive, not yellow: share of grey at the top
MOTTLE = ((.08, .03), .14, (1.5, .55))   # damp and weathered patches with sharp edges: sizes in metres,
                                       # share of each, light and dark factor
DAMP = (.12, .05, .38)                 # damp blotches: sizes in metres, strength (log spread)
ABOVE = (.62, .03)                     # the wood above the strap is darker: factor, soft edge in metres
OCCLUSION = (.6, 2.2)                  # powers of the AO and the crevice AO: the cracks go black
CRACK_SIDE = (.015, .8)                # near a crack the wood is damper: reach in metres, factor
STRAP_TONE = (.7, 2.6)                 # the strap against the wood's median; its worn edges lighter
IRON_COLOUR = np.array([1., .79, .25]) # the strap's dark olive iron (the original's strap: sRGB 33/30/15)
CRACK_BLACK = (.15, .6, .06)           # the crack's inside goes black: crevice AO from, to, darkest factor
NAIL = np.array([.16, .15, .12])       # the worn nail heads
RUST = (.18, .03, .5, np.array([.045, .026, .012]))   # streaks under the strap: reach, column size, share, colour
CONTRAST = 1.3
ROUGH = ((.86, .95), .80, .72, .6)     # dry wood range, damp wood (no sky highlight: it fades out at 0.8), the strap, the nail heads


def _kinds(spec, piece):
    return np.array([p['kind'] for p in spec['pieces']] + ['none'])[piece]


def compose(passes: dict, spec: dict, seed: int = 0) -> dict:
    """Albedo (linear RGB) and the masks it was made with, at the render size."""
    rng = np.random.default_rng([seed, 7])
    piece = passes['piece']
    rows, cols = piece.shape
    period = spec['period']
    px, py = period
    kind = _kinds(spec, piece)
    face, strap, nail = kind == 'face', kind == 'strap', kind == 'nail'

    # the generated oak's light and grain, in the original's olive brown
    colour = passes['colour']
    lum = colour @ LUM
    grey = lum[..., None]
    wood = grey + (colour - grey) * (1 - WOOD_DESATURATE)
    ratio = np.median(wood[face], axis=0)
    wood = wood / (ratio / ratio[0]) * OLIVE
    rel = lum / np.median(lum[face])
    wood *= (np.maximum(rel, 1e-6) ** (GRAIN_CONTRAST - 1))[..., None]     # calmer grain
    light = np.clip((rel - 1) / 1.5, 0, 1) * GREY_LIGHT                    # light streaks go grey-olive
    wood = wood * (1 - light[..., None]) + (wood @ LUM)[..., None] * GREY_OLIVE / (GREY_OLIVE @ LUM) * light[..., None]
    mottle = pb.noise_metres(rng, (rows, cols), period, MOTTLE[0][0]) + .5 * pb.noise_metres(rng, (rows, cols), period,
                                                                                         MOTTLE[0][1])
    mottle += .25 * pb.noise_metres(rng, (rows, cols), period, .008)            # ragged edges
    pale = np.clip((mottle - np.quantile(mottle, 1 - MOTTLE[1])) * 10, 0, 1)
    stain = np.clip((np.quantile(mottle, MOTTLE[1]) - mottle) * 10, 0, 1)
    pale_colour = (wood @ LUM)[..., None] * PALE_OLIVE / (PALE_OLIVE @ LUM)
    wood = wood * (1 - pale[..., None]) + pale_colour * MOTTLE[2][0] * pale[..., None]
    wood *= (1 - (1 - MOTTLE[2][1]) * stain)[..., None]

    damp = sum(w * pb.noise_metres(rng, (rows, cols), period, size) for w, size in zip((.75, .45), DAMP[:2]))
    wood *= np.exp(DAMP[2] * damp)[..., None]
    y = passes['y'] % py
    strap_top, strap_bottom = (1 - spec['strap']['v'][0]) * py, (1 - spec['strap']['v'][1]) * py
    above = np.clip((y - strap_top) / ABOVE[1] + .5, 0, 1)
    wood *= (1 - (1 - ABOVE[0]) * above)[..., None]

    # near a crack the wood stays damp and dark
    from scipy.ndimage import distance_transform_edt
    crack = face & (passes['crevice'] < .35)
    near = distance_transform_edt(~np.tile(crack, (3, 1)))[rows:2 * rows] * (px / cols)
    side = np.clip(1 - near / CRACK_SIDE[0], 0, 1) * np.clip(.6 + .5 * pb.noise_metres(rng, (rows, cols), period, .03), 0, 1)
    wood *= (1 - (1 - CRACK_SIDE[1]) * side)[..., None]

    # rust and dirt washed down from the strap, in columns
    below = strap_bottom - y
    columns = pb.periodic_noise(rng, (1, cols), px / RUST[1], 1)[0]
    columns = np.clip((columns - np.quantile(columns, 1 - RUST[2])) * 1.5, 0, 1)
    streak = face * (below > 0) * np.exp(-np.maximum(below, 0) / (RUST[0] / 3)) * columns[None, :]
    wood = wood * (1 - .6 * streak[..., None]) + RUST[3] * .6 * streak[..., None]

    out = np.where(face[..., None], wood, 0.)
    median = np.median((wood @ LUM)[face])
    # the strap: the wood's dark olive, its rounded edges worn lighter
    normal = passes['normal'] / (np.linalg.norm(passes['normal'], axis=-1, keepdims=True) + 1e-9)
    edge = np.clip((1 - normal[..., 2]) * 6, 0, 1)
    iron = IRON_COLOUR / (IRON_COLOUR @ LUM) * median * STRAP_TONE[0] * (1 + (STRAP_TONE[1] - 1) * edge)[..., None]
    pitting = pb.noise_metres(rng, (rows, cols), period, .003) + .6 * pb.noise_metres(rng, (rows, cols), period, .012)
    iron *= np.exp(.28 * np.tanh(pitting))[..., None]                      # hammered and pitted
    scale = np.clip((pb.noise_metres(rng, (rows, cols), period, .01) - 1.) * 2, 0, 1)
    iron = iron * (1 - .5 * scale[..., None]) + RUST[3] * .7 * scale[..., None]   # rust scale
    out = np.where(strap[..., None], iron, out)
    out = np.where(nail[..., None], NAIL * (1 + .5 * edge)[..., None], out)

    occlusion = np.clip(passes['ao'], 0, 1) ** OCCLUSION[0] * np.clip(passes['crevice'], 0, 1) ** OCCLUSION[1]
    inside = np.clip((CRACK_BLACK[1] - passes['crevice']) / (CRACK_BLACK[1] - CRACK_BLACK[0]), 0, 1) * face
    out *= (occlusion * (1 - (1 - CRACK_BLACK[2]) * inside))[..., None]
    luminance = out @ LUM
    mean = np.median(luminance[face])
    out *= (((np.maximum(luminance, 1e-6) / mean) ** CONTRAST * mean) / np.maximum(luminance, 1e-6))[..., None]

    wet = np.clip(np.maximum(-damp * .6, side), 0, 1) * face
    dry = ROUGH[0][0] + (ROUGH[0][1] - ROUGH[0][0]) * np.clip(.5 + .3 * pb.noise_metres(rng, (rows, cols), period, .1), 0, 1)
    rough = np.where(face, dry - (dry - ROUGH[1]) * wet, 1.)
    rough = np.where(strap, ROUGH[2], rough)
    rough = np.where(nail, ROUGH[3], rough)
    rough = np.where(crack, 1., rough)
    return dict(albedo=np.clip(out, 0, 1), roughness=rough, normal=normal, crack=crack.astype(float),
                rust=streak, damp=np.clip(-damp, 0, 1) * face)


LAYOUTS = {'beam02': beam02}
OUT_SIZE = {'beam02': (1024, 2048)}
TEXTURES = {'beam02': 'NW_CITY_WOODBEAM_02.TGA'}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('layout')
    p.add_argument('texture', choices=sorted(LAYOUTS))
    p.add_argument('out', type=Path)
    p.add_argument('--seed', type=int, default=0)
    p = sub.add_parser('render')
    p.add_argument('layout', type=Path)
    p.add_argument('out_dir', type=Path)
    p.add_argument('--wood', type=Path, help='image_gen surface of the wood')
    p.add_argument('--resolution', type=int, default=1024)
    p.add_argument('--samples', type=int, default=64)
    p.add_argument('--street', action='store_true', help='perspective views of a wall of beams instead of the passes')
    p.add_argument('--sun', type=float, default=0., help='a raking sun, for the block-out only')
    p = sub.add_parser('compose')
    p.add_argument('layout', type=Path)
    p.add_argument('passes', type=Path)
    p.add_argument('out', type=Path)
    p.add_argument('--seed', type=int, default=0)
    p = sub.add_parser('blockout')
    p.add_argument('layout', type=Path)
    p.add_argument('passes', type=Path)
    p.add_argument('out', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'layout':
        spec, points, grids = LAYOUTS[args.texture](args.seed)
        pb.write_layout(args.out, spec, points, grids)
        print(args.out)
    elif args.command == 'render':
        surfaces = {}
        if args.wood:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            surfaces['oak'] = pb.seamless_along(args.wood, args.out_dir / 'surface-wood.png')
        spec = json.loads(args.layout.read_text())
        px, py = spec['period']
        # a wall of beams, stretched along as the game stretches the texture
        options = (dict(view='street', pitch='90', eaves='.6', stretch=str(STRETCH), close=f'{px / 2},{py * .75}')
                   if args.street else {})
        if args.sun:
            options['sun'] = str(args.sun)
        print(pb.render(args.layout, args.out_dir, surfaces, args.resolution, args.samples, options))
    elif args.command == 'compose':
        spec = json.loads(args.layout.read_text())
        result = compose(pb.read_passes(args.passes, spec['period']), spec, args.seed)
        name = next(k for k, f in LAYOUTS.items() if spec['texture'] == TEXTURES[k])
        pb.write_maps(args.out, OUT_SIZE[name], result['albedo'], result['normal'],
                      {k: result[k] for k in ('roughness', 'crack', 'rust', 'damp')})
        print(args.out)
    elif args.command == 'blockout':
        spec = json.loads(args.layout.read_text())
        passes = pb.read_passes(args.passes, spec['period'])
        width = passes['piece'].shape[1]
        pb.write_preview(passes, args.out, (width, round(width * spec['period'][1] / spec['period'][0])))
        print(args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
