import math
import random
import shutil
import subprocess
import sys
import wave
from array import array
from pathlib import Path
from unittest.mock import Mock

import pytest

from gothic_shorts import clicks


def _decoded(monkeypatch, samples):
    pcm = array("f", samples)
    if sys.byteorder != "little":
        pcm.byteswap()
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout=pcm.tobytes()))
    monkeypatch.setattr(clicks.subprocess, "run", run)
    return run


def test_clean_sine_has_no_clicks(monkeypatch):
    _decoded(monkeypatch, (
        0.3 * math.sin(2 * math.pi * 1000 * index / clicks.SAMPLE_RATE)
        for index in range(clicks.SAMPLE_RATE)
    ))
    assert clicks.detect(Path("sine.wav")) == []


def test_reproducible_noise_has_no_clicks(monkeypatch):
    randomizer = random.Random(42)
    _decoded(monkeypatch, (
        randomizer.gauss(0, 0.08) for _ in range(clicks.SAMPLE_RATE)
    ))
    assert clicks.detect(Path("noise.wav")) == []


def test_single_impulse_reports_its_time(monkeypatch):
    samples = array("f", [0]) * clicks.SAMPLE_RATE
    samples[clicks.SAMPLE_RATE // 2] = 0.5
    _decoded(monkeypatch, samples)
    assert clicks.detect(Path("impulse.wav")) == pytest.approx([0.5], abs=1 / clicks.SAMPLE_RATE)


def test_close_hits_merge_but_separate_clicks_remain(monkeypatch):
    samples = array("f", [0]) * clicks.SAMPLE_RATE
    for at, amplitude in [(0.5, 0.5), (0.504, 0.8), (0.52, 0.5)]:
        samples[round(at * clicks.SAMPLE_RATE)] = amplitude
    _decoded(monkeypatch, samples)
    assert clicks.detect(Path("impulses.wav")) == pytest.approx([0.504, 0.52], abs=1 / clicks.SAMPLE_RATE)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_short_decoded_audio_has_no_clicks(monkeypatch, count):
    _decoded(monkeypatch, [0] * count)
    assert clicks.detect(Path("empty.wav")) == []


def test_decode_uses_mono_48khz_float_audio(monkeypatch):
    run = _decoded(monkeypatch, [])
    path = Path("finished short.mp4")
    assert clicks.detect(path) == []
    run.assert_called_once_with(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
        check=True, capture_output=True,
    )


def test_decode_failure_propagates(monkeypatch):
    monkeypatch.setattr(
        clicks.subprocess, "run", Mock(side_effect=subprocess.CalledProcessError(1, "ffmpeg")),
    )
    with pytest.raises(subprocess.CalledProcessError):
        clicks.detect(Path("broken.mp4"))


def test_detect_decodes_a_real_wave_file(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for the click detector's decode check")
    samples = array("h", [0]) * clicks.SAMPLE_RATE
    samples[clicks.SAMPLE_RATE // 2] = 16_000
    if sys.byteorder != "little":
        samples.byteswap()
    path = tmp_path / "impulse.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(clicks.SAMPLE_RATE)
        output.writeframes(samples.tobytes())
    assert clicks.detect(path) == pytest.approx([0.5], abs=1 / clicks.SAMPLE_RATE)
