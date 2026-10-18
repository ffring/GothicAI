"""A final line beyond the body still publishes before the CLI reports it."""

import json
import shutil
import subprocess
import sys

import pytest
from test_dub import CONFIG, _assert_publication, _tts_sound

from gothic_shorts import cli, dub, explain
from gothic_shorts.shot import ShotError


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
def test_cli_reports_the_long_final_line_after_real_levels_publication(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "explainer"
    workspace.mkdir()
    source = tmp_path / "stems"
    source.mkdir()
    length = 12.2  # ten seconds of body plus the standard brand ending
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"color=c=black:s=16x16:r=30:d={length}", "-an", "-c:v", "mpeg4",
         str(source / "picture.mp4")], check=True, capture_output=True,
    )
    for name, frequency, duration, filters in [
        ("voice", 1000, 1.7,
         "volume=-12dB,afade=t=in:d=0.06,afade=t=out:st=1.64:d=0.06,"
         f"adelay=8000,apad=whole_dur={length}"),
        ("game", 440, length,
         f"volume=-20dB,afade=t=in:d=0.06,afade=t=out:st={length - 0.06}:d=0.06"),
    ]:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
             f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
             "-af", filters, "-c:a", "pcm_f32le", str(source / f"{name}.wav")],
            check=True, capture_output=True,
        )
    words = [{"text": "ru", "start": 8, "end": 9.7}]
    plan = {
        "lang": "ru", "brand": True,
        "beats": [{"id": "last", "kind": "footage", "src": "footage.mp4", "dur": 10}],
        "lines": [words], "voice": [{"file": "voice.wav", "start": 8}], "gameVolume": 1,
    }
    (workspace / "plan.json").write_text(json.dumps(plan))
    (workspace / "words.ru.json").write_text(json.dumps({"lines": [{"words": words}]}))
    (workspace / "script.en.txt").write_text("long English line\n")

    def render(ws, plan, directory, *, stems=(None, "voice", "game", "music")):
        for stem in stems:
            filename = "picture.mp4" if stem is None else f"{stem}.wav"
            if (source / filename).exists():
                shutil.copyfile(source / filename, directory / filename)

    monkeypatch.setattr(explain, "render", render)
    russian = explain.publish(workspace)
    recording = _tts_sound(workspace / "voice/en/raw/01.wav", seconds=5, frequency=437)
    monkeypatch.setattr(dub, "clone", lambda *args, **kwargs: [recording])

    def no_shot(short):
        raise ShotError("an explainer")

    monkeypatch.setattr(cli, "_shot", no_shot)
    monkeypatch.setattr(cli, "_config", lambda: CONFIG)
    monkeypatch.setattr(cli, "workspace", lambda name: workspace)
    completed = []

    def dub_explainer(*args):
        done = dub.dub_explainer(*args)
        completed.append(done)
        return done

    monkeypatch.setattr(cli, "dub_explainer", dub_explainer)
    monkeypatch.setattr(sys, "argv", ["gothic-shorts", "dub", workspace.name])

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 1
    assert len(completed) == 1
    done, = completed
    assert len(done.too_long) == 1 and "long English line" in done.too_long[0]
    report = _assert_publication(done, russian)
    assert report["speech_windows"] == [[8, 10]]
    output = capsys.readouterr().out
    assert "too long even sped up" in output and "long English line" in output
    assert "voice_mastered" in output and "clicks" in output
