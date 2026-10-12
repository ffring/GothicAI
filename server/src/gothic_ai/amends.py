"""The director's own storylets, v0 (docs/story-graph.md, stage 5): a lie comes home.

When someone the hero deals with learns that he lied — it was lied to and found out, it caught him, or a
rumor reached it (social.py) — the director may have it ask him to make amends, as a writer of the game
would. The model proposes; the code builds the change, the validator checks it, the game plays it:
- the talk: a dialog of the director's that the npc starts when the hero comes to it (important): what it
  heard and what it wants;
- what it closes until then: its dialogs the hero would come for — work, joining, teaching, trade —
  switched off;
- the amends: a dialog of the director's, at it or at the one lied to, offered once the talk is over:
  paying a fine or the reward back (gold taken from the hero), never more than MAX_GOLD;
- once he has made amends the dialogs open again, the npc (and the one lied to) remember it, and trust.py
  counts it.

Its state is in the chronicle (`amends_*` facts) and in the patches of the save branch, so a load brings
back exactly where it was. A lie gets STORYLETS_PER_LIE of them at most, the one lied to first, then those
the rumor reached, in the order they heard it; the model may find that one does not care, and is asked
about LOOKS_PER_LIE people at most.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from .chronicle import Chronicle, Fact
from .game import GameError, GameLink
from .gamedata.storygraph import Node, StoryGraph
from .journal import Journal
from .llm import LLMBackend, LLMError, Message, Tool, ToolCall
from .protocol import DialogChosen, GameMessage, Session, Snapshot
from .story import StoryPatches
from .turn import ToolOutcome
from .validator import DIRECTOR_PREFIX, Rules, Story, names_in, validate

if TYPE_CHECKING:
    from .dialogue import Conversation, DialogueManager
    from .director import Director

log = logging.getLogger(__name__)

PROPOSE = "propose_amends"
STORYLETS_PER_LIE = 2  # opened
LOOKS_PER_LIE = 4  # people the model is asked about, for one lie
MAX_GOLD = 100  # the most a fine may be
MAX_CLOSE = 4
ATTEMPTS = 2
# what the hero comes to an npc for: what it may close until he makes amends
STAKES = {"join": "принимает в гильдию", "work": "даёт работу", "teach": "учит", "trade": "торгует"}
SKIP = ("PICKPOCKET", "EXIT", "PETZ", "PMSCHULDEN")  # the scripts' own crime and pickpocket dialogs

INSTRUCTIONS = """\
Ты — режиссёр сюжета Gothic II. Код следит за миром и зовёт тебя, когда о лжи героя узнал человек, с которым \
у героя есть дела: обманутый сам, тот, кто его уличил, или тот, до кого дошёл слух. Реши, как этот человек \
поступит, — как сделал бы сценарист оригинальной игры: в характере, бережно к миру и тону.

- amends: он требует загладить вину. Пока герой не заплатит, он закрывает для него свои дела — те из \
списка, которые ему естественно закрыть (close, по id; можно не все). Ты пишешь разговор, который он начнёт \
сам (lines: 2–6 реплик; npc — он, hero — герой): что он слышал и от кого, что требует. Расплата — золото \
(gold, не больше предела из сводки; 0 — достаточно повиниться) ему самому (to: self) или обманутому \
(to: victim), строка героя в меню (description, до 60 знаков, например «Вот штраф — 50 золотых»), разговор \
при расплате (amends_lines: 1–4 реплики), название темы журнала (title) и две записи от первого лица: \
journal_now — что от героя требуют, journal_done — что он заплатил.
- none: ему всё равно или это не его дело. Остальные поля пустые.

