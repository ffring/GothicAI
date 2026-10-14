"""The scripts' quests in the giver's conversation, and the hero's lies about them (story-graph.md, stage 5).

A quest of the scripts (Lobart's turnips) ends in a dialog whose effects are lifted into the graph and checked
against the VM (`storygraph --effects`). In a conversation the npc that ends it sees what it expects from the
hero (what it asked for, the reward) and may take his word that it is done (`accept_quest`). The code plays
the ending as the scripts would in a world where his claim is true — he has the turnips: the quest variable,
the experience, the reward (the first choice that pays), the journal. Items he really has are taken; items he
does not have are not, and that is a lie, written down with the truth.

Whether the npc believes him is up to its model, in character, but on what the code shows it, so the player
can read and play it:
- what it sees for itself: the items he has on him (he hands them over, the rest is his word), the people
  that must be dead — before its eyes (the engine's own sight: not behind a wall), a short walk away (60 m),
  or too far to check;
- what it knows of his word (trust.py): lies of his it knows of, rumors, claims of his that were true.
What it sees it does not take on his word: with someone alive before its eyes, or once he lied to it and
it does not see the claim true, `accept_quest` is refused. It may go and see for itself (`check_claim`):
it walks there, and what it sees decides — he lied (caught) or it pays. It may call him a liar to his face
when it sees the claim false (`expose_lie`). A lie seen through before it pays costs him its trust and a
rumor (quests.caught); a lie believed is found out later by a rule:
- fetch (bring items): the next morning the npc misses what he did not bring, unless a later step of the
  quest got it;
- kill (someone must be dead): the npc sees them alive within 20 m (and not behind a wall);
- other claims (talked to someone, a variable) are not found out by a rule yet.
Then the same happens as with the director's quests (quests.unmask): memory, hostility, the scripts, rumors.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .chronicle import Chronicle
from .director import topic_titles
from .game import GameError, GameLink
from .gamedata.storygraph import Expr, Node, StoryGraph, walk
from .journal import Journal
from .llm import Tool, ToolCall
from .protocol import GameMessage, Session, Snapshot
from .quests import SEE_CM, Lie, caught, unmask
from .trust import WORD_BROKEN, Trust
from .turn import ToolOutcome

if TYPE_CHECKING:
    from .dialogue import Conversation, DialogueManager

log = logging.getLogger(__name__)

ACCEPT = "accept_quest"
CHECK = "check_claim"
EXPOSE = "expose_lie"
CHECK_CM = 6000  # how far an npc goes to see for itself
LOOK_CM = 1800  # how close it comes to what it goes to see
BACK_CM = 300  # how close it comes back to the hero to tell him
LOG_RUNNING, LOG_SUCCESS = 1, 2
STATUS = {1: "LOG_RUNNING", 2: "LOG_SUCCESS", 3: "LOG_FAILED", 4: "LOG_OBSOLETE"}
CHECK_EVERY_S = 1.0
MORNING_EVERY_S = 5.0
HERO = (["inst", "OTHER"], ["inst", "HERO"])
# engine calls in an ending that only show something: they don't keep the ending from being played
COSMETIC_CALLS = ("AI_", "PRINT", "SND_", "WLD_PLAYEFFECT", "MDL_", "B_SAY", "B_ENDPRODUCTIONDIALOG")

_QUEST_ARG = {
    "type": "object",
    "properties": {
        "quest": {"type": "string", "description": "id из обстановки, например MIS_LOBART_RUEBEN"}
    },
    "required": ["quest"],
    "additionalProperties": False,
}
ACCEPT_TOOL = Tool(
    ACCEPT,
    "Засчитать герою дело, которого ты от него ждёшь (список — в обстановке), и отдать обещанную награду. "
    "Вызывай, только если веришь, что он его сделал. Что из принесённого у него с собой, перейдёт к тебе; "
    "если он говорит, что остальное оставил где-то ещё, решай, веришь ли. Тело всё сделает после реплики.",
    _QUEST_ARG,
)
CHECK_TOOL = Tool(
    CHECK,
    "Сходить и посмотреть своими глазами, прежде чем засчитать дело, — когда в обстановке сказано, что "
    "можно сходить. После реплики ты пойдёшь туда; что увидишь, придёт ремаркой: солгал он — уличишь, "
    "сказал правду — отдашь награду.",
    _QUEST_ARG,
)
EXPOSE_TOOL = Tool(
    EXPOSE,
    "Уличить героя во лжи: он уверяет, что сделал дело, а ты своими глазами видишь, что это не так (что "
    "ты видишь сам — в обстановке). Только если он сам сказал, что сделал. Ты это запомнишь, люди вокруг "
    "услышат.",
    _QUEST_ARG,
)


@dataclass
class Claim:
    """What the hero must have done for the ending, as its condition says: items he has, people dead,
    dialogs he heard, variables."""

    items: dict[str, int] = field(default_factory=dict)
    dead: list[str] = field(default_factory=list)  # npc symbols
    knows: list[str] = field(default_factory=list)
    vars: dict[str, int] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return "fetch" if self.items else "kill" if self.dead else "talk" if self.knows else "other"

    def assume(self, e: Any, graph: StoryGraph) -> Any:
        """The expression in the world where the claim is true."""
        if not isinstance(e, list) or not e:
            return e
        if e[0] == "call" and len(e) >= 3:
            if e[1] == "NPC_HASITEMS" and len(e) == 4 and e[2] in HERO and _sym(e[3]) in self.items:
                return ["int", self.items[_sym(e[3])]]
            if e[1] == "NPC_ISDEAD" and _npc(e[2], graph) in self.dead:
                return ["int", 1]
            if e[1] == "NPC_KNOWSINFO" and len(e) == 4 and e[2] in HERO and _sym(e[3]) in self.knows:
                return ["int", 1]
        if e[0] == "var" and len(e) == 2 and e[1] in self.vars:
            return ["int", self.vars[e[1]]]
        if e[0] in ("int", "str", "var", "inst", "sym", "param", "unknown"):
            return e
        if e[0] == "field":
            return [e[0], self.assume(e[1], graph), *e[2:]]
        if e[0] in ("call", "fn"):
            return [e[0], e[1], *(self.assume(a, graph) for a in e[2:])]
        return [e[0], *(self.assume(a, graph) for a in e[1:])]


@dataclass
class Target:
    """Someone the claim says is dead, where the npc could see it."""

    npc: str
    name: str
    alive: bool
    distance: float | None  # cm from the npc; None: not in this world
    visible: bool | None = None  # the npc sees it now (the engine's sight); None: unknown, by distance

    @property
    def seen(self) -> bool:
        if self.distance is None:
            return False
        return self.visible if self.visible is not None else self.distance <= SEE_CM

    @property
    def walk(self) -> bool:
        """Not seen from here, but a short walk away."""
        return not self.seen and self.distance is not None and self.distance <= CHECK_CM


@dataclass
class Offer:
    """A quest in progress that the npc can accept as done, what accepting it does, and what of the hero's
    claim the npc can see for itself."""

    quest: str
    title: str
    npc: str
    ending: str
    claim: Claim
    truth: Expr  # the ending's condition as it is: true if the hero really did it
    effects: list[dict[str, Any]]  # what the ending does in the claimed world; takes marked "take"
    reward: str
    needs: str
    names: dict[str, str]  # item instance -> name in the game
    have: dict[str, int] = field(default_factory=dict)  # fetch: what of it the hero has on him
    targets: list[Target] = field(default_factory=list)  # kill: where they are

    def items(self, counts: dict[str, int] | None = None) -> str:
        """The items of the claim in the game's words (with other counts: what is missing...)."""
        counts = self.claim.items if counts is None else counts
        return ", ".join(f"{self.names.get(i, i)} ×{n}" for i, n in counts.items() if n > 0)

    def missing(self) -> dict[str, int]:
        return {i: n - min(n, self.have.get(i, 0)) for i, n in self.claim.items.items()}

    def on_him(self) -> str:
        """What of the items he has on him: «Репа ×5 из 20»."""
        return ", ".join(
            f"{self.names.get(i, i)} ×{self.have.get(i, 0)} из {n}" for i, n in self.claim.items.items()
        )

    def alive_in_view(self) -> Target | None:
        alive = [t for t in self.targets if t.alive and t.seen]
        return min(alive, key=lambda t: t.distance or 0) if alive else None

    def seen_false(self) -> str | None:
        """What the npc sees for itself that says the claim is false; None if it sees nothing of the kind."""
        if any(self.missing().values()):
            return f"у героя с собой только {self.on_him()}"
        if (t := self.alive_in_view()) is not None:
            return f"{t.name} жив — у тебя на глазах, в {_m(t.distance or 0)} м"
        return None

    def seen_true(self) -> bool:
        """The npc sees for itself all the claim says: the items on him, the dead before its eyes."""
        visible = bool(self.claim.items or self.claim.dead)
        return (
            visible and not any(self.missing().values()) and all(not t.alive and t.seen for t in self.targets)
        )

    def reachable(self) -> Target | None:
        """The nearest of the claim's people the npc does not see from here but could go and see."""
        if self.seen_false() is not None:
            return None
        near = [t for t in self.targets if t.walk]
        return min(near, key=lambda t: t.distance or 0) if near else None

    def evidence(self) -> str | None:
        """What the npc sees for itself, in words for its model."""
        parts = []
        if self.claim.items:
            if not any(self.missing().values()):
                parts.append(f"у героя с собой всё это: {self.items()} — отдаст, и дело сделано")
            else:
                parts.append(
                    f"у героя с собой {self.on_him()} — это он отдаст тебе сразу; "
                    "где остальное, знаешь только с его слов"
                )
        seen = [t for t in self.targets if t.seen]
        if seen:
            alive = [t for t in seen if t.alive]
            dead = [t for t in seen if not t.alive]
            what = "; ".join(
                f"{_names(group)} — {state}"
                for group, state in (
                    (alive, "жив" if len(alive) == 1 else "живы"),
                    (dead, "мёртв" if len(dead) == 1 else "мертвы"),
                )
                if group
            )
            parts.append(f"у тебя на глазах: {what}")
        near = [t for t in self.targets if t.walk]
        if near:
            ds = sorted(_m(t.distance or 0) for t in near)
            span = f"{ds[0]}–{ds[-1]}" if ds[0] != ds[-1] else f"{ds[0]}"
            parts.append(
                f"{_names(near)} — в {span} м, отсюда не видно: можешь сходить и посмотреть сам ({CHECK})"
            )
        far = [t for t in self.targets if not t.seen and not t.walk]
        if far:
            parts.append(f"{_names(far)} — далеко, проверить сам не можешь")
        return "; ".join(parts) or None


