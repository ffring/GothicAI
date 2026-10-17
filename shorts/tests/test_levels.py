import importlib.util
import json
import math
import shutil
import subprocess
from array import array
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gothic_shorts import levels


@pytest.fixture(scope="module")
def tone_inputs():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for synthetic loudness measurements")
    return ["-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=12"]


def _tone_level(inputs, gain_db=0, *, windows=None, extra="anull"):
    return levels.measure(
        inputs, f"[0:a]volume={gain_db}dB,{extra}[track]", "track", windows=windows,
    )


def _rows(report):
    return {row["id"]: row for row in report["rows"]}


def test_speech_windows_use_words_beat_starts_and_joined_timeline():
    plan = {
        "beats": [
            {
                "start": 10, "end": 16,
                "lines": [
                    {"at": 10, "words": [["first", 0.4, 0.8], ["last", 1.2, 2]]},
                    {"at": 11, "words": [["overlap", 0.5, 1], ["last", 1, 2]]},
                    {"at": 8, "words": [["before", 0, 1], ["last", 1, 3]]},
                    {"at": 15, "words": [["end", 0, 0.4], ["after", 1, 2]]},
                ],
            },
            {
                "start": 20, "end": 24,
                "lines": [{"at": 21, "words": [["next", 0.2, 0.6], ["last", 1, 1.5]]}],
            },
        ],
    }
    windows, duration = levels.speech_windows(plan)
    assert duration == 10
    for actual, expected in zip(windows, [(0, 3), (5, 7), (7.2, 8.5)], strict=True):
        assert actual == pytest.approx(expected)
    pauses = levels.pause_windows(windows, duration)
    for actual, expected in zip(pauses, [(3, 5), (7, 7.2), (8.5, 10)], strict=True):
        assert actual == pytest.approx(expected)


def test_empty_speech_and_full_speech_have_complementary_pauses():
    windows, duration = levels.speech_windows({"beats": [{"start": 2, "end": 7}]})
    assert windows == []
    assert duration == 5
    assert levels.pause_windows(windows, duration) == [(0, 5)]
    assert levels.pause_windows([(0, 5)], duration) == []


@pytest.mark.parametrize("voice_gain,music_gain", [(-4, 0), (-10, -12)])
def test_music_calibration_reaches_voice_minus_fourteen_lu(tone_inputs, voice_gain, music_gain):
    pauses = [(1, 3), (9, 11)]
    speech = _tone_level(tone_inputs, voice_gain)
    original = _tone_level(tone_inputs, music_gain, windows=pauses)
    correction = levels.music_gain(speech, original)
    calibrated = _tone_level(tone_inputs, music_gain + correction, windows=pauses)
    assert math.isfinite(speech)
    assert correction == pytest.approx(speech - levels.MUSIC_BELOW_VOICE_LU - original)
    assert calibrated == pytest.approx(speech - 14, abs=0.5)


@pytest.mark.parametrize("game_gain", [-2, -24])
def test_music_calibration_respects_actual_game_pauses(tone_inputs, game_gain):
    pauses = [(1, 3), (9, 11)]
    speech = _tone_level(tone_inputs)
    game = _tone_level(tone_inputs, game_gain, windows=pauses)
    original = _tone_level(tone_inputs, windows=pauses)
    correction = levels.music_gain(speech, original, game)
    target = min(speech - 14, game - 3)
    assert correction == pytest.approx(target - original)
    assert _tone_level(tone_inputs, correction, windows=pauses) == pytest.approx(target, abs=0.5)


def test_music_duck_is_six_db_and_leaves_distant_pauses_unchanged(tone_inputs):
    speech = [(4, 8)]
    pauses = [(1, 3), (9, 11)]
    envelope = levels.duck_filter(speech, levels.MUSIC_DUCK_DB)
    original_speech = _tone_level(tone_inputs, windows=speech)
    original_pauses = _tone_level(tone_inputs, windows=pauses)
    ducked_speech = _tone_level(tone_inputs, windows=speech, extra=envelope)
    ducked_pauses = _tone_level(tone_inputs, windows=pauses, extra=envelope)
    assert ducked_speech - original_speech == pytest.approx(-6, abs=0.2)
    assert ducked_pauses == pytest.approx(original_pauses, abs=0.1)


def test_duck_ramps_smoothly_before_and_after_the_word_window(tone_inputs):
    envelope = levels.duck_filter([(4, 8)], 6)
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *tone_inputs, "-af", envelope,
         "-f", "f32le", "-ac", "1", "-ar", "48000", "-"],
        check=True, capture_output=True,
    )
    samples = array("f")
    samples.frombytes(result.stdout)

    def rms(at):
        part = samples[round((at - 0.01) * 48000):round((at + 0.01) * 48000)]
        return math.sqrt(sum(value * value for value in part) / len(part))

    baseline = rms(3.8)
    floor = baseline * 10 ** (-6 / 20)
    assert rms(4.05) == pytest.approx(floor, rel=0.01)
    assert rms(7.95) == pytest.approx(floor, rel=0.01)
    assert floor < rms(3.925) < baseline
    assert floor < rms(8.075) < baseline
    assert rms(8.2) == pytest.approx(baseline, rel=0.01)


@pytest.fixture(scope="module")
def balanced_tracks(tone_inputs):
    return {
        "speech_lufs": _tone_level(tone_inputs),
        "music_pause_lufs": _tone_level(tone_inputs, -14),
        "music_speech_lufs": _tone_level(tone_inputs, -20),
        "game_speech_lufs": _tone_level(tone_inputs, -6),
        "game_pause_lufs": _tone_level(tone_inputs, -3),
        "ending_lufs": _tone_level(tone_inputs, -3),
    }


