"""An npc's day for the behavioral model: what it may do in the next hour.

The menu is built from the npc's own routine (`routine` from the game, protocol v6: entries with time, a
ZS_ state and a waypoint) and its habits (a few lines in the persona). Only activities of the routine it
is in now are offered, never its other routine variants: those belong to the story (a ritual, a death).
A choice becomes `start_activity` for an hour; afterwards the routine goes on by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# what common routine states look like, in the words the model reads (English)
ACTIVITIES = {
    "ZS_STAND_ARMSCROSSED": "stand about with your arms crossed",
    "ZS_STAND_GUARDING": "stand guard",
    "ZS_STAND_EATING": "stand and eat",
    "ZS_STAND_DRINKING": "stand and drink",
    "ZS_STAND_SWEEPING": "sweep the floor",
    "ZS_STAND_WP": "stand at your place",
    "ZS_SIT_BENCH": "sit on a bench",
    "ZS_SIT_CHAIR": "sit on a chair",
    "ZS_SIT_THRONE": "sit on your throne",
    "ZS_SIT_CAMPFIRE": "sit by the campfire",
    "ZS_SIT_GROUND": "sit on the ground",
    "ZS_SLEEP": "sleep in your bed",
    "ZS_SLEEP_GROUND": "sleep on the ground",
    "ZS_SMALLTALK": "chat with the people around",
    "ZS_READ_BOOKSTAND": "read at your bookstand",
    "ZS_POTION_ALCHEMY": "brew potions at the alchemy bench",
    "ZS_PRACTICE_MAGIC": "practise magic",
    "ZS_PRACTICE_SWORD": "practise with your sword",
    "ZS_SMITH_FIRE": "work at the forge",
    "ZS_SMITH_ANVIL": "hammer at the anvil",
    "ZS_SMITH_COOL": "cool a blade",
    "ZS_SMITH_SHARP": "sharpen blades",
    "ZS_COOK_CAULDRON": "cook at the cauldron",
    "ZS_COOK_STOVE": "cook at the stove",
    "ZS_COOK_PAN": "fry something in a pan",
    "ZS_ROAST_SCAVENGER": "roast meat over the fire",
    "ZS_PICK_FIELD": "work in the field",
    "ZS_PICK_ORE": "dig for ore",
    "ZS_RAKE_FIP": "rake the field",
    "ZS_PRAY_INNOS": "pray to Innos",
    "ZS_PRAY_INNOS_FP": "pray to Innos",
    "ZS_PRAY_SLEEPER": "pray to the Sleeper",
    "ZS_PLAY_LUTE": "play the lute",
    "ZS_STOMP_HERB": "stomp herbs",
    "ZS_SAW": "saw wood",
    "ZS_REPAIR_HUT": "repair the hut",
    "ZS_WASH_FP": "wash yourself",
    "ZS_GUARD_PASSAGE": "guard the passage",
}
# states that are not everyday activities: never offered
NOT_ACTIVITIES = ("ZS_DEAD", "ZS_TALK", "ZS_ATTACK", "ZS_FLEE", "ZS_GUIDE", "ZS_FOLLOW", "ZS_UNCONSCIOUS")
KEEP = "keep_routine"


@dataclass(frozen=True)
class Habit:
    """Something the npc likes to do besides its routine (persona): a state at a waypoint (None: where the
    routine has it now)."""

    text: str  # English
    state: str
    waypoint: str | None = None


def describe(state: str) -> str:
    return ACTIVITIES.get(state.upper(), state.upper().removeprefix("ZS_").replace("_", " ").lower())


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def current_entry(entries: list[Any], hour: int, minute: int) -> Any | None:
    now = hour * 60 + minute
    for e in entries:
        start, end = _minutes(e.start), _minutes(e.end)
        if (start <= now < end) if start < end else (now >= start or now < end):
            return e
    return entries[0] if entries else None


def plan_options(
    entries: list[Any], habits: list[Habit], hour: int, minute: int
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """The menu (option id -> text) and what each option does (id -> start_activity args)."""
    now = current_entry(entries, hour, minute)
    here = now.waypoint if now is not None else None
    texts = {
        KEEP: f"keep to your usual routine now: {describe(now.state)}" if now else "keep to your routine"
    }
    args: dict[str, dict[str, Any]] = {}
    seen = {(now.state, now.waypoint)} if now is not None else set()
    for e in entries:
        if (e.state, e.waypoint) in seen or e.state.upper().startswith(NOT_ACTIVITIES):
            continue
        seen.add((e.state, e.waypoint))
        key = f"a{len(args) + 1}"
        texts[key] = f"{describe(e.state)} (you usually do it at {e.start}-{e.end})"
        args[key] = {"state": e.state, "waypoint": e.waypoint, "minutes": 60}
    for h in habits:
        waypoint = h.waypoint or here
        if not waypoint or (h.state, waypoint) in seen:
            continue
        seen.add((h.state, waypoint))
        key = f"h{len(args) + 1}"
        texts[key] = h.text
        args[key] = {"state": h.state, "waypoint": waypoint, "minutes": 60}
    return texts, args
