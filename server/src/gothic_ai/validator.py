"""Checks the director's changes to the story graph before they reach the game (docs/story-graph.md, stage 5).

The director proposes, the code decides. A change (graph_patch operations) is accepted only if:
- what it names exists in the scripts: npc's, items, variables, dialogs, engine functions;
- it leaves the chapters and the main plot alone: no KAPITEL and no variable a chapter change reads, no
  dialogs of the plot's npc's or of chapter changes;
- a dialog it adds can be offered from the current state: its npc lives, its condition can become true;
- every quest in progress keeps an ending the hero can reach, and the quest it repairs gets one.

A program in place of a dialog's own (graph_patch `program`, programs.py) is judged by what it does: the
quest it ends, the variables it writes, the condition it has when it is one of the scripts' shapes (any
other condition may come true).

"Can become true" is computed over the lifted conditions with what may still change left open: a
variable a dialog of a living npc writes, the items the hero may get, the dialogs he may yet hear. The dead
stay dead, chapters only go forward. It over-approximates: a condition it calls impossible is impossible,
one it calls possible may still take luck.
"""

from __future__ import annotations

import copy
import itertools
import operator
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from . import programs
from .gamedata.storygraph import Expr, StoryGraph, render, walk

LOG_RUNNING, LOG_SUCCESS, LOG_FAILED, LOG_OBSOLETE = 1, 2, 3, 4
ENDINGS = {LOG_SUCCESS: "success", LOG_FAILED: "failed", LOG_OBSOLETE: "obsolete"}
LAST_CHAPTER = 6
DIRECTOR_PREFIX = "DIR_"  # ids of the director's dialogs
# what a dialog of the director may do (the bridge plays these)
EFFECTS = {"give", "xp", "set", "journal", "end"}
JOURNAL_STATUS = {"LOG_RUNNING", "LOG_SUCCESS", "LOG_FAILED", "LOG_OBSOLETE"}
MAX_LINES = 10
MAX_TEXT = 300
MAX_DESCRIPTION = 80

# The plot's npc's (instance prefixes): the director does not touch their dialogs. With the owners of the
# dialogs that change the chapter, from the graph.
PLOT_NPCS = (
    "NONE_100_XARDAS",
    "PC_",  # the hero's friends: Lester, Diego, Milten, Gorn
    "PAL_200_HAGEN",
    "PAL_250_GAROND",
    "KDF_500_PYROKAR",
    "KDF_501_SERPENTES",
    "KDF_502_ULTHAR",
    "KDF_505_MARDUK",
    "VLK_439_VATRAS",
    "VLK_449_LARES",
    "SLD_800_LEE",
    "SLD_809_BENNET",
    "DJG_705_ANGAR",
    "KDW_1400_ADDON_SATURAS",
    "KDW_14000_ADDON_SATURAS",
    "BDT_1090_ADDON_RAVEN",
    "DRAGON_",
)

Vals = frozenset[int] | None  # the values an expression can still take; None: any
MAX_VALUES = 64


@dataclass
class WorldState:
    """What the game reported (story_state): variables, who lives, what the hero has heard."""

    vars: dict[str, int] = field(default_factory=dict)
    npcs: dict[str, str] = field(default_factory=dict)  # symbol -> alive, dead, absent
    names: dict[str, str] = field(default_factory=dict)  # symbol -> name in the game
    knows: dict[str, bool] = field(default_factory=dict)
    symbols: dict[str, str] = field(default_factory=dict)  # name -> npc, item, info, var, const, func, other
    dialog: bool = False
    cutscene: bool = False

    @classmethod
    def from_game(cls, value: dict[str, Any]) -> WorldState:
        npcs = value.get("npcs") or {}
        return cls(
            vars={k: int(v) for k, v in (value.get("vars") or {}).items()},
            npcs={k: v.get("state", "absent") for k, v in npcs.items()},
            names={k: v["name"] for k, v in npcs.items() if v.get("name")},
            knows={k: bool(v) for k, v in (value.get("knows") or {}).items()},
            symbols=dict(value.get("symbols") or {}),
            dialog=bool(value.get("dialog")),
            cutscene=bool(value.get("cutscene")),
        )

    def merge(self, other: WorldState) -> WorldState:
        out = copy.deepcopy(self)
        out.vars.update(other.vars)
        out.npcs.update(other.npcs)
        out.names.update(other.names)
        out.knows.update(other.knows)
        out.symbols.update(other.symbols)
        out.dialog, out.cutscene = other.dialog, other.cutscene
        return out

    def alive(self, npc: str) -> bool:
        return self.npcs.get(npc.upper()) == "alive"

    def dead(self, npc: str) -> bool:
        return self.npcs.get(npc.upper()) == "dead"

    def name(self, npc: str) -> str:
        return self.names.get(npc.upper(), npc)


