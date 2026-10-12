"""Deals with the hero (docs/npc-tools.md, wave 1): the npc offers terms, the player accepts them with a key.

The conversational model proposes (`propose_deal`) what each side gives, once the two have talked it
over. The game shows it in a window that does not take the controls: the hero keeps walking, talking and
fighting. Proposing again in the same conversation changes the terms in that window (a new version): the
two haggle in words. The hero declines with N (the npc hears of it and may offer again); the offer also
goes when the npc withdraws it (`withdraw_deal`) or the conversation is over.

The hero accepts the version he sees (Y). The game sends it only once he has all he gives and the terms
have not changed for a moment; here an older version is not carried out. Then everything changes hands
at once, through the scripts' own giving, all or nothing (`story_effect` with `check`), and it is a fact
of the chronicle.

The terms are what the code can carry out and check (step 2: deals in the story):
- gold and items, either way. The npc gives only what it has. What the hero has, the npc does not see (his
  purse is his): the window marks what he lacks;
- the npc forgives a lie of the hero's it knows of (`forgive`): trust.py no longer counts it against his
  word with it, and a storylet of amends for it at it (amends.py) is settled;
- the npc keeps silent about it (`silence`): it no longer passes the rumor on (social.py); whom it told
  already, knows;
- the hero gives later, by a day (`hero_later`): a debt. Paid back in a later deal (`repay`); a debt not paid
  by its day is a broken word (trust.py) and a rumor (social.py).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from .chronicle import Chronicle, Fact
from .game import GameError, GameLink
from .journal import Journal
from .llm import Tool, ToolCall
from .protocol import (
    ChatStatusCmd,
    DealAccept,
    DealDecline,
    DealHideCmd,
    DealShowCmd,
    DealTerm,
    GameMessage,
    Loading,
    Session,
    Snapshot,
)
from .turn import ToolOutcome

if TYPE_CHECKING:
    from .dialogue import Conversation, DialogueManager

log = logging.getLogger(__name__)

PROPOSE_DEAL = "propose_deal"
WITHDRAW_DEAL = "withdraw_deal"
GOLD = "ITMI_GOLD"
MAX_COUNT = 100_000  # a sanity bound, not a price limit: what is fair is the npc's to judge
MAX_TERMS = 6  # things on one side
MAX_DAYS = 10  # the furthest a debt may be due
DEBTS_EVERY_S = 10.0  # how often the debts' days are checked
LIE_KINDS = ("found_out", "caught")

_GIVES = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "item": {"type": "string", "description": "идентификатор предмета, золото — ITMI_GOLD"},
            "count": {"type": "integer", "description": "сколько, не меньше 1"},
        },
        "required": ["item", "count"],
        "additionalProperties": False,
    },
}
_IDS = {"type": "array", "items": {"type": "string"}}

PROPOSE_TOOL = Tool(
    PROPOSE_DEAL,
    "Предложить герою сделку. Герой увидит условия и примет их сам, если согласен, — тогда всё сразу "
    "перейдёт из рук в руки. Торгуетесь — вызови снова с новыми условиями, они заменят прежние. "
    "Отдать можешь только то, что есть в твоём инвентаре (он в обстановке); у героя проси предметы по "
    "идентификатору, золото — ITMI_GOLD. Сколько у него с собой, ты не знаешь, но сделка сама тебя "
    "защищает: если у него нет того, что он отдаёт, она не состоится — осматривать вещь заранее не нужно. "
    "Кроме вещей: простить ему ложь, о которой ты знаешь (forgive), пообещать о ней молчать (silence), дать "
    "в долг — он отдаст позже, к сроку (hero_later), принять возврат его долга тебе (repay); id лжи и долгов "
    "— в обстановке. Сначала договоритесь о сути словами, потом предлагай и скажи, что предлагаешь. Ненужные "
    "списки — пустые.",
    {
        "type": "object",
        "properties": {
            "npc_gives": {**_GIVES, "description": "что отдаёшь ты"},
            "hero_gives": {**_GIVES, "description": "что отдаёт герой сейчас"},
            "forgive": {**_IDS, "description": "id лжи героя, которую ты прощаешь"},
            "silence": {**_IDS, "description": "id лжи героя, о которой ты обещаешь никому не рассказывать"},
            "hero_later": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {
                            "type": "string",
                            "description": "идентификатор предмета, золото — ITMI_GOLD",
                        },
                        "count": {"type": "integer", "description": "сколько, не меньше 1"},
                        "days": {"type": "integer", "description": f"за сколько дней, от 1 до {MAX_DAYS}"},
                    },
                    "required": ["item", "count", "days"],
                    "additionalProperties": False,
                },
                "description": "что герой отдаст позже, к сроку: его долг тебе",
            },
            "repay": {**_IDS, "description": "id долгов героя тебе, которые он возвращает сейчас"},
        },
        "required": ["npc_gives", "hero_gives", "forgive", "silence", "hero_later", "repay"],
        "additionalProperties": False,
    },
)

WITHDRAW_TOOL = Tool(
    WITHDRAW_DEAL,
    "Отозвать своё предложение сделки: передумал, или разговор ушёл в сторону.",
    {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
)


# how the conversation ended, for the npc's memory of an offer not taken
ENDED = {
    "npc": "Ты закончил разговор, а герой так и не согласился",
    "switched": "Герой отвернулся к другому, не приняв его",
    "gone": "Разговор оборвался, герой не согласился",
    "walked_away": "Герой ушёл, не приняв его",
}


def gold_text(count: int) -> str:
    return f"{count} {'золотой' if count % 10 == 1 and count % 100 != 11 else 'золотых'}"


@dataclass(frozen=True)
class Give:
    item: str  # instance symbol
    count: int
    name: str  # in the game

    def text(self) -> str:
        if self.item == GOLD:
            return gold_text(self.count)
        return f"{self.name} ×{self.count}" if self.count > 1 else self.name

    def term(self, text: str | None = None) -> DealTerm:
        return DealTerm(item=self.item, count=self.count, text=text or self.text())

    def effect(self, to: Literal["hero", "self"]) -> dict[str, Any]:
        return {"do": "give", "item": self.item, "count": self.count, "to": to}

    def fact(self) -> dict[str, Any]:
        return {"item": self.item, "count": self.count, "name": self.name}


@dataclass(frozen=True)
class LieRef:
    """A lie of the hero's an npc knows of: lied to it, or heard of (the key of amends.Lie, social.Rumor)."""

    key: str  # kind:about:quest
    title: str
    about: str  # the one lied to
    about_name: str

    def words(self, npc: str) -> str:
        """What the hero did, to the npc's face."""
        whom = "тебя" if self.about == npc else f"человека по имени {self.about_name}"
        if self.key.startswith("found_out:"):
            return f"обманул {whom}: взял награду за «{self.title}», а дела не сделал"
        return f"обманул {whom}: уверял, что сделал «{self.title}», а это неправда"


