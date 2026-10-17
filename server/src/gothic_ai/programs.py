"""Programs: a dialog's script function as steps (docs/story-graph.md, «Исполнение без VM»).

The bridge runs the script functions of dialogs (conditions, informations, choices) as programs. The director
may put a program of its own in place of one (graph_patch `program`, v23) or give one to a dialog of its own
(`add_node.program`). Steps are JSON, as `story_program` returns them:
`{"do": "call", "call": expr}`, `{"do": "set", "target": expr, "value": expr}`,
`{"do": "if", "cond": expr, "then": [...], "else": [...]}`, `{"do": "return", "value": expr}` and
`{"do": "say", "speaker": "self" | "other", "line": id}` or with `"text"` for a line of its own; in the
director's own dialogs also `{"do": "choice", "id"?, "text": ..., "steps": [...]}` — a choice the dialog
offers after its lines, its steps run when the hero picks it (v24) — and `{"do": "clear_choices"}`.
Expressions are those of the graph (gamedata/storygraph.py), taken as written: nothing is folded.

Here: what a program does, in the terms the validator reads (the lifted model's effects).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .gamedata.storygraph import Expr, walk
from .gamedata.storygraph import render as render_expr

Step = dict[str, Any]

# the chapter changes in this script function (the validator leaves the plot alone)
CHAPTER_CHANGE = "B_KAPITELWECHSEL"


def steps_of(steps: list[Step] | None) -> Iterator[Step]:
    """Every step, those in branches too, in order."""
    for s in steps or []:
        yield s
        if s.get("do") == "if":
            yield from steps_of(s.get("then"))
            yield from steps_of(s.get("else"))
        elif s.get("do") == "choice":
            yield from steps_of(s.get("steps"))


def exprs_of(steps: list[Step] | None) -> Iterator[Expr]:
    """Every expression the steps compute, subexpressions included."""
    for s in steps_of(steps):
        for key in ("call", "target", "value", "cond"):
            if isinstance(s.get(key), list):
                yield from walk(s[key])


def effects(steps: list[Step] | None) -> list[dict[str, Any]]:
    """What the program may do, as the lifted model writes it: stores into globals
    (`{"do": "set", "var", "value"}`, the value as an expression), experience (`xp`), the chapter
    (`chapter`), what the hero gets (`give`: item, count or None) and lines (`line`); a branch may or may not
    run, so each is what it may do."""
    out: list[dict[str, Any]] = []
    for s in steps_of(steps):
        match s.get("do"):
            case "set" if (t := s.get("target")) and t[:1] == ["var"]:
                out.append({"do": "set", "var": str(t[1]).upper(), "value": s.get("value")})
            case "say":
                speaker = ["inst", "OTHER" if s.get("speaker") == "other" else "SELF"]
                said = {"id": s.get("line", ""), "text": s.get("text", "")}
                out.append({"do": "line", "speaker": speaker, **said})
    for e in exprs_of(steps):
        name = str(e[1]).upper() if len(e) > 1 else ""
        if e[0] == "fn" and name == CHAPTER_CHANGE:
            out.append({"do": "chapter", "chapter": e[2] if len(e) > 2 else None})
        elif e[0] == "fn" and name == "B_GIVEPLAYERXP" and len(e) > 2:
            out.append({"do": "xp", "amount": e[2]})
        elif (given := _given(e)) is not None:
            out.append({"do": "give", "item": given[0], "count": given[1]})
    return out


def _given(e: Expr) -> tuple[str, int | None] | None:
    """What the hero gets from the npc: B_GiveInvItems(self, other, item, n) or CreateInvItems(other, item,
    n), `hero` for `other` too (a trigger's program, where other may be someone else); the count None if it
    is not a number."""
    name = str(e[1]).upper() if len(e) > 1 else ""
    hero = (["inst", "OTHER"], ["inst", "HERO"])
    if (
        e[0] == "fn"
        and name == "B_GIVEINVITEMS"
        and len(e) == 6
        and e[2] == ["inst", "SELF"]
        and e[3] in hero
    ):
        item, count = e[4], e[5]
    elif e[0] == "call" and name == "CREATEINVITEMS" and len(e) == 5 and e[2] in hero:
        item, count = e[3], e[4]
    else:
        return None
    if not (isinstance(item, list) and item[:1] == ["sym"]):
        return None
    return str(item[1]).upper(), _int(count)


def condition(steps: list[Step] | None) -> Expr | None:
    """A condition's program as an expression, if it is one of the shapes the scripts write:
    `return e`, or `if c: return TRUE` then `return FALSE`; None if it is something else."""
    s = list(steps or [])
    if len(s) == 1 and s[0].get("do") == "return" and isinstance(s[0].get("value"), list):
        return s[0]["value"]
    if (
        len(s) == 2
        and s[0].get("do") == "if"
        and not s[0].get("else")
        and len(s[0].get("then") or []) == 1
        and s[0]["then"][0].get("do") == "return"
        and _int(s[0]["then"][0].get("value")) == 1
        and s[1].get("do") == "return"
        and _int(s[1].get("value")) == 0
    ):
        return s[0].get("cond")
    return None


def names(steps: list[Step] | None) -> set[str]:
    """Script names the program uses: variables, functions, symbols, instances other than self, other and
    the hero (original lines are not script symbols: the game checks them)."""
    out: set[str] = set()
    for e in exprs_of(steps):
        if e[0] in ("var", "sym", "call", "fn") or (e[0] == "inst" and e[1] not in ("SELF", "OTHER", "HERO")):
            out.add(str(e[1]).upper())
    return out


def lines(steps: list[Step] | None) -> list[Step]:
    """The say steps, those of choices too."""
    return [s for s in steps_of(steps) if s.get("do") == "say"]


def choices(steps: list[Step] | None) -> list[Step]:
    """The choice steps, those of choices too."""
    return [s for s in steps_of(steps) if s.get("do") == "choice"]


def render(steps: list[Step] | None, indent: str = "") -> str:
    """The program, readable: one step a line, branches indented (as storygraph --program prints it)."""
    out = []
    for s in steps or []:
        match s.get("do"):
            case "call":
                out.append(indent + render_expr(s.get("call")))
            case "set":
                out.append(f"{indent}{render_expr(s.get('target'))} = {render_expr(s.get('value'))}")
            case "return":
                value = s.get("value")
                out.append(indent + "return" + (f" {render_expr(value)}" if value is not None else ""))
            case "say":
                said = s.get("line") or f"«{s.get('text', '')}»"
                out.append(f"{indent}{s.get('speaker', 'self')} says {said}")
            case "choice":
                out.append(f"{indent}choice «{s.get('text', '')}»" + (f" {s['id']}" if s.get("id") else ""))
                out.append(render(s.get("steps"), indent + "  "))
            case "clear_choices":
                out.append(f"{indent}clear choices")
            case "if":
                out.append(f"{indent}if {render_expr(s.get('cond'))}")
                out.append(render(s.get("then"), indent + "  "))
                if s.get("else"):
                    out.append(f"{indent}else")
                    out.append(render(s.get("else"), indent + "  "))
            case other:
                out.append(f"{indent}? {other}")
    return "\n".join(x for x in out if x)


def _int(e: Any) -> int | None:
    return int(e[1]) if isinstance(e, list) and e[:1] == ["int"] else None
