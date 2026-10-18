"""Project known, unresolved deeds into the native NPC encounter memory.

The native B_GetPlayerCrime remains in charge of its two-day expiry for sheep/assault and of
encounter behavior until layer 4b. The projection never calls B_MemorizePlayerCrime: that would
increment the script law counters again. Unchanged projections are not repeated, preserving native
Charm's local clearing until this NPC's projected knowledge changes or the world is loaded again.
"""

from __future__ import annotations

import asyncio
import logging

from .chronicle import Fact
from .crimes import Crimes
from .game import GameError, GameLink
from .protocol import GameMessage, Loading, Session

log = logging.getLogger(__name__)
CRIME_LEVELS = {"sheep": 1, "attack": 2, "theft": 3, "murder": 4}
POLL_SECONDS = 1.0


def _memory_cases(crimes: Crimes, npc: str) -> list[Fact]:
    npc = npc.upper()
    if any(f.get("npc") == npc for f in crimes.chronicle.facts("died")):
        return []
    absolved = {
        crime for fact in crimes.chronicle.facts("absolved") for crime in fact.get("crimes", [])
    }
    return [
        k.crime for k in crimes.known(npc)
        if k.degree != "suspects"
        and k.suspect == "PLAYER"
        and not k.forgiven
        and not k.silenced
        and k.crime["id"] not in absolved
        and k.crime.get("kind") in CRIME_LEVELS
    ]


def memory_value(crimes: Crimes, npc: str) -> tuple[int, int]:
    """Highest native crime and its day, excluding suspicions and resolved personal claims."""
    return _native_value(_memory_cases(crimes, npc))


def _native_value(cases: list[Fact]) -> tuple[int, int]:
    return max(
        ((CRIME_LEVELS[c["kind"]], int(c.get("day", 0))) for c in cases), default=(0, 0)
    )


class CrimeMemoryRuntime:
    def __init__(self, link: GameLink, crimes: Crimes) -> None:
        self.link, self.crimes, self.chronicle = link, crimes, crimes.chronicle
        self._generation = 0
        self._projection: dict[str, tuple[int, int, tuple[str, ...]]] = {}
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self.link.listeners.append(self._on_message)
        self.chronicle.on_fact.append(self._on_fact)

    def _on_message(self, message: GameMessage) -> None:
        if isinstance(message, (Session, Loading)):
            self._generation += 1
            self._projection.clear()
            self._wake.set()

    def _on_fact(self, fact: Fact) -> None:
        if fact.get("fact") in {
            "crime", "crime_seen", "rumor", "testimony", "silenced", "forgiven", "absolved", "died"
        }:
            self._wake.set()

    async def project(self, npc: str) -> None:
        """Flush one NPC before its on-scene reaction, also used by the background worker."""
        async with self._lock:
            if self.chronicle.branch is None:
                return
            npc = npc.upper()
            generation, branch = self._generation, self.chronicle.branch
            cases = _memory_cases(self.crimes, npc)
            value = _native_value(cases)
            state = (*value, tuple(sorted(c["id"] for c in cases)))
            if self._projection.get(npc) == state:
                return
            await self.link.story_effect(
                npc, [{"do": "crime_memory", "value": value[0], "day": value[1]}]
            )
            if generation == self._generation and branch == self.chronicle.branch:
                self._projection[npc] = state

    async def tick(self) -> None:
        recipients = {
            str(f.get("npc") or f.get("to")).upper()
            for f in self.chronicle.facts()
            if f.get("fact") in {"crime_seen", "rumor", "testimony"}
            and (f.get("npc") or f.get("to"))
            and (f.get("fact") != "rumor" or f.get("crime"))
        }
        for npc in sorted(recipients | self._projection.keys()):
            try:
                await self.project(npc)
            except (GameError, TimeoutError) as error:
                # An NPC in another world is retried after travel, without blocking present witnesses.
                log.debug("crime memory %s: %s", npc, error or type(error).__name__)

    async def run(self) -> None:
        while not self.link.closed.is_set():
            self._wake.clear()
            await self.link.wait_world()
            await self.tick()
            try:
                await asyncio.wait_for(self._wake.wait(), POLL_SECONDS)
            except TimeoutError:
                pass

    async def close(self) -> None:
        self.link.listeners.remove(self._on_message)
        self.chronicle.on_fact.remove(self._on_fact)