def test_balance_accepts_calibrated_synthetic_tracks(balanced_tracks):
    report = levels.check_balance(**balanced_tracks)
    assert report["ok"] is True
    rows = _rows(report)
    assert set(rows) == {
        "voice", "music_pause", "music_speech", "game_speech", "game_pause", "ending",
        "game_below_voice", "music_below_game",
    }
    assert rows["music_pause"]["target_lufs"] == pytest.approx(balanced_tracks["speech_lufs"] - 14)
    assert rows["music_speech"]["target_lufs"] == pytest.approx(balanced_tracks["speech_lufs"] - 20)
    assert rows["game_speech"]["target_lufs"] == pytest.approx(balanced_tracks["speech_lufs"] - 6)
    assert rows["game_pause"]["target_lufs"] == pytest.approx(balanced_tracks["speech_lufs"] - 3)
    assert rows["ending"]["target_lufs"] == pytest.approx(balanced_tracks["speech_lufs"] - 2)
    assert rows["game_below_voice"]["target_lufs"] == pytest.approx(balanced_tracks["speech_lufs"] - 3)
    assert rows["music_below_game"]["target_lufs"] == pytest.approx(balanced_tracks["game_pause_lufs"] - 3)
    assert all({"id", "measured_lufs", "target_lufs", "ok", "target"} <= row.keys()
               for row in report["rows"])


def test_balance_rejects_music_that_is_too_loud(tone_inputs, balanced_tracks):
    tracks = {**balanced_tracks,
              "music_pause_lufs": _tone_level(tone_inputs, -10),
              "music_speech_lufs": _tone_level(tone_inputs, -16)}
    report = levels.check_balance(**tracks)
    assert report["ok"] is False
    assert _rows(report)["music_pause"]["ok"] is False


def test_balance_rejects_music_without_the_full_speech_duck(tone_inputs, balanced_tracks):
    report = levels.check_balance(**{
        **balanced_tracks, "music_speech_lufs": _tone_level(tone_inputs, -16),
    })
    assert report["ok"] is False
    assert _rows(report)["music_pause"]["ok"] is True
    assert _rows(report)["music_speech"]["ok"] is False


def test_balance_rejects_game_audio_that_is_too_loud_during_speech(tone_inputs, balanced_tracks):
    report = levels.check_balance(**{
        **balanced_tracks, "game_speech_lufs": _tone_level(tone_inputs, -2),
    })
    assert report["ok"] is False
    assert _rows(report)["game_speech"]["ok"] is False


def test_balance_rejects_game_audio_that_is_too_loud_in_pauses(tone_inputs, balanced_tracks):
    report = levels.check_balance(**{
        **balanced_tracks, "game_pause_lufs": _tone_level(tone_inputs, 4),
    })
    assert report["ok"] is False
    assert _rows(report)["game_pause"]["ok"] is False
    assert _rows(report)["game_below_voice"]["ok"] is False


def test_balance_rejects_music_above_the_game_ordering_limit(balanced_tracks):
    report = levels.check_balance(**{
        **balanced_tracks, "music_pause_lufs": balanced_tracks["game_pause_lufs"] - 1,
    })
    assert report["ok"] is False
    assert _rows(report)["music_below_game"]["ok"] is False


def test_balance_rejects_an_ending_louder_than_voice_minus_two(balanced_tracks):
    report = levels.check_balance(**{
        **balanced_tracks, "ending_lufs": balanced_tracks["speech_lufs"] - 1,
    })
    assert report["ok"] is False
    assert _rows(report)["ending"]["ok"] is False


def test_unvoiced_balance_only_reports_levels(balanced_tracks):
    report = levels.check_balance(**{**balanced_tracks, "speech_lufs": None})
    assert report["ok"] is True
    assert all(row["ok"] is None for row in report["rows"])


def test_measurement_reports_silence_short_audio_and_empty_windows_as_unavailable(tone_inputs):
    silent = ["-f", "lavfi", "-i", "anullsrc=sample_rate=48000:channel_layout=mono:duration=1"]
    short = ["-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=0.1"]
    assert levels.measure(silent, "[0:a]anull[track]", "track") == -math.inf
    assert levels.measure(silent, "[0:a]anull[track]", "track", meter_gain_db=20) == -math.inf
    assert levels.measure(short, "[0:a]anull[track]", "track") == -math.inf
    assert _tone_level(tone_inputs, windows=[]) == -math.inf


@pytest.mark.parametrize("gain_db", [-65, -105, -180])
def test_measurement_recovers_quiet_float_audio_below_the_absolute_gate(tone_inputs, gain_db):
    original = _tone_level(tone_inputs, windows=[(1, 3), (9, 11)])
    graph = f"[0:a]aformat=sample_fmts=flt,volume={gain_db}dB[track]"
    windows = [(1, 3), (9, 11)]
    for meter_gain in (0, 19.4, 40):
        measured = levels.measure(
            tone_inputs, graph, "track", windows, meter_gain_db=meter_gain,
        )
        assert measured == pytest.approx(original + gain_db, abs=0.2)


def test_short_explicit_selection_cannot_pass_the_balance_check_as_silence(tone_inputs):
    with pytest.raises(ValueError, match="400 ms"):
        _tone_level(tone_inputs, windows=[(1, 1.1), (2, 2.1)])


def test_silent_voice_and_unmeasurable_music_cannot_be_calibrated():
    with pytest.raises(ValueError, match="measurable"):
        levels.music_gain(-math.inf, -20)
    with pytest.raises(ValueError, match="measurable"):
        levels.music_gain(-22, -math.inf)
    report = levels.check_balance(-math.inf, -36, -42, -28, -10)
    assert report["ok"] is False
    assert _rows(report)["voice"]["ok"] is False