@dataclass
class Told:
    """A quest the hero claimed to have done and was believed."""

    quest: str
    npc: str
    npc_name: str
    title: str
    reward: str
    lie: bool
    kind: str = ""
    day: int = 0
    tag: str = ""  # kill: the people that must be dead, watched under this tag
    targets: list[str] = field(default_factory=list)
    items: str = ""  # fetch: what he claimed to have brought and did not, as words
    follow: list[str] = field(default_factory=list)  # fetch: later steps that would have got the items
    found_out: bool = False


class VanillaQuests:
    """The scripts' quests for the conversational model (a TurnExtension) and the rules that find lies out."""

    def __init__(
        self,
        link: GameLink,
        dialogue: DialogueManager,
        chronicle: Chronicle,
        graph: StoryGraph,
        *,
        hero_distance: Callable[[str], float | None] | None = None,
        journal: Journal | None = None,
        trust: Trust | None = None,
    ) -> None:
        self.link = link
        self.dialogue = dialogue
        self.chronicle = chronicle
        self.graph = graph
        self.hero_distance = hero_distance or (lambda _npc: None)
        self.journal = journal or dialogue.journal
        self.trust = trust or Trust(chronicle)
        self.titles = topic_titles(graph)
        self._topic_uses = Counter(t for q in graph.quests.values() for t in q.topics)
        self.offers: dict[str, dict[str, Offer]] = {}  # npc -> quest -> offer, as of its last turn
        self.checking: set[str] = set()  # npc's gone to see for themselves
        self.told: dict[str, Told] = {}
        self._endings: dict[str, list[Node]] = {}  # npc -> dialogs of its that end a quest with success
        for q in graph.quests.values():
            for id in sorted(q.success):
                node = graph.nodes[id]
                self._endings.setdefault(node.npc, []).append(node)
        self._background: set[asyncio.Task[Any]] = set()
        dialogue.extensions.append(self)
        link.listeners.append(self._on_message)
        self._load()

    # --- the conversation (TurnExtension) ------------------------------------------------------

    async def prepare(self, npc: str) -> None:
        """The quests in progress this npc can accept, and what accepting each does, from the game."""
        npc = npc.upper()
        endings = self._endings.get(npc, [])
        if not endings:
            return
        quests = sorted({q for n in endings for q in _ends(n)})
        items = {i for n in endings for i in _items(n)}
        state = await self.link.story_state(vars=quests, items=sorted(items | {"ITMI_GOLD"}))
        names = state.get("items", {})
        offers: dict[str, Offer] = {}
        for node in endings:
            for quest in _ends(node):
                if state.get("vars", {}).get(quest) != LOG_RUNNING or quest in offers or quest in self.told:
                    continue
                if any(o.ending == node.id for o in offers.values()):
                    continue  # one ending of two quests (Andre's and Lobart's bugs): offered once
                offer = await self._offer(node, quest, npc, names)
                if offer is not None:
                    offers[quest] = offer
        await self._evidence(npc, list(offers.values()))
        self.offers[npc] = offers

    async def _evidence(self, npc: str, offers: list[Offer]) -> None:
        """What of each claim the npc can see for itself: the items the hero has on him, where the people
        that must be dead are."""
        items = sorted({i for o in offers for i in o.claim.items})
        dead = sorted({d for o in offers for d in o.claim.dead})
        have = (
            await self.link.story_eval(
                npc, [["call", "NPC_HASITEMS", ["inst", "OTHER"], ["sym", i]] for i in items]
            )
            if items
            else []
        )
        counts = {i: int(v or 0) for i, v in zip(items, have, strict=False)}
        people = (await self.link.story_state(npcs=dead, near=npc)).get("npcs", {}) if dead else {}
        for o in offers:
            o.have = {i: counts.get(i, 0) for i in o.claim.items}
            o.targets = [_target(d, people.get(d) or {}) for d in o.claim.dead]
            if o.targets:
                o.needs = f"будут мертвы: {_names(o.targets)}"

    def situation(self, npc: str) -> str | None:
        npc = npc.upper()
        lines = []
        for o in self.offers.get(npc, {}).values():
            seen = o.evidence()
            lines.append(
                f"Ты ждёшь от героя: «{o.title}» (id {o.quest}). Засчитать можно, когда {o.needs}. "
                f"Награда, как обещано: {o.reward}. "
                + (
                    f"Что ты видишь сам: {seen}."
                    if seen
                    else "Сделал ли он это — своими глазами ты не видел."
                )
            )
        for t in self.told.values():
            if t.npc != npc:
                continue
            if t.found_out:
                lines.append(f"Ты знаешь, что герой солгал тебе о деле «{t.title}» и забрал награду.")
            else:
                lines.append(f"Ты засчитал герою дело «{t.title}» и отдал награду: {t.reward}.")
        return "\n".join(lines) or None

    all_tools: tuple[Tool, ...] = (ACCEPT_TOOL, CHECK_TOOL, EXPOSE_TOOL)

    def tools(self, npc: str) -> list[Tool]:
        offers = list(self.offers.get(npc.upper(), {}).values())
        tools = [ACCEPT_TOOL] if offers else []
        if any(o.reachable() for o in offers):
            tools.append(CHECK_TOOL)
        if any(o.seen_false() for o in offers):
            tools.append(EXPOSE_TOOL)
        return tools

    async def handle(
        self, dialogue: DialogueManager, conv: Conversation, call: ToolCall, snap: Snapshot | None
    ) -> ToolOutcome | None:
        if call.name not in (ACCEPT, CHECK, EXPOSE):
            return None
        quest = str(call.arguments.get("quest", "")).upper()
        offers = self.offers.get(conv.npc, {})
        offer = offers.get(quest)
        if offer is None:
            return ToolOutcome("Отклонено: такого дела ты от героя не ждёшь.", "rejected")
        if call.name == CHECK:
            return self._go_check(conv, offer)
        if call.name == EXPOSE:
            return self._expose(conv, offer)
        # what it sees for itself, it does not take on his word
        if (t := offer.alive_in_view()) is not None:
            return ToolOutcome(
                f"Отклонено: {t.name} жив — у тебя на глазах, в {_m(t.distance or 0)} м. Своим глазам ты "
                "веришь больше, чем его словам.",
                "rejected",
            )
        if self.trust.broken(conv.npc) and not offer.seen_true():
            hint = f" Можешь сходить и посмотреть сам ({CHECK})." if offer.reachable() else ""
            return ToolOutcome(f"Отклонено: он уже лгал тебе. {WORD_BROKEN}{hint}", "rejected")
        offers.pop(quest)

        async def job() -> tuple[str, bool]:
            return await self._accept(offer, conv)

        dialogue.after_speech(conv, job)
        return ToolOutcome("Принято: засчитаешь и отдашь награду после реплики.", "accepted")

    def _go_check(self, conv: Conversation, offer: Offer) -> ToolOutcome:
        target = offer.reachable()
        if target is None:
            why = (
                "ты и так видишь, что он лжёт" if offer.seen_false() else "отсюда не дойти, чтобы посмотреть"
            )
            return ToolOutcome(f"Отклонено: {why}.", "rejected")
        self.offers[conv.npc].pop(offer.quest)

        async def job() -> tuple[str, bool]:
            return await self._check(offer, target, conv)

        self.dialogue.after_speech(conv, job)
        return ToolOutcome(f"Принято: после реплики пойдёшь смотреть сам ({target.name}).", "accepted")

    def _expose(self, conv: Conversation, offer: Offer) -> ToolOutcome:
        seen = offer.seen_false()
        if seen is None:
            return ToolOutcome(
                "Отклонено: уличить его тебе нечем — своими глазами ты не видишь, что он лжёт. Можешь не "
                "верить ему на слово.",
                "rejected",
            )
        self.offers[conv.npc].pop(offer.quest)
        lie = Lie(offer.quest, offer.npc, conv.persona.name, offer.title, offer.reward)

        async def job() -> tuple[str, bool]:
            await caught(self.link, self.dialogue, self.chronicle, self.journal, lie, f"видел сам: {seen}")
            return f"Ты уличил героя во лжи о деле «{offer.title}»", True

        self.dialogue.after_speech(conv, job)
        return ToolOutcome("Принято: ты уличил его; ты это запомнишь, люди вокруг услышат.", "accepted")

    # --- the ending -------------------------------------------------------------------------

    async def _offer(self, node: Node, quest: str, npc: str, names: dict[str, str]) -> Offer | None:
        """The ending as it would play if the hero's claim were true; None if it can't be played that way."""
        guard = next((e.get("when") for e in node.effects if _ends_effect(e, quest)), None)
        condition = node.condition
        if node.is_choice and node.parents and node.parents[0] in self.graph.nodes:
            condition = self.graph.nodes[node.parents[0]].condition
        claim = _claim(guard, condition, self.graph)

        def assume(e: Any) -> Any:
            return claim.assume(e, self.graph)

        # what fires in the claimed world: the ending's effects and its choices'
        choices = [
            self.graph.nodes[e["node"]]
            for e in node.effects
            if e["do"] == "choice" and e.get("node") in self.graph.nodes
        ]
        effects = [e for n in [node, *choices] for e in n.effects]
        exprs = [
            assume(_all(condition, guard) or ["int", 1]),
            *(assume(e.get("when") or ["int", 1]) for e in effects),
        ]
        try:
            values = await self.link.story_eval(npc, exprs)
        except (GameError, TimeoutError) as err:
            log.info("vanilla: %s of %s: %s", quest, npc, err)
            return None
        if len(values) != len(exprs) or not values[0]:
            return None  # even with the claim true it would not be offered now (another chapter...)
        fired = {id(e) for e, v in zip(effects, values[1:], strict=True) if v}
        played: list[dict[str, Any]] = []
        if not self._play(node, quest, fired, played):
            return None
        # the first choice that pays, as the hero would take it
        picked: list[dict[str, Any]] = []
        for c in choices:
            if not any(id(e) in fired for e in node.effects if e.get("node") == c.id):
                continue
            mine: list[dict[str, Any]] = []
            if self._play(c, quest, fired, mine) and (not picked or _pays(mine) and not _pays(picked)):
                picked = mine
        played += picked
        if not any(e["do"] == "set" and e["var"] == quest for e in played):
            return None
        reward = _reward(played, names)
        return Offer(
            quest,
            self._title(quest, node.id),
            npc,
            node.id,
            claim,
            _all(condition, guard) or ["int", 1],
            played,
            reward,
            _needs(claim, names, self.graph, node),
            names,
        )

    def _play(self, node: Node, quest: str, fired: set[int], out: list[dict[str, Any]]) -> bool:
        """The node's effects that fire, in the bridge's words; False if one of them can't be played."""
        for e in node.effects:
            if id(e) not in fired or e["do"] in (
                "line",
                "svm",
                "choice",
                "clear_choices",
                "end",
                "journal_topic",
            ):
                continue
            made = _bridge(e, self.titles, self.graph)
            if made is None:
                if e["do"] == "call" and str(e.get("fn", "")).startswith(COSMETIC_CALLS):
                    continue
                if e["do"] in ("create_items", "remove_items") and e.get("npc") == ["inst", "SELF"]:
                    continue  # the npc's own stock: B_GiveInvItems makes what it gives
                log.info("vanilla: %s can't be played: %s %s", quest, node.id, e["do"])
                return False
            out.append(made)
        return True

    async def _accept(self, offer: Offer, conv: Conversation, checked: bool = False) -> tuple[str, bool]:
        """The npc accepts: the ending plays; what the hero has of the items is taken, what he does not have
        is not, and that is a lie."""
        try:
            truth = (await self.link.story_eval(offer.npc, [offer.truth]))[0]
            have = await self.link.story_eval(
                offer.npc,
                [["call", "NPC_HASITEMS", ["inst", "OTHER"], ["sym", i]] for i in offer.claim.items],
            )
            counts = {i: int(v or 0) for i, v in zip(offer.claim.items, have, strict=False)}
            state = await self.link.story_state()
            await self.link.story_effect(offer.npc, _takes(offer.effects, counts, bool(truth)))
        except (GameError, TimeoutError) as err:
            log.warning("vanilla: %s not accepted: %s", offer.quest, err)
            return f"Ты не смог засчитать «{offer.title}»: {err}", False
        name = conv.persona.name
        lie = not truth
        told = Told(
            offer.quest,
            offer.npc,
            name,
            offer.title,
            offer.reward,
            lie,
            offer.claim.kind,
            int(state.get("day", 0)),
        )
        claim = {
            "fact": "claim",
            "quest": offer.quest,
            "npc": offer.npc,
            "name": name,
            "title": offer.title,
            "reward": offer.reward,
        }
        self.chronicle.record({**claim, "believed": True, "truth": bool(truth), "checked": checked})
        self.chronicle.record(
            {
                "fact": "belief",
                "npc": offer.npc,
                "quest": offer.quest,
                "claim": "done",
                "believed": True,
                "source": "hero",
            }
        )
        if lie:
            offer.have = counts
            told.items = offer.items(offer.missing())
            told.targets = list(offer.claim.dead)
            told.tag = f"{offer.quest}_ALIVE" if told.targets else ""
            told.follow = sorted(
                q.var
                for q in self.graph.quests.values()
                if offer.ending in q.running and q.var != offer.quest
            )
            had = ", ".join(
                f"{i} {v or 0} of {n}" for (i, n), v in zip(offer.claim.items.items(), have, strict=False)
            )
            truth_text = had or (
                "alive: " + ", ".join(told.targets) if told.targets else "the claim is false"
            )
            self.chronicle.record(
                {
                    "fact": "lie",
                    "quest": offer.quest,
                    "npc": offer.npc,
                    "name": name,
                    "title": offer.title,
                    "reward": offer.reward,
                    "kind": told.kind,
                    "truth": truth_text,
                    "day": told.day,
                    "tag": told.tag,
                    "targets": told.targets,
                    "items": told.items,
                    "follow": told.follow,
                }
            )
            self.journal.add("lie", npc=offer.npc, name=name, quest=offer.quest, truth=truth_text)
        self.told[offer.quest] = told
        await self._watch()
        return f"Ты засчитал герою «{offer.title}» и отдал награду: {offer.reward}", True

    async def _check(self, offer: Offer, target: Target, conv: Conversation) -> tuple[str, bool]:
        """The npc goes to see for itself, then comes back to the hero to say what it saw: he lied to it, or
        it pays."""
        self.checking.add(conv.npc)
        held = await self.dialogue.borrow_body(conv, "check")
        try:
            walked = await self._go(conv, target.npc, LOOK_CM) if held else "ты был занят"
            if held and not walked:
                await self._face(conv, target.npc)  # to see it, it looks that way
            try:
                people = (await self.link.story_state(npcs=offer.claim.dead, near=offer.npc)).get("npcs", {})
            except (GameError, TimeoutError) as err:
                return f"Ты не смог проверить: {err}", False
            seen = [t for t in (_target(d, people.get(d) or {}) for d in offer.claim.dead) if t.seen]
            alive = sorted((t for t in seen if t.alive), key=lambda t: t.distance or 0)
            if alive:
                t = alive[0]
                how = f"сходил проверить и увидел: {t.name} жив, в {_m(t.distance or 0)} м от тебя"
                lie = Lie(offer.quest, offer.npc, conv.persona.name, offer.title, offer.reward)
                await caught(self.link, self.dialogue, self.chronicle, self.journal, lie, how)
                note, ok = f"Ты {how}", True
                cue = f"Ты сходил проверить слова героя о деле «{offer.title}»: {t.name} жив. Он тебе солгал"
            elif not seen:
                return f"Ты не дошёл, чтобы посмотреть самому{f' ({walked})' if walked else ''}", False
            else:
                paid, ok = await self._accept(offer, conv, checked=True)
                dead = f"{_names(seen)} — {'мёртв' if len(seen) == 1 else 'мертвы'}"
                note = f"Ты сходил проверить: {dead}. {paid}"
                cue = f"Ты сходил проверить слова героя о деле «{offer.title}»: {dead}, он сказал правду"
                cue += f". {paid}"
            if held:
                await self._go(conv, "PLAYER", BACK_CM)  # to say it to his face
        finally:
            if held:
                self.dialogue.return_body(conv, "check")
            self.checking.discard(conv.npc)
        self._react(conv, cue)
        return note, ok

    async def _go(self, conv: Conversation, npc: str, radius: int) -> str:
        """The npc's body walks until it is `radius` cm from someone; what went wrong, or ''."""
        try:
            res = await self.link.act(conv.npc, "go_to_npc", npc=npc, radius=radius)
            return "" if res.status == "done" else res.detail or res.status
        except (GameError, TimeoutError) as err:
            return str(err) or type(err).__name__

    async def _face(self, conv: Conversation, npc: str) -> None:
        try:
            await self.link.act(conv.npc, "turn_to", timeout=10.0, npc=npc)
        except (GameError, TimeoutError) as err:
            log.info("vanilla: %s does not turn to %s: %s", conv.npc, npc, err)

    def _react(self, conv: Conversation, cue: str) -> None:
        """The npc may say what it thinks of what it saw (the consequences are done already)."""
        self.dialogue.event_turn(conv.npc, f"[{cue}]", tools=("set_intent",))

    # --- finding out --------------------------------------------------------------------------

    async def run(self) -> None:
        """The rules that find lies out: people alive near the npc, once a second; missing goods, mornings."""
        last = 0.0
        while True:
            await asyncio.sleep(CHECK_EVERY_S)
            for t in list(self.told.values()):
                if t.lie and not t.found_out and t.tag:
                    d = self.chronicle.seen(t.tag, t.npc)
                    if d is not None and d <= SEE_CM:
                        await self.found_out(
                            t,
                            f"увидел живым того, кого герой будто бы убил, в {_m(d)} м от себя",
                        )
            if asyncio.get_running_loop().time() - last >= MORNING_EVERY_S:
                last = asyncio.get_running_loop().time()
                await self._mornings()

    async def _mornings(self) -> None:
        pending = [t for t in self.told.values() if t.lie and not t.found_out and t.kind == "fetch"]
        if not pending:
            return
        follow = sorted({q for t in pending for q in t.follow})
        try:
            state = await self.link.story_state(vars=follow, npcs=[t.npc for t in pending])
        except (GameError, TimeoutError):
            return
        day = int(state.get("day", 0))
        for t in pending:
            if day <= t.day or (state.get("npcs", {}).get(t.npc) or {}).get("state") != "alive":
                continue
            if any(state.get("vars", {}).get(q) == LOG_SUCCESS for q in t.follow):
                # a later step got the goods after all
                t.found_out = True
                self.chronicle.record({"fact": "lie_mended", "quest": t.quest, "npc": t.npc})
                continue
            await self.found_out(
                t, f"наутро хватился того, что герой будто бы принёс ({t.items}), — а этого нет"
            )

    async def found_out(self, t: Told, how: str) -> None:
        t.found_out = True
        lie = Lie(t.quest, t.npc, t.npc_name, t.title, t.reward)
        await unmask(self.link, self.dialogue, self.chronicle, self.journal, lie, how, self.hero_distance)
        await self._watch()

    async def _watch(self) -> None:
        """The people that must be dead, watched near the npc's that were lied to."""
        open_ = [t for t in self.told.values() if t.lie and not t.found_out and t.tag]
        try:
            for t in open_:
                await self.link.story_tag(t.tag, t.targets)
            await self.link.story_watch(sorted({t.npc for t in open_}), owner="vanilla")
        except (GameError, TimeoutError) as err:
            log.info("vanilla: no watch: %s", err)

    # --- the save branch --------------------------------------------------------------------

    def _load(self) -> None:
        """The claims of the branch, from its facts (after a load: as they were at that save)."""
        self.told.clear()
        self.offers.clear()
        for f in self.chronicle.facts():
            quest = f.get("quest", "")
            if quest not in self.graph.quests:
                continue
            match f.get("fact"):
                case "claim":
                    name, reward = f.get("name", f["npc"]), f.get("reward", "")
                    self.told[quest] = Told(
                        quest, f["npc"], name, self._title(quest), reward, lie=not f.get("truth")
                    )
                case "lie" if quest in self.told:
                    t = self.told[quest]
                    t.npc_name, t.title, t.reward = (
                        f.get("name", t.npc),
                        f.get("title", t.title),
                        f.get("reward", ""),
                    )
                    t.kind, t.day, t.tag = f.get("kind", ""), int(f.get("day", 0)), f.get("tag", "")
                    t.targets, t.items, t.follow = (
                        list(f.get("targets", [])),
                        f.get("items", ""),
                        list(f.get("follow", [])),
                    )
                case "found_out" | "lie_mended" if quest in self.told:
                    self.told[quest].found_out = True

    def _on_message(self, msg: GameMessage) -> None:
        if isinstance(msg, Session):
            self._load()
            task = asyncio.create_task(self._watch())  # the game forgot its tags with the old world
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    def _title(self, quest: str, ending: str | None = None) -> str:
        """The quest's name in the hero's journal: its least shared topic. A topic many quests write to (the
        militia's) gives way to that of another quest the same ending closes (Lobart's field raiders)."""
        quests = [quest]
        if ending in self.graph.nodes:
            quests += [q for q in _ends(self.graph.nodes[ending]) if q != quest]
        titles = [
            (self._topic_uses.get(topic, 0), i, title)
            for i, q in enumerate(quests)
            if q in self.graph.quests
            for topic, title in self.graph.quests[q].topics.items()
            if title
        ]
        return min(titles)[2] if titles else quest


