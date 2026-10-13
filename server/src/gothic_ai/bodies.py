"""What became of a free npc's soft act: it got there, was taken away or never did it.

`npc_act` is answered with `npc_act_result` at once (queued or rejected) and, once the npc is done with
it, `npc_idle` carrying the act's id and a reason: done, gone (down, removed, taken over) or timeout (the
bridge gives a soft act up after 30 s, ActMaxMs). `Bodies.act` sends one act and waits for that end.

A newer npc_act on the same npc replaces the bridge's tracking of the older one: the older act then never
gets its npc_idle and ends here as "timeout". Whoever walks an npc keeps other acts off it meanwhile.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Literal

from .game import GameError, GameLink
from .protocol import GameMessage, Loading, NpcActResult, NpcActType, NpcIdle

log = logging.getLogger(__name__)

Status = Literal["done", "gone", "timeout", "rejected", "lost"]

ACT_TIMEOUT_S = 35.0  # the bridge's 30 s, then its npc_idle still has to come
KEEP = 256  # ended acts nobody waited for yet: the answer can come before the wait starts
_REASONS: dict[str, Status] = {"done": "done", "gone": "gone", "timeout": "timeout"}


@dataclass(frozen=True)
class BodyOutcome:
    act: str  # the npc_act id; "" if it was never sent
    npc: str
    status: Status
    detail: str = ""


class Bodies:
    def __init__(self, link: GameLink) -> None:
        self.link = link
        self._ended: OrderedDict[str, BodyOutcome] = OrderedDict()
        self._waiting: dict[str, tuple[str, asyncio.Future[BodyOutcome]]] = {}  # act -> (npc, its end)
        link.listeners.append(self._on_message)

    async def act(
        self, npc: str, action: NpcActType, /, *, timeout: float = ACT_TIMEOUT_S, **args: Any
    ) -> BodyOutcome:
        """Queue a soft act for a free npc and wait for its end. `args` go to the game as-is."""
        try:
            act = await self.link.npc_act(npc, action, **args)
        except GameError as err:
            return BodyOutcome("", npc, "lost", str(err))
        if (ended := self._ended.pop(act, None)) is not None:
            return ended
        fut: asyncio.Future[BodyOutcome] = asyncio.get_running_loop().create_future()
        self._waiting[act] = (npc, fut)
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            log.info("%s %s of %s: no npc_idle in %.0f s", action, act, npc, timeout)
            return BodyOutcome(act, npc, "timeout", "no npc_idle")
        finally:
            self._waiting.pop(act, None)

    def _on_message(self, msg: GameMessage) -> None:
        match msg:
            case NpcActResult(status="rejected"):
                self._end(BodyOutcome(msg.id, msg.npc, "rejected", msg.detail or ""))
            case NpcIdle(act=str() as act):
                reason = msg.reason or "done"
                status = _REASONS.get(reason, "done")
                self._end(BodyOutcome(act, msg.npc, status, "" if reason in _REASONS else reason))
            case Loading():
                # the npc's were released: their acts will never be answered
                for act, (npc, fut) in list(self._waiting.items()):
                    if not fut.done():
                        fut.set_result(BodyOutcome(act, npc, "lost", "the world is loading"))

    def _end(self, outcome: BodyOutcome) -> None:
        waiting = self._waiting.pop(outcome.act, None)
        if waiting is not None:
            if not waiting[1].done():
                waiting[1].set_result(outcome)
            return
        self._ended[outcome.act] = outcome
        while len(self._ended) > KEEP:
            self._ended.popitem(last=False)