def test_calibration_uses_the_render_stems_and_caps_loud_game_pauses(tmp_path, tone_inputs):
    paths = {}
    for track, gain, duration in [("game", 0, 12), ("music", -6, 12), ("voice", -12, 4)]:
        paths[track] = tmp_path / f"{track}.wav"
        gain_filter = f"volume={gain}dB"
        if track == "game":
            gain_filter += ",afade=t=out:st=11.94:d=0.06"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", *tone_inputs, "-t", str(duration),
             "-af", gain_filter, "-c:a", "pcm_s16le", str(paths[track])],
            check=True, capture_output=True,
        )
    speech, pauses = [(4, 8)], [(0, 4), (8, 12)]
    voice = [(paths["voice"], 4)]
    calibrated, report = levels.calibrate(
        paths["game"], paths["music"], voice, speech, 12, 12, music_volume=0.001,
    )
    assert report["ok"] is True
    assert report["game_duck_db"] > 0
    assert "sidechaincompress" not in calibrated.mix()
    assert calibrated.measure("music", pauses) == pytest.approx(
        calibrated.measure("voice") - 14, abs=0.5,
    )
    # The game tone is ~12 LU over the voice: its pauses come down to S - 3, its speech to S - 6.
    assert report["game_gain_db"] < 0
    assert calibrated.measure("game", pauses) == pytest.approx(calibrated.measure("voice") - 3, abs=0.5)
    assert calibrated.measure("game", speech) <= calibrated.measure("voice") - 6

    # The plan's music volume has no effect on a voiced graph; only calibrated dB do.
    other_volume = levels.audio_graph(
        paths["game"], paths["music"], voice, speech, 12, 12, music_volume=0.9,
        music_gain_db=report["music_gain_db"], game_gain_db=report["game_gain_db"],
        game_duck_db=report["game_duck_db"], ending_gain_db=report["ending_gain_db"],
    )
    assert other_volume.mix() == calibrated.mix()


def test_calibration_keeps_game_pauses_that_are_quiet_enough(tmp_path, tone_inputs):
    paths = {}
    for track, gain, duration in [("game", -16, 12), ("voice", -12, 4)]:
        paths[track] = tmp_path / f"{track}.wav"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", *tone_inputs, "-t", str(duration),
             "-af", f"volume={gain}dB", "-c:a", "pcm_s16le", str(paths[track])],
            check=True, capture_output=True,
        )
    speech, pauses = [(4, 8)], [(0, 4), (8, 12)]
    voice = [(paths["voice"], 4)]
    calibrated, report = levels.calibrate(paths["game"], None, voice, speech, 12, 12)
    assert report["ok"] is True
    assert report["game_gain_db"] == 0
    original = levels.audio_graph(paths["game"], None, voice, speech, 12, 12)
    assert calibrated.measure("game", pauses) == pytest.approx(original.measure("game", pauses), abs=0.5)


@pytest.mark.parametrize("game_gain,codec", [
    (-32, "pcm_s24le"), (-45, "pcm_s24le"), (-65, "pcm_f32le"), (-105, "pcm_f32le"),
])
def test_calibration_lowers_music_below_quiet_effects(tmp_path, tone_inputs, game_gain, codec):
    paths = {}
    for track, gain, duration in [("game", game_gain, 12), ("music", 0, 12), ("voice", -12, 4)]:
        paths[track] = tmp_path / f"{track}.wav"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", *tone_inputs, "-t", str(duration),
             "-af", f"volume={gain}dB", "-c:a", codec, str(paths[track])],
            check=True, capture_output=True,
        )
    speech, pauses = [(4, 8)], [(0, 4), (8, 12)]
    calibrated, report = levels.calibrate(
        paths["game"], paths["music"], [(paths["voice"], 4)], speech, 12, 12,
    )
    assert report["ok"] is True
    assert report["game_gain_db"] == 0
    meter_gain = report["master_gain_db"]
    voice = calibrated.measure("voice")
    game = calibrated.measure("game", pauses, meter_gain_db=meter_gain)
    music = calibrated.measure("music", pauses, meter_gain_db=meter_gain)
    music_speech = calibrated.measure("music", speech, meter_gain_db=meter_gain)
    assert game - 3 < voice - 14
    assert music == pytest.approx(min(voice - 14, game - 3), abs=levels.LEVEL_TOLERANCE_LU)
    assert music_speech == pytest.approx(music - 6, abs=levels.LEVEL_TOLERANCE_LU)
    assert _rows(report)["music_below_game"]["ok"] is True
    if game_gain <= -45:
        # A measurement-only boost clears the gate even if the master gain cannot.
        assert calibrated.measure("music", speech) == pytest.approx(music_speech, abs=0.2)
        assert music_speech < -70
        if game_gain <= -65:
            assert music_speech + meter_gain < -70
        assert _rows(report)["music_speech"]["measured_lufs"] == music_speech
        assert _rows(report)["music_speech"]["ok"] is True


def test_calibration_brings_a_loud_ending_to_voice_minus_three(tmp_path, tone_inputs):
    paths = {}
    for track, gain, duration in [("game", 0, 14), ("voice", -12, 4)]:
        paths[track] = tmp_path / f"{track}.wav"
        source = f"sine=frequency=1000:sample_rate=48000:duration={duration}"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", source,
             "-af", f"volume={gain}dB", "-c:a", "pcm_s16le", str(paths[track])],
            check=True, capture_output=True,
        )
    voice, speech = [(paths["voice"], 4)], [(4, 8)]
    raw = levels.audio_graph(paths["game"], None, voice, speech, 12, 14)
    assert raw.measure("game", [(12, 14)]) > raw.measure("voice")
    calibrated, report = levels.calibrate(paths["game"], None, voice, speech, 12, 14)
    ending = calibrated.measure("game", [(12, 14)])
    s = calibrated.measure("voice")
    assert report["ok"] is True
    assert report["ending_gain_db"] < 0
    assert ending == pytest.approx(s - 3, abs=0.5)
    assert ending <= s - 2
    assert _rows(report)["ending"]["ok"] is True


