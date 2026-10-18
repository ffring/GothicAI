import copy
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_cut import _small_cut
from test_cut import published_cut as published_cut
from test_cut import synthetic_assets as synthetic_assets
from test_explain import _plan
from test_explain import synthetic_stems as synthetic_stems
from test_timeline import EVENTS

from gothic_shorts import cli, cut, dub, explain, levels, stems
from gothic_shorts.timeline import build
from gothic_shorts.voice import VoiceError

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")
# the launcher's settings as dub reads them: Inworld, VoxCPM when it fails
CONFIG = SimpleNamespace(settings=SimpleNamespace(tts=SimpleNamespace(provider="inworld", fallback="voxcpm")))
HERO = {"hero": ("SHORTS_HERO", "A man")}


@pytest.fixture(autouse=True)
def voices(tmp_path, monkeypatch):
    """The kept voices in the test's own folder, never the real ~/.local/share/gothic-ai/voices."""
    kept = tmp_path / "kept-voices"
    monkeypatch.setattr(dub, "VOICES", kept)
    return kept


SUBS = {
    "Ксардас": "Xardas",
    "Меня прислал Ли.": "Lee sent me.",
    "Ли? Не смеши меня.": "Lee? Don't make me laugh.",
    "Уходи.": "Go away.",
}


def test_a_line_runs_until_the_next_one_starts():
    got = dub.slots(
        [
            ("hero", "а", "a", 1.0, 3.0),
            ("NPC", "б", "b", 4.0, 6.0),
            ("OTHER", "в", "c", 5.5, 7.0),  # speaks over the npc in Russian
            ("NPC", "г", "d", 8.0, 9.0),
        ],
        10.0,
    )
    assert [(s.start, s.end) for s in got] == [
        (1.0, 4.0 - dub.GAP_S),
        (4.0, 6.0),  # the next line starts while it speaks: the English may go on as long as the Russian did
        (5.5, 8.0 - dub.GAP_S),
        (8.0, 10.0 - dub.TAIL_S),  # the last one ends before the short does
    ]


def test_a_line_is_sped_up_a_little_or_reported():
    slot = dub.Slot("hero", "а", "a", 1.0, 3.0)
    assert dub.fit(1.5, slot) == dub.Fit(1.0, 1.5, True)
    assert dub.fit(2.2, slot) == dub.Fit(1.1, 2.0, True)
    too_long = dub.fit(3.0, slot)
    assert not too_long.fits and too_long.tempo == dub.MAX_TEMPO
    assert too_long.seconds == pytest.approx(3.0 / dub.MAX_TEMPO)


def test_the_take_lines_in_the_time_of_the_cut():
    tl = build(EVENTS)
    hero, answer, last = dub.take_slots(tl, SUBS)
    # the hero's speech starts after its recording's lead-in
    assert (hero.who, hero.en, hero.start) == ("hero", "Lee sent me.", pytest.approx(1.25))
    # the answer at 6.0 s of the recording is at 3.45 s of the cut (the AI's wait cut down)
    assert (answer.who, answer.start) == ("XARDAS", pytest.approx(3.45))
    assert hero.end == pytest.approx(3.45 - dub.GAP_S)
    assert answer.end == pytest.approx(250 / 30 - 2.55 - dub.GAP_S)
    assert last.end == pytest.approx(tl.cut_duration - dub.TAIL_S)


def test_english_voices_are_designed_by_inworld_in_english():
    assert dub.english_provider("inworld", "en-US") == "inworld:language=en-US"
    assert dub.english_provider("inworld:model=m,language=ru-RU", "en-US") == "inworld:model=m,language=en-US"
    assert dub.english_provider("voxcpm", "en-US") == "inworld:language=en-US"


def test_the_reference_is_the_first_lines_up_to_its_length():
    assert dub.reference_count([3.0, 3.0, 3.0, 3.0]) == 3  # 9.9 s with the gaps: at least 8
    assert dub.reference_count([5.0, 10.0]) == 1  # the second would make it longer than VoxCPM takes
    assert dub.reference_count([16.0]) == 1  # cut to the most there
    assert dub.reference_count([2.0]) == 1


