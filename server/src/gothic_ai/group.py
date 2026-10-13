"""Talk with several npc's at once, step B1 (docs/npc-tools.md «Беседа нескольких»): a shared transcript.

The npc's around a conversation used to be deaf to each other. The hero's words went into the memories of
everyone near him, but an npc's answer only into its own: on 2026-10-13 Brago shouted «Держи его!» not
knowing what his mate had just said to the hero. Now:

- **what an npc says is heard** by everyone within `SAID_CM` of it. It goes into their memories as the
  hero's words do (talk.py): «Ты слышал, как Бандит говорит герою: «…»». Only what was said counts: an
  answer the hero cut off is heard as far as it went. No model call;
- **the conversation around the hero has participants**: those he spoke to and those who spoke in it,
  while they are within `GROUP_CM` of him. Each of them is told who else is in it and who speaks now
  («В разговоре с героем ещё: Браго, Кавалорн. Сейчас говорит: Браго.»). It is over when nobody has said
  a word in it for `QUIET_S`.

One floor and who interjects are steps B2–B3.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .dialogue import DialogueManager
from .game import GameError, GameLink
from .llm import Tool, ToolCall
from .protocol import GameMessage, Loading, Snapshot
from .turn import ToolOutcome

if TYPE_CHECKING:
    from .dialogue import Conversation
    from .talk import Addressee, TalkDesk

log = logging.getLogger(__name__)

SAID_CM = 1500  # an npc's words are heard this far (talk.PARTNER_CM: the hero hears his partner as far)
GROUP_CM = 1500.0  # a participant farther than this from the hero is out of the conversation
QUIET_S = 60.0  # nobody has said a word in it this long: the conversation is over
TO_HERO = frozenset({"player", "initiative"})  # turns said to the hero, whoever else stands by


@dataclass
class Member:
    name: str
    since: float  # when it joined
    last: float  # when it last spoke or was spoken to


class GroupTalk:
    """The participants of the conversation around the hero, and who hears whose words. A turn extension:
    the situation line; no tools."""

    def __init__(
        self,
        link: GameLink,
        dialogue: DialogueManager,
        talk: TalkDesk,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.link = link
        self.dialogue = dialogue
        self.talk = talk
        self.clock = clock
        self.members: dict[str, Member] = {}
        self.last = float("-inf")  # when a word was last said in the conversation, by anyone
        self._tasks: set[asyncio.Task[Any]] = set()
        link.listeners.append(self.on_message)
        dialogue.on_said.append(self.on_said)
        talk.on_phrase.append(self.on_phrase)

    # --- who is in it --------------------------------------------------------------------------

    def on_message(self, msg: GameMessage) -> None:
        if isinstance(msg, Loading):
            self.members.clear()

    def on_phrase(self, to: Addressee | None, text: str) -> None:
        """The hero said something: whom he said it to joins the conversation."""
        now = self.clock()
        self.last = now
        if to is not None:
            self._join(to.id, to.name, now)

    def on_said(self, npc: str, name: str, text: str, kind: str) -> None:
        """An npc said its lines: it takes part if it is near the hero, and whoever stands by hears it."""
        npc = npc.upper()
        now = self.clock()
        if self._near_hero(npc):
            self.last = now
            self._join(npc, name, now)
        to_hero = kind in TO_HERO or (self.talk.partner is not None and self.talk.partner.npc == npc)
        self._spawn(self._heard(npc, name, text, "герою" if to_hero else "вслух"))

    def participants(self) -> dict[str, Member]:
        """Those in the conversation now: near the hero and on their feet, while it goes on."""
        if self.clock() - self.last > QUIET_S:
            self.members.clear()
        for npc in [n for n in self.members if not self._near_hero(n)]:
            log.info("%s is out of the conversation", npc)
            del self.members[npc]
        return self.members

    def _join(self, npc: str, name: str, now: float) -> None:
        npc = npc.upper()
        if npc == "PLAYER":
            return
        m = self.members.get(npc)
        if m is None:
            self.members[npc] = Member(name, now, now)
            if len(self.members) > 1:
                log.info("%s joins the conversation: %s", npc, ", ".join(self.members))
        else:
            m.last = now

    def _near_hero(self, npc: str) -> bool:
        e = self.talk.people.get(npc.upper())
        return e is not None and e.activity != "down" and e.distance <= GROUP_CM

    # --- who hears it --------------------------------------------------------------------------

    async def _heard(self, npc: str, name: str, text: str, whom: str) -> None:
        try:
            near = await self.link.story_nearby(npc, SAID_CM)
        except (GameError, TimeoutError) as err:
            log.info("who heard %s: %s", npc, err or type(err).__name__)
            return
        for p in near:
            hearer = str(p.get("npc", "")).upper()
            if not hearer or hearer == npc or p.get("dead"):
                continue
            self.dialogue.heard(hearer, str(p.get("name") or hearer), name, text, whom)

    # --- the turn extension --------------------------------------------------------------------

    async def prepare(self, npc: str) -> None:
        return None

    def situation(self, npc: str) -> str | None:
        npc = npc.upper()
        members = self.participants()
        if npc not in members or len(members) < 2:
            return None
        others = [m.name for n, m in members.items() if n != npc]
        line = f"В разговоре с героем ещё: {', '.join(others)}."
        speaking = [m.name for n, m in members.items() if n != npc and self.dialogue.speaking(n)]
        if speaking:
            line += f" Сейчас говорит: {', '.join(speaking)}."
        return line

    def tools(self, npc: str) -> list[Tool]:
        return []

    async def handle(
        self, dialogue: DialogueManager, conv: Conversation, call: ToolCall, snap: Snapshot | None
    ) -> ToolOutcome | None:
        return None

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
