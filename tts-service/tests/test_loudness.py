import numpy as np
import pytest

from gothic_tts.loudness import CEILING_DB, DEFAULT_LUFS, TARGET_LUFS, Leveler, Line, level
from gothic_tts.providers import Voice
from gothic_tts.providers.fake import FakeProvider
from gothic_tts.synthesizer import Synthesizer

RATE = 24000
XARDAS = Voice("xardas", "An old man with a deep voice")


def _speech(seconds: float, peak_db: float, seed: int = 0) -> np.ndarray:
    """Speech-like test audio: a voiced tone in syllables with pauses and a few sharp peaks."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    x = np.sin(2 * np.pi * 140 * t) + 0.5 * np.sin(2 * np.pi * 420 * t)
    x *= np.clip(np.sin(2 * np.pi * 3 * t), 0, None)  # syllables, half of the time silent
    x += 2.5 * (rng.random(len(t)) < 0.0005)  # clicks, plosives: the peaks
    return (x / np.max(np.abs(x)) * 32767 * 10 ** (peak_db / 20)).astype("<i2")


def _run(line: Line, pcm: np.ndarray, chunk: int) -> np.ndarray:
    data = pcm.tobytes()
    out = b"".join(line.process(data[i : i + chunk]) for i in range(0, len(data), chunk))
    return np.frombuffer(out + line.flush(), "<i2")


def test_level_ignores_pauses() -> None:
    tone = _speech(3, -6)
    padded = np.concatenate((np.zeros(RATE * 2, "<i2"), tone, np.zeros(RATE * 2, "<i2")))
    assert level(padded, RATE) == pytest.approx(level(tone, RATE), abs=0.5)
    assert level(np.zeros(RATE, "<i2"), RATE) is None


def test_raised_to_target_without_clipping() -> None:
    quiet = _speech(4, -8)  # peaks well above the level, as in TTS: raising it by 10 dB would clip
    gain = TARGET_LUFS - level(quiet, RATE)
    assert gain > 8
    out = _run(Line(RATE, gain), quiet, 4800)
    assert len(out) == len(quiet)
    assert level(out, RATE) == pytest.approx(TARGET_LUFS, abs=1.0)
    assert np.max(np.abs(out.astype(float))) <= 32768 * 10 ** (CEILING_DB / 20) + 1


def test_the_same_audio_whatever_the_chunks() -> None:
    pcm = _speech(2, -3, seed=1)
    whole = _run(Line(RATE, 10.0), pcm, len(pcm) * 2)
    for chunk in (2, 202, 4800):
        assert np.array_equal(_run(Line(RATE, 10.0), pcm, chunk), whole)


def test_learns_each_voice() -> None:
    leveler = Leveler()
    usual = TARGET_LUFS - DEFAULT_LUFS
    first = leveler.start("inworld", "xardas", RATE)
    assert first.gain_db == pytest.approx(usual)  # a voice not heard yet: the usual TTS level
    _run(first, _speech(3, -16), 4800)
    leveler.learn("inworld", "xardas", first)

    second = leveler.start("inworld", "xardas", RATE)
    out = _run(second, _speech(3, -16, seed=2), 4800)
    assert level(out, RATE) == pytest.approx(TARGET_LUFS, abs=1.0)
    # another voice of the same provider starts from what it knows of the provider
    assert leveler.start("inworld", "diego", RATE).gain_db == pytest.approx(second.gain_db)
    assert leveler.start("voxcpm", "xardas", RATE).gain_db == pytest.approx(usual)


async def test_synthesizer_levels_the_lines() -> None:
    synth = Synthesizer(FakeProvider(), leveler=Leveler())
    for _ in range(2):
        parts: list[bytes] = []

        async def chunk(pcm: bytes, p, parts: list[bytes] = parts) -> None:
            parts.append(pcm)

        s = await synth.speak("Драконы уже здесь, и их много.", XARDAS, chunk)
        out = np.frombuffer(b"".join(parts), "<i2")
        assert len(out) / FakeProvider.rate == pytest.approx(s.audio_s)
    assert level(out, FakeProvider.rate) == pytest.approx(TARGET_LUFS, abs=0.5)
    await synth.aclose()
