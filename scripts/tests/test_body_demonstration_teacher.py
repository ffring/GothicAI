"""Causal rule and independent effect checks; no engine or model is launched."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_demonstration_teacher import PROVIDER, RULES, TEACHER_ID, select_certificate, verify_outcome, verify_precondition
from body_demonstration_suite import DemonstrationCycle, DemonstrationRunner, declared_cases
from body_arena_env import ArenaRunner


def row():
    return {"entity_id": 1, "session_epoch": 7, "world_epoch": 8, "control_generation": 4,
            "observation_seq": 9, "t_game_ms": 1000, "phase": "post_simulation_post_animation", "periodic": True,
            "can_decide": True, "action_mask_valid": True, "action_mask": [True]*12,
            "self": {"hp": 300, "down": False, "position_world_cm": [0, 0, 0], "yaw_rad": 0},
            "target": {"entity_id": 2, "visible": True, "current_valid": True, "attack_anim": False, "prehit": False,
                       "fight_distance_cm": 450, "g_range_cm": 310, "in_w_range": False, "focus_5": True,
                       "focus_30": True, "melee_line_clear": True, "yaw_error_rad": 0, "hp": 300},
            "local_obstacles": {"rays": [{"hit": False, "distance_cm": 300} for _ in range(16)]}}


def history(action):
    return [{"action_id": action, "command_id": 1, "entity_id": 1, "t_game_ms": 900, "phase": "completed"}]


def certificate(before, action, prior=None):
    past = history(prior) if prior else []
    context = {"goal": "defensive_cycle", "variant_index": 0, "completed_command_ids": [item["command_id"] for item in past],
               "last_completed_action": prior}
    return select_certificate(before, action, context, past, command_id=2, certificate_seq=1, wall_monotonic=10.0)


@pytest.mark.parametrize("field,value", [("periodic", False), ("can_decide", False), ("action_mask_valid", False),
                                         ("phase", "pre_native_primitive")])
def test_teacher_rejects_event_only_locked_or_different_sampling_view(field, value):
    before = row()
    before[field] = value
    assert certificate(before, 1) is None


def test_masked_command_hidden_target_and_future_history_cannot_get_label():
    before = row()
    before["action_mask"][1] = False
    assert certificate(before, 1) is None
    before = row()
    before["target"]["visible"] = False
    assert certificate(before, 1) is None
    before = row()
    cert = certificate(before, 11, prior=5)
    assert cert is not None
    future = history(5)
    future[0]["t_game_ms"] = 1001
    assert verify_precondition(cert, before, future)["passed"] is False


def test_jump_requires_observed_attack_and_past_completed_defense():
    before = row()
    before["target"].update(in_w_range=True, fight_distance_cm=180, attack_anim=True)
    assert certificate(before, 5) is None
    assert certificate(before, 5, prior=9) is not None
    before["target"]["attack_anim"] = False
    assert certificate(before, 5, prior=9) is None


def test_wait300_requires_safe_distance_no_threat_and_real_retreat_history():
    before = row()
    assert certificate(before, 11, prior=5) is not None
    assert certificate(before, 11, prior=9) is None
    before["target"]["attack_anim"] = True
    assert certificate(before, 11, prior=5) is None
    before["target"].update(attack_anim=False, fight_distance_cm=200)
    assert certificate(before, 11, prior=5) is None


def test_near_wall_ray_selects_clear_lateral_side():
    before = row()
    before["local_obstacles"]["rays"][4] = {"hit": True, "distance_cm": 100}
    assert certificate(before, 3) is None
    assert certificate(before, 4) is not None


def test_front_left_right_followups_have_causal_history_and_clear_line():
    before = row()
    before["target"].update(in_w_range=True, fight_distance_cm=180)
    assert certificate(before, 6) is not None
    assert certificate(before, 7) is None
    assert certificate(before, 7, prior=6) is not None
    assert certificate(before, 8, prior=7) is not None
    before["target"]["melee_line_clear"] = False
    assert certificate(before, 6) is None


def test_completed_approach_and_turn_between_strikes_preserve_strike_history():
    before = row()
    before["target"].update(in_w_range=True, fight_distance_cm=180)
    past = [{"action_id": action, "command_id": command, "entity_id": 1, "t_game_ms": 850+command*20, "phase": "completed"}
            for command, action in enumerate((6, 1, 2), 1)]
    context = {"goal": "defensive_cycle", "variant_index": 0, "completed_command_ids": [1, 2, 3], "last_completed_action": 2}
    cert = select_certificate(before, 7, context, past, command_id=4, certificate_seq=4, wall_monotonic=10)
    assert cert is not None
    assert verify_precondition(cert, before, past)["passed"] is True
    past[0]["action_id"] = 8
    assert verify_precondition(cert, before, past)["passed"] is False


def evidence(action, *, mechanics=None, reason=None):
    return {"executions": [{"phase": "accepted", "t_game_ms": 1000},
                           {"phase": "started", "t_game_ms": 1033, "effective_primitive": action, "reason": reason or "segment_started"},
                           {"phase": "completed", "t_game_ms": 1367, "reason": "wait_elapsed" if action in (10, 11) else "segment_finished"}],
            "mechanics": [{"phase": "observation_actor_mapping", "actor_id": 101, "observation_entity_id": 1},
                          {"phase": "observation_actor_mapping", "actor_id": 102, "observation_entity_id": 2}]+(mechanics or [])}


def test_completed_animation_without_actual_damage_is_not_attack_expert():
    before, after = row(), row()
    after["target"]["hp"] = 280
    assert verify_outcome(6, before, after, evidence(6))["passed"] is False
    hits = [{"phase": "hit_attempt", "t_game": 1100, "actor_id": 101, "target_id": 102, "result": "applied"},
            {"phase": "damage", "t_game": 1100, "actor_id": 102, "target_id": 101,
             "damage_kind": "melee", "hp_before": 300, "hp_after": 280}]
    assert verify_outcome(6, before, after, evidence(6, mechanics=hits))["passed"] is True


def test_retreat_requires_real_opponent_attempt_and_verified_miss_or_jump_defense():
    before, after = row(), row()
    before["target"]["fight_distance_cm"] = 180
    after["self"]["position_world_cm"] = [-200, 0, 0]
    attempts = [{"phase": "hit_attempt", "t_game": 1100, "actor_id": 102, "target_id": 101, "result": "out_of_range"}]
    assert verify_outcome(5, before, after, evidence(5))["passed"] is False
    assert verify_outcome(5, before, after, evidence(5, mechanics=attempts))["passed"] is True
    attempts[0]["result"] = "applied"
    assert verify_outcome(5, before, after, evidence(5, mechanics=attempts))["passed"] is False


def test_applied_delivery_with_same_tick_native_jump_defense_is_avoided():
    before, after = row(), row()
    before["target"]["fight_distance_cm"] = 180
    after["self"]["position_world_cm"] = [-123, 0, 0]
    after["target"]["fight_distance_cm"] = 250
    mechanics = [{"phase": "hit_attempt", "seq": 10, "t_game": 1100, "actor_id": 102, "target_id": 101, "result": "applied"},
                 {"phase": "defence_result", "seq": 11, "t_game": 1100, "actor_id": 101, "target_id": 102,
                  "damage_kind": "melee", "blocked": False, "jumped_back": True}]
    proof = verify_outcome(5, before, after, evidence(5, mechanics=mechanics))
    assert proof["passed"] is True
    assert proof["proof"]["jump_defended_attempts"] == 1
    assert proof["proof"]["jump_defended_pairs"] == [{"hit_seq": 10, "defence_seq": 11}]
    assert proof["proof"]["explicit_miss_attempts"] == 0
    assert proof["proof"]["received_melee_damage"] == 0


@pytest.mark.parametrize("changed,value", [("jumped_back", False), ("t_game", 1133), ("actor_id", 999), ("damage_kind", "ranged"),
                                         ("seq", 9), ("seq", 10), ("seq", None)])
def test_applied_delivery_without_matching_native_jump_defense_fails(changed, value):
    before, after = row(), row()
    before["target"]["fight_distance_cm"] = 180
    after["self"]["position_world_cm"] = [-123, 0, 0]
    mechanics = [{"phase": "hit_attempt", "seq": 10, "t_game": 1100, "actor_id": 102, "target_id": 101, "result": "applied"},
                 {"phase": "defence_result", "seq": 11, "t_game": 1100, "actor_id": 101, "target_id": 102,
                  "damage_kind": "melee", "blocked": False, "jumped_back": True}]
    mechanics[1][changed] = value
    assert verify_outcome(5, before, after, evidence(5, mechanics=mechanics))["passed"] is False


def test_two_applied_deliveries_cannot_reuse_one_native_jump_defense():
    before, after = row(), row()
    before["target"]["fight_distance_cm"] = 180
    after["self"]["position_world_cm"] = [-123, 0, 0]
    mechanics = [{"phase": "hit_attempt", "seq": 10, "t_game": 1100, "actor_id": 102, "target_id": 101, "result": "applied"},
                 {"phase": "hit_attempt", "seq": 11, "t_game": 1100, "actor_id": 102, "target_id": 101, "result": "applied"},
                 {"phase": "defence_result", "seq": 12, "t_game": 1100, "actor_id": 101, "target_id": 102,
                  "damage_kind": "melee", "blocked": False, "jumped_back": True}]
    proof = verify_outcome(5, before, after, evidence(5, mechanics=mechanics))
    assert proof["passed"] is False
    assert proof["proof"]["jump_defended_attempts"] == 1 and proof["proof"]["unresolved_attempts"] == 1


def test_jump_proof_rejects_actual_melee_damage_even_if_final_hp_was_restored():
    before, after = row(), row()
    before["target"]["fight_distance_cm"] = 180
    after["self"]["position_world_cm"] = [-123, 0, 0]
    mechanics = [{"phase": "hit_attempt", "seq": 10, "t_game": 1100, "actor_id": 102, "target_id": 101, "result": "applied"},
                 {"phase": "defence_result", "seq": 11, "t_game": 1100, "actor_id": 101, "target_id": 102,
                  "damage_kind": "melee", "blocked": False, "jumped_back": True},
                 {"phase": "damage", "t_game": 1100, "actor_id": 101, "target_id": 102,
                  "damage_kind": "melee", "hp_before": 300, "hp_after": 290}]
    proof = verify_outcome(5, before, after, evidence(5, mechanics=mechanics))
    assert proof["passed"] is False and proof["proof"]["received_melee_damage"] == 1
    after["self"]["hp"] = 290
    assert verify_outcome(5, before, after, evidence(5, mechanics=mechanics))["passed"] is False


def test_wait_proof_rejects_promoted_primitive_movement_or_received_damage():
    before, after = row(), row()
    proof = evidence(11, reason="wait_started")
    assert verify_outcome(11, before, after, proof)["passed"] is True
    proof["executions"][1]["effective_primitive"] = 3
    assert verify_outcome(11, before, after, proof)["passed"] is False
    proof = evidence(11, reason="wait_started")
    after["self"]["position_world_cm"] = [10, 0, 0]
    assert verify_outcome(11, before, after, proof)["passed"] is False


def test_turn_and_strafe_need_true_geometric_change():
    before, after = row(), row()
    before["target"].update(yaw_error_rad=math.radians(20), focus_5=False)
    after["target"]["yaw_error_rad"] = 0
    assert verify_outcome(2, before, after, evidence(2))["passed"] is False
    after["self"]["yaw_rad"] = math.radians(20)
    assert verify_outcome(2, before, after, evidence(2))["passed"] is True
    before, after = row(), row()
    assert verify_outcome(3, before, after, evidence(3))["passed"] is False
    after["self"]["position_world_cm"] = [0, 0, 100]
    assert verify_outcome(3, before, after, evidence(3))["passed"] is True
    assert verify_outcome(4, before, after, evidence(4))["passed"] is False


def test_declared_collection_has_unique_37_cases_and_inherited_splits():
    config = json.loads((Path(__file__).resolve().parents[2]/"evals/npc-body/body-demonstrations-v1.json").read_text())
    cases = declared_cases(config)
    assert len(cases) == 37 and len({case["name"] for case in cases}) == 37
    assert sum(case["split"] == "train" and case["geometry"] == "open" for case in cases) == 24
    assert sum(case["split"] == "eval" and case["geometry"] == "open" for case in cases) == 4
    assert sum(case["split"] == "test" and case["geometry"] == "open" for case in cases) == 4
    assert sum(case["split"] == "ood" for case in cases) == 2
    for case in cases:
        group = json.dumps([config["setup_family"], case["seed"]], separators=(",", ":"))
        assert case["split"] == config["inherited_group_assignments"][group]
    smoke = declared_cases(config, smoke=True)
    assert len(smoke) == 9 and [case["variant_index"] for case in smoke[:4]] == [0, 1, 2, 3]
    assert [case["seed"] for case in smoke[:4]] == [17]*4
    assert [case["geometry"] for case in smoke] == ["open"]*4+["near_wall"]*3+["open"]*2
    assert {case["name"] for case in smoke[4:7]} == {case["name"] for case in config["near_wall_cases"]}
    assert {case["name"] for case in smoke[7:]} == {case["name"] for case in config["ood_cases"]}
    assert config["coverage"]["actions"] == [1, 2, 5, 6, 7, 8, 9, 11]
    assert config["coverage"]["native_complement_actions"] == [3, 4, 10]
    assert config["coverage"]["global_bc_actions"] == list(range(1, 12))
    assert config["coverage"]["global_bc_minimum_train_usable_per_action"] == 20


def test_targeted_cycle_keeps_real_corrections_and_omits_native_complement(monkeypatch):
    cycle = DemonstrationCycle.__new__(DemonstrationCycle)
    cycle.config = {"coverage": {"actions": [1, 2, 5, 6, 7, 8, 9, 11]}}
    cycle.history = []
    cycle.opponent = 2
    cycle.env = type("FinishedEnv", (), {"arena": {"truncated": True, "outcome": "time_limit"}})()
    called = []

    def act(action):
        called.append(action)
        cycle.history.append({"action_id": action})
        return len(called)

    monkeypatch.setattr(cycle, "act", act)
    monkeypatch.setattr(cycle, "ensure_attack_ready", lambda action: act(1) if action in (7, 8) else None)
    monkeypatch.setattr(cycle, "stimulate_attack", lambda action: 100+action)
    monkeypatch.setattr(cycle, "wait_completed", lambda entity, command: None)
    monkeypatch.setattr(cycle, "establish_safe_distance", lambda: None)
    cycle.run()
    assert called == [2, 1, 6, 1, 7, 1, 8, 9, 5, 11]
    assert not {3, 4, 10}.intersection(called)


def test_separate_provider_and_reset_geometry_are_logged_as_actual_request(monkeypatch):
    seen = []
    monkeypatch.setattr(ArenaRunner, "call", lambda self, command: seen.append(command) or {"accepted": True})
    runner = DemonstrationRunner.__new__(DemonstrationRunner)
    runner.reset_extras = {"yaw_offset_deg": 20, "geometry": "near_wall"}
    runner.call({"cmd": "body_acquire", "provider": "stage5-scripted"})
    runner.call({"cmd": "body_action", "provider": "stage5-scripted"})
    runner.call({"cmd": "body_heartbeat", "provider": "stage5-scripted"})
    runner.call({"cmd": "reset", "arena": {"scenario": "reset_stress", "seed": 17}})
    assert all(command["provider"] == PROVIDER for command in seen[:3])
    assert seen[3]["arena"] == {"scenario": "reset_stress", "seed": 17, "yaw_offset_deg": 20, "geometry": "near_wall"}