@dataclass(frozen=True)
class Later:
    """What the hero gives later: a debt, due by the end of a game day (0-based, as the game counts)."""

    give: Give
    due: int

    def text(self) -> str:
        return f"{self.give.text()} — до конца дня {self.due + 1}"


@dataclass
class Debt:
    id: str
    npc: str
    name: str
    give: Give
    due: int
    paid: bool = False
    broken: bool = False

    def what(self) -> str:
        return f"{self.give.text()} до конца дня {self.due + 1}"


DealState = Literal["open", "accepting", "done", "withdrawn", "declined", "expired"]


@dataclass
class Deal:
    id: str
    npc: str
    name: str
    version: int
    npc_gives: tuple[Give, ...]
    hero_gives: tuple[Give, ...]
    forgive: tuple[LieRef, ...] = ()
    silence: tuple[LieRef, ...] = ()
    later: tuple[Later, ...] = ()
    repay: tuple[Debt, ...] = ()
    state: DealState = "open"

    def same(self, other: Deal) -> bool:
        return self.terms() == other.terms()

    def gold(self) -> int:
        """What the hero pays in gold now."""
        return sum(g.count for g in (*self.hero_gives, *(d.give for d in self.repay)) if g.item == GOLD)

    def terms(self) -> str:
        """In the npc's words: what it gives and what the hero gives."""
        mine = [f"прощаешь ему ложь про «{lie.title}»" for lie in self.forgive]
        mine += [f"обещаешь молчать о лжи про «{lie.title}»" for lie in self.silence]
        his = [f"возвращает долг — {d.give.text()}" for d in self.repay]
        his += [f"отдаст позже {x.give.text()} до конца дня {x.due + 1}" for x in self.later]
        return f"ты {_side('отдаёшь', self.npc_gives, mine)}; герой {_side('отдаёт', self.hero_gives, his)}"

    def show(self) -> DealShowCmd:
        mine = [g.term() for g in self.npc_gives]
        mine += [DealTerm(item="", count=0, text=f"Прощает ложь про «{lie.title}»") for lie in self.forgive]
        mine += [DealTerm(item="", count=0, text=f"Молчит о лжи про «{lie.title}»") for lie in self.silence]
        his = [g.term() for g in self.hero_gives]
        his += [d.give.term(f"Долг: {d.give.text()}") for d in self.repay]
        his += [DealTerm(item="", count=0, text=f"Позже: {x.text()}") for x in self.later]
        return DealShowCmd(deal=self.id, version=self.version, npc=self.npc, npc_gives=mine, hero_gives=his)

    def effects(self) -> list[dict[str, Any]]:
        hero = [g.effect("self") for g in self.hero_gives] + [d.give.effect("self") for d in self.repay]
        return hero + [g.effect("hero") for g in self.npc_gives]


