"""Causal selection and source-bound physical proof; no engine or model starts."""
from copy import deepcopy
import math
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_safe_peer_teacher import PROVIDER, choose, motion, observed_guard, select_certificate, verify_outcome, verify_precondition


def observed(action=2):
    distance = 245. if action == 5 else 450.
    yaw = math.radians(20) if action == 2 else 0.
    mask = [True]*12
    return {"entity_id": 1, "session_epoch": 7, "world_epoch": 9, "control_generation": 3,
            "observation_seq": 10, "t_game_ms": 1000, "periodic": True,
            "phase": "post_simulation_post_animation", "can_decide": True,
            "native_decision_eligible": True, "action_mask_valid": True,
            "mask_kind": "shared_melee_resolver", "action_mask": mask,
            "availability_mask": mask.copy(),
            "self": {"hp": 300, "hp_fraction": 1., "down": False, "in_air": False,
                     "swim": False, "dive": False, "velocity_valid": True,
                     "linear_velocity_cm_s": [0., 0., 0.], "yaw_rad": 0.,
                     "position_world_cm": [0., 0., 0.]},
            "target": {"entity_id": 2, "visible": True, "current_valid": True,
                       "down": False, "melee_line_clear": True, "yaw_error_rad": yaw,
                       "fight_distance_cm": distance, "attack_range_cm": 210., "g_range_cm": 310.,
                       "in_w_range": False, "focus_5": action != 2, "focus_30": True,
                       "attack_anim": action == 5, "prehit": False,
                       "relative_position_cm": [distance, 0., 0.],
                       "relative_velocity_cm_s": [0., 0., 50.]},
            "local_obstacles": {"rays": [{"hit": False, "distance_cm": 300.} for _ in range(16)]}}


def certificate(row, action, history=None):
    return select_certificate(row, action, history or [], command_id=7,
                              certificate_seq=1, wall_monotonic=10.)


def physical_segment(action=2):
    """Symbolic native receipts, dense numeric capture and mapped physical events."""
    before = observed(action)
    identity = {key: before[key] for key in ("entity_id", "session_epoch", "world_epoch", "control_generation")}
    decision = {**identity, "accepted": True, "source": "scripted", "action_id": action,
                "decision_id": 4, "execution_id": 500, "command_id": 7,
                "observation_seq": 77, "raw_observation_record_seq": 100,
                "raw_capture_seq": 101, "t_game_ms": 1000}
    times = [1033] if action == 2 else [1033, 1066, 1100, 1133, 1166, 1200, 1233, 1266, 1300, 1333]
    end_time, settle_time = times[-1], times[-1]+33
    common = {**identity, "decision_id": 4, "execution_id": 500,
              "source": "scripted", "action_id": action}
    end_raw = 200+(len(times)-1)*100-1
    executions = [{**common, "phase": "accepted", "raw_capture_seq": 101, "t_game_ms": 1000},
                  {**common, "phase": "started", "raw_capture_seq": 110, "t_game_ms": times[0],
                   "effective_primitive": action, "reason": "wait_started" if action == 11 else "primitive_started"},
                  {**common, "phase": "completed", "raw_capture_seq": end_raw, "t_game_ms": end_time,
                   "reason": "wait_elapsed" if action == 11 else "primitive_completed"}]
    wrappers = [{"entity_id": 1, "observation_seq": 77, "raw_capture_seq": 100, "observation": deepcopy(before)}]
    for index, game in enumerate(times+[settle_time]):
        row = deepcopy(before)
        row.update(observation_seq=11+index, t_game_ms=game, can_decide=game == settle_time)
        row["target"].update(focus_5=True, yaw_error_rad=0.)
        if action == 2:
            row["self"]["yaw_rad"] = math.radians(20)
        elif action == 5:
            row["self"]["position_world_cm"] = [0., 0., -120.]
            row["target"].update(fight_distance_cm=365., relative_position_cm=[365., 0., 0.])
        wrappers.append({"entity_id": 1, "observation_seq": 78+index,
                         "raw_capture_seq": 200+index*100, "observation": row})
    mechanics = [{"phase": "arena_actor_mapping", "actor_id": 101, "observation_entity_id": 1},
                 {"phase": "observation_actor_mapping", "actor_id": 102, "observation_entity_id": 2},
                 {"phase": "physics_step", "seq": 1, "actor_id": 102, "t_game": 967,
                  "movealgo_tick": True, "moved_dx": 2., "moved_dz": 0.}]
    for index, game in enumerate(times):
        mechanics.append({"phase": "physics_step", "seq": 10+index, "actor_id": 101,
                          "t_game": game, "movealgo_tick": True, "grounded": True,
                          "collision_failed_count": 0, "collision_prefall": False,
                          "moved_dx": 0., "moved_dz": -12. if action == 5 else 0.})
    for index, game in enumerate(times+[settle_time]):
        mechanics.append({**identity, "phase": "visibility_diagnostic", "seq": 100+index,
                          "actor_id": 101, "t_game": game, "target_entity_id": 2,
                          "actual_can_see": True, "controller_visible": True,
                          "query_matches_diagnostic": True, "release_pending": False,
                          "owner": "scripted", "provider": PROVIDER,
                          "execution_id": 500, "command_id": 7,
                          "source_phase": "controller_segment_completed" if game == end_time else "pre_native_primitive",
                          "center_ray": {"range_pass": True, "ray_hit": False, "query_pass": True},
                          "head_ray": {"range_pass": True, "ray_hit": False, "query_pass": True}})
    if action == 5:
        mechanics.append({"phase": "hit_attempt", "seq": 50, "actor_id": 102,
                          "target_id": 101, "t_game": 1200, "result": "out_of_range"})
    proof = {"decision": decision, "executions": executions, "observations": wrappers,
             "decisions": [], "mechanics": mechanics}
    return before, deepcopy(wrappers[-1]["observation"]), proof