Строго по сводке: не выдумывай, чего человек не знает. Тот, кто знает со слухов, может и сомневаться. \
Речь — по-русски, в духе Gothic: просто, грубовато, без пафоса и современных слов. Ответ — один вызов \
propose_amends."""

_LINES = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"speaker": {"type": "string", "enum": ["hero", "npc"]}, "text": {"type": "string"}},
        "required": ["speaker", "text"],
        "additionalProperties": False,
    },
}
PROPOSE_TOOL = Tool(
    PROPOSE,
    "Предложить, как человек из сводки поступит с героем, узнав о его лжи. Код проверит предложение и "
    "применит его или вернёт причины отказа.",
    {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["amends", "none"]},
            "close": {"type": "array", "items": {"type": "string"}, "description": "id его дел из сводки"},
            "lines": _LINES,
            "to": {"type": "string", "enum": ["self", "victim"]},
            "gold": {"type": "integer"},
            "description": {"type": "string"},
            "amends_lines": _LINES,
            "title": {"type": "string"},
            "journal_now": {"type": "string"},
            "journal_done": {"type": "string"},
            "reason": {"type": "string", "description": "почему так, одной фразой"},
        },
        "required": [
            "kind",
            "close",
            "lines",
            "to",
            "gold",
            "description",
            "amends_lines",
            "title",
            "journal_now",
            "journal_done",
            "reason",
        ],
        "additionalProperties": False,
    },
)


@dataclass
class Lie:
    """A lie of the hero's that came out, as the chronicle has it."""

    quest: str
    kind: str  # found_out: he took a reward for it; caught: he tried to
    victim: str
    victim_name: str
    title: str
    reward: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.victim}:{self.quest}"

    def words(self) -> str:
        who = f"обманул человека по имени {self.victim_name}"
        if self.kind == "found_out":
            return f"{who}: взял награду ({self.reward or 'какую-то'}) за «{self.title}», а дела не сделал"
        return f"{who}: уверял, что сделал «{self.title}», а это неправда; награды не получил"


@dataclass
class Stake:
    """A dialog of the npc's the hero would come for."""

    id: str
    description: str
    what: str  # STAKES' words


@dataclass
class Brief:
    lie: Lie
    npc: str
    name: str
    guild: str
    how: str  # how it knows: was lied to, caught him, heard from whom
    stakes: list[Stake]
    victim_alive: bool
    asked: list[str] = field(default_factory=list)  # what others already ask of the hero for this lie

    def text(self) -> str:
        lines = [
            f"Человек: {self.name} ({self.npc}){f', {self.guild}' if self.guild else ''}.",
            f"Что он знает: герой {self.lie.words()}. Откуда: {self.how}.",
            f"Обманутый: {self.lie.victim_name} ({self.lie.victim}), "
            f"{'жив' if self.victim_alive else 'мёртв'}.",
            "Его дела, ради которых герой к нему ходит (можно закрыть до расплаты):",
            *(f"- {s.id}: «{s.description}» — {s.what}" for s in self.stakes),
            f"Предел расплаты: {MAX_GOLD} золотых.",
        ]
        if self.asked:
            lines.append(
                "За эту ложь уже требуют (если он заодно с ними, можно потребовать того же — тогда одна "
                "расплата закроет оба требования): " + "; ".join(self.asked)
            )
        return "\n".join(lines)


@dataclass
class Proposal:
    kind: str
    close: list[str] = field(default_factory=list)
    lines: list[dict[str, str]] = field(default_factory=list)
    to: str = "self"
    gold: int = 0
    description: str = ""
    amends_lines: list[dict[str, str]] = field(default_factory=list)
    title: str = ""
    journal_now: str = ""
    journal_done: str = ""
    reason: str = ""

    @classmethod
    def of(cls, args: dict[str, Any]) -> Proposal:
        fields = {k: args[k] for k in cls.__dataclass_fields__ if k in args}
        try:
            fields["gold"] = int(fields.get("gold") or 0)
        except (TypeError, ValueError):
            fields["gold"] = -1
        fields["close"] = [str(x).upper() for x in fields.get("close") or []]
        return cls(**fields)


@dataclass
class Storylet:
    """An npc asking the hero to make amends for a lie, and where it stands."""

    id: str
    lie: str  # Lie.key
    npc: str
    name: str
    close: list[str]
    to: str  # npc symbol the amends are made at
    to_name: str
    gold: int
    title: str
    talk: str  # the director's dialogs
    pay: str
    pay_node: dict[str, Any]
    description: str
    stage: str = "open"  # open -> told (the talk is over) -> done (amends made)


