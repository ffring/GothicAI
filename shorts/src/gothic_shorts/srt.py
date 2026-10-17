"""Subtitle tracks (SubRip) for YouTube: the short's texts in another language, on the short's own time, so
a viewer can turn them on over the Russian video. Pure: cues in, text out."""

from __future__ import annotations

from dataclasses import dataclass

CHUNK = "|"  # an explainer's script marks how its captions break a line: "In Gothic | you don't aim"


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def timestamp(t: float) -> str:
    ms = max(0, round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render(cues: list[Cue]) -> str:
    rows: list[str] = []
    for i, c in enumerate(sorted(cues, key=lambda c: c.start), 1):
        rows += [str(i), f"{timestamp(c.start)} --> {timestamp(c.end)}", c.text.strip(), ""]
    return "\n".join(rows)


def explainer(words: dict, lines: list[str]) -> list[Cue]:
    """An explainer's cues: each line of the translated script over the time its Russian line is said
    (words.<lang>.json), its `|` chunks one after another, each as long as its share of the letters."""
    said = words["lines"]
    if len(said) != len(lines):
        raise ValueError(f"{len(lines)} translated lines for {len(said)} said ones")
    out: list[Cue] = []
    for line, text in zip(said, lines, strict=True):
        a, z = line["words"][0]["start"], line["words"][-1]["end"]
        chunks = [c.strip() for c in text.split(CHUNK) if c.strip()]
        total = sum(len(c) for c in chunks) or 1
        at = a
        for c in chunks:
            end = at + (z - a) * len(c) / total
            out.append(Cue(round(at, 3), round(end, 3), c))
            at = end
    return out
