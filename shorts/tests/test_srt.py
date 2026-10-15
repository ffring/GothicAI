import json

import pytest
from test_timeline import EVENTS

from gothic_shorts import cut, srt
from gothic_shorts.shot import Shot


def test_timestamps():
    assert srt.timestamp(0) == "00:00:00,000"
    assert srt.timestamp(3725.4567) == "01:02:05,457"


def test_cues_numbered_in_time_order():
    text = srt.render([srt.Cue(2.0, 3.0, "b"), srt.Cue(0.5, 1.25, " a ")])
    assert text == "1\n00:00:00,500 --> 00:00:01,250\na\n\n2\n00:00:02,000 --> 00:00:03,000\nb\n"


def test_explainer_lines_over_their_russian_voice():
    words = {"lines": [
        {"words": [{"text": "В", "start": 1.0, "end": 1.2}, {"text": "Готике", "start": 1.2, "end": 4.0}]},
        {"words": [{"text": "Да", "start": 5.0, "end": 6.0}]},
    ]}  # fmt: skip
    cues = srt.explainer(words, ["In Gothic | you don't aim", "Yes"])
    assert [c.text for c in cues] == ["In Gothic", "you don't aim", "Yes"]
    # each chunk as long as its share of the letters: 9 and 13 of 22
    assert (cues[0].start, cues[0].end, cues[1].end) == (1.0, pytest.approx(1 + 3 * 9 / 22, abs=1e-3), 4.0)
    assert (cues[2].start, cues[2].end) == (5.0, 6.0)
    with pytest.raises(ValueError):
        srt.explainer(words, ["one line"])


def test_a_take_as_english_captions_on_the_cut(tmp_path):
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in EVENTS))
    shot = Shot(name="t", title="T", script="", hook="Я соврал")
    (tmp_path / "subs.en.json").write_text(json.dumps({
        "Я соврал": "I lied", "Ксардас": "Xardas", "Меня прислал Ли.": "Lee sent me.",
        "Ли? Не смеши меня.": "Lee? Don't make me laugh.", "Уходи.": "Go away.",
    }))  # fmt: skip
    text = cut.subtitles(shot, tmp_path, "en").read_text()
    cues = text.split("\n\n")
    assert cues[0] == "1\n00:00:00,000 --> 00:00:02,800\n[I lied]"
    assert "\nLee sent me." in cues[1]  # the hero's line has no name
    # the answer at 6.0 s of the recording is at 3.45 s of the cut (the wait was cut down)
    assert "00:00:03,450 -->" in text and "Xardas: Lee? Don't make me laugh." in text
    assert "[Xardas]" in text  # the caption, burnt in in Russian
    with pytest.raises(cut.CutError):
        (tmp_path / "subs.en.json").write_text(json.dumps({"Я соврал": "I lied"}))
        cut.subtitles(shot, tmp_path, "en")