def test_the_english_script_without_its_caption_breaks(tmp_path):
    (tmp_path / "script.en.txt").write_text("In Gothic | you don't aim\n\nYes\n")
    assert dub.english_script(tmp_path, "en") == ["In Gothic you don't aim", "Yes"]


def test_inworld_clones_the_voice_the_game_speaks_russian_in(tmp_path, monkeypatch, voices):
    calls = []

    def say(config, voice_id, description, lines, *, provider=None, fallback=None, clone=None, keep=None):
        if not lines:
            return  # as voice.say: nothing to voice, no gothic-tts
        calls.append((voice_id, provider, clone, list(lines)))
        for path in lines.values():
            path.parent.mkdir(parents=True, exist_ok=True)  # as voice.say
            path.write_bytes(b"wav " + voice_id.encode())

    monkeypatch.setattr(dub, "say", say)
    config = SimpleNamespace(settings=SimpleNamespace(tts=SimpleNamespace(provider="inworld:delivery=stable",
                                                                          fallback="voxcpm")))  # fmt: skip
    got = dub.english_voices(config, HERO, {"hero": ["Hi."]}, tmp_path, "en")
    (game, ru_spec, _, sample), (english, en_spec, clone, texts) = calls
    # a Russian sample in the game's own voice (its id and description: the voice the game designed) ...
    assert (game, ru_spec, sample) == ("SHORTS_HERO", "inworld:delivery=stable", [dub.RUSSIAN_SAMPLE])
    # ... cloned, the lines said in English
    assert (english, texts) == ("SHORTS_HERO@en~ru", ["Hi."])
    assert en_spec == "inworld:delivery=stable,language=en-US"
    assert clone.read_bytes() == b"wav SHORTS_HERO" and got[("hero", "Hi.")].is_file()
    assert clone.parent == voices  # kept for every checkout: cloned once
    dub.english_voices(config, HERO, {"hero": ["Hi."]}, tmp_path, "en")
    assert len(calls) == 2  # kept: nothing voiced again


def test_a_speaker_falls_back_to_the_next_provider(tmp_path, monkeypatch):
    def no_credits(*args, **kwargs):
        raise VoiceError("gothic-tts say failed:\nerror: no voice: inworld unavailable")

    designed = []

    def voxcpm(ref, lines, work, design=None):
        designed.append(design)
        for path in lines.values():
            path.write_bytes(b"wav")

    monkeypatch.setattr(dub, "say", no_credits)
    monkeypatch.setattr(dub, "voxcpm", voxcpm)
    got = dub.english_voices(CONFIG, HERO, {"hero": ["Hi.", "Bye."]}, tmp_path, "en")
    assert set(got) == {("hero", "Hi."), ("hero", "Bye.")} and all(p.is_file() for p in got.values())
    assert designed[0]["description"] == "A man" and designed[0]["sample"] == dub.SAMPLES["en"]
    with pytest.raises(dub.DubError, match="inworld unavailable"):
        dub.english_voices(CONFIG, HERO, {"hero": ["New."]}, tmp_path, "en", "inworld")


def _sound(path: Path, seconds: float, expr: str = "0.1*sin(2*PI*330*t)") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    src = f"aevalsrc='{expr}|{expr}':s=48000:d={seconds}"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-c:a", "pcm_s16le", str(path)],
                   check=True)  # fmt: skip
    return path


def _samples(wav: Path) -> int:
    import wave

    with wave.open(str(wav), "rb") as w:
        return w.getnframes()


def _tts_sound(path, seconds=1.0, frequency=700, gain=-8):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"sine=frequency={frequency}:sample_rate=48000:duration={seconds}",
         "-af", f"volume={gain}dB,afade=t=in:d=0.06,afade=t=out:st={seconds - 0.06}:d=0.06",
         "-c:a", "pcm_s16le", str(path)], check=True, capture_output=True,
    )
    return path


