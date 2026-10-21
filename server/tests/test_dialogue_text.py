import pytest

from gothic_ai.dialogue import (
    MoodTag,
    MoodTags,
    SentenceSplitter,
    first_words,
    mood_of,
    paragraph_end,
    strip_said,
    to_game_text,
    tool_leak,
)
from gothic_ai.gamedata.ou import npc_dialog_key, npc_lines, parse_ou_bin
from gothic_ai.persona import pick_samples, render_instructions


@pytest.mark.parametrize(
    "text",
    [
        "give_item",
        "SET_INTENT(goal=follow)",
        "protoc function give_quest?",
        "FuNcTiOn",
        "FUNCTIONS",
        "functions.give_quest({...})",
        "to=functions.unknown",
        '"arguments": {}',
        "call_id=abc",
        "<tool_call>",
        '{"name": "unknown"}',
    ],
)
def test_tool_leak_recognizes_names_and_call_syntax(text):
    assert tool_leak(text, {"give_item", "set_intent", "give_quest"})


@pytest.mark.parametrize(
    "text",
    [
        "Дай мне функцию подумать.",
        "Это задание как раз по тебе.",
        "Отдам тебе булаву после боя.",
        "Xardas знает больше.",
        "Ладно, пошли.",
        "give_itemized preset_intent give_quests",
        "functional dysfunctional functions_extra",
    ],
)
def test_tool_leak_leaves_ordinary_words_and_name_substrings_alone(text):
    assert not tool_leak(text, {"give_item", "set_intent", "give_quest"})


def test_splitter_emits_whole_sentences_as_they_complete():
    s = SentenceSplitter(min_len=10)
    assert s.feed("Ты вернулся. Хоро") == ["Ты вернулся."]
    # "Хорошо!" is shorter than min_len: held back and merged with what follows
    assert s.feed("шо! Слушай") == []
    assert s.flush() == ["Хорошо! Слушай"]


def test_splitter_merges_short_sentences_and_keeps_order():
    s = SentenceSplitter(min_len=20)
    out = s.feed("Да. Нет. Возможно, когда-нибудь потом. ")
    assert out == ["Да. Нет. Возможно, когда-нибудь потом."]
    assert s.flush() == []


def test_splitter_flushes_tail_without_punctuation():
    s = SentenceSplitter()
    assert s.feed("Иди к лорду Хагену") == []
    assert s.flush() == ["Иди к лорду Хагену"]


def test_splitter_breaks_overlong_sentence_at_commas():
    s = SentenceSplitter(min_len=10, max_len=40)
    parts = s.feed("Драконы пришли с севера, орки идут с востока, паладины заперлись в городе. ")
    assert all(len(p) <= 40 for p in parts)
    assert " ".join(parts) == "Драконы пришли с севера, орки идут с востока, паладины заперлись в городе."


def _run(tags: MoodTags, *pieces: str) -> list[str | MoodTag]:
    out: list[str | MoodTag] = []
    for p in pieces:
        out += tags.feed(p)
    return out + tags.flush()


def test_mood_tags_come_out_of_the_text():
    out = _run(MoodTags(), "[angry] Опять ты? [neutral] Ладно, говори.")
    assert out == [MoodTag("angry"), " Опять ты? ", MoodTag("neutral"), " Ладно, говори."]


def test_mood_tag_split_across_pieces_waits_for_its_end():
    tags = MoodTags()
    assert tags.feed("[ang") == []
    assert tags.feed("ry] Оп") == [MoodTag("angry"), " Оп"]


def test_mood_tag_synonyms_and_stage_directions():
    assert mood_of(" Злость ") == "angry" and mood_of("испуг") == "afraid" and mood_of("HOSTILE") == "hostile"
    assert mood_of("задумчиво") is None
    # an unknown short tag is a stage direction: not said
    assert _run(MoodTags(), "[смеётся] Ха!") == [" Ха!"]


def test_long_brackets_and_unclosed_ones_are_text():
    long = "[" + "очень длинная ремарка, совсем не метка" + "]"
    assert "".join(p for p in _run(MoodTags(), long + " да") if isinstance(p, str)) == long + " да"
    assert _run(MoodTags(), "Цена [пять") == ["Цена ", "[пять"]


def test_game_text_drops_markdown_and_non_cp1251():
    assert to_game_text("**Ксардас** смотрит 🔥 на тебя…  «Иди»") == "Ксардас смотрит на тебя… «Иди»"


def _binsafe_line(name: str, text: str) -> bytes:
    raw = text.encode("cp1251")
    wav = f"{name}.WAV".encode()
    return (
        b"\x01\x05\x00ATOMB"
        + b"\x01"
        + len(raw).to_bytes(2, "little")
        + raw
        + b"\x12\x06\x00\x00\x00\x01"
        + len(wav).to_bytes(2, "little")
        + wav
    )