# --- the lifted ending, in words the bridge plays ------------------------------------------------


def _ends(node: Node) -> list[str]:
    """Quests the node sets to success."""
    return [
        e["var"]
        for e in node.effects
        if e["do"] == "set" and _int(e.get("value")) == LOG_SUCCESS and e.get("var", "").startswith("MIS_")
    ]


def _ends_effect(e: dict[str, Any], quest: str) -> bool:
    return e["do"] == "set" and e.get("var") == quest and _int(e.get("value")) == LOG_SUCCESS


def _items(node: Node) -> set[str]:
    out = set()
    for e in node.effects:
        for x in walk(e.get("when")):
            if x[0] == "call" and x[1] == "NPC_HASITEMS" and len(x) == 4:
                out.add(_sym(x[3]))
        if e["do"] in ("give", "create_items", "remove_items"):
            out.add(_sym(e.get("item")))
    for x in walk(node.condition):
        if x[0] == "call" and x[1] == "NPC_HASITEMS" and len(x) == 4:
            out.add(_sym(x[3]))
    out.discard("")
    return out


def _claim(guard: Expr | None, condition: Expr | None, graph: StoryGraph) -> Claim:
    """What the hero claims by saying the quest is done: the ending's guard, and in its condition what
    is about him (items, dialogs heard) or about someone being dead."""
    c = Claim()

    def terms(e: Expr | None) -> list[Expr]:
        if e is None:
            return []
        if e[0] == "&&":
            return [t for a in e[1:] for t in terms(a)]
        if e[0] == "||":
            return terms(e[1])  # the first way it can be done
        return [e]

    for from_guard, e in [(True, t) for t in terms(guard)] + [(False, t) for t in terms(condition)]:
        if e[0] in (">=", ">", "==") and len(e) == 3 and _int(e[2]) is not None:
            call, n = e[1], _int(e[2]) or 0
            if call[:2] == ["call", "NPC_HASITEMS"] and len(call) == 4 and call[2] in HERO:
                item = _sym(call[3])
                c.items[item] = max(c.items.get(item, 0), n + 1 if e[0] == ">" else n)
            elif from_guard and e[0] == "==" and call[:1] == ["var"] and len(call) == 2:
                c.vars[call[1]] = n
        elif e[:2] == ["call", "NPC_ISDEAD"] and len(e) == 3 and (npc := _npc(e[2], graph)):
            c.dead.append(npc)
        elif e[:2] == ["call", "NPC_KNOWSINFO"] and len(e) == 4 and e[2] in HERO:
            c.knows.append(_sym(e[3]))
    return c


