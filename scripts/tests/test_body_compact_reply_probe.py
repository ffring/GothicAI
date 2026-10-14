from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from body_compact_reply_probe import compact_expected, full_evidence, same_native_state


def full_reply():
    actor = {"entity_id": 1, "hp": 300, "pose": "S_1HRUN", "action_mask": [True]+[False]*11,
             "purity_state": {"rng_state": "actual recorded RNG", "queue": [0]*6, "fai_wait_until": 1000}}
    control = {"actors": [actor], "world_epoch": 1}
    return {"id": 1, "game_ms": 1000, "sim_step": 1, "native_choices": 0,
            "body_control": control, "body_observation": {"emitted": 2, "queue_size": 2},
            "arena": {"actors": [{"entity_id": 1, "hp": 300}], "initial_state": {"actors": []},
                      "reaction": {"phase": "active", "game_ms": 1000}},
            "graphics_device_created": False, "window_created": False, "audio_device_created": False,
            "body_reply": deepcopy(control), "elapsed_wall_ms": 10, "wall_monotonic_ns": 1234}


def test_compaction_removes_only_repeated_rng_and_reset_snapshot():
    original = full_reply()
    original["observations"] = [{"self": {"hp": 300}, "clock": 1000, "action_mask": [True]*12}]
    snapshot = deepcopy(original)
    compact = compact_expected(original)
    assert original == snapshot
    assert "initial_state" not in compact["arena"]
    assert "rng_state" not in compact["body_control"]["actors"][0]["purity_state"]
    assert compact["observations"] == original["observations"]
    same_native_state(original, compact)


@pytest.mark.parametrize("change", ["hp", "mask", "pose", "clock", "queue", "observer", "geometry"])
def test_unrelated_native_changes_cannot_be_accepted_as_compaction(change):
    original = full_reply()
    compact = compact_expected(original)
    actor = compact["body_control"]["actors"][0]
    if change == "hp":
        actor["hp"] -= 1
    elif change == "mask":
        actor["action_mask"][4] = True
    elif change == "pose":
        actor["pose"] = "T_1HATTACK"
    elif change == "clock":
        compact["game_ms"] += 33
    elif change == "queue":
        actor["purity_state"]["queue"][0] = 6
    elif change == "observer":
        compact["body_observation"]["queue_size"] = 0
    else:
        compact["arena"]["setup"] = {"geometry": "altered"}
    with pytest.raises(RuntimeError):
        same_native_state(original, compact)


@pytest.mark.parametrize("drop", ["rng", "initial"])
def test_held_and_terminal_full_evidence_requires_both_diagnostics(drop):
    reply = full_reply()
    assert full_evidence(reply)
    if drop == "rng":
        del reply["body_control"]["actors"][0]["purity_state"]["rng_state"]
    else:
        del reply["arena"]["initial_state"]
    assert not full_evidence(reply)
