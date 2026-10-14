"""Independent stage 5 truth/counter checks; no native engine or remote calls."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("body_arena_report", Path(__file__).parents[1]/"body_arena_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def actor(role="primary", entity=10, target=20):
    return {"entity_id": entity, "role": role, "symbol": "BDT_1003_BANDIT_M" if role == "primary" else "PAL_255_RITTER",
            "physical_side": 0 if role == "primary" else 1, "hp": 300, "hp_max": 300, "str": 60, "dex": 60,
            "onehand": 60, "onehand_talent": 2, "edge_protection": 30, "blunt_protection": 30,
            "weapon_symbol": 101, "weapon": "ItMw_1h_Common_01", "armor_symbol": 102, "armor": "ItAr_Leather_L",
            "weapon_damage": 10, "weapon_length": 100, "weapon_state": 2, "immortal": False,
            "pos": [0 if role == "primary" else 150, 0, 0], "yaw_deg": 0 if role == "primary" else 180,
            "target_entity_id": target, "external_control": False, "current_state": 0, "ai_queue_size": 0,
            "ai_overlay_queue_size": 0, "fight_queue": [0]*6, "primitive": 0,
            "waits_ms": {"fai": 0, "ani": 0, "ai": 0, "output": 0}, "down": False, "dead": False,
            "unconscious": False, "owner": "native", "control_generation": 0, "goal_version": 0,
            "command_id": 0, "execution_id": 0, "release_pending": False, "control_allowed": True,
            "can_decide": True, "action_mask": [False]*12, "blocked_hits": 0, "received_damage": 0, "outgoing_damage": 0,
            "body_state": 98304, "pose": "S_1HRUN", "interruptible": True, "segment_remaining_ms": 0,
            "native_eligible": True, "attack_anim": False, "prehit": False, "defence": False, "jump_back": False}


def arena():
    return {"schema_version": 1, "episode_id": 1, "runner_generation": 2, "session_epoch": 123,
            "world_epoch": 2, "scenario": "native_duel", "seed": 17, "side": 0, "distance_cm": 150,
            "timeout_ms": 30000, "primary_entity_id": 10, "opponent_entity_id": 20, "ready": True,
            "started": False, "outcome": "running", "terminated": False, "truncated": False,
            "infrastructure": False, "elapsed_game_ms": 0, "start_game_ms": 0, "game_ms": 10,
            "actors": [actor(), actor("opponent", 20, 10)]}


def command(action=6, phases=("accepted", "started", "completed")):
    base = {"schema_version": 1, "event": "body_control_event", "session_epoch": 123, "world_epoch": 2,
            "control_generation": 3, "entity_id": 10, "target_entity_id": 20, "command_id": 1, "goal_version": 1,
            "action_id": action, "observation_seq": 4, "execution_id": 7, "t_game_ms": 10, "t_wall_ms": 10,
            "reason": "segment_started", "pose_before": "idle:start=0", "pose_after": "attack:start=10",
            "remaining_lock_game_ms": 200, "segment_started": True}
    return [{**base, "event_seq": i+1, "phase": phase, "t_game_ms": 10+i*10} for i, phase in enumerate(phases)]


def decision_inputs(action=6):
    request = {"cmd": "body_action", "session_epoch": 123, "world_epoch": 2, "control_generation": 3,
               "entity_id": 10, "command_id": 1, "action_id": action, "observation_seq": 4, "issued_game_ms": 10,
               "goal_version": 1, "target_entity_id": 20}
    observation = {key: request[key] for key in ("session_epoch", "world_epoch", "control_generation", "entity_id", "observation_seq")}
    observation.update(t_game_ms=10, action_mask=[True]*12, can_decide=True, action_mask_valid=True)
    return [observation], [(request, {"accepted": True}, 1.0, 1.1)]


def mechanics():
    return [{"phase": "arena_actor_mapping", "observation_entity_id": 10, "actor_id": 100},
            {"phase": "arena_actor_mapping", "observation_entity_id": 20, "actor_id": 200},
            {"phase": "hit_attempt", "actor_id": 100, "target_id": 200, "result": "applied", "t_game": 20},
            {"phase": "damage", "actor_id": 200, "target_id": 100, "damage_kind": "melee", "hp_before": 300, "hp_after": 290, "t_game": 20}]


def test_clean_reset_contains_full_semantic_state():
    assert report.clean_reset(arena()) == []


@pytest.mark.parametrize("field,value", [("hp", 200), ("immortal", True), ("owner", "scripted"),
    ("control_generation", 1), ("command_id", 9), ("execution_id", 4), ("ai_queue_size", 1),
    ("fight_queue", [1, 0, 0, 0, 0, 0]), ("waits_ms", {"fai": 1, "ani": 0, "ai": 0, "output": 0}),
    ("release_pending", True), ("received_damage", 10)])
def test_dirty_previous_combat_cannot_be_reported_as_clean_reset(field, value):
    state = arena()
    state["actors"][0][field] = value
    assert report.clean_reset(state)


def test_semantic_state_ignores_only_runtime_identity():
    state = arena()
    renamed = copy.deepcopy(state)
    renamed.update(session_epoch=999, world_epoch=9, episode_id=8, runner_generation=9)
    renamed["actors"][0].update(entity_id=30, target_entity_id=40)
    renamed["actors"][1].update(entity_id=40, target_entity_id=30)
    assert report.semantic_initial(state) == report.semantic_initial(renamed)
    renamed["actors"][0]["weapon_damage"] += 1
    assert report.semantic_initial(state) != report.semantic_initial(renamed)


def test_missing_target_or_equipment_cannot_pass_semantic_comparison():
    state = arena()
    state["actors"][0]["target_entity_id"] = 0
    with pytest.raises(ValueError, match="target"):
        report.semantic_initial(state)
    state = arena()
    state["actors"][0].pop("armor_symbol")
    with pytest.raises(ValueError, match="required"):
        report.semantic_initial(state)


def test_timing_comes_from_raw_monotonic_ipc():
    rows = [{"direction": "request", "wall_monotonic": 2.0, "value": {"id": 1, "cmd": "reset"}},
            {"direction": "response", "wall_monotonic": 2.5, "value": {"id": 1}}]
    assert report.timed_pairs(rows)[0][2:] == (2.0, 2.5)
    rows[1]["wall_monotonic"] = 1.0
    with pytest.raises(ValueError, match="backwards"):
        report.timed_pairs(rows)
    rows[1].pop("wall_monotonic")
    with pytest.raises(ValueError, match="absent"):
        report.timed_pairs(rows)


def test_only_started_finished_noncontinue_decisions_are_transitions():
    observations, pairs = decision_inputs()
    result = report.command_evidence(command(), observations, pairs, {"terminated": False})
    assert result["issues"] == []
    assert result["policy_transitions"] == 1
    assert result["completed_decisions"] == 1
    result = report.command_evidence(command(phases=("accepted",)), observations, pairs, {})
    assert result["policy_transitions"] == 0
    assert result["not_started_decisions"] == 1
    result = report.command_evidence(command(phases=("accepted", "started")), observations, pairs, {})
    assert result["policy_transitions"] == 0
    assert result["pending_decisions"] == 1


def test_continue_ticks_do_not_inflate_policy_transition_throughput():
    observations, pairs = decision_inputs(0)
    result = report.command_evidence(command(0, ("accepted",)), observations, pairs, {})
    assert result["policy_transitions"] == 0
    assert result["continue_commands"] == 1


def test_false_mask_or_locked_observation_invalidates_claimed_decision():
    observations, pairs = decision_inputs()
    observations[0]["action_mask"][6] = False
    assert report.command_evidence(command(), observations, pairs, {})["issues"]
    observations, pairs = decision_inputs()
    observations[0]["can_decide"] = False
    assert report.command_evidence(command(), observations, pairs, {})["issues"]


def test_terminal_engine_outcome_closes_started_segment_only():
    observations, pairs = decision_inputs()
    result = report.command_evidence(command(phases=("accepted", "started")), observations, pairs, {"terminated": True})
    assert result["terminal_decisions"] == result["policy_transitions"] == 1
    result = report.command_evidence(command(phases=("accepted",)), observations, pairs, {"terminated": True})
    assert result["policy_transitions"] == 0


def test_damage_requires_both_native_actor_mapping_and_same_tick_hit():
    initial, final = arena(), arena()
    final.update(start_game_ms=10, game_ms=30)
    assert report.linked_mechanics(initial, final, [], mechanics())["linked_opponent_hp_loss"] == 10
    rows = mechanics()
    rows[-1]["t_game"] = 21
    assert report.linked_mechanics(initial, final, [], rows)["linked_attack_hits"] == 0
    rows = mechanics()
    rows[-1]["target_id"] = 777
    assert report.linked_mechanics(initial, final, [], rows)["linked_attack_hits"] == 0


def test_blocked_hit_requires_attacker_defender_mapping_and_native_hit():
    initial, final = arena(), arena()
    final.update(start_game_ms=10, game_ms=30)
    rows = mechanics()[:2]+[
        {"phase": "hit_attempt", "actor_id": 200, "target_id": 100, "result": "applied", "t_game": 20},
        {"phase": "defence_result", "actor_id": 100, "target_id": 200, "damage_kind": "melee", "blocked": True, "t_game": 20}]
    assert report.linked_mechanics(initial, final, [], rows)["linked_blocked_hits"] == 1
    rows[2]["result"] = "out_of_range"
    assert report.linked_mechanics(initial, final, [], rows)["linked_blocked_hits"] == 0


def test_throughput_uses_end_to_end_wall_time_including_reset():
    episodes = [{"commands": {"policy_transitions": 10}, "total_wall_s": 10, "rollout_wall_s": 2, "reset_wall_s": 8,
                 "usable_for_actor_update": True}]
    result = report.throughput_report(episodes, {})
    assert result["policy_transitions_s"] == 1
    assert result["rollout_policy_transitions_s"] == 5
    assert result["collection_projection_s"]["100000"] == 100000
    assert result["small_ppo_collection_feasible"] is False
    episodes[0]["total_wall_s"] = 5
    assert report.throughput_report(episodes, {})["small_ppo_collection_feasible"] is True


def test_ppo_collection_excludes_control_loss_transitions_but_keeps_discarded_wall_time():
    episodes = [{"commands": {"policy_transitions": 10}, "total_wall_s": 5, "rollout_wall_s": 4, "reset_wall_s": 1,
                 "usable_for_actor_update": True},
                {"commands": {"policy_transitions": 90}, "total_wall_s": 5, "rollout_wall_s": 4, "reset_wall_s": 1,
                 "usable_for_actor_update": False}]
    result = report.throughput_report(episodes, {})
    assert result["policy_transitions"] == 100
    assert result["policy_transitions_s"] == 10
    assert result["actor_update_transitions"] == 10
    assert result["actor_update_transitions_s"] == 1
    assert result["discarded_transitions"] == 90
    assert result["diagnostic_collection_projection_s"]["100000"] == 10000
    assert result["collection_projection_s"]["100000"] == 100000
    assert result["small_ppo_collection_feasible"] is False


def test_all_discarded_episodes_measure_speed_without_claiming_any_ppo_collection():
    episodes = [{"commands": {"policy_transitions": 100}, "total_wall_s": 1, "rollout_wall_s": .9, "reset_wall_s": .1,
                 "usable_for_actor_update": False}]
    result = report.throughput_report(episodes, {})
    assert result["policy_transitions_s"] == 100
    assert result["actor_update_transitions_s"] == 0
    assert result["collection_projection_s"] == {"32768": None, "100000": None, "1000000": None}
    assert result["small_ppo_collection_feasible"] is False


def test_driver_pass_flags_do_not_establish_stage5_readiness(tmp_path):
    (tmp_path/"manifest.json").write_text(json.dumps({"suite_scope": "full"}))
    (tmp_path/"suite.json").write_text(json.dumps({"status": "passed", "full_stage5_evidence": True}))
    result = report.summarize(tmp_path, {})
    assert result["status"] == "incomplete"
    assert result["full_stage5_evidence"] is False
    assert result["throughput"]["policy_transitions"] == 0


def test_empty_native_control_file_allowed_but_not_empty_arena_journal(tmp_path):
    path = tmp_path/"native.jsonl"
    path.write_text("")
    assert report.read_rows(path, empty=True) == []
    with pytest.raises(ValueError, match="no records"):
        report.read_rows(path)


def test_reset_profile_is_checked_against_preregistered_setup():
    state = arena()
    state["actors"][0]["str"] = 50
    issues = report.clean_reset(state, {"str": 60})
    assert any("preregistered profile" in issue for issue in issues)


def test_time_limit_closes_real_execution_without_becoming_a_terminal_win():
    observations, pairs = decision_inputs()
    result = report.command_evidence(command(phases=("accepted", "started")), observations, pairs,
                                     {"truncated": True, "outcome": "time_limit"})
    assert result["policy_transitions"] == result["truncated_decisions"] == 1
    assert result["terminal_decisions"] == 0


def test_combat_counters_reconcile_both_actor_roles_with_raw_engine_damage():
    initial, final = arena(), arena()
    final.update(start_game_ms=10, game_ms=30)
    final["actors"][1].update(hp=290, received_damage=10)
    final["actors"][0]["outgoing_damage"] = 10
    assert report.linked_mechanics(initial, final, [], mechanics())["issues"] == []
    final["actors"][0]["received_damage"] = 777
    issues = report.linked_mechanics(initial, final, [], mechanics())["issues"]
    assert any("primary: final received_damage" in issue for issue in issues)
    final["actors"][0]["received_damage"] = 0
    final["actors"][1]["blocked_hits"] = 1
    assert any("opponent: final blocked_hits" in issue for issue in report.linked_mechanics(initial, final, [], mechanics())["issues"])


def test_mirror_requires_actual_swapped_positions_and_yaw_not_only_a_side_label():
    left, right = arena(), arena()
    right["side"] = 1
    assert report.mirror_issues(left, right)
    for role, other in ((0, 1), (1, 0)):
        right["actors"][role].update(pos=left["actors"][other]["pos"], yaw_deg=left["actors"][other]["yaw_deg"],
                                      physical_side=left["actors"][other]["physical_side"])
    assert report.mirror_issues(left, right) == []
    right["actors"][0]["yaw_deg"] += 10
    assert any("yaw" in issue for issue in report.mirror_issues(left, right))


def write_native_episode(root):
    """A native time-limit trace, generated without constructing a simulator."""
    initial = arena()
    initial.update(timeout_ms=33)
    initial["initial_state"] = {"actors": copy.deepcopy(initial["actors"]), "game_ms": initial["game_ms"],
                                "runner_generation": 2, "session_epoch": 123, "world_epoch": 2}
    started = copy.deepcopy(initial)
    started.update(started=True, provider="native", start_game_ms=10)
    final = copy.deepcopy(started)
    final.update(game_ms=43, elapsed_game_ms=33, outcome="time_limit", truncated=True)
    rows = []
    for index, (request, response) in enumerate([
        ({"cmd": "reset"}, {"arena": initial}), ({"cmd": "arena_start"}, {"arena": started}),
        ({"cmd": "arena_step"}, {"arena": final, "advanced_substeps": 1}),
        ({"cmd": "arena_status"}, {"arena": final})], 1):
        rows.extend([{"direction": "request", "wall_monotonic": float(index), "value": {"id": index, **request}},
                     {"direction": "response", "wall_monotonic": index+.5, "value": {"id": index, **response}}])
    data = {"ipc_file": rows, "body_events_file": [], "mechanics_file": mechanics()[:2],
            "arena_events_file": [{"kind": "reset_ready", "arena": initial}, {"kind": "start", "arena": started},
                                  {"kind": "terminal", "arena": final}, {"kind": "snapshot", "arena": final}],
            "observations_file": [{"entity_id": actor["entity_id"], "session_epoch": 123, "world_epoch": 2,
                                   "t_game_ms": 43, "self": {"hp": 300}} for actor in final["actors"]]}
    record = {"name": "duel_timeout", "scope": "micro", "provider": "native", "scenario": "native_duel",
              "seed": 17, "side": 0, "runner_pid": 99, "setup": {"distance_cm": 150, "timeout_ms": 33},
              "initial": initial, "final": final, "recurrent_before": None, "recurrent_after": [123, 2, 2]}
    for key, values in data.items():
        name = key+".jsonl"
        (root/name).write_text("".join(json.dumps(value)+"\n" for value in values))
        record[key] = name
    return record


def test_native_time_limit_has_final_observation_and_no_false_win(tmp_path):
    record = write_native_episode(tmp_path)
    result = report.validate_episode(record, tmp_path, {})
    assert result["status"] == "complete", result["issues"]
    assert result["final"]["outcome"] == "time_limit"
    assert result["commands"]["policy_transitions"] == 0
    assert result["reset_wall_s"] == .5
    assert result["total_wall_s"] == 3.5


def test_terminal_numerical_state_must_exist_before_next_world_reset(tmp_path):
    record = write_native_episode(tmp_path)
    path = tmp_path/record["observations_file"]
    rows = report.read_rows(path)
    rows[0]["t_game_ms"] = 10
    path.write_text("".join(json.dumps(row)+"\n" for row in rows))
    result = report.validate_episode(record, tmp_path, {})
    assert any("final native observation absent" in issue for issue in result["issues"])


def test_driver_final_snapshot_cannot_overwrite_real_native_time_limit(tmp_path):
    record = write_native_episode(tmp_path)
    record["final"] = {**record["final"], "outcome": "task_success"}
    result = report.validate_episode(record, tmp_path, {})
    assert result["final"]["outcome"] == "time_limit"
    assert any("copied final" in issue for issue in result["issues"])


@pytest.mark.parametrize("field,value", [("interruptible", False), ("native_eligible", False),
    ("segment_remaining_ms", 50), ("attack_anim", True), ("prehit", True), ("defence", True), ("jump_back", True)])
def test_canonical_reset_cannot_retain_animation_or_native_decision_lock(field, value):
    state = arena()
    state["actors"][0][field] = value
    assert report.clean_reset(state)


def test_repeated_reset_compares_actual_native_body_state_and_clip():
    left, right = arena(), arena()
    right["actors"][0]["body_state"] = 1
    assert report.semantic_initial(left) != report.semantic_initial(right)
    right = arena()
    right["actors"][0]["pose"] = "S_1HATTACK"
    assert report.semantic_initial(left) != report.semantic_initial(right)


def control_loss_inputs():
    initial, final = arena(), arena()
    final.update(provider="scripted", started=True, start_game_ms=10, game_ms=20, elapsed_game_ms=10,
                 outcome="control_lost", reason="target_visibility_lost", truncated=True)
    final["actors"][0].update(owner="scripted", control_generation=3, release_pending=True)
    base = {"schema_version": 1, "event": "body_control_event", "phase": "ownership", "session_epoch": 123,
            "world_epoch": 2, "entity_id": 10, "control_generation": 3, "target_entity_id": 20,
            "provider": "stage5-scripted", "mode": "lockstep", "t_game_ms": 20,
            "owner": "scripted", "release_pending": True, "release_reason": "target_visibility_lost"}
    events = [{**base, "event_seq": 1, "reason": "acquired", "t_game_ms": 10},
              {**base, "event_seq": 2, "reason": "release_pending"}]
    observations = [{"entity_id": 10, "session_epoch": 123, "world_epoch": 2, "t_game_ms": 20,
                     "target": {"entity_id": 20, "visible": False, "current_valid": False}}]
    return {"scope": "paired", "provider": "scripted"}, initial, final, events, observations, mechanics()[:2]


def test_alive_control_loss_is_linked_to_actual_los_ownership_event():
    values = control_loss_inputs()
    assert report.control_loss_issues(*values) == []


@pytest.mark.parametrize("field,value", [("reason", "watchdog_timeout"), ("terminated", True), ("infrastructure", True),
                                      ("winner_entity_id", 10), ("outcome", "task_success")])
def test_control_loss_cannot_hide_infrastructure_or_become_a_win(field, value):
    values = control_loss_inputs()
    values[2][field] = value
    assert report.control_loss_issues(*values)


def test_native_provider_or_micro_task_cannot_claim_control_loss():
    values = control_loss_inputs()
    values[0]["provider"] = "native"
    assert report.control_loss_issues(*values)
    values = control_loss_inputs()
    values[0]["scope"] = "micro"
    assert report.control_loss_issues(*values)


def test_missing_or_wrong_generation_los_event_cannot_claim_control_loss():
    values = control_loss_inputs()
    values[3].pop()
    assert report.control_loss_issues(*values)
    values = control_loss_inputs()
    values[3][1]["control_generation"] = 99
    assert report.control_loss_issues(*values)


def test_native_restored_control_loss_retains_original_acquired_generation():
    values = control_loss_inputs()
    values[2]["actors"][0].update(owner="native", control_generation=0, release_pending=False)
    values[3].append({**values[3][-1], "event_seq": 3, "reason": "native_restored", "owner": "native", "release_pending": False})
    assert report.control_loss_issues(*values) == []


def test_control_loss_stops_native_choices_and_later_policy_commands():
    values = control_loss_inputs()
    values[5].append({"phase": "native_choice", "actor_id": 100, "t_game": 20})
    assert report.control_loss_issues(*values)
    values = control_loss_inputs()
    values[3].append({**values[3][-1], "event_seq": 3, "phase": "accepted", "command_id": 2})
    assert report.control_loss_issues(*values)


def test_control_loss_requires_hidden_numeric_target_and_both_actors_alive():
    values = control_loss_inputs()
    values[4][0]["target"]["visible"] = True
    assert report.control_loss_issues(*values)
    values = control_loss_inputs()
    values[2]["actors"][1]["hp"] = 0
    assert report.control_loss_issues(*values)


def test_control_loss_closes_a_real_started_decision_without_win():
    observations, pairs = decision_inputs()
    result = report.command_evidence(command(phases=("accepted", "started")), observations, pairs,
                                     {"truncated": True, "outcome": "control_lost"})
    assert result["policy_transitions"] == result["control_lost_decisions"] == 1
    assert result["terminal_decisions"] == result["truncated_decisions"] == 0
