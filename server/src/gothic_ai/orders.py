"""Orders to the hero's companions, carried out at once: "За мной!", "Стой!", "В атаку!" (commands.py
tells an order from other words).

The conversational model answers in seconds, too late for "Стой!" in a fight. An order skips it: the
body moves in the moment the transcript is in, and the npc says a short "Понял!" in its own voice
(fillers.py). The order goes into its memory, so the conversational model knows of it next time, but
no answer is asked of it.

**Who obeys**: an npc that goes with the hero — its intent is to follow or to guard him, it agreed to in
conversation — or that stands where he told it to. Named, only that one ("Горн, стой!"); else every
such npc within ORDER_CM. To anyone else the words are no order: they go to the conversational model,
and the npc decides (it may agree to come along). So does an attack with nobody to attack (the hero
looks at no one): the npc asks whom.

**What the body does** (bridge actions; the npc is held through talk.py while it obeys):
- follow: follows the hero; come, retreat: breaks off whatever it does (a fight, a wait) and follows
  him;
- halt: stops where it is, for HALT_S, then goes on as before; wait: stays there until the next
  order, at most WAIT_S, then goes back to its own business;
- attack: fights whom the hero looks at (the game's own combat), then follows him again;
- draw, sheathe: takes its weapon out or puts it away (sheathe also ends a fight it was ordered into);
- dismiss: goes back to its own business; it no longer goes with the hero.
A new order replaces the one before (the bridge preempts the running action).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import commands
from .commands import Order
from .dialogue import DialogueManager
from .game import GameError, GameLink
from .journal import Journal

if TYPE_CHECKING:
    from .talk import Addressee, TalkDesk

log = logging.getLogger(__name__)

ORDER_CM = 3000.0  # companions hear an order this far
HALT_S = 20.0  # "Стой!" holds this long
WAIT_S = 600.0  # "Жди здесь!" at most this long
COMPANION_GOALS = frozenset({"follow_hero", "guard_hero"})
WEAPONS = frozenset({"draw", "sheathe"})


@dataclass(eq=False)
class Standing:
    """What an npc does on the hero's order; a later order puts another in its place."""

    kind: str  # the order (commands.KINDS)


def failed(status: str) -> str | None:
    """What went wrong with an action (`Orders._act`), None if nothing did."""
    return None if status in ("done", "cancelled") else status


