"""What became of a free npc's soft act: it got there, was taken away or never did it.

`npc_act` is answered with `npc_act_result` at once (queued or rejected) and, once the npc is done with
it, `npc_idle` carrying the act's id and a reason: done, gone (down, removed, taken over) or timeout (the
bridge gives a soft act up after 30 s, ActMaxMs). Since v15 also arrived (a `start_activity` with
`arrive` got to its waypoint) and failed (a `give` that could not be done, with the bridge's detail).
`Bodies.act` sends one act and waits for that end.

A newer npc_act on the same npc replaces the bridge's tracking of the older one: the older act then never
gets its npc_idle. So once the newer one is queued (ours, or anyone's: the planner's applier, a reflex),
the older one ends here as "gone", replaced, instead of waiting out its timeout. Whoever walks an npc keeps
other acts off it meanwhile (`busy` tells whether one of ours is under way).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any, Literal

from .game import GameError, GameLink
from .protocol import GameMessage, Loading, NpcActResult, NpcActType, NpcIdle

log = logging.getLogger(__name__)

Status = Literal["done", "gone", "timeout", "arrived", "failed", "rejected", "lost"]

ACT_TIMEOUT_S = 35.0  # the bridge's 30 s, then its npc_idle still has to come
KEEP = 256  # ended acts nobody waited for yet: the answer can come before the wait starts
_REASONS: dict[str, Status] = {
    "done": "done",
    "gone": "gone",
    "timeout": "timeout",
    "arrived": "arrived",
    "failed": "failed",
}


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
        self._acting: Counter[str] = Counter()  # npc -> our acts under way, from the send to the end
        self._queued: dict[str, str] = {}  # npc -> the act the bridge tracks for it now (the latest queued)
        link.listeners.append(self._on_message)

    def busy(self, npc: str) -> bool:
        """An act of ours on the npc is under way: another one now would drop its tracking (a walk's
        npc_idle never comes)."""
        return self._acting[npc.upper()] > 0 or self.link.body_owned(npc)

    async def act(
        self, npc: str, action: NpcActType, /, *, timeout: float = ACT_TIMEOUT_S, **args: Any
    ) -> BodyOutcome:
        """Queue a soft act for a free npc and wait for its end. `args` go to the game as-is."""
        key = npc.upper()
        if self.link.body_owned(npc):
            return BodyOutcome("", npc, "rejected", "NPC body is owned by the combat provider")
        self._acting[key] += 1
        try:
            return await self._act(npc, action, timeout, args)
        finally:
            self._acting[key] -= 1
            if self._acting[key] <= 0:
                del self._acting[key]

    async def _act(self, npc: str, action: NpcActType, timeout: float, args: dict[str, Any]) -> BodyOutcome:
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
                # the bridge rejects before it touches the tracking: the act before goes on
                self._end(BodyOutcome(msg.id, msg.npc, "rejected", msg.detail or ""))
            case NpcActResult(status="queued"):
                # the bridge tracks one act per npc and replies in order: the one queued before is dropped
                key = msg.npc.upper()
                before, self._queued[key] = self._queued.get(key), msg.id
                if before is not None and before != msg.id:
                    self._end(BodyOutcome(before, msg.npc, "gone", f"replaced by {msg.id}"))
            case NpcIdle(act=str() as act):
                if self._queued.get(msg.npc.upper()) == act:
                    del self._queued[msg.npc.upper()]
                reason = msg.reason or "done"
                if reason in _REASONS:
                    self._end(BodyOutcome(act, msg.npc, _REASONS[reason], msg.detail or ""))
                else:  # a reason it doesn't know still ends the act
                    self._end(BodyOutcome(act, msg.npc, "done", reason))
            case Loading():
                # the npc's were released: their acts will never be answered
                self._queued.clear()
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
