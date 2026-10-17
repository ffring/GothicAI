import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from body_visibility_boundary_probe import accepted_schedule, boundary_evidence, compare, physical_prefix


def evidence():
    events = [{"phase": "arena_actor_mapping", "actor_id": 5, "observation_entity_id": 3},
              {"phase": "damage", "actor_id": 5, "t_game": 8567, "hp_before": 2772, "hp_after": 2702, "seq": 20},
              {"phase": "native_attempt", "actor_id": 5, "t_game": 8567, "animation": "T_STUMBLEB",
               "accepted": True, "seq": 21, "layers_after": [{"name": "T_STUMBLEB", "duration_ms": 360}]}]
    observations = [{"entity_id": 3, "t_game_ms": 8567, "observation_seq": 1, "control_generation": 7,
                     "self": {"interruptible": False}, "target": {"visible": False}, "can_decide": False,
                     "control_allowed": False, "action_mask": [False]*12},
                    {"entity_id": 3, "t_game_ms": 8667, "observation_seq": 2, "control_generation": 7,
                     "self": {"interruptible": False}, "target": {"visible": True}}]
    controls = [{"entity_id": 3, "t_game_ms": 8567, "phase": "cancelled", "reason": "hit_reaction",
                 "command_id": 14, "action_id": 4, "event_seq": 19}]
    return events, observations, controls


def test_genuine_native_hit_and_locked_recovery_are_required():
    result = boundary_evidence(*evidence(), 1000, 7567)
    assert result["damage_hp"] == 70 and result["recovered_offset_ms"] == 7667


@pytest.mark.parametrize("mutation", ["damage", "duration", "masked", "release", "recovery", "cancel"])
def test_fabricated_or_unsafe_boundary_evidence_fails(mutation):
    events, observations, controls = copy.deepcopy(evidence())
    if mutation == "damage":
        events[1]["hp_after"] += 1
    elif mutation == "duration":
        events[2]["layers_after"][0]["duration_ms"] = 300
    elif mutation == "masked":
        observations[0]["action_mask"][4] = True
    elif mutation == "release":
        controls.append({"entity_id": 3, "t_game_ms": 8567, "phase": "ownership", "reason": "release_pending"})
    elif mutation == "recovery":
        observations[1]["control_generation"] = 0
    else:
        controls[0]["reason"] = "segment_replaced"
    with pytest.raises(RuntimeError):
        boundary_evidence(events, observations, controls, 1000, 7567)


def test_prefix_retains_damage_pose_clock_and_limits_position_tolerance():
    original = [{"phase": "arena_actor_mapping", "actor_id": 8, "observation_entity_id": 3},
                {"phase": "phase_snapshot", "actor_id": 8, "t_game": 1200, "x": 10.0, "hp": 300,
                 "layers": [{"start": 1100, "clock": 1200, "name": "T_STUMBLEB"}]}]
    prefix = physical_prefix(original, 1000, 200)
    assert prefix[0]["actor_id"] == 3 and prefix[0]["layers"][0]["start"] == 100
    changed = copy.deepcopy(prefix)
    changed[0]["x"] += .9
    compare(changed, prefix)
    changed[0]["hp"] -= 1
    with pytest.raises(RuntimeError):
        compare(changed, prefix)


def test_reference_schedule_rejects_future_issue_time():
    event = {"phase": "accepted", "event_seq": 1, "t_game_ms": 1200, "issued_game_ms": 1201,
             "entity_id": 3, "command_id": 14, "action_id": 4, "ttl_ms": 100}
    with pytest.raises(RuntimeError):
        accepted_schedule([event], 1000)