@dataclass
class Patches:
    """The changes in effect on the branch, replayed from its graph_patch operations like the game does."""

    disabled: set[str] = field(default_factory=set)
    conditions: dict[str, list[Expr]] = field(default_factory=dict)
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)  # the director's dialogs (add_node)
    told: set[str] = field(default_factory=set)
    # the director's programs in place of the scripts': by dialog or choice, NODE:CONDITION for a condition
    programs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @classmethod
    def of(cls, ops: Iterable[dict[str, Any]]) -> Patches:
        return cls().then(ops)

    def then(self, ops: Iterable[dict[str, Any]]) -> Patches:
        out = copy.deepcopy(self)
        for op in ops:
            node = str(op.get("node", "")).upper() if not isinstance(op.get("node"), dict) else ""
            match op.get("op"):
                case "add_node":
                    made = dict(op["node"])
                    made["id"] = str(made.get("id", "")).upper()
                    made["npc"] = str(made.get("npc", "")).upper()
                    out.nodes[made["id"]] = made
                case "remove_node":
                    out.nodes.pop(node, None)
                    out.told.discard(node)
                case "disable":
                    out.disabled.add(node)
                case "enable":
                    out.disabled.discard(node)
                case "condition" if op.get("expr") is None:
                    out.conditions.pop(node, None)
                case "condition":
                    out.conditions.setdefault(node, []).append(op["expr"])
                case "told":
                    out.told.add(node)
                case "program":
                    key = node + (":CONDITION" if op.get("part") == "condition" else "")
                    if op.get("steps") is None:
                        out.programs.pop(key, None)
                    else:
                        out.programs[key] = op["steps"]
        return out


