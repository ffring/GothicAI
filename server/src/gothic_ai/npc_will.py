"""Character rules that start nearby physical deeds, or accusations, without a model.

Motives and starts belong to the save branch. A distant or occupied body keeps its motive until
an actual meeting; no off-screen result is invented here. MOTIVES is the complete rule table.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .characters import Card
from .chronicle import Chronicle, Fact
from .game import GameError, GameLink
from .memory import MemoryStore
from .npc_crimes import NpcCrimes
from .people_knowledge import PeopleKnowledge
from .protocol import GameMessage, Loading, MemoryCmd, NpcIdle, NpcRoster, NpcView, NpcViews, Session
from .social import Rumors
from .storyguard import StoryGuard, asleep

log = logging.getLogger(__name__)
DAY = 24 * 60
SIMULATION_CM = 3000
MEET_CM = 1500
ROUTINE_CM = 2000
OWNER_NEAR_CM = 2000
VIEW_SECONDS = 3.0
LOOP_SECONDS = 60.0
LOOP_STARTS = 8


def greed_interval(card: Card | None) -> int:
    """The character supplies the normal frequency; a missing card supplies no motive."""
    if card is None or card.honesty > 2 or card.greed < 4:
        return 0
    return (2 if card.honesty == 1 else 4) * DAY


@dataclass(frozen=True)
class MotiveRule:
    discover: str
    perform: str
    choose: Callable[[Card | None], str | None]
    interval: Callable[[Card | None], int] = lambda _: 0

    def accepts(self, card: Card | None) -> bool:
        return self.choose(card) is not None

    def action(self, card: Card | None) -> str | None:
        return self.choose(card)

    def cooldown(self, card: Card | None) -> int:
        return self.interval(card)


MOTIVES = {
    "reclaim": MotiveRule(
        "_discover_reclaim", "_meet",
        lambda card: None if card is None else "fight" if card.courage >= 3 else "accuse",
    ),
    "suspect": MotiveRule("_discover_suspect", "_meet", lambda _: "accuse"),
    "greed": MotiveRule(
        "_discover_greed", "_steal", lambda card: "steal" if greed_interval(card) else None,
        greed_interval,
    ),
}


class LoopGuard:
    """A backstop for broken rules, rather than a source of ordinary character frequency."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.starts: dict[str, deque[float]] = defaultdict(deque)
        self.refused = 0

    def claim(self, npc: str) -> bool:
        now, starts = self.clock(), self.starts[npc.upper()]
        while starts and starts[0] <= now - LOOP_SECONDS:
            starts.popleft()
        if len(starts) >= LOOP_STARTS:
            self.refused += 1
            log.warning("npc will loop: %s attempted more than %s starts in %.0fs", npc, LOOP_STARTS,
                        LOOP_SECONDS)
            return False
        starts.append(now)
        return True


