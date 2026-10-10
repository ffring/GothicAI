"""Scripted checks that drive an NPC without an LLM."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .dialogue import DialogueManager
from .game import GameError, GameLink
from .protocol import ActionResult, LineStarted

log = logging.getLogger(__name__)

GESTURES = ("T_COMEOVERHERE", "T_GREETNOV", "T_YES")


@dataclass(frozen=True)
class Step:
    name: str
    result: ActionResult


async def attach_when_free(link: GameLink, npc: str, *, attempts: int = 60, delay: float = 1.0) -> None:
    """Attach, retrying while the npc is busy (e.g. in a scripted dialog with the hero)."""
    for attempt in range(attempts):
        try:
            await link.attach(npc)
            return
        except GameError as err:
            if attempt == attempts - 1:
                raise
            log.info("attach %s failed (%s), retrying", npc, err)
            await asyncio.sleep(delay)


async def m0_smoke(link: GameLink, npc: str = "NONE_100_XARDAS") -> list[Step]:
    """M0 check: take the npc over, walk him around, gesture, bring him home and back to his routine."""
    world = await link.wait_world()
    log.info("world loaded: %s", world)
    await attach_when_free(link, npc)

    snap = await link.snapshot(npc)
    home = snap.npc.waypoint or snap.npc.nearest_waypoint
    away = max(snap.nearby_waypoints, key=lambda w: w.distance, default=None)
    log.info("%s (%s) home=%s away=%s", snap.npc.id, snap.npc.name, home, away and away.name)

    steps: list[Step] = []

    async def step(name: str, coro) -> ActionResult:
        res = await coro
        steps.append(Step(name, res))
        extra = ", ".join(
            x for x in (res.detail, f"{res.distance:.0f}cm" if res.distance is not None else None) if x
        )
        log.info("%-24s %s%s", name, res.status, f" ({extra})" if extra else "")
        return res

    if away is not None:
        await step(f"go_to_waypoint {away.name}", link.act(npc, "go_to_waypoint", waypoint=away.name))
    await step("turn_to PLAYER", link.act(npc, "turn_to", npc="PLAYER"))
    for gesture in GESTURES:
        res = await step(f"play_ani {gesture}", link.act(npc, "play_ani", name=gesture))
        if res.status != "rejected":
            break
    await step("wait 1500", link.act(npc, "wait", ms=1500))
    await step("go_to_npc PLAYER", link.act(npc, "go_to_npc", npc="PLAYER"))
    if home is not None:
        await step(f"go_to_waypoint {home}", link.act(npc, "go_to_waypoint", waypoint=home))
    await step("return_to_routine", link.act(npc, "return_to_routine"))
    return steps


async def m1_chat(
    link: GameLink, dialogue: DialogueManager, npc: str = "NONE_100_XARDAS", text: str = "Кто ты такой?"
) -> list[str]:
    """M1 check without the keyboard: player text injected on the server, the npc must speak every line."""
    await link.wait_world()
    await attach_when_free(link, npc)
    snap = await link.snapshot(npc)
    conv = dialogue.open(npc, snap.npc.name)

    started: list[str] = []
    link.listeners.append(lambda msg: started.append(msg.line) if isinstance(msg, LineStarted) else None)

    await dialogue.say_as_player(npc, text)
    lines = conv.last_lines
    log.info("%s said %d line(s), waiting for the npc to speak them", npc, len(lines))
    for _ in range(20 * max(1, len(lines))):  # ~10 s per line
        if all(line in started for line in lines):
            break
        await asyncio.sleep(0.5)
    await link.act(npc, "return_to_routine")
    missing = [line for line in lines if line not in started]
    if not lines or missing:
        raise RuntimeError(f"npc did not speak lines: {missing or 'no lines generated'}")
    return lines
