"""The last step of scripts/hd-textures.sh: the 4x textures down to at most MAX pixels a side, with the
colors of the originals.

Real-ESRGAN draws fine detail (hair, eyes, seams of leather) but shifts the tone a little: a head came
out darker and with more contrast than its body texture, and the two meet at the neck. So the coarse
part of a texture (what a blur as wide as one pixel of the original keeps) comes from the original,
the fine part from the upscaled one.

    uv run --with numpy --with pillow scripts/hd/finish.py <src dir> <x4 dir> <out dir> <max side>
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def blur(a: np.ndarray, radius: float) -> np.ndarray:
    channels = [Image.fromarray(a[..., c]).filter(ImageFilter.GaussianBlur(radius)) for c in range(a.shape[2])]
    return np.stack([np.asarray(c, dtype=np.float32) for c in channels], axis=2)


def finish(src: Path, up: Path, out: Path, max_side: int) -> None:
    orig = Image.open(src).convert("RGBA")
    hd = Image.open(up).convert("RGBA")
    w, h = orig.size
    scale = min(4.0, max_side / max(w, h))
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    if scale <= 1.0:
        orig.save(out)
        return
    hd = hd.resize(size, Image.LANCZOS)
    base = orig.resize(size, Image.BICUBIC)

    rgb_hd = np.asarray(hd, dtype=np.float32)[..., :3]
    # the coarse part from the original, the fine part from the upscaled one
    r = scale * 0.75
    rgb = rgb_hd + blur(np.asarray(base)[..., :3], r) - blur(np.asarray(hd)[..., :3], r)
    alpha = np.asarray(hd, dtype=np.float32)[..., 3:4]
    if not (np.asarray(orig)[..., 3] < 255).any():
        alpha = np.full_like(alpha, 255.0)  # no alpha in the original: none in the result either
    res = np.concatenate([rgb, alpha], axis=2)
    Image.fromarray(np.clip(res + 0.5, 0, 255).astype(np.uint8), "RGBA").save(out, optimize=False, compress_level=6)


def main() -> None:
    src, up, out, max_side = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4])
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for line in (src / "textures.txt").read_text().splitlines():
        name = line.split()[0]
        if not (up / f"{name}.png").is_file():
            print(f"missing upscaled {name}", file=sys.stderr)
            continue
        finish(src / f"{name}.png", up / f"{name}.png", out / f"{name}.png", max_side)
        n += 1
    print(f"hd-textures: {n} textures in {out}")


if __name__ == "__main__":
    main()
