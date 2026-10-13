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
  purse is his): the window marks what he lacks. It asks for his things by the scripts' ids, and it can
  only guess them: a guessed id that is no item («ITWR_CAVALORNLETTER») is the thing of its own story
  (the story graph) of that kind the hero carries, when he carries just one: Cavalorn's letter is
  ITWR_SATURASFIRSTMESSAGE_ADDON_SEALED. Else it is told the things of its story;
- the npc forgives a lie of the hero's it knows of (`forgive`): trust.py no longer counts it against his
  word with it, and a storylet of amends for it at it (amends.py) is settled;
- the npc keeps silent about it (`silence`): it no longer passes the rumor on (social.py); whom it told
  already, knows;
- the hero gives later, by a day (`hero_later`): a debt. Paid back in a later deal (`repay`); a debt not paid
  by its day is a broken word (trust.py) and a rumor (social.py);
- past the inventory (contracts.py, docs/contracts.md): experience (`npc_xp`), teaching for the hero's
  learning points (`npc_teach`), the things the npc keeps elsewhere (its stash, in `npc_gives` like the rest);
- a task (`task`: whom to kill, what to bring, by a day or with none) and its reward (`reward`): the code
  pays it when the task is done, the next time the hero talks to the npc.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import uuid
from collections.abc import Callable, Collection, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .chronicle import Chronicle, Fact
from .contracts import (
    GOLD,
    MAX_TASK_DAYS,
    TEACH_KINDS,
    Contracts,
    Give,
    Reward,
    Stash,
    Task,
    Teach,
    carried,
    parse_kills,
    parse_teach,
    split,
)
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
MAX_COUNT = 100_000  # a sanity bound, not a price limit: what is fair is the npc's to judge
MAX_TERMS = 6  # things on one side
MAX_DAYS = 10  # the furthest a debt may be due
STORY_ITEMS = 8  # the things of its story an npc is told of, when it asked for one that is not
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
_TEACH = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "what": {"type": "string", "enum": list(TEACH_KINDS)},
            "arg": {"type": "string", "description": "константа скриптов; для circle — пусто"},
            "count": {"type": "integer", "description": "очки, проценты или номер круга; иначе 1"},
        },
        "required": ["what", "arg", "count"],
        "additionalProperties": False,
    },
}
_KILLS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "кого: человек или существо — символ скриптов или имя; вид — гильдия GIL_…",
            },
            "count": {"type": "integer", "description": "сколько; для одного человека — 1"},
        },
        "required": ["target", "count"],
        "additionalProperties": False,
    },
}

