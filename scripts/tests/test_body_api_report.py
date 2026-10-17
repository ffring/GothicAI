"""Fail-closed lifecycle/report checks; no engine, remote service or model."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("body_api_report", Path(__file__).parents[1] / "body_api_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
CONFIG = json.loads((Path(__file__).parents[2] / "evals/npc-body/body-api-v1.json").read_text())


def events(action_id=6):
    base = {"schema_version": 1, "event": "body_control_event", "session_epoch": 100123,
            "world_epoch": 2, "control_generation": 3, "entity_id": 10, "target_entity_id": 20, "command_id": 1,
            "goal_version": 1, "action_id": action_id, "observation_seq": 2,
            "execution_id": 4, "t_game_ms": 20, "t_wall_ms": 100, "reason": "segment_started",
            "pose_before": "sequence_idle;start=10;combo=0", "pose_after": "sequence_attack;start=20;combo=1",
            "remaining_lock_game_ms": 200, "segment_started": True}
    return [{**base, "event_seq": index+1, "phase": phase, "t_game_ms": 20+index*100,
             "t_wall_ms": 100+index*100} for index, phase in enumerate(("accepted", "started", "completed"))]


def ipc(pairs):
    rows = []
    for index, (request, response) in enumerate(pairs, 1):
        rows += [{"direction": "request", "value": {"id": index, **request}},
                 {"direction": "response", "value": {"id": index, "schema_version": 1, **response}}]
    return rows


def pure_gate():
    return {"checks": [{"name": name, "actual": expected, "expected": expected, "passed": True}
                       for name, expected in report.PURE_EXPECTED.items()]+[
                           {"name": "mask_repeat_purity", "actual": "101010101010", "expected": "101010101010", "passed": True}]}


def scene():
    return [{"t": 533, "seg": "", "frame": -1, "kind": "body_api_setup", "seed": 17, "t_game_ms": 16294,
             "duration_ms": 600000, "ownership": "native", "actors": [
        {"entity_id": 10, "symbol": "BDT_1003_BANDIT_M", "hp": 1000000, "hp_max": 1000000, "immortal": False},
        {"entity_id": 20, "symbol": "PAL_255_RITTER", "hp": 1000000, "hp_max": 1000000, "immortal": False}]}]


@pytest.mark.parametrize("text", ['{"x": NaN}', '{"x": Infinity}', '{"x": 1e999}', '{"x": 1, "x": 2}'])
def test_strict_json_rejects_nonfinite_and_duplicate_fields(text):
    with pytest.raises(ValueError):
        report.strict_json(text)


def test_native_real_start_and_completion_form_coverage():
    result = report.validate_lifecycle(events())
    assert result["status"] == "complete", result["issues"]
    assert result["completed_action_coverage"] == {6: 1}


def test_queue_consumption_is_not_a_native_execution_start():
    rows = events()
    rows[1]["phase"] = "consumed"
    result = report.validate_lifecycle(rows)
    assert result["status"] == "incomplete"
    assert any("completion without engine" in issue for issue in result["issues"])


def test_animation_name_only_cannot_prove_an_execution_start():
    rows = events()
    rows[1].pop("pose_after")
    rows[1]["animation"] = "S_1HATTACK"
    assert report.validate_lifecycle(rows)["status"] == "incomplete"


def test_duplicate_rejection_does_not_hide_second_start():
    rows = events()
    rows += [{**rows[1], "event_seq": 4}, {**rows[2], "event_seq": 5}]
    result = report.validate_lifecycle(rows)
    assert result["status"] == "incomplete"
    assert any("repeated started" in issue for issue in result["issues"])


def test_continue_requires_existing_execution_identity():
    rows = events(11)
    extra = [{**row, "command_id": 2, "action_id": 0, "event_seq": row["event_seq"]+2} for row in events(0)]
    extra[1]["phase"] = "continued"
    rows = rows[:2]+extra+[{**rows[-1], "event_seq": 6}]
    assert report.validate_lifecycle(rows)["status"] == "complete"
    rows[3]["execution_id"] = 0
    assert report.validate_lifecycle(rows)["status"] == "incomplete"


def test_generations_distinguish_reused_command_id():
    rows = events()+[{**row, "control_generation": 4, "event_seq": row["event_seq"]+3} for row in events()]
    assert report.validate_lifecycle(rows)["command_count"] == 2
    assert report.validate_lifecycle(rows)["status"] == "complete"


def test_missing_native_sequence_is_incomplete():
    rows = events()
    rows[2]["event_seq"] = 4
    assert report.validate_lifecycle(rows)["status"] == "incomplete"


def test_accepted_command_cannot_be_both_cancelled_and_completed():
    rows = events()+[{**events()[-1], "phase": "cancelled", "event_seq": 4}]
    assert report.validate_lifecycle(rows)["status"] == "incomplete"


def test_pending_expiry_proves_no_start():
    rows = events()[:1]+[{**events()[2], "phase": "cancelled", "event_seq": 2, "reason": "expired"}]
    case = {"name": "accepted_expired", "cancel_command_id": 1}
    result = report.validate_case(case, ipc([({"cmd": "body_action", "action_id": 6}, {"accepted": True, "validation_mask": [True]*12})]), rows, CONFIG, scene())
    assert result["status"] == "complete", result["issues"]
    rows.insert(1, {**events()[1], "event_seq": 2})
    rows[2]["event_seq"] = 3
    assert report.validate_case(case, ipc([({"cmd": "body_action"}, {})]), rows, CONFIG)["status"] == "incomplete"


def test_action_acceptance_cannot_override_false_mask():
    raw = ipc([({"cmd": "body_action", "action_id": 6}, {"accepted": True, "validation_mask": [False]*12})])
    result = report.validate_case({"name": "attack_front"}, raw, events(), CONFIG)
    assert any("contradicts" in issue for issue in result["issues"])


def test_mask_purity_checks_repeated_engine_state():
    snapshot = {"actors": [{"entity_id": 10, "purity_state": {"queue": [0]*6, "rng_calls": 0, "loop_next_time": 12}, "action_mask": [True]*12}]}
    raw = ipc([({"cmd": "body_self_test"}, pure_gate())]+[
        ({"cmd": "body_control_status"}, copy.deepcopy(snapshot)) for _ in range(3)])
    assert report.validate_case({"name": "mask_purity"}, raw, events(), CONFIG, scene())["status"] == "complete"
    raw[-1]["value"]["actors"][0]["purity_state"]["rng_calls"] = 1
    assert report.validate_case({"name": "mask_purity"}, raw, events(), CONFIG)["status"] == "incomplete"


def test_ipc_missing_response_rejected():
    with pytest.raises(ValueError, match="unanswered"):
        report.paired_ipc([{"direction": "request", "value": {"id": 1}}])


def test_evidence_cannot_escape_run(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        report.evidence_path(tmp_path, "../elsewhere.jsonl")


def test_blank_or_missing_journals_rejected(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("")
    with pytest.raises(ValueError, match="no records"):
        report.read_jsonl(path)
    path.write_text('{"phase": "accepted"}\n\n')
    with pytest.raises(ValueError, match="empty record"):
        report.read_jsonl(path)


def test_driver_pass_flag_is_not_stage4_evidence(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"suite_scope": "full", "suite_status": "passed"}))
    (tmp_path / "suite.json").write_text(json.dumps({"status": "passed", "full_stage4_evidence": True, "cases": []}))
    result = report.summarize(tmp_path, CONFIG)
    assert result["status"] == "incomplete"
    assert not result["full_stage4_evidence"]
    assert any("archived pre-refactor" in issue for issue in result["issues"])
    assert any("exactly one raw case" in issue for issue in result["issues"])


def test_service_resource_counters_may_change_without_a_restart():
    service = "ActiveState=active\nSubState=running\nMainPID=314\nCPUUsageNSec=20\nMemoryCurrent=90"
    before = {"services": {name: service for name in ("gothic-voxtral", "gothic-voxcpm")}}
    after = copy.deepcopy(before)
    after["services"]["gothic-voxtral"] = service.replace("CPUUsageNSec=20", "CPUUsageNSec=50")
    assert report.service_identity(before) == report.service_identity(after)
    after["services"]["gothic-voxcpm"] = service.replace("MainPID=314", "MainPID=315")
    assert report.service_identity(before) != report.service_identity(after)


def test_unit_absent_from_the_host_is_not_a_stopped_production_service():
    absent = "LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0"
    snapshot = {"services": {name: absent for name in ("gothic-voxtral", "gothic-voxcpm")}}
    assert report.service_identity(snapshot)["gothic-voxtral"]["LoadState"] == "not-found"
    stopped = {"services": {name: absent.replace("not-found", "loaded") for name in ("gothic-voxtral", "gothic-voxcpm")}}
    with pytest.raises(ValueError, match="not active"):
        report.service_identity(stopped)


def test_native_self_test_pass_flag_does_not_replace_computed_gate_results():
    result = pure_gate()
    assert report.validate_pure_gate(result) == []
    result["passed"] = True
    result["checks"][0]["actual"] = "stale_goal"
    assert report.validate_pure_gate(result)
    assert report.validate_pure_gate({"passed": True, "checks": []})


def test_attack_requires_linked_native_hit_and_real_hp_drop():
    raw = ipc([({"cmd": "body_action", "action_id": 6}, {"accepted": True, "validation_mask": [True]*12})])
    mechanics = scene()+[{"phase": "observation_actor_mapping", "actor_id": 1, "observation_entity_id": 10},
                 {"phase": "observation_actor_mapping", "actor_id": 2, "observation_entity_id": 20},
                 {"phase": "hit_attempt", "actor_id": 1, "target_id": 2, "t_game": 150, "result": "applied"},
                 {"phase": "damage", "actor_id": 2, "target_id": 1, "t_game": 150,
                  "damage_kind": "melee", "hp_before": 300, "hp_after": 280}]
    result = report.validate_case({"name": "attack_front"}, raw, events(), CONFIG, mechanics)
    assert result["status"] == "complete", result["issues"]
    assert result["attack_mechanics"] == {"applied_hits": 1, "target_hp_loss": 20}
    mechanics[-1]["hp_after"] = 300
    assert report.validate_case({"name": "attack_front"}, raw, events(), CONFIG, mechanics)["status"] == "incomplete"


def test_other_actor_damage_does_not_prove_scripted_attack_execution():
    raw = ipc([({"cmd": "body_action", "action_id": 6}, {"accepted": True, "validation_mask": [True]*12})])
    mechanics = scene()+[{"phase": "observation_actor_mapping", "actor_id": 1, "observation_entity_id": 10},
                 {"phase": "observation_actor_mapping", "actor_id": 2, "observation_entity_id": 20},
                 {"phase": "hit_attempt", "actor_id": 3, "target_id": 4, "t_game": 150, "result": "applied"},
                 {"phase": "damage", "actor_id": 4, "target_id": 3, "t_game": 150,
                  "damage_kind": "melee", "hp_before": 300, "hp_after": 280}]
    assert report.validate_case({"name": "attack_front"}, raw, events(), CONFIG, mechanics)["status"] == "incomplete"


def test_scene_activity_failure_cannot_be_ignored_by_driver_pass_flag():
    assert report.validate_scene(scene()) == []
    assert report.validate_scene(scene()+[{"kind": "body_observe_error", "reason": "native actor unexpectedly frozen"}])
    assert report.validate_scene([])
    wrong = scene()
    wrong[0]["actors"][1]["immortal"] = True
    assert report.validate_scene(wrong)


def test_native_scene_discriminator_is_kind_not_body_controller_event():
    rows = scene()
    assert report.validate_scene(rows) == []
    rows[0]["event"] = rows[0].pop("kind")
    assert report.validate_scene(rows)


def test_actual_occlusion_fixture_geometry_requires_native_search_count_and_rays():
    owner = {**events()[0], "phase": "ownership", "reason": "acquired", "command_id": 0, "execution_id": 0}
    geometry = {"center_ray_hit": True, "head_ray_hit": True, "melee_line_hit": True,
                "target_visible": False, "search_attempts": 39}
    raw = ipc([({"cmd": "body_fixture", "kind": "occluded", "entity_id": 10}, {"ok": True, "fixture_geometry": geometry}),
               ({"cmd": "body_action", "action_id": 6, "command_id": 1}, {"accepted": False, "reason": "target_invalid"})])
    case = {"name": "collision_mask", "expected_reason": "target_invalid"}
    assert report.validate_case(case, raw, [owner], CONFIG, scene())["status"] == "complete"
    geometry["center_ray_hit"] = False
    assert report.validate_case(case, raw, [owner], CONFIG, scene())["status"] == "incomplete"
