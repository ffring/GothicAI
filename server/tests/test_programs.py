import json
from pathlib import Path
from typing import Any

import pytest

from gothic_ai import programs
from gothic_ai.gamedata.storygraph import StoryGraph, load
from gothic_ai.validator import Rules, Story, validate
from tests.test_director import CAPS, GRAPH, HAS_20, HILDA, LOBART, QUEST, _repair_node, _state

ENDING = "DIA_LOBART_RUEBENRUNNING"
SAY = {"do": "say", "speaker": "self", "text": "Спасибо за репу."}
SUCCESS = {"do": "set", "target": ["var", QUEST], "value": ["int", 2, "LOG_SUCCESS"]}
XP = {"do": "call", "call": ["fn", "B_GIVEPLAYERXP", ["int", 50, "XP_LOBARTHOLRUEBEN"]]}

# Lobart's ending as story_program gives it, cut down
LOBART_ENDING = [
    {"do": "call", "call": ["call", "AI_OUTPUT", ["inst", "OTHER"], ["inst", "SELF"], ["str", "DIA_15_00"]]},
    {
        "do": "if",
        "cond": HAS_20,
        "then": [SUCCESS, XP],
        "else": [{"do": "call", "call": ["call", "AI_STOPPROCESSINFOS", ["inst", "SELF"]]}],
    },
    {"do": "return"},
]


@pytest.fixture
def graph(tmp_path: Path) -> StoryGraph:
    path = tmp_path / "story.json"
    path.write_text(json.dumps(GRAPH, ensure_ascii=False), encoding="utf-8")
    return load(path)


def _story(graph: StoryGraph, dead: frozenset[str] = frozenset()) -> Story:
    state = _state(dead=set(dead))
    state.symbols.update(
        {n: "func" for n in ("AI_OUTPUT", "AI_STOPPROCESSINFOS", "B_GIVEPLAYERXP", "B_KAPITELWECHSEL")}
    )
    return Story(graph, state)


def _program(steps: Any, node: str = ENDING, **extra: Any) -> dict[str, Any]:
    return {"op": "program", "node": node, "steps": steps, **extra}


def test_what_a_program_does_in_the_terms_of_the_lifted_model():
    assert programs.effects(LOBART_ENDING) == [
        {"do": "set", "var": QUEST, "value": ["int", 2, "LOG_SUCCESS"]},
        {"do": "xp", "amount": ["int", 50, "XP_LOBARTHOLRUEBEN"]},
    ]
    assert programs.names(LOBART_ENDING) == {
        "AI_OUTPUT",
        "AI_STOPPROCESSINFOS",
        "B_GIVEPLAYERXP",
        "NPC_HASITEMS",
        "ITPL_BEET",
        QUEST,
    }
    assert programs.render(LOBART_ENDING).splitlines() == [
        'AI_OUTPUT(other, self, "DIA_15_00")',
        "if NPC_HASITEMS(other, ITPL_BEET) >= 20",
        "  MIS_LOBART_RUEBEN = LOG_SUCCESS",
        "  B_GIVEPLAYERXP(XP_LOBARTHOLRUEBEN)",
        "else",
        "  AI_STOPPROCESSINFOS(self)",
        "return",
    ]


def test_a_condition_program_reads_as_an_expression_when_the_scripts_could_have_written_it():
    yes = {"do": "return", "value": ["int", 1, "TRUE"]}
    no = {"do": "return", "value": ["int", 0]}
    assert programs.condition([{"do": "if", "cond": HAS_20, "then": [yes]}, no]) == HAS_20
    assert programs.condition([{"do": "return", "value": HAS_20}]) == HAS_20
    assert programs.condition([SUCCESS, {"do": "return", "value": HAS_20}]) is None  # writes: not a condition


def test_the_scripts_ending_as_a_program_keeps_the_quest_finishable(graph):
    story = _story(graph)
    ops = [_program(LOBART_ENDING)]
    assert validate(ops, story, Rules.of(graph)) == []
    assert story.then(ops).endings(QUEST) == [ENDING]