@pytest.mark.parametrize("action", [2, 5, 11])
def test_selection_is_causal_and_ignores_case_oracle_and_future_hints(action):
    row = observed(action)
    original = deepcopy(row)
    assert choose(row, []) == action
    row.update(expected_action_id=4, task="force_strafe", case="heldout", seed=223,
               native_choice_id=4, future_peer_action=4, pose_cone={"actual_can_see": False},
               visibility_diagnostic={"actual_can_see": False}, model_logits=[999]*12)
    row["self"].update(queued_primitive=4, body_state_raw="S_DUMMY", choice_id=4)
    row["target"].update(body_state_raw="S_DUMMY", animation_name="future_attack")
    assert choose(row, [{"entity_id": 2, "phase": "completed", "command_id": 999, "t_game_ms": 2000}]) == action
    assert original == observed(action)


@pytest.mark.parametrize("field,value", [("periodic", False), ("can_decide", False),
    ("native_decision_eligible", False), ("action_mask_valid", False),
    ("phase", "pre_native_primitive"), ("mask_kind", "invented_mask")])
def test_event_only_or_locked_rows_are_not_teacher_opportunities(field, value):
    row = observed()
    row[field] = value
    assert choose(row, []) is None and certificate(row, 2) is None


def test_masks_are_exact_boolean_shared_resolver_receipts():
    row = observed(5)
    row["action_mask"][5] = False
    assert choose(row, []) is None
    row = observed(5)
    row["action_mask"][5] = 1
    assert choose(row, []) is None


def test_near_threat_jump_uses_native_g_range_without_inventing_attack_legality():
    row = observed(5)
    assert row["target"]["in_w_range"] is False
    assert observed_guard(row, 5)["passed"] is True and choose(row, []) == 5
    row["target"]["fight_distance_cm"] = 311.
    assert choose(row, []) is None
    row = observed(5)
    row["local_obstacles"]["rays"][8]["hit"] = True
    assert choose(row, []) is None


@pytest.mark.parametrize("field", ["yaw_error_rad", "fight_distance_cm", "g_range_cm"])
def test_nonfinite_numeric_threat_does_not_make_a_label(field):
    row = observed(5)
    row["target"][field] = float("nan")
    assert choose(row, []) is None


def test_wait_bounds_closing_range_and_angular_motion_in_world_coordinates():
    row = observed(11)
    row["self"]["linear_velocity_cm_s"] = [30., 0., 10.]
    row["target"]["relative_velocity_cm_s"] = [-30., 0., 40.]
    assert motion(row)["peer_speed_cm_s"] == 50.
    assert choose(row, []) == 11
    row["target"]["relative_velocity_cm_s"] = [-400., 0., 40.]
    assert choose(row, []) is None
    row = observed(11)
    row["target"]["relative_velocity_cm_s"] = [0., 0., 1000.]
    assert choose(row, []) is None


@pytest.mark.parametrize("history", [
    [{"entity_id": 1, "phase": "completed", "command_id": 9, "t_game_ms": 1001}],
    [{"entity_id": 2, "phase": "completed", "command_id": 9, "t_game_ms": 999}],
    [{"entity_id": 1, "phase": "started", "command_id": 9, "t_game_ms": 999}]])
def test_certificates_reject_future_opposite_peer_and_uncompleted_context(history):
    row = observed()
    cert = certificate(row, 2)
    cert["context"]["completed_command_ids"] = [9]
    assert verify_precondition(cert, row, history)["passed"] is False
    assert certificate(row, 2, history) is None


def test_certificate_binds_exact_current_lease_and_observation():
    row = observed()
    cert = certificate(row, 2)
    assert verify_precondition(cert, row, [])["passed"] is True
    for field in ("entity_id", "session_epoch", "world_epoch", "control_generation", "observation_seq", "t_game_ms"):
        changed = deepcopy(row)
        changed[field] += 1
        assert verify_precondition(cert, changed, [])["passed"] is False


