"""The voice of an explainer, ready for the cut: the chosen takes cleaned, every word timed, the lines laid
out one after another on the short's timeline.

    voice/takes.json              the chosen take of each line (the studio writes it)
    script.<lang>.said.txt        what was really said, a line per row, when it differs from the script (the
                                  captions follow the voice word for word); else script.<lang>.txt
    voice/gaps.json               optional: {"4": 0.7} seconds of silence after line 4 (default GAP)
 -> voice/clean/03.wav            the take cleaned: low cut, de-esser, gentle compression, the silence at both
                                  ends trimmed to a breath
    voice/clean/03.words.json     its words (gothic-shorts align, on the GPU server)
    words.<lang>.json             {"lines": [{"n", "file", "start", "words": [{"text", "start", "end"}]}]},
                                  seconds of the short

Only ffmpeg runs here; the alignment runs on the GPU server. A line whose take, text and cleaning are the same
as last time is not aligned again.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from .align import align
from .studio import StudioError, script_lines

GAP = 0.35  # seconds between two lines unless voice/gaps.json says otherwise

# speech: no rumble, softer s, a little compression, peaks kept under -1.5 dBFS; the silence at both ends
# trimmed to a breath (80 ms before, 120 ms after). Loudness is set on the final mix, not here.
CLEAN = (
    "highpass=f=80,deesser=i=0.3,"
    "acompressor=threshold=0.1:ratio=3:attack=10:release=150:makeup=2,alimiter=limit=0.84,"
    "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.08,areverse,"
    "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.12,areverse"
)


def _duration(wav: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)],
        capture_output=True, text=True,
    )  # fmt: skip
    return float(out.stdout.strip())


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
        key = hashlib.sha1(f"{raw.name}|{raw.stat().st_mtime_ns}|{text}|{CLEAN}".encode()).hexdigest()
        stamp = clean_dir / f"{n}.key"
        if not (stamp.is_file() and stamp.read_text() == key and words.is_file() and clean.is_file()):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af", CLEAN, "-ar", "48000",
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