class Deals:
    """The deals npc's offer the hero in conversation: a TurnExtension, what the window answers, and the
    hero's debts."""

    def __init__(
        self,
        link: GameLink,
        dialogue: DialogueManager,
        chronicle: Chronicle,
        *,
        journal: Journal | None = None,
    ) -> None:
        self.link = link
        self.dialogue = dialogue
        self.chronicle = chronicle
        self.journal = journal or dialogue.journal
        self.open: dict[str, Deal] = {}  # npc -> its offer, while the conversation lasts
        self.debts: dict[str, Debt] = {}  # by id, of the save branch
        self._ids = itertools.count(1)
        self._background: set[asyncio.Task[Any]] = set()
        link.listeners.append(self._on_message)
        self._load()

    def _on_message(self, msg: GameMessage) -> None:
        match msg:
            case DealAccept():
                self._spawn(self.accept(msg.deal, msg.version))
            case DealDecline():
                self._spawn(self.decline(msg.deal, msg.version))
            case Loading():
                self.open.clear()  # the game drops its window with the world
            case Session():
                self._load()

    # --- the conversation (TurnExtension) ------------------------------------------------------

    async def prepare(self, npc: str) -> None:
        pass

    def situation(self, npc: str) -> str | None:
        npc = npc.upper()
        lines = []
        if (deal := self.open.get(npc)) is not None:
            lines.append(f"Твоё предложение герою ждёт его согласия: {deal.terms()}.")
        known, forgiven, silent = self.lies_of(npc)
        open_lies = {k: lie for k, lie in known.items() if k not in forgiven}
        if open_lies:
            lines.append(
                "Ложь героя, о которой ты знаешь (в сделке её можно простить — forgive, "
                "или пообещать молчать — silence, по id):"
            )
            for k, lie in open_lies.items():
                hushed = "; ты уже обещал о ней молчать" if k in silent else ""
                lines.append(f"- {k}: он {lie.words(npc)}{hushed}")
        debts = [d for d in self.debts.values() if d.npc == npc and not d.paid]
        if debts:
            lines.append("Долги героя тебе (вернуть — repay в сделке, по id):")
            for d in debts:
                state = " — срок прошёл, он не сдержал слово" if d.broken else ""
                lines.append(f"- {d.id}: {d.what()}{state}")
        return "\n".join(lines) or None

    def tools(self, npc: str) -> list[Tool]:
        return [PROPOSE_TOOL, WITHDRAW_TOOL] if npc.upper() in self.open else [PROPOSE_TOOL]

    async def handle(
        self, dialogue: DialogueManager, conv: Conversation, call: ToolCall, snap: Snapshot | None
    ) -> ToolOutcome | None:
        if call.name == PROPOSE_DEAL:
            return await self.propose(conv.npc, conv.persona.name, call.arguments, snap)
        if call.name == WITHDRAW_DEAL:
            deal = self.open.get(conv.npc)
            if deal is None:
                return ToolOutcome("Отклонено: ты ничего не предлагал.", "rejected")
            if deal.state == "accepting":
                return ToolOutcome(
                    "Отклонено: герой уже согласился, вещи переходят из рук в руки.", "rejected"
                )
            await self._close(deal, "withdrawn")
            return ToolOutcome("Предложение снято.", "accepted")
        return None

    async def propose(self, npc: str, name: str, args: dict[str, Any], snap: Snapshot | None) -> ToolOutcome:
        npc = npc.upper()
        current = self.open.get(npc)
        if current is not None and current.state == "accepting":
            return ToolOutcome("Отклонено: герой уже согласился на прежние условия.", "rejected")
        npc_gives, why = self._npc_gives(args.get("npc_gives"), snap)
        if npc_gives is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        forgive, silence, why = self._lies(npc, args.get("forgive"), args.get("silence"))
        if forgive is None or silence is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        repay, why = self._repay(npc, args.get("repay"))
        if repay is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        later_raw, why = _parse_later(args.get("hero_later"))
        if later_raw is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        wanted, why = _parse(args.get("hero_gives"))
        if wanted is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        things, day, why = await self._items({*wanted, *(i for i, _, _ in later_raw)})
        if things is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        hero_gives = tuple(Give(i, c, things[i]) for i, c in wanted.items())
        later = tuple(Later(Give(i, c, things[i]), day + d) for i, c, d in later_raw)
        if not (npc_gives or hero_gives or forgive or silence or later or repay):
            return ToolOutcome("Отклонено: в сделке ничего нет.", "rejected")
        current = self.open.get(npc)  # he may have accepted the shown terms meanwhile
        if current is not None and current.state == "accepting":
            return ToolOutcome("Отклонено: герой уже согласился на прежние условия.", "rejected")
        deal_id, version = (current.id, current.version + 1) if current else (f"D{next(self._ids)}", 1)
        deal = Deal(deal_id, npc, name, version, npc_gives, hero_gives, forgive, silence, later, repay)
        if current is not None and current.same(deal):
            return ToolOutcome(f"Эти условия герой уже видит: {current.terms()}.", "accepted")
        try:
            await self.link.send(deal.show())
        except GameError as err:
            return ToolOutcome(f"Отклонено: {err}", "rejected")
        self.open[npc] = deal
        log.info("deal %s v%d with %s: %s", deal.id, deal.version, npc, deal.terms())
        terms = deal.terms()
        self.journal.add("deal_offer", npc=npc, name=name, deal=deal.id, version=deal.version, terms=terms)
        changed = "Условия изменились" if current is not None else "Герой видит твоё предложение"
        after = "Он примет его сам, если согласен; пока не принял, можно торговаться."
        return ToolOutcome(f"{changed}: {deal.terms()}. {after}", "accepted")

    def end(self, npc: str, why: str = "") -> None:
        """The conversation is over (`why` as TalkDesk tells it: npc, gone, walked_away, switched): an offer
        not taken goes."""
        if (deal := self.open.get(npc.upper())) is not None and deal.state == "open":
            self._spawn(self._close(deal, "expired", ENDED.get(why, ENDED["walked_away"])))

    # --- what the npc knows ----------------------------------------------------------------------

    def lies_of(self, npc: str) -> tuple[dict[str, LieRef], set[str], set[str]]:
        """The hero's lies the npc knows of (lied to it, or heard of), those it forgave, those it keeps
        silent about."""
        npc = npc.upper()
        known: dict[str, LieRef] = {}
        forgiven: set[str] = set()
        silent: set[str] = set()
        for f in self.chronicle.facts():
            match f.get("fact"):
                case "found_out" | "caught" if f.get("npc") == npc:
                    key = f"{f['fact']}:{npc}:{f.get('quest', '')}"
                    known.setdefault(key, LieRef(key, _title(f), npc, f.get("name") or npc))
                case "rumor" if f.get("to") == npc and f.get("kind", "found_out") in LIE_KINDS:
                    about = f.get("about") or f.get("from", "")
                    key = f"{f.get('kind', 'found_out')}:{about}:{f.get('quest', '')}"
                    known.setdefault(key, LieRef(key, _title(f), about, f.get("about_name") or about))
                case "deal" if f.get("npc") == npc:
                    forgiven.update(f.get("forgive", []))
                    silent.update(f.get("silence", []))
                case "amends" if npc in (f.get("npc"), f.get("to")) and f.get("lie"):
                    forgiven.add(f["lie"])
        return known, forgiven, silent

    # --- the terms ------------------------------------------------------------------------------

    def _npc_gives(self, raw: Any, snap: Snapshot | None) -> tuple[tuple[Give, ...] | None, str]:
        wanted, why = _parse(raw)
        if wanted is None:
            return None, why
        if not wanted:
            return (), ""
        if snap is None:
            return None, "не видно, что у тебя есть."
        have = {i.item: i for i in snap.npc.inventory if not i.equipped and i.count > 0}
        out = []
        for item, count in wanted.items():
            got = have.get(item)
            if got is None:
                listed = "; ".join(f"{i.name} ({i.item}) ×{i.count}" for i in have.values()) or "пусто"
                return None, f"у тебя нет {item}. Твой инвентарь: {listed}."
            if got.count < count:
                return None, f"у тебя только {got.count} — {got.name} ({item})."
            out.append(Give(item, count, got.name))
        return tuple(out), ""

    def _lies(
        self, npc: str, forgive: Any, silence: Any
    ) -> tuple[tuple[LieRef, ...] | None, tuple[LieRef, ...] | None, str]:
        known, forgiven, silent = self.lies_of(npc)
        out: list[tuple[LieRef, ...]] = []
        for raw, done, what in ((forgive, forgiven, "простил"), (silence, silent, "обещал молчать о")):
            keys = [str(k).strip() for k in (raw or [])]
            unknown = [k for k in keys if k not in known]
            if unknown:
                listed = ", ".join(known) or "никакой"
                return None, None, f"ты не знаешь такой лжи героя: {', '.join(unknown)}. Ты знаешь: {listed}."
            again = [k for k in keys if k in done]
            if again:
                return None, None, f"ты уже {what} этой лжи: {', '.join(again)}."
            out.append(tuple(known[k] for k in dict.fromkeys(keys)))
        return out[0], out[1], ""

    def _repay(self, npc: str, raw: Any) -> tuple[tuple[Debt, ...] | None, str]:
        ids = [str(k).strip() for k in (raw or [])]
        mine = {d.id: d for d in self.debts.values() if d.npc == npc and not d.paid}
        unknown = [i for i in ids if i not in mine]
        if unknown:
            listed = ", ".join(f"{d.id} ({d.what()})" for d in mine.values()) or "никаких"
            return None, f"у героя нет такого долга тебе: {', '.join(unknown)}. Его долги тебе: {listed}."
        return tuple(mine[i] for i in dict.fromkeys(ids)), ""

    async def _items(self, items: set[str]) -> tuple[dict[str, str] | None, int, str]:
        """The names of the items in the game, and today's game day; or why not."""
        try:
            state = await self.link.story_state(symbols=list(items), items=list(items))
        except (GameError, TimeoutError) as err:
            return None, 0, f"не выходит проверить предметы: {err}"
        kinds, names = state.get("symbols", {}), state.get("items", {})
        unknown = [i for i in items if kinds.get(i) != "item"]
        if unknown:
            return None, 0, f"нет таких предметов: {', '.join(sorted(unknown))}."
        return {i: str(names.get(i) or i) for i in items}, int(state.get("day", 0)), ""

    # --- the hero accepts ------------------------------------------------------------------------

    async def accept(self, deal_id: str, version: int) -> None:
        deal = next((d for d in self.open.values() if d.id == deal_id), None)
        if deal is None or deal.state != "open":
            log.info("deal %s v%d accepted, but it is not open", deal_id, version)
            return
        if version != deal.version:
            # the terms changed as he pressed: he takes only what he saw
            log.info("deal %s: v%d accepted, v%d is shown", deal_id, version, deal.version)
            await self._notice("Условия изменились — посмотри ещё раз")
            await self._send(deal.show())
            return
        deal.state = "accepting"
        if effects := deal.effects():
            try:
                await self.link.story_effect(deal.npc, effects, check=True)
            except (GameError, TimeoutError) as err:
                await self._failed(deal, str(err))
                return
        deal.state = "done"
        if self.open.get(deal.npc) is deal:
            del self.open[deal.npc]
        await self._send(DealHideCmd(deal=deal.id))
        await self._notice("Сделка заключена")
        day = await _day(self.link)
        debts = [Debt(uuid.uuid4().hex[:6], deal.npc, deal.name, x.give, x.due) for x in deal.later]
        self.chronicle.record(
            {
                "fact": "deal",
                "npc": deal.npc,
                "name": deal.name,
                "deal": deal.id,
                "version": deal.version,
                "npc_gives": [g.fact() for g in deal.npc_gives],
                "hero_gives": [g.fact() for g in deal.hero_gives],
                "gold": deal.gold(),
                "forgive": [lie.key for lie in deal.forgive],
                "silence": [lie.key for lie in deal.silence],
                "later": [{**d.give.fact(), "due": d.due, "debt": d.id} for d in debts],
                "repay": [d.id for d in deal.repay],
                **day,
            }
        )
        for d in debts:
            self.debts[d.id] = d
            self.chronicle.record(
                {
                    "fact": "debt",
                    "id": d.id,
                    "npc": d.npc,
                    "npc_name": d.name,
                    **d.give.fact(),
                    "due": d.due,
                    **day,
                }
            )
        for d in deal.repay:
            d.paid = True
            self.chronicle.record({"fact": "debt_paid", "id": d.id, "npc": d.npc, **day})
        self.journal.add("deal", npc=deal.npc, name=deal.name, deal=deal.id, version=deal.version)
        log.info("deal %s v%d with %s done: %s", deal.id, deal.version, deal.npc, deal.terms())
        self.dialogue.note(deal.npc, f"Герой принял твою сделку, она состоялась: {deal.terms()}")
        self.dialogue.event_turn(
            deal.npc, "[Герой согласился, сделка состоялась. Отзовись коротко.]", tools=()
        )

    async def decline(self, deal_id: str, version: int) -> None:
        """The hero declined (N): the offer goes, the npc hears of it and may offer otherwise."""
        deal = next((d for d in self.open.values() if d.id == deal_id), None)
        if deal is None or deal.state != "open":
            log.info("deal %s v%d declined, but it is not open", deal_id, version)
            return
        await self._close(deal, "declined")
        self.dialogue.note(deal.npc, f"Герой отказался от твоего предложения: {deal.terms()}")
        self.dialogue.event_turn(
            deal.npc,
            "[Герой отказался от сделки. Отзовись коротко; захочешь — предложи иначе.]",
            tools=(PROPOSE_DEAL,),
        )

    async def _failed(self, deal: Deal, error: str) -> None:
        """Something is missing after all: the hero's things (his window marks them), or the npc's (it no
        longer has what it offered: the offer goes)."""
        log.info("deal %s v%d with %s failed: %s", deal.id, deal.version, deal.npc, error)
        if "missing: npc" in error:
            await self._close(deal, "withdrawn")
            await self._notice("Сделка не состоялась")
            self.dialogue.note(deal.npc, "Сделка сорвалась: у тебя уже нет того, что ты обещал отдать")
            self.dialogue.event_turn(
                deal.npc, "[Сделка сорвалась: у тебя нет того, что ты предлагал. Объясни герою.]", tools=()
            )
            return
        deal.state = "open"
        await self._notice("Не хватает того, что ты отдаёшь")
        await self._send(deal.show())  # the same version: the window lets Y be pressed again

    async def _close(self, deal: Deal, state: DealState, how: str = "") -> None:
        deal.state = state
        if self.open.get(deal.npc) is deal:
            del self.open[deal.npc]
        await self._send(DealHideCmd(deal=deal.id))
        log.info("deal %s with %s: %s", deal.id, deal.npc, state)
        self.journal.add(f"deal_{state}", npc=deal.npc, name=deal.name, deal=deal.id, version=deal.version)
        if state == "expired":
            self.dialogue.note(deal.npc, f"{how}, твоё предложение снято: {deal.terms()}")

    # --- the debts --------------------------------------------------------------------------------

    async def run(self) -> None:
        """A debt not paid by the end of its day is a broken word."""
        while True:
            await asyncio.sleep(DEBTS_EVERY_S)
            if not any(not d.paid and not d.broken for d in self.debts.values()):
                continue
            await self.link.wait_world()
            try:
                day = int((await self.link.story_state()).get("day", 0))
            except (GameError, TimeoutError) as err:
                log.info("debts: %s", err or type(err).__name__)
                continue
            self.overdue(day)

    def overdue(self, day: int) -> list[Debt]:
        """The debts past their day as of `day`, now broken words."""
        out = []
        for d in self.debts.values():
            if d.paid or d.broken or day <= d.due:
                continue
            d.broken = True
            out.append(d)
            self.chronicle.record(
                {
                    "fact": "broken",
                    "debt": d.id,
                    "npc": d.npc,
                    "name": d.name,
                    "what": d.what(),
                    "due": d.due,
                    "day": day,
                }
            )
            self.journal.add("debt_broken", npc=d.npc, name=d.name, what=d.what())
            log.info("debt %s to %s broken: %s", d.id, d.npc, d.what())
            broke = f"Герой не вернул тебе долг в срок: {d.what()}. Он не сдержал слово"
            self.dialogue.remember(d.npc, d.name, broke)
        return out

    def _load(self) -> None:
        self.debts.clear()
        for f in self.chronicle.facts():
            self._on_fact(f)

    def _on_fact(self, f: Fact) -> None:
        match f.get("fact"):
            case "debt":
                give = Give(f.get("item", GOLD), int(f.get("count", 1)), f.get("name") or f.get("item", ""))
                npc = f.get("npc", "")
                self.debts[f["id"]] = Debt(f["id"], npc, f.get("npc_name") or npc, give, int(f["due"]))
            case "debt_paid" if f.get("id") in self.debts:
                self.debts[f["id"]].paid = True
            case "broken" if f.get("debt") in self.debts:
                self.debts[f["debt"]].broken = True

    async def _notice(self, text: str) -> None:
        await self._send(ChatStatusCmd(status="notice", detail=text))

    async def _send(self, cmd: Any) -> None:
        try:
            await self.link.send(cmd)
        except GameError as err:
            log.info("deals: %s", err)

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)


