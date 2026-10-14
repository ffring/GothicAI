"""Observational combat rules and protocol checks; no engine/model is launched."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_arena_env import ArenaRunner
from body_combat_demonstrations import CombatRunner, declared_cases, verify_maneuver_enable, verify_numeric_wall_clearance, wall_clearance_evidence
from body_trajectory_recorder import RecorderError
from body_combat_teacher import PROVIDER, choose, select_certificate, verify_outcome, verify_precondition


def row():
    return {"entity_id": 1, "session_epoch": 7, "world_epoch": 2, "control_generation": 1,
            "observation_seq": 3, "t_game_ms": 1000, "periodic": True, "phase": "post_simulation_post_animation",
            "can_decide": True, "action_mask_valid": True, "action_mask": [True]*12,
            "self": {"hp": 300, "hp_fraction": 1., "down": False, "yaw_rad": 0, "position_world_cm": [0, 0, 0],
                     "in_air": False, "swim": False, "dive": False, "interruptible": True, "gates": {"fai_wait": False}},
            "target": {"entity_id": 2, "visible": True, "current_valid": True, "down": False, "hp": 300,
                       "fight_distance_cm": 180, "relative_position_cm": [180, 0, 0], "g_range_cm": 310, "in_w_range": True, "focus_5": True,
                       "focus_30": True, "yaw_error_rad": 0., "melee_line_clear": True, "attack_anim": False,
                       "prehit": False, "defence_active": False},
            "local_obstacles": {"rays": [{"hit": False, "distance_cm": 300} for _ in range(16)]}}


def cert(before, action, history=None, *, enabled=False):
    return select_certificate(before, action, history or [], command_id=1, certificate_seq=1, wall_monotonic=10., maneuver_enabled=enabled)


def evidence(action, mechanics):
    return {"executions": [{"phase": "accepted", "t_game_ms": 1000},
                           {"phase": "started", "t_game_ms": 1033, "effective_primitive": action},
                           {"phase": "completed", "t_game_ms": 1367}],
            "mechanics": [{"phase": "arena_actor_mapping", "actor_id": 101, "observation_entity_id": 1},
                          {"phase": "arena_actor_mapping", "actor_id": 102, "observation_entity_id": 2}]+mechanics}


def test_choices_follow_actual_fight_without_an_action_cycle_or_source_mutation():
    before, history = row(), []
    original = deepcopy((before, history))
    assert choose(before, history) == 6
    assert (before, history) == original
    history.append({"entity_id": 1, "command_id": 1, "action_id": 6, "phase": "completed", "t_game_ms": 900})
    assert choose(before, history) == 6
    before["target"].update(in_w_range=False, fight_distance_cm=400)
    assert choose(before, history) == 1
    before["target"].update(focus_5=False, yaw_error_rad=math.radians(20))
    assert choose(before, history) == 2
    before["target"].update(in_w_range=True, attack_anim=True)
    assert choose(before, history) == 9
    before["self"]["hp_fraction"] = .2
    assert choose(before, history) == 5


@pytest.mark.parametrize("field,value", [("periodic", False), ("can_decide", False), ("action_mask_valid", False),
                                         ("phase", "pre_native_primitive")])
def test_teacher_ignores_locked_or_event_only_opportunities(field, value):
    before = row()
    before[field] = value
    assert choose(before, []) is None and cert(before, 6) is None


def test_safe_maneuvers_remain_fail_closed_despite_clear_numeric_rays():
    before = row()
    before["target"].update(in_w_range=False, fight_distance_cm=450)
    assert cert(before, 3) is None and cert(before, 4) is None


def maneuver_row():
    before = row()
    before["target"].update(in_w_range=False, fight_distance_cm=378, relative_position_cm=[350, 0, 0], yaw_error_rad=math.radians(20), focus_5=False)
    before["local_obstacles"]["rays"][7] = {"hit": True, "distance_cm": 100}
    return before


def test_enabled_direction_uses_observed_clearance_and_wait_uses_visible_distance():
    before = maneuver_row()
    assert cert(before, 4) is None  # Probe enable marker is mandatory.
    assert cert(before, 4, enabled=True) is not None and cert(before, 3, enabled=True) is None
    assert choose(before, [], allow_maneuver=True, maneuvers_only=True) == 4
    past = [{"entity_id": 1, "command_id": 1, "action_id": 4, "phase": "completed", "t_game_ms": 980}]
    assert choose(before, past, allow_maneuver=True, maneuvers_only=True) == 10
    before["target"].update(fight_distance_cm=478, relative_position_cm=[450, 0, 0])
    assert choose(before, past, allow_maneuver=True, maneuvers_only=True) == 11
    before["local_obstacles"]["rays"][15] = {"hit": True, "distance_cm": 100}
    assert choose(before, [], allow_maneuver=True, maneuvers_only=True) is None  # Equal clearances have no invented direction.


def arc_evidence():
    before, after = maneuver_row(), maneuver_row()
    after["self"]["position_world_cm"] = [0, 0, -120]
    after["target"].update(focus_5=True, yaw_error_rad=0)
    proof = evidence(4, [])
    proof["decision"] = {"raw_capture_seq": 10, "entity_id": 1, "command_id": 1}
    proof["decisions"] = []
    proof["observations"] = []
    for index, game in enumerate((1050, 1100, 1150, 1200, 1250, 1300, 1350, 1367, 1400), 20):
        value = deepcopy(after)
        value.update(t_game_ms=game, observation_seq=index, can_decide=game >= 1400, periodic=game != 1367)
        value["self"].update(interruptible=False, gates={"fai_wait": game < 1367})
        proof["observations"].append({"raw_capture_seq": index, "raw_observation_source": "periodic", "observation": value})
    for index, game in enumerate((1033, 1067, 1100, 1133, 1167, 1200, 1233, 1267, 1300, 1333, 1367), 100):
        proof["mechanics"].append({"seq": index, "phase": "physics_step", "t_game": game, "actor_id": 101,
                                   "movealgo_tick": True, "grounded": True, "collision_failed_count": 0,
                                   "collision_prefall": False, "moved_dx": 0, "moved_dz": -12})
        proof["mechanics"].append({"seq": index+100, "phase": "visibility_diagnostic", "t_game": game, "actor_id": 101,
                                   "actual_can_see": True, "reason": "visible", "native_fai_wait": game < 1367,
                                   "center_ray": {"range_pass": True, "ray_hit": False}, "head_ray": {"range_pass": True, "ray_hit": False}})
    return before, after, proof


def test_safe_arc_requires_real_covered_motion_and_settled_visibility():
    before, after, proof = arc_evidence()
    assert verify_outcome(4, before, after, proof)["passed"] is True
    proof["observations"][-1]["observation"]["target"]["visible"] = False
    assert verify_outcome(4, before, after, proof)["passed"] is False


def test_safe_arc_endpoint_uses_actual_native_focus30_and_attack_requires_focus5():
    before, after, proof = arc_evidence()
    proof["observations"][-1]["observation"]["target"].update(focus_5=False, focus_30=True, yaw_error_rad=math.radians(7))
    assert verify_outcome(4, before, after, proof)["passed"] is True
    strike = row()
    strike["target"].update(focus_5=False, focus_30=True, yaw_error_rad=math.radians(7))
    assert cert(strike, 6) is None and choose(strike, [], allow_maneuver=True) == 2


def test_enable_marker_requires_same_actual_backend_before_capture(tmp_path):
    args = SimpleNamespace(output=tmp_path/"new-run"/"capture")
    pending = {"maneuver_rule_status": "fail_closed_pending_native_bip_los_diagnosis"}
    assert verify_maneuver_enable(args, pending, "backend") == {"enabled": False, "reason": "paired_motor_evidence_pending"}
    enabled = {"maneuver_rule_status": "enabled_after_paired_motor_probe", "microcurriculum": {"safe_endpoint_focus_degrees": 30},
               "maneuver_enable_evidence": {"run": "motor", "binary_sha256": "different", "files": {}}}
    with pytest.raises(RecorderError, match="binary"):
        verify_maneuver_enable(args, enabled, "backend")
    enabled["maneuver_enable_evidence"]["binary_sha256"] = "backend"
    enabled["maneuver_enable_evidence"]["run"] = "../escape"
    with pytest.raises(RecorderError, match="run"):
        verify_maneuver_enable(args, enabled, "backend")


def test_transient_protected_pose_gap_100ms_is_truthful_and_longer_gap_fails():
    before, after, proof = arc_evidence()
    for item in proof["observations"]:
        if item["observation"]["t_game_ms"] in (1100, 1150):
            item["observation"]["target"].update(visible=False, current_valid=False)
    for item in proof["mechanics"]:
        if item.get("phase") == "visibility_diagnostic" and 1100 <= item["t_game"] < 1200:
            item.update(actual_can_see=False, reason="outside_pose_cone")
    result = verify_outcome(4, before, after, proof)
    assert result["passed"] is True and result["proof"]["maximum_invalid_gap_ms"] == 100
    next(item for item in proof["mechanics"] if item.get("phase") == "visibility_diagnostic" and item["t_game"] == 1200).update(actual_can_see=False, reason="outside_pose_cone")
    assert verify_outcome(4, before, after, proof)["passed"] is False


@pytest.mark.parametrize("failure", ["geometry", "split_ray", "range", "collision", "prefall", "hp", "new_command"])
def test_native_arc_rejects_actual_unsafe_effects(failure):
    before, after, proof = arc_evidence()
    diagnostic = next(item for item in proof["mechanics"] if item.get("phase") == "visibility_diagnostic")
    physics = next(item for item in proof["mechanics"] if item.get("phase") == "physics_step")
    if failure == "geometry":
        diagnostic["center_ray"]["ray_hit"] = diagnostic["head_ray"]["ray_hit"] = True
    elif failure == "split_ray":
        diagnostic["center_ray"]["ray_hit"] = True
        diagnostic["head_ray"]["range_pass"] = False
    elif failure == "range":
        diagnostic["center_ray"]["range_pass"] = diagnostic["head_ray"]["range_pass"] = False
    elif failure == "collision":
        physics["collision_failed_count"] = 1
    elif failure == "prefall":
        physics["collision_prefall"] = True
    elif failure == "hp":
        proof["observations"][3]["observation"]["self"]["hp"] = 290
    else:
        proof["decisions"].append({"entity_id": 1, "accepted": True, "raw_capture_seq": 30, "t_game_ms": 1200})
    assert verify_outcome(4, before, after, proof)["passed"] is False


def test_actual_completed_offensive_trade_has_hit_proof_but_cancellation_does_not():
    before, after = row(), row()
    after["target"]["hp"] = 280
    after["self"]["hp"] = 290
    mechanics = [{"phase": "hit_attempt", "seq": 10, "t_game": 1100, "actor_id": 101, "target_id": 102, "result": "applied"},
                 {"phase": "damage", "seq": 11, "t_game": 1100, "actor_id": 102, "target_id": 101,
                  "damage_kind": "melee", "hp_before": 300, "hp_after": 280},
                 {"phase": "damage", "seq": 12, "t_game": 1200, "actor_id": 101, "target_id": 102,
                  "damage_kind": "melee", "hp_before": 300, "hp_after": 290}]
    proof = evidence(6, mechanics)
    assert verify_outcome(6, before, after, proof)["passed"] is True
    proof["executions"][-1]["phase"] = "cancelled"
    assert verify_outcome(6, before, after, proof)["passed"] is False
    assert verify_outcome(6, before, after, evidence(6, []))["passed"] is False


def test_context_rejects_unrecorded_future_commands():
    before = row()
    certificate = cert(before, 6)
    future = [{"entity_id": 1, "command_id": 1, "action_id": 2, "phase": "completed", "t_game_ms": 1100}]
    certificate["context"]["completed_command_ids"] = [1]
    assert verify_precondition(certificate, before, future)["passed"] is False


def test_full_cases_use_new_groups_both_sides_and_real_nearwall_distances():
    config = json.loads((Path(__file__).resolve().parents[2]/"evals/npc-body/body-combat-demonstrations-v1.json").read_text())
    old = json.loads((Path(__file__).resolve().parents[2]/"evals/npc-body/body-demonstrations-v1.json").read_text())
    cases = declared_cases(config)
    assert len(cases) == len({case["name"] for case in cases}) == 84
    assert sum(case["split"] == "train" for case in cases) == 72
    assert sum(case["task"] == "native_duel" and case["split"] == "train" for case in cases) == 24
    assert sum(case["task"] == "reset_stress" and case["split"] == "train" for case in cases) == 48
    assert sum(case["split"] == "eval" for case in cases) == 8
    assert sum(case["split"] == "ood" for case in cases) == 4
    assert config["microcurriculum"]["apply_clearance_to_full_bouts"] is False
    assert all("wall_clearance_cm" not in case for case in cases if case["task"] == "native_duel")
    assert not any(case["split"] == "test" or case["seed"] in config["reserved_test_seeds"] for case in cases)
    assert not set(config["new_group_assignments"]).intersection(old["inherited_group_assignments"])
    for side in (0, 1):
        expected = {200,350,450}
        assert {case["distance_cm"] for case in cases if case["split"] == "train" and case["geometry"] == "near_wall" and case["side"] == side} == expected
    micros = [case for case in cases if case["task"] == "reset_stress"]
    assert {case["wall_clearance_cm"] for case in micros} == {120,130,140,150,160,170}
    for seed in config["train_seeds"]:
        selected = [case for case in micros if case["seed"] == seed]
        assert len({(case["side"], case["yaw_offset_deg"], case["distance_cm"]) for case in selected}) == 8
        assert len({case["wall_clearance_cm"] for case in selected}) == 1
        assert [case["expected_action_id"] for case in selected].count(3) == 4
        assert [case["expected_action_id"] for case in selected].count(4) == 4
    assert all("wall_clearance_cm" not in case for case in cases if case["split"] != "train" or case["geometry"] == "open")
    assert len(declared_cases(config, smoke=True)) == 12


def test_geometry_preflight_covers_all_micro_tuples_and_never_full_bouts_or_heldout():
    config = json.loads((Path(__file__).resolve().parents[2]/"evals/npc-body/body-combat-demonstrations-v1.json").read_text())
    cases = declared_cases(config, geometry_preflight=True)
    full_micros = [case for case in declared_cases(config) if case["task"] == "reset_stress"]
    assert cases == full_micros
    assert len(cases) == len({case["name"] for case in cases}) == 48
    assert all(case["split"] == "train" and case["geometry"] == "near_wall" for case in cases)
    assert {case["wall_clearance_cm"] for case in cases} == {120,130,140,150,160,170}
    assert config["geometry_preflight"]["scope"] == "preflight"
    assert config["geometry_preflight"]["automatic_training_admission"] is False
    with pytest.raises(ValueError, match="mutually exclusive"):
        declared_cases(config, calibration=True, geometry_preflight=True)
    configured = deepcopy(config)
    configured["microcurriculum"]["variants"][0]["wall_tangent_sign"] = -1
    mirrored = [case for case in declared_cases(configured,geometry_preflight=True) if case["variant_id"] == configured["microcurriculum"]["variants"][0]["id"]]
    assert len(mirrored) == 6 and all(case["wall_tangent_sign"] == -1 for case in mirrored)


def test_calibration_cases_are_explicit_bounded_train_only_and_not_full_production():
    config = json.loads((Path(__file__).resolve().parents[2]/"evals/npc-body/body-combat-demonstrations-v1.json").read_text())
    cases = declared_cases(config, calibration=True)
    assert len(cases) == len({case["name"] for case in cases}) == 8
    assert all(case["seed"] == 181 and case["split"] == "train" and case["task"] == "reset_stress" for case in cases)
    right = [case for case in cases if case["calibration_expected_direction"] == 4]
    left = [case for case in cases if case["calibration_expected_direction"] == 3]
    assert len(right) == len(left) == 4
    assert {(case["side"],case["yaw_offset_deg"],case["distance_cm"],case["wall_tangent_sign"]) for case in right} == {
        (0,yaw,distance,-1) for yaw in (-35,-38) for distance in (350,450)}
    assert {(case["side"],case["yaw_offset_deg"],case["distance_cm"],case["wall_tangent_sign"]) for case in left} == {
        (0,yaw,distance,1) for yaw in (20,35) for distance in (350,450)}
    assert all(case["wall_clearance_cm"] == 150 for case in cases)
    assert all(case["wall_tangent_sign"]*(1 if case["side"] == 0 else -1) == -1 for case in right)
    assert not {case["name"] for case in cases}.intersection(case["name"] for case in declared_cases(config))
    assert config["calibration"]["automatic_training_admission"] is False


def normal_geometry_initial():
    x, z = 70/72.3, math.sqrt(1-(70/72.3)**2)
    geometry = {"requested_wall_clearance_cm": 70, "actual_wall_clearance_cm": 72.3,
                "wall_clearance_error_cm": 2.3, "wall_clearance_tolerance_cm": 20, "obstacle_distance_cm": 72.3,
                "placement_basis": "wall_normal_xz", "placement_wall_normal_xz_world": [-x,0,z],
                "placement_wall_tangent_world": [z,0,x], "search_ray_direction_world": [1,0,0],
                "wall_direction_world": [x,0,-z], "placement_wall_anchor_cm": [70,85,0],
                "wall_point_cm": [70,85,-72.3*z], "wall_normal": [-1,0,0],
                "primary_collision_center_cm": [0,85,0], "actual_wall_normal_xz_world": [-1,0,0],
                "requested_wall_normal_clearance_cm": 70, "actual_wall_normal_clearance_cm": 70,
                "wall_normal_clearance_error_cm": 0, "wall_normal_clearance_tolerance_cm": 20}
    return {"primary_entity_id": 1, "session_epoch": 7, "world_epoch": 2,
            "initial_state": {"setup": {"wall_clearance_cm": 70, "geometry_proof": geometry}}}


def test_wall_clearance_requires_actual_native_measurement_and_preserves_legacy_default():
    case = {"geometry": "near_wall", "wall_clearance_cm": 70}
    initial = normal_geometry_initial()
    assert wall_clearance_evidence(initial, case)["actual_cm"] == 72.3
    assert wall_clearance_evidence(initial, case)["normal_actual_cm"] == 70
    reversed_winding = deepcopy(initial)
    reversed_winding["initial_state"]["setup"]["geometry_proof"]["wall_normal"] = [1,0,0]
    assert wall_clearance_evidence(reversed_winding, case)["normal_actual_cm"] == 70
    assert wall_clearance_evidence({}, {"geometry": "near_wall"}) is None
    for key, value in (("actual_wall_clearance_cm", 120), ("actual_wall_clearance_cm", float("nan")),
                       ("wall_clearance_error_cm", -2.3), ("wall_clearance_tolerance_cm", 50),
                       ("obstacle_distance_cm", 110), ("requested_wall_clearance_cm", 90)):
        forged = deepcopy(initial)
        forged["initial_state"]["setup"]["geometry_proof"][key] = value
        with pytest.raises(RecorderError, match="actual native geometry"):
            wall_clearance_evidence(forged, case)


@pytest.mark.parametrize("key,value", [("actual_wall_normal_clearance_cm", 72.3), ("actual_wall_normal_xz_world", [0,0,1]),
                                      ("placement_wall_tangent_world", [1,0,0]), ("wall_direction_world", [1,0,0]),
                                      ("primary_collision_center_cm", [5,85,0]), ("placement_basis", "search_ray")])
def test_wall_normal_projection_is_not_replaced_by_ray_length_or_wrong_geometry(key, value):
    initial = normal_geometry_initial()
    initial["initial_state"]["setup"]["geometry_proof"][key] = value
    with pytest.raises(RecorderError, match="actual native geometry proof"):
        wall_clearance_evidence(initial, {"geometry": "near_wall", "wall_clearance_cm": 70})


def test_ordinary_numeric_ray_separately_projects_onto_actual_wall_normal():
    initial = normal_geometry_initial()
    evidence = wall_clearance_evidence(initial, {"geometry": "near_wall", "wall_clearance_cm": 70})
    observed = row()
    for index, ray in enumerate(observed["local_obstacles"]["rays"]):
        ray["angle_rad"] = 2*math.pi*index/16
    observed["local_obstacles"]["rays"][0] = {"angle_rad": 0, "distance_cm": 70, "hit": True}
    assert verify_numeric_wall_clearance(initial, evidence, observed)["normal_projection_cm"] == 70
    for distance in (72.3,300):
        changed = deepcopy(observed)
        changed["local_obstacles"]["rays"][0]["distance_cm"] = distance
        with pytest.raises(RecorderError, match="independently confirm"):
            verify_numeric_wall_clearance(initial, evidence, changed)


@pytest.mark.parametrize("side,sign", [(0,1),(0,-1),(1,1),(1,-1)])
def test_requested_wall_tangent_sign_is_bound_to_actual_positions_and_physical_side(side, sign):
    initial = normal_geometry_initial()
    setup = initial["initial_state"]["setup"]
    setup["wall_tangent_sign"] = sign
    geometry = setup["geometry_proof"]
    geometry.update(requested_wall_tangent_sign=sign, placement_tangent_sign=sign)
    relative_sign = sign*(1 if side == 0 else -1)
    displacement = [v*350*relative_sign for v in geometry["placement_wall_tangent_world"]]
    initial["initial_state"]["actors"] = [{"role":"primary","physical_side":side,"pos":[0,0,0]},
                                          {"role":"opponent","physical_side":1-side,"pos":displacement}]
    case = {"geometry":"near_wall","wall_clearance_cm":70,"wall_tangent_sign":sign,"distance_cm":350,"side":side}
    assert wall_clearance_evidence(initial,case)["primary_relative_tangent_sign"] == relative_sign
    changed = deepcopy(initial)
    changed["initial_state"]["actors"][1]["pos"] = [-v for v in displacement]
    with pytest.raises(RecorderError,match="actor coordinates"):
        wall_clearance_evidence(changed,case)
    with pytest.raises(RecorderError,match="unexpectedly imposed"):
        wall_clearance_evidence(initial,{"geometry":"near_wall","wall_clearance_cm":70})


def test_actual_teacher_provider_is_rewritten_before_ipc_logging(monkeypatch):
    commands = []
    monkeypatch.setattr(ArenaRunner, "call", lambda self, command: commands.append(command) or {"ok": True})
    runner = CombatRunner.__new__(CombatRunner)
    runner.setup_options = {"geometry": "near_wall", "yaw_offset_deg": 20, "wall_clearance_cm": 90, "wall_tangent_sign": -1}
    runner.call({"cmd": "body_action", "provider": "stage5-scripted"})
    runner.call({"cmd": "body_heartbeat", "provider": "stage5-scripted"})
    runner.call({"cmd": "reset", "arena": {"scenario": "native_duel"}})
    assert [command["provider"] for command in commands[:2]] == [PROVIDER]*2
    assert commands[-1]["arena"] == {"scenario": "native_duel", "geometry": "near_wall", "yaw_offset_deg": 20, "wall_clearance_cm": 90, "wall_tangent_sign": -1}