def test_calibration_boosts_a_quiet_ending_without_a_body_surge(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for synthetic loudness measurements")
    game, voice = tmp_path / "game.wav", tmp_path / "voice.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=1000:sample_rate=48000:duration=12", "-f", "lavfi", "-i",
         "sine=frequency=1000:sample_rate=48000:duration=2", "-filter_complex",
         "[0:a]afade=t=out:st=11.94:d=0.06[body];[1:a]volume=0.01[ending];"
         "[body][ending]concat=n=2:v=0:a=1[a]", "-map", "[a]", "-c:a", "pcm_s24le", str(game)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=1000:sample_rate=48000:duration=4", "-af", "volume=-12dB",
         "-c:a", "pcm_s24le", str(voice)], check=True, capture_output=True,
    )
    calibrated, report = levels.calibrate(game, None, [(voice, 4)], [(4, 8)], 12, 14)
    rows = _rows(report)
    s = report["speech_lufs"]
    assert report["ending_gain_db"] > 24
    assert report["ok"] is True
    assert rows["game_pause"]["measured_lufs"] <= s - 3
    assert rows["ending"]["measured_lufs"] == pytest.approx(s - 3, abs=0.5)
    # No sample in the body's fade may exceed the same body with a constant gain.
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *calibrated.inputs,
         "-filter_complex", ";".join(calibrated.filters + ["[vo]anullsink"]), "-map", "[g]",
         "-f", "f32le", "-ac", "1", "-ar", "48000", "-"], check=True, capture_output=True,
    )
    samples = array("f")
    samples.frombytes(result.stdout)
    body_peak = max(abs(value) for value in samples[round(11.94 * 48000):12 * 48000])
    assert body_peak <= 0.125 * 10 ** (report["game_gain_db"] / 20)


@pytest.mark.parametrize("ending_gain_db", [-18, -3, 18])
def test_body_gain_ramps_on_the_side_that_preserves_the_body_fade(tone_inputs, ending_gain_db):
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *tone_inputs,
         "-af", levels.body_gain_filter(-6, 6, ending_gain_db),
         "-f", "f32le", "-ac", "1", "-ar", "48000", "-"],
        check=True, capture_output=True,
    )
    samples = array("f")
    samples.frombytes(result.stdout)

    def rms(at):
        part = samples[round((at - 0.005) * 48000):round((at + 0.005) * 48000)]
        return math.sqrt(sum(value * value for value in part) / len(part))

    body = rms(5.9)
    ending = rms(6.1)
    assert ending == pytest.approx(body * 10 ** ((ending_gain_db + 6) / 20), rel=0.01)
    if ending_gain_db > -6:
        assert rms(5.99) == pytest.approx(body, rel=0.01)
        assert body < rms(6.01) < rms(6.03) < rms(6.05) < ending
    else:
        assert body > rms(5.95) > rms(5.97) > rms(5.99) > ending


def test_unvoiced_graph_keeps_plan_volume_and_does_not_duck(tmp_path):
    graph = levels.audio_graph(tmp_path / "game.mp4", tmp_path / "music.wav", [], [], 12, 14,
                               music_volume=0.8)
    assert graph.filters == [
        "[0:a]anull[g]",
        "[1:a]atrim=0:12,asetpts=PTS-STARTPTS,volume=0.8,afade=t=in:d=1,"
        "afade=t=out:st=10.5:d=1.5[m]",
    ]
    assert graph.mix().endswith("[g][m]amix=inputs=2:duration=first:normalize=0")
    ignored_voiced_gains = levels.audio_graph(
        tmp_path / "game.mp4", tmp_path / "music.wav", [], [], 12, 14,
        music_volume=0.8, music_gain_db=-20, game_gain_db=-12, game_duck_db=6, ending_gain_db=-18,
    )
    assert ignored_voiced_gains.inputs == graph.inputs
    assert ignored_voiced_gains.filters == graph.filters


@pytest.fixture
def comparison_cut():
    spec = importlib.util.spec_from_file_location(
        "comparison_cut", Path(__file__).resolve().parents[1] / "compare" / "cut.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mock_mastered_balance(monkeypatch, report):
    """Keep loop/peak/click unit tests independent of synthetic stem decoding."""
    report.update(speech_windows=[(4, 8)], pause_windows=[(0, 4), (8, 12)], ending_windows=[])
    monkeypatch.setattr(levels, "check_mastered_balance", lambda _stems, voice, *_windows:
                        levels.check_balance(voice, None, None, voice - 6, voice - 3))


@pytest.mark.parametrize("linear_peak,accepted", [(0.892, False), (0.891, True)])
def test_decoded_peak_metadata_decides_the_gate_when_the_summary_rounds_to_minus_one(
    monkeypatch, capsys, linear_peak, accepted,
):
    stderr = (
        f"lavfi.r128.true_peak={linear_peak}\n"
        "lavfi.r128.true_peak=0.5\n"
        "  I: -14.0 LUFS\n  True peak:\n    Peak: -1.0 dBFS\n"
    )
    run = Mock(return_value=SimpleNamespace(stderr=stderr))
    monkeypatch.setattr(levels.subprocess, "run", run)
    voice, peak = levels.meter_file(Path("short.mp4"))
    assert peak == pytest.approx(20 * math.log10(linear_peak), abs=1e-12)
    graph = run.call_args.args[0][run.call_args.args[0].index("-af") + 1]
    assert graph == "ebur128=peak=true:metadata=1,ametadata=print:key=lavfi.r128.true_peak"
    report = levels.check_balance(voice, None, None, None, None)
    levels.check_mastering(report, voice, peak, [])
    assert _rows(report)["true_peak"]["ok"] is accepted
    assert report["ok"] is accepted
    levels.print_balance(report)
    assert f"{peak:.6f}" in capsys.readouterr().out


def test_decoded_peak_cannot_fall_back_to_the_rounded_summary(monkeypatch):
    run = Mock(return_value=SimpleNamespace(stderr="I: -14.0 LUFS\nPeak: -1.0 dBFS\n"))
    monkeypatch.setattr(levels.subprocess, "run", run)
    with pytest.raises(ValueError, match="true peak"):
        levels.meter_file(Path("short.mp4"))


@pytest.mark.parametrize("balanced", [True, False])
def test_comparison_checks_and_writes_levels_before_publication(
    tmp_path, monkeypatch, capsys, comparison_cut, balanced,
):
    plan = {
        "brand": False,
        "labels": {"ru": ["До", "После"]},
        "hook": {"ru": "Проверка"}, "hook_s": 2,
        "music": {"file": "music.wav", "volume": 0.8},
        "beats": [{"segment": "main", "start": 0, "end": 12, "lines": [
            {"at": 4, "file": "voice.wav", "words": [["Проверка", 0, 4]]},
        ]}],
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan))
    out = tmp_path / "render" / "short-ru.mp4"
    out.parent.mkdir()
    out.write_bytes(b"stale short must not survive a failed balance check")
    report = levels.check_balance(-22, -36, -42, -28 if balanced else -20, -25)
    audio = SimpleNamespace(inputs=["-i", "game.mp4"], mix=lambda: "[0:a]anull")
    calibrate = Mock(return_value=(audio, report))
    master = Mock(side_effect=lambda _inputs, _mix, path, _length, **_kwargs: path.touch())
    monkeypatch.setattr(comparison_cut, "ROOT", tmp_path)
    monkeypatch.setattr(comparison_cut.sys, "argv", ["cut.py", "probe", "ru", str(plan_file)])
    monkeypatch.setattr(comparison_cut, "text_image", Mock())
    monkeypatch.setattr(comparison_cut, "caption_image", Mock())
    monkeypatch.setattr(comparison_cut, "run", Mock())
    monkeypatch.setattr(comparison_cut, "master", master)
    monkeypatch.setattr(levels, "calibrate", calibrate)

    if balanced:
        comparison_cut.main()
        master.assert_called_once_with(
            audio.inputs, audio.mix(), out, 12.0, voiced=True, audio=audio, report=report,
        )
        assert out.exists()
    else:
        with pytest.raises(SystemExit, match="balance check failed"):
            comparison_cut.main()
        master.assert_not_called()
        assert not out.exists()
    assert calibrate.call_args.kwargs["music_volume"] == 1.0
    assert json.loads(out.with_suffix(".levels.json").read_text()) == report
    output = capsys.readouterr().out
    assert all(track in output for track in ("voice", "music_pause", "music_speech", "game_speech"))
    assert ("ок" if balanced else "нет") in output