def _audio_pcm(path, stream="a:0", *, sample_format="f32le", samples=None):
    trim = [] if samples is None else ["-af", f"atrim=end_sample={samples}"]
    return subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", f"0:{stream}",
         *trim, "-f", sample_format, "-ar", "48000", "-ac", "2", "-"],
        check=True, capture_output=True,
    ).stdout


def _streams(path, selector="a"):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", selector, "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)["streams"]


def _packet_hashes(path, selector):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", selector, "-show_packets",
         "-show_entries", "packet=data_hash", "-show_data_hash", "sha256", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return [packet["data_hash"] for packet in json.loads(result.stdout)["packets"]]


def _rows(report):
    return {row["id"]: row for row in report["rows"]}


def _stale_outputs(directory, lang="en"):
    before = {}
    for suffix in ("wav", "mp4", "levels.json"):
        path = directory / f"dub.{lang}.{suffix}"
        before[path] = f"stale publication: {path.name}".encode()
        path.write_bytes(before[path])
    return before


def _assert_unchanged(before):
    assert {path: path.read_bytes() for path in before} == before


def _no_outputs(directory):
    assert not (directory / "dub.en.wav").exists()
    assert not (directory / "dub.en.mp4").exists()


def _assert_publication(done, russian):
    report = json.loads(done.preview.with_suffix(".levels.json").read_text())
    ru_report = json.loads(russian.with_suffix(".levels.json").read_text())
    rows = _rows(report)
    assert report["ok"] is True and report["balance_stage"] == "mastered_aac"
    assert "stale" not in report
    assert rows["voice_mastered"]["measured_lufs"] == pytest.approx(-14, abs=0.5)
    assert rows["true_peak"]["measured_dbtp"] <= -1
    assert rows["clicks"]["count"] == 0
    assert all(row["ok"] is not False for row in report["rows"])
    assert done.lufs == rows["voice_mastered"]["measured_lufs"]
    assert done.ru_lufs == _rows(ru_report)["voice_mastered"]["measured_lufs"]
    assert done.duration == pytest.approx(done.ru_duration, abs=0.001)
    wav_stream, = _streams(done.wav)
    assert wav_stream["codec_name"] == "pcm_s24le"
    assert wav_stream["sample_rate"] == "48000" and wav_stream["channels"] == 2
    assert _samples(done.wav) / 48000 == pytest.approx(float(_streams(russian)[0]["duration"]), abs=0.001)
    english, original = _streams(done.preview)
    assert english["codec_name"] == original["codec_name"] == "aac"
    assert english["tags"]["language"] == "eng" and english["disposition"]["default"] == 1
    assert original["tags"]["language"] == "rus" and original["disposition"]["default"] == 0
    assert _packet_hashes(done.preview, "v:0") == _packet_hashes(russian, "v:0")
    assert _packet_hashes(done.preview, "a:1") == _packet_hashes(russian, "a:0")
    assert _audio_pcm(done.wav, sample_format="s24le") == _audio_pcm(
        done.preview, sample_format="s24le", samples=_samples(done.wav),
    )
    # MP4 atom order verifies +faststart without decoding the picture.
    data, atoms, offset = done.preview.read_bytes(), [], 0
    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        atoms.append(data[offset + 4:offset + 8])
        if size == 0:
            break
        offset += size
    assert atoms.index(b"moov") < atoms.index(b"mdat")
    return report


