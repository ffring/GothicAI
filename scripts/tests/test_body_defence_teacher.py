import copy
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from body_defence_teacher import select_certificate, verify_outcome, verify_precondition
import pytest
import body_defence_demonstrations as collector
from body_defence_demonstrations import declared_cases


def observation():
    return {"entity_id": 3, "session_epoch": 1, "world_epoch": 2, "control_generation": 7,
            "observation_seq": 12, "t_game_ms": 1600, "periodic": True, "event_only": False,
            "phase": "post_simulation_post_animation", "can_decide": True, "action_mask_valid": True,
            "action_mask": [False]*9+[True, False, False],
            "self": {"hp": 300, "hp_max": 300, "down": False, "attack_anim": False,
                     "defence_active": False, "in_air": False},
            "target": {"entity_id": 4, "hp": 300, "hp_max": 300, "down": False,
                       **{key: True for key in ("present", "visible", "current_valid", "in_w_range", "melee_line_clear",
                                               "focus_30", "attack_anim", "prehit")}}}


def outcome_evidence():
    return {"executions": [{"phase": "accepted", "t_game_ms": 1600},
                            {"phase": "started", "t_game_ms": 1633, "effective_primitive": 9},
                            {"phase": "completed", "t_game_ms": 2233, "effective_primitive": 9}],
            "mechanics": [{"phase": "arena_actor_mapping", "observation_entity_id": 3, "actor_id": 30},
                          {"phase": "arena_actor_mapping", "observation_entity_id": 4, "actor_id": 40},
                          {"phase": "hit_attempt", "actor_id": 40, "target_id": 30, "t_game": 1766, "seq": 20, "result": "applied"},
                          {"phase": "defence_result", "actor_id": 30, "target_id": 40, "t_game": 1766, "seq": 21,
                           "damage_kind": "melee", "blocked": True}]}


def test_precondition_requires_visible_real_windup_and_legal_block():
    row = observation()
    cert = select_certificate(row, command_id=1, wall_monotonic=1.0)
    assert cert is not None and verify_precondition(cert, row, [])["passed"]
    for section, key, value in (("target", "prehit", False), ("target", "visible", False),
                                ("target", "in_w_range", False), ("self", "hp", 230)):
        changed = copy.deepcopy(row)
        changed[section][key] = value
        assert select_certificate(changed, command_id=1, wall_monotonic=1.0) is None
    row["action_mask"][9] = False
    assert select_certificate(row, command_id=1, wall_monotonic=1.0) is None


def test_certificate_is_causal_first_prepared_command():
    row = observation()
    cert = select_certificate(row, command_id=1, wall_monotonic=1.0)
    cert["context"]["prepared_game_ms"] -= 33
    assert not verify_precondition(cert, row, [])["passed"]
    cert = select_certificate(row, command_id=1, wall_monotonic=1.0)
    assert not verify_precondition(cert, row, [{"phase": "completed", "command_id": 1}])["passed"]
    row["periodic"] = False
    assert select_certificate(row, command_id=1, wall_monotonic=1.0) is None


def test_actual_applied_delivery_plus_later_native_block_passes():
    before, after = observation(), observation()
    after["t_game_ms"] = 2266
    proof = verify_outcome(9, before, after, outcome_evidence())
    assert proof["passed"] and proof["proof"]["delivered_hits"] == 1


def test_completed_animation_without_real_hit_is_not_expert():
    evidence = outcome_evidence()
    evidence["mechanics"] = evidence["mechanics"][:2]
    assert not verify_outcome(9, observation(), observation(), evidence)["passed"]


def test_defence_pair_requires_same_time_role_and_later_unique_sequence():
    for key, value in (("t_game", 1799), ("actor_id", 40), ("seq", 19), ("blocked", False), ("damage_kind", "ranged")):
        evidence = outcome_evidence()
        evidence["mechanics"][-1][key] = value
        assert not verify_outcome(9, observation(), observation(), evidence)["passed"]
    evidence = outcome_evidence()
    evidence["mechanics"].append({**evidence["mechanics"][-2], "seq": 22})
    assert not verify_outcome(9, observation(), observation(), evidence)["passed"]


def test_damage_cancel_and_terminal_censor_fail_closed():
    evidence = outcome_evidence()
    evidence["mechanics"].append({"phase": "damage", "actor_id": 30, "target_id": 40, "t_game": 1766,
                                  "hp_before": 300, "hp_after": 230, "damage_kind": "melee"})
    assert not verify_outcome(9, observation(), observation(), evidence)["passed"]
    evidence = outcome_evidence()
    evidence["executions"][-1]["phase"] = "cancelled"
    assert not verify_outcome(9, observation(), observation(), evidence)["passed"]
    assert not verify_outcome(5, observation(), observation(), outcome_evidence())["passed"]


def test_declared_matrix_keeps_known_group_splits_and_real_distances():
    config = json.loads((Path(__file__).resolve().parents[2]/"evals/npc-body/body-defence-demonstrations-v1.json").read_text())
    cases = declared_cases(config)
    assert len(cases) == config["case_count"] == 72
    assert len(declared_cases(config, smoke=True)) == config["smoke_case_count"] == 6
    assert {case["distance_cm"] for case in cases} == {140, 150, 180}
    assert {case["side"] for case in cases} == {0, 1}
    assert all(case["task"] == "reset_stress" and case["timeout_ms"] == 2000 for case in cases)
    for case in cases:
        key = json.dumps([config["setup_family"], case["seed"]], separators=(",", ":"))
        assert config["group_assignments"][key] == case["split"]


def test_prepared_binding_preserves_encodings_and_exact_model_float32(monkeypatch):
    native = {"entity_id": 1, "self": {"yaw_rad": 1.}, "action_mask": [True]*12}
    wire = copy.deepcopy(native)
    wire["self"]["yaw_rad"] = math.nextafter(1., 2.)
    monkeypatch.setattr(collector.feature_module, "pack_features",
                        lambda row, normalizer, **kwargs: [row["self"]["yaw_rad"]]+[0.]*183)
    proof = collector.prepared_anchor_binding({"raw_capture_seq": 5, "observation": native}, wire, {})
    assert native != wire and proof["original_wire_and_capture_preserved"]
    assert proof["relation"]["observed_max_ulps"] == 1
    assert proof["capture_float32_features_sha256"] == proof["wire_float32_features_sha256"]
    assert proof["capture_anchor_sha256"] != proof["wire_anchor_sha256"]


@pytest.mark.parametrize("failure", ["three_ulps", "identity", "mask", "type", "missing_key"])
def test_prepared_binding_never_admits_larger_or_structural_difference(failure, monkeypatch):
    native = {"entity_id": 1, "self": {"yaw_rad": 1.}, "action_mask": [True]*12}
    wire = copy.deepcopy(native)
    if failure == "three_ulps":
        for _ in range(3):
            wire["self"]["yaw_rad"] = math.nextafter(wire["self"]["yaw_rad"], 2.)
    elif failure == "identity":
        wire["entity_id"] = 2
    elif failure == "mask":
        wire["action_mask"][9] = False
    elif failure == "type":
        wire["entity_id"] = 1.
    else:
        del wire["self"]
    with pytest.raises(ValueError, match="native/wire"):
        collector.prepared_anchor_binding({"raw_capture_seq": 5, "observation": native}, wire, {})
