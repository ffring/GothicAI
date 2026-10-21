#!/usr/bin/env python3
"""Assemble the slate roof texture NW_CITY_ROOF_04 from image_gen slate-stone surfaces.

The image generator draws slate as large symmetric fish scales and makes every tile alike
(reviewer Р1 on city-roofs, rounds 1–6). The layout is therefore ours, measured from the
original (reports/content/remaster/city-roofs/intent.md): horizontal courses, 16 slates
across and 18 courses down a 1024² square; every joint slants from upper left to lower
right and sweeps right at the bottom like a rounded tail; courses are separated by dark
lines. Each slate takes its own patch of a generated stone surface with its own tone; some
slates are chipped at the tail; dark wet streaks run down across several courses.

The square tiles in both axes by construction: 1024 is a whole number of slates, the
course phases wrap, and the streak noise is periodic. Original pixels are never used.

    slate_roof.py OUT.png SURFACE.png [SURFACE.png ...] [--seed N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

SIZE, ACROSS, COURSES = 1024, 16, 18
SLANT = .85         # horizontal joint travel over one course, in course heights
JOINT_PX = 3.       # joint line width
COURSE_LINE = .13   # soft dark overlap band at the bottom of a course, share of course height
CHIPPED = .12       # share of slates with a broken tail
PALE = .07          # share of paler weathered slates
STONE_SCALE = .4    # the generated stone is drawn at 1024 px per ~2.5 slates; shrink its detail


def _linear(srgb):
    return np.where(srgb <= .04045, srgb / 12.92, ((srgb + .055) / 1.055) ** 2.4)


def _srgb(linear):
    linear = np.clip(linear, 0, 1)
    return np.where(linear <= .0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - .055)


def periodic_noise(rng, size, scale_x, scale_y):
    """Smooth noise that wraps in both axes: white noise filtered in the frequency domain."""
    fy = np.fft.fftfreq(size)[:, None] * size
    fx = np.fft.fftfreq(size)[None, :] * size
    spectrum = np.exp(-(fx / scale_x) ** 2 - (fy / scale_y) ** 2)
    field = np.fft.ifft2(np.fft.fft2(rng.standard_normal((size, size))) * spectrum).real
    return (field - field.mean()) / (field.std() + 1e-9)


def layout(rng, size=SIZE, across=ACROSS, courses=COURSES, slant=SLANT):
    """Per pixel: course, slate index within the course, position inside the slate (u, v)."""
    width, height = size / across, size / courses
    y, x = np.mgrid[0:size, 0:size].astype(float)
    course = np.floor(y / height).astype(int) % courses
    v = y / height - np.floor(y / height)
    phase = rng.uniform(0, width, courses)
    # nearly straight diagonal joints that sweep a little more to the right near the tail
    shifted = (x - phase[course] - slant * height * (.55 * v + .45 * v ** 2)) % size
    slate = np.floor(shifted / width).astype(int) % across
    u = shifted / width - np.floor(shifted / width)
    return course, slate, u, v, width, height


def assemble(surfaces, seed=0):
    rng = np.random.default_rng(seed)
    course, slate, u, v, width, height = layout(rng)
    stones = []
    for s in surfaces:
        side = round(min(s.size) * STONE_SCALE)
        stones.append(_linear(np.asarray(s.convert('RGB').resize((side, side), Image.Resampling.LANCZOS),
                                         dtype=float) / 255))
    count = COURSES * ACROSS
    which = rng.integers(0, len(stones), count)
    tone = np.exp(rng.normal(0, .14, count)) * np.where(rng.random(count) < PALE, 1.45, 1.)
    margin = int(width * 2.5)
    span = min(min(s.shape[:2]) for s in stones) - margin
    origin = rng.integers(0, span, (count, 2))
    chipped = rng.random(count) < CHIPPED
    chip_depth = rng.uniform(.25, .5, count)

    ident = course * ACROSS + slate
    local_y = (v * height).astype(int)
    local_x = (u * width).astype(int)
    out = np.zeros((SIZE, SIZE, 3))
    for index, stone in enumerate(stones):
        mask = which[ident] == index
        sy = (origin[ident, 0] + local_y) % stone.shape[0]
        sx = (origin[ident, 1] + local_x) % stone.shape[1]
        out[mask] = stone[sy[mask], sx[mask]]
    out *= tone[ident][..., None]

    # Dark wet streaks: elongated blotches running down across several courses, darker cores.
    streak = periodic_noise(rng, SIZE, scale_x=14, scale_y=4)
    out *= (1 - .7 * np.clip((streak - .35) / 1.4, 0, 1))[..., None]

    # The bottom of each course is a soft dark band where the next course overlaps; its edge is
    # uneven from slate to slate and the tail corner is rounded into the joint.
    # periodic_noise keeps frequencies up to `scale` cycles per square: small scale, smooth field
    tilt = rng.uniform(-.06, .06, count)
    edge = 1 - COURSE_LINE * rng.uniform(.6, 1.5, count)[ident] - tilt[ident] * (u - .5) \
        - .015 * periodic_noise(rng, SIZE, scale_x=12, scale_y=12)
    corner = np.clip((u - .8) / .2, 0, 1) ** 2 * .35
    band = np.clip((v - (edge - corner - .06)) / .06, 0, 1)
    out *= (1 - .92 * band)[..., None]
    out *= (1 - .18 * np.clip(1 - v / .15, 0, 1))[..., None]   # under the overlap of the course above

    joint = np.minimum(u, 1 - u) * width < JOINT_PX / 2
    tail = chipped[ident] & (u > .72) & (v > 1 - chip_depth[ident]) & (u - .72 > (1 - v) * .9)
    out[joint | tail] = np.array([.012, .012, .011])
    return (np.clip(_srgb(out), 0, 1) * 255 + .5).astype(np.uint8)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('out')
    parser.add_argument('surfaces', nargs='+')
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args(argv)
    surfaces = [Image.open(p) for p in args.surfaces]
    pixels = assemble(surfaces, seed=args.seed)
    Image.fromarray(pixels).save(args.out)
    meta = dict(tool='scripts/content/slate_roof.py', seed=args.seed, size=SIZE, across=ACROSS, courses=COURSES,
                slant=SLANT, joint_px=JOINT_PX, course_line=COURSE_LINE, chipped=CHIPPED, pale=PALE,
                stone_scale=STONE_SCALE,
                surfaces={p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in args.surfaces})
    Path(args.out).with_suffix('.layout.json').write_text(json.dumps(meta, indent=2) + '\n')
    print(args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