@pytest.fixture
def take_workspace(tmp_path, published_cut, monkeypatch):
    take = tmp_path / "take"
    shutil.copytree(published_cut.take, take, ignore=shutil.ignore_patterns("dub-*", "dub.*"))
    events = copy.deepcopy(published_cut.events)
    for event in events:
        if "wav" in event:
            event["wav"] = str(take / Path(event["wav"]).name)
    (take / "events.jsonl").write_text("\n".join(json.dumps({"seg": "main", **event}) for event in events))
    translations = {line.text: f"English line {i}" for i, line in enumerate(published_cut.timeline.lines)}
    (take / "subs.en.json").write_text(json.dumps(translations))
    _small_cut(monkeypatch, published_cut)
    monkeypatch.setattr(dub, "tools", lambda config: (tmp_path, Path("uv")))
    monkeypatch.setattr(dub, "npc_voices", lambda config, npcs: {
        npc: {"voice_id": npc, "voice": f"Voice of {npc}"} for npc in npcs
    })

    def english_voices(config, voices, lines, cache, lang, tts=None):
        assert set(voices) == {"hero", "XARDAS", "DIEGO"}
        recordings = {}
        for who, texts in lines.items():
            frequency = {"hero": 700, "XARDAS": 1000, "DIEGO": 1500}[who]
            for i, text in enumerate(texts):
                gain = {"hero": -8 - 6 * i, "XARDAS": -25, "DIEGO": 0}[who]
                recordings[(who, text)] = _tts_sound(cache / f"{who}-{i}.wav", frequency=frequency, gain=gain)
        return recordings

    def no_gpu(*args, **kwargs):
        raise AssertionError("a stem dub must not request GPU separation or real voices")

    monkeypatch.setattr(dub, "english_voices", english_voices)
    monkeypatch.setattr(dub, "job", no_gpu)
    monkeypatch.setattr(dub, "say", no_gpu)
    return SimpleNamespace(take=take, shot=published_cut.shot, events=events, source=published_cut)


def test_take_dub_uses_the_russian_effects_bed_and_picture_with_english_speaker_gains(
    take_workspace, monkeypatch, capsys,
):
    case = take_workspace
    _stale_outputs(case.take)
    captured, speaker_calls = [], []
    real_publish, real_gains = levels.publish, stems.speaker_gains

    def speaker_gains(clips):
        speaker_calls.append([(clip.who, clip.path) for clip in clips])
        return real_gains(clips)

    def publish(joined, music, voice, speech, total, length, out, **kwargs):
        assert out == case.take / "dub.en.mp4"
        assert joined.name == "joined.mkv" and music.is_file()
        assert len(voice) == 1 and voice[0][1] == 0.0
        assert total == cut.body_duration(case.source.timeline)
        assert length == pytest.approx(total + 1.2)
        assert float(_streams(voice[0][0])[0]["duration"]) == pytest.approx(total, abs=1 / 48000)
        assert _audio_pcm(joined) == _audio_pcm(case.source.take / "cut-ru/joined.mkv")
        assert _audio_pcm(music) == _audio_pcm(case.source.take / "cut-ru/music.wav")
        assert _packet_hashes(joined, "v:0") == _packet_hashes(case.take / "short-ru.mp4", "v:0")
        subs = json.loads((case.take / "subs.en.json").read_text())
        expected_slots = dub.take_slots(case.source.timeline, subs)
        assert len(speech) == len(expected_slots)
        for (start, end), slot in zip(speech, expected_slots, strict=True):
            assert start == pytest.approx(slot.start, abs=0.001)
            assert end - start == pytest.approx(1, abs=0.08)
        assert speech != case.source.report["speech_windows"]
        # Pool the two hero lines as one speaker while retaining their 6 dB dynamics.
        by_speaker = [
            levels.measure(["-i", str(voice[0][0])], "[0:a]anull[v]", "v", [speech[0], speech[2]]),
            levels.measure(["-i", str(voice[0][0])], "[0:a]anull[v]", "v", [speech[1]]),
            levels.measure(["-i", str(voice[0][0])], "[0:a]anull[v]", "v", [speech[3]]),
        ]
        assert max(by_speaker) - min(by_speaker) <= 0.5
        hero = [levels.measure(["-i", str(voice[0][0])], "[0:a]anull[v]", "v", [speech[i]]) for i in (0, 2)]
        assert hero[0] - hero[1] == pytest.approx(6, abs=0.5)
        captured.append(speech)
        return real_publish(joined, music, voice, speech, total, length, out, **kwargs)

    monkeypatch.setattr(stems, "speaker_gains", speaker_gains)
    monkeypatch.setattr(levels, "publish", publish)
    done = dub.dub_take(CONFIG, case.shot, case.take)

    assert done.wav == case.take / "dub.en.wav" and done.preview == case.take / "dub.en.mp4"
    assert len(done.rows) == 4 and not done.too_long
    assert len(captured) == 1 and len(speaker_calls) == 1
    assert [who for who, path in speaker_calls[0]].count("hero") == 2
    report = _assert_publication(done, case.take / "short-ru.mp4")
    assert report["speech_windows"] == [list(window) for window in captured[0]]
    # Ending windows follow the calculated cut length; AAC may retain a short
    # codec tail. Only the deliverable WAV follows that encoded audio duration.
    assert report["ending_windows"] == case.source.report["ending_windows"]
    output = capsys.readouterr().out
    assert all(name in output for name in (
        "voice_mastered", "music_pause", "game_speech", "true_peak", "clicks",
    ))


