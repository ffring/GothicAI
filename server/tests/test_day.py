import json
from pathlib import Path

import pytest

from gothic_ai.brain.day import (
    ACTIVITIES,
    KEEP,
    NOT_OFFERED,
    DayOption,
    Habit,
    asleep_at,
    current_entry,
    describe,
    duration_text,
    own_options,
    plan_ids,
    plan_options,
)
from gothic_ai.places import PlaceBook
from gothic_ai.protocol import RoutineEntry

FARMER = [
    RoutineEntry(start="06:00", end="12:00", state="ZS_PICK_FP", waypoint="FIELD_01"),
    RoutineEntry(start="12:00", end="13:00", state="ZS_STAND_EATING", waypoint="FARM_YARD"),
    RoutineEntry(start="13:00", end="20:00", state="ZS_PICK_FP", waypoint="FIELD_01"),
    RoutineEntry(start="20:00", end="23:00", state="ZS_SIT_CAMPFIRE", waypoint="FARM_FIRE"),
    RoutineEntry(start="23:00", end="06:00", state="ZS_GOTOBED", waypoint="FARM_BED"),
]
DICE = Habit("play dice by the fire", "ZS_SIT_CAMPFIRE")
# Vino's day from the routines' dump: one state at two fields, then the bed
VINO = [
    RoutineEntry(start="08:00", end="12:00", state="ZS_RAKE_FP", waypoint="NW_FARM1_FIELD_04"),
    RoutineEntry(start="12:00", end="15:00", state="ZS_RAKE_FP", waypoint="NW_FARM1_FIELD_05"),
    RoutineEntry(start="15:00", end="18:00", state="ZS_RAKE_FP", waypoint="NW_FARM1_FIELD_04"),
    RoutineEntry(start="18:00", end="22:00", state="ZS_RAKE_FP", waypoint="NW_FARM1_FIELD_05"),
    RoutineEntry(start="22:00", end="08:00", state="ZS_GOTOBED", waypoint="NW_FARM1_INSTABLE_BED"),
]
XARDAS = [
    RoutineEntry(start="08:00", end="23:00", state="ZS_READ_BOOKSTAND", waypoint="NW_XARDAS_TOWER_IN1_28"),
    RoutineEntry(start="23:00", end="08:00", state="ZS_READ_BOOKSTAND", waypoint="NW_XARDAS_TOWER_IN1_28"),
]
BROOD = Habit("stand and brood over the coming dragons, arms crossed", "ZS_STAND_ARMSCROSSED")
# Lutero's day from the dump: every waypoint is in the book
LUTERO = [
    RoutineEntry(start="00:00", end="08:00", state="ZS_GOTOBED", waypoint="NW_CITY_LUTERO_BED"),
    RoutineEntry(start="08:00", end="12:00", state="ZS_SMALLTALK", waypoint="NW_CITY_LUTERO"),
    RoutineEntry(start="12:00", end="15:00", state="ZS_SIT_BENCH", waypoint="NW_CITY_UPTOWN_PATH_27"),
    RoutineEntry(start="15:00", end="18:00", state="ZS_SMALLTALK", waypoint="NW_CITY_LUTERO"),
    RoutineEntry(start="18:00", end="20:00", state="ZS_SIT_BENCH", waypoint="NW_CITY_UPTOWN_PATH_27"),
    RoutineEntry(start="20:00", end="22:00", state="ZS_READ_BOOKSTAND", waypoint="NW_CITY_LUTERO_READ"),
    RoutineEntry(start="22:00", end="00:00", state="ZS_SIT_THRONE", waypoint="NW_CITY_UPTOWN_HUT_04_05"),
]
BOOK = PlaceBook.load()
FARM = {"FIELD_01": "in the field", "FARM_YARD": "in the yard", "FARM_FIRE": "by the fire"}


def where(waypoint: str) -> str | None:
    place = BOOK.of(waypoint) if BOOK is not None else None
    return place.where_en if place is not None else None


def keys(options: list[DayOption]) -> list[str]:
    return [o.key for o in options]


