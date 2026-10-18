"""Explainer publication uses real FFmpeg audio, with Remotion replaced by prepared stems."""

import copy
import json
import shutil
import subprocess
import sys

import pytest

from gothic_shorts import cli, explain, levels, studio


def _rows(report):
    return {row["id"]: row for row in report["rows"]}


def test_speech_windows_use_absolute_line_words_and_merge_clip_to_rounded_body():
    plan = {
        "beats": [{"dur": 4.11}, {"dur": 5.91}],
        "lines": [
            [{"text": "first", "start": 4.2, "end": 4.5},
             {"text": "last", "start": 5.1, "end": 6}],
            [],
            [{"text": "before", "start": -0.5, "end": 0.2},
             {"text": "one", "start": 0.5, "end": 1}],
            [{"text": "overlap", "start": 0.8, "end": 1.5},
             {"text": "last", "start": 2, "end": 2.3}],
            [{"text": "end", "start": 9, "end": 9.4},
             {"text": "after", "start": 9.8, "end": 12}],
            [{"text": "outside", "start": 20, "end": 21}],
        ],
    }
    original = copy.deepcopy(plan)

    windows, total, length = explain.speech_windows(plan)

    assert total == pytest.approx(301 / 30)
    assert length == pytest.approx(301 / 30 + 66 / 30)
    for actual, expected in zip(windows, [(0, 2.3), (4.2, 6), (9, total)], strict=True):
        assert actual == pytest.approx(expected)
    assert plan == original


@pytest.mark.parametrize("brand,ending", [(False, 0), (True, 66 / 30)])
def test_speech_windows_match_remotion_rounding_and_optional_sting(brand, ending):
    # Math.round(4.5) is 5 in Remotion; Python's round(4.5) would give 4.
    windows, total, length = explain.speech_windows({
        "brand": brand, "beats": [{"dur": 0.075}, {"dur": 0.075}], "lines": [[], []],
    })
    assert windows == []
    assert total == pytest.approx(5 / 30)
    assert length == pytest.approx(total + ending)


@pytest.fixture(scope="module")
def synthetic_stems(tmp_path_factory):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for synthetic explainer publication")
    directory = tmp_path_factory.mktemp("explainer-stems")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=16x16:r=30:d=14.2", "-an", "-c:v", "mpeg4",
         str(directory / "picture.mp4")], check=True, capture_output=True,
    )
    for name, frequency, duration, filters in [
        ("voice", 1000, 4,
         "volume=-12dB,afade=t=in:d=0.06,afade=t=out:st=3.94:d=0.06,"
         "adelay=4000,apad=whole_dur=14.2"),
        ("game", 440, 14.2,
         "volume=-20dB,afade=t=in:d=0.06,afade=t=out:st=14.14:d=0.06"),
        ("music", 220, 12,
         "afade=t=in:d=0.5,afade=t=out:st=11.5:d=0.5,apad=whole_dur=14.2"),
    ]:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
             "-af", filters, "-c:a", "pcm_f32le", str(directory / f"{name}.wav")],
            check=True, capture_output=True,
        )
    return directory


def _plan(*, music=True, lang="en"):
    plan = {
        "lang": lang,
        "hook": {"text": "Synthetic explainer", "until": 2},
        "beats": [{"id": "one", "kind": "footage", "src": "footage.mp4", "dur": 12}],
        "lines": [[{"text": "first", "start": 4, "end": 5},
                   {"text": "last", "start": 7, "end": 8}]],
        "voice": [{"file": "voice.wav", "start": 4}],
        # Deliberately unsuitable draft levels must not reach publication.
        "gameVolume": 0,
        "duck": 0,
        "brandVolume": 100,
    }
    if music:
        plan["music"] = {"file": "music.wav", "volume": 100, "duck": 1,
                         "fadeIn": 0.5, "fadeOut": 0.5}
    return plan


def _prepared_render(monkeypatch, synthetic_stems):
    calls = []

    def render(ws, plan, directory):
        calls.append((ws, copy.deepcopy(plan)))
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("picture.mp4", "voice.wav", "game.wav", "music.wav"):
            shutil.copyfile(synthetic_stems / name, directory / name)

    monkeypatch.setattr(explain, "render", render)
    return calls


