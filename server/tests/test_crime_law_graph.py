import copy
import os
from collections import Counter
from pathlib import Path

import pytest

from gothic_ai.crime_law_graph import (
    AMBIENT,
    LAWFUL,
    absolution_program,
    ambient_ops,
    ambient_program,
    counter_values,
    gate_nodes,
    init_ops,
    law_var,
    news_expr,
    project_program,
    projection_values,
    validate_ops,
)
from gothic_ai.gamedata.storygraph import Node, StoryGraph, load, walk
from gothic_ai.validator import Patches, Rules, Story, WorldState, validate

ANDRE = "MIL_311_ANDRE"
PETZMASTER = "DIA_ANDRE_PETZMASTER"
GREATEST = ["fn", "B_GETGREATESTPETZCRIME", ["inst", "SELF"]]


@pytest.fixture
def graph():
    nodes = {
        PETZMASTER: Node(
            PETZMASTER,
            "dialog",
            ANDRE,
            "",
            [{"do": "line", "id": "DIA_Andre_Petzmaster_05_01"}],
            set(),
            condition=[">", GREATEST, ["var", "ANDRE_LASTPETZCRIME"]],
            permanent=True,
            important=True,
        ),
        AMBIENT: Node(
            AMBIENT,
            "dialog",
            "$INSTANCE_HELP",
            "",
            [],
            set(),
            condition=["!=", ["fn", "B_GETPLAYERCRIME", ["inst", "SELF"]], ["int", 0]],
        ),
    }
    return StoryGraph(nodes=nodes, functions={}, instances={}, vars={}, quests={})


def test_additive_lock_keeps_native_condition_and_effects(graph):
    before = copy.deepcopy(graph)
    ops = init_ops(graph)
    assert graph == before
    assert ops[1:] == [{"op": "condition", "node": PETZMASTER, "expr": ["var", law_var("city")]}]
    patched = Story(graph, WorldState(), Patches.of(ops))
    assert patched.graph.nodes[PETZMASTER].condition == before.nodes[PETZMASTER].condition
    assert patched.effects_of(PETZMASTER) == before.nodes[PETZMASTER].effects
    assert not patched.can_offer(PETZMASTER)
    assert validate_ops(ops, Story(graph, WorldState())) == []


def test_counters_count_deeds_not_stacks_and_project_zeros_for_other_kinds():
    crimes = [
        {"community": "city", "kind": "theft", "count": 23},
        {"community": "city", "kind": "murder"},
        {"community": "farm", "kind": "attack"},
    ]
    assert counter_values("city", crimes) == {
        "PETZCOUNTER_CITY_SHEEPKILLER": 0,
        "PETZCOUNTER_CITY_ATTACK": 0,
        "PETZCOUNTER_CITY_THEFT": 1,
        "PETZCOUNTER_CITY_MURDER": 1,
    }
    assert projection_values("city", crimes)["DIR_CRIME_PETZ_CITY_THEFT"] == 1
    assert counter_values("bl", crimes) == {}
    assert counter_values(None, crimes) == {}


def test_every_native_condition_and_information_runs_after_counter_projection():
    own = [
        {"do": "if", "cond": GREATEST, "then": [{"do": "return", "value": ["int", 1]}]},
        {"do": "return", "value": ["int", 0]},
    ]
    original = copy.deepcopy(own)
    projected = project_program("city", own)
    assert projected[1:] == own == original
    assert projected[0]["cond"] == ["var", law_var("city")]
    assert projected[0]["then"] == [
        {
            "do": "set",
            "target": ["var", f"PETZCOUNTER_CITY_{suffix}"],
            "value": ["var", f"DIR_CRIME_PETZ_CITY_{suffix}"],
        }
        for suffix in ("SHEEPKILLER", "ATTACK", "THEFT", "MURDER")
    ]
    projected[1]["then"].clear()
    assert own == original


@pytest.mark.parametrize("location, communities", [(2, ("city",)), (6, LAWFUL)])
def test_native_absolution_closes_projections_at_call_before_return(location, communities):
    native_call = {"do": "call", "call": ["fn", "B_GRANTABSOLUTION", ["int", location]]}
    own = [{"do": "if", "cond": ["int", 1], "then": [native_call, {"do": "return"}]}]
    original = copy.deepcopy(own)
    made = absolution_program(own)
    steps = made[0]["then"]
    assert steps[0] == native_call
    assert steps[-1] == {"do": "return"}
    written = {step["target"][1]: step["value"] for step in steps[1:-1]}
    assert written == {
        name: ["int", 0]
        for community in communities
        for name in [law_var(community), *projection_values(community, [])]
    }
    assert own == original


def test_absolution_rejects_a_program_without_the_native_call():
    with pytest.raises(ValueError, match="does not call"):
        absolution_program([{"do": "return"}])