# every state NEWWORLD's routines use (counted from the routines' dump, 370 people)
NEWWORLD_STATES = {
    "ZS_ANNOUNCE_HEROLD", "ZS_COOK_CAULDRON", "ZS_COOK_STOVE", "ZS_DANCE", "ZS_GOTOBED", "ZS_GUARD_HAMMER",
    "ZS_GUARD_PASSAGE", "ZS_PEE", "ZS_PICK_FP", "ZS_PLAY_LUTE", "ZS_POTION_ALCHEMY", "ZS_PRACTICE_SWORD",
    "ZS_PRAY_INNOS", "ZS_PRAY_INNOS_FP", "ZS_PREACH_VATRAS", "ZS_RAKE_FP", "ZS_READ_BOOKSTAND",
    "ZS_REPAIR_HUT", "ZS_SAW", "ZS_SIT_BENCH", "ZS_SIT_CAMPFIRE", "ZS_SIT_CHAIR", "ZS_SIT_THRONE",
    "ZS_SMALLTALK", "ZS_SMITH_ANVIL", "ZS_SMITH_COOL", "ZS_SMITH_FIRE", "ZS_SMITH_SHARP", "ZS_SMOKE_JOINT",
    "ZS_SMOKE_WATERPIPE", "ZS_STAND_ARMSCROSSED", "ZS_STAND_DRINKING", "ZS_STAND_EATING",
    "ZS_STAND_GUARDING", "ZS_STAND_SWEEPING", "ZS_STAND_WP", "ZS_STOMP_HERB", "ZS_STUDY_WP", "ZS_SWEEP_FP",
}  # fmt: skip
DAY_PEOPLE = Path(__file__).parents[2] / "evals" / "npc-decisions" / "day_people.json"


def test_the_current_entry_wraps_around_midnight():
    assert current_entry(FARMER, 9, 30).state == "ZS_PICK_FP"
    assert current_entry(FARMER, 2, 0).state == "ZS_GOTOBED"
    assert current_entry(FARMER, 23, 0).state == "ZS_GOTOBED"


def test_the_old_menu_keeps_its_ids_and_offers_no_sleep():
    texts, args = plan_options(FARMER, [DICE], 15, 0)
    assert texts[KEEP] == "keep to your usual routine now: work in the field"
    # the other activities of its routine, each once, then its habit (at the current spot); not the bed
    assert list(texts) == [KEEP, "a1", "a2", "h3"]
    assert [(a["state"], a["waypoint"]) for a in args.values()] == [
        ("ZS_STAND_EATING", "FARM_YARD"),
        ("ZS_SIT_CAMPFIRE", "FARM_FIRE"),
        ("ZS_SIT_CAMPFIRE", "FIELD_01"),
    ]
    assert texts["a1"] == "stand and eat (you usually do it at 12:00-13:00)"
    assert texts["h3"] == "play dice by the fire"
    assert all(a["minutes"] == 60 for a in args.values())
    assert not any("sleep" in t or "bed" in t for t in texts.values())


def test_asleep_it_is_not_asked():
    assert asleep_at(FARMER, 2, 0) and asleep_at(FARMER, 23, 0) and not asleep_at(FARMER, 15, 0)
    # one option is no question
    assert plan_options(FARMER, [DICE], 2, 0) == (
        {KEEP: "keep to your usual routine now: sleep in your bed"},
        {},
    )
    assert plan_options(FARMER, [DICE], 15, 0, "ZS_SLEEP")[1] == {}  # the scripts have it asleep by day
    assert not asleep_at([], 2, 0)
    (keep,) = own_options(FARMER, [DICE], 2, 0, "", FARM.get)
    assert (keep.key, keep.id, keep.kind, keep.args) == (KEEP, KEEP, "keep", None)
    assert keep.text == "keep to your usual routine: sleep in your bed"  # the bed is not in the book
    assert keys(own_options(FARMER, [DICE], 15, 0, "ZS_SLEEP", FARM.get)) == [KEEP]


