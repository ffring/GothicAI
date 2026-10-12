"""Who knows whom, and the rumors about the hero they pass on (docs/story-graph.md, stage 5).

Two people know each other in two ways:
- they meet: by their daily routines (`story_people`) they are within MEET_CM of each other at the same
  hours, every day — the farm's people, the barracks, the tavern's regulars;
- they have dealings: they share a quest of the scripts (its variable is read or written in both their
  dialogs) — Andre, who sends the hero to Lobart's field, and Lobart. A quest with more than DEAL_MAX people
  in it makes no acquaintances: those are the chapters and the big lines.
Outlaws (bandits...) know only each other: Maleth's quest to kill a bandit is no acquaintance.

A rumor that the hero lied to someone starts with the people around when it came out (quests.unmask,
quests.caught). Then, as the game's time runs, whoever knows it tells whom it meets — at their meeting
hours, a day's meeting passing it P_MEET of the time — or whom it has dealings with, once a day with
some traveller, P_DEAL of the time; to TELLS_PER_DAY people a day at most, while it is news (FRESH_DAYS).
It goes HOPS retellings away from the one lied to: a lie seen through on the spot is talked about nearby,
one the hero took a reward for reaches further. The chances are
drawn from a hash of who, what and which day, so a run repeats. When the game's time jumps (a sleep, the
clock set), the meetings in between are gone through in order, so a rumor travels a chain in one night.

Each retelling is a `rumor` fact and a remark in the listener's memory; trust.py puts it in the situation.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import logging
import math
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .chronicle import Chronicle, Fact
from .game import GameError, GameLink
from .gamedata.storygraph import StoryGraph, walk
from .protocol import GameMessage, Session

if TYPE_CHECKING:
    from .dialogue import DialogueManager

log = logging.getLogger(__name__)

MEET_CM = 2500  # people at their daily spots this close, at the same hours, meet
DEAL_MAX = 6  # a quest with more people in it makes no acquaintances
OUTLAWS = frozenset({9, 10, 11})  # bandits, convicts, seekers: they have no dealings with the rest
# retellings away from the one lied to: a lie seen through on the spot is talked about nearby, one he took
# a reward for goes further
HOPS = {"caught": 2, "found_out": 3, "broken": 2}  # broken: a debt not paid back by its day (deals.py)
TELLS_PER_DAY = 3  # one tells a rumor to this many a day at most: it goes round in chains, not in a flood
FRESH_DAYS = 2  # and only while it is news: the day one heard it and the next
P_MEET = 0.7  # how often a day's meeting passes a rumor on
P_DEAL = 0.5  # how often people with dealings hear it, once a day
DEAL_FROM, DEAL_TO = 8 * 60, 20 * 60  # the hours news comes with some traveller
TICK_S = 3.0
DAY = 24 * 60


@dataclass
class Tie:
    """How two people know each other."""

    meets: list[tuple[int, int]] = field(default_factory=list)  # daily, minutes of the day, end > start
    deals: list[str] = field(default_factory=list)  # quests they share

    def occasions(self, a: str, b: str, after: int, until: int) -> Iterator[int]:
        """The game minutes in [after, until] they talk: their meetings (from when one already goes on),
        and once a day news through their dealings."""
        for day in range(after // DAY, until // DAY + 1):
            base = day * DAY
            for s, e in self.meets:
                if base + e > after and base + s <= until:
                    yield max(base + s, after)
            if self.deals:
                at = (
                    base + DEAL_FROM + int(_chance("deal", *sorted((a, b)), str(day)) * (DEAL_TO - DEAL_FROM))
                )
                if after <= at <= until:
                    yield at


class Social:
    """The acquaintances of the world's people: whom each meets, and when; whom each has dealings with."""

    def __init__(self, people: list[dict[str, Any]], graph: StoryGraph | None = None) -> None:
        self.names = {p["npc"]: p.get("name") or p["npc"] for p in people}
        self.guilds = {p["npc"]: int(p.get("guild", 0)) for p in people}
        self.ties: dict[str, dict[str, Tie]] = defaultdict(dict)
        self._meetings(people)
        if graph is not None:
            self._dealings(graph)

    def tie(self, a: str, b: str) -> Tie | None:
        """Their tie, made if there is none yet; None if they can't be acquainted (an outlaw and the rest)."""
        if (self.guilds.get(a) in OUTLAWS) != (self.guilds.get(b) in OUTLAWS):
            return None
        if b not in self.ties[a]:
            t = Tie()
            self.ties[a][b] = self.ties[b][a] = t
        return self.ties[a][b]

    def _meetings(self, people: list[dict[str, Any]]) -> None:
        spots = [
            (p["npc"], s, e, tuple(r["pos"]))
            for p in people
            for r in p.get("routine", [])
            if r.get("pos")
            for s, e in _span(int(r["start"]), int(r["end"]))
        ]
        cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, (_, _, _, pos) in enumerate(spots):
            cells[(int(pos[0] // MEET_CM), int(pos[2] // MEET_CM))].append(i)
        for (cx, cz), ids in cells.items():
            near = [j for dx in (-1, 0, 1) for dz in (-1, 0, 1) for j in cells.get((cx + dx, cz + dz), [])]
            for i in ids:
                a, s1, e1, p1 = spots[i]
                for j in near:
                    b, s2, e2, p2 = spots[j]
                    if j <= i or a == b or math.dist(p1, p2) > MEET_CM:
                        continue
                    s, e = max(s1, s2), min(e1, e2)
                    if s < e and (tie := self.tie(a, b)) is not None:
                        tie.meets.append((s, e))
        for ties in self.ties.values():
            for tie in ties.values():
                tie.meets = _merged(tie.meets)

    def _dealings(self, graph: StoryGraph) -> None:
        """People whose dialogs read or write the same quest's variable."""
        involved: dict[str, set[str]] = defaultdict(set)
        quests = set(graph.quests)
        for node in graph.nodes.values():
            if node.npc not in self.names:
                continue  # not a person of this world
            exprs = [node.condition, *(e.get("when") for e in node.effects)]
            used = {x[1] for e in exprs for x in walk(e) if x[0] == "var" and x[1] in quests}
            used |= {e["var"] for e in node.effects if e.get("do") == "set" and e.get("var") in quests}
            for q in used:
                involved[q].add(node.npc)
        for q, people in involved.items():
            if 1 < len(people) <= DEAL_MAX:
                for a in people:
                    for b in people:
                        if a < b and (tie := self.tie(a, b)) is not None and q not in tie.deals:
                            tie.deals.append(q)

    def describe(self, npc: str) -> list[str]:
        """Its acquaintances, in words (for the console)."""
        out = []
        for other, t in sorted(self.ties.get(npc.upper(), {}).items()):
            how = [f"{s // 60:02d}:{s % 60:02d}–{e // 60:02d}:{e % 60:02d}" for s, e in sorted(t.meets)]
            how += [f"дела: {q}" for q in t.deals]
            out.append(f"{self.names.get(other, other)} ({other}): {', '.join(how)}")
        return out


@dataclass
class Rumor:
    """That the hero lied to someone, as it goes round: who knows it, how many retellings away."""

    quest: str
    kind: str  # found_out: he took a reward for it; caught: he tried to
    about: str  # the one lied to
    about_name: str
    title: str
    known: dict[str, int] = field(default_factory=dict)  # npc -> retellings away from `about`
    since: dict[str, int] = field(default_factory=dict)  # npc -> the game day it heard it (if known)
    silent: set[str] = field(default_factory=set)  # promised the hero in a deal to tell nobody (deals.py)

    @property
    def hops(self) -> int:
        return HOPS.get(self.kind, 2)

    def tells(self, npc: str, day: int) -> bool:
        """It still passes the rumor on that day: not too far from the source, and it is still news."""
        heard = self.since.get(npc)
        if npc in self.silent:
            return False
        return self.known.get(npc, self.hops) < self.hops and (heard is None or day < heard + FRESH_DAYS)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.about}:{self.quest}"

    def told(self, teller: str) -> str:
        """The remark for the one it is told to (names stay as they are: nothing to decline)."""
        if self.kind == "broken":
            return (
                f"Слух (со слов: {teller}): герой задолжал человеку по имени {self.about_name} и не вернул "
                f"в срок — {self.title}"
            )
        what = (
            f"взял награду за «{self.title}», а дела не сделал"
            if self.kind == "found_out"
            else f"уверял, что сделал «{self.title}», а это неправда"
        )
        return f"Слух (со слов: {teller}): герой обманул человека по имени {self.about_name} — {what}"


class Rumors:
    """Rumors about the hero's lies going round the people that know each other, by the game's clock."""

    def __init__(
        self,
        link: GameLink,
        dialogue: DialogueManager,
        chronicle: Chronicle,
        graph: StoryGraph | None = None,
    ) -> None:
        self.link = link
        self.dialogue = dialogue
        self.chronicle = chronicle
        self.graph = graph
        self.social: Social | None = None
        self.rumors: dict[str, Rumor] = {}
        self.dead: set[str] = set()
        self.tells: dict[tuple[str, str, int], int] = defaultdict(int)  # (rumor, teller, day) -> told
        self.clock: int | None = None  # the game minute gone through
        chronicle.on_fact.append(self._on_fact)
        link.listeners.append(self._on_message)
        self._load()

    async def run(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            await self.link.wait_world()  # the game answers in the world's tick
            try:
                await self.tick()
            except (GameError, TimeoutError) as err:
                log.info("rumors: %s", err or type(err).__name__)

    async def tick(self) -> None:
        """Who met whom since the last tick, and what they told each other."""
        if self.social is None:
            self.social = Social(await self.link.story_people(), self.graph)
            log.info("rumors: %d people, %d ties", len(self.social.names), _count(self.social))
        state = await self.link.story_state()
        now = int(state.get("day", 0)) * DAY + int(state.get("hour", 0)) * 60 + int(state.get("minute", 0))
        if self.clock is not None and now > self.clock:
            self.spread(self.clock, now)
        self.clock = now

    def spread(self, after: int, until: int) -> int:
        """The meetings in (after, until], in order: each passes on what one knows and the other does not.
        How many retellings there were."""
        social = self.social
        if social is None or not self.rumors:
            return 0
        heap: list[tuple[int, str, str]] = []

        def meetings(npc: str, since: int) -> None:
            for other, tie in social.ties.get(npc, {}).items():
                for t in tie.occasions(npc, other, since, until):
                    heapq.heappush(heap, (t, npc, other))

        knowers = {n for r in self.rumors.values() for n in r.known if r.tells(n, after // DAY)}
        for n in sorted(knowers):
            meetings(n, after + 1)
        told = 0
        while heap:
            t, a, b = heapq.heappop(heap)
            if a in self.dead or b in self.dead:
                continue
            tie = social.ties[a][b]
            day = t // DAY
            for r in self.rumors.values():
                if a not in r.known or b in r.known or not r.tells(a, day):
                    continue
                if self.tells[(r.key, a, day)] >= TELLS_PER_DAY:
                    continue
                meet = any(s <= t % DAY < e for s, e in tie.meets)
                p = P_MEET if meet else P_DEAL
                if _chance(r.key, a, b, str(day)) >= p:
                    continue
                self._learn(r, b, a, t, "met" if meet else "dealings")
                told += 1
                if r.tells(b, day):
                    meetings(b, t)
        return told

    def _learn(self, r: Rumor, listener: str, teller: str, at: int, how: str) -> None:
        assert self.social is not None
        r.known[listener] = r.known[teller] + 1
        r.since[listener] = at // DAY
        self.tells[(r.key, teller, at // DAY)] += 1
        teller_name = self.social.names.get(teller, teller)
        name = self.social.names.get(listener, listener)
        self.chronicle.record(
            {
                "fact": "rumor",
                "quest": r.quest,
                "kind": r.kind,
                "about": r.about,
                "about_name": r.about_name,
                "title": r.title,
                "from": teller,
                "from_name": teller_name,
                "to": listener,
                "hops": r.known[listener],
                "how": how,
                "day": at // DAY,
            }
        )
        self.dialogue.remember(listener, name, r.told(teller_name))

    # --- the save branch --------------------------------------------------------------------

    def _load(self) -> None:
        self.rumors.clear()
        self.dead.clear()
        self.tells.clear()
        for f in self.chronicle.facts():
            self._on_fact(f)

    def _on_fact(self, f: Fact) -> None:
        match f.get("fact"):
            case "found_out" | "caught":
                r = Rumor(
                    f.get("quest", ""),
                    f["fact"],
                    f.get("npc", ""),
                    f.get("name") or f.get("npc", ""),
                    f.get("title") or f.get("quest", ""),
                )
                r.known[r.about] = 0
                if "day" in f:
                    r.since[r.about] = int(f["day"])
                self.rumors.setdefault(r.key, r)
            case "rumor":
                about = f.get("about") or f.get("from", "")
                for r in self.rumors.values():
                    if r.quest == f.get("quest") and r.about == about and f.get("kind", r.kind) == r.kind:
                        if f.get("to", "") not in r.known and "day" in f:
                            self.tells[(r.key, f.get("from", ""), int(f["day"]))] += 1
                        r.known.setdefault(f.get("to", ""), int(f.get("hops", 1)))
                        if "day" in f:
                            r.since.setdefault(f.get("to", ""), int(f["day"]))
                        if not r.about_name or r.about_name == about:
                            r.about_name = f.get("about_name") or f.get("name") or about
            case "broken":
                r = Rumor(
                    f.get("debt", ""), "broken", f.get("npc", ""), f.get("name") or "", f.get("what", "")
                )
                r.known[r.about] = 0
                if "day" in f:
                    r.since[r.about] = int(f["day"])
                self.rumors.setdefault(r.key, r)
            case "deal" if f.get("silence"):
                for key in f["silence"]:
                    if key in self.rumors:
                        self.rumors[key].silent.add(f.get("npc", ""))
            case "died":
                self.dead.add(f.get("npc", ""))

    def _on_message(self, msg: GameMessage) -> None:
        if isinstance(msg, Session):
            self._load()
            self.social = None  # the people of the new world
            self.clock = None

    def describe(self) -> list[str]:
        """The rumors and who knows them (for the console)."""
        names = self.social.names if self.social else {}
        out = []
        for r in self.rumors.values():
            by_hops: dict[int, list[str]] = defaultdict(list)
            for n, h in r.known.items():
                by_hops[h].append(names.get(n, n))
            spread = "; ".join(f"{h}: {', '.join(sorted(ns))}" for h, ns in sorted(by_hops.items()))
            out.append(f"{r.kind} {r.about_name} «{r.title}» — {spread}")
        return out


def _span(start: int, end: int) -> list[tuple[int, int]]:
    """A routine entry as intervals of one day (22:00–08:00 is two)."""
    if start < end:
        return [(start, end)]
    if start == end:
        return [(0, DAY)]
    return [(start, DAY), (0, end)]


def _merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Spans of a day, overlapping or touching ones as one."""
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _chance(*parts: str) -> float:
    """A number in [0, 1) that is the same for the same parts."""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


def _count(social: Social) -> int:
    return sum(len(t) for t in social.ties.values()) // 2
