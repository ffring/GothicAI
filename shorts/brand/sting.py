"""The end sting of a short: a letter on the desk, the name GOTHIC AI written in ink from left to right, the red
wax seal with the G of that lettering pressed onto it — the frame jolts at the press, the game's diary-entry sound
starts there — and the candle flickers to the end. 1080×1920, 30 fps, 2.2 s, -14 LUFS of its own sound.

The pictures and the sound are not in the repository (generated art and a sound of the game): they live in
~/.local/share/gothic-ai/brand/sting/ — bg.png (the blank letter, top-down), seal-green.png (the seal on chroma
green), lettering.png (the name, black with alpha), LOGENTRY.WAV
(from the game's Sounds.vdf).

    uv run --project shorts python shorts/brand/sting.py   -> <assets>/sting.mp4 (+ sting.png, its frames)
"""

# Keep the existing rendering and audio commands unchanged. Pillow evaluates
# point() callbacks immediately within each frame, so they do not escape the loop.
# ruff: noqa: E501, B023

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageEnhance, ImageFilter

ASSETS = Path.home() / ".local/share/gothic-ai/brand/sting"
W, H, FPS, LENGTH = 1080, 1920, 30, 2.2
INK = (53, 30, 16)  # iron-gall brown; the paper is multiplied by it, so its grain shows through the letters
WRITE = (0.10, 0.65)   # the name is written between these seconds
PRESS = (0.58, 0.85)   # the seal comes down; it touches the paper at the second one
SOUND_LEAD = 0.06      # the diary sound's first note sounds this long after its start
NAME_Y, NAME_W, SEAL_Y, SEAL_D = 0.40, 0.70, 0.62, 360


def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    k = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def keyed(path: Path) -> Image.Image:
    """The seal cut out of its chroma green: alpha from how much green outweighs red and blue, the green spill
    taken out of the edge, cropped to the seal."""
    r, g, b = Image.open(path).convert("RGB").split()
    other = ImageChops.lighter(r, b)
    spill = ImageChops.subtract(g, other)
    a = spill.point(lambda v: 255 - min(255, max(0, (v - 30) * 255 // 60)))
    g = ImageChops.darker(g, other.point(lambda v: min(255, v + 8)))
    img = Image.merge("RGBA", (r, g, b, a.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1))))
    a = img.split()[3]
    return img.crop(a.point(lambda v: 255 if v > 40 else 0).getbbox())


def ease_in(t: float) -> float:
    return t ** 3


def render(assets: Path = ASSETS) -> Path:
    paper = cover(Image.open(assets / "bg.png").convert("RGB"), W, H)
    inked = ImageChops.multiply(paper, Image.new("RGB", (W, H), INK))
    name = Image.open(assets / "lettering.png").convert("RGBA").split()[3]
    name = name.crop(name.getbbox())
    nw = round(W * NAME_W)
    name = name.resize((nw, round(nw * name.height / name.width)), Image.LANCZOS).filter(ImageFilter.GaussianBlur(0.6))
    na = Image.new("L", (W, H), 0)
    nx, ny = (W - nw) // 2, round(H * NAME_Y - name.height / 2)
    na.paste(name.point(lambda v: v * 9 // 10), (nx, ny))
    ramp = Image.linear_gradient("L").rotate(90, expand=True).resize((40, H))  # 255 → 0 from left to right
    seal = keyed(assets / "seal-green.png")
    seal = seal.resize((SEAL_D, round(SEAL_D * seal.height / seal.width)), Image.LANCZOS)
    # lit by the candle like the paper: warmer, darker, falling off from the upper left
    fall = Image.linear_gradient("L").rotate(45, expand=True).resize(seal.size).point(lambda v: 255 - v * 70 // 255)
    lit = ImageChops.multiply(seal.convert("RGB"), Image.new("RGB", seal.size, (236, 196, 160)))
    lit = ImageChops.multiply(lit, Image.merge("RGB", (fall, fall, fall)))
    seal = Image.merge("RGBA", (*lit.split(), seal.split()[3]))
    out = assets / "sting.mp4"
    with tempfile.TemporaryDirectory() as tmp:
        frames = Path(tmp)
        for i in range(round(LENGTH * FPS)):
            t = i / FPS
            # the ink: a soft edge sweeping from the name's left to its right
            k = min(max((t - WRITE[0]) / (WRITE[1] - WRITE[0]), 0), 1)
            edge = round(nx + k * (nw + 80) - 40)
            sweep = Image.new("L", (W, H), 0)
            sweep.paste(255, (0, 0, max(edge - 40, 0), H))
            sweep.paste(ramp, (edge - 40, 0))
            img = Image.composite(inked, paper, ImageChops.multiply(na, sweep)).convert("RGBA")
            # the seal: comes down (bigger, transparent, its shadow wide) and presses at PRESS[1]
            if t >= PRESS[0]:
                p = min((t - PRESS[0]) / (PRESS[1] - PRESS[0]), 1)
                after = max(t - PRESS[1], 0)
                scale = 1 + 0.6 * (1 - ease_in(p)) + (0.035 * math.exp(-after * 30) if after else 0)
                s = seal.resize((round(seal.width * scale), round(seal.height * scale)), Image.LANCZOS)
                lift = (1 - ease_in(p)) * 60
                shadow = Image.new("RGBA", s.size, (20, 10, 5, 0))
                shadow.putalpha(s.split()[3].point(lambda v: int(v * (0.55 - 0.25 * (1 - p)))))
                shadow = shadow.filter(ImageFilter.GaussianBlur(6 + 18 * (1 - p)))
                cx, cy = W // 2, round(H * SEAL_Y)
                img.alpha_composite(shadow, (cx - s.width // 2 + round(8 + lift * 0.5), cy - s.height // 2 + round(10 + lift)))
                s.putalpha(s.split()[3].point(lambda v: int(v * min(p * 2.5, 1))))
                img.alpha_composite(s, (cx - s.width // 2, cy - s.height // 2))
            img = img.convert("RGB")
            # the jolt of the press, and the candle's flicker; in from black, out to black
            if PRESS[1] <= t < PRESS[1] + 0.12:
                j = math.exp(-(t - PRESS[1]) * 25)
                img = img.transform(img.size, Image.AFFINE, (1, 0, 6 * j * math.sin(t * 90), 0, 1, 4 * j * math.cos(t * 70)),
                                    resample=Image.BICUBIC)
            light = 1 + 0.025 * math.sin(13 * t) + 0.015 * math.sin(29 * t + 1)
            light *= min(t / 0.12, 1) * min((LENGTH - t) / 0.25, 1)
            ImageEnhance.Brightness(img).enhance(light).save(frames / f"{i:04d}.png")
        start = PRESS[1] - SOUND_LEAD
        sound = (f"[0:a]aresample=48000,pan=stereo|c0=c0|c1=c0,volume=10dB,adelay={round(start * 1000)}|{round(start * 1000)},"
                 f"apad,atrim=0:{LENGTH},afade=t=out:st={LENGTH - 0.3}:d=0.3,loudnorm=I=-14:TP=-1.5[a]")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(assets / "LOGENTRY.WAV"), "-framerate", str(FPS),
                        "-i", str(frames / "%04d.png"), "-filter_complex", sound, "-map", "1:v", "-map", "[a]",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS),
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(out)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-vf", "fps=6,scale=216:-2,tile=7x2", "-frames:v", "1",
                    str(out.with_suffix(".png"))], check=True)
    return out


if __name__ == "__main__":
    where = Path(sys.argv[1]) if len(sys.argv) > 1 else ASSETS
    print(render(where))