def test_the_state_it_is_in_is_not_offered_elsewhere():
    """The engine would only move it to the other field and keep the old end time: no choice at all."""
    assert plan_options(VINO, [], 10, 0, "ZS_RAKE_FP") == (
        {KEEP: "keep to your usual routine now: rake the field"},
        {},
    )
    assert plan_options(VINO, [], 10, 0)[1] == {}  # the roster's state unknown: its entry's
    texts, args = plan_options(FARMER, [DICE], 15, 0, "ZS_SIT_CAMPFIRE")  # by the fire already (a script's)
    assert [a["state"] for a in args.values()] == ["ZS_STAND_EATING"]
    assert keys(own_options(VINO, [], 10, 0, "ZS_RAKE_FP", where)) == [KEEP]
    assert [o.args["state"] for o in own_options(FARMER, [DICE], 15, 0, "ZS_SIT_CAMPFIRE", FARM.get)[1:]] == [
        "ZS_STAND_EATING"
    ]


def test_duty_speeches_and_the_rest_are_never_offered():
    states = ["ZS_SIT_BENCH", *NOT_OFFERED, "ZS_SLEEP_GROUND", "ZS_GUIDE_PLAYER", "ZS_SAW"]
    day = [
        RoutineEntry(start=f"{h:02d}:00", end=f"{h + 1:02d}:00", state=s, waypoint=f"WP_{h}")
        for h, s in enumerate(states)
    ]
    texts, args = plan_options(day, [Habit("guard the gate", "ZS_GUARD_PASSAGE")], 0, 0)
    assert [a["state"] for a in args.values()] == ["ZS_SAW"]
    own = own_options(day, [Habit("guard the gate", "ZS_GUARD_PASSAGE")], 0, 0, "", lambda wp: None)
    assert [o.args["state"] for o in own[1:]] == ["ZS_SAW"]


def test_a_full_waypoint_is_left_out():
    full = ("FARM_FIRE", "FIELD_01")
    texts, args = plan_options(FARMER, [DICE], 15, 0, free=lambda wp: wp not in full)
    assert list(texts) == [KEEP, "a1"] and args["a1"]["waypoint"] == "FARM_YARD"
    own = own_options(FARMER, [DICE], 15, 0, "", FARM.get, free=lambda wp: wp not in full)
    assert [(o.key, o.args["waypoint"]) for o in own[1:]] == [("a1", "FARM_YARD")]


def test_here_only_keeps_the_body_at_its_spot():
    """A plot npc: what it can do where its routine has it now, nothing that walks it away."""
    texts, args = plan_options(XARDAS, [BROOD], 14, 0, "ZS_READ_BOOKSTAND", here_only=True)
    assert list(texts) == [KEEP, "h1"] and texts["h1"] == BROOD.text
    assert args["h1"] == {
        "state": "ZS_STAND_ARMSCROSSED",
        "waypoint": "NW_XARDAS_TOWER_IN1_28",
        "minutes": 60,
    }
    away = Habit("watch the sheep", "ZS_STAND_ARMSCROSSED", "PASTURE")
    yard = [*FARMER, RoutineEntry(start="23:30", end="23:45", state="ZS_STAND_EATING", waypoint="FIELD_01")]
    texts, args = plan_options(yard, [DICE, away], 15, 0, here_only=True)
    assert [(a["state"], a["waypoint"]) for a in args.values()] == [
        ("ZS_STAND_EATING", "FIELD_01"),
        ("ZS_SIT_CAMPFIRE", "FIELD_01"),
    ]
    assert list(texts) == [KEEP, "a1", "h2"]
    # the do menu: the same, with its stable ids, and where it is
    keep, brood = own_options(XARDAS, [BROOD], 14, 0, "ZS_READ_BOOKSTAND", where, here_only=True)
    assert keep.text == "keep to your usual routine: read at your bookstand in Xardas's tower"
    assert (brood.key, brood.id, brood.kind) == (
        "h1",
        "habit:ZS_STAND_ARMSCROSSED@NW_XARDAS_TOWER_IN1_28",
        "habit",
    )
    assert brood.text == f"{BROOD.text} in Xardas's tower (about an hour)"
    assert brood.args == {
        "state": "ZS_STAND_ARMSCROSSED",
        "waypoint": "NW_XARDAS_TOWER_IN1_28",
        "minutes": 60,
    }
    assert not brood.trip and brood.concern is None and brood.partner is None
    assert keys(own_options(yard, [DICE, away], 15, 0, "", FARM.get, here_only=True)) == [KEEP, "a1", "h2"]