@pytest.mark.parametrize("voice_lufs,peak,click_times,failed_row", [
    (-14.0, -1.1, [], None),
    (-14.0, -0.9, [], "true_peak"),
    (-14.0, -1.1, [2.25], "clicks"),
    (-14.6, -1.1, [], "voice_mastered"),
])
def test_voiced_master_checks_decoded_audio_before_publication(
    tmp_path, monkeypatch, comparison_cut, voice_lufs, peak, click_times, failed_row,
):
    report = levels.check_balance(-28, None, None, -34, -31)
    report["speech_lufs"] = -28
    _mock_mastered_balance(monkeypatch, report)
    audio = levels.AudioGraph(["-i", "joined.mp4"], ["[0:a]asplit=2[g][vo]"],
                              {"game": "g", "voice": "vo"})
    commands = []

    def render(command):
        commands.append(command)
        for argument in command:
            if argument.endswith(("short-ru.mp4", "voice.m4a")):
                Path(argument).touch()

    monkeypatch.setattr(comparison_cut, "run", render)
    passes = 3 if failed_row == "voice_mastered" else 1
    meter = Mock(side_effect=[(voice_lufs, -4)] * passes + [(-13, peak)])
    detector = Mock(return_value=click_times)
    monkeypatch.setattr(levels, "meter_file", meter)
    monkeypatch.setattr(comparison_cut.clicks, "detect", detector)
    out = tmp_path / "short-ru.mp4"
    out.write_bytes(b"stale")
    if failed_row is None:
        comparison_cut.master(["-i", "joined.mp4"], "[0:a]anull", out, 12,
                              voiced=True, audio=audio, report=report)
        assert out.exists()
    else:
        with pytest.raises(ValueError, match="mastering check failed"):
            comparison_cut.master(["-i", "joined.mp4"], "[0:a]anull", out, 12,
                                  voiced=True, audio=audio, report=report)
        assert not out.exists()
        assert not list(tmp_path.rglob("*.mp4"))
        assert _rows(report)[failed_row]["ok"] is False
    assert _rows(report)["clicks"]["times_seconds"] == click_times
    assert detector.call_args.args[0].name == out.name
    assert meter.call_args_list[-1].args[0] == detector.call_args.args[0]
    assert len(commands) == report["master_passes"] == passes
    assert len(report["master_corrections_db"]) == passes - 1
    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "volume=14.000000000dB" in graph
    assert graph.count("alimiter=") == 1
    assert "alimiter=limit=0.79:level=false:latency=true" in graph
    assert "aeval='val(0)|val(1)|0.01':c=2.1" in graph
    assert "amultiply[voice_mastered]" in graph
    assert "loudnorm" not in graph
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("readings,corrections,passes_ok", [
    ([-14.1], [], True),
    ([-13.9], [], True),
    ([-14.8, -14.1], [0.8], True),
    ([-14.8, -14.3, -14.0], [0.8, 0.3], True),
    ([-13.7, -14.0], [-0.3], True),
    ([-15.0, -14.8, -14.7], [1.0, 0.8], False),
])
def test_master_corrections_change_only_common_gain_and_stop_after_two(
    tmp_path, monkeypatch, comparison_cut, readings, corrections, passes_ok,
):
    report = levels.check_balance(-28, None, None, -34, -31)
    report["speech_lufs"] = -28
    _mock_mastered_balance(monkeypatch, report)
    audio = levels.AudioGraph(["-i", "joined.mp4"], ["[0:a]asplit=2[g][vo]"],
                              {"game": "g", "voice": "vo"})
    mix = audio.mix()
    rendered_graphs = []

    def render(command):
        rendered_graphs.append(command[command.index("-filter_complex") + 1])
        for argument in command:
            if argument.endswith(("short-ru.mp4", "voice.m4a")):
                Path(argument).touch()

    monkeypatch.setattr(comparison_cut, "run", render)
    meter = Mock(side_effect=[(value, -4) for value in readings] + [(-13, -1.9)])
    detector = Mock(return_value=[])
    monkeypatch.setattr(levels, "meter_file", meter)
    monkeypatch.setattr(comparison_cut.clicks, "detect", detector)
    out = tmp_path / "short-ru.mp4"
    if passes_ok:
        comparison_cut.master(audio.inputs, mix, out, 12, voiced=True, audio=audio, report=report)
    else:
        with pytest.raises(ValueError, match="mastering check failed"):
            comparison_cut.master(audio.inputs, mix, out, 12, voiced=True, audio=audio, report=report)
    assert out.exists() is passes_ok
    assert report["ok"] is passes_ok
    assert report["master_passes"] == len(rendered_graphs) == len(readings) <= 3
    assert report["master_corrections_db"] == pytest.approx(corrections)
    assert report["master_gain_db"] == pytest.approx(14 + sum(corrections))
    assert detector.call_count == 1
    # Across renders only the mix and stem taps of the same common gain may change.
    gain = 14.0
    for index, graph in enumerate(rendered_graphs):
        assert graph.count(f"volume={gain:.9f}dB") == len(audio.tracks) + 1
        assert graph.replace(f"volume={gain:.9f}dB", "COMMON_GAIN") == rendered_graphs[0].replace(
            "volume=14.000000000dB", "COMMON_GAIN",
        )
        if index < len(corrections):
            gain += corrections[index]
    assert audio.mix() == mix