@pytest.mark.parametrize("invalid", ["video-missing", "report-missing", "report-false"])
def test_take_dub_requires_a_successful_russian_publication(take_workspace, monkeypatch, invalid):
    case = take_workspace
    before = _stale_outputs(case.take)
    russian = case.take / "short-ru.mp4"
    if invalid == "video-missing":
        russian.unlink()
    elif invalid == "report-missing":
        russian.with_suffix(".levels.json").unlink()
    else:
        russian.with_suffix(".levels.json").write_text('{"ok": false}')

    def unexpected_voices(*args, **kwargs):
        raise AssertionError("invalid Russian publication must be rejected before requesting voices")

    monkeypatch.setattr(dub, "english_voices", unexpected_voices)
    with pytest.raises(dub.DubError, match="gothic-shorts cut"):
        dub.dub_take(CONFIG, case.shot, case.take)
    _assert_unchanged(before)


@pytest.mark.parametrize("old_format", ["npc-mixed-in-game", "game-music", "world-sounds"])
def test_take_dub_rejects_the_same_old_recordings_as_cut(take_workspace, old_format):
    case = take_workspace
    before = _stale_outputs(case.take)
    if old_format == "npc-mixed-in-game":
        (case.take / "events.jsonl").write_text("\n".join(
            json.dumps({"seg": "main", **event}) for event in case.events if event["kind"] != "npc_voice"
        ))
    elif old_format == "game-music":
        (case.take / "run/Gothic.ini").write_text("[SOUND]\nmusicVolume=0.35\nworldSounds=0\n")
    else:
        (case.take / "run/Gothic.ini").write_text("[SOUND]\nmusicVolume=0\n")
    with pytest.raises(dub.DubError, match="переснимите дубль"):
        dub.dub_take(CONFIG, case.shot, case.take)
    _assert_unchanged(before)


def test_take_dub_rejects_a_changed_brand_duration(take_workspace, monkeypatch):
    case = take_workspace
    before = _stale_outputs(case.take)

    def unexpected_voices(*args, **kwargs):
        raise AssertionError("changed timeline must be rejected before requesting voices")

    monkeypatch.setattr(dub, "english_voices", unexpected_voices)
    with pytest.raises(dub.DubError):
        dub.dub_take(CONFIG, replace(case.shot, brand=False), case.take)
    _assert_unchanged(before)


@pytest.mark.parametrize("invalid", [
    "events-missing", "main-missing", "subs-missing", "subs-incomplete", "language",
])
def test_take_dub_input_refusals_preserve_the_previous_publication(take_workspace, monkeypatch, invalid):
    case = take_workspace
    lang = "de" if invalid == "language" else "en"
    before = _stale_outputs(case.take)
    if lang != "en":
        before.update(_stale_outputs(case.take, lang))
    if invalid == "events-missing":
        (case.take / "events.jsonl").unlink()
    elif invalid == "main-missing":
        (case.take / "main.wav").unlink()
    elif invalid == "subs-missing":
        (case.take / "subs.en.json").unlink()
    elif invalid == "subs-incomplete":
        translations = json.loads((case.take / "subs.en.json").read_text())
        translations.pop(next(iter(translations)))
        (case.take / "subs.en.json").write_text(json.dumps(translations))

    def unexpected_work(*args, **kwargs):
        raise AssertionError("invalid inputs must be rejected before rendering or requesting voices")

    monkeypatch.setattr(cut, "render_effects", unexpected_work)
    monkeypatch.setattr(dub, "english_voices", unexpected_work)
    with pytest.raises(dub.DubError):
        dub.dub_take(CONFIG, case.shot, case.take, lang)
    _assert_unchanged(before)