def _run_cli(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["gothic-shorts", "explain", *map(str, arguments)])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    return exit_info.value.code


@pytest.mark.parametrize("music", [True, False], ids=["music-defaults", "no-music-custom-paths"])
def test_cli_publishes_synthetic_stems_with_shared_levels(
    tmp_path, synthetic_stems, monkeypatch, capsys, music,
):
    ws = tmp_path / "explainers" / "synthetic"
    ws.mkdir(parents=True)
    plan = _plan(music=music)
    plan_file = "plan.json" if music else "plan.other.json"
    (ws / plan_file).write_text(json.dumps(plan))
    calls = _prepared_render(monkeypatch, synthetic_stems)
    published = []
    real_publish = levels.publish

    def publish(joined, bed, voice, speech, total, length, out, **kwargs):
        published.append((joined, bed, voice, speech, total, length, out, kwargs))
        assert joined.is_file()
        assert joined.name == "joined.mkv"
        assert len(voice) == 1 and voice[0][0].name == "voice.wav" and voice[0][1] == 0
        assert speech == [(4, 8)]
        assert total == 12 and length == 14.2
        assert kwargs["music_fades"] is False
        return real_publish(joined, bed, voice, speech, total, length, out, **kwargs)

    monkeypatch.setattr(levels, "publish", publish)
    if music:
        out = ws / "render" / "short-en.mp4"
        arguments = [ws]
    else:
        monkeypatch.setattr(studio, "ROOT", ws.parent)
        out = tmp_path / "custom" / "published.mp4"
        arguments = [ws.name, "--plan", plan_file, "--out", out]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"stale video")
    out.with_suffix(".levels.json").write_text('{"stale": true}')

    assert _run_cli(monkeypatch, *arguments) == 0

    assert calls == [(ws, plan)]
    assert len(published) == 1
    assert (published[0][1] is not None) is music
    assert published[0][6] == out
    assert out.stat().st_size > 0
    report = json.loads(out.with_suffix(".levels.json").read_text())
    assert report["ok"] is True
    assert report["balance_stage"] == "mastered_aac"
    assert "error" not in report and "stale" not in report
    rows = _rows(report)
    assert rows["voice_mastered"]["measured_lufs"] == pytest.approx(-14, abs=0.5)
    assert rows["true_peak"]["measured_dbtp"] <= -1
    assert rows["clicks"]["count"] == 0
    assert all(row["ok"] is not False for row in report["rows"])
    assert rows["ending"]["measured_lufs"] <= rows["voice_mastered"]["measured_lufs"] - 3
    if not music:
        for name in ("music_pause", "music_speech", "music_below_game"):
            assert rows[name]["measured_lufs"] is None
            assert rows[name]["ok"] is None
    output = capsys.readouterr().out
    assert all(track in output for track in ("voice", "music_pause", "game_speech", "true_peak", "clicks"))


def test_cli_rejects_loud_music_removes_stale_video_and_writes_failed_report(
    tmp_path, synthetic_stems, monkeypatch, capsys,
):
    ws = tmp_path / "synthetic"
    ws.mkdir()
    plan = _plan()
    (ws / "plan.json").write_text(json.dumps(plan))
    calls = _prepared_render(monkeypatch, synthetic_stems)
    out = ws / "render" / "short-en.mp4"
    out.parent.mkdir()
    out.write_bytes(b"stale video must be removed")
    report_path = out.with_suffix(".levels.json")
    report_path.write_text('{"stale": true}')
    real_music_gain = levels.music_gain
    # Raw music loudness is calibrated away. Make the real calibration choose a
    # bad coefficient, so the real measurements and publication gate reject it.
    monkeypatch.setattr(levels, "music_gain", lambda *args: real_music_gain(*args) + 12)

    assert _run_cli(monkeypatch, ws) == 1

    assert calls == [(ws, plan)]
    assert not out.exists()
    report = json.loads(report_path.read_text())
    assert report["ok"] is False
    assert report["error"]
    assert "stale" not in report
    rows = _rows(report)
    assert rows["music_pause"]["ok"] is False
    assert rows["music_speech"]["ok"] is False
    assert "error:" in capsys.readouterr().err