@pytest.mark.parametrize("failed_row", ["true_peak", "clicks", "voice_mastered"])
def test_mastering_failure_writes_report_and_click_times(
    tmp_path, monkeypatch, comparison_cut, capsys, failed_row,
):
    plan = {
        "brand": False, "labels": {"ru": ["До", "После"]},
        "hook": {"ru": "Проверка"}, "hook_s": 2,
        "beats": [{"segment": "main", "start": 0, "end": 12, "lines": [
            {"at": 4, "file": "voice.wav", "words": [["Проверка", 0, 4]]},
        ]}],
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan))
    out = tmp_path / "render" / "short-ru.mp4"
    out.parent.mkdir()
    out.touch()
    report = levels.check_balance(-22, None, None, -28, -25)
    report["speech_lufs"] = -22
    _mock_mastered_balance(monkeypatch, report)
    audio = levels.AudioGraph(["-i", "game.mp4"], ["[0:a]asplit=2[g][vo]"],
                              {"game": "g", "voice": "vo"})

    def render(command):
        for argument in command:
            if argument.endswith(("short-ru.mp4", "voice.m4a")):
                Path(argument).touch()

    monkeypatch.setattr(comparison_cut.sys, "argv", ["cut.py", "probe", "ru", str(plan_file)])
    monkeypatch.setattr(comparison_cut, "text_image", Mock())
    monkeypatch.setattr(comparison_cut, "caption_image", Mock())
    monkeypatch.setattr(comparison_cut, "run", render)
    monkeypatch.setattr(levels, "calibrate", Mock(return_value=(audio, report)))
    voice_readings = [(-15, -4)] * 3 if failed_row == "voice_mastered" else [(-14, -4)]
    monkeypatch.setattr(levels, "meter_file", Mock(side_effect=voice_readings + [
        (-13, -0.9 if failed_row == "true_peak" else -1.1),
    ]))
    times = [2.25] if failed_row == "clicks" else []
    monkeypatch.setattr(comparison_cut.clicks, "detect", Mock(return_value=times))
    with pytest.raises(SystemExit, match="mastering check failed"):
        comparison_cut.main()
    assert not out.exists()
    written = json.loads(out.with_suffix(".levels.json").read_text())
    assert written["ok"] is False
    assert _rows(written)[failed_row]["ok"] is False
    assert _rows(written)["clicks"]["times_seconds"] == times
    printed = capsys.readouterr().out
    assert failed_row in printed
    if times:
        assert "2.250000" in printed


@pytest.mark.parametrize("speech", [-30, -20, -10])
def test_voice_based_master_gain_reaches_base(tone_inputs, speech):
    assert levels.master_gain(speech) + speech == levels.BASE_LUFS
    original = _tone_level(tone_inputs, speech)
    mastered = _tone_level(tone_inputs, speech, extra=levels.master_filter(original, 12))
    assert mastered == pytest.approx(levels.BASE_LUFS, abs=levels.VOICE_TOLERANCE_LU)


def test_synthetic_voiced_mix_publishes_voice_at_base_after_aac(
    tmp_path, tone_inputs, comparison_cut, monkeypatch,
):
    joined, voice, out = (tmp_path / name for name in ("joined.mp4", "voice.wav", "short-ru.mp4"))
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=16x16:r=1:d=12", *tone_inputs, "-map", "0:v", "-map", "1:a",
         "-af", "volume=-12dB", "-c:v", "mpeg4", "-c:a", "aac", str(joined)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *tone_inputs,
         "-c:a", "pcm_s16le", str(voice)],
        check=True, capture_output=True,
    )
    audio, report = levels.calibrate(joined, None, [(voice, 0)], [(0, 12)], 12, 12)
    assert report["ok"] is True
    render = Mock(wraps=comparison_cut.run)
    monkeypatch.setattr(comparison_cut, "run", render)
    comparison_cut.master(audio.inputs, audio.mix(), out, 12, voiced=True, audio=audio, report=report)
    rows = _rows(report)
    assert rows["voice_mastered"]["measured_lufs"] == pytest.approx(-14, abs=0.5)
    assert rows["true_peak"]["measured_dbtp"] <= -1
    assert rows["clicks"]["count"] == 0
    assert report["ok"] is True
    assert out.stat().st_size > 0
    assert render.call_count == report["master_passes"] == 1
    assert report["master_corrections_db"] == []


