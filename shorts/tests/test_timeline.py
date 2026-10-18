import pytest

from gothic_shorts.timeline import build


def ev(frame: int, kind: str, **kw) -> dict:
    return {"t": frame * 33, "seg": "main", "frame": frame, "kind": kind, **kw}


EVENTS = [
    ev(0, "rec", fps=30),
    ev(0, "caption", text="Ксардас"),
    ev(30, "hero", wav="/x/1.wav", ms=2000, text="Меня прислал Ли."),
    ev(102, "hero_end"),
    ev(110, "heard", text="Меня прислал Ли", to="Ксардас"),
    # the AI thinks: 3 s after the phrase (frame 90) the answer starts
    ev(180, "npc_line", npc="XARDAS", name="Ксардас", text="Ли? Не смеши меня.", ms=5000),
    ev(240, "npc_voice_end", npc="XARDAS", ms=2000),
    ev(250, "npc_line", npc="XARDAS", name="Ксардас", text="Уходи.", ms=4000),
    ev(270, "npc_voice_end", npc="XARDAS", ms=700),
    ev(300, "caption", text=""),
    ev(330, "rec_stop", frames=330),
]


def test_lines_end_when_their_voice_has_played_out():
    tl = build(EVENTS)
    assert tl.duration == pytest.approx(11.0)
    hero, first, second = tl.lines
    assert (hero.hero, hero.start, hero.end, hero.wav) == (True, 1.0, 3.0, "/x/1.wav")
    assert (first.name, first.start, first.end) == ("Ксардас", 6.0, 8.0)
    assert (second.start, second.end) == (pytest.approx(250 / 30), 9.0)
    assert tl.heard == ["Меня прислал Ли"]


def test_the_ai_thinking_is_cut_down_to_a_beat():
    tl = build(EVENTS)
    # from the end of the hero's phrase (3.0) to the answer (6.0), 0.45 s stay
    assert tl.cuts == [(pytest.approx(3.225), pytest.approx(5.775))]
    assert tl.keep() == [(0.0, pytest.approx(3.225)), (pytest.approx(5.775), pytest.approx(11.0))]
    assert tl.to_cut(6.0) == pytest.approx(3.45)
    assert tl.to_cut(4.0) == pytest.approx(3.225)  # inside the cut: where it was
    assert tl.cut_duration == pytest.approx(11.0 - 2.55)


def test_only_the_wait_for_the_first_answer_is_cut():
    quick = [
        ev(0, "rec"),
        ev(0, "hero", ms=1000, text="Эй"),
        ev(45, "npc_line", npc="A", name="A", text="Да?", ms=900),
        ev(90, "rec_stop", frames=90),
    ]
    assert build(quick).cuts == []  # half a second: a natural pause
    two = [
        ev(0, "rec"),
        ev(0, "hero", ms=1000, text="Эй"),
        ev(60, "npc_line", npc="B", name="B", text="Тсс", ms=500),
        ev(150, "npc_line", npc="A", name="A", text="Да?", ms=900),
        ev(200, "rec_stop", frames=200),
    ]
    # whoever answers first ends the wait; what happens between the answers is the scene
    assert build(two).cuts == [(pytest.approx(1.225), pytest.approx(1.775))]


def test_after_a_sound_said_at_once_the_wait_for_the_answer_is_cut():
    events = [
        ev(0, "rec"),
        ev(0, "hero", ms=1000, text="Орки напали на город!"),
        ev(36, "npc_line", npc="A", name="A", text="Что?!", ms=600, filler=True),
        ev(54, "npc_voice_end", npc="A", ms=600),
        ev(204, "npc_line", npc="A", name="A", text="Значит, Беллиар торопится.", ms=2000),
        ev(270, "rec_stop", frames=270),
    ]
    tl = build(events)
    hero, sound, answer = tl.lines
    assert (sound.filler, answer.filler) == (True, False)
    # the sound 0.2 s after the phrase stays; the model's 5 s after it go down to a beat
    assert tl.cuts == [(pytest.approx(2.025), pytest.approx(6.575))]
    events[2] = ev(36, "npc_line", npc="A", name="A", text="Что?!", ms=600)  # not a filler: the scene
    assert build(events).cuts == []


def test_captions_run_until_the_next_one_or_off():
    tl = build(EVENTS)
    assert [(c.text, c.start, c.end) for c in tl.captions] == [("Ксардас", 0.0, 10.0)]


def test_events_outside_the_recording_are_ignored(tmp_path):
    import json

    from gothic_shorts.timeline import read_events

    path = tmp_path / "events.jsonl"
    before = {**ev(5, "npc_line", npc="A", name="A", text="до записи", ms=1), "frame": -1, "seg": ""}
    rows = [ev(0, "rec"), before, ev(9, "rec_stop", frames=9), {**ev(0, "rec"), "seg": "other"}]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    assert [e["kind"] for e in read_events(path)] == ["rec", "rec_stop"]


def _silent_wav(path, duration, rate=24_000):
    import wave

    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * round(duration * rate))
    return str(path)


def test_real_wav_lengths_and_npc_samples_set_the_timeline(tmp_path):
    hero = _silent_wav(tmp_path / "hero.wav", 1.5)
    npc = _silent_wav(tmp_path / "npc.wav", 2.0)
    events = [
        ev(30, "hero", ms=500, wav=hero, text="Эй"),
        # Voice precedes its text event, with an internal ID rather than text.
        ev(156, "npc_voice", npc="A", line="ai-0001", wav=npc, sample=241_200),
        ev(156, "npc_line", npc="A", text="Да?", ms=9000),
        ev(285, "npc_voice_end", npc="A"),
        ev(300, "rec_stop", frames=300),
    ]
    tl = build(events)
    first, second = tl.lines
    assert (first.start, first.end) == (1.0, 2.5)
    assert (second.start, second.end, second.wav) == (5.025, 7.025, npc)
    assert tl.cuts == [(pytest.approx(2.725), pytest.approx(4.8))]


def test_npc_voice_pairing_handles_interleaved_symbols_and_same_frame(tmp_path):
    first = _silent_wav(tmp_path / "first.wav", 0.5)
    other = _silent_wav(tmp_path / "other.wav", 1.0)
    second = _silent_wav(tmp_path / "second.wav", 1.5)
    events = [
        ev(30, "npc_voice", npc="A", line="id-a1", wav=first, sample=48_000),
        ev(30, "npc_line", npc="A", text="Повтор", ms=5000),
        ev(30, "npc_voice", npc="B", line="id-b", wav=other, sample=48_000),
        ev(30, "npc_line", npc="B", text="Текст Б", ms=5000),
        ev(30, "npc_line", npc="A", text="Повтор", ms=5000),
        ev(30, "npc_voice", npc="A", line="id-a2", wav=second, sample=72_000),
        ev(150, "rec_stop", frames=150),
    ]
    tl = build(events)
    assert [(line.who, line.start, line.end, line.wav) for line in tl.lines] == [
        ("A", 1.0, 1.5, first),
        ("B", 1.0, 2.0, other),
        ("A", 1.5, 3.0, second),
    ]
