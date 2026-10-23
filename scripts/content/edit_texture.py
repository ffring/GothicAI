#!/usr/bin/env python3
"""Maps of a texture remastered by editing the original: an image_gen edit of the original texture
(the user's decision of 2026-10-23, docs/own-content.md, «Граница с оригиналом») becomes the albedo,
its colour brought back to the original's broad colour, and a normal map from its own fine light.

    python3 scripts/content/edit_texture.py EDIT.png ORIGINAL.png OUT.png --period W H [--size W H]

The generator draws the original sharper but drifts its colour warm and yellow; the colour of every
broad patch (a few centimetres) is taken back from the original, the edit keeps all detail finer than
that. The relief is the edit's light less its broad patches, scaled to a median normal tilt, as the
normal-map rule asks (docs/remaster-pipeline.md). Such a texture is derived from the original: its
registry entry says so, and the original's file stays out of git.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

import piece_bake as pb
import timber_bake as tb

COLOUR_SIZE = .05      # metres: the original's colour is taken back over patches of about this size
RELIEF_SIZE = .012     # metres: light finer than this becomes relief
TILT = 8.              # median normal tilt in degrees: the plaster about 7, the oak 9-10 (rule of 2026-10-23)
ROUGHNESS = .9


def _load(path: Path, size) -> np.ndarray:
    from PIL import Image
    image = Image.open(path).convert('RGB').resize(size, Image.LANCZOS)
    return (np.asarray(image, dtype=np.float64) / 255) ** 2.2


def _normal(height: np.ndarray, metres: float) -> np.ndarray:
    dy, dx = np.gradient(height, metres)
    normal = np.stack([-dx, dy, np.ones_like(dx)], axis=-1)
    return normal / np.linalg.norm(normal, axis=-1, keepdims=True)


def _median_tilt(normal: np.ndarray) -> float:
    return float(np.degrees(np.median(np.arccos(np.clip(normal[..., 2], -1, 1)))))


def remaster(edit: np.ndarray, original: np.ndarray, period) -> dict:
    """Albedo, normal and roughness of the edit, its colour taken back from the original."""
    from scipy.ndimage import gaussian_filter
    rows, cols = edit.shape[:2]
    metres = period[1] / rows
    sigma = COLOUR_SIZE / metres
    broad = lambda image: np.stack([gaussian_filter(image[..., k], sigma, mode='wrap') for k in range(3)], -1)
    gain = np.clip(broad(original) / np.maximum(broad(edit), 1e-6), .4, 2.5)
    albedo = np.clip(edit * gain, 0, 1)
    light = edit @ tb.LUM
    fine = np.log(np.maximum(light, 1e-4)) - np.log(np.maximum(gaussian_filter(light, RELIEF_SIZE / metres, mode='wrap'), 1e-4))
    height = gaussian_filter(fine, 1., mode='wrap')
    scale = .001
    for _ in range(4):                                   # tilt grows about linearly with the scale
        tilt = _median_tilt(_normal(height * scale, metres))
        scale *= TILT / max(tilt, 1e-3)
    normal = _normal(height * scale, metres)
    return dict(albedo=albedo, normal=normal, roughness=np.full((rows, cols), ROUGHNESS))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('edit', type=Path, help='the image_gen edit of the original texture')
    parser.add_argument('original', type=Path, help='the original texture (a PNG outside the repository)')
    parser.add_argument('out', type=Path)
    parser.add_argument('--period', type=float, nargs=2, required=True, metavar=('W', 'H'),
                        help='metres of wall the texture covers')
    parser.add_argument('--size', type=int, nargs=2, default=(1024, 1024), metavar=('W', 'H'))
    args = parser.parse_args(argv)
    work = (args.size[0] * 2, args.size[1] * 2)
    result = remaster(_load(args.edit, work), _load(args.original, work), args.period)
    pb.write_maps(args.out, tuple(args.size), result['albedo'], result['normal'], {'roughness': result['roughness']})
    print(args.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