class AmendsModel:
    """The cloud model that writes the storylets."""

    def __init__(self, llm: LLMBackend) -> None:
        self.llm = llm

    async def propose(
        self, brief: Brief, previous: Proposal | None = None, errors: list[str] | None = None
    ) -> Proposal | None:
        messages = [Message("user", brief.text())]
        if previous is not None and errors:
            sent = json.dumps(asdict(previous), ensure_ascii=False)
            reasons = "\n".join(f"- {e}" for e in errors)
            messages.append(Message("user", f"Твоё предложение: {sent}\nОтклонено:\n{reasons}\nИсправь его."))
        calls: list[ToolCall] = []
        try:
            async for ev in self.llm.stream(
                instructions=INSTRUCTIONS, messages=messages, tools=[PROPOSE_TOOL]
            ):
                if isinstance(ev, ToolCall) and ev.name == PROPOSE:
                    calls.append(ev)
        except LLMError as err:
            log.warning("amends: the model failed: %s", err)
            return None
        return Proposal.of(calls[0].arguments) if calls else None


class Amends:
    """Storylets of amends for the hero's lies; also a TurnExtension: the npc knows what it asked of him."""

    def __init__(
        self,
        link: GameLink,
        graph: StoryGraph,
        patches: StoryPatches,
        chronicle: Chronicle,
        director: Director,
        llm: LLMBackend,
        *,
        dialogue: DialogueManager | None = None,
        journal: Journal | None = None,
    ) -> None:
        self.link = link
        self.graph = graph
        self.patches = patches
        self.chronicle = chronicle
        self.director = director
        self.model = AmendsModel(llm)
        self.dialogue = dialogue
        self.journal = journal or Journal()
        self.rules = Rules.of(graph)
        self.lies: dict[str, Lie] = {}
        self.storylets: dict[str, Storylet] = {}  # by id
        self.looked: dict[str, list[str]] = {}  # lie -> npc's looked at for it
        self._queue: asyncio.Queue[tuple[str, str, str] | None] = asyncio.Queue()
        self._background: set[asyncio.Task[Any]] = set()
        chronicle.on_fact.append(self._on_fact)
        link.listeners.append(self._on_message)
        if dialogue is not None:
            dialogue.extensions.append(self)
        self._load()

    # --- watching -----------------------------------------------------------------------------

    def _on_fact(self, f: Fact) -> None:
        match f.get("fact"):
            case "found_out" | "caught":
                lie = Lie(
                    f.get("quest", ""),
                    f["fact"],
                    f.get("npc", ""),
                    f.get("name") or f.get("npc", ""),
                    f.get("title") or f.get("quest", ""),
                    self._reward(f.get("quest", "")),
                )
                self.lies.setdefault(lie.key, lie)
                self._queue.put_nowait((lie.key, lie.victim, "lied"))
            case "rumor":
                key = (
                    f"{f.get('kind', 'found_out')}:{f.get('about') or f.get('from', '')}:{f.get('quest', '')}"
                )
                self._queue.put_nowait((key, f.get("to", ""), f.get("from_name") or ""))

    def _on_message(self, msg: GameMessage) -> None:
        match msg:
            case Session():
                self._load()
            case DialogChosen(made=True):
                for s in list(self.storylets.values()):
                    if msg.node == s.talk and s.stage == "open":
                        self._spawn(self._told(s))
                    elif msg.node == s.pay and s.stage != "done":
                        self._spawn(self._paid(s))

    async def run(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                continue
            key, npc, how = item
            try:
                await self.consider(key, npc.upper(), how)
            except (GameError, TimeoutError) as err:
                log.warning("amends: %s", err)

    # --- a storylet ---------------------------------------------------------------------------

    async def consider(self, key: str, npc: str, how: str) -> Storylet | None:
        """Whether `npc`, who has just learnt of the lie `key`, asks the hero to make amends."""
        lie = self.lies.get(key)
        looked = self.looked.setdefault(key, [])
        opened = sum(1 for s in self.storylets.values() if s.lie == key)
        if lie is None or not npc or npc in looked:
            return None
        if len(looked) >= LOOKS_PER_LIE or opened >= STORYLETS_PER_LIE:
            return None
        if any(s.npc == npc and s.stage != "done" for s in self.storylets.values()):
            return None
        if self.rules.plot_npc(npc):
            return None
        stakes = self.stakes(npc)
        if not stakes:
            return None  # nothing he comes to it for: no reason to write a storylet
        await self.director.pacing.wait(self.director.busy)
        try:
            story = await self.director.story()
            if not story.state.alive(npc):
                return None
            stakes = [s for s in stakes if story.can_offer(s.id)]
            if not stakes:
                return None
            looked.append(npc)
            brief = Brief(
                lie,
                npc,
                story.state.name(npc),
                "",
                _how(how, lie),
                stakes,
                story.state.alive(lie.victim),
                [
                    f"{x.name} — {_price(x)}"
                    for x in self.storylets.values()
                    if x.lie == key and x.stage != "done"
                ],
            )
            previous: Proposal | None = None
            errors: list[str] = []
            for attempt in range(1, ATTEMPTS + 1):
                proposal = await self.model.propose(brief, previous, errors)
                log.info("amends: %s for %s, proposal %d: %s", npc, key, attempt, proposal)
                if proposal is None:
                    errors, previous = ["нет предложения: ответ — вызов propose_amends"], Proposal(kind="")
                    continue
                if proposal.kind == "none":
                    self.chronicle.record(
                        {"fact": "amends_none", "lie": key, "npc": npc, "reason": proposal.reason}
                    )
                    return None
                made, errors = await self._try(proposal, brief, story)
                if made is not None:
                    return made
                previous = proposal
                self.chronicle.record({"fact": "amends_rejected", "lie": key, "npc": npc, "why": errors})
            return None
        finally:
            self.director.pacing.stepped()

    def stakes(self, npc: str) -> list[Stake]:
        """Its dialogs the hero would come for, as the graph has them."""
        out = []
        for node in self.graph.dialogs_of(npc):
            if any(s in node.id for s in SKIP) or node.id in self.rules.plot_nodes:
                continue
            what = _stake(node, self.graph)
            if what:
                out.append(Stake(node.id, node.description, STAKES[what]))
        return out

    async def _try(self, p: Proposal, brief: Brief, story: Story) -> tuple[Storylet | None, list[str]]:
        errors = []
        stakes = {s.id for s in brief.stakes}
        if p.kind != "amends":
            return None, [f"вид: amends или none, а не {p.kind!r}"]
        if not p.close or len(p.close) > MAX_CLOSE or not set(p.close) <= stakes:
            errors.append(f"close: от 1 до {MAX_CLOSE} id из списка его дел")
        if p.to not in ("self", "victim"):
            errors.append("to: self или victim")
        elif p.to == "victim" and not brief.victim_alive:
            errors.append(f"{brief.lie.victim_name} мёртв: расплата — ему самому (to: self)")
        if not 0 <= p.gold <= MAX_GOLD:
            errors.append(f"gold: от 0 до {MAX_GOLD}")
        if not 2 <= len(p.lines) <= 6 or not 1 <= len(p.amends_lines) <= 4:
            errors.append("lines: 2–6 реплик, amends_lines: 1–4")
        if not (
            p.title.strip() and p.journal_now.strip() and p.journal_done.strip() and p.description.strip()
        ):
            errors.append("нужны title, description, journal_now и journal_done")
        if errors:
            return None, errors
        n = 1 + sum(1 for s in self.storylets.values() if s.npc == brief.npc)
        base = f"{DIRECTOR_PREFIX}AMENDS_{brief.npc}_{n}"
        to = brief.npc if p.to == "self" else brief.lie.victim
        talk = {
            "id": f"{base}_TALK",
            "npc": brief.npc,
            "description": "",
            "important": True,
            "nr": 1,
            "condition": None,
            "lines": _lines(p.lines),
            "effects": [{"do": "journal", "topic": p.title, "text": p.journal_now}],
        }
        pay_effects: list[dict[str, Any]] = []
        if p.gold > 0:
            pay_effects.append({"do": "give", "item": "ITMI_GOLD", "count": p.gold, "to": "self"})
        pay_effects.append(
            {"do": "journal", "topic": p.title, "text": p.journal_done, "status": "LOG_SUCCESS"}
        )
        has_gold = [">=", ["call", "NPC_HASITEMS", ["inst", "OTHER"], ["sym", "ITMI_GOLD"]], ["int", p.gold]]
        pay = {
            "id": f"{base}_PAY",
            "npc": to,
            "description": p.description,
            "nr": 1,
            "condition": has_gold if p.gold > 0 else None,
            "lines": _lines(p.amends_lines),
            "effects": pay_effects,
        }
        ops = [{"op": "add_node", "node": talk}, *({"op": "disable", "node": c} for c in p.close)]
        story = await self.director.with_symbols(
            story, names_in([*ops, {"op": "add_node", "node": pay}]) | {to}
        )
        errors = validate([*ops, {"op": "add_node", "node": pay}], story, self.rules)
        if errors:
            return None, errors
        for op in ops:
            await self.patches.apply(op)
        s = Storylet(
            base,
            brief.lie.key,
            brief.npc,
            brief.name,
            p.close,
            to,
            story.state.name(to),
            p.gold,
            p.title,
            talk["id"],
            pay["id"],
            pay,
            p.description,
        )
        self.storylets[s.id] = s
        self.chronicle.record({"fact": "amends_open", **asdict(s), "reason": p.reason})
        self.journal.add("amends_open", npc=s.npc, name=s.name, lie=s.lie, close=s.close, gold=s.gold)
        if self.dialogue is not None:
            self.dialogue.remember(
                s.npc,
                s.name,
                f"Ты решил: пока герой не загладит вину за ложь ({brief.lie.words()}), твои дела для него "
                f"закрыты. Расплата: {_price(s)}",
            )
        log.info("amends: %s opened (%s): %s", s.id, ", ".join(s.close), p.reason)
        return s, []

    async def _told(self, s: Storylet) -> None:
        """The talk is over: the amends are offered."""
        s.stage = "told"
        try:
            await self.patches.apply({"op": "add_node", "node": s.pay_node})
        except GameError as err:
            log.warning("amends: %s: %s", s.id, err)
            return
        self.chronicle.record({"fact": "amends_told", "id": s.id})

    async def _paid(self, s: Storylet) -> None:
        """The hero made amends: the npc's dialogs open again; so do those of the others asking the same of
        him for the same lie (one confession to Lobart is enough for Hilda too)."""
        await self._settle(s)
        for other in list(self.storylets.values()):
            if other.lie == s.lie and other.to == s.to and other.stage != "done" and other.gold <= s.gold:
                for node in (other.talk, other.pay):
                    if node in self.patches_nodes():
                        await self._apply({"op": "remove_node", "node": node})
                await self._settle(other)

    def patches_nodes(self) -> set[str]:
        return {op["node"]["id"] for op in self.patches.ops() if op.get("op") == "add_node"}

    async def _apply(self, op: dict[str, Any]) -> None:
        try:
            await self.patches.apply(op)
        except GameError as err:
            log.warning("amends: %s: %s", op, err)

    async def _settle(self, s: Storylet) -> None:
        s.stage = "done"
        for c in s.close:
            await self._apply({"op": "enable", "node": c})
        lie = self.lies.get(s.lie)
        fact = {"fact": "amends", "id": s.id, "npc": s.npc, "to": s.to, "gold": s.gold, "lie": s.lie}
        self.chronicle.record({**fact, "quest": lie.quest if lie else "", "about": lie.victim if lie else ""})
        self.journal.add("amends", npc=s.npc, name=s.name, to=s.to, gold=s.gold)
        if self.dialogue is not None:
            self.dialogue.remember(
                s.npc, s.name, f"Герой загладил вину ({_price(s)}). Твои дела для него снова открыты"
            )
            if s.to != s.npc:
                paid = f" и заплатил тебе {s.gold} золотых" if s.gold else ""
                self.dialogue.remember(s.to, s.to_name, f"Герой пришёл к тебе повиниться за ложь{paid}")

    # --- the save branch --------------------------------------------------------------------

    def _load(self) -> None:
        self.lies.clear()
        self.storylets.clear()
        self.looked.clear()
        for f in self.chronicle.facts():
            match f.get("fact"):
                case "found_out" | "caught":
                    lie = Lie(
                        f.get("quest", ""),
                        f["fact"],
                        f.get("npc", ""),
                        f.get("name") or f.get("npc", ""),
                        f.get("title") or f.get("quest", ""),
                        self._reward(f.get("quest", "")),
                    )
                    self.lies.setdefault(lie.key, lie)
                case "amends_open":
                    fields = {k: f[k] for k in Storylet.__dataclass_fields__ if k in f}
                    s = Storylet(**fields)
                    self.storylets[s.id] = s
                    self.looked.setdefault(s.lie, []).append(s.npc)
                case "amends_none" | "amends_rejected":
                    looked = self.looked.setdefault(f.get("lie", ""), [])
                    if f.get("npc") not in looked:
                        looked.append(f.get("npc", ""))
                case "amends_told" if f.get("id") in self.storylets:
                    self.storylets[f["id"]].stage = "told"
                case "amends" if f.get("id") in self.storylets:
                    self.storylets[f["id"]].stage = "done"

    def _reward(self, quest: str) -> str:
        """What the hero took for the lie, from the chronicle."""
        for f in reversed(self.chronicle.facts("claim")):
            if f.get("quest") == quest:
                return str(f.get("reward", ""))
        return ""

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # --- the conversation (TurnExtension) ------------------------------------------------------

    async def prepare(self, npc: str) -> None:
        pass

    def situation(self, npc: str) -> str | None:
        npc = npc.upper()
        lines = []
        for s in self.storylets.values():
            names = [self.graph.nodes[c].description for c in s.close if c in self.graph.nodes]
            if s.npc == npc and s.stage != "done":
                lines.append(
                    f"Ты требуешь от героя загладить вину за ложь: {_price(s)}. Пока не заплатит, для него "
                    f"закрыто: {'; '.join(f'«{n}»' for n in names)}."
                )
            elif s.npc == npc:
                lines.append(f"Герой загладил перед тобой вину за ложь ({_price(s)}).")
            elif s.to == npc and s.stage != "done":
                lines.append(
                    f"{s.name} требует, чтобы герой повинился перед тобой и заплатил тебе {s.gold} золотых."
                )
        return "\n".join(lines) or None

    def tools(self, npc: str) -> list[Tool]:
        return []

    async def handle(
        self, dialogue: DialogueManager, conv: Conversation, call: ToolCall, snap: Snapshot | None
    ) -> ToolOutcome | None:
        return None


def _stake(node: Node, graph: StoryGraph, seen: set[str] | None = None) -> str | None:
    """What of STAKES the dialog (or a choice of it) gives the hero."""
    seen = seen if seen is not None else set()
    if node.id in seen:
        return None
    seen.add(node.id)
    if node.trade:
        return "trade"
    found = None
    for e in node.effects:
        if e["do"] == "set_guild":
            return "join"
        if e["do"] == "call" and str(e.get("fn", "")).startswith("B_TEACH"):
            found = found or "teach"
        if e["do"] == "set" and str(e.get("var", "")).startswith("MIS_") and _value(e.get("value")) == 1:
            found = found or "work"
        if e["do"] == "choice" and (c := graph.nodes.get(e.get("node", ""))) is not None:
            found = _stake(c, graph, seen) or found
    return found


def _value(v: Any) -> int | None:
    if isinstance(v, int):
        return v
    return int(v[1]) if isinstance(v, list) and v[:1] == ["int"] else None


def _lines(lines: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"speaker": "other" if x.get("speaker") == "hero" else "self", "text": x.get("text", "")}
        for x in lines
    ]


def _price(s: Storylet) -> str:
    to = "" if s.to == s.npc else f" — {s.to_name}"
    return f"{s.gold} золотых{to}" if s.gold else f"повиниться{to}"


def _how(how: str, lie: Lie) -> str:
    if how == "lied":
        return "его самого обманули" if lie.kind == "found_out" else "он сам уличил героя"
    return f"слух, рассказал {how}" if how else "слух"