def test_a_program_that_no_longer_ends_the_quest_is_rejected(graph):
    story = _story(graph)
    ops = [_program([SAY, {"do": "return"}])]
    assert validate(ops, story, Rules.of(graph)) == [f"после правки квест {QUEST} остаётся без концовки"]
    # the scripts' own program again: nothing to reject
    back = [_program([SAY, {"do": "return"}]), _program(None)]
    assert validate(back, story, Rules.of(graph)) == []
    assert story.then(back).endings(QUEST) == [ENDING]


def test_a_program_may_end_a_quest_from_another_dialog(graph):
    story = _story(graph)
    ops = [_program([SAY, SUCCESS], node="DIA_LOBART_WORKNOW")]
    assert validate(ops, story, Rules.of(graph)) == []
    assert story.then(ops).endings(QUEST) == ["DIA_LOBART_RUEBENRUNNING", "DIA_LOBART_WORKNOW"]
    # and the values the quest may take come from it too
    assert 2 in story.then(ops).var_values(QUEST)


def test_a_condition_the_director_writes_can_close_the_ending(graph):
    never = [{"do": "return", "value": ["<", ["var", "KAPITEL"], ["int", 1]]}]
    story = _story(graph)
    errors = validate([_program(never, part="condition")], story, Rules.of(graph))
    assert errors == [f"после правки квест {QUEST} остаётся без концовки"]
    # a condition in another shape may come true
    odd = [{"do": "set", "target": ["var", "MIS_OTHER"], "value": ["int", 1]}, *never]
    assert validate([_program(odd, part="condition")], story, Rules.of(graph)) == []


@pytest.mark.parametrize(
    ("op", "why"),
    [
        (_program([{"do": "set", "target": ["var", "KAPITEL"], "value": ["int", 2]}, SUCCESS]), "главы"),
        (_program([{"do": "call", "call": ["fn", "B_KAPITELWECHSEL", ["int", 2]]}, SUCCESS]), "меняет главу"),
        (_program([{"do": "set", "target": ["var", "MIS_NOPE"], "value": ["int", 1]}, SUCCESS]), "неизвест"),
        (_program([SUCCESS], node="DIA_XARDAS_FIRSTEXIT"), "меняет главу"),
        (_program([SUCCESS], node="DIA_NOPE"), "нет такого диалога"),
        (_program([SUCCESS], node="DIA_LOBART_RUEBENRUNNING_GOLD", part="condition"), "нет условия"),
        (_program([SUCCESS], part="dialog"), "часть диалога"),
        (_program({"do": "return"}), "список шагов"),
        (_program([{"do": "say", "speaker": "hero", "text": "Эй"}, SUCCESS]), "кто говорит"),
        (_program([{"do": "say", "speaker": "self", "text": " "}, SUCCESS]), "пустая"),
    ],
)
def test_the_validator_says_why_not_for_programs(graph, op, why):
    errors = validate([op], _story(graph), Rules.of(graph))
    assert any(why in e for e in errors), errors


def test_a_dialog_of_the_director_may_have_a_program(graph):
    program = [SAY, {"do": "say", "speaker": "other", "text": "Держи."}, SUCCESS, XP]
    ops = [_repair_node(lines=[], effects=[], program=program)]
    story = _story(graph, dead=frozenset({LOBART}))
    assert validate(ops, story, Rules.of(graph), repairs=QUEST) == []
    assert story.then(ops).dead_ends() == []
    assert story.then(ops).effects_of("DIR_MIS_LOBART_RUEBEN_1")[2] == programs.effects(program)[2]
    # a repair gives no more than the ending did: its effects say it, a program could do anything
    errors = validate(ops, story, Rules.of(graph), repairs=QUEST, caps=CAPS)
    assert "у ремонта нет своей программы: только эффекты исходной концовки" in errors
    # the lines of its program count as lines
    bare = [_repair_node(npc=HILDA, lines=[], effects=[{"do": "set", "var": QUEST, "value": 2}])]
    assert any("реплик" in e for e in validate(bare, story, Rules.of(graph), repairs=QUEST))
