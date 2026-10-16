from typing import Any

import pytest

from gothic_ai.validator import Caps, Rules, Story, validate
from tests import test_director
from tests.test_director import HILDA, LOBART, QUEST, _repair_node, _state

graph = test_director.graph


def _set(var: str) -> dict[str, Any]:
    return {"do": "set", "target": ["var", var], "value": ["int", 2]}


def _dialog(**change: Any) -> dict[str, Any]:
    return _repair_node(
        id="DIR_Q1_GIVE",
        condition=None,
        effects=[],
        **change,
    )


@pytest.mark.parametrize(
    "op",
    [
        _repair_node(id="DIR_Q1_GIVE", condition=None, effects=[{"do": "set", "var": QUEST, "value": 2}]),
        _dialog(program=[_set(QUEST)]),
        _dialog(
            program=[
                {
                    "do": "choice",
                    "text": "Хорошо.",
                    "steps": [
                        {"do": "if", "cond": ["int", 1], "then": [_set(QUEST)], "else": [_set(QUEST)]}
                    ],
                }
            ]
        ),
        {"op": "program", "node": "DIA_HILDA_HALLO", "steps": [_set(QUEST)]},
        {
            "op": "trigger",
            "id": "DIR_Q1_WAIT",
            "on": "state",
            "when": ["int", 1],
            "steps": [{"do": "if", "cond": ["int", 1], "then": [], "else": [_set(QUEST)]}],
        },
    ],
    ids=["effects", "program", "nested_choice_and_if", "script_program", "trigger"],
)
def test_a_book_quest_cannot_write_script_variables(graph, op):
    story = Story(graph, _state())
    errors = validate([op], story, Rules.of(graph), book=True)
    assert any("квест книги" in error and QUEST in error for error in errors), errors
    # A repair still has the right to write the same variable.
    assert validate([op], story, Rules.of(graph), repairs=QUEST) == []


def test_a_book_quest_can_write_its_director_variables_everywhere(graph):
    story = Story(graph, _state())
    ops = [
        {"op": "vars", "vars": {"DIR_Q1": 0}},
        _repair_node(
            id="DIR_Q1_GIVE",
            condition=None,
            effects=[{"do": "set", "var": "DIR_Q1", "value": 1}],
            program=[
                {
                    "do": "choice",
                    "text": "Хорошо.",
                    "steps": [{"do": "if", "cond": ["int", 1], "then": [_set("DIR_Q1")]}],
                }
            ],
        ),
        {"op": "trigger", "id": "DIR_Q1_WAIT", "on": "state", "when": ["int", 1], "steps": [_set("DIR_Q1")]},
    ]
    assert validate(ops, story, Rules.of(graph), book=True) == []


def test_an_ordinary_repair_can_still_finish_a_script_quest(graph):
    story = Story(graph, _state(dead={LOBART}))
    assert validate(
        [_repair_node(HILDA)],
        story,
        Rules.of(graph),
        repairs=QUEST,
        caps=Caps(xp=50, items={"ITMI_GOLD": 5}),
    ) == []
