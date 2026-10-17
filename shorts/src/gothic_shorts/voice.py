"""The hero's lines as recordings: the TTS service (tts-service/, `gothic-tts say`) with a voice designed
from a description, never cloned. Recordings are kept by voice and text, so a line is voiced once and
every take of a shot says exactly the same audio. The dub (dub.py) voices its English lines the same way."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path

from gothic_launcher.commands import child_env, tools
from gothic_launcher.settings import Config

VOICE_ID = "SHORTS_HERO"
LEAD_IN_S = 0.25  # silence before the words: speech recognition missed the first sound without it


class VoiceError(RuntimeError):
    pass


def recording(cache: Path, description: str, text: str) -> Path:
    key = hashlib.sha1(f"{description}\n{text}\n{LEAD_IN_S}".encode()).hexdigest()[:16]
    return cache / f"hero-{key}.wav"


def voice_lines(config: Config, cache: Path, description: str, lines: list[str]) -> dict[str, Path]:
    """Every line's recording; the missing ones are made in one `gothic-tts say`."""
    cache.mkdir(parents=True, exist_ok=True)
    out = {text: recording(cache, description, text) for text in lines}
    missing = {t: p for t, p in out.items() if not p.is_file()}
    if missing:
        say(config, VOICE_ID, description, missing, keep=_lead_in)
    return out


def say(
    config: Config,
    voice_id: str,
    description: str,
    lines: dict[str, Path],
    *,
    provider: str | None = None,
    fallback: str | None = None,
    clone: Path | None = None,
    keep: Callable[[Path, Path], object] = shutil.copyfile,
) -> None:
    """Voices each text into its file, in one `gothic-tts say`: the voice `voice_id` designed from
    `description` (or cloned from the recording `clone`), by the launcher's providers unless
    `provider`/`fallback` name others."""
    texts = list(dict.fromkeys(lines))
    if not texts:
        return
    repo, uv = tools(config)
    s, keys = config.settings, config.secrets
    env: dict[str, str] = {}
    if keys.inworld_api_key:
        env["INWORLD_API_KEY"] = keys.inworld_api_key
    if keys.openai_api_key:
        env["OPENAI_API_KEY"] = keys.openai_api_key
    if s.tts.voxcpm_url:
        env["GOTHIC_VOXCPM_URL"] = s.tts.voxcpm_url
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "line.wav"
        argv = [
            str(uv), "run", "--project", str(repo / "tts-service"), "gothic-tts", "say",
            "--provider", provider or s.tts.provider, "--fallback", fallback or s.tts.fallback,
            "--voice-id", voice_id, "--voice", description, "--out", str(target),
            *(["--clone", str(clone.resolve())] if clone else []), *texts,
        ]  # fmt: skip
        done = subprocess.run(argv, cwd=tmp, env=child_env(env), capture_output=True, text=True)
        print(done.stdout.strip())
        if done.returncode != 0:
            raise VoiceError(f"gothic-tts say failed:\n{done.stderr.strip()[-2000:]}")
        for i, text in enumerate(texts):
            made = target if len(texts) == 1 else target.with_name(f"line_{i}.wav")
            if not made.is_file():
                raise VoiceError(f"gothic-tts made no {made.name} for: {text}")
            lines[text].parent.mkdir(parents=True, exist_ok=True)
            keep(made, lines[text])


def _lead_in(src: Path, dst: Path) -> None:
    with wave.open(str(src), "rb") as r:
        params, pcm = r.getparams(), r.readframes(r.getnframes())
    silence = bytes(int(LEAD_IN_S * params.framerate) * params.sampwidth * params.nchannels)
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        w.writeframes(silence + pcm)