class NpcWill:
    def __init__(
        self, link: GameLink, chronicle: Chronicle, npc_crimes: NpcCrimes, *,
        cards: Mapping[str, Card] | None = None, guard: StoryGuard | None = None,
        people: PeopleKnowledge | None = None, rumors: Rumors | None = None,
        memory: MemoryStore | None = None, busy: Callable[[str], bool] = lambda _: False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.link, self.chronicle, self.npc_crimes = link, chronicle, npc_crimes
        self.cards = cards if cards is not None else npc_crimes.cards
        self.guard, self.people = guard or npc_crimes.guard, people or PeopleKnowledge(chronicle)
        self.rumors, self.memory, self.busy, self.clock = rumors, memory, busy, clock
        self.roster = npc_crimes.roster
        self.views: dict[str, NpcView] = {}
        self._view_at: dict[str, float] = {}
        self._hours: dict[str, int] = {}
        self._active: dict[str, str] = {}
        self._generation = 0
        self._now: int | None = None
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self.loop_guard = LoopGuard(clock)
        self._scheduled: asyncio.Task[Any] | None = None
        link.listeners.append(self._on_message)
        chronicle.on_fact.append(self._on_fact)

    @property
    def enabled(self) -> bool:
        return self.npc_crimes.enabled

    async def close(self) -> None:
        if self._on_message in self.link.listeners:
            self.link.listeners.remove(self._on_message)
        if self._on_fact in self.chronicle.on_fact:
            self.chronicle.on_fact.remove(self._on_fact)
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    def _on_message(self, message: GameMessage) -> None:
        if isinstance(message, (Loading, Session)):
            self._generation += 1
            self._now = None
            self.views.clear()
            self._view_at.clear()
            self._hours.clear()
            self._active.clear()
            for task in tuple(self._tasks):
                task.cancel()
            self._scheduled = None
        elif isinstance(message, NpcViews):
            for view in message.views:
                npc = view.npc.upper()
                self.views[npc], self._view_at[npc] = view, self.clock()
                if view.time is not None:
                    self._now = view.time
            self._kick()
        elif isinstance(message, NpcRoster):
            self._kick()
        elif isinstance(message, NpcIdle) and self._active.get(message.npc.upper()) == message.act:
            self._active.pop(message.npc.upper(), None)

    def _on_fact(self, fact: Fact) -> None:
        if fact.get("fact") in {"npc_crime", "npc_crime_seen", "npc_crime_suspected", "rumor"}:
            self._kick()

    def _kick(self) -> None:
        if not self.enabled or (self._scheduled is not None and not self._scheduled.done()):
            return

        async def run() -> None:
            try:
                await self.tick()
            except (GameError, TimeoutError, ConnectionError) as error:
                log.debug("npc will: %s", error)
            except Exception:
                log.exception("npc will rule failed")

        self._scheduled = asyncio.create_task(run())
        self._tasks.add(self._scheduled)
        self._scheduled.add_done_callback(self._tasks.discard)

    async def tick(self, now: int | None = None) -> None:
        """Check character rules on the game hour, and pending claims on roster meetings."""
        if not self.enabled or self.chronicle.branch is None:
            return
        async with self._lock:
            generation = self._generation
            if now is not None:
                self._now = now
            elif self._now is None and self.rumors is not None and self.rumors.clock is not None:
                self._now = self.rumors.clock
            if self._now is None:
                return
            for rule in MOTIVES.values():
                await getattr(self, rule.discover)(generation)
                if generation != self._generation:
                    return
            for motive in self.pending():
                if generation != self._generation or not self.enabled:
                    return
                rule = MOTIVES.get(motive.get("kind", ""))
                if rule is None or not rule.accepts(self.cards.get(motive["npc"])):
                    continue
                try:
                    await getattr(self, rule.perform)(motive, rule, generation)
                except (GameError, TimeoutError, ConnectionError) as error:
                    log.debug("npc will %s waits: %s", motive["id"], error)

    evaluate = tick

    def pending(self) -> list[Fact]:
        begun = {f.get("motive") for f in self.chronicle.facts("npc_will")}
        return [f for f in self.chronicle.facts("npc_motive") if f.get("id") not in begun]

    def _stamp(self) -> dict[str, int]:
        now = self._now or 0
        return {"at": now, "day": now // DAY, "hour": now % DAY // 60, "minute": now % 60}

    def _remember(self, npc: str, kind: str, target: str, key: str, why: str, **extra: Any) -> None:
        if any(f.get("id") == key for f in self.chronicle.facts("npc_motive")):
            return
        self.chronicle.record({
            "fact": "npc_motive", "id": key, "npc": npc, "kind": kind, "target": target,
            "why": why, **extra, **self._stamp(),
        })

    def _resolved(self, crime: str) -> bool:
        return any(f.get("crime") == crime for f in self.chronicle.facts("npc_crime_restitution"))

    async def _discover_reclaim(self, generation: int) -> None:
        for crime in self.npc_crimes.facts():
            owner, culprit, cid = crime.get("owner"), crime.get("culprit"), crime.get("id")
            if crime.get("kind") != "theft" or not owner or not culprit or self._resolved(cid):
                continue
            # Layer four already acted for a witness owner on the original scene. Its completed
            # reaction must not turn into a second, delayed attempt of the same personal claim.
            if any(f.get("crime") == cid and f.get("npc") == owner
                   for f in self.chronicle.facts("npc_crime_reaction")):
                continue
            if any(k.kind == "npc_theft" and k.subject == culprit and
                   k.key == f"npc_crime_seen:{cid}" for k in self.people.of(owner)):
                self._remember(owner, "reclaim", culprit, f"reclaim:{owner}:{cid}",
                               "знает, кто украл его вещь", crime=cid)

    async def _discover_suspect(self, generation: int) -> None:
        for suspicion in self.chronicle.facts("npc_crime_suspected"):
            npc, target, cid = suspicion.get("npc"), suspicion.get("suspect"), suspicion.get("crime")
            if not npc or not target or target in {"PLAYER", "HERO", "PC_HERO"} or not cid:
                continue
            if self.npc_crimes.crime(cid) is not None and not self._resolved(cid):
                self._remember(npc, "suspect", target, f"suspect:{npc}:{cid}:{target}",
                               "вспомнил его около сундука во время пропажи; доказательств нет", crime=cid)

    def _cool(self, npc: str, card: Card | None) -> bool:
        interval = greed_interval(card)
        if not interval:
            return False
        starts = [int(f.get("at", int(f.get("day", 0)) * DAY + int(f.get("hour", 0)) * 60
                                + int(f.get("minute", 0))))
                  for f in self.chronicle.facts("npc_will")
                  if f.get("npc") == npc and f.get("kind") == "greed"]
        return not starts or (self._now or 0) - max(starts) >= interval

    def _free(self, npc: str) -> bool:
        entry = self.roster.get(npc)
        # The engine sets AiNormal strictly inside 3000 cm (worldobjects.cpp). Do not use the
        # planner's tier hysteresis, which can retain a T2 actor already outside that boundary.
        return bool(
            entry is not None and entry.distance < SIMULATION_CM and entry.activity == "routine"
            and not asleep(entry.state) and not self.guard.body(npc, entry.state)
            and not self.busy(npc) and not getattr(self.link, "body_owned", lambda _: False)(npc)
            and npc not in self._active
        )

    async def _discover_greed(self, generation: int) -> None:
        social = self.rumors.social if self.rumors is not None else None
        if social is None:
            return
        for npc in list(self.roster):
            card = self.cards.get(npc)
            if not self._free(npc) or not self._cool(npc, card):
                continue
            hour = (self._now or 0) // 60
            if self._hours.get(npc) == hour:
                continue
            self._hours[npc] = hour
            if any(f.get("npc") == npc and f.get("kind") == "greed" for f in self.pending()):
                continue
            spots = [s.pos for s in social.days.get(npc, ()) if s.pos is not None]
            if not spots:
                continue
            for owner in list(self.roster):
                if owner == npc or not self._free(owner):
                    continue
                try:
                    chests = await self.link.npc_chests(owner)
                except (GameError, TimeoutError, ConnectionError) as error:
                    log.debug("npc will chests of %s unavailable: %s", owner, error)
                    continue
                for chest in chests:
                    if generation != self._generation:
                        return
                    if (chest.why != "owner" or chest.distance <= OWNER_NEAR_CM
                            or not any(math.dist(chest.pos, pos) <= ROUTINE_CM for pos in spots)):
                        continue
                    for item in chest.items:
                        if item.count <= 0:
                            continue
                        args = {"chest": chest.id, "what": item.item, "count": 1, "deed": True}
                        try:
                            reason = await self.npc_crimes.check_command(npc, "steal", args)
                        except (GameError, TimeoutError, ConnectionError) as error:
                            log.debug("npc will target %s unavailable: %s", item.item, error)
                            continue
                        if generation != self._generation:
                            return
                        if reason or args.get("expected_owner") != owner:
                            continue
                        self._remember(
                            npc, "greed", chest.id, f"greed:{npc}:{self._now}",
                            f"честность {card.honesty}, жадность {card.greed}; чужой сундук в обычном месте",
                            owner=owner, item=item.item,
                        )
                        break
                    else:
                        continue
                    break
                else:
                    continue
                break

    async def _view(self, npc: str, generation: int) -> NpcView | None:
        views = await self.link.npc_view([npc])
        if generation != self._generation or not any(view.npc.upper() == npc for view in views):
            return None
        for view in views:
            who = view.npc.upper()
            self.views[who], self._view_at[who] = view, self.clock()
        if self.clock() - self._view_at.get(npc, -math.inf) > VIEW_SECONDS:
            return None
        return self.views.get(npc)

    async def _steal(self, motive: Fact, rule: MotiveRule, generation: int) -> None:
        npc, owner = motive["npc"], motive["owner"]
        if not self._free(npc) or not self._free(owner) or not self._cool(npc, self.cards.get(npc)):
            return
        chest = next((c for c in await self.link.npc_chests(owner) if c.id == motive["target"]), None)
        if (generation != self._generation or chest is None or chest.why != "owner"
                or chest.distance <= OWNER_NEAR_CM):
            return
        social = self.rumors.social if self.rumors is not None else None
        if social is None or not any(
            spot.pos is not None and math.dist(chest.pos, spot.pos) <= ROUTINE_CM
            for spot in social.days.get(npc, ())
        ):
            return
        if not any(item.item == motive["item"] and item.count > 0 for item in chest.items):
            return
        view = await self._view(npc, generation)
        # This is the thief's belief, never a truth query for actual observers. A watcher behind
        # its back can still become an honest engine witness of the subsequent deed.
        if view is None or view.sees or (view.hero is not None and view.hero.seen):
            return
        args = {"chest": chest.id, "what": motive["item"], "count": 1, "deed": True}
        if await self.npc_crimes.check_command(npc, "steal", args):
            return
        if args.get("expected_owner") != owner:
            return
        if generation != self._generation or not self._free(npc) or not self._free(owner):
            return
        if not self.loop_guard.claim(npc):
            return
        action = await self.npc_crimes._act(npc, "steal", **args)
        self._started(motive, "steal", action, generation)

    async def _meet(self, motive: Fact, rule: MotiveRule, generation: int) -> None:
        npc, target, cid = motive["npc"], motive["target"], motive["crime"]
        if not self._free(npc) or not self._free(target) or self._resolved(cid):
            return
        if motive["kind"] == "reclaim" and (
            any(key[1:] == (cid, npc) for key in self.npc_crimes._reclaiming)
            or any(f.get("crime") == cid and f.get("npc") == npc
                   for f in self.chronicle.facts("npc_crime_reaction"))
        ):
            return
        view = await self._view(npc, generation)
        if view is None or not any(o.id.upper() == target and o.distance <= MEET_CM for o in view.sees):
            return
        if not self._free(npc) or not self._free(target) or generation != self._generation:
            return
        act = rule.action(self.cards.get(npc))
        if not self.loop_guard.claim(npc):
            return
        if act == "fight":
            crime = self.npc_crimes.crime(cid)
            if crime is None:
                return
            action = await self.npc_crimes._fight(
                crime, npc, self.npc_crimes._generation, role="reclaim", svm="DIRTYTHIEF",
            )
        else:
            action = await self.npc_crimes._act(
                npc, "say_svm", svm="DIRTYTHIEF", npc=target, overlay=True,
            )
        if generation != self._generation:
            return
        self._started(motive, str(act), action, generation)
        if act == "accuse":
            self._accused(motive)

    def _started(self, motive: Fact, act: str, action: str, generation: int) -> None:
        if generation != self._generation:
            return
        if action not in self.npc_crimes._completed:
            self._active[motive["npc"]] = action
        self.chronicle.record({
            "fact": "npc_will", "npc": motive["npc"], "kind": motive["kind"],
            "target": motive["target"], "act": act, "action": action, "motive": motive["id"],
            **({"crime": motive["crime"]} if motive.get("crime") else {}), **self._stamp(),
        })

    def _name(self, npc: str) -> str:
        entry = self.roster.get(npc)
        return entry.name if entry is not None else npc

    def _accused(self, motive: Fact) -> None:
        npc, target = motive["npc"], motive["target"]
        witnesses = {npc}
        for observer, view in self.views.items():
            if self.clock() - self._view_at.get(observer, -math.inf) > VIEW_SECONDS:
                continue
            if any(o.id.upper() == npc and o.distance <= MEET_CM for o in view.sees):
                witnesses.add(observer)
        witnesses.discard(target)
        self.chronicle.record({
            "fact": "npc_accused", "id": motive["id"], "npc": npc, "name": self._name(npc),
            "target": target, "target_name": self._name(target), "kind": motive["kind"],
            "crime": motive["crime"], "witnesses": sorted(witnesses),
            "witness_names": {who: self._name(who) for who in witnesses}, **self._stamp(),
        })
        branch = self.chronicle.branch
        if self.memory is None or branch is None:
            return
        name = self._name(target)
        note = (f"Ты думаешь, что {name} украл твою вещь, и обвинил его; доказательств нет."
                if motive["kind"] == "suspect" else f"Ты обвинил {name} в краже своей вещи.")
        seq = self.memory.append(branch, npc, "system", note)
        task = asyncio.create_task(self.link.send(MemoryCmd(branch=branch, seq=seq)))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