def test_parse_ou_bin_reads_subtitles_by_line_name():
    data = b"ZenGin Archive\nEND\n" + _binsafe_line("DIA_Xardas_Hello_14_01", "Наконец-то.")
    data += _binsafe_line("DIA_Xardas_Hello_15_02", "Что случилось?")
    lines = parse_ou_bin(data)
    assert lines == {"DIA_XARDAS_HELLO_14_01": "Наконец-то.", "DIA_XARDAS_HELLO_15_02": "Что случилось?"}
    # hero (voice 15) lines are not the npc's
    assert npc_lines(lines, "NONE_100_XARDAS") == [("DIA_XARDAS_HELLO_14_01", "Наконец-то.")]


def test_npc_dialog_key():
    assert npc_dialog_key("NONE_100_XARDAS") == "XARDAS"
    assert npc_dialog_key("VLK_408_Jora") == "JORA"
    assert npc_dialog_key("PC_Hero") == "HERO"


def test_persona_prompt_contains_samples_and_is_deterministic():
    samples = pick_samples([f"Реплика номер {i}, достаточно длинная для образца." for i in range(100)], n=5)
    assert len(samples) == 5
    a = render_instructions("NONE_100_XARDAS", "Ксардас", samples)
    assert a == render_instructions("NONE_100_XARDAS", "Ксардас", samples)
    assert "некромант" in a and samples[0] in a


def test_first_line_can_go_out_alone():
    text = "Ты вернулся. Хорошо. Времени у нас мало, так что слушай внимательно. Драконы уже здесь."
    default = SentenceSplitter()
    assert default.feed(text + " ") == [
        "Ты вернулся. Хорошо. Времени у нас мало, так что слушай внимательно."
    ]
    voiced = SentenceSplitter(first_min_len=1)
    lines = voiced.feed(text + " ") + voiced.flush()
    assert lines == [
        "Ты вернулся.",
        "Хорошо. Времени у нас мало, так что слушай внимательно.",
        "Драконы уже здесь.",
    ]


def test_first_line_at_the_first_clause():
    def lines(text: str) -> list[str]:
        sp = SentenceSplitter(first_clause=True)
        out = []
        for i in range(0, len(text), 3):  # streamed in small deltas
            out += sp.feed(text[i : i + 3])
        return out + sp.flush()

    assert lines("Ты вернулся из храма, и это хорошо. Теперь слушай меня внимательно.") == [
        "Ты вернулся из храма,",
        "и это хорошо. Теперь слушай меня внимательно.",
    ]
    # a short first sentence goes alone, a comma too early does not cut
    assert lines("Ты вернулся. Хорошо, теперь слушай меня внимательно.")[0] == "Ты вернулся."
    assert lines("Да, конечно. Драконы уже здесь, и их много.")[0] == "Да, конечно."
    # no punctuation: cut after a few words, never right after a preposition
    first = lines("Когда ты пойдёшь в город через северные ворота мимо стражи найди там Лестера.")[0]
    assert first == "Когда ты пойдёшь в город через северные ворота мимо стражи"
    first = lines("Последний крик Спящего призвал их вместе со всей армией тьмы на этот остров.")[0]
    assert first == "Последний крик Спящего призвал их вместе со всей армией тьмы"
    first = lines("Я долго ждал тебя здесь и думал о том что делать с этим.")[0]
    assert first == "Я долго ждал тебя здесь и думал о том что делать"  # "с" is not left at the end


def test_a_quick_line_ends_at_its_first_blank_line():
    assert paragraph_end("[hostile] Держи его!\n\nset_intent(goal=fight)") == len("[hostile] Держи его!")
    assert paragraph_end("\n\nДержи его!") is None  # blank lines before any words do not end it
    assert paragraph_end("Держи его!\nИ живо!") is None


def test_the_answer_does_not_say_the_first_words_again():
    assert strip_said("Хм... Ты прав.", "Хм...") == "Ты прав."
    assert strip_said("Что?! Орки напали?", "Что?!") == "Орки напали?"
    assert strip_said("Что? Орки?", "Что?!") == "Орки?"
    assert strip_said("Ну-ну. Так уж и голыми?", "Ну-ну...") == "Так уж и голыми?"
    assert strip_said("Хм, ну ладно.", "Тьфу!") == "Ну ладно."  # an interjection after any first words
    assert strip_said("М-м... Пожалуй.", "Хм...") == "Пожалуй."
    assert strip_said("Так, слушай.", "Так...") == "Слушай."
    assert strip_said("Так вот оно что.", "Так...") == "Так вот оно что."  # not an interjection here
    assert strip_said("Что ты сказал?", "Что?!") == "Что ты сказал?"
    assert strip_said("Хмурый сегодня день.", "Хм...") == "Хмурый сегодня день."
    assert strip_said("Хм.", "Хм.") == ""


def test_the_quick_models_first_words_are_its_first_sentence_without_tags():
    assert first_words("[afraid] Что?! Лестер мёртв?") == ("Что?!", "afraid")
    assert first_words("[thoughtful] Хм... Дай подумать") == ("Хм...", None)  # not a mood: only dropped
    assert first_words("Тьфу, ну и наглец!\n\nset_intent(...)") == ("Тьфу, ну и наглец!", None)
    assert first_words("Ишь ты") == ("Ишь ты", None)  # no end: what it wrote
    assert first_words("[neutral] —") == ("", None)  # nothing to say first
    assert first_words("— Пошли тогда.") == ("Пошли тогда.", None)
    assert first_words("") == ("", None)
