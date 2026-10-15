import copy
import json
from pathlib import Path

import pytest

from gothic_ai import characters
from gothic_ai.characters import Card
from gothic_ai.gamedata.storygraph import StoryGraph, load
from gothic_ai.llm import ToolCall
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.persona import PersonaProvider, render_instructions
from tests.test_director import GRAPH, HILDA, LOBART, XARDAS, _line, _node

TRADER = "VLK_416_MATTEO"


@pytest.fixture
def graph(tmp_path: Path) -> StoryGraph:
    g = copy.deepcopy(GRAPH)
    g["nodes"] += [
        _node(
            "DIA_MATTEO_TRADE",
            TRADER,
            [_line("Смотри, но руками не трогай."), _line("Что берёшь?", "OTHER")],
            trade=True,
        ),
        _node(
            "DIA_MATTEO_PRICE", TRADER, [_line("Дёшево не отдам, у меня товар хороший, не то что у Зуриса.")]
        ),
    ]
    path = tmp_path / "story.json"
    path.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    return load(path)


PEOPLE = [
    {
        "npc": TRADER,
        "name": "Маттео",
        "guild": 3,
        "routine": [{"state": "ZS_STAND_ARMSCROSSED"}, {"state": "ZS_SLEEP"}],
    },
    {"npc": "VLK_999_NOBODY", "name": "Никто", "guild": 3, "routine": []},  # no lines of its own
]


def _card(npc: str = TRADER, **change) -> dict:
    card = {
        "npc": npc,
        "ru": "Ты — Маттео, торговец с рыночной площади. Прижимист и горд своим товаром.",
        "en": "A proud, tight-fisted market trader.",
        "greed": 5,
        "honesty": 3,
        "courage": 2,
        "talk": 3,
        "pushy": 2,
        "strangers": 2,
    }
    return {**card, **change}


def test_what_the_model_is_told_of_an_npc(graph):
    (s,) = characters.subjects(graph, PEOPLE)
    assert (s.npc, s.name, s.guild) == (TRADER, "Маттео", 3)
    assert s.does == ["торгует"] and s.day == ["sleep", "stand armscrossed"]
    # its own lines only, not the hero's
    assert "Смотри, но руками не трогай." in s.lines and "Что берёшь?" not in s.lines
    text = s.text()
    assert text.startswith(f"## {TRADER} — Маттео, горожанин Хориниса") and "Для героя: торгует." in text
    assert characters.subjects(graph, PEOPLE, only={LOBART}) == []


async def test_cards_from_the_models_answer(graph):
    subjects = characters.subjects(graph, PEOPLE)
    llm = FakeBackend(script=[FakeStep("", [ToolCall("write_cards", {"cards": [_card()]}, "c1")])], delay=0)
    cards, errors = await characters.write(llm, subjects)
    assert errors == [] and cards[TRADER].greed == 5 and cards[TRADER].name == "Маттео"
    assert "Маттео" in llm.calls[0][1][0].content and "Никакого сюжета" in llm.calls[0][0]
    # out of the list, a scale out of 1..5, nothing at all
    bad = [_card(npc=HILDA), _card(greed=7)]
    llm = FakeBackend(script=[FakeStep("", [ToolCall("write_cards", {"cards": bad}, "c1")])], delay=0)
    cards, errors = await characters.write(llm, subjects)
    assert cards == {} and any("not in the list" in e for e in errors) and any("1..5" in e for e in errors)
    cards, errors = await characters.write(FakeBackend("нет", delay=0), subjects)
    assert cards == {} and errors == ["no write_cards call"]


def test_a_card_in_the_persona(tmp_path):
    card = Card(TRADER, "Маттео", **{k: v for k, v in _card().items() if k != "npc"})
    path = tmp_path / "characters.json"
    characters.save({TRADER: card}, path)
    assert characters.load(path) == {TRADER: card}
    assert characters.load(tmp_path / "none.json") == {}

    assert card.traits_ru() == (
        "Твой характер: жадность — очень высокая; честность — средняя; смелость — низкая; "
        "разговорчивость — средняя; назойливость — низкая; к чужакам — подозрителен."
    )
    text = render_instructions(TRADER, "Маттео", [], card)
    assert card.ru in text and "жадность — очень высокая" in text and "житель острова" not in text
    # a hand-written biography goes first
    assert "некромант" in render_instructions(XARDAS, "Ксардас", [], card)

    personas = PersonaProvider(None, {TRADER: card})
    p = personas.get(TRADER, "Маттео")
    assert p.traits == "A proud, tight-fisted market trader." and card.ru in p.instructions
    assert "житель острова" in personas.get(LOBART, "Лобарт").instructions  # no card


def test_the_voices_command_prints_the_tts_voices(capsys):
    """`gothic-ai voices`: what the shorts' dub designs its English voices from."""
    import argparse

    from gothic_ai import cli
    from gothic_ai.persona import DEFAULT_VOICE, VOICES

    assert cli.voices(argparse.Namespace(npc=["none_100_xardas", "NO_SUCH_NPC"], game_dir=None)) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["NONE_100_XARDAS"] == {"voice_id": "NONE_100_XARDAS", "voice": VOICES["NONE_100_XARDAS"]}
    assert got["NO_SUCH_NPC"]["voice"] == DEFAULT_VOICE