class Story:
    """The story as it stands: the graph, the game's state and the director's changes. Answers what can
    still happen: whether a dialog can be offered, what a condition can come to, which quests in progress
    have no ending left."""

    def __init__(self, graph: StoryGraph, state: WorldState, patches: Patches | None = None) -> None:
        self.graph = graph
        self.state = state
        self.patches = patches or Patches()
        self._open: dict[str, bool] = {}
        self._values: dict[str, Vals] = {}

    def then(self, ops: Iterable[dict[str, Any]]) -> Story:
        """The story with these changes made."""
        return Story(self.graph, self.state, self.patches.then(ops))

    # --- quests -------------------------------------------------------------------------------

    def running(self) -> list[str]:
        return sorted(q for q in self.graph.quests if self.state.vars.get(q) == LOG_RUNNING)

    def endings(self, quest: str) -> list[str]:
        """Dialogs that may end the quest: the scripts' own and the director's; a dialog with the director's
        program ends it if the program does."""
        quest = quest.upper()
        out = set()
        if (q := self.graph.quests.get(quest)) is not None:
            out = (q.success | q.failed | q.obsolete) - set(self.patches.programs)
        for id in [*self.patches.nodes, *(k for k in self.patches.programs if ":" not in k)]:
            if any(_sets(e, quest) in ENDINGS for e in self.effects_of(id)):
                out.add(id)
        return sorted(out)

    def effects_of(self, id: str) -> list[dict[str, Any]]:
        """What the dialog (or choice) does when it runs: its lifted effects, or what the director's program
        in its place does; for a dialog of the director, its effects and its program's."""
        id = id.upper()
        if (made := self.patches.nodes.get(id)) is not None:
            return [*made.get("effects", []), *programs.effects(made.get("program"))]
        if id in self.patches.programs:
            return programs.effects(self.patches.programs[id])
        node = self.graph.nodes.get(id)
        return node.effects if node else []

    def dead_ends(self) -> list[str]:
        """Quests in progress that no dialog can end any more."""
        return [q for q in self.running() if not any(self.can_offer(n) for n in self.endings(q))]

    # --- dialogs ------------------------------------------------------------------------------

    def owner(self, id: str) -> str | None:
        id = id.upper()
        if (made := self.patches.nodes.get(id)) is not None:
            return made["npc"]
        node = self.graph.nodes.get(id)
        return node.npc if node else None

    def can_offer(self, id: str) -> bool:
        """The dialog (or choice) can be offered now or later: its npc is not dead, it is not switched off
        or used up, and its conditions can come true."""
        id = id.upper()
        if id not in self._open:
            self._open[id] = True  # a cycle through Npc_KnowsInfo: it does not close the dialog
            self._open[id] = self._can_offer(id)
        return self._open[id]

    def _can_offer(self, id: str) -> bool:
        conditions: list[Expr | None]
        if (made := self.patches.nodes.get(id)) is not None:
            npc = made["npc"]
            if id in self.patches.told and not made.get("permanent"):
                return False
            conditions = [made.get("condition")]
        elif (node := self.graph.nodes.get(id)) is not None:
            npc = node.npc
            if id in self.patches.disabled:
                return False
            if node.is_choice:
                # offered when a dialog that adds it runs
                return not self.state.dead(npc) and any(self.can_offer(p) for p in node.parents)
            if not node.permanent and self.state.knows.get(id):
                return False
            condition = node.condition if node.lifted else None
            if (own := self.patches.programs.get(id + ":CONDITION")) is not None:
                condition = programs.condition(own)  # the director's; None: may come true
            conditions = [condition, *self.patches.conditions.get(id, [])]
        else:
            return False
        if self.state.dead(npc):
            return False
        return all(self.possible(c, self_npc=npc) for c in conditions)

    # --- conditions ---------------------------------------------------------------------------

    def possible(
        self, expr: Expr | None, *, self_npc: str | None = None, hero_items: dict[str, int] | None = None
    ) -> bool:
        """The condition can still come true (None: always)."""
        return (
            expr is None
            or _truth(self.values_of(expr, self_npc=self_npc, hero_items=hero_items)) is not False
        )

    def values_of(
        self, expr: Expr, *, self_npc: str | None = None, hero_items: dict[str, int] | None = None
    ) -> Vals:
        return _Eval(self, self_npc, hero_items or {}).eval(expr)

    def var_values(self, var: str) -> Vals:
        """What a global can still be: its value now and what living npc's dialogs (and the director's) may
        write into it; any value if something else writes it or it was not reported."""
        var = var.upper()
        if var in self._values:
            return self._values[var]
        now = self.state.vars.get(var)
        if now is None:
            vals: Vals = None
        elif var == "KAPITEL":
            vals = frozenset(range(now, max(now, LAST_CHAPTER) + 1))
        else:
            vals = self._written(var, now)
        self._values[var] = vals
        return vals

    def _written(self, var: str, now: int) -> Vals:
        info = self.graph.vars.get(var)
        out = {now}
        writers: list[tuple[str, list[dict[str, Any]]]] = []
        if info is not None:
            if info.others:
                return None  # scripts outside dialogs write it: anything
            for w in info.writers:
                node = self.graph.nodes.get(w)
                if node is not None and w not in self.patches.programs:
                    writers.append((node.npc, node.effects))
        for id in [*self.patches.nodes, *(k for k in self.patches.programs if ":" not in k)]:
            if (npc := self.owner(id)) is not None:
                writers.append((npc, self.effects_of(id)))
        for npc, effects in writers:
            if self.state.dead(npc):
                continue
            for e in effects:
                if e.get("do") != "set" or str(e.get("var", "")).upper() != var:
                    continue
                v = _constant(e.get("value"))
                if v is None:
                    return None
                out.add(v)
        return frozenset(out)