def _bridge(e: dict[str, Any], titles: dict[str, str], graph: StoryGraph) -> dict[str, Any] | None:
    """One lifted effect as the bridge plays it; None if it can't."""
    match e["do"]:
        case "routine" if e.get("routine") and e.get("npc") == ["inst", "SELF"]:
            return {"do": "routine", "routine": str(e["routine"]).upper()}
        case "routine" if e.get("routine") and (who := _npc(e.get("npc"), graph)):
            return {"do": "routine", "npc": who, "routine": str(e["routine"]).upper()}
        case "set" if _int(e.get("value")) is not None:
            return {"do": "set", "var": e["var"], "value": _int(e["value"])}
        case "xp" if _int(e.get("amount")) is not None:
            return {"do": "xp", "amount": _int(e["amount"])}
        case "give" if _sym(e.get("item")) and _int(e.get("amount")) is not None:
            item, n = _sym(e["item"]), _int(e["amount"])
            if e.get("from") == ["inst", "SELF"] and e.get("to") in HERO:
                return {"do": "give", "item": item, "count": n}
            if e.get("from") in HERO and e.get("to") == ["inst", "SELF"]:
                return {"do": "give", "item": item, "count": n, "to": "self", "take": True}
        case "create_items" if (
            e.get("npc") in HERO and _sym(e.get("item")) and _int(e.get("amount")) is not None
        ):
            return {"do": "give", "item": _sym(e["item"]), "count": _int(e["amount"])}
        case "remove_items" if (
            e.get("npc") in HERO and _sym(e.get("item")) and _int(e.get("amount")) is not None
        ):
            return {
                "do": "give",
                "item": _sym(e["item"]),
                "count": _int(e["amount"]),
                "to": "self",
                "take": True,
            }
        case "journal_entry" if e.get("topic") in titles:
            return {"do": "journal", "topic": titles[e["topic"]], "text": e.get("text", "")}
        case "journal_status" if e.get("topic") in titles and _int(e.get("status")) in STATUS:
            return {
                "do": "journal",
                "topic": titles[e["topic"]],
                "text": "",
                "status": STATUS[_int(e["status"]) or 1],
            }
    return None


