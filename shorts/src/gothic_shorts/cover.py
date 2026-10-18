"""Channel template B: a topic and optional comparison line over the central seam."""

from __future__ import annotations

import math
import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError

W, H = 1080, 1920
FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
INK = (20, 12, 8)
WHITE = (255, 255, 255)
GOLD = (255, 214, 120)
MAX_JPEG_BYTES = 2_000_000


class CoverError(RuntimeError):
    pass


def _portrait(image: Image.Image) -> Image.Image:
    return ImageOps.fit(ImageOps.exif_transpose(image).convert("RGB"), (W, H), Image.Resampling.LANCZOS)


def frame(source: str | Path, at: float = 0) -> Image.Image:
    """Read an image or extract a video frame, centred and cropped to 1080×1920."""
    source = Path(source).expanduser()
    if not math.isfinite(at) or at < 0:
        raise CoverError("the frame time must be finite and non-negative")
    if not source.is_file():
        raise CoverError(f"no source: {source}")
    try:
        with Image.open(source) as image:
            return _portrait(image)
    except UnidentifiedImageError:
        pass  # A video is decoded by ffmpeg below.
    except OSError as err:
        raise CoverError(f"cannot read {source}: {err}") from err

    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-ss", str(at), "-i", str(source), "-frames:v", "1",
             "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1",
             "-f", "image2pipe", "-vcodec", "png", "pipe:1"],
            check=True, capture_output=True,
        )
        with Image.open(BytesIO(result.stdout)) as image:
            return image.convert("RGB")
    except subprocess.CalledProcessError as err:
        detail = err.stderr.decode(errors="replace") if err.stderr else str(err)
        raise CoverError(f"ffmpeg could not extract the cover frame: {detail[-3000:]}") from err
    except OSError as err:
        raise CoverError(f"cannot extract a frame from {source} at {at}s: {err}") from err


def _fit(text: str, size: int, width: int) -> ImageFont.FreeTypeFont:
    while size > 0:
        font = ImageFont.truetype(FONT, size)
        # Fit the visible outline too: the narrower centre crop needs 50 px margins.
        left, _, right, _ = font.getbbox(text, anchor="mm", stroke_width=max(4, size // 12))
        if left >= -width / 2 and right <= width / 2:
            return font
        size -= 4 if size > 4 else 1
    raise CoverError("the cover text is too long")


def _band(image: Image.Image) -> None:
    top, bottom, feather = H // 2 - 190, H // 2 + 190, 60
    shade = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(shade)
    for y in range(top - feather, bottom + feather):
        edge = min(y - (top - feather), (bottom + feather) - y)
        alpha = int(170 * min(1.0, edge / feather))
        draw.line([(0, y), (W, y)], fill=(*INK, alpha))
    image.alpha_composite(shade)


def _text(image: Image.Image, position: tuple[int, int], text: str,
          font: ImageFont.FreeTypeFont, fill: tuple[int, int, int]) -> None:
    stroke = max(4, font.size // 12)
    shadow = Image.new("RGBA", image.size)
    ImageDraw.Draw(shadow).text(
        position, text, font=font, fill=(0, 0, 0, 200), anchor="mm", stroke_width=stroke + 6,
        stroke_fill=(0, 0, 0, 200),
    )
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
    ImageDraw.Draw(image).text(
        position, text, font=font, fill=(*fill, 255), anchor="mm", stroke_width=stroke,
        stroke_fill=(*INK, 255),
    )


def _lettering(title: str, sub: str | None) -> Image.Image:
    lettering = Image.new("RGBA", (W, H))
    _text(lettering, (W // 2, H // 2 - 75), title, _fit(title, 210, W - 100), WHITE)
    if sub:
        _text(lettering, (W // 2, H // 2 + 95), sub, _fit(sub, 110, W - 160), GOLD)
    return lettering


def render(frame: Image.Image, title: str, sub: str | None) -> Image.Image:
    """Render template B; text stays inside the centre square used by channel surfaces."""
    title = " ".join(title.split()).upper()
    sub = " ".join(sub.split()) if sub else None
    if not title:
        raise CoverError("the cover needs a title")
    try:
        image = _portrait(frame).convert("RGBA")
        _band(image)
        image.alpha_composite(_lettering(title, sub))
        return image.convert("RGB")
    except OSError as err:
        raise CoverError(f"cannot render the cover (Arial Black is required at {FONT}): {err}") from err


def save(image: Image.Image, path: str | Path) -> Path:
    """Write a 1080×1920 JPEG within YouTube thumbnails.set's 2 MB limit."""
    path = Path(path).expanduser()
    try:
        image = _portrait(image)
        for quality in (95, 90, 85, 80, 75, 65, 55, 45, 35, 25, 15, 5):
            data = BytesIO()
            image.save(data, format="JPEG", quality=quality, optimize=True)
            if data.tell() <= MAX_JPEG_BYTES:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data.getvalue())
                return path
    except OSError as err:
        raise CoverError(f"cannot write the cover {path}: {err}") from err
    raise CoverError("could not compress the cover below 2 MB")