def test_voice_correction_uses_the_mix_limiter_and_matches_the_published_channel(
    tmp_path, comparison_cut, monkeypatch,
):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for synthetic mastering")
    voice, joined, out = (tmp_path / name for name in ("voice.wav", "joined.mkv", "short-ru.mp4"))
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "aevalsrc='0.03*sin(2*PI*1000*t)|0':s=48000:d=6:c=stereo",
         "-c:a", "pcm_s24le", str(voice)], check=True, capture_output=True,
    )
    impulses = ("aevalsrc='0|if(lt(mod(t,0.1),0.002),"
                "0.2*sin(PI*mod(t,0.1)/0.002)^2*sin(2*PI*1000*mod(t,0.1)),0)':s=48000:d=6:c=stereo")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=16x16:r=1:d=6", "-f", "lavfi", "-i", impulses,
         "-c:v", "mpeg4", "-c:a", "pcm_s24le", str(joined)], check=True, capture_output=True,
    )
    audio, report = levels.calibrate(joined, None, [(voice, 0)], [(0, 6)], 6, 6)
    assert report["ok"] is True  # Effects are quiet enough in LUFS, despite their high peaks.
    measured_mix_voice = []
    real_meter = levels.meter_file

    def meter(path):
        if path.name == out.name:
            # The mix's left channel contains only the voice, so this is an independent check.
            measured_mix_voice.append(levels.measure(["-i", str(path)], "[0:a]pan=mono|c0=c0[v]", "v"))
        return real_meter(path)

    monkeypatch.setattr(levels, "meter_file", meter)
    comparison_cut.master(audio.inputs, audio.mix(), out, 6, voiced=True, audio=audio, report=report)
    rows = _rows(report)
    assert measured_mix_voice[0] == pytest.approx(-14.0, abs=0.2)
    assert rows["voice_mastered"]["measured_lufs"] == pytest.approx(measured_mix_voice[0], abs=0.1)
    assert rows["voice_mastered"]["ok"] is True
    assert rows["true_peak"]["ok"] is True
    assert rows["clicks"]["count"] == 0
    assert report["ok"] is True
    assert report["master_passes"] == 2
    assert out.exists()


def test_unvoiced_master_keeps_original_two_pass_filters(tmp_path, monkeypatch, comparison_cut):
    measured = {"input_i": "-19.1", "input_tp": "-3.2", "input_lra": "2.0",
                "input_thresh": "-29.1", "target_offset": "0.1"}
    probe = Mock(return_value=SimpleNamespace(stderr=json.dumps(measured)))
    render = Mock()
    monkeypatch.setattr(comparison_cut.subprocess, "run", probe)
    monkeypatch.setattr(comparison_cut, "run", render)
    comparison_cut.master(["-i", "joined.mp4"], "[0:a]anull", tmp_path / "short.mp4", 12)
    graph = render.call_args.args[0][render.call_args.args[0].index("-filter_complex") + 1]
    assert graph == (
        "[0:a]anull,alimiter=limit=0.75:level=false,loudnorm=I=-14:TP=-1.5:LRA=11:"
        "measured_I=-19.1:measured_TP=-3.2:measured_LRA=2.0:measured_thresh=-29.1:"
        "offset=0.1:linear=true,afade=t=out:st=11.920:d=0.08[a]"
    )


def test_peaky_voice_reaches_base_with_bounded_common_gain_corrections(
    tmp_path, comparison_cut, monkeypatch,
):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for synthetic mastering")
    voice, joined, out = (tmp_path / name for name in ("voice.wav", "joined.mkv", "short-ru.mp4"))
    # Smooth 2-ms peaks have a high crest factor without introducing clicks.
    peaky_voice = (
        "aevalsrc='(0.03+if(lt(mod(t,0.1),0.002),"
        "0.15*pow(sin(PI*mod(t,0.1)/0.002),2),0))*sin(2*PI*1000*t)':s=48000:d=6"
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", peaky_voice,
         "-c:a", "pcm_s24le", str(voice)], check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=16x16:r=1:d=6", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=6",
         "-c:v", "mpeg4", "-c:a", "pcm_s24le", str(joined)], check=True, capture_output=True,
    )
    audio, report = levels.calibrate(joined, None, [(voice, 0)], [(0, 6)], 6, 6)
    calibrated_mix = audio.mix()
    assert report["ok"] is True
    measured_voice = []
    real_meter = levels.meter_file

    def meter(path):
        result = real_meter(path)
        if path.name == "voice.m4a":
            measured_voice.append(result[0])
        return result

    monkeypatch.setattr(levels, "meter_file", meter)
    comparison_cut.master(audio.inputs, calibrated_mix, out, 6, voiced=True, audio=audio, report=report)
    rows = _rows(report)
    assert measured_voice[0] <= levels.BASE_LUFS - 0.5
    assert measured_voice[-1] == pytest.approx(levels.BASE_LUFS, abs=0.2)
    assert rows["voice_mastered"]["measured_lufs"] == measured_voice[-1]
    assert 1 < len(measured_voice) == report["master_passes"] <= 3
    assert len(report["master_corrections_db"]) == len(measured_voice) - 1 <= 2
    assert audio.mix() == calibrated_mix
    assert rows["voice_mastered"]["ok"] is True
    assert rows["true_peak"]["ok"] is True
    assert rows["clicks"]["count"] == 0
    assert report["ok"] is True
    assert out.stat().st_size > 0