def _takes(effects: list[dict[str, Any]], have: dict[str, int], truth: bool) -> list[dict[str, Any]]:
    """The ending's effects as they play: of the items it takes, what the hero has (all, if he did it)."""
    left = dict(have)
    out = []
    for e in effects:
        e = {k: v for k, v in e.items() if k != "take"}
        if e["do"] == "give" and e.get("to") == "self" and not truth:
            n = min(int(e["count"]), left.get(e["item"], 0))
            if n <= 0:
                continue
            left[e["item"]] -= n
            e["count"] = n
        out.append(e)
    return out


def _target(npc: str, state: dict[str, Any]) -> Target:
    d, seen = state.get("distance"), state.get("seen")
    return Target(
        npc,
        state.get("name") or npc,
        state.get("state") == "alive",
        None if d is None else float(d),
        None if seen is None else bool(seen),
    )


def _names(targets: list[Target]) -> str:
    """Their names, grouped: «Полевой хищник ×3, Бандит»."""
    counts: dict[str, int] = {}
    for t in targets:
        counts[t.name] = counts.get(t.name, 0) + 1
    return ", ".join(f"{n} ×{c}" if c > 1 else n for n, c in counts.items())


def _pays(effects: list[dict[str, Any]]) -> bool:
    return any(e["do"] == "give" and not e.get("take") for e in effects)


