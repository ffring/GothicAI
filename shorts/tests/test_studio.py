import json
import shutil
import subprocess

import pytest

from gothic_shorts.studio import Studio, StudioError, planned, script_lines

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def _short(tmp_path, plan=True):
    (tmp_path / "script.ru.txt").write_text("Первая фраза.\n\nВторая фраза, длиннее.\n")
    if plan:
        lines = [[{"text": "Первая", "start": 0.3, "end": 0.8}, {"text": "фраза.", "start": 0.8, "end": 1.4}]]
        (tmp_path / "plan.storyboard.json").write_text(json.dumps({"lines": lines}))
    return tmp_path


def _tone(path, silence=1.0, tone=1.5, tail=0.5):
    """A take: silence, a tone at -12 dBFS, silence (a voice stands in as a tone)."""
    total = silence + tone + tail
    expr = f"if(between(t,{silence},{silence + tone}),0.25*sin(2*PI*220*t),0)"
    src = f"aevalsrc='{expr}':s=44100:d={total}"  # quoted: the commas are the expression's, not the graph's
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-c:a", "pcm_f32le", str(path)],
                   check=True)  # fmt: skip
    return path.read_bytes()


def test_the_script_skips_blank_rows_and_the_plan_places_the_lines_it_has(tmp_path):
    ws = _short(tmp_path)
    assert script_lines(ws, "ru") == ["Первая фраза.", "Вторая фраза, длиннее."]
    assert planned(ws, 2) == [(0.3, 1.4), None]
    with pytest.raises(StudioError):
        script_lines(ws, "en")


def test_a_short_without_a_plan_has_no_places(tmp_path):
    ws = _short(tmp_path, plan=False)
    assert planned(ws, 2) == [None, None]


@needs_ffmpeg
def test_a_take_is_kept_as_48k_24bit_measured_and_chosen(tmp_path):
    studio = Studio(_short(tmp_path))
    take = studio.add_take(1, _tone(tmp_path / "in.wav"), video_from=0.0)
    wav = tmp_path / "voice/raw/01-1.wav"
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=sample_rate,channels,codec_name", "-of", "csv=p=0", str(wav)],
                           capture_output=True, text=True).stdout  # fmt: skip
    assert probe.strip() == "pcm_s24le,48000,1"
    assert take["k"] == 1 and take["video_from"] == 0.0
    assert take["peak"] == pytest.approx(-12, abs=0.5)
    assert take["speech"][0] == pytest.approx(1.0, abs=0.05)
    assert take["speech"][1] == pytest.approx(2.5, abs=0.05)
    assert studio.session()["lines"][0]["chosen"] == 1


@needs_ffmpeg
def test_a_discarded_take_goes_to_the_trash_and_its_number_is_not_reused(tmp_path):
    studio = Studio(_short(tmp_path))
    body = _tone(tmp_path / "in.wav")
    studio.add_take(2, body, None)
    studio.add_take(2, body, None)
    studio.choose(2, 1)
    studio.discard(2, 2)
    assert (tmp_path / "voice/trash/02-2.wav").is_file()
    assert [t["k"] for t in studio.takes(2)] == [1]
    assert studio.add_take(2, body, None)["k"] == 3  # 2 is in the trash: never two files of one name
    studio.discard(2, 3)  # the chosen one goes: the newest left is chosen
    assert studio.session()["lines"][1]["chosen"] == 1
    with pytest.raises(StudioError):
        studio.choose(2, 2)