def test_levels_rejection_removes_both_dub_outputs_and_keeps_the_failed_report(take_workspace, monkeypatch):
    case = take_workspace
    _stale_outputs(case.take)
    real_music_gain = levels.music_gain
    monkeypatch.setattr(levels, "music_gain", lambda *args: real_music_gain(*args) + 12)

    with pytest.raises(dub.DubError, match="check failed"):
        dub.dub_take(CONFIG, case.shot, case.take)

    _no_outputs(case.take)
    report = json.loads((case.take / "dub.en.levels.json").read_text())
    assert report["ok"] is False and report["error"]
    assert any(row["ok"] is False for row in report["rows"])
    assert "stale" not in report


@needs_ffmpeg
@pytest.mark.parametrize("music", [True, False], ids=["custom-plan-with-game-and-music", "without-music"])
def test_explainer_dub_renders_only_game_and_music_from_its_russian_plan(
    tmp_path, synthetic_stems, monkeypatch, music,
):
    ws = tmp_path / "explainer"
    ws.mkdir()
    plan = _plan(music=music, lang="ru")
    plan["gameVolume"] = 1
    plan_file = "plan.russian.json"
    (ws / plan_file).write_text(json.dumps(plan))
    (ws / "words.ru.json").write_text(json.dumps({"lines": [{
        "n": 1, "file": "voice/clean/01.wav", "words": plan["lines"][0],
    }]}))
    (ws / "script.en.txt").write_text("First | and last\n")
    calls = []

    def render(workspace, rendered_plan, directory, *, stems=(None, "voice", "game", "music")):
        calls.append((workspace, copy.deepcopy(rendered_plan), stems))
        directory.mkdir(parents=True, exist_ok=True)
        for name in stems:
            filename = f"{name}.wav" if name is not None else "picture.mp4"
            shutil.copyfile(synthetic_stems / filename, directory / filename)

    monkeypatch.setattr(explain, "render", render)
    russian = explain.publish(ws, plan_file)
    calls.clear()
    _stale_outputs(ws / "render")
    cloned = []

    def clone(config, workspace, texts_en, lang, tts=None, reclone=False):
        cloned.append((workspace, texts_en, lang, tts, reclone))
        return [_tts_sound(ws / "voice/en/raw/01.wav", seconds=2)]

    monkeypatch.setattr(dub, "clone", clone)
    real_publish = levels.publish
    forwarded = []

    def publish(joined, bed, voice, speech, total, length, out, **kwargs):
        assert _packet_hashes(joined, "v:0") == _packet_hashes(russian, "v:0")
        assert _audio_pcm(joined) == _audio_pcm(synthetic_stems / "game.wav")
        assert (bed is not None) is music
        if music:
            assert _audio_pcm(bed) == _audio_pcm(synthetic_stems / "music.wav")
        assert len(voice) == 1 and voice[0][1] == 0.0
        assert float(_streams(voice[0][0])[0]["duration"]) == pytest.approx(total, abs=1 / 48000)
        assert total == 12 and length == 14.2
        assert kwargs["music_fades"] is False
        assert kwargs["music_fade_durations"] == ((0.5, 0.5) if music else (0, 0))
        assert len(speech) == 1 and speech[0][0] == 4
        assert speech[0][1] - speech[0][0] == pytest.approx(2, abs=0.1)
        assert speech != [(4, 8)]
        forwarded.append(speech)
        return real_publish(joined, bed, voice, speech, total, length, out, **kwargs)

    monkeypatch.setattr(levels, "publish", publish)
    done = dub.dub_explainer(CONFIG, ws, plan_file=plan_file, tts="inworld", reclone=True)

    assert calls == [(ws, plan, ("game", "music"))]
    assert cloned == [(ws, ["First and last"], "en", "inworld", True)]
    assert len(forwarded) == 1
    assert done.wav == ws / "render/dub.en.wav" and done.preview == ws / "render/dub.en.mp4"
    assert not done.too_long
    _assert_publication(done, russian)


