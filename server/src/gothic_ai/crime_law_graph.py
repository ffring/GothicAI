"""The community law's locks and projections around otherwise native dialog programs.

The bridge's existing graph operations are enough: an additive condition leaves the script's own
condition intact; a few stores before its original program prevent fresh vanilla perceptions from
polluting the counters while a report is still travelling. None of these ops belongs in the branch
patch log: the crime service supplies them as static patches, and --no-crimes supplies none.
"""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from .crimes import COMMUNITIES
from .gamedata.storygraph import StoryGraph, walk
from .programs import Step
from .validator import Rules, Story, validate

LAWFUL = tuple(key for key, community in COMMUNITIES.items() if community.lawkeepers)
SUFFIXES = {"sheep": "SHEEPKILLER", "attack": "ATTACK", "theft": "THEFT", "murder": "MURDER"}
CRIME_LEVELS = {"sheep": 1, "attack": 2, "theft": 3, "murder": 4}
LOCATIONS = {1: "oldcamp", 2: "city", 3: "monastery", 4: "farm"}
AMBIENT = "DIA_AMBIENT_NEWS"


def law_var(community: str) -> str:
    return f"DIR_CRIME_LAW_{community.upper()}"


def projection_var(counter: str) -> str:
    return counter.replace("PETZCOUNTER_", "DIR_CRIME_PETZ_", 1)


