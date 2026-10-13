"""What the cut needs from a take, in seconds of the recording: who said what when, the pauses while the
AI thinks (cut down to a beat; after a sound said at once, "Хм...", too), the captions. Pure: events in,
timeline out."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

FPS = 30
MAX_GAP_S = 0.8  # a pause before an npc's answer longer than this is cut ...
KEEP_GAP_S = 0.45  # ... down to this


@dataclass(frozen=True)
class Line:
    who: str  # "hero" or the npc's symbol
    name: str
    text: str
    start: float
    end: float
    wav: str | None = None  # the hero's recording
    filler: bool = False  # a sound the npc makes at once while the model thinks ("Хм...")

    @property
    def hero(self) -> bool:
        return self.who == "hero"


@dataclass(frozen=True)
class Caption:
    text: str
    start: float
    end: float


@dataclass
class Timeline:
    duration: float  # of the recording
    lines: list[Line] = field(default_factory=list)
    captions: list[Caption] = field(default_factory=list)
    heard: list[str] = field(default_factory=list)  # what speech recognition made of the hero's lines
    timeouts: int = 0
    cuts: list[tuple[float, float]] = field(default_factory=list)  # removed from the recording

    def keep(self) -> list[tuple[float, float]]:
        """The parts of the recording that stay, in order."""
        out, at = [], 0.0
        for a, b in self.cuts:
            if a > at:
                out.append((at, a))
            at = b
        if at < self.duration:
            out.append((at, self.duration))
        return out

    def to_cut(self, t: float) -> float:
        """A time of the recording in the cut (a time inside a removed part goes to where it was)."""
        removed = 0.0
        for a, b in self.cuts:
            if t >= b:
                removed += b - a
            elif t > a:
                removed += t - a
        return t - removed

    @property
    def cut_duration(self) -> float:
        return self.to_cut(self.duration)


def read_events(path: Path, segment: str = "main") -> list[dict]:
    events = []
    for raw in path.read_text().splitlines():
        if raw.strip():
            e = json.loads(raw)
            if e.get("seg") == segment and e.get("frame", -1) >= 0:
                events.append(e)
    return events


def build(events: list[dict], *, max_gap: float = MAX_GAP_S, keep_gap: float = KEEP_GAP_S) -> Timeline:
    def at(e: dict) -> float:
        return float(e["frame"]) / FPS

    stop = next((e for e in events if e["kind"] == "rec_stop"), None)
    duration = float(stop["frames"]) / FPS if stop else max((at(e) for e in events), default=0.0)
    tl = Timeline(duration)

    for i, e in enumerate(events):
        match e["kind"]:
            case "hero":
                start = at(e)
                end = start + e["ms"] / 1000
                tl.lines.append(Line("hero", "", e.get("text", ""), start, end, e.get("wav")))
            case "npc_line":
                start = at(e)
                end = start + e.get("ms", 0) / 1000
                # the voice's real end, when it played out before the next line of the same npc
                for later in events[i + 1 :]:
                    if later.get("npc") != e["npc"]:
                        continue
                    if later["kind"] == "npc_voice_end":
                        end = at(later)
                        break
                    if later["kind"] == "npc_line":
                        end = min(end, at(later))
                        break
                name = e.get("name", e["npc"])
                filler = bool(e.get("filler"))
                tl.lines.append(
                    Line(e["npc"], name, e.get("text", ""), start, min(end, duration), filler=filler)
                )
            case "caption":
                if tl.captions and tl.captions[-1].end >= duration:
                    last = tl.captions[-1]
                    tl.captions[-1] = Caption(last.text, last.start, at(e))
                if e.get("text"):
                    tl.captions.append(Caption(e["text"], at(e), duration))
            case "heard":
                tl.heard.append(e.get("text", ""))
            case "timeout":
                tl.timeouts += 1
    tl.lines.sort(key=lambda line: line.start)

    def wait(after: Line) -> Line | None:
        """Cut the wait from the end of `after` to the next npc line down to a beat, when it is long and
        nothing else happens in it; that line."""
        answer = next((n for n in tl.lines if not n.hero and n is not after and n.start >= after.end), None)
        if answer is None:
            return None
        others = [n for n in tl.lines if n is not after and after.end < n.start < answer.start]
        if answer.start - after.end > max_gap and not others:
            tl.cuts.append((after.end + keep_gap / 2, answer.start - keep_gap / 2))
        return answer

    # the AI thinking: from the end of the hero's phrase to the first npc line after it; when that line is a
    # sound said at once ("Хм..."), the sound stays where it was and the model's wait after it is cut
    for line in tl.lines:
        if line.hero and (first := wait(line)) is not None and first.filler:
            wait(first)
    tl.cuts.sort()
    return tl
