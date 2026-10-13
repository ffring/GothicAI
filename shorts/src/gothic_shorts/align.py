"""Word timings of a known text: forced alignment (Qwen3 Forced Aligner on audio.cpp) on the GPU server,
installed there by `scripts/deploy-gpu.sh aligner`. Only ffmpeg runs here: the recording goes to the server
as 16 kHz mono, the server's `align.sh` maps the text onto it, the words come back in seconds.

The server is GOTHIC_GPU_SSH, read like scripts/deploy-gpu.sh reads it: the environment first, then the
nearest .env at or above the repository.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
REMOTE_ALIGN = "gothic-ai/aligner/align.sh"  # relative to the home directory, where ssh starts
RATE = 16000


class AlignError(RuntimeError):
    pass


def setting(name: str, start: Path = REPO) -> str:
    """A setting: the environment first, then the first NAME=value line of the nearest .env."""
    if value := os.environ.get(name):
        return value
    for d in (start, *start.parents):
        env = d / ".env"
        if env.is_file():
            pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(name)}=(.*)$")
            for line in env.read_text().splitlines():
                if m := pattern.match(line):
                    return m.group(1).strip().strip("\"'")
            return ""
    return ""


def _clean(token: str) -> str:
    # what the aligner keeps of a word: letters, digits and the apostrophe
    return "".join(c for c in token if c.isalnum() or c == "'")


def normalise(raw: object, text: str, rate: int = RATE) -> dict:
    """audio.cpp's word list ([{start_sample, end_sample, word}], or {"words": [...]}, or times in seconds)
    as {"text", "words": [{"text", "start", "end"}]} in seconds. The words keep their punctuation as the
    text has it ("Ксардас," rather than "Ксардас") when the aligner's words match the text's one to one."""
    items = raw.get("words", []) if isinstance(raw, dict) else raw
    if isinstance(raw, dict):
        rate = int(raw.get("sample_rate", rate))
    if not isinstance(items, list):
        raise AlignError(f"the aligner wrote no word list: {str(raw)[:200]}")
    words = []
    for item in items:
        word = str(item.get("word", item.get("text", "")))
        if "start_sample" in item:
            start, end = item["start_sample"] / rate, item["end_sample"] / rate
        elif "start_ms" in item:
            start, end = item["start_ms"] / 1000, item["end_ms"] / 1000
        else:
            start, end = float(item["start"]), float(item["end"])
        words.append({"text": word, "start": round(start, 3), "end": round(end, 3)})
    written = [t for t in text.split() if _clean(t)]
    if len(written) == len(words) and all(
        _clean(t).lower() == _clean(w["text"]).lower() for t, w in zip(written, words, strict=True)
    ):
        for t, w in zip(written, words, strict=True):
            w["text"] = t
    return {"text": text, "words": words}


def _run(argv: list[str], what: str, stdin: str | None = None) -> str:
    done = subprocess.run(argv, input=stdin, capture_output=True, text=True)
    if done.returncode != 0:
        raise AlignError(f"{what} failed:\n{(done.stderr or done.stdout).strip()[-2000:]}")
    return done.stdout


def _seconds(wav: Path) -> float:
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / w.getframerate()


def align(wav: Path, text: str, out: Path, language: str = "Russian", dest: str | None = None) -> dict:
    """Aligns `text` (exactly what is said) to `wav`, writes `out` and returns what it wrote, plus (not in
    the file) "ssh_s", the seconds the aligner's run took over ssh, and "metrics", audio.cpp's own timing."""
    text = " ".join(text.split())
    if not text:
        raise AlignError("no text to align")
    if not wav.is_file():
        raise AlignError(f"no recording {wav}")
    if shutil.which("ffmpeg") is None:
        raise AlignError("ffmpeg is not installed (brew install ffmpeg)")
    dest = dest or setting("GOTHIC_GPU_SSH")
    if not dest:
        raise AlignError("GOTHIC_GPU_SSH is not set: the GPU server's ssh destination (environment or .env)")
    ssh = ["ssh", "-o", "BatchMode=yes", dest]
    scp = ["scp", "-q", "-o", "BatchMode=yes"]
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp)
        audio, words, script = local / "audio.wav", local / "words.json", local / "text.txt"
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
              "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(audio)], "ffmpeg")  # fmt: skip
        script.write_text(text + "\n")
        remote = _run([*ssh, "mktemp -d /tmp/gothic-align.XXXXXX"], "ssh mktemp").strip()
        try:
            _run([*scp, str(audio), str(script), f"{dest}:{remote}/"], "scp to the GPU server")
            q = shlex.quote
            began = time.monotonic()
            log = _run([*ssh, f"test -x {REMOTE_ALIGN} || {{ echo 'no ~/{REMOTE_ALIGN}: run "
                              f"scripts/deploy-gpu.sh aligner' >&2; exit 1; }}; {REMOTE_ALIGN} "
                              f"{q(remote + '/audio.wav')} {q(remote + '/text.txt')} {q(language)} "
                              f"{q(remote + '/words.json')}"], "the aligner")  # fmt: skip
            took = time.monotonic() - began
            _run([*scp, f"{dest}:{remote}/words.json", str(words)], "scp from the GPU server")
        finally:
            subprocess.run([*ssh, f"rm -rf {shlex.quote(remote)}"], capture_output=True)
        result = normalise(json.loads(words.read_text()), text)
        result["duration"] = round(_seconds(audio), 3)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return {**result, "ssh_s": round(took, 2), "metrics": _metrics(log)}


def _metrics(log: str) -> str:
    """audio.cpp's --metrics line (wall time and real-time factor of the run), if it printed one."""
    lines = [line.strip() for line in log.splitlines() if re.search(r"wall|rtf", line, re.I)]
    return "; ".join(lines)