class Orders:
    def __init__(
        self,
        link: GameLink,
        talk: TalkDesk,
        dialogue: DialogueManager,
        *,
        journal: Journal | None = None,
        halt_s: float = HALT_S,
        wait_s: float = WAIT_S,
    ) -> None:
        self.link = link
        self.talk = talk
        self.dialogue = dialogue
        self.journal = journal or dialogue.journal
        self.halt_s, self.wait_s = halt_s, wait_s
        self.standing: dict[str, Standing] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # --- who obeys -----------------------------------------------------------------------------

    def companion(self, npc: str) -> bool:
        """The npc goes with the hero (or stands on his order)."""
        npc = npc.upper()
        if npc in self.standing:
            return True
        conv = self.dialogue.conversations.get(npc)
        return conv is not None and conv.intent.goal in COMPANION_GOALS

    def squad(self, to: Addressee | None) -> list[tuple[str, str]]:
        """Who obeys an order: the npc called by name if it is a companion (else nobody: it is words to
        it), else every companion near enough. (id, name)."""
        if to is not None and to.why == "name":
            return [(to.id, to.name)] if self.companion(to.id) else []
        return [
            (e.id.upper(), e.name)
            for e in sorted(self.talk.people.values(), key=lambda e: e.distance)
            if e.activity != "down" and e.distance <= ORDER_CM and self.companion(e.id)
        ]

    def target(self, focus: str | None, squad: list[tuple[str, str]]) -> str | None:
        """Whom to attack: the one the hero looks at, if not himself or one of his own."""
        if focus is None:
            return None
        focus = focus.upper()
        if focus == "PLAYER" or focus in {npc for npc, _ in squad} or self.companion(focus):
            return None
        e = self.talk.people.get(focus)
        return None if e is not None and e.activity == "down" else focus

    def take(self, order: Order, focus: str | None, to: Addressee | None) -> list[tuple[str, str]]:
        """Carry the order out at once, if anyone obeys it; who does ([]: nobody, it is words)."""
        squad = self.squad(to)
        target = self.target(focus, squad) if order.kind == "attack" else None
        if not squad or (order.kind == "attack" and target is None):
            return []
        for npc, name in squad:
            before = self.standing.get(npc)
            if order.kind in WEAPONS:
                standing = before  # the weapon changes no standing order: it waits, follows... as before
            else:
                standing = self.standing[npc] = Standing(order.kind)
            self.journal.add("order", npc=npc, name=name, order=order.kind, text=order.heard, target=target)
            log.info("order %s -> %s (%s)%s", order.kind, npc, name, f" at {target}" if target else "")
            self._spawn(self._obey(npc, name, order, standing, before, target))
        return squad

    # --- the body ------------------------------------------------------------------------------

    def _current(self, npc: str, standing: Standing) -> bool:
        return self.standing.get(npc) is standing

    def _following(self, npc: str) -> bool:
        conv = self.dialogue.conversations.get(npc)
        return conv is not None and conv.intent.goal in COMPANION_GOALS

    async def _obey(
        self,
        npc: str,
        name: str,
        order: Order,
        standing: Standing | None,
        before: Standing | None,
        target: str | None,
    ) -> None:
        self._spawn(self.dialogue.acknowledge(npc, name))
        what = commands.SAID[order.kind]
        self.dialogue.remember(npc, name, f"Герой приказал тебе: «{order.heard}» — {what}. Ты подчинился.")
        if order.kind in WEAPONS:
            failed = await self._weapon(npc, order.kind, before)
        else:
            assert standing is not None
            failed = await self._carry_out(npc, name, order.kind, standing, target)
        if failed:
            self.dialogue.remember(npc, name, f"Приказ героя ({what}) ты выполнить не смог: {failed}")

    async def _carry_out(
        self, npc: str, name: str, kind: str, standing: Standing, target: str | None
    ) -> str | None:
        """The body's part of the order; what did not work, None if all did."""
        if kind in ("follow", "come", "retreat"):
            return await self._follow(npc, stop=kind != "follow")
        if kind == "dismiss":
            self.standing.pop(npc, None)
            await self._act(npc, "stop")
            if self._following(npc):
                self.dialogue.change_intent(npc, name, goal="keep_routine", target=None, body=False)
            self.talk.free(npc, "follow")
            self.talk.free(npc, "order")
            return None
        if not await self.talk.hold(npc, "order"):
            return "тебя не отпускают"
        if kind == "attack":
            assert target is not None
            status = await self._act(npc, "fight", npc=target, timeout=70)
            if status != "cancelled":  # the fight is over (not cut by a later order): back to the hero
                await self._resume(npc, standing)
            return failed(status)
        # halt, wait
        status = await self._act(npc, "stop")
        self._later(self.halt_s if kind == "halt" else self.wait_s, npc, standing)
        return failed(status)

    async def _weapon(self, npc: str, kind: str, before: Standing | None) -> str | None:
        """Take the weapon out or put it away; put away, it ends a fight the npc was ordered into. An npc
        not held for anything is held only for this."""
        held = npc in self.talk.held
        if not await self.talk.hold(npc, "order"):
            return "тебя не отпускают"
        if kind == "sheathe" and before is not None and before.kind == "attack":
            await self._resume(npc, before)
        status = await self._act(npc, "draw_weapon" if kind == "draw" else "remove_weapon")
        if not held:
            self.talk.free(npc, "order")
        return failed(status)

    async def _follow(self, npc: str, *, stop: bool) -> str | None:
        """Follow the hero; `stop`: break off what it does first (a fight, a wait)."""
        if not await self.talk.hold(npc, "follow"):
            return "тебя не отпускают"
        if stop and (err := failed(await self._act(npc, "stop"))) is not None:
            return err
        err = failed(await self._act(npc, "follow", npc="PLAYER"))
        self.talk.free(npc, "order")
        return err

    async def _resume(self, npc: str, standing: Standing | None) -> None:
        """After an order that ends (attack, halt): a companion follows the hero again, an npc that only
        stood on his order goes back to its business."""
        if standing is not None and not self._current(npc, standing):
            return
        self.standing.pop(npc, None)
        if self._following(npc):
            await self._follow(npc, stop=True)
        else:
            await self._act(npc, "stop")
            self.talk.free(npc, "order")

    def _later(self, seconds: float, npc: str, standing: Standing) -> None:
        async def run() -> None:
            await asyncio.sleep(seconds)
            if not self._current(npc, standing):
                return
            if standing.kind == "wait":  # waited long enough: back to its own business
                self.standing.pop(npc, None)
                await self._act(npc, "stop")
                self.talk.free(npc, "follow")
                self.talk.free(npc, "order")
                return
            await self._resume(npc, standing)

        self._spawn(run())

    async def _act(self, npc: str, action: str, /, *, timeout: float = 10, **args: Any) -> str:
        """ "done", "cancelled" (a later order took over) or what went wrong."""
        try:
            res = await self.link.act(npc, action, timeout=timeout, **args)  # type: ignore[arg-type]
        except (GameError, TimeoutError) as err:
            log.info("%s %s failed: %s", npc, action, err or "no answer")
            return f"{action}: {err or 'нет ответа'}"
        if res.status in ("done", "cancelled"):
            return res.status
        log.info("%s %s: %s %s", npc, action, res.status, res.detail or "")
        return f"{action}: {res.detail or res.status}"

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
