"""The short from a take: the pauses while the AI thinks cut down, the hero's voice mixed into the game's
sound, subtitles, the hook over the first seconds; 1080x1920, loudness for phones.

The texts are burnt in as images (Pillow), so ffmpeg needs no libass. A cut in another language takes
`subs.<lang>.json` from the take: every text of the short (hook, captions, lines) mapped to its translation;
`gothic-shorts subs` writes the list of texts to fill in. The same translations make a caption track for
the Russian short (`subtitles`, a SubRip file YouTube takes).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import srt
from .shot import Shot
from .timeline import Timeline, build, read_events

W, H = 1080, 1920
SUB_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
HOOK_FONT = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
HOOK_S = 2.8
HERO_COLOR = (255, 214, 102)
NPC_COLOR = (255, 255, 255)
NAME_COLOR = (230, 190, 120)


class CutError(RuntimeError):
    pass


@dataclass(frozen=True)
class Overlay:
    png: Path
    start: float
    end: float
    y: int  # top of the image on the frame


@dataclass(frozen=True)
class Shown:
    """A text of the cut, translated, and when it is on screen (seconds of the cut)"""

    kind: str  # "hook", "caption" or "line"
    text: str
    start: float
    end: float
    name: str = ""  # the npc's, over its line
    hero: bool = False


def texts(shot: Shot, tl: Timeline) -> list[str]:
    """Every text a cut shows, in order (what a translation has to cover)."""
    out = [shot.hook] if shot.hook else []
    out += [c.text for c in tl.captions] + [line.text for line in tl.lines]
    out += sorted({line.name for line in tl.lines if not line.hero and line.name})
    return list(dict.fromkeys(t for t in out if t))


def load_subs(take: Path, lang: str, needed: list[str]) -> dict[str, str]:
    if lang == "ru":
        return {t: t for t in needed}
    path = take / f"subs.{lang}.json"
    if not path.is_file():
        raise CutError(f"no {path}: gothic-shorts subs {take} --lang {lang} writes it to fill in")
    subs = json.loads(path.read_text())
    missing = [t for t in needed if not subs.get(t)]
    if missing:
        raise CutError(f"{path} has no translation for:\n" + "\n".join(f"  {t}" for t in missing))
    return subs


def write_subs_template(shot: Shot, take: Path, lang: str) -> Path:
    tl = build(read_events(take / "events.jsonl"))
    path = take / f"subs.{lang}.json"
    old = json.loads(path.read_text()) if path.is_file() else {}
    path.write_text(json.dumps({t: old.get(t, "") for t in texts(shot, tl)}, ensure_ascii=False, indent=1))
    return path


def shown(shot: Shot, tl: Timeline, subs: dict[str, str]) -> list[Shown]:
    out: list[Shown] = []
    if shot.hook:
        out.append(Shown("hook", subs[shot.hook], 0.0, min(HOOK_S, tl.cut_duration)))
    for c in tl.captions:
        start = max(tl.to_cut(c.start), HOOK_S if shot.hook else 0.0)
        out.append(Shown("caption", subs[c.text], start, tl.to_cut(c.end)))
    for line in tl.lines:
        start, stop = tl.to_cut(line.start), tl.to_cut(line.end)
        name = "" if line.hero else subs.get(line.name, line.name)
        out.append(Shown("line", subs[line.text], start, max(stop, start + 0.6), name, line.hero))
    return out


def subtitles(shot: Shot, take: Path, lang: str) -> Path:
    """The take's texts in `lang` as a caption track over its Russian cut: <take>/subs.<lang>.srt. A line
    says whose it is (no colours there); the hook and the captions, burnt in in Russian, go in brackets."""
    events = take / "events.jsonl"
    if not events.is_file():
        raise CutError(f"the take has no {events.name}")
    tl = build(read_events(events))
    subs = load_subs(take, lang, texts(shot, tl))
    cues = []
    for s in shown(shot, tl, subs):
        text = s.text if s.kind == "line" else f"[{s.text}]"
        cues.append(srt.Cue(s.start, s.end, f"{s.name}: {text}" if s.name else text))
    path = take / f"subs.{lang}.srt"
    path.write_text(srt.render(cues))
    return path


def cut(shot: Shot, take: Path, lang: str = "ru", out: Path | None = None) -> Path:
    events = take / "events.jsonl"
    video, audio = take / "main.mp4", take / "main.wav"
    for p in (events, video, audio):
        if not p.is_file():
            raise CutError(f"the take has no {p.name}: was the segment recorded as `rec main`?")
    tl = build(read_events(events))
    subs = load_subs(take, lang, texts(shot, tl))
    out = out or take / f"short-{lang}.mp4"
    work = take / f"cut-{lang}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()

    overlays = _overlays(shown(shot, tl, subs), work)
    keep = tl.keep()
    hero = [line for line in tl.lines if line.hero and line.wav and Path(line.wav).is_file()]

    inputs = ["-i", str(video), "-i", str(audio)]
    for line in hero:
        inputs += ["-i", line.wav]
    first_png = 2 + len(hero)
    for o in overlays:
        inputs += ["-i", str(o.png)]

    f: list[str] = []
    # the game's sound and the hero's voice, on the recording's own time
    mix = ["[1:a]aformat=sample_rates=48000:channel_layouts=stereo[g]"]
    for i, line in enumerate(hero):
        ms = int(line.start * 1000)
        mix.append(f"[{2 + i}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms}[h{i}]")
    labels = "[g]" + "".join(f"[h{i}]" for i in range(len(hero)))
    mix.append(f"{labels}amix=inputs={1 + len(hero)}:normalize=0:duration=first[mixed]")
    f += mix
    # the parts that stay
    f.append(f"[0:v]split={len(keep)}" + "".join(f"[vs{i}]" for i in range(len(keep))))
    f.append("[mixed]asplit=" + str(len(keep)) + "".join(f"[as{i}]" for i in range(len(keep))))
    for i, (a, b) in enumerate(keep):
        fade = min(0.02, (b - a) / 4)
        f.append(f"[vs{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        f.append(
            f"[as{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={fade:.3f},afade=t=out:st={max(0.0, b - a - fade):.3f}:d={fade:.3f}[a{i}]"
        )
    f.append("".join(f"[v{i}][a{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=1:a=1[vc][ac]")
    f.append(f"[vc]scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H}[v]")
    last = "v"
    for i, o in enumerate(overlays):
        f.append(
            f"[{last}][{first_png + i}:v]overlay=x=0:y={o.y}:"
            f"enable='between(t,{o.start:.3f},{o.end:.3f})'[o{i}]"
        )
        last = f"o{i}"
    f.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs,
        "-filter_complex", ";".join(f), "-map", f"[{last}]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out),
    ]  # fmt: skip
    (work / "ffmpeg.txt").write_text(" ".join(cmd))
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise CutError(f"ffmpeg failed:\n{done.stderr.strip()[-3000:]}")
    _sheet(out, out.with_suffix(".png"), tl.cut_duration)
    _transcript(shot, tl, subs, out.with_suffix(".md"))
    return out


def _overlays(on_screen: list[Shown], work: Path) -> list[Overlay]:
    out: list[Overlay] = []
    for i, s in enumerate(on_screen):
        png = work / f"{s.kind}{i}.png"
        if s.kind == "hook":
            h = _text_png(s.text, png, HOOK_FONT, 84, (255, 255, 255), max_chars=18, stroke=7)
            y = 300 - h // 2
        elif s.kind == "caption":
            h = _text_png(s.text, png, HOOK_FONT, 64, HERO_COLOR, max_chars=22, stroke=6)
            y = 300 - h // 2
        else:
            color = HERO_COLOR if s.hero else NPC_COLOR
            h = _text_png(s.text, png, SUB_FONT, 62, color, max_chars=26, name=s.name)
            y = 1290 - h // 2
        out.append(Overlay(png, s.start, s.end, y))
    return out


def _text_png(
    text: str,
    path: Path,
    font_file: str,
    size: int,
    color: tuple[int, int, int],
    *,
    max_chars: int,
    stroke: int = 6,
    name: str = "",
) -> int:
    """The text centered on a transparent strip as wide as the frame; returns the strip's height."""
    font = ImageFont.truetype(font_file, size)
    name_font = ImageFont.truetype(font_file, int(size * 0.62))
    rows = textwrap.wrap(text, max_chars) or [""]
    gap = int(size * 0.18)
    heights = [font.getbbox(r, stroke_width=stroke)[3] for r in rows]
    name_h = name_font.getbbox(name, stroke_width=stroke)[3] + gap if name else 0
    height = name_h + sum(heights) + gap * (len(rows) - 1) + stroke * 2
    img = Image.new("RGBA", (W, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = stroke
    if name:
        draw.text((W // 2, y), name, font=name_font, fill=NAME_COLOR, anchor="ma",
                  stroke_width=stroke, stroke_fill=(0, 0, 0))  # fmt: skip
        y += name_h
    for r, h in zip(rows, heights, strict=True):
        draw.text((W // 2, y), r, font=font, fill=color, anchor="ma",
                  stroke_width=stroke, stroke_fill=(0, 0, 0))  # fmt: skip
        y += h + gap
    img.save(path)
    return height


def _sheet(video: Path, png: Path, duration: float, n: int = 12) -> None:
    """Frames over the whole short in one image, to look at it without playing it."""
    step = max(duration / n, 0.1)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
         "-vf", f"fps=1/{step:.3f},scale=270:480,tile=6x{(n + 5) // 6}", "-frames:v", "1", str(png)],
        check=False,
    )  # fmt: skip


def _transcript(shot: Shot, tl: Timeline, subs: dict[str, str], path: Path) -> None:
    rows = [f"# {shot.title}", "", f"Длина {tl.cut_duration:.1f} с (запись {tl.duration:.1f} с, вырезано "
            f"{tl.duration - tl.cut_duration:.1f} с пауз).", ""]  # fmt: skip
    if shot.hook:
        rows += [f"Зацепка: {subs[shot.hook]}", ""]
    heard = iter(tl.heard)
    for line in tl.lines:
        who = "Герой" if line.hero else line.name
        rows.append(f"- {tl.to_cut(line.start):5.1f}–{tl.to_cut(line.end):5.1f} **{who}**: {subs[line.text]}")
        if line.hero:
            got = next(heard, None)
            if got is not None and got.strip().lower() != line.text.strip().lower():
                rows.append(f"  - распознано: {got}")
    if tl.timeouts:
        rows += ["", f"Таймаутов ожидания ответа: {tl.timeouts}"]
    path.write_text("\n".join(rows) + "\n")
