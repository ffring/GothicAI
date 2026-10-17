"""The training packer equals the gate's reference packer bit for bit, and fails where it fails."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import body_fast_features as fast  # noqa: E402
from body_trajectory_dataset import (FEATURE_VERSION, PERIODIC_FEATURE_VERSION, DatasetValidationError,  # noqa: E402
                                     canonical_feature_names, pack_features)


def row(**changes):
    value = {"schema_version": 1, "session_epoch": 7, "world_epoch": 9, "control_generation": 3, "entity_id": 11,
             "observation_seq": 41, "t_game_ms": 16827, "dt_game_ms": 33, "sim_step": 510, "periodic": True,
             "phase": "post_simulation_post_animation", "can_decide": True, "native_decision_eligible": True,
             "action_mask_valid": True, "action_mask": [True]*12, "availability_mask": [True]*12,
             "mask_kind": "shared_melee_resolver",
             "self": {"hp_fraction": 0.7666666666666667, "weapon_state": 3, "weapon_length_cm": 90, "skill": 1,
                      "skill_talent": 2, "skill_valid": True, "dead": False, "down": False, "attack_anim": True,
                      "prehit": False, "interruptible": True, "velocity_valid": True,
                      "linear_velocity_cm_s": [12.5, -0.0, 301.25],
                      "gates": {"fai_wait_remaining_ms": 0, "ani_wait_remaining_ms": 233}},
             "target": {"present": True, "visible": True, "current_valid": True, "yaw_error_rad": -0.1234567890123,
                        "fight_distance_cm": 187.33, "relative_position_cm": [150.0, 1.5, -2.25],
                        "relative_velocity_cm_s": None, "hp_fraction": 0.3, "last_known_age_ms": 0,
                        "last_seen_game_ms": 16800},
             "goal": {"kind": "combat", "move_hint": 2},
             "local_obstacles": {"rays": [{"distance_cm": 50.0*index, "hit": index % 3 == 0} for index in range(16)]},
             "events": [{"source_game_ms": 16800, "source_sim_step": 509}],
             "last_result": {"valid": True, "source_game_ms": 16794, "source_sim_step": 508}}
    value.update(changes)
    return value


def normalizer(version=PERIODIC_FEATURE_VERSION):
    names = canonical_feature_names(version)
    return {"feature_version": version, "feature_names": names,
            "mean": {name: (index % 7)*0.1-0.2 for index, name in enumerate(names)},
            "std": {name: 1.0+(index % 5)*0.37 for index, name in enumerate(names)}}


def bits(values):
    return struct.pack(f"{len(values)}d", *values)


@pytest.mark.parametrize("version", [PERIODIC_FEATURE_VERSION, FEATURE_VERSION])
@pytest.mark.parametrize("variant", ["full", "hidden_target", "no_rays", "idle_goal", "locked"])
def test_fast_packer_equals_reference_bit_for_bit(version, variant):
    observation = row()
    if variant == "hidden_target":
        observation["target"] = {"present": True, "visible": False, "current_valid": False, "last_known_age_ms": 400}
    elif variant == "no_rays":
        observation["local_obstacles"] = {"rays": []}
    elif variant == "idle_goal":
        observation["goal"] = {"kind": "idle"}
    elif variant == "locked":
        observation.update(can_decide=False, action_mask_valid=False, action_mask=None, availability_mask=[False]*12)
    norm = normalizer(version)
    for dt in (0, 33, 67):
        expected = pack_features(observation, norm, sequence_dt_game_ms=dt)
        assert bits(fast.pack_features(observation, norm, sequence_dt_game_ms=dt)) == bits(expected)


@pytest.mark.parametrize("mutate", [
    lambda value: value["self"].__setitem__("hp_fraction", float("nan")),
    lambda value: value["self"].__setitem__("skill", "2"),
    lambda value: value["target"].__setitem__("relative_position_cm", [1, 2]),
    lambda value: value["local_obstacles"].__setitem__("rays", [{}]*3),
    lambda value: value.__setitem__("phase", "pre_native_primitive"),
    lambda value: value.__setitem__("availability_mask", [True]*11),
    lambda value: value.__setitem__("oracle", {}),
    lambda value: value["target"].__setitem__("last_seen_game_ms", 99999),
    lambda value: value.update(target={"current_valid": False, "fight_distance_cm": 10.0}),
])
def test_fast_packer_rejects_what_the_reference_rejects(mutate):
    observation = row()
    mutate(observation)
    norm = normalizer()
    with pytest.raises(DatasetValidationError):
        pack_features(observation, norm, sequence_dt_game_ms=33)
    with pytest.raises(DatasetValidationError):
        fast.pack_features(observation, norm, sequence_dt_game_ms=33)


def test_normalizer_schema_and_scale_checks_are_kept():
    bad = normalizer()
    bad["feature_names"] = bad["feature_names"][1:]
    with pytest.raises(DatasetValidationError):
        fast.pack_features(row(), bad, sequence_dt_game_ms=33)
    zero = deepcopy(normalizer())
    zero["std"]["dt_game_ms"] = 0.0
    with pytest.raises(DatasetValidationError):
        fast.pack_features(row(), zero, sequence_dt_game_ms=33)