def _parse(raw: Any) -> tuple[dict[str, int] | None, str]:
    """[{item, count}] -> {ITEM: count}, the same item added up; or why not."""
    if raw is None:
        return {}, ""
    if not isinstance(raw, list):
        return None, "нужен список предметов."
    out: dict[str, int] = {}
    for x in raw:
        if not isinstance(x, dict):
            return None, "каждый предмет — {item, count}."
        item = str(x.get("item") or "").strip().upper()
        try:
            count = int(x.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if not item or count < 1:
            return None, f"неверный предмет: {x}."
        out[item] = out.get(item, 0) + count
    if len(out) > MAX_TERMS:
        return None, f"не больше {MAX_TERMS} разных предметов с одной стороны."
    if any(c > MAX_COUNT for c in out.values()):
        return None, f"не больше {MAX_COUNT} штук."
    return out, ""


def _parse_later(raw: Any) -> tuple[list[tuple[str, int, int]] | None, str]:
    """[{item, count, days}] -> [(ITEM, count, days)]; or why not."""
    out = []
    for x in raw or []:
        if not isinstance(x, dict):
            return None, "каждый долг — {item, count, days}."
        got, why = _parse([{"item": x.get("item"), "count": x.get("count")}])
        if got is None:
            return None, why
        try:
            days = int(x.get("days") or 0)
        except (TypeError, ValueError):
            days = 0
        if not 1 <= days <= MAX_DAYS:
            return None, f"срок долга — от 1 до {MAX_DAYS} дней."
        ((item, count),) = got.items()
        out.append((item, count, days))
    if len(out) > MAX_TERMS:
        return None, f"не больше {MAX_TERMS} долгов."
    return out, ""


def _side(verb: str, gives: tuple[Give, ...], more: list[str]) -> str:
    """One side of the terms: the things it gives, then the rest ("отдаёшь — ничего" if nothing at all)."""
    parts = [f"{verb} — {_list(gives)}"] if gives else []
    return ", ".join(parts + more) or f"{verb} — ничего"


def _list(gives: tuple[Give, ...]) -> str:
    return ", ".join(g.text() for g in gives) or "ничего"


def _title(f: Fact) -> str:
    return str(f.get("title") or f.get("quest") or "")


async def _day(link: GameLink) -> dict[str, int]:
    try:
        return {"day": int((await link.story_state()).get("day", 0))}
    except (GameError, TimeoutError):
        return {}