class _Eval:
    """One condition, over sets of values (see Story.values_of)."""

    def __init__(self, story: Story, self_npc: str | None, hero_items: dict[str, int]) -> None:
        self.story = story
        self.self_npc = self_npc
        self.hero_items = {k.upper(): v for k, v in hero_items.items()}

    def eval(self, e: Expr) -> Vals:
        if not isinstance(e, list) or not e:
            return None
        tag = e[0]
        match tag:
            case "int":
                return frozenset({int(e[1])})
            case "var":
                return self.story.var_values(e[1]) if len(e) == 2 else None
            case "call":
                return self.call(e[1], e[2:])
            case "cases":
                return self.cases(e)
            case "!":
                return _of(_not(_truth(self.eval(e[1]))))
            case "&&":
                return _of(_and([_truth(self.eval(a)) for a in e[1:]]))
            case "||":
                return _of(_or([_truth(self.eval(a)) for a in e[1:]]))
            case "neg":
                v = self.eval(e[1])
                return None if v is None else frozenset(-x for x in v)
        if tag in _OPS and len(e) == 3:
            return _pairwise(_OPS[tag], self.eval(e[1]), self.eval(e[2]), tag in _COMPARE)
        return None  # strings, fields, instances, script helpers, unknown: anything

    def cases(self, e: Expr) -> Vals:
        out: set[int] = set()
        fallthrough = True
        for i in range(1, len(e) - 1, 2):
            guard = _truth(self.eval(e[i]))
            if guard is False:
                continue
            v = self.eval(e[i + 1])
            if v is None:
                return None
            out |= v
            if guard is True:
                fallthrough = False
                break
        if fallthrough:
            out.add(0)
        return frozenset(out)

    def call(self, fn: str, args: list[Expr]) -> Vals:
        match fn:
            case "NPC_ISDEAD" if args:
                npc = self.npc(args[0])
                if npc is None or npc not in self.story.state.npcs:
                    return None
                return frozenset({1}) if self.story.state.dead(npc) else frozenset({0, 1})
            case "NPC_KNOWSINFO" if len(args) == 2 and self.hero(args[0]) and args[1][:1] == ["sym"]:
                info = args[1][1].upper()
                if self.story.state.knows.get(info):
                    return frozenset({1})
                if info not in self.story.graph.nodes and info not in self.story.patches.nodes:
                    return None
                return frozenset({0, 1}) if self.story.can_offer(info) else frozenset({0})
            case "NPC_HASITEMS" if len(args) == 2 and self.hero(args[0]) and args[1][:1] == ["sym"]:
                item = args[1][1].upper()
                return frozenset({self.hero_items[item]}) if item in self.hero_items else None
        return None

    def hero(self, e: Expr) -> bool:
        return e[:1] == ["inst"] and e[1] in ("OTHER", "HERO")

    def npc(self, e: Expr) -> str | None:
        if e[:1] != ["inst"]:
            return None
        if e[1] == "SELF":
            return self.self_npc
        if e[1] in ("OTHER", "HERO"):
            return None
        return self.story.graph.npc(e[1])


# --- the check --------------------------------------------------------------------------------


@dataclass
class Rules:
    """What the director may not touch."""

    plot_npcs: set[str]
    plot_vars: set[str]
    plot_nodes: set[str]  # dialogs that change the chapter

    @classmethod
    def of(cls, graph: StoryGraph) -> Rules:
        chapter = {n.id for n in graph.nodes.values() if any(e["do"] == "chapter" for e in n.effects)}
        npcs = {graph.nodes[n].npc for n in chapter}
        npcs |= {n.npc for n in graph.nodes.values() if n.npc.startswith(PLOT_NPCS)}
        plot_vars = {"KAPITEL"}
        for n in chapter:
            plot_vars |= graph.nodes[n].reads
        return cls(plot_npcs=npcs, plot_vars=plot_vars, plot_nodes=chapter)

    def plot_npc(self, npc: str) -> bool:
        npc = npc.upper()
        return npc in self.plot_npcs or npc.startswith(PLOT_NPCS)