def counter_values(community: str, crimes: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """One counter increment per known, unpaid deed, irrespective of item stack size."""
    if community not in LAWFUL:
        return {}
    counts = Counter(crime.get("kind") for crime in crimes if crime.get("community", community) == community)
    return {f"PETZCOUNTER_{community.upper()}_{suffix}": counts[kind] for kind, suffix in SUFFIXES.items()}


def projection_values(community: str, crimes: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    return {projection_var(counter): count for counter, count in counter_values(community, crimes).items()}


def gate_nodes(graph: StoryGraph) -> dict[str, str]:
    """Every actual condition that reads the greatest PETZ crime, including guards' passage dialogs."""
    keepers = {npc: key for key, group in COMMUNITIES.items() for npc in group.lawkeepers}
    guards = {
        "MIL_305_TORWACHE": "city",
        "PAL_205_TORWACHE": "city",
        "MIL_310_STADTWACHE": "city",
        "MIL_333_STADTWACHE": "city",
    }
    result = {}
    for node in sorted(graph.nodes.values(), key=lambda n: n.id):
        if not any(e[:2] == ["fn", "B_GETGREATESTPETZCRIME"] for e in walk(node.condition)):
            continue
        community = keepers.get(node.npc) or guards.get(node.npc)
        if community is None:
            raise ValueError(f"unmapped community law dialog: {node.id} ({node.npc})")
        result[node.id] = community
    return result


def init_ops(graph: StoryGraph) -> list[dict[str, Any]]:
    variables = {law_var(community): 0 for community in LAWFUL}
    for community in LAWFUL:
        variables.update(projection_values(community, []))
    return [
        {"op": "vars", "vars": variables},
        *(
            {"op": "condition", "node": node, "expr": ["var", law_var(community)]}
            for node, community in gate_nodes(graph).items()
        ),
    ]


def project_program(community: str, own: list[Step]) -> list[Step]:
    """Refresh native PETZ immediately before executing the untouched native program.

    Apply to each gated condition and to PETZMASTER's information. A server tick alone would leave
    a window in which native perceptions could add an unreported crime to an already open law.
    """
    return [
        {
            "do": "if",
            "cond": ["var", law_var(community)],
            "then": [
                {"do": "set", "target": ["var", counter], "value": ["var", projection_var(counter)]}
                for counter in counter_values(community, [])
            ],
        },
        *copy.deepcopy(own),
    ]


def absolution_program(own: list[Step]) -> list[Step]:
    """Close our locks immediately after each successful native GrantAbsolution call.

    Insert at the call site, before any following native return, so guards cannot restore the
    counters from an old projection between native payment and the server's dialog_chosen event.
    LOC_ALL (Parlan's welcome) closes every community; the original call and voices stay intact.
    """
    result = copy.deepcopy(own)
    found = False

    def visit(steps: list[Step]) -> list[Step]:
        nonlocal found
        out = []
        for step in steps:
            if step.get("do") == "if":
                for branch in ("then", "else"):
                    if branch in step:
                        step[branch] = visit(step[branch])
            out.append(step)
            call = step.get("call", [])
            if step.get("do") != "call" or call[:2] != ["fn", "B_GRANTABSOLUTION"]:
                continue
            location = call[2] if len(call) > 2 else None
            if not isinstance(location, list) or location[:1] != ["int"]:
                raise ValueError("native absolution's location must be a constant")
            communities = LAWFUL if location[1] == 6 else (LOCATIONS[location[1]],)
            found = True
            for community in communities:
                values = {law_var(community): 0, **projection_values(community, [])}
                out.extend(
                    {"do": "set", "target": ["var", name], "value": ["int", value]}
                    for name, value in values.items()
                )
        return out

    result = visit(result)
    if not found:
        raise ValueError("native program does not call B_GRANTABSOLUTION")
    return result


def news_expr(levels: Mapping[str, int]) -> list:
    """A branch-derived knowledge lookup for the actual speaker, never a shared current-NPC global.

    Instance expressions cannot be compared by this engine's evaluator; Hlp_GetInstanceID and sym
    both produce the script symbol's integer index. Unlisted speakers receive CRIME_NONE.
    """
    expr: list = ["cases"]
    for npc, level in sorted(levels.items()):
        if level not in CRIME_LEVELS.values():
            continue
        expr.extend(
            [
                ["==", ["call", "HLP_GETINSTANCEID", ["inst", "SELF"]], ["sym", npc.upper()]],
                ["int", level],
            ]
        )
    return expr if len(expr) > 1 else ["int", 0]


def ambient_program(own: list[Step], levels: Mapping[str, int]) -> list[Step]:
    """Keep native lines and guards; only the source of the speaker's crime knowledge changes."""
    replacement = news_expr(levels)

    def rewrite(value: Any) -> Any:
        if isinstance(value, list):
            if value == ["fn", "B_GETPLAYERCRIME", ["inst", "SELF"]]:
                return copy.deepcopy(replacement)
            return [rewrite(part) for part in value]
        if isinstance(value, dict):
            return {key: rewrite(part) for key, part in value.items()}
        return value

    return rewrite(own)


def ambient_ops(
    graph: StoryGraph, own_information: list[Step], levels: Mapping[str, int]
) -> list[dict[str, Any]]:
    if AMBIENT not in graph.nodes:
        return []
    condition = [{"do": "return", "value": graph.nodes[AMBIENT].condition}]
    return [
        {"op": "program", "node": AMBIENT, "part": part, "steps": ambient_program(own, levels)}
        for part, own in (("condition", condition), ("information", own_information))
    ]


def validate_ops(ops: list[dict[str, Any]], story: Story) -> list[str]:
    """Run the ordinary validator with a narrow exception for the lawkeepers' law dialogs.

    Their quest dialogs remain protected, as do plot variables and chapter changes. The director's
    general validator rules are never changed: only these server-authored law operations are admitted.
    """
    nodes = gate_nodes(story.graph)
    allowed_programs = {
        *(str(node) for node in nodes),
        *(str(node) + "_PAYNOW" for node in nodes if node.endswith("_PETZMASTER")),
        "DIA_PARLAN_WELCOME",
        AMBIENT,
    }
    for op in ops:
        if op.get("op") == "vars":
            allowed_vars = init_ops(story.graph)[0]["vars"]
            if op.get("vars") != allowed_vars:
                return ["unexpected community law variables"]
        elif op.get("op") == "condition":
            node = op.get("node")
            if node not in nodes or op.get("expr") != ["var", law_var(nodes[node])]:
                return [f"unexpected community law condition: {node}"]
        elif op.get("op") == "program":
            if op.get("node") not in allowed_programs:
                return [f"unexpected community law program: {op.get('node')}"]
        else:
            return [f"unexpected community law operation: {op.get('op')}"]

    class LawRules(Rules):
        def plot_npc(self, npc: str) -> bool:
            return False  # exact law node allowlist above; plot_nodes/plot_vars still apply

    rules = Rules.of(story.graph)
    scoped = LawRules(rules.plot_npcs, rules.plot_vars, rules.plot_nodes)
    return validate(ops, story, scoped)
