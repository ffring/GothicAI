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
SHUTTER = dict(rect=(.21, .79, .19, .63), boards=5, gap=.007, z=.006, straps=(.25, .57), strap=(.028, .004))
GAP = (.004, .010, .4)       # the plaster's shrinkage gap at the timber: width, depth, share of the edge
PEG = (.013, .003)           # oak pegs at the joints: radius, how far they stand out
PLASTER_RELIEF = ((.5, .004), (.12, .0022), (.03, .0008))   # median tilt about 5°   # undulation of the render: size, height
SPALLS = (2.5, (.02, .07), .003)  # places where the whitewash flaked off: count per m², radius, depth
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
    ragged = pb.noise_metres(rng, gx.shape, period, .025)
    for _ in range(round(SPALLS[0] * px * py)):      # flaked whitewash: shallow dips with torn edges
        cx, cy, r = rng.uniform(0, px), rng.uniform(0, py), rng.uniform(*SPALLS[1])
        d = np.hypot((gx - cx + px / 2) % px - px / 2, (gy - cy + py / 2) % py - py / 2) / r
        d *= np.clip(1 + .45 * ragged, .5, None)
        dip = np.clip(3 - d * 3, 0, 1)
        height -= SPALLS[2] * dip
        labels[dip > .5] = 1
    members = MEMBERS[variant]
    timber = np.zeros(gx.shape, bool)
    for index, member in enumerate(members):
        kind, _, _, width = member
        along, across, length = _local(gx, gy, period, member)
        # hand-built: rails and heads sag a little between their posts, posts and jambs lean
        t = np.clip(along / max(length, .01), 0, 1)
        if kind in ('rail', 'head', 'sill') and length < px * 1.5:
            across = across + rng.uniform(.004, .016) * np.sin(math.pi * t) * (1 if kind != 'rail' or member[1][1] > .5 else .6)
        elif kind in ('post', 'jamb'):
            across = across + rng.normal(0, .006) * (t - .5) * 2
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
    split = np.abs(gx - middle) < .008                  # the two leaves meet with a clear dark line
    for leaf, (a, b) in enumerate(((x0, middle), (middle, x1))):
        widths = rng.uniform(.82, 1.18, SHUTTER['boards'])     # boards of the widths a plank gave
        edges = a + np.concatenate([[0], np.cumsum(widths)]) / widths.sum() * (b - a)
        for k in range(SHUTTER['boards']):
            bx0 = edges[k] + SHUTTER['gap'] / 2
            bx1 = edges[k + 1] - SHUTTER['gap'] / 2
            top, foot = y1 - rng.uniform(0, .012), y0 + rng.uniform(0, .012)   # uneven ends
            board = inside & (gx > bx0) & (gx < bx1) & (gy < top) & (gy > foot) & ~split
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
# Targets: the original's plaster as measured, dirty like it (the user, 2026-10-23: clean white walls
# belong to the upper quarter), the oak as accepted on house-2, the shutter as the original's boards.
PLASTER = (159, 158, 134)
PLASTER_SPREAD = ((0, 55), (5, 83), (10, 108), (25, 137), (50, 160), (75, 178), (90, 193), (95, 202),
                  (99, 220), (100, 232))       # the original's plaster luminance (sRGB) by percentile
