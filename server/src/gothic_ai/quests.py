"""The director's quests for the hero, and what happens when he lies about them (story-graph.md, stage 4).

A quest ("the sheep trample my field, kill them") is backed by creatures the director puts into the world
under a tag, so the chronicle knows whether it was done. Its giver knows only what it saw or was told:
when the hero says it is done, the conversational model decides in character whether to believe him and
pays (`pay_reward`), never more than the giver has. The lie is not blocked. It is written down with the
truth and found out by a rule over the chronicle: the giver sees the creatures alive near it. Then the
giver knows it, sees the hero as an enemy, acts on it through the scripts (a beating until the hero is
down, refusing to talk) and tells the people around, who keep it as a rumor.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import executor
from .chronicle import Chronicle, Fact
from .game import GameError, GameLink
from .journal import Journal
from .llm import Tool, ToolCall
from .protocol import GameMessage, Session, Snapshot
from .turn import ToolOutcome

if TYPE_CHECKING:
    from .dialogue import Conversation, DialogueManager

log = logging.getLogger(__name__)

PAY_REWARD = "pay_reward"
SEE_CM = 2000  # the giver sees living creatures of the quest this close
ATTACK_CM = 1500  # it goes for the hero if he is this close when it finds out
RUMOR_CM = 2000  # who hears the giver's anger
REFUSE_TALK_S = 300  # how long it won't talk in the game's own dialogs
CHECK_EVERY_S = 1.0

PAY_TOOL = Tool(
    PAY_REWARD,
    "Отдать герою обещанную награду за твоё поручение (список поручений — в обстановке). Вызывай, только "
    "если веришь, что он его выполнил. Своими глазами ты этого не видел: решай по его словам, по тому, что "
    "он принёс и показал, и по тому, как ты к нему относишься. Тело подойдёт и отдаст после реплики.",
    {
        "type": "object",
        "properties": {"quest": {"type": "string", "description": "id поручения из обстановки, например Q1"}},
        "required": ["quest"],
        "additionalProperties": False,
    },
)


@dataclass
class Quest:
    id: str
    giver: str  # npc symbol
    giver_name: str
    title: str  # what is to be done, in the giver's words
    tag: str  # the creatures in the world
    instance: str
    count: int
    reward_item: str
    reward_name: str
    reward_count: int
    paid: bool = False
    lie: bool = False  # paid for, though not done
    found_out: bool = False

    def reward(self) -> str:
        return f"{self.reward_name} ×{self.reward_count}"


class QuestBoard:
    """The quests, their situation lines and `pay_reward` for the conversational model (a TurnExtension),
    and the rule that finds lies out."""

    def __init__(
        self,
        link: GameLink,
        dialogue: DialogueManager,
        chronicle: Chronicle,
        *,
        hero_distance: Callable[[str], float | None] | None = None,
        journal: Journal | None = None,
    ) -> None:
        self.link = link
        self.dialogue = dialogue
        self.chronicle = chronicle
        self.hero_distance = hero_distance or (lambda _npc: None)
        self.journal = journal or dialogue.journal
        self.quests: dict[str, Quest] = {}
        self._background: set[asyncio.Task[Any]] = set()
        dialogue.extensions.append(self)
        link.listeners.append(self._on_message)
        self._load()

    # --- the quests -------------------------------------------------------------------------

    async def give(
        self,
        giver: str,
        giver_name: str,
        title: str,
        instance: str,
        count: int,
        reward_item: str,
        reward_name: str,
        reward_count: int,
        distance: int = 3000,
    ) -> Quest:
        """A new quest: its creatures come into the world `distance` cm from the giver."""
        qid = f"Q{len(self.quests) + 1}"
        tag = f"{qid}_{instance.upper()}"
        made = await self.link.story_spawn(instance, tag, count, near=giver, distance=distance)
        self.chronicle.record({"fact": "spawned", "tag": tag, "instance": instance.upper(), "count": made})
        q = Quest(
            qid,
            giver.upper(),
            giver_name,
            title,
            tag,
            instance.upper(),
            made,
            reward_item.upper(),
            reward_name,
            reward_count,
        )
        self.chronicle.record({"fact": "quest", **_fields(q)})
        self.quests[qid] = q
        await self._watch()
        return q

    def done(self, q: Quest) -> bool:
        """The truth: the hero killed them all."""
        return self.chronicle.killed(q.tag, "PLAYER") >= q.count

    def _load(self) -> None:
        """The quests of the branch, from its facts (after a load: as they were at that save)."""
        self.quests.clear()
        for f in self.chronicle.facts():
            q = self.quests.get(f.get("quest") or f.get("id") or "")
            match f.get("fact"):
                case "quest":
                    fields = {k: f[k] for k in _fields_of() if k in f}
                    self.quests[f["id"]] = Quest(**fields)
                case "paid" if q:
                    q.paid = True
                case "lie" if q:
                    q.lie = True
                case "found_out" if q:
                    q.found_out = True

    async def _watch(self) -> None:
        givers = sorted({q.giver for q in self.quests.values() if not q.found_out})
        try:
            await self.link.story_watch(givers)
        except GameError as err:
            log.info("quests: no watch: %s", err)

    def _on_message(self, msg: GameMessage) -> None:
        if isinstance(msg, Session):
            self._load()
            self._spawn(self._watch())

    # --- the conversation (TurnExtension) ------------------------------------------------------

    def situation(self, npc: str) -> str | None:
        lines = []
        for q in self.quests.values():
            if q.giver != npc.upper():
                continue
            if q.found_out:
                state = "Ты знаешь, что герой солгал тебе об этом и забрал награду."
            elif q.paid:
                state = "Ты поверил герою, что это сделано, и заплатил."
            else:
                state = "Сделано ли — не знаешь: знаешь только то, что видел сам и что тебе сказали."
            lines.append(f"Твоё поручение герою (id {q.id}): {q.title}. Награда: {q.reward()}. {state}")
        return "\n".join(lines) or None

    def tools(self, npc: str) -> list[Tool]:
        open_ = any(q.giver == npc.upper() and not q.paid for q in self.quests.values())
        return [PAY_TOOL] if open_ else []

    async def handle(
        self, dialogue: DialogueManager, conv: Conversation, call: ToolCall, snap: Snapshot | None
    ) -> ToolOutcome | None:
        if call.name != PAY_REWARD:
            return None
        q = self.quests.get(str(call.arguments.get("quest", "")).upper())
        if q is None or q.giver != conv.npc:
            return ToolOutcome("Отклонено: нет такого твоего поручения.", "rejected")
        if q.paid:
            return ToolOutcome("Отклонено: за это ты уже заплатил.", "rejected")
        # never more than the giver has
        args, reason = executor.check_give({"item": q.reward_item, "count": q.reward_count}, snap)
        if args is None:
            return ToolOutcome(f"Отклонено: {reason}", "rejected")
        q.paid = True  # also for a second call in this turn
        give = ToolCall("give_item", args, call.call_id)

        async def job() -> tuple[str, bool]:
            return await self._pay(q, conv, give, snap)

        dialogue.after_speech(conv, job)
        return ToolOutcome("Принято: отдашь награду после реплики.", "accepted")

    async def _pay(
        self, q: Quest, conv: Conversation, give: ToolCall, snap: Snapshot | None
    ) -> tuple[str, bool]:
        note, ok = await self.dialogue.give(conv, give, snap)
        if not ok:
            q.paid = False
            return note, False
        done = self.done(q)
        count = int(give.arguments.get("count") or q.reward_count)  # capped by what the giver had
        self.chronicle.record({"fact": "paid", "quest": q.id, "item": q.reward_item, "count": count})
        self._belief(q, True, "hero")
        if not done:
            # not blocked: written down with the truth, to be found out
            q.lie = True
            truth = f"killed {self.chronicle.killed(q.tag, 'PLAYER')} of {q.count}"
            self.chronicle.record({"fact": "lie", "quest": q.id, "npc": q.giver, "truth": truth})
            self.journal.add("lie", npc=q.giver, name=q.giver_name, quest=q.id, truth=truth)
            await self._watch()
        return f"Ты отдал герою награду за поручение «{q.title}»: {q.reward_name} ×{count}", True

    # --- finding out --------------------------------------------------------------------------

    async def run(self) -> None:
        """The rule that finds lies out, checked once a second."""
        while True:
            await asyncio.sleep(CHECK_EVERY_S)
            for q in list(self.quests.values()):
                if q.lie and not q.found_out:
                    d = self.chronicle.near(q.tag, q.giver)
                    if d is not None and d <= SEE_CM:
                        await self.found_out(q, f"увидел их живыми в {max(1, round(d / 100))} м от себя")

    async def found_out(self, q: Quest, how: str) -> None:
        """The giver learns the truth: it remembers, turns against the hero, acts, and tells others."""
        q.found_out = True
        self.chronicle.record({"fact": "found_out", "quest": q.id, "npc": q.giver, "how": how})
        self._belief(q, False, how)
        self.journal.add("lie_found_out", npc=q.giver, name=q.giver_name, quest=q.id, how=how)
        self.dialogue.remember(
            q.giver,
            q.giver_name,
            f"Ты узнал правду: герой солгал тебе, что выполнил твоё поручение «{q.title}», — ты {how}. "
            f"А награду ({q.reward()}) он у тебя взял",
        )
        agenda = "make the hero answer for lying to you and taking your reward"
        self.dialogue.change_intent(q.giver, q.giver_name, attitude="hostile", agenda=agenda)
        effects: list[dict[str, Any]] = [{"do": "refuse_talk", "seconds": REFUSE_TALK_S}]
        near = self.hero_distance(q.giver)
        if near is not None and near <= ATTACK_CM:
            effects.append({"do": "attack", "reason": "AR_NONE"})  # until the hero is down, not to death
        try:
            await self.link.story_effect(q.giver, effects)
            people = await self.link.story_nearby(q.giver, RUMOR_CM)
        except (GameError, TimeoutError) as err:
            log.warning("quests: consequences of %s: %s", q.id, err)
            people = []
        for p in people:
            self.dialogue.remember(
                p["npc"],
                p.get("name") or p["npc"],
                f"Слух: {q.giver_name} при тебе рассказывал, что герой его обманул — сказал, что выполнил "
                f"поручение «{q.title}», взял награду ({q.reward()}), а это неправда",
            )
            self.chronicle.record({"fact": "rumor", "quest": q.id, "from": q.giver, "to": p["npc"]})
        await self._watch()

    def _belief(self, q: Quest, believed: bool, source: str) -> None:
        """What the giver believes about the quest being done, and why."""
        fact = {"fact": "belief", "npc": q.giver, "quest": q.id, "claim": "done", "believed": believed}
        self.chronicle.record({**fact, "source": source})

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)


def _fields_of() -> list[str]:
    return list(Quest.__dataclass_fields__)


def _fields(q: Quest) -> Fact:
    return {k: getattr(q, k) for k in _fields_of() if k not in ("paid", "lie", "found_out")}