def _reward(effects: list[dict[str, Any]], names: dict[str, str]) -> str:
    parts = [
        f"{names.get(e['item'], e['item'])} ×{e['count']}"
        for e in effects
        if e["do"] == "give" and not e.get("take")
    ]
    parts += [f"опыт +{e['amount']}" for e in effects if e["do"] == "xp"]
    return ", ".join(parts) or "ничего, кроме благодарности"


def _needs(claim: Claim, names: dict[str, str], graph: StoryGraph, node: Node) -> str:
    if claim.items:
        return "герой принесёт тебе " + ", ".join(f"{names.get(i, i)} ×{n}" for i, n in claim.items.items())
    if claim.dead:
        return "будут мертвы: " + ", ".join(claim.dead)
    if claim.knows:
        said = [graph.nodes[k].description for k in claim.knows if k in graph.nodes]
        return "герой поговорит об этом: " + "; ".join(f"«{s}»" for s in said if s)
    return f"герой скажет «{node.description}»"


def _all(*parts: Expr | None) -> Expr | None:
    found = [p for p in parts if p is not None]
    return None if not found else found[0] if len(found) == 1 else ["&&", *found]


def _npc(e: Any, graph: StoryGraph) -> str:
    if isinstance(e, list) and e[:1] == ["inst"] and e[1] not in ("SELF", "OTHER", "HERO"):
        return graph.npc(e[1])
    return ""


def _m(cm: float) -> int:
    return max(1, round(cm / 100))


def _int(e: Any) -> int | None:
    if isinstance(e, int) and not isinstance(e, bool):
        return e
    return int(e[1]) if isinstance(e, list) and e[:1] == ["int"] else None


def _sym(e: Any) -> str:
    return str(e[1]).upper() if isinstance(e, list) and e[:1] == ["sym"] else ""