PLASTER_HUE = ((52, 50, 24), (107, 106, 69), (138, 137, 107), (163, 163, 139), (186, 186, 168), (212, 213, 200))
# its colour by light: olive-black in the mould and stains, near-neutral in the thick wash
OAK = (41, 34, 20)
OAK_SPREAD = (31, 56)                  # the original timber's sRGB luminance: median, p90
GRAIN = 2.8                            # power on the oak picture's light: stripier grain
EDGE = (.03, .004, 2.8, .3)          # worn arris: reach, the dark rim's width (metres), lightening; the rim's factor
SHUTTER_TONE = (63, 58, 47)
IRON = (35, 31, 23)
WASH = (206, 207, 193)                 # the lime wash where it is thick: the original's lightest tenth
RENDER = (120, 119, 85)                # the olive render under it where it is worn thin: its darkest tenth
WASH_COVER = ((.3, .45), (.09, .4), (.025, .3), (.01, .25))   # patchiness of the wash: sizes in metres, weights
WASH_EDGE, WASH_SHARE = 3., .7       # how sharp the wash's patches are, how much of the wall it holds
SPALL_TONE = 1.1                       # flaked to the render, a little lighter than its stains
DAMP = (.45, 1.6)                      # the wash washed off under each rail: reach in metres, how much
STREAKS = (.035, .5)                   # rain streaks down the panels: width and length in metres
MOULD = (.16, 1.)                     # black-green mould hanging from each rail: reach in metres, strength
MOULD_TONE = (48, 52, 28)
RIM = (.025, .3)                       # grime at the timber: reach in metres, darkening
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
        out[mask] = _sample(wood_img, across[mask] + index * .37, along[mask], 1.) * math.exp(rng.normal(0, .2))
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
    out[wood | shutter] = (out[wood | shutter] @ tb.LUM)[:, None] ** GRAIN * np.array([1., .86, .62])   # grain, olive brown
    # the oak's tone spreads like the original's (luminance cv .66 against .28 without this): worn light
    # arrises along every member and a dark line where it goes into the plaster, on both sides alike
    from scipy.ndimage import distance_transform_edt
    inner = distance_transform_edt(wood) * (py / rows)
    worn = np.clip(1 - inner / EDGE[0], 0, 1) * (inner > EDGE[1]) * wood
    worn *= np.clip(.6 + .6 * pb.noise_metres(rng, (rows, cols), period, .08), 0, 1)
    out *= (1 + EDGE[2] * worn)[..., None]
    out[wood & (inner <= EDGE[1])] *= EDGE[3]

    # plaster weathering, by form only: a lime wash, thick in places and worn thin in others down to the
    # olive render under it, washed off under the rails and blackened by mould there, rain streaks down
    # the panels, grime gathered at the timber
    timber_px = ~plaster & ~(kinds == 'shutter')
    r_idx = np.arange(rows)[:, None] * np.ones((1, cols))
    last = np.maximum.accumulate(np.where(timber_px, r_idx, -1e6), axis=0)
    rails_above = (r_idx - last) * (py / rows)       # how far the nearest timber straight above is
    streaks = pb.periodic_noise(rng, (rows, cols), px / STREAKS[0], py / STREAKS[1])
    cover = sum(w * pb.noise_metres(rng, (rows, cols), period, size) for size, w in WASH_COVER)
    reach, washed = DAMP
    ragged = reach * (1 + .5 * pb.noise_metres(rng, (rows, cols), period, .09) + .4 * streaks)
    damp = np.clip(1 - rails_above / np.maximum(ragged, .02), 0, 1) * plaster
    cover = 1 / (1 + np.exp(-WASH_EDGE * (cover + WASH_SHARE - washed * damp + .25 * streaks)))
    wash = _linear(WASH) / _linear(PLASTER)
    render = _linear(RENDER) / _linear(PLASTER)
    tone = render + (wash - render) * cover[..., None]
    detail = np.where(plaster[..., None], (out @ tb.LUM)[..., None] ** .6, 1.)   # the lime picture's grain, softened, without its hue
    out = np.where(plaster[..., None], tone * detail, out)
    out = out * (1 - spall[..., None]) + render * SPALL_TONE * spall[..., None]
    m_reach, m_depth = MOULD
    m_ragged = m_reach * (1 + .6 * pb.noise_metres(rng, (rows, cols), period, .05) + .7 * np.clip(streaks, 0, None))
    mould = np.clip(1 - rails_above / np.maximum(m_ragged, .01), 0, 1) ** 1.2 * plaster
    mould *= np.clip(.8 + .6 * pb.noise_metres(rng, (rows, cols), period, .03), 0, 1)
    out = out * (1 - m_depth * mould[..., None]) + _linear(MOULD_TONE) / _linear(PLASTER) * m_depth * mould[..., None]
    nearest = distance_transform_edt(plaster) * (py / rows)
    rim = np.clip(1 - nearest / RIM[0], 0, 1) ** 2 * plaster
    out *= (1 - RIM[1] * rim)[..., None]
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

    # the plaster's light spread as the original's: its luminance mapped rank by rank onto the
    # original's quantiles, its colour by light from the original's, a third of each pixel's own hue kept
    pix = out[plaster]
    lum = pix @ tb.LUM
    srgb = np.clip(lum, 1e-6, 1) ** (1 / 2.2) * 255
    q, values = zip(*PLASTER_SPREAD)
    ranks = np.argsort(np.argsort(srgb)) / max(len(srgb) - 1, 1) * 100
    target = np.interp(ranks, q, values)
    hue = np.array(PLASTER_HUE, dtype=float)
    hue_lum = hue @ tb.LUM
    colour = np.stack([np.interp(target, hue_lum, hue[:, k]) for k in range(3)], axis=-1) * \
        (target / np.maximum(np.stack([np.interp(target, hue_lum, hue[:, k]) for k in range(3)], -1) @ tb.LUM, 1e-6))[:, None]
    own = pix / np.maximum(lum, 1e-6)[:, None]
    own /= own.mean(axis=0)
    out[plaster] = _linear(np.clip(colour, 0, 255)) * own ** .35

    # the oak's light spread as the original's, not only its mean: sRGB luminance median and p90 of its
    # timber (31 and 56); a power curve through both keeps the dark rims dark
    lum = out[wood] @ tb.LUM
    srgb = np.clip(lum, 1e-6, 1) ** (1 / 2.2) * 255
    x50, x90 = np.percentile(srgb, [50, 90])
    b_ = math.log(OAK_SPREAD[1] / OAK_SPREAD[0]) / math.log(max(x90, 1e-3) / max(x50, 1e-3))
    a_ = math.log(OAK_SPREAD[0]) - b_ * math.log(max(x50, 1e-3))
    target = np.exp(a_ + b_ * np.log(np.maximum(srgb, 1e-3)))
    out[wood] *= (((target / 255) ** 2.2) / np.maximum(lum, 1e-6))[:, None]

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