@pytest.mark.parametrize("action", [2, 5, 11])
def test_actual_completed_segment_requires_numeric_native_and_settled_proof(action):
    before, after, proof = physical_segment(action)
    assert verify_outcome(action, before, after, proof)["passed"] is True


def test_real_turn_can_start_and_complete_in_one_game_tick_but_not_without_order_or_rotation():
    before, after, proof = physical_segment(2)
    start, end = proof["executions"][1:]
    assert start["t_game_ms"] == end["t_game_ms"]
    assert verify_outcome(2, before, after, proof)["passed"] is True
    end["raw_capture_seq"] = start["raw_capture_seq"]
    assert verify_outcome(2, before, after, proof)["passed"] is False
    before, after, proof = physical_segment(2)
    proof["observations"][-1]["observation"]["self"]["yaw_rad"] = before["self"]["yaw_rad"]
    assert verify_outcome(2, before, after, proof)["passed"] is False


@pytest.mark.parametrize("field", ["entity_id", "control_generation", "execution_id", "decision_id"])
def test_other_actor_lease_or_execution_receipts_cannot_certify_the_effect(field):
    before, after, proof = physical_segment()
    proof["executions"][1][field] += 1
    assert verify_outcome(2, before, after, proof)["passed"] is False


def test_future_anchor_and_cancellation_are_not_completed_labels():
    before, after, proof = physical_segment()
    proof["decision"]["raw_observation_record_seq"] = 201
    assert verify_outcome(2, before, after, proof)["passed"] is False
    before, after, proof = physical_segment()
    proof["executions"].append({**proof["executions"][-1], "phase": "cancelled"})
    assert verify_outcome(2, before, after, proof)["passed"] is False


@pytest.mark.parametrize("failure", ["visibility", "same_ray", "ownership", "endpoint", "missing_physics",
    "missing_peer_motion", "hp", "native_damage", "airborne_settled", "periodic_gap_68", "overlap"])
def test_intended_safe_effect_fails_closed_for_actual_physical_or_coverage_losses(failure):
    before, after, proof = physical_segment(11)
    diagnostic = next(row for row in proof["mechanics"] if row.get("phase") == "visibility_diagnostic")
    if failure == "visibility":
        diagnostic.update(actual_can_see=False, source_phase="pre_native_primitive")
    elif failure == "same_ray":
        diagnostic["center_ray"]["ray_hit"] = True
        diagnostic["head_ray"]["range_pass"] = False
    elif failure == "ownership":
        diagnostic["release_pending"] = True
    elif failure == "endpoint":
        for row in proof["mechanics"]:
            if row.get("source_phase") == "controller_segment_completed":
                row["execution_id"] += 1
    elif failure in ("missing_physics", "missing_peer_motion"):
        actor = 101 if failure == "missing_physics" else 102
        proof["mechanics"] = [row for row in proof["mechanics"] if not (row.get("phase") == "physics_step" and row.get("actor_id") == actor)]
    elif failure == "hp":
        proof["observations"][3]["observation"]["self"]["hp"] -= 1
    elif failure == "native_damage":
        proof["mechanics"].append({"phase": "damage", "actor_id": 101, "t_game": 1200, "hp_before": 300, "hp_after": 299})
    elif failure == "airborne_settled":
        proof["observations"][-1]["observation"]["self"]["in_air"] = True
    elif failure == "periodic_gap_68":
        proof["observations"][1]["observation"]["t_game_ms"] = 1068
        del proof["observations"][2]
    else:
        proof["decisions"].append({"entity_id": 1, "accepted": True, "raw_capture_seq": 450, "t_game_ms": 1133})
    assert verify_outcome(11, before, after, proof)["passed"] is False


def test_jump_needs_an_actual_mapped_avoided_attempt_in_its_own_segment():
    before, after, proof = physical_segment(5)
    assert verify_outcome(5, before, after, proof)["passed"] is True
    attempt = next(row for row in proof["mechanics"] if row.get("phase") == "hit_attempt")
    attempt["target_id"] = 999
    assert verify_outcome(5, before, after, proof)["passed"] is False
    attempt["target_id"] = 101
    attempt["t_game"] = 900
    assert verify_outcome(5, before, after, proof)["passed"] is False


def test_unmatched_applied_hit_cannot_be_relabelled_as_a_safe_jump():
    before, after, proof = physical_segment(5)
    attempt = next(row for row in proof["mechanics"] if row.get("phase") == "hit_attempt")
    attempt["result"] = "applied"
    assert verify_outcome(5, before, after, proof)["passed"] is False
    proof["mechanics"].append({"phase": "defence_result", "seq": 51, "actor_id": 101,
                               "target_id": 102, "t_game": 1200, "damage_kind": "melee", "jumped_back": True})
    assert verify_outcome(5, before, after, proof)["passed"] is True