def test_the_do_menu_says_where_and_for_how_long():
    """Lutero on his bench at 13:00: his reading and his evening chair, in the book's words; the talk at
    home is not offered (a smalltalk without a partner is a silent stand), nor the bed."""
    assert BOOK is not None
    keep, read, chair = own_options(LUTERO, [], 13, 0, "ZS_SIT_BENCH", where)
    assert keep.text == "keep to your usual routine: sit on a bench on the Upper Quarter streets"
    assert read.text == "read at your bookstand in Lutero's house (about an hour; usually 20:00-22:00)"
    assert chair.text == (
        "sit on your throne in the Upper Quarter houses near Lutero's shop"
        " (about an hour; usually 22:00-00:00)"
    )
    assert (read.key, read.id, read.kind) == (
        "a1",
        "routine:ZS_READ_BOOKSTAND@NW_CITY_LUTERO_READ",
        "routine",
    )
    assert read.args == {"state": "ZS_READ_BOOKSTAND", "waypoint": "NW_CITY_LUTERO_READ", "minutes": 60}
    # a waypoint the book does not know: the activity alone
    (_, odd) = own_options(FARMER[:2], [], 7, 0, "", lambda wp: None)
    assert odd.text == "stand and eat (about an hour; usually 12:00-13:00)"


def test_do_keys_follow_the_soonest_start_and_ids_stay():
    """The do menu lists what starts soonest first (a1, a2), habits after (h<n> goes on counting); the old
    menu keeps the routine's order. A key is only a position: the stable id names the option."""
    own = own_options(FARMER, [DICE], 15, 0, "", FARM.get)
    assert [(o.key, o.id) for o in own] == [
        (KEEP, KEEP),
        ("a1", "routine:ZS_SIT_CAMPFIRE@FARM_FIRE"),  # at 20:00
        ("a2", "routine:ZS_STAND_EATING@FARM_YARD"),  # at 12:00, tomorrow
        ("h3", "habit:ZS_SIT_CAMPFIRE@FIELD_01"),
    ]
    assert own[1].text == "sit by the campfire by the fire (about an hour; usually 20:00-23:00)"
    assert own[3].text == "play dice by the fire in the field (about an hour)"
    assert all(o.args["minutes"] == 60 and not o.trip for o in own[1:])
    texts, args = plan_options(FARMER, [DICE], 15, 0)
    assert plan_ids(args) == {
        "a1": "routine:ZS_STAND_EATING@FARM_YARD",
        "a2": "routine:ZS_SIT_CAMPFIRE@FARM_FIRE",
        "h3": "habit:ZS_SIT_CAMPFIRE@FIELD_01",
    }
    assert {o.id for o in own[1:]} == set(plan_ids(args).values())  # the same choices, by id
    # an hour later the same options keep their ids, whatever their keys
    later = own_options(FARMER, [DICE], 21, 0, "ZS_SIT_CAMPFIRE", FARM.get)
    assert [o.id for o in later[1:]] == ["routine:ZS_PICK_FP@FIELD_01", "routine:ZS_STAND_EATING@FARM_YARD"]


@pytest.mark.parametrize(
    ("minutes", "text"),
    [
        (30, "about half an hour"),
        (44, "about half an hour"),
        (45, "about an hour"),
        (61, "about an hour"),
        (89, "about an hour"),
        (90, "about two hours"),
    ],
)
def test_duration_text(minutes: int, text: str):
    assert duration_text(minutes) == text


def test_every_routine_state_has_its_words():
    # the probe's people (evals/npc-decisions): the dump of the world's routines, committed with the probe
    people = json.loads(DAY_PEOPLE.read_text())
    states = {e["state"].upper() for p in people for e in p.get("routine", [])}
    assert len(people) == 370 and states == set(NEWWORLD_STATES)
    assert sorted(s for s in states if s not in ACTIVITIES) == []
    assert describe("ZS_PICK_FP") == "work in the field" and describe("zs_rake_fp") == "rake the field"


def test_unknown_states_are_named_from_their_symbol():
    assert describe("ZS_FEED_SHEEP") == "feed sheep"
