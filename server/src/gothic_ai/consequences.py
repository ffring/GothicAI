"""What a lie found out changes in the scripts' dialogs of the one lied to (docs/story-graph.md, «Последствия
лжи в ванильных диалогах»).

The hero told the giver of a quest of the scripts that it was done and took the reward (vanilla.py), then
the giver found out (quests.unmask). The scripts still have the quest done, and the giver's other dialogs
count it: Lobart takes 10 gold off his work clothes for the turnips and has no more work for the hero. From
then on, in the giver's dialogs other than the quest's own, the quest reads as still running: what the giver
gave for the work is gone (the discount and the line about it), and it asks for the work first. Until the
hero makes amends (amends.py), the gold it pays for other jobs is halved.

The changes are the director's programs in place of the scripts' (graph_patch `program`, programs.py). They
are built from the scripts' own programs, which the game gives (`story_program` with `own`), checked by the
validator, and kept in the save branch like every change of the graph.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import TYPE_CHECKING, Any, Literal

from .chronicle import Chronicle, Fact
from .game import GameError, GameLink
from .gamedata.storygraph import Expr, Node, StoryGraph
from .journal import Journal
from .programs import Step
from .story import StoryPatches
from .validator import Rules, names_in, validate

if TYPE_CHECKING:
    from .dialogue import DialogueManager
    from .director import Director

log = logging.getLogger(__name__)

RUNNING = ["int", 1, "LOG_RUNNING"]  # what the quest reads as in the giver's dialogs
GOLD = ["sym", "ITMI_GOLD"]
SKIP = ("PICKPOCKET", "EXIT", "PETZ", "PMSCHULDEN")  # the scripts' own crime and pickpocket dialogs
Part = Literal["condition", "information"]
Key = tuple[str, str]  # node, part


def rewrite(steps: list[Step], quests: set[str], halve: bool) -> tuple[list[Step], list[str]] | None:
    """The giver's program as it runs after the lie: each quest of `quests` it reads is still running; with
    `halve`, the gold it gives the hero is half (at least 1). None if nothing changes; else the steps and what
    changed ("quest", "pay")."""
    changed: set[str] = set()

    def expr(e: Any) -> Any:
        if not isinstance(e, list) or not e:
            return e
        if e[0] == "var" and len(e) == 2 and str(e[1]).upper() in quests:
            changed.add("quest")
            return list(RUNNING)
        e = [e[0], *(expr(a) for a in e[1:])]
        if halve and _pays(e):
            amount = e[-1]
            e[-1] = ["int", max(1, int(amount[1]) // 2)]
            changed.add("pay")
        return e

    def step(s: Step) -> Step:
        s = dict(s)
        for k in ("call", "value", "cond"):
            if k in s:
                s[k] = expr(s[k])
        if "target" in s and isinstance(s["target"], list) and s["target"][:1] == ["field"]:
            s["target"] = [s["target"][0], expr(s["target"][1]), *s["target"][2:]]  # the instance is read
        for k in ("then", "else", "steps"):
            if isinstance(s.get(k), list):
                s[k] = [step(x) for x in s[k]]
        return s

    out = [step(s) for s in copy.deepcopy(steps)]
    return (out, sorted(changed)) if changed else None


def _pays(e: Expr) -> bool:
    """B_GiveInvItems(self, other, ItMi_Gold, n) or CreateInvItems(other, ItMi_Gold, n), n a number."""
    if len(e) < 2 or not isinstance(e[-1], list) or e[-1][:1] != ["int"]:
        return False
    head = [e[0], str(e[1]).upper(), *e[2:-1]]
    return head in (
        ["fn", "B_GIVEINVITEMS", ["inst", "SELF"], ["inst", "OTHER"], GOLD],
        ["call", "CREATEINVITEMS", ["inst", "OTHER"], GOLD],
    )


class LieConsequences:
    def __init__(
        self,
        link: GameLink,
        graph: StoryGraph,
        patches: StoryPatches,
        chronicle: Chronicle,
        director: Director,
        *,
        dialogue: DialogueManager | None = None,
        journal: Journal | None = None,
    ) -> None:
        self.link = link
        self.graph = graph
        self.patches = patches
        self.chronicle = chronicle
        self.director = director
        self.dialogue = dialogue
        self.journal = journal or Journal()
        self.rules = Rules.of(graph)
        self._lock = asyncio.Lock()
        self._background: set[asyncio.Task[Any]] = set()
        chronicle.on_fact.append(self._on_fact)

    def _on_fact(self, f: Fact) -> None:
        match f.get("fact"):
            case "found_out":
                self._spawn(self.update(f.get("npc", "")))
            case "amends" if f.get("about"):
                self._spawn(self.update(f["about"]))  # the one lied to pays as before

    def lies(self, npc: str) -> tuple[set[str], bool]:
        """The quests the hero lied about to the npc and it found out, and whether it still pays him half:
        some of those lies are not made up for."""
        quests = {f.get("quest", "") for f in self.chronicle.facts("found_out") if f.get("npc") == npc} - {""}
        amended = {f.get("quest", "") for f in self.chronicle.facts("amends") if f.get("about") == npc}
        return quests, bool(quests - amended)

    async def update(self, npc: str) -> list[dict[str, Any]]:
        """The npc's dialogs as the lies found out make them; the changes made."""
        async with self._lock:
            quests, halve = self.lies(npc)
            if not quests:
                return []
            current = self._current()
            ops = []
            for node in self._dialogs(npc, quests):
                parts: tuple[Part, ...] = ("information",) if node.is_choice else ("condition", "information")
                for part in parts:
                    try:
                        own = await self.link.story_program(node.id, part, own=True)
                    except (GameError, TimeoutError):
                        continue  # no such function (a dialog without a condition), or not a program
                    made = rewrite(own.get("steps") or [], quests, halve)
                    steps = made[0] if made else None
                    if steps != current.get((node.id, part)):
                        ops.append({"op": "program", "node": node.id, "part": part, "steps": steps})
            done = [op for op in ops if await self._make(op)]
            if done:
                fact = {"fact": "lie_consequences", "npc": npc, "quests": sorted(quests), "halved": halve}
                self.chronicle.record({**fact, "nodes": sorted({op["node"] for op in done})})
                self.journal.add(
                    "lie_consequences", npc=npc, quests=sorted(quests), halved=halve, ops=len(done)
                )
                self._remember(npc, quests, halve)
            return done

    def _dialogs(self, npc: str, quests: set[str]) -> list[Node]:
        """The npc's dialogs and choices, not the quests' own (the ending stays as the lie left it: done)."""
        own: set[str] = set()
        for q in quests:
            if q in self.graph.quests:
                own |= self.graph.quests[q].movers()
        found = [
            n
            for n in self.graph.nodes.values()
            if n.npc == npc and n.id not in own and not n.id.endswith(SKIP)
        ]
        return sorted(found, key=lambda n: n.id)

    def _current(self) -> dict[Key, list[Step] | None]:
        """The programs in place now, from the branch's changes."""
        out: dict[Key, list[Step] | None] = {}
        for op in self.patches.ops():
            if op.get("op") == "program":
                out[(str(op.get("node", "")).upper(), op.get("part") or "information")] = op.get("steps")
        return out

    async def _make(self, op: dict[str, Any]) -> bool:
        story = await self.director.with_symbols(await self.director.story(), names_in([op]))
        errors = validate([op], story, self.rules)
        if errors:
            log.info("lie consequences: %s %s not made: %s", op["node"], op["part"], "; ".join(errors))
            return False
        try:
            await self.patches.apply(op)
        except GameError as err:
            log.warning("lie consequences: %s %s: %s", op["node"], op["part"], err)
            return False
        return True

    def _remember(self, npc: str, quests: set[str], halve: bool) -> None:
        if self.dialogue is None:
            return
        found = [f for f in self.chronicle.facts("found_out") if f.get("npc") == npc]
        name = str(found[-1].get("name") or npc) if found else npc
        titles = ", ".join(f"«{self._title(q)}»" for q in sorted(quests))
        pay = (
            "и за другую работу платишь ему вполовину, пока не повинится" if halve else "а платишь как прежде"
        )
        self.dialogue.remember(
            npc,
            name,
            f"Ты не считаешь сделанным {titles}: герой солгал. Ни скидки, ни новой работы за это, {pay}",
        )

    def _title(self, quest: str) -> str:
        q = self.graph.quests.get(quest)
        return q.title if q is not None and q.title else quest

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
