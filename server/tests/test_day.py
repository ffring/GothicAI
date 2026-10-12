from gothic_ai.brain.day import KEEP, Habit, current_entry, describe, plan_options
from gothic_ai.protocol import RoutineEntry

FARMER = [
    RoutineEntry(start="06:00", end="12:00", state="ZS_PICK_FIELD", waypoint="FIELD_01"),
    RoutineEntry(start="12:00", end="13:00", state="ZS_STAND_EATING", waypoint="FARM_YARD"),
    RoutineEntry(start="13:00", end="20:00", state="ZS_PICK_FIELD", waypoint="FIELD_01"),
    RoutineEntry(start="20:00", end="23:00", state="ZS_SIT_CAMPFIRE", waypoint="FARM_FIRE"),
    RoutineEntry(start="23:00", end="06:00", state="ZS_SLEEP", waypoint="FARM_BED"),
]


def test_the_current_entry_wraps_around_midnight():
    assert current_entry(FARMER, 9, 30).state == "ZS_PICK_FIELD"
    assert current_entry(FARMER, 2, 0).state == "ZS_SLEEP"
    assert current_entry(FARMER, 23, 0).state == "ZS_SLEEP"


def test_the_menu_is_the_npcs_own_day_and_habits():
    texts, args = plan_options(FARMER, [Habit("play dice by the fire", "ZS_SIT_CAMPFIRE")], 15, 0)
    assert texts[KEEP] == "keep to your usual routine now: work in the field"
    # the other activities of its routine, each once, and its habit (at the current spot)
    assert [a["state"] for a in args.values()] == [
        "ZS_STAND_EATING",
        "ZS_SIT_CAMPFIRE",
        "ZS_SLEEP",
        "ZS_SIT_CAMPFIRE",
    ]
    assert texts["a1"] == "stand and eat (you usually do it at 12:00-13:00)"
    habit = next(k for k in texts if k.startswith("h"))
    assert texts[habit] == "play dice by the fire" and args[habit]["waypoint"] == "FIELD_01"
    assert all(a["minutes"] == 60 for a in args.values())


def test_unknown_states_are_named_from_their_symbol():
    assert describe("ZS_FEED_SHEEP") == "feed sheep"