def test_news_uses_actual_speaker_known_crime_and_keeps_native_voice(graph):
    player_crime = ["fn", "B_GETPLAYERCRIME", ["inst", "SELF"]]
    native_voice = {
        "do": "call",
        "call": ["fn", "B_SAY", ["inst", "SELF"], ["inst", "OTHER"], ["str", "$THEFT_CRIME"]],
    }
    own = [
        {"do": "if", "cond": ["==", player_crime, ["int", 3]], "then": [native_voice]},
        {"do": "return"},
    ]
    levels = {"VLK_400_MATTEO": 3, ANDRE: 2}
    expected = news_expr(levels)
    made = ambient_program(own, levels)
    assert made[0]["cond"] == ["==", expected, ["int", 3]]
    assert made[0]["then"] == own[0]["then"] == [native_voice]
    assert made[1] == own[1]
    assert player_crime in list(walk(own[0]["cond"]))
    assert all(e[:2] != ["fn", "B_GETPLAYERCRIME"] for e in walk(made[0]["cond"]))
    # Unknown NPCs fall through cases to zero. No mutable "current NPC" global can leak knowledge.
    assert expected[1] == ["==", ["call", "HLP_GETINSTANCEID", ["inst", "SELF"]], ["sym", ANDRE]]
    assert news_expr({}) == ["int", 0]
    ops = ambient_ops(graph, own, levels)
    assert {op["part"] for op in ops} == {"condition", "information"}
    assert all(op["node"] == AMBIENT for op in ops)


def test_scoped_validator_does_not_authorize_other_plot_dialogs(graph):
    assert validate_ops(
        [{"op": "program", "node": "DIA_HAGEN_HELLO", "steps": []}], Story(graph, WorldState())
    ) == ["unexpected community law program: DIA_HAGEN_HELLO"]
    assert validate_ops(
        [{"op": "condition", "node": PETZMASTER, "expr": ["int", 1]}], Story(graph, WorldState())
    ) == [f"unexpected community law condition: {PETZMASTER}"]


REAL_GRAPH = Path(os.environ.get("GOTHIC_STORY_GRAPH", Path.home() / ".local/share/gothic-ai/story.json"))


@pytest.fixture(scope="module")
def real_graph():
    if not REAL_GRAPH.exists():
        pytest.skip("needs actual storygraph export (GOTHIC_STORY_GRAPH)")
    return load(REAL_GRAPH)


def test_all_24_real_graph_locks_preserve_native_conditions_and_pass_validator(real_graph):
    native = {node.id: copy.deepcopy(node.condition) for node in real_graph.nodes.values()}
    nodes = gate_nodes(real_graph)
    assert len(nodes) == 24
    assert Counter(nodes.values()) == {"city": 17, "farm": 2, "monastery": 3, "oldcamp": 2}
    assert nodes["DIA_PARLAN_DONTDISTURB"] == "monastery"
    ops = init_ops(real_graph)
    story = Story(real_graph, WorldState())
    assert validate_ops(ops, story) == []
    patched = story.then(ops)
    assert all(real_graph.nodes[node].condition == native[node] for node in nodes)
    assert all(patched.patches.conditions[node] == [["var", law_var(group)]] for node, group in nodes.items())
    # Only the trusted crime service receives the law-dialog exception for Hagen, Lee and Garond.
    assert validate(ops, story, Rules.of(real_graph))


def test_real_native_fine_and_forgiveness_paths_are_retained(real_graph):
    for keeper in ("ANDRE", "HAGEN", "LEE", "PARLAN", "GAROND"):
        parent = real_graph.nodes[f"DIA_{keeper}_PETZMASTER"]
        debts = [e for e in parent.effects if e.get("do") == "set" and e.get("var") == f"{keeper}_SCHULDEN"]
        # One theft is B_GetTotalPetzCounter(self)*50; murder adds 500, sheep alone is flat 100.
        fifty = ["*", ["fn", "B_GETTOTALPETZCOUNTER", ["inst", "SELF"]], ["int", 50]]
        values = [e["value"] for e in debts]
        assert fifty in values
        assert ["+", fifty, ["int", 500]] in values
        assert ["int", 100] in values
        assert ["int", 1000] in values
        pay = real_graph.nodes[f"DIA_{keeper}_PETZMASTER_PAYNOW"]
        assert any(e.get("fn") == "B_GRANTABSOLUTION" for e in pay.effects)
    welcome = real_graph.nodes["DIA_PARLAN_WELCOME"]
    assert any(
        e.get("fn") == "B_GRANTABSOLUTION" and e.get("args") == [["int", 6, "LOC_ALL"]]
        for e in welcome.effects
    )
    thorben = [
        node.id
        for node in real_graph.nodes.values()
        if any(e.get("fn") == "B_THORBEN_DELETEPETZCRIMEGRITTA" for e in node.effects)
    ]
    assert len(thorben) == 3
    assert any(
        e.get("fn") == "B_MEMORIZEPLAYERCRIME" for e in real_graph.nodes["DIA_HILDA_PFANNETOOLATE"].effects
    )