@dataclass
class Caps:
    """The most a repair may give the hero: what the ending it replaces gave."""

    xp: int = 0
    items: dict[str, int] = field(default_factory=dict)


def names_in(ops: Iterable[dict[str, Any]]) -> set[str]:
    """Script names the operations use, for story_state's `symbols`."""
    out: set[str] = set()
    for op in ops:
        made = op.get("node") if isinstance(op.get("node"), dict) else None
        if made is not None:
            out |= {str(made.get("id", "")).upper(), str(made.get("npc", "")).upper()}
            out |= _expr_names(made.get("condition"))
            for e in made.get("effects", []):
                for k in ("item", "var", "status"):
                    if e.get(k):
                        out.add(str(e[k]).upper())
            out |= programs.names(made.get("program"))
        out |= _expr_names(op.get("expr"))
        if isinstance(op.get("steps"), list):
            out |= programs.names(op["steps"])
    out.discard("")
    return out


def validate(
    ops: list[dict[str, Any]],
    story: Story,
    rules: Rules,
    *,
    repairs: str | None = None,
    caps: Caps | None = None,
) -> list[str]:
    """Why the change can't be made (in the words the director's model reads); empty: it can."""
    errors: list[str] = []
    after = story.then(ops)
    for op in ops:
        kind = op.get("op")
        if kind == "add_node":
            errors += _check_node(op.get("node") or {}, story, after, rules, caps)
        elif kind in ("disable", "enable", "condition", "remove_node"):
            errors += _check_change(op, story, rules)
        elif kind == "program":
            errors += _check_program(op, story, rules)
        else:
            errors.append(f"операция {kind!r} режиссёру недоступна")
    if errors:
        return errors
    before = set(story.dead_ends())
    for q in after.dead_ends():
        if q == repairs:
            errors.append(f"квест {q} так и остаётся без концовки: ни одна его концовка не может случиться")
        elif q not in before:
            errors.append(f"после правки квест {q} остаётся без концовки")
    return errors


def _check_node(
    made: dict[str, Any], story: Story, after: Story, rules: Rules, caps: Caps | None
) -> list[str]:
    errors = []
    id = str(made.get("id", "")).upper()
    npc = str(made.get("npc", "")).upper()
    symbols = story.state.symbols
    if not id.startswith(DIRECTOR_PREFIX):
        errors.append(f"id диалога режиссёра начинается с {DIRECTOR_PREFIX}: {id}")
    if id in story.graph.nodes or id in symbols or id in story.patches.nodes:
        errors.append(f"диалог {id} уже есть")
    if symbols.get(npc) != "npc":
        errors.append(f"нет такого NPC: {npc}")
    elif not story.state.alive(npc):
        errors.append(f"{story.state.name(npc)} ({npc}) {'мёртв' if story.state.dead(npc) else 'не в мире'}")
    elif rules.plot_npc(npc):
        errors.append(f"{story.state.name(npc)} ({npc}) — сюжетный NPC, его диалоги не трогаем")
    description = str(made.get("description", ""))
    if not made.get("important") and not description.strip():
        errors.append("у диалога нет строки для меню")
    if len(description) > MAX_DESCRIPTION:
        errors.append(f"строка меню длиннее {MAX_DESCRIPTION} знаков")
    lines = made.get("lines") or []
    program = made.get("program")
    if program is not None and not isinstance(program, list):
        errors.append("программа диалога — список шагов")
        program = None
    if not 1 <= len(lines) + len(programs.lines(program)) <= MAX_LINES:
        errors.append(f"реплик должно быть от 1 до {MAX_LINES}")
    for line in lines:
        text = str(line.get("text", ""))
        if line.get("speaker") not in ("self", "other"):
            errors.append(f"кто говорит: self или other, а не {line.get('speaker')!r}")
        if (not line.get("line") and not text.strip()) or len(text) > MAX_TEXT:
            errors.append(f"реплика пустая или длиннее {MAX_TEXT} знаков")
    errors += check_effects(made.get("effects") or [], symbols, rules, caps)
    if program:
        if caps is not None:
            errors.append("у ремонта нет своей программы: только эффекты исходной концовки")
        errors += _check_steps(program, symbols, rules)
    errors += _check_names(made.get("condition"), symbols)
    if not errors and not after.can_offer(id):
        errors.append(
            f"диалог никогда не будет предложен: условие {render(made.get('condition'))} не может выполниться"
        )
    return errors