@needs_ffmpeg
def test_the_explainer_clone_is_cached_and_falls_back(tmp_path, monkeypatch, voices):
    ws = tmp_path
    for n in ("01", "02"):
        _sound(ws / f"voice/clean/{n}.wav", 1.5)
    asked = []

    def say(config, voice_id, description, lines, *, provider=None, fallback=None, clone=None, keep=None):
        assert (voice_id, clone, provider) == ("USER", voices / "user.inworld.wav", "inworld:language=en-US")
        asked.extend(lines)
        for path in lines.values():
            _sound(path, 1.0)

    monkeypatch.setattr(dub, "say", say)
    first = dub.clone(CONFIG, ws, ["One two", "Three"], "en")
    assert asked == ["One two", "Three"] and all(path.is_file() for path in first)
    assert dub.clone(CONFIG, ws, ["One two", "Three"], "en") == first
    assert asked == ["One two", "Three"]

    def voxcpm(ref, lines, work, design=None):
        assert ref == voices / "user.voxcpm.wav" and design is None
        asked.extend(lines)
        for path in lines.values():
            _sound(path, 1.0)

    monkeypatch.setattr(dub, "voxcpm", voxcpm)
    assert dub.clone(CONFIG, ws, ["One two", "Three"], "en", tts="voxcpm") == first
    assert asked == ["One two", "Three", "One two", "Three"]


def test_cli_keeps_too_long_line_exit_code_and_reports_voice_mastered(tmp_path, monkeypatch, capsys):
    done = dub.Dub(tmp_path / "dub.en.wav", tmp_path / "dub.en.mp4", 12, 12, -14.1, -13.9)
    done.rows.append("1.00 hero 3.0/2.0 s x1.15 +0.0 dB A long line")
    done.too_long.append("1.0 s, hero: 3.0 s at x1.15 for 2.0 s: A long line")
    monkeypatch.setattr(cli, "_shot", lambda short: object())
    monkeypatch.setattr(cli, "_config", lambda: CONFIG)
    monkeypatch.setattr(cli, "_take", lambda *args: tmp_path)
    monkeypatch.setattr(cli, "dub_take", lambda *args: done)
    monkeypatch.setattr(sys, "argv", ["gothic-shorts", "dub", "probe"])

    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    assert exit_info.value.code == 1
    output = capsys.readouterr().out
    assert "too long even sped up" in output and "A long line" in output
    assert "voice_mastered" in output and "-14.1" in output and "-13.9" in output
    assert str(done.wav) in output and str(done.preview) in output and "12.000" in output

@needs_ffmpeg
def test_the_users_voice_is_kept_for_every_short_and_replaced_only_on_demand(tmp_path, voices):
    first, second = tmp_path / "first", tmp_path / "second"
    _sound(first / "voice/clean/01.wav", 9.0, "0.1*sin(2*PI*200*t)")
    _sound(second / "voice/clean/01.wav", 9.0, "0.1*sin(2*PI*300*t)")
    ref = dub.user_voice(first, "voxcpm", dub.REFERENCE_S)
    assert ref == voices / "user.voxcpm.wav"
    kept = ref.read_bytes()
    # another short: the same voice
    assert dub.user_voice(second, "voxcpm", dub.REFERENCE_S).read_bytes() == kept
    dub.user_voice(second, "voxcpm", dub.REFERENCE_S, anew=True)
    assert ref.read_bytes() != kept  # --reclone: from this short's recordings
    with pytest.raises(dub.DubError, match="no recorded lines"):
        dub.user_voice(tmp_path / "empty", "inworld", dub.REFERENCE_INWORLD_S)
