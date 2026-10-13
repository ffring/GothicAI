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
