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

The terms are what the code can carry out and check: gold and items, either way. The npc gives only what
it has. What the hero has, the npc does not see (his purse is his): the window marks what he lacks.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from .chronicle import Chronicle
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

PROPOSE_TOOL = Tool(
    PROPOSE_DEAL,
    "Предложить герою сделку: что отдаёшь ты и что отдаёт он. Герой увидит условия и примет их сам, если "
    "согласен, — тогда всё сразу перейдёт из рук в руки. Торгуетесь — вызови снова с новыми условиями, они "
    "заменят прежние. Отдать можешь только то, что есть в твоём инвентаре (он в обстановке); у героя "
    "проси предметы по идентификатору, золото — ITMI_GOLD. Сколько у него с собой, ты не знаешь. Сначала "
    "договоритесь о сути словами, потом предлагай и скажи, что предлагаешь.",
    {
        "type": "object",
        "properties": {
            "npc_gives": {**_GIVES, "description": "что отдаёшь ты; пусто — ничего"},
            "hero_gives": {**_GIVES, "description": "что отдаёт герой; пусто — ничего"},
        },
        "required": ["npc_gives", "hero_gives"],
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

    def term(self) -> DealTerm:
        return DealTerm(item=self.item, count=self.count, text=self.text())

    def effect(self, to: Literal["hero", "self"]) -> dict[str, Any]:
        return {"do": "give", "item": self.item, "count": self.count, "to": to}

    def fact(self) -> dict[str, Any]:
        return {"item": self.item, "count": self.count, "name": self.name}


DealState = Literal["open", "accepting", "done", "withdrawn", "declined", "expired"]


@dataclass
class Deal:
    id: str
    npc: str
    name: str
    version: int
    npc_gives: tuple[Give, ...]
    hero_gives: tuple[Give, ...]
    state: DealState = "open"

    def terms(self) -> str:
        """In the npc's words: what it gives and what the hero gives."""
        return f"ты отдаёшь — {_list(self.npc_gives)}; герой отдаёт — {_list(self.hero_gives)}"

    def show(self) -> DealShowCmd:
        return DealShowCmd(
            deal=self.id,
            version=self.version,
            npc=self.npc,
            npc_gives=[g.term() for g in self.npc_gives],
            hero_gives=[g.term() for g in self.hero_gives],
        )


class Deals:
    """The deals npc's offer the hero in conversation: a TurnExtension, and what the window answers."""

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
        self._ids = itertools.count(1)
        self._background: set[asyncio.Task[Any]] = set()
        link.listeners.append(self._on_message)

    def _on_message(self, msg: GameMessage) -> None:
        match msg:
            case DealAccept():
                self._spawn(self.accept(msg.deal, msg.version))
            case DealDecline():
                self._spawn(self.decline(msg.deal, msg.version))
            case Loading():
                self.open.clear()  # the game drops its window with the world

    # --- the conversation (TurnExtension) ------------------------------------------------------

    async def prepare(self, npc: str) -> None:
        pass

    def situation(self, npc: str) -> str | None:
        deal = self.open.get(npc.upper())
        if deal is None:
            return None
        return f"Твоё предложение герою ждёт его согласия: {deal.terms()}."

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
        hero_gives, why = await self._hero_gives(args.get("hero_gives"))
        if hero_gives is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        if not npc_gives and not hero_gives:
            return ToolOutcome("Отклонено: в сделке ничего нет.", "rejected")
        current = self.open.get(npc)  # he may have accepted the shown terms meanwhile
        if current is not None and current.state == "accepting":
            return ToolOutcome("Отклонено: герой уже согласился на прежние условия.", "rejected")
        if current is not None and (current.npc_gives, current.hero_gives) == (npc_gives, hero_gives):
            return ToolOutcome(f"Эти условия герой уже видит: {current.terms()}.", "accepted")
        if current is None:
            deal = Deal(f"D{next(self._ids)}", npc, name, 1, npc_gives, hero_gives)
        else:
            deal = Deal(current.id, npc, name, current.version + 1, npc_gives, hero_gives)
        try:
            await self.link.send(deal.show())
        except GameError as err:
            return ToolOutcome(f"Отклонено: {err}", "rejected")
        self.open[npc] = deal
        log.info("deal %s v%d with %s: %s", deal.id, deal.version, npc, deal.terms())
        self.journal.add(
            "deal_offer",
            npc=npc,
            name=name,
            deal=deal.id,
            version=deal.version,
            npc_gives=[g.fact() for g in npc_gives],
            hero_gives=[g.fact() for g in hero_gives],
        )
        changed = "Условия изменились" if current is not None else "Герой видит твоё предложение"
        after = "Он примет его сам, если согласен; пока не принял, можно торговаться."
        return ToolOutcome(f"{changed}: {deal.terms()}. {after}", "accepted")

    def end(self, npc: str, why: str = "") -> None:
        """The conversation is over (`why` as TalkDesk tells it: npc, gone, walked_away, switched): an offer
        not taken goes."""
        if (deal := self.open.get(npc.upper())) is not None and deal.state == "open":
            self._spawn(self._close(deal, "expired", ENDED.get(why, ENDED["walked_away"])))

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

    async def _hero_gives(self, raw: Any) -> tuple[tuple[Give, ...] | None, str]:
        wanted, why = _parse(raw)
        if wanted is None:
            return None, why
        if not wanted:
            return (), ""
        try:
            state = await self.link.story_state(symbols=list(wanted), items=list(wanted))
        except (GameError, TimeoutError) as err:
            return None, f"не выходит проверить предметы: {err}"
        kinds, names = state.get("symbols", {}), state.get("items", {})
        unknown = [i for i in wanted if kinds.get(i) != "item"]
        if unknown:
            return None, f"нет таких предметов: {', '.join(unknown)}."
        return tuple(Give(i, c, str(names.get(i) or i)) for i, c in wanted.items()), ""

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
        effects = [g.effect("self") for g in deal.hero_gives] + [g.effect("hero") for g in deal.npc_gives]
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
        self.chronicle.record(
            {
                "fact": "deal",
                "npc": deal.npc,
                "name": deal.name,
                "deal": deal.id,
                "version": deal.version,
                "npc_gives": [g.fact() for g in deal.npc_gives],
                "hero_gives": [g.fact() for g in deal.hero_gives],
                **(await _day(self.link)),
            }
        )
        self.journal.add("deal", npc=deal.npc, name=deal.name, deal=deal.id, version=deal.version)
        log.info("deal %s v%d with %s done: %s", deal.id, deal.version, deal.npc, deal.terms())
        self.dialogue.note(deal.npc, f"Герой принял твою сделку, всё перешло из рук в руки: {deal.terms()}")
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


def _list(gives: tuple[Give, ...]) -> str:
    return ", ".join(g.text() for g in gives) or "ничего"


async def _day(link: GameLink) -> dict[str, int]:
    try:
        return {"day": int((await link.story_state()).get("day", 0))}
    except (GameError, TimeoutError):
        return {}
