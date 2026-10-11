from gothic_ai.dialogue import SentenceSplitter, to_game_text
from gothic_ai.gamedata.ou import npc_dialog_key, npc_lines, parse_ou_bin
from gothic_ai.persona import pick_samples, render_instructions


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