PROPOSE_TOOL = Tool(
    PROPOSE_DEAL,
    "Предложить герою сделку. Герой увидит условия и примет их сам, если согласен, — тогда всё сразу "
    "перейдёт из рук в руки. Торгуетесь — вызови снова с новыми условиями, они заменят прежние. "
    "Отдать можешь только то, что есть в твоём инвентаре (он в обстановке); у героя проси предметы по "
    "идентификатору, золото — ITMI_GOLD. Не знаешь точного — пиши догадку того же вида (ITWR_… письма и "
    "книги, ITMI_… прочие вещи): вещь из твоих дел, которая у него с собой, код подставит сам. Сколько у "
    "него с собой, ты не знаешь, но сделка сама тебя защищает: если у него нет того, что он отдаёт, она "
    "не состоится — осматривать вещь заранее не нужно. "
    "Кроме вещей: простить ему ложь, о которой ты знаешь (forgive), пообещать о ней молчать (silence), дать "
    "в долг — он отдаст позже, к сроку (hero_later), принять возврат его долга тебе (repay); id лжи и долгов "
    "— в обстановке. Вещи из твоего тайника (если он есть, он в обстановке) отдаёшь так же, через npc_gives. "
    "Ещё можешь дать опыт (npc_xp) и научить (npc_teach) за его очки обучения, как учитель в игре: "
    "attribute — ATR_STRENGTH сила, ATR_DEXTERITY ловкость, ATR_MANA_MAX мана, count — очки; fight — "
    "NPC_TALENT_1H, NPC_TALENT_2H, NPC_TALENT_BOW, NPC_TALENT_CROSSBOW, count — проценты; thief — "
    "NPC_TALENT_PICKLOCK, NPC_TALENT_SNEAK, NPC_TALENT_PICKPOCKET, NPC_TALENT_ACROBAT; circle — круг магии, "
    "count 1–6; rune — SPL_…; alchemy — POTION_…; smith — WEAPON_…; trophy — TROPHY_…. Не хватит у него "
    "очков — сделка не состоится. Поручение (task): кого убить — человека или существо по символу или "
    "имени, или вид по гильдии (GIL_WOLF волки, GIL_ORC орки, GIL_DRAGON драконы, GIL_SKELETON скелеты, "
    "GIL_ZOMBIE зомби, GIL_BDT бандиты, GIL_GOBBO гоблины, GIL_TROLL тролли, GIL_DEMON демоны и другие "
    "GIL_…), "
    "и что принести; days — срок в днях, 0 — без срока. reward — что ты отдашь, когда он исполнит: код "
    "сам выдаст награду, как только он с тобой заговорит, — так что обещай только то, что готов отдать. "
    "Сначала договоритесь о сути словами, потом предлагай и скажи, что предлагаешь. Ненужные списки — "
    "пустые, ненужные числа — 0.",
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
            "repay": {
                **_IDS,
                "description": "id долгов героя тебе, которые он возвращает сейчас; что он должен, войдёт в "
                "сделку само — не добавляй это в hero_gives ещё раз",
            },
            "npc_xp": {"type": "integer", "description": "опыт, который ты даёшь герою сейчас; 0 — нет"},
            "npc_teach": {**_TEACH, "description": "чему ты учишь его сейчас, за его очки обучения"},
            "task": {
                "type": "object",
                "properties": {
                    "kill": {**_KILLS, "description": "кого ему убить"},
                    "bring": {**_GIVES, "description": "что ему принести тебе"},
                    "days": {"type": "integer", "description": "срок в днях; 0 — без срока"},
                },
                "required": ["kill", "bring", "days"],
                "additionalProperties": False,
                "description": "поручение герою; пустые списки — без поручения",
            },
            "reward": {
                "type": "object",
                "properties": {
                    "npc_gives": {**_GIVES, "description": "что ты отдашь"},
                    "xp": {"type": "integer", "description": "опыт; 0 — нет"},
                    "teach": {**_TEACH, "description": "чему научишь"},
                },
                "required": ["npc_gives", "xp", "teach"],
                "additionalProperties": False,
                "description": "награда, когда он исполнит поручение; без поручения — пустая",
            },
        },
        "required": [
            "npc_gives",
            "hero_gives",
            "forgive",
            "silence",
            "hero_later",
            "repay",
            "npc_xp",
            "npc_teach",
            "task",
            "reward",
        ],
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


