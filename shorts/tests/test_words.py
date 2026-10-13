import json
import shutil
import subprocess

import pytest

from gothic_shorts import words
from gothic_shorts.studio import StudioError

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def _take(path, silence=1.0, tone=1.5, tail=0.5):
    """A take: silence, a tone at -12 dBFS (a voice stands in as a tone), silence."""
    expr = f"if(between(t,{silence},{silence + tone}),0.25*sin(2*PI*220*t),0)"
    src = f"aevalsrc='{expr}':s=48000:d={silence + tone + tail}"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-c:a", "pcm_s24le", str(path)],
                   check=True)  # fmt: skip


def _short(tmp_path):
    (tmp_path / "script.ru.txt").write_text("Первая фраза.\nВторая фраза.\n")
    raw = tmp_path / "voice/raw"
    raw.mkdir(parents=True)
    _take(raw / "01-1.wav")
    _take(raw / "02-2.wav", tone=1.0)
    (tmp_path / "voice/takes.json").write_text(json.dumps({"01": 1, "02": 2}))
    return tmp_path


@pytest.fixture
def aligned(monkeypatch):
    """The GPU server's aligner stood in for: each word 0.4 s long from the start of the recording."""
    calls = []

    def fake(wav, text, out, language="Russian"):
        calls.append(text)
        got = {
            "text": text,
            "duration": 0.0,
            "words": [
                {"text": w, "start": 0.4 * i, "end": 0.4 * i + 0.4} for i, w in enumerate(text.split())
            ],
        }
        out.write_text(json.dumps(got, ensure_ascii=False))
        return got

    monkeypatch.setattr(words, "align", fake)
    return calls


@needs_ffmpeg
def test_the_lines_are_cleaned_and_laid_one_after_another_with_their_gaps(tmp_path, aligned):
    ws = _short(tmp_path)
    (ws / "voice/gaps.json").write_text(json.dumps({"1": 0.7}))
    out = words.prepare(ws, log=lambda *_: None)

    first, second = out["lines"]
    clean = words._duration(ws / "voice/clean/01.wav")
    assert 1.5 < clean < 1.8  # the silence at both ends trimmed to a breath
    assert first["start"] == 0 and first["file"] == "voice/clean/01.wav"
    assert second["start"] == pytest.approx(clean + 0.7, abs=0.002)
    assert second["words"][0] == {
        "text": "Вторая",
        "start": second["start"],
        "end": round(second["start"] + 0.4, 3),
    }
    assert json.loads((ws / "words.ru.json").read_text()) == out


@needs_ffmpeg
def test_the_captions_follow_what_was_said_and_unchanged_lines_are_not_aligned_again(tmp_path, aligned):
    ws = _short(tmp_path)
    words.prepare(ws, log=lambda *_: None)
    assert aligned == ["Первая фраза.", "Вторая фраза."]

    (ws / "script.ru.said.txt").write_text("Первая фраза.\nВторая, другая фраза.\n")
    out = words.prepare(ws, log=lambda *_: None)
    assert aligned[2:] == ["Вторая, другая фраза."]  # only the line whose text changed
    assert [w["text"] for w in out["lines"][1]["words"]] == ["Вторая,", "другая", "фраза."]


def test_a_line_without_a_chosen_take_is_an_error(tmp_path):
    (tmp_path / "script.ru.txt").write_text("Первая фраза.\nВторая фраза.\n")
    (tmp_path / "voice").mkdir()
    (tmp_path / "voice/takes.json").write_text(json.dumps({"01": 1}))
    with pytest.raises(StudioError, match="line\\(s\\) 2"):
        words.prepare(tmp_path)
