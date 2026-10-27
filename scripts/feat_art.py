# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pillow"]
# ///
"""Feat pictures in the approved style (docs/achievements.md#изображение): generate through headless
`codex exec` image_gen and process to the house look.

    uv run scripts/feat_art.py gen OUT.png "EMBLEM"     # raw picture from the style block + anchors
    uv run scripts/feat_art.py process RAW.png OUT_DIR  # OUT_DIR/engraving.jpg (512) and icon.png (128)

Processing removes the generator's spread: the subject is cropped to its content and re-centred with one
margin, colours are pulled to the fixed palette (ink, parchment, one accent), the parchment gets the same
grain everywhere, and the icon gets the common frame. The prototype of the stage 1b server module.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

KIT = Path(__file__).resolve().parents[1] / "server/src/gothic_ai/assets/feats"
STYLE = json.loads((KIT / "style.json").read_text())
INK, PARCH, ACCENT = (np.array(STYLE["palette"][k], float) for k in ("ink", "parchment", "accent"))

WORK = 1024  # working size of the processed picture
FILL = 0.76  # the subject's longer side as a share of the square
INK_LO = 70.0  # luminance ramp: below is solid ink
PAPER_KNEE = 0.8  # share of the picture's own paper tone above which it is bare parchment (no stains)
FRAME = (0.034, 0.020, 0.010)  # icon frame at 512: outer ink rule, parchment gap, inner rule (share of side)

WRAP = (
    "Use your image generation tool exactly once with the prompt below and the attached images. Do not "
    "view, judge or edit the result. Reply with only the absolute path of the generated file.\n\nPrompt:\n"
)


def prompt(emblem: str) -> str:
    return f"{STYLE['style']}\n\nSubject: {emblem}\n\n{STYLE['common']}\n\n{STYLE['anchor_note']}"


def gen(out: Path, emblem: str, timeout: int = 180) -> bool:
    """One image_gen call in an empty trusted repo, read-only sandbox; one retry."""
    images = [arg for a in STYLE["anchors"] for arg in ("-i", str(KIT / a))]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp, check=True)
        for _ in range(2):
            try:
                r = subprocess.run(
                    ["codex", "exec", "--skip-git-repo-check", "-s", "read-only", *images, "-"],
                    input=WRAP + prompt(emblem),
                    cwd=tmp,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                continue
            paths = re.findall(r"(/\S+\.png)", r.stdout)
            if r.returncode == 0 and paths and Path(paths[-1]).exists():
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(paths[-1], out)
                out.with_suffix(".txt").write_text(prompt(emblem))
                return True
    return False


def _accent_weight(rgb: np.ndarray) -> np.ndarray:
    """How strongly each pixel belongs to the red accent: a saturated red hue, not the warm parchment."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(2), rgb.min(2)
    sat = (mx - mn) / np.maximum(mx, 1)
    red = (r >= g) & (r >= b) & ((g - b) < 0.45 * (r - b + 1e-6))  # hue below ~25°, parchment sits at ~38°
    return np.clip((sat - 0.42) / 0.18, 0, 1) * red * np.clip((mx - 45) / 40, 0, 1)


def _grain(size: int) -> np.ndarray:
    """The same paper grain for every picture: soft blotches and a fine tooth, seeded."""
    rng = np.random.default_rng(1)
    blot = Image.fromarray((rng.random((24, 24)) * 255).astype(np.uint8)).resize((size, size), Image.BICUBIC)
    blot = np.asarray(blot.filter(ImageFilter.GaussianBlur(size / 40)), float) / 255 - 0.5
    tooth = rng.normal(0, 1, (size, size))
    return 1 + 0.05 * blot + 0.018 * tooth


def normalise(src: Image.Image) -> tuple[Image.Image, dict]:
    rgb = np.asarray(src.convert("RGB"), float)
    lum = rgb @ [0.299, 0.587, 0.114]
    acc = _accent_weight(rgb)
    ink_hi = PAPER_KNEE * np.percentile(lum, 90)  # the generator's parchment varies in tone
    t = np.clip((lum - INK_LO) / (ink_hi - INK_LO), 0, 1)[..., None]
    base = INK + t * (PARCH - INK)
    shade = np.clip(lum / 120, 0, 1)[..., None]  # the accent keeps its own carved darks
    out = base + acc[..., None] * (INK + shade * (ACCENT - INK) - base)

    mask = (lum < 120) | (acc > 0.5)
    h, w = mask.shape
    rows = np.flatnonzero(mask.sum(1) > w * 0.004)
    cols = np.flatnonzero(mask.sum(0) > h * 0.004)
    if not len(rows) or not len(cols):
        raise ValueError("empty picture")
    y0, y1, x0, x1 = rows[0], rows[-1], cols[0], cols[-1]
    side = int(max(y1 - y0, x1 - x0) / FILL)
    cy, cx = (y0 + y1) // 2, (x0 + x1) // 2
    canvas = Image.new("RGB", (side, side), tuple(PARCH.astype(int)))
    canvas.paste(Image.fromarray(out.clip(0, 255).astype(np.uint8)), (side // 2 - cx, side // 2 - cy))
    pic = np.asarray(canvas.resize((WORK, WORK), Image.LANCZOS), float) * _grain(WORK)[..., None]

    stats = {
        "ink": round(float(mask.mean()), 3),
        "accent": round(float((acc > 0.5).mean()), 4),
        "fill": round(max(y1 - y0, x1 - x0) / max(h, w), 3),
    }
    return Image.fromarray(pic.clip(0, 255).astype(np.uint8)), stats


def icon(pic: Image.Image, size: int = 128) -> Image.Image:
    """The picture inside the common frame: a heavy ink rule and a thin one, like a broadside block."""
    s = 512
    outer, gap, inner = (round(f * s) for f in FRAME)
    frame = Image.new("RGB", (s, s), tuple(INK.astype(int)))
    d = ImageDraw.Draw(frame)
    d.rectangle((outer, outer, s - 1 - outer, s - 1 - outer), fill=tuple(PARCH.astype(int)))
    a = outer + gap
    d.rectangle((a, a, s - 1 - a, s - 1 - a), fill=tuple(INK.astype(int)))
    b = a + inner
    # the subject sits a little larger in the icon than in the engraving: the frame already gives the margin
    crop = round(WORK * (1 - FILL) * 0.28)
    frame.paste(
        pic.crop((crop, crop, WORK - crop, WORK - crop)).resize((s - 2 * b, s - 2 * b), Image.LANCZOS), (b, b)
    )
    return frame.resize((size, size), Image.LANCZOS)


def process(raw: Path, out_dir: Path) -> dict:
    pic, stats = normalise(Image.open(raw))
    out_dir.mkdir(parents=True, exist_ok=True)
    pic.resize((512, 512), Image.LANCZOS).save(out_dir / "engraving.jpg", quality=90)
    icon(pic).save(out_dir / "icon.png", optimize=True)
    stats["ok"] = 0.06 <= stats["ink"] <= 0.6 and stats["accent"] <= 0.08
    return stats


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "gen":
        sys.exit(0 if gen(Path(args[0]), args[1]) else 1)
    elif cmd == "process":
        print(json.dumps(process(Path(args[0]), Path(args[1]))))
    else:
        sys.exit(__doc__)