def test_mastering_rejects_effects_and_ending_raised_above_the_final_voice(
    tmp_path, comparison_cut, monkeypatch,
):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for synthetic mastering")
    voice, joined, out = (tmp_path / name for name in ("voice.wav", "joined.mkv", "short-ru.mp4"))
    peaky_voice = (
        "aevalsrc='(0.03+if(lt(mod(t,0.1),0.002),"
        "0.4*pow(sin(PI*mod(t,0.1)/0.002),2),0))*sin(2*PI*1000*t)':s=48000:d=4"
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", peaky_voice,
         "-c:a", "pcm_f32le", str(voice)], check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=16x16:r=1:d=14", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=14",
         "-c:v", "mpeg4", "-c:a", "pcm_f32le", str(joined)], check=True, capture_output=True,
    )
    audio, report = levels.calibrate(joined, None, [(voice, 4)], [(4, 8)], 12, 14)
    assert report["ok"] is True
    pre_master_rows = [row.copy() for row in report["rows"]]
    measured_voice, candidate_levels = [], {}
    real_meter = levels.meter_file

    def meter(path):
        result = real_meter(path)
        if path.name == "voice.m4a":
            measured_voice.append(result[0])
        elif path.name == out.name:
            # These windows contain only effects in the actual mixed AAC.
            candidate_levels["game_pause"] = levels.measure(
                ["-i", str(path)], "[0:a]anull[mixed]", "mixed", [(0, 3.8), (8.2, 11.8)],
            )
            candidate_levels["ending"] = levels.measure(
                ["-i", str(path)], "[0:a]anull[mixed]", "mixed", [(12, 14)],
            )
        return result

    monkeypatch.setattr(levels, "meter_file", meter)
    with pytest.raises(ValueError, match="mastering check failed"):
        comparison_cut.master(audio.inputs, audio.mix(), out, 14,
                              voiced=True, audio=audio, report=report)
    rows = _rows(report)
    assert measured_voice[-1] == pytest.approx(-14.4, abs=0.2)
    assert rows["voice_mastered"]["measured_lufs"] == measured_voice[-1]
    assert rows["voice_mastered"]["ok"] is True
    for key, below in (("game_pause", 3), ("ending", 2)):
        assert rows[key]["ok"] is False
        assert rows[key]["target_lufs"] == pytest.approx(measured_voice[-1] - below)
        assert rows[key]["measured_lufs"] == pytest.approx(candidate_levels[key], abs=0.2)
    assert candidate_levels["game_pause"] == pytest.approx(-12.5, abs=0.2)
    assert candidate_levels["ending"] == pytest.approx(-12.4, abs=0.2)
    assert report["pre_master_rows"] == pre_master_rows
    assert all(row["ok"] is not False for row in report["pre_master_rows"])
    assert report["balance_stage"] == "mastered_aac"
    assert report["ok"] is False
    assert not out.exists()


def test_mastering_checks_all_decoded_stems_of_a_balanced_music_mix(tmp_path, comparison_cut):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for synthetic mastering")
    joined, voice, music, out = (
        tmp_path / name for name in ("joined.mkv", "voice.wav", "music.wav", "short-ru.mp4")
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=16x16:r=1:d=12", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=12", "-af", "volume=-20dB",
         "-c:v", "mpeg4", "-c:a", "pcm_f32le", str(joined)], check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=1000:sample_rate=48000:duration=4", "-af", "volume=-12dB",
         "-c:a", "pcm_f32le", str(voice)], check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "sine=frequency=220:sample_rate=48000:duration=12",
         "-c:a", "pcm_f32le", str(music)], check=True, capture_output=True,
    )
    audio, report = levels.calibrate(joined, music, [(voice, 4)], [(4, 8)], 12, 12)
    assert report["ok"] is True
    pre_master_rows = [row.copy() for row in report["rows"]]
    comparison_cut.master(audio.inputs, audio.mix(), out, 12, voiced=True, audio=audio, report=report)
    rows = _rows(report)
    assert report["balance_stage"] == "mastered_aac"
    assert rows["voice"]["measured_lufs"] == rows["voice_mastered"]["measured_lufs"]
    assert rows["voice_mastered"]["measured_lufs"] == pytest.approx(-14, abs=0.2)
    for key in ("music_pause", "music_speech", "game_pause", "game_speech",
                "game_below_voice", "music_below_game", "voice_mastered", "true_peak", "clicks"):
        assert rows[key]["ok"] is True
    assert report["pre_master_rows"] == pre_master_rows
    assert report["ok"] is True
    assert out.stat().st_size > 0


def test_mastering_rejects_a_true_peak_hidden_by_the_rounded_summary(
    tmp_path, comparison_cut, monkeypatch,
):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for synthetic mastering")
    voice, joined, out = (tmp_path / name for name in ("voice.wav", "joined.mkv", "short-ru.mp4"))
    peaky_voice = (
        "aevalsrc='(0.03+if(lt(mod(t,0.1),0.002),"
        "0.4*pow(sin(PI*mod(t,0.1)/0.002),2),0))*sin(2*PI*12000*t+0.49)':s=48000:d=3"
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", peaky_voice,
         "-c:a", "pcm_f32le", str(voice)], check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=black:s=16x16:r=1:d=3", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=3",
         "-c:v", "mpeg4", "-c:a", "pcm_f32le", str(joined)], check=True, capture_output=True,
    )
    audio, report = levels.calibrate(joined, None, [(voice, 0)], [(0, 3)], 3, 3)
    assert report["ok"] is True
    candidate_peak = {}
    real_meter = levels.meter_file

    def meter(path):
        result = real_meter(path)
        if path.name == out.name:
            # Independently compare the decoded candidate's detailed and rounded R128 output.
            probe = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                 "ebur128=peak=true:metadata=1,ametadata=print:key=lavfi.r128.true_peak",
                 "-f", "null", "-"], check=True, capture_output=True, text=True,
            ).stderr
            peaks = [float(line.split("lavfi.r128.true_peak=", 1)[1])
                     for line in probe.splitlines() if "lavfi.r128.true_peak=" in line]
            candidate_peak["detailed"] = 20 * math.log10(max(peaks))
            candidate_peak["rounded"] = float(probe.rsplit("Peak:", 1)[1].split()[0])
        return result

    monkeypatch.setattr(levels, "meter_file", meter)
    with pytest.raises(ValueError, match="mastering check failed"):
        comparison_cut.master(audio.inputs, audio.mix(), out, 3,
                              voiced=True, audio=audio, report=report)
    rows = _rows(report)
    assert candidate_peak["rounded"] == levels.TRUE_PEAK_DBTP
    assert candidate_peak["detailed"] > levels.TRUE_PEAK_DBTP
    assert rows["true_peak"]["measured_dbtp"] == pytest.approx(candidate_peak["detailed"], abs=1e-6)
    assert rows["true_peak"]["ok"] is False
    assert rows["voice_mastered"]["ok"] is True
    assert rows["clicks"]["count"] == 0
    assert report["ok"] is False
    assert not out.exists()
