"""The director turns a real occasion into a plot line, on a save with scripted models."""

from __future__ import annotations

import asyncio
import copy
import math
from collections.abc import AsyncIterator, Collection, Sequence
from typing import TYPE_CHECKING

from .game import GameLink
from .llm import Done, Item, LLMEvent, Message, Tool, ToolCall, ToolChoice
from .llm.fake import FakeBackend
from .protocol import Saved, Session
from .scenarios import AuthoredScript, _logged, _until, wait_message

if TYPE_CHECKING:
    from .showrunner import Showrunner

PREMISE = (
    "После недавней суеты в хозяйстве обнаружилась пропажа небольшой вещи. "
    "Хозяин просит помочь сейчас, пока следы ещё свежи и работа не встала."
)


class ShowrunnerScript:
    """First a forbidden giver, then an eligible one; after the budget's pause, no new line."""

    name = "showrunner-script"

    def __init__(self, giver: str, plot: str) -> None:
        self.giver, self.plot = giver, plot
        self.calls: list[str] = []

    async def aclose(self) -> None:
        pass

    async def stream(
        self,
        *,
        instructions: str,
        messages: Sequence[Item],
        tools: list[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append("\n".join(m.content for m in messages if isinstance(m, Message)))
        n = len(self.calls)
        args = {
            "kind": "quest" if n <= 2 else "none",
            "giver": self.plot if n == 1 else self.giver if n == 2 else "",
            "place": "",
            "new_people": False,
            "premise": PREMISE if n <= 2 else "",
            "reason": "свежая местная пропажа" if n <= 2 else "герою пока хватает поручений",
        }
        call = ToolCall("propose_line", args, f"showrunner{n}")
        yield call
        yield Done("", (call,))


async def showrunner(link: GameLink, director: Showrunner, slot: int = 7) -> dict[str, str]:
    """Arrived → canon retry → quest, early skip, tomorrow's none, then save/load of the budget.

    The running occasion watcher samples the real game. Only the director's wall clock is advanced in
    the scenario, so checking its fifteen-minute pause never requires a real fifteen-minute wait.
    """
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    if not isinstance(director.model.llm, FakeBackend):
        return _logged({"scripted_model": "failed (showrunner requires LLM=fake)"})
    service, author = director.occasions, director.author
    people = await link.story_people()
    context = people[0]["npc"] if people else "PC_HERO"
    nearest = await link.story_eval(context, [["call", "NPC_GETNEARESTWP", ["inst", "HERO"]]])
    start = service.places.of(nearest[0]) if nearest and isinstance(nearest[0], str) else None
    if start is None:
        return _logged({"start": "failed (no place for the hero's nearest waypoint)"})
    plot = next((p["npc"] for p in people if author.rules.plot_npc(p["npc"])), None)
    if plot is None:
        return _logged({"plot_giver": "failed (no plot person for the canon check)"})

    # A safe resident's actual routine supplies the target, as in the occasions scenario. Exclude the
    # loaded hero's place: this must be an arrival somewhere new, rather than its initial sample.
    points = {p["name"].upper() for p in await link.waypoints() if not p.get("fp")}
    homes: dict[str, tuple[str, list[str]]] = {}
    for person in people:
        npc = person["npc"]
        if author.rules.plot_npc(npc) or not npc.startswith(("BAU_", "VLK_", "MIL_")):
            continue
        for entry in person.get("routine", []):
            waypoint = str(entry.get("waypoint", "")).upper()
            place = service.places.of(waypoint)
            if place is None or waypoint not in points:
                continue
            if place.id not in homes:
                homes[place.id] = (waypoint, [])
            homes[place.id][1].append(npc)
    targets = [
        p
        for p in service.places.places
        if p.id in homes and p.id != start.id and p.id not in service.visited and p.center is not None
    ]
    if not targets:
        return _logged({"target": f"failed (no unvisited inhabited place near {start.id})"})
    target = min(targets, key=lambda p: math.dist(start.center or (0, 0, 0), p.center))
    waypoint, residents = homes[target.id]
    for npc in dict.fromkeys(residents):
        await link.debug("skip_important", near=npc)
    await link.debug("player_hp", hp=5000)
    await link.debug("player_goto", waypoint=waypoint)
    settled = await _until(lambda: service.current is not None and service.current.id == target.id, 25)
    if not settled:
        return _logged({"arrived": f"failed (hero did not settle at {target.id}, {waypoint})"})

    occasion = await asyncio.wait_for(service.next(), timeout=20)
    while occasion.get("kind") != "arrived" or occasion.get("place") != target.id:
        occasion = await asyncio.wait_for(service.next(), timeout=20)
    check("arrived", target.id in service.visited, f"{target.id}, {waypoint}")
    brief = await director.brief(occasion)
    giver = None
    for npc in brief.candidates:
        if await author.brief(npc) is not None:
            giver = npc
            break
    check("candidates", giver is not None and plot not in brief.candidates, list(brief.candidates))
    if giver is None:
        return _logged(results)

    script = ShowrunnerScript(giver, plot)
    builder = AuthoredScript()
    director.model.llm, author.model.llm = script, builder
    real_clock = director.clock
    wall = [real_clock()]
    director.clock = lambda: wall[0]
    try:
        before_rejected = len(director.chronicle.facts("line_rejected"))
        line = await director.decide(occasion)
        rejected = director.chronicle.facts("line_rejected")[before_rejected:]
        reasons = [str(why) for fact in rejected for why in fact.get("why", [])]
        check("rejected", len(script.calls) == 2 and bool(reasons), rejected)
        check("retry_reasons", bool(reasons) and all(r in script.calls[1] for r in reasons), reasons)
        check("line", line is not None and line.get("fact") == "line", line)
        if line is None or line.get("fact") != "line":
            return _logged(results)
        check(
            "giver",
            line.get("giver") == giver and giver in brief.candidates and not author.rules.plot_npc(giver),
            giver,
        )
        quests = line.get("quests", [])
        check("book", bool(quests) and all(q in director.book.all() for q in quests), quests)
        check("premise", bool(builder.calls) and f"Замысел режиссёра: {PREMISE}" in builder.calls[0])
        check("open", director.is_open(line))

        calls = len(script.calls)
        skipped = await director.pitch()
        check(
            "early",
            skipped is not None
            and skipped.get("fact") == "line_skipped"
            and len(script.calls) == calls
            and "рано" in str(skipped),
            skipped,
        )

        # Two half-days pass a full game day even when the loaded save is near midnight.
        before = await link.story_state()
        hour, minute = int(before["hour"]), int(before["minute"])
        await link.debug("time", hour=(hour + 12) % 24, minute=minute)
        await link.debug("time", hour=hour, minute=minute)
        after = await link.story_state()
        elapsed = (
            (int(after["day"]) - int(before["day"])) * 24 * 60
            + int(after["hour"]) * 60
            + int(after["minute"])
            - hour * 60
            - minute
        )
        wall[0] += 15 * 60 + 1
        none = await director.pitch()
        check(
            "none",
            elapsed >= 24 * 60
            and len(script.calls) == calls + 1
            and none is not None
            and none.get("fact") == "line_none",
            f"{elapsed} game minutes; {none}",
        )

        at = await link.story_state(vars=["KAPITEL"])
        chapter = int(at.get("vars", {}).get("KAPITEL") or 1)
        lines = copy.deepcopy(director.lines)
        budget = director.budget(chapter, at).text()
        opened = [director.is_open(old) for old in lines]
        await asyncio.sleep(0.5)  # the game's save includes the latest chronicle memory head
        saving = asyncio.create_task(wait_message(link, Saved, timeout=60))
        await asyncio.sleep(0)
        await link.debug("save", slot)
        await saving
        loaded = asyncio.create_task(wait_message(link, Session, lambda m: m.reason == "load"))
        await asyncio.sleep(0)
        await link.debug("load", slot)
        await loaded
        await link.wait_world()
        check(
            "after_load",
            director.lines == lines
            and [director.is_open(old) for old in director.lines] == opened
            and director.budget(chapter, at).text() == budget,
            f"{len(lines)} lines; {budget}",
        )
        check("book_after_load", all(q in director.book.all() for q in quests), quests)
    finally:
        director.clock = real_clock
    return _logged(results)
