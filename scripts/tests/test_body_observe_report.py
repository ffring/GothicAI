"""Stage 3 schema/report regressions; no engine or model is launched."""
import importlib.util
import copy
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("body_observe_report", Path(__file__).parents[1] / "body_observe_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


@pytest.mark.parametrize("text", ['{"x": NaN}', '{"x": Infinity}', '{"x": 1e999}', '{"x": 1, "x": 2}'])
def test_strict_json_rejects_nonfinite_and_duplicate_fields(text):
    with pytest.raises(ValueError):
        report.strict_json(text)


def test_boolean_is_not_a_numeric_feature():
    assert not report.number(True)
    assert not report.integer(False)
    assert report.number(1.25)
    assert report.integer(3, 1)


def test_p95_uses_nearest_rank_not_a_too_low_interpolation():
    assert report.stats(list(range(1, 21))) == {"n": 20, "min": 1, "median": 10.5, "p95": 19, "max": 20}


def test_raw_stream_missing_or_blank_evidence_rejected(tmp_path):
    path = tmp_path / "observations.jsonl"
    path.write_text("")
    with pytest.raises(ValueError, match="no records"):
        report.read_jsonl(path)
    path.write_text('{"valid": true}\n\n')
    with pytest.raises(ValueError, match="empty record"):
        report.read_jsonl(path)


def test_manifest_cannot_redirect_raw_evidence_outside_run(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        report.evidence_path(tmp_path, "../another-run.jsonl")


def batch_fixture():
    def row(seq, actor, time):
        return {"kind": "body_observation", "schema_version": 1, "phase": "post_simulation_post_animation",
                "session_epoch": 1, "world_epoch": 1, "control_generation": 0,
                "observation_seq": seq, "generation": 1, "entity_id": actor,
                "script_symbol": 999, "t_game_ms": time, "dt_game_ms": 0 if time == 33 else 50,
                "sim_step": 1 if time == 33 else 3, "periodic": True, "event_only": False,
                "state_changed": True, "motion_discontinuity": False,
                "can_decide": False, "native_decision_eligible": False, "control_allowed": False,
                "action_mask_valid": False, "availability_mask": [True]+[False]*11,
                "mask_kind": "conservative_native_availability",
                "self": {"position_world_cm": [0, 0, 0], "hp": 300, "hp_max": 300, "hp_fraction": 1, "yaw_rad": 0,
                         "velocity_valid": time != 33, "linear_velocity_cm_s": None if time == 33 else [0, 0, 0],
                         "skill_valid": False, "skill_talent": 0, "skill": 0,
                         **{key: 0 for key in ("weapon_state", "weapon_symbol", "body_state_raw", "body_state_base", "body_flags", "choice_id")},
                         **{key: False for key in ("dead", "unconscious", "down", "in_air", "swim", "dive", "attack_anim", "prehit", "defence_active",
                                                   "jumpback_active", "def_window", "par_window", "interruptible")}},
                "target": {"entity_id": 99, "present": True, "visible": False, "current_valid": False,
                           "position_world_cm": None, "linear_velocity_cm_s": None,
                           "hp": None, "last_known_position_world_cm": [1, 0, 0], "last_seen_game_ms": 0, "last_known_age_ms": time},
                "local_obstacles": {"ray_range_cm": 500,
                                    "rays": [{"angle_rad": i/16, "distance_cm": 500, "hit": False} for i in range(16)]},
                "events": [], "builder_wall_us": 200}
    return {"kind": "body_observations", "schema_version": 1,
            "session_epoch": 1, "world_epoch": 1, "control_generation": 0,
            "phase": "post_simulation_post_animation", "generation": 1,
            "sample_period_ms": 50, "queue_capacity": 8, "dropped_total": 0, "dropped_since_drain": 0,
            "event_dropped_total": 0,
            "observations": [row(1, 11, 33), row(2, 12, 33), row(3, 11, 83), row(4, 12, 83)]}


def test_distinct_runtime_instances_can_share_script_type():
    result = report.validate_stream([batch_fixture()])
    assert result["status"] == "complete", result["issues"]
    assert result["actor_ids_by_generation"] == {"1": [11, 12]}
    assert result["periodic_interval_game_ms"]["p95"] == 50


def test_hidden_current_target_state_leak_fails_even_with_valid_json():
    batch = batch_fixture()
    batch["observations"][2]["target"]["position_world_cm"] = [10, 0, 0]
    result = report.validate_stream([batch])
    assert result["status"] == "incomplete"
    assert any("hidden target leaks current" in issue for issue in result["issues"])


def test_gap_must_be_explained_by_engine_drop_counter():
    batch = batch_fixture()
    for row in batch["observations"]:
        row["observation_seq"] += 1
    assert report.validate_stream([batch])["status"] == "incomplete"
    batch["dropped_total"] = batch["dropped_since_drain"] = 1
    result = report.validate_stream([batch])
    assert result["status"] == "complete", result["issues"]
    assert result["sequence_gaps"] == result["dropped_total"] == 1


def test_same_generation_replayed_batch_is_not_new_data():
    batch = batch_fixture()
    result = report.validate_stream([batch, copy.deepcopy(batch)])
    assert result["status"] == "incomplete"
    assert any("duplicate/out-of-order" in issue for issue in result["issues"])


def test_event_deadline_checks_real_source_tick():
    batch = batch_fixture()
    batch["observations"][2]["events"] = [{"event_id": 1, "source_game_ms": 33,
                                          "source_sim_step": 1, "perspective": "self", "code": 1, "source_entity_id": 11}]
    result = report.validate_stream([batch])
    assert result["status"] == "incomplete"
    assert any("after next sample tick" in issue for issue in result["issues"])


def test_bad_ray_cannot_be_blessed_by_positive_suite_status():
    batch = batch_fixture()
    batch["observations"][0]["local_obstacles"]["rays"][3]["distance_cm"] = 501
    result = report.validate_stream([batch])
    assert result["status"] == "incomplete"


def test_velocity_uses_actual_previous_same_phase_snapshot_and_dt():
    batch = batch_fixture()
    batch["observations"][2]["self"]["linear_velocity_cm_s"] = [100, 0, 0]
    result = report.validate_stream([batch])
    assert result["status"] == "incomplete"
    assert any("velocity does not derive" in issue for issue in result["issues"])


def privacy_fixture():
    batch = batch_fixture()
    base = batch["observations"][0]
    rows, probes, truth_samples = [], [], []
    cases = [("visible", True, [1, 0, 0], 300),
             ("occluded", False, [10, 0, 0], 300),
             ("hidden_move", False, [60, 0, 0], 283),
             ("reappear", True, [1, 0, 0], 283)]
    for index, (case, visible, position, hp) in enumerate(cases):
        time = 33+800*index
        probes.append({"kind": "body_observe_probe", "case": case, "t_game_ms": time,
                       "actor_id": 11, "target_id": 99, "visible": visible,
                       "center_ray_hit": not visible, "head_ray_hit": not visible,
                       "truth_target_hp": hp, "truth_target_position_cm": position,
                       "truth_self_hp": 300, "truth_self_yaw_rad": 0, "truth_self_position_cm": [0, 0, 0]})
        row = copy.deepcopy(base)
        row["t_game_ms"] = time+4
        row["sim_step"] = index+1
        row["target"].update(visible=visible, current_valid=visible,
                              position_world_cm=position if visible else None,
                              hp=hp if visible else None,
                              last_known_position_world_cm=[1, 0, 0],
                              last_known_age_ms=0 if visible else 800*index)
        rows.append(row)
        truth_samples.append({**probes[-1], "kind": "body_observe_probe_sample", "t_game_ms": row["t_game_ms"],
                              "sim_step": row["sim_step"]})
    batch["observations"] = rows
    return [batch], probes+truth_samples


def test_real_privacy_oracle_checks_hidden_position_and_hp_mutation():
    batches, probes = privacy_fixture()
    assert report.validate_privacy(batches, probes)["status"] == "complete"
    batches[0]["observations"][2]["target"]["last_known_position_world_cm"] = [60, 0, 0]
    result = report.validate_privacy(batches, probes)
    assert result["status"] == "incomplete"
    assert any("followed unseen" in issue for issue in result["issues"])


def test_a_suite_pass_boolean_does_not_replace_raw_stage3_evidence(tmp_path):
    suite = {"status": "passed", "full_stage3_evidence": True, "required_gate_failures": [], "runs": []}
    manifest = {"suite_status": "passed", "suite_scope": "full"}
    result = report.summarize(suite, manifest, tmp_path)
    assert result["status"] == "incomplete"
    assert any(issue.startswith("passive_10min:") for issue in result["issues"])


def activity_fixture(tmp_path, *, omit_hit=False):
    rows = []
    seq = 0
    for actor in range(1, 5):
        seq += 1
        rows.append({"kind": "body_combat", "phase": "observation_actor_mapping", "seq": seq,
                     "actor_id": actor+100, "observation_entity_id": actor, "t_game": 100})
    rows.append({"kind": "body_observe_setup", "t_game_ms": 100, "duration_ms": 120000,
                 "ownership": "native", "actors": [{"entity_id": actor, "immortal": False} for actor in range(1, 5)]})
    for bucket in range(2):
        for actor in range(1, 5):
            for phase in ("native_choice", "native_attempt", "hit_attempt", "damage"):
                if omit_hit and actor == 4 and bucket == 1 and phase == "hit_attempt":
                    continue
                seq += 1
                rows.append({"kind": "body_combat", "seq": seq, "actor_id": actor+100,
                             "phase": phase, "t_game": 101+bucket*60000, "accepted": True,
                             "hp_before": 300, "hp_after": 295})
    rows.append({"kind": "body_observe_end", "elapsed_ms": 120000, "actors": 4})
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(row)+"\n" for row in rows))
    return path


def test_every_duplicate_type_actor_has_native_combat_in_every_time_bucket(tmp_path):
    result = report.validate_activity(activity_fixture(tmp_path), 120000, [1, 2, 3, 4])
    assert result["status"] == "complete", result["issues"]
    assert [row["entity_id"] for row in result["actors"]] == [1, 2, 3, 4]
    assert all(row["actual_hp_loss"] == 10 for row in result["actors"])


def test_native_ai_flag_cannot_hide_actor_that_stops_attacking(tmp_path):
    result = report.validate_activity(activity_fixture(tmp_path, omit_hit=True), 120000, [1, 2, 3, 4])
    assert result["status"] == "incomplete"
    assert "actor 4 bucket 1: no hit_attempt" in result["issues"]