def check_effects(
    effects: list[dict[str, Any]], symbols: dict[str, str], rules: Rules, caps: Caps | None
) -> list[str]:
    errors = []
    xp = 0
    given: dict[str, int] = {}
    for e in effects:
        kind = e.get("do")
        count = _count(e)
        if kind not in EFFECTS:
            errors.append(f"эффект {kind!r} режиссёру недоступен")
        elif kind == "give":
            item = str(e.get("item", "")).upper()
            if symbols.get(item) != "item":
                errors.append(f"нет такого предмета: {item}")
            if count <= 0:
                errors.append(f"сколько {item}: {count}")
            if e.get("to") != "self":
                given[item] = given.get(item, 0) + count
        elif kind == "xp":
            xp += count
            if count <= 0:
                errors.append(f"опыт должен быть больше нуля: {count}")
        elif kind == "set":
            var = str(e.get("var", "")).upper()
            if symbols.get(var) != "var":
                errors.append(f"нет такой переменной: {var}")
            elif var in rules.plot_vars:
                errors.append(f"{var} ведёт главы сюжета, её не трогаем")
            if not isinstance(e.get("value"), int):
                errors.append(f"{var}: значение — целое число")
        elif kind == "journal":
            if not str(e.get("topic", "")).strip() or not str(e.get("text", "")).strip():
                errors.append("запись в журнал без темы или текста")
            if str(e.get("status") or "LOG_RUNNING").upper() not in JOURNAL_STATUS:
                errors.append(f"статус темы журнала: {e.get('status')}")
    if caps is not None:
        if xp > caps.xp:
            errors.append(f"опыта {xp}, а исходная концовка давала {caps.xp}")
        for item, n in given.items():
            if n > caps.items.get(item, 0):
                errors.append(f"{item} ×{n}, а исходная концовка давала ×{caps.items.get(item, 0)}")
    return errors


def _check_change(op: dict[str, Any], story: Story, rules: Rules) -> list[str]:
    node = str(op.get("node", "")).upper()
    owner = story.owner(node)
    if owner is None:
        return [f"нет такого диалога: {node}"]
    if node in rules.plot_nodes:
        return [f"{node} меняет главу, его не трогаем"]
    if rules.plot_npc(owner):
        return [f"{node} — диалог сюжетного NPC {owner}, его не трогаем"]
    return _check_names(op.get("expr"), story.state.symbols)


def _check_program(op: dict[str, Any], story: Story, rules: Rules) -> list[str]:
    node = str(op.get("node", "")).upper()
    part = op.get("part", "information")
    if part not in ("information", "condition"):
        return [f"часть диалога: information или condition, а не {part!r}"]
    graph_node = story.graph.nodes.get(node)
    if graph_node is None:
        return [f"нет такого диалога или варианта ответа в скриптах: {node}"]
    if node in rules.plot_nodes:
        return [f"{node} меняет главу, его не трогаем"]
    if rules.plot_npc(graph_node.npc):
        return [f"{node} — диалог сюжетного NPC {graph_node.npc}, его не трогаем"]
    if part == "condition" and graph_node.is_choice:
        return [f"{node} — вариант ответа, у него нет условия"]
    steps = op.get("steps")
    if steps is None:
        return []  # the scripts' own program again
    if not isinstance(steps, list):
        return ["программа — список шагов"]
    return _check_steps(steps, story.state.symbols, rules)


