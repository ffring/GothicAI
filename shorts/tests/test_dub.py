import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_timeline import EVENTS

from gothic_shorts import dub
from gothic_shorts.shot import Shot
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


def value(expr: str, t: float) -> float:
    """An ffmpeg expression of t, evaluated the way ffmpeg does it (clip, min, max)."""
    return eval(expr, {"clip": lambda x, a, b: min(max(x, a), b), "min": min, "max": max, "t": t})


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


def test_the_separated_sound_fades_in_around_the_voices():
    expr = dub.mask([(2.0, 3.0), (2.5, 4.0), (6.0, 7.0)], 0.1)
    assert expr.count("clip(") == 2  # the overlapping spans are one
    assert value(expr, 1.0) == 0
    assert value(expr, 1.95) == pytest.approx(0.5)  # halfway up its ramp
    assert value(expr, 2.0) == value(expr, 3.5) == value(expr, 6.5) == 1
    assert value(expr, 5.0) == 0
    assert dub.mask([], 0.1) == "0"


def test_the_music_ducks_under_the_english_lines_as_the_render_does():
    music = {"file": "m.wav", "volume": 0.2, "duck": 0.5, "fadeIn": 1.0, "fadeOut": 1.5}
    expr = dub.music_volume(music, 20.0, [(5.0, 8.0)])
    assert value(expr, 0.0) == 0  # fading in
    assert value(expr, 0.5) == pytest.approx(0.1)
    assert value(expr, 3.0) == pytest.approx(0.2)  # a pause
    assert value(expr, 6.0) == pytest.approx(0.1)  # under the line
    assert value(expr, 5.0 - dub.DUCK_RAMP_S / 2) == pytest.approx(0.15)
    assert value(expr, 20.0) == 0


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


@needs_ffmpeg
def test_a_take_dub_is_as_long_as_the_cut_and_as_loud(tmp_path, monkeypatch):
    take = tmp_path / "take"
    take.mkdir()
    (take / "events.jsonl").write_text("\n".join(json.dumps(e) for e in EVENTS))
    (take / "subs.en.json").write_text(json.dumps(SUBS))
    # the game's sound: a bed, the npc's voice over it from 6 to 9 s
    _sound(take / "main.wav", 11.0, "0.05*sin(2*PI*110*t)+if(between(t,6,9),0.2*sin(2*PI*440*t),0)")

    def separate(take, work):
        return _sound(work / "voices.wav", 11.0, "if(between(t,6,9),0.2*sin(2*PI*440*t),0)"), _sound(
            work / "rest.wav", 11.0, "0.05*sin(2*PI*110*t)"
        )

    def english_voices(config, voices, lines, cache, lang, tts=None):
        assert set(voices) == {"hero", "XARDAS"} and voices["XARDAS"] == ("XARDAS", "An old man")
        return {(who, t): _sound(cache / f"{abs(hash(t))}.wav", 1.0) for who, ts in lines.items() for t in ts}

    monkeypatch.setattr(dub, "separate", separate)
    monkeypatch.setattr(dub, "english_voices", english_voices)
    xardas = {"XARDAS": {"voice_id": "XARDAS", "voice": "An old man"}}
    monkeypatch.setattr(dub, "npc_voices", lambda config, npcs: xardas)
    monkeypatch.setattr(dub, "tools", lambda config: (tmp_path, Path("uv")))
    done = dub.dub_take(CONFIG, Shot(name="t", title="T", script="", hook="Я соврал"), take)

    tl = build(EVENTS)
    assert done.wav == take / "dub.en.wav" and done.preview is None  # no short-ru.mp4 to put it on
    assert _samples(done.wav) == round(tl.cut_duration * 48000)
    assert done.lufs == pytest.approx(dub.TARGET_LUFS, abs=0.5)
    assert len(done.rows) == 3 and not done.too_long
    assert "Lee? Don't make me laugh." in done.rows[1]


@needs_ffmpeg
def test_an_explainer_dub_over_its_music_bed(tmp_path, monkeypatch, voices):
    ws = tmp_path
    for n in ("01", "02"):
        _sound(ws / f"voice/clean/{n}.wav", 1.5)
    _sound(ws / "music/bed.wav", 2.0, "0.2*sin(2*PI*220*t)")
    (ws / "words.ru.json").write_text(json.dumps({"lines": [
        {"n": 1, "file": "voice/clean/01.wav", "words": [{"text": "Раз", "start": 0.5, "end": 1.8}]},
        {"n": 2, "file": "voice/clean/02.wav", "words": [{"text": "Два", "start": 3.0, "end": 4.3}]},
    ]}))  # fmt: skip
    plan = {"beats": [{"id": "a", "kind": "card", "dur": 3.0}, {"id": "b", "kind": "footage", "src": "f.mp4",
                                                                "dur": 3.0}],
            "gameVolume": 0, "music": {"file": "music/bed.wav", "volume": 0.2, "duck": 0.5}}  # fmt: skip
    (ws / "plan.json").write_text(json.dumps(plan))
    (ws / "script.en.txt").write_text("One | two\nThree\n")
    asked = []

    def say(config, voice_id, description, lines, *, provider=None, fallback=None, clone=None, keep=None):
        assert (voice_id, clone, provider) == ("USER", voices / "user.inworld.wav", "inworld:language=en-US")
        asked.extend(lines)
        for path in lines.values():
            _sound(path, 1.0)

    monkeypatch.setattr(dub, "say", say)
    done = dub.dub_explainer(CONFIG, ws)
    assert asked == ["One two", "Three"]
    assert _samples(done.wav) == 6 * 48000  # the plan's length
    assert done.lufs == pytest.approx(dub.TARGET_LUFS, abs=0.5)
    assert not done.too_long

    dub.dub_explainer(CONFIG, ws)  # unchanged lines are not voiced again
    assert asked == ["One two", "Three"]

    # without Inworld, VoxCPM clones from a reference its encoder takes
    def no_credits(*args, **kwargs):
        raise VoiceError("error: no voice: inworld unavailable")

    def voxcpm(ref, lines, work, design=None):
        assert ref == voices / "user.voxcpm.wav" and design is None  # the user's own voice: never designed
        asked.extend(lines)
        for path in lines.values():
            _sound(path, 1.0)

    monkeypatch.setattr(dub, "say", no_credits)
    monkeypatch.setattr(dub, "voxcpm", voxcpm)
    dub.dub_explainer(CONFIG, ws, tts="voxcpm")
    assert asked == ["One two", "Three", "One two", "Three"]  # another provider: voiced again

    plan["gameVolume"] = 1
    (ws / "plan.json").write_text(json.dumps(plan))
    with pytest.raises(dub.DubError, match="footage's own sound"):
        dub.dub_explainer(CONFIG, ws)


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
