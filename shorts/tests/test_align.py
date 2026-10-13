import pytest

from gothic_shorts.align import AlignError, normalise, setting

TEXT = "Ксардас, меня прислал Ли. Он — кто-то?"


def test_samples_become_seconds_and_the_words_keep_the_text_punctuation():
    raw = [  # audio.cpp's --words-out: the aligner's words without punctuation, in samples of 16 kHz
        {"start_sample": 4000, "end_sample": 12000, "word": "Ксардас", "confidence": 1},
        {"start_sample": 12800, "end_sample": 16000, "word": "меня", "confidence": 1},
        {"start_sample": 16000, "end_sample": 21000, "word": "прислал", "confidence": 1},
        {"start_sample": 21000, "end_sample": 24000, "word": "Ли", "confidence": 1},
        {"start_sample": 30000, "end_sample": 32000, "word": "Он", "confidence": 1},
        {"start_sample": 32000, "end_sample": 36005, "word": "ктото", "confidence": 1},
    ]
    got = normalise(raw, TEXT)
    assert got["text"] == TEXT
    assert got["words"][0] == {"text": "Ксардас,", "start": 0.25, "end": 0.75}
    assert [w["text"] for w in got["words"]] == ["Ксардас,", "меня", "прислал", "Ли.", "Он", "кто-то?"]
    assert got["words"][-1]["end"] == 2.25  # rounded to the millisecond


def test_words_that_do_not_match_the_text_stay_as_the_aligner_wrote_them():
    raw = {"sample_rate": 8000, "words": [{"start_sample": 800, "end_sample": 1600, "word": "Ксардас"}]}
    got = normalise(raw, TEXT)
    assert got["words"] == [{"text": "Ксардас", "start": 0.1, "end": 0.2}]


def test_seconds_and_milliseconds_are_read_too():
    assert normalise([{"text": "Ли", "start": 0.5, "end": 0.75}], "Ли!")["words"] == [
        {"text": "Ли!", "start": 0.5, "end": 0.75}
    ]
    assert normalise([{"word": "Ли", "start_ms": 500, "end_ms": 750}], "Ли")["words"][0]["end"] == 0.75


def test_no_word_list_is_an_error():
    with pytest.raises(AlignError):
        normalise({"words": "oops"}, TEXT)


def test_the_ssh_destination_comes_from_the_environment_first_then_the_nearest_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OTHER=1\nexport GOTHIC_GPU_SSH='me@gpu'\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.delenv("GOTHIC_GPU_SSH", raising=False)
    assert setting("GOTHIC_GPU_SSH", nested) == "me@gpu"
    monkeypatch.setenv("GOTHIC_GPU_SSH", "you@gpu")
    assert setting("GOTHIC_GPU_SSH", nested) == "you@gpu"