def _check_steps(steps: list[dict[str, Any]], symbols: dict[str, str], rules: Rules) -> list[str]:
    errors = []
    missing = sorted(n for n in programs.names(steps) if n not in symbols)
    if missing:
        errors.append(f"в программе неизвестные имена: {', '.join(missing)}")
    for e in programs.effects(steps):
        if e["do"] == "set" and e["var"] in rules.plot_vars:
            errors.append(f"{e['var']} ведёт главы сюжета, её не трогаем")
        elif e["do"] == "chapter":
            errors.append("программа меняет главу: это сюжет, его не трогаем")
    for line in programs.lines(steps):
        text = str(line.get("text", ""))
        if line.get("speaker") not in ("self", "other"):
            errors.append(f"кто говорит: self или other, а не {line.get('speaker')!r}")
        if (not line.get("line") and not text.strip()) or len(text) > MAX_TEXT:
            errors.append(f"реплика пустая или длиннее {MAX_TEXT} знаков")
    return errors


def _check_names(expr: Expr | None, symbols: dict[str, str]) -> list[str]:
    missing = sorted(n for n in _expr_names(expr) if n not in symbols)
    return [f"в условии неизвестные имена: {', '.join(missing)}"] if missing else []


def _expr_names(expr: Expr | None) -> set[str]:
    out = set()
    for e in walk(expr):
        if e[0] in ("var", "sym", "call") or (e[0] == "inst" and e[1] not in ("SELF", "OTHER", "HERO")):
            out.add(str(e[1]).upper())
    return out


def _sets(effect: dict[str, Any], var: str) -> int | None:
    """The value the effect writes into `var`, if it is a constant."""
    if effect.get("do") != "set" or str(effect.get("var", "")).upper() != var:
        return None
    return _constant(effect.get("value"))


def _constant(v: Any) -> int | None:
    """An int, from the graph (["int", 2, "LOG_SUCCESS"]) or from an add_node effect (2)."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, list) and v[:1] == ["int"]:
        return int(v[1])
    return None


def _count(e: dict[str, Any]) -> int:
    for k in ("count", "amount", "value"):
        if isinstance(e.get(k), int):
            return int(e[k])
    return 1


# --- three-valued logic over sets of values ---------------------------------------------------


def _truth(v: Vals) -> bool | None:
    if v is None or not v:
        return None
    if all(x != 0 for x in v):
        return True
    if all(x == 0 for x in v):
        return False
    return None


def _of(b: bool | None) -> Vals:
    return frozenset({int(b)}) if b is not None else frozenset({0, 1})


def _not(b: bool | None) -> bool | None:
    return None if b is None else not b


def _and(bs: list[bool | None]) -> bool | None:
    if any(b is False for b in bs):
        return False
    return True if all(b is True for b in bs) else None


def _or(bs: list[bool | None]) -> bool | None:
    if any(b is True for b in bs):
        return True
    return False if all(b is False for b in bs) else None


def _div(a: int, b: int) -> int:
    return int(a / b) if b else 0


_COMPARE = {"==", "!=", "<", ">", "<=", ">="}
_OPS: dict[str, Callable[[int, int], int]] = {
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "<": lambda a, b: int(a < b),
    ">": lambda a, b: int(a > b),
    "<=": lambda a, b: int(a <= b),
    ">=": lambda a, b: int(a >= b),
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": _div,
    "%": lambda a, b: int(a - b * _div(a, b)) if b else 0,
    "&": operator.and_,
    "|": operator.or_,
}


def _pairwise(fn: Callable[[int, int], int], a: Vals, b: Vals, compare: bool) -> Vals:
    if a is None or b is None or len(a) * len(b) > MAX_VALUES * MAX_VALUES:
        return frozenset({0, 1}) if compare else None
    out = frozenset(fn(x, y) for x, y in itertools.product(a, b))
    return out if len(out) <= MAX_VALUES else (frozenset({0, 1}) if compare else None)


__all__ = [
    "Caps",
    "Patches",
    "Rules",
    "Story",
    "WorldState",
    "check_effects",
    "names_in",
    "validate",
]