@dataclass(frozen=True)
class LieRef:
    """A lie of the hero's an npc knows of: lied to it, or heard of (the key of amends.Lie, social.Rumor)."""

    key: str  # kind:about:quest
    title: str
    about: str  # the one lied to
    about_name: str
    said: bool = False  # caught: the title is his own words, seen to be false (sightings.py), not a quest

    def words(self, npc: str) -> str:
        """What the hero did, to the npc's face."""
        whom = "тебя" if self.about == npc else f"человека по имени {self.about_name}"
        if self.key.startswith("found_out:"):
            return f"обманул {whom}: взял награду за «{self.title}», а дела не сделал"
        if self.said:
            return f"обманул {whom}: сказал «{self.title}», а это неправда"
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
    xp: int = 0  # the npc gives experience now
    teach: tuple[Teach, ...] = ()  # the npc teaches now, for the hero's learning points
    task: Task | None = None  # the hero takes on a task...
    reward: Reward = field(default_factory=Reward)  # ...the npc pays this when it is done (contracts.py)
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
        if self.xp:
            mine.append(f"даёшь ему опыт +{self.xp}")
        mine += [f"учишь его: {t.text()}" for t in self.teach]
        if self.task:
            mine.append(f"когда он исполнит поручение, отдашь: {self.reward.text()}")
        his = [f"возвращает долг — {d.give.text()}" for d in self.repay]
        his += [f"отдаст позже {x.give.text()} до конца дня {x.due + 1}" for x in self.later]
        if self.task:
            his.append(f"берётся за поручение — {self.task.text()}")
        return f"ты {_side('отдаёшь', self.npc_gives, mine)}; герой {_side('отдаёт', self.hero_gives, his)}"

    def show(self) -> DealShowCmd:
        mine = [g.term() for g in self.npc_gives]
        mine += [DealTerm(item="", count=0, text=f"Прощает ложь про «{lie.title}»") for lie in self.forgive]
        mine += [DealTerm(item="", count=0, text=f"Молчит о лжи про «{lie.title}»") for lie in self.silence]
        if self.xp:
            mine.append(DealTerm(item="", count=0, text=f"Опыт +{self.xp}"))
        mine += [DealTerm(item="", count=0, text=f"Учит: {t.text()}") for t in self.teach]
        if self.task:
            mine += [DealTerm(item="", count=0, text=f"За поручение: {x}") for x in self.reward.parts()]
        his = [g.term() for g in self.hero_gives]
        his += [d.give.term(f"Долг: {d.give.text()}") for d in self.repay]
        his += [DealTerm(item="", count=0, text=f"Позже: {x.text()}") for x in self.later]
        if self.task:
            his += [DealTerm(item="", count=0, text=f"Поручение: {x}") for x in self.task.parts()]
            his.append(DealTerm(item="", count=0, text=f"Срок: {self.task.when()}"))
        # the game's codepage (cp1251) has no "×": the window would show "?"
        mine = [t.model_copy(update={"text": t.text.replace("×", "x")}) for t in mine]
        his = [t.model_copy(update={"text": t.text.replace("×", "x")}) for t in his]
        return DealShowCmd(deal=self.id, version=self.version, npc=self.npc, npc_gives=mine, hero_gives=his)

    def effects(self) -> list[dict[str, Any]]:
        """Teaching first (the check has counted his learning points), the hero's things, the npc's (out of
        its stash first), experience last."""
        out = [t.effect() for t in self.teach]
        out += [g.effect("self") for g in self.hero_gives] + [d.give.effect("self") for d in self.repay]
        for g in self.npc_gives:
            out += g.effects("hero")
        if self.xp:
            out.append({"do": "xp", "amount": self.xp})
        return out


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
        names: Callable[[str], str | None] | None = None,
        things: Callable[[str], Collection[str]] | None = None,
    ) -> None:
        self.link = link
        self.dialogue = dialogue
        self.chronicle = chronicle
        self.journal = journal or dialogue.journal
        self.names = names  # an npc's name -> its symbol, for whom a task is to kill (the character cards)
        self.things = things  # an npc -> the item ids of its story (StoryGraph.things_of), for a guessed id
        self.stash = Stash(chronicle)
        # the tasks of the deals and their rewards: a TurnExtension of its own (cli.py)
        self.contracts = Contracts(link, dialogue, chronicle, self.stash, journal=self.journal)
        self.open: dict[str, Deal] = {}  # npc -> its offer, while the conversation lasts
        self.debts: dict[str, Debt] = {}  # by id, of the save branch
        self.day: int | None = None  # the game day, as last read
        self.on_day: list[Callable[[int], None]] = []  # a new game day (concerns.py: a debt due today)
        self.on_reload: list[Callable[[], None]] = []  # the debts were read again (concerns.py)
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
                self.day = None  # the world loaded may be of another day
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
        npc_gives, why = self._npc_gives(npc, args.get("npc_gives"), snap)
        if npc_gives is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        xp, why = _parse_xp(args.get("npc_xp"))
        if xp is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        teach, why = await parse_teach(self.link, args.get("npc_teach"))
        if teach is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        task_raw = args.get("task") if isinstance(args.get("task"), dict) else {}
        reward_raw = args.get("reward") if isinstance(args.get("reward"), dict) else {}
        kills, why = await parse_kills(self.link, task_raw.get("kill"), self.names)
        if kills is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        bring, why = _parse(task_raw.get("bring"))
        if bring is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        days, why = _parse_days(task_raw.get("days"))
        if days is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        paid, why = self._npc_gives(npc, reward_raw.get("npc_gives"), snap)
        if paid is None:
            return ToolOutcome(f"Отклонено: награда — {why}", "rejected")
        paid_xp, why = _parse_xp(reward_raw.get("xp"))
        if paid_xp is None:
            return ToolOutcome(f"Отклонено: награда — {why}", "rejected")
        paid_teach, why = await parse_teach(self.link, reward_raw.get("teach"))
        if paid_teach is None:
            return ToolOutcome(f"Отклонено: награда — {why}", "rejected")
        # what it pays with is decided when it pays: what it carries then, the rest from its stash
        reward = Reward(tuple(Give(g.item, g.count, g.name) for g in paid), paid_xp, paid_teach)
        if reward and not (kills or bring):
            return ToolOutcome(
                "Отклонено: награда бывает только за поручение (task). Без поручения отдавай сразу — "
                "npc_gives, npc_xp, npc_teach.",
                "rejected",
            )
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
        later_ids = {i for i, _, _ in later_raw}
        asked = {*wanted, *bring, *later_ids}
        things, swaps, day, why = await self._items(npc, asked, set(wanted) - set(bring) - later_ids)
        if things is None:
            return ToolOutcome(f"Отклонено: {why}", "rejected")
        wanted = _swapped(wanted, swaps)
        hero_gives = tuple(Give(i, c, things[i]) for i, c in wanted.items())
        later = tuple(Later(Give(i, c, things[i]), day + d) for i, c, d in later_raw)
        task = None
        if kills or bring:
            brought = tuple(Give(i, c, things[i]) for i, c in bring.items())
            task = Task(kills, brought, day + days if days else None)
        if not (npc_gives or hero_gives or forgive or silence or later or repay or xp or teach or task):
            return ToolOutcome("Отклонено: в сделке ничего нет.", "rejected")
        current = self.open.get(npc)  # he may have accepted the shown terms meanwhile
        if current is not None and current.state == "accepting":
            return ToolOutcome("Отклонено: герой уже согласился на прежние условия.", "rejected")
        deal_id, version = (current.id, current.version + 1) if current else (f"D{next(self._ids)}", 1)
        deal = Deal(
            deal_id,
            npc,
            name,
            version,
            npc_gives,
            hero_gives,
            forgive,
            silence,
            later,
            repay,
            xp,
            teach,
            task,
            reward,
        )
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
        if swaps:
            after += " " + " ".join(
                f"Предмета {guess} нет: вписано то, что герой держит из твоих дел, — {things[i]} ({i})."
                for guess, i in swaps.items()
            )
        twice = [g for g in hero_gives if any(d.give.item == g.item for d in repay)]
        if twice:  # the debt brings its own: what was added on top is more, not the debt again
            total = ", ".join(
                Give(
                    g.item, g.count + sum(d.give.count for d in repay if d.give.item == g.item), g.name
                ).text()
                for g in twice
            )
            after += f" Учти: долг уже включает своё, с hero_gives герой отдаёт всего {total}."
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
                    ref = LieRef(key, _title(f), npc, f.get("name") or npc, said=bool(f.get("said")))
                    known.setdefault(key, ref)
                case "rumor" if f.get("to") == npc and f.get("kind", "found_out") in LIE_KINDS:
                    about = f.get("about") or f.get("from", "")
                    key = f"{f.get('kind', 'found_out')}:{about}:{f.get('quest', '')}"
                    ref = LieRef(
                        key, _title(f), about, f.get("about_name") or about, said=bool(f.get("said"))
                    )
                    known.setdefault(key, ref)
                case "deal" if f.get("npc") == npc:
                    forgiven.update(f.get("forgive", []))
                    silent.update(f.get("silence", []))
                case "amends" if npc in (f.get("npc"), f.get("to")) and f.get("lie"):
                    forgiven.add(f["lie"])
        return known, forgiven, silent

    # --- the terms ------------------------------------------------------------------------------

    def _npc_gives(self, npc: str, raw: Any, snap: Snapshot | None) -> tuple[tuple[Give, ...] | None, str]:
        """What the npc gives: what it carries first, the rest from its stash (contracts.py)."""
        wanted, why = _parse(raw)
        if wanted is None:
            return None, why
        if not wanted:
            return (), ""
        kept = self.stash.left(npc)
        if snap is None and not kept:
            return None, "не видно, что у тебя есть."
        have = carried(snap)
        out = []
        for item, count in wanted.items():
            n, name = have.get(item, (0, ""))
            got = split(item, count, name or self.stash.names.get(item, item), n, kept.get(item, 0))
            if got is None:
                listed = "; ".join(f"{nm} ({i}) ×{c}" for i, (c, nm) in have.items()) or "пусто"
                hidden = f" Твой тайник: {self.stash.text(npc)}." if kept else ""
                if n or kept.get(item):
                    return None, f"у тебя только {n + kept.get(item, 0)} — {name or item} ({item}).{hidden}"
                return None, f"у тебя нет {item}. Твой инвентарь: {listed}.{hidden}"
            out.append(got)
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

    async def _items(
        self, npc: str, items: set[str], hero: set[str]
    ) -> tuple[dict[str, str] | None, dict[str, str], int, str]:
        """The names of the items in the game, what stands for an id the npc guessed for a thing the hero
        gives now (`hero`: the guess -> the one of that kind of its story he carries), and today's game
        day; or why not."""
        story = sorted(set(self.things(npc)) - items) if self.things is not None else []
        try:
            state = await self.link.story_state(symbols=[*items, *story], items=[*items, *story])
        except (GameError, TimeoutError) as err:
            return None, {}, 0, f"не выходит проверить предметы: {err}"
        kinds, names = state.get("symbols", {}), state.get("items", {})
        story = [i for i in story if kinds.get(i) == "item"]
        unknown = sorted(i for i in items if kinds.get(i) != "item")
        swaps: dict[str, str] = {}
        for guess in (i for i in unknown if i in hero):
            alike = [i for i in story if _kind(i) == _kind(guess)]
            try:
                have = await self.link.story_eval(npc, [_has(i) for i in alike]) if alike else []
            except (GameError, TimeoutError) as err:
                log.info("deal: what %s means by %s: %s", npc, guess, err or type(err).__name__)
                have = []
            held = [i for i, n in zip(alike, have, strict=False) if isinstance(n, int) and n > 0]
            if len(held) == 1:
                log.info("deal: %s guessed %s for %s", npc, guess, held[0])
                swaps[guess] = held[0]
        unknown = [i for i in unknown if i not in swaps]
        if unknown:
            why = f"нет таких предметов: {', '.join(unknown)}."
            if story:
                kinds_asked = {_kind(i) for i in unknown}
                story.sort(key=lambda i: (_kind(i) not in kinds_asked, i))
                listed = "; ".join(f"{names.get(i) or i} ({i})" for i in story[:STORY_ITEMS])
                why += f" Вещи из твоих дел: {listed}."
            return None, {}, 0, why
        known = {*(i for i in items if i not in swaps), *swaps.values()}
        return {i: str(names.get(i) or i) for i in known}, swaps, int(state.get("day", 0)), ""

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
        if "day" in day:
            self._set_day(day["day"])
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
                **({"xp": deal.xp} if deal.xp else {}),
                **({"teach": [t.fact() for t in deal.teach]} if deal.teach else {}),
                **day,
            }
        )
        if deal.task:
            self.contracts.add(deal.id, deal.npc, deal.name, deal.task, deal.reward, day.get("day"))
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
            deal.npc,
            "[Герой согласился, сделка состоялась. Отзовись коротко. Если ты хотел от него именно этого, "
            "твоё желание исполнено — обнови его (set_intent).]",
            tools=("set_intent",),
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
        if "missing: hero LP" in error:
            # he can't find learning points in his pocket: the offer goes, the npc may offer otherwise
            await self._close(deal, "withdrawn")
            await self._notice("Не хватает очков обучения")
            need = error.removeprefix("missing: hero LP ").replace("(has", "(у него")
            self.dialogue.note(
                deal.npc, f"Сделка сорвалась: у героя не хватает очков обучения — нужно {need}"
            )
            self.dialogue.event_turn(
                deal.npc,
                f"[Сделка не состоялась: у героя не хватает очков обучения — нужно {need}. Скажи ему; "
                "захочешь — предложи иначе.]",
                tools=(PROPOSE_DEAL,),
            )
            return
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
            tasks = any(c.state == "open" and c.due is not None for c in self.contracts.contracts.values())
            if not tasks and not any(not d.paid and not d.broken for d in self.debts.values()):
                continue
            await self.link.wait_world()
            try:
                day = int((await self.link.story_state()).get("day", 0))
            except (GameError, TimeoutError) as err:
                log.info("debts: %s", err or type(err).__name__)
                continue
            self._set_day(day)
            self.overdue(day)
            self.contracts.overdue(day)

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

    def _set_day(self, day: int) -> None:
        if day != self.day:
            self.day = day
            for listener in self.on_day:
                listener(day)

    def reload(self) -> None:
        """The debts again from the chronicle: after a fact written past the deals (the e2e seeds a debt)."""
        self._load()
        for listener in self.on_reload:
            listener()

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


def _kind(item: str) -> str:
    """The kind of an item by its id's prefix: ITWR_ writings, ITMI_ things, ITAR_ armour..."""
    return item.split("_", 1)[0]


def _has(item: str) -> list[Any]:
    """How many the hero has (story_eval, self = the npc)."""
    return ["call", "NPC_HASITEMS", ["inst", "OTHER"], ["sym", item]]


def _swapped(wanted: dict[str, int], swaps: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item, count in wanted.items():
        item = swaps.get(item, item)
        out[item] = out.get(item, 0) + count
    return out


def _parse_xp(raw: Any) -> tuple[int | None, str]:
    try:
        xp = int(raw or 0)
    except (TypeError, ValueError):
        return None, f"опыт — число: {raw}."
    if not 0 <= xp <= MAX_COUNT:
        return None, f"опыт — от 0 до {MAX_COUNT}."
    return xp, ""


def _parse_days(raw: Any) -> tuple[int | None, str]:
    try:
        days = int(raw or 0)
    except (TypeError, ValueError):
        return None, f"срок — число дней: {raw}."
    if not 0 <= days <= MAX_TASK_DAYS:
        return None, f"срок поручения — от 1 до {MAX_TASK_DAYS} дней, 0 — без срока."
    return days, ""


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
