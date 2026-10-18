"""The voice of an explainer, ready for the cut: the chosen takes cleaned, every word timed, the lines laid
out one after another on the short's timeline.

    voice/takes.json              the chosen take of each line (the studio writes it)
    script.<lang>.said.txt        what was really said, a line per row, when it differs from the script (the
                                  captions follow the voice word for word); else script.<lang>.txt
    voice/gaps.json               optional: {"4": 0.7} seconds of silence after line 4 (default GAP)
 -> voice/clean/03.wav            the take cleaned: clicks out, low cut, de-esser, gentle compression, the
                                  silence at both ends trimmed to a breath
    voice/clean/03.words.json     its words (gothic-shorts align, on the GPU server)
    words.<lang>.json             {"lines": [{"n", "file", "start", "words": [{"text", "start", "end"}]}]},
                                  seconds of the short

Only ffmpeg runs here; the alignment runs on the GPU server. A line whose take, text and cleaning are the same
as last time is not aligned again.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from .align import align
from .studio import StudioError, script_lines

GAP = 0.35  # seconds between two lines unless voice/gaps.json says otherwise

# speech: no rumble, softer s, a little compression, peaks kept under -1.5 dBFS, a short fade at both ends.
# Loudness is set on the final mix, not here.
CLEAN = (
    "highpass=f=80,deesser=i=0.3,"
    "acompressor=threshold=0.1:ratio=3:attack=10:release=150:makeup=2,alimiter=limit=0.84,"
    "afade=t=in:d=0.01,areverse,afade=t=in:d=0.02,areverse"
)
# a recorded take's clicks out first (mouth clicks, samples the browser dropped; the default leaves some, and
# this is slow: ~40 s a line). Speech from TTS has none.
DECLICK = "adeclick=t=1:b=3,"
# the silence at both ends trimmed to a breath: 80 ms before the first sound over -50 dB,
# 120 ms after the last.
# Cut with atrim: silenceremove left a step in the waveform where it cut (a click 80 ms into most lines).
QUIET, LEAD, TAIL, MIN_SOUND = "-50dB", 0.08, 0.12, 0.08


def _duration(wav: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)],
        capture_output=True, text=True,
    )  # fmt: skip
    return float(out.stdout.strip())


def _trim(raw: Path) -> str:
    """Keep speech with atrim: from LEAD before its first sound over QUIET to TAIL after its last."""
    out = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(raw), "-af", f"silencedetect=n={QUIET}:d=0.05", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr  # fmt: skip
    dur = _duration(raw)
    starts = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", out)]
    ends = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", out)]
    quiet = [(s, ends[i] if i < len(ends) else dur) for i, s in enumerate(starts)]
    # the sounds between the quiet spans; a few ms over QUIET (a click as the recording starts) is not speech
    edges = [0.0] + [t for span in quiet for t in span] + [dur]
    sounds = [(a, b) for a, b in zip(edges[::2], edges[1::2], strict=False) if b - a >= MIN_SOUND]
    if not sounds:  # all of it under QUIET: keep it whole
        sounds = [(0.0, dur)]
    begin, finish = sounds[0][0], sounds[-1][1]
    return f"atrim=start={max(0.0, begin - LEAD):.3f}:end={min(dur, finish + TAIL):.3f},asetpts=PTS-STARTPTS,"


def clean_chain(wav: Path, declick: bool = True) -> str:
    """the cleaning of a line: its speech cut out (LEAD before it), the clicks out (a recorded take), CLEAN"""
    return _trim(wav) + (DECLICK if declick else "") + CLEAN


def said_lines(ws: Path, lang: str) -> list[str]:
    said = ws / f"script.{lang}.said.txt"
    if said.is_file():
        return [row.strip() for row in said.read_text().splitlines() if row.strip()]
    return script_lines(ws, lang)


def prepare(ws: Path, lang: str = "ru", language: str = "Russian", log=print) -> dict:
    texts = said_lines(ws, lang)
    chosen_path = ws / "voice/takes.json"
    chosen = json.loads(chosen_path.read_text()) if chosen_path.is_file() else {}
    missing = [i for i in range(1, len(texts) + 1) if f"{i:02d}" not in chosen]
    if missing:
        lines = ", ".join(map(str, missing))
        raise StudioError(f"no chosen take for line(s) {lines}: record them in the studio")
    gaps_path = ws / "voice/gaps.json"
    gaps = {}
    if gaps_path.is_file():
        gaps = {int(k): float(v) for k, v in json.loads(gaps_path.read_text()).items()}
    clean_dir = ws / "voice/clean"
    clean_dir.mkdir(parents=True, exist_ok=True)

    lines, t = [], 0.0
    for i, text in enumerate(texts, start=1):
        n = f"{i:02d}"
        raw = ws / f"voice/raw/{n}-{chosen[n]}.wav"
        clean, words = clean_dir / f"{n}.wav", clean_dir / f"{n}.words.json"
        chain = clean_chain(raw)
        key = hashlib.sha1(f"{raw.name}|{raw.stat().st_mtime_ns}|{text}|{chain}".encode()).hexdigest()
        stamp = clean_dir / f"{n}.key"
        if not (stamp.is_file() and stamp.read_text() == key and words.is_file() and clean.is_file()):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af", chain, "-ar", "48000",
                            "-c:a", "pcm_s24le", str(clean)], check=True)  # fmt: skip
            done = align(clean, text, words, language)
            stamp.write_text(key)
            log(f"line {i:2d} take {chosen[n]}: {len(done['words'])} words over {done['duration']:.2f} s")
        timed = json.loads(words.read_text())
        line = {
            "n": i,
            "file": f"voice/clean/{n}.wav",
            "start": round(t, 3),
            "words": [
                {"text": w["text"], "start": round(t + w["start"], 3), "end": round(t + w["end"], 3)}
                for w in timed["words"]
            ],
        }
        lines.append(line)
        t += _duration(clean) + gaps.get(i, GAP)
    out = {"lang": lang, "lines": lines, "duration": round(t, 3)}
    (ws / f"words.{lang}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return out
