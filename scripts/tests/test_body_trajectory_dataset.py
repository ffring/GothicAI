"""Dataset integrity tests over recorded JSON fixtures; no simulation or model."""
from __future__ import annotations

from copy import deepcopy
import json
import hashlib
from pathlib import Path
import sys
import weakref

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_trajectory_dataset import (ACTIONS, DatasetValidationError, OrderedSequenceLoader, FullEpisodeLoader,
                                     actor_features, build_dataset, read_json, sha256, validate_episode,
                                     periodic_model_view, PERIODIC_FEATURE_VERSION, canonical_feature_names, pack_features,
                                     policy_action_mask, merge_teacher_registries, registry_sha256)
from body_trajectory_recorder import TrajectoryRecorder, approve_scripted_demonstration
import body_demonstration_teacher as demo_teacher
import body_combat_teacher as combat_teacher
import body_trajectory_dataset as dataset_module


def observation(sequence, time, *, phase="post_simulation_post_animation", generation=0, hp=.5):
    return {"schema_version": 1, "session_epoch": 7, "world_epoch": 9, "control_generation": generation,
            "entity_id": 11, "observation_seq": sequence, "t_game_ms": time, "dt_game_ms": 50,
            "sim_step": time // 33,
            "periodic": phase == "post_simulation_post_animation",
            "phase": phase, "self": {"hp_fraction": hp, "queued_primitive": 99, "choice_id": 777},
            "target": {"current_valid": True, "hp_fraction": .5, "relative_position_cm": [150, 0, 0]},
            "can_decide": phase == "pre_native_primitive", "native_decision_eligible": True,
            "action_mask_valid": phase == "pre_native_primitive", "action_mask": [True]*12,
            "availability_mask": [True]*12,
            "mask_kind": "shared_melee_resolver",
            "events": [], "last_result": {"valid": False}, "local_obstacles": {"rays": []}}


def episode(path, *, seed=17, episode_id=None, ood=False, generation=0, pending=False, diagnostic=False, hp=.5):
    path.mkdir()
    episode_id = episode_id or path.name
    epoch = int(hashlib.sha256(path.name.encode()).hexdigest()[:8], 16)
    common = {"episode_id": episode_id, "session_epoch": 7, "world_epoch": epoch,
              "control_generation": generation, "entity_id": 11}
    action = None if diagnostic else 6
    records = [
        {**common, "kind": "observation", "observation_seq": 1, "decision_valid": not diagnostic,
         "bc_label": None if diagnostic else 6,
         "observation": observation(88, 100, phase="pre_native_primitive", generation=generation, hp=hp)},
        {**common, "kind": "decision", "decision_id": "native-1", "execution_id": 51, "observation_seq": 1,
         "t_game_ms": 100, "source": "native", "action_id": action, "expert": not diagnostic,
         "accepted": True, "eligible": not diagnostic, "decision_valid": not diagnostic, "action_mask": [True]*12},
        {**common, "kind": "execution", "decision_id": "native-1", "execution_id": 51, "action_id": action,
         "phase": "accepted", "t_game_ms": 100},
        {**common, "kind": "execution", "decision_id": "native-1", "execution_id": 51, "action_id": action,
         "phase": "started", "t_game_ms": 100}]
    if not pending:
        records += [
            {**common, "kind": "execution", "decision_id": "native-1", "execution_id": 51,
             "action_id": action, "phase": "consumed", "t_game_ms": 100},
            {**common, "kind": "execution", "decision_id": "native-1", "execution_id": 51,
             "action_id": action, "phase": "completed", "t_game_ms": 133}]
    records += [
        {**common, "kind": "observation", "observation_seq": 2, "decision_valid": False, "bc_label": None,
         "observation": observation(99, 150, generation=generation, hp=hp)},
        {**common, "kind": "transition", "decision_id": "native-1", "observation_seq": 1,
         "next_observation_seq": 2, "duration_ms": 50,
         "execution_status": "episode_ended" if pending else "completed", "uncompleted": pending,
         "usable_for_bc": not diagnostic and not pending, "terminated": False, "truncated": True, "infrastructure": False}]
    for index, record in enumerate(records, 1):
        record["record_seq"] = index
    for record in records:
        if record["kind"] == "observation":
            record["observation"]["world_epoch"] = epoch
    manifest = {"schema_version": 1, "episode_id": episode_id, "session_epoch": 7, "world_epoch": epoch,
                "actors": [{"entity_id": 11}], "group": {"setup_family": "duel-baseline", "seed": seed},
                "ood": ood, "feature_version": 1, "action_version": 1,
                "initial": {"game_ms": 100},
                "writer_closed": True,
                "conditions": [], "final": {"t_game_ms": 150, "terminated": False, "truncated": True,
                                            "infrastructure": False, "reason": "time_limit"},
                "source_integrity": {"gap_count": 0, "dropped_observations": 0, "dropped_events": 0,
                                     "dropped_capture_records": 0, "reset_discarded_total": 0,
                                     "capture_failures_total": 0, "capture_failed": False,
                                     "missed_period_buckets": 0, "invalid_numeric_rows": 0, "dropped_control_events": 0}, "source_files": []}
    save(path, manifest, records)
    return path


def save(path, manifest, records):
    raw = []
    observations = {}
    times = {}
    for record in records:
        if record["kind"] == "transition":
            continue
        sequence = len(raw)+1
        record.update(raw_capture_seq=sequence, raw_record_seq=sequence, capture_generation=1)
        common = {key: record[key] for key in ("session_epoch", "world_epoch", "entity_id", "control_generation")}
        common.update(schema_version=1, record_seq=sequence, capture_generation=1)
        if record["kind"] == "observation":
            record["t_game_ms"] = record["observation"]["t_game_ms"]
            record["sequence_dt_game_ms"] = record["t_game_ms"]-times.get(record["entity_id"], manifest["initial"]["game_ms"])
            record["raw_dt_game_ms"] = record["observation"]["dt_game_ms"]
            times[record["entity_id"]] = record["t_game_ms"]
            source = "native_capture" if record["observation"]["phase"] == "pre_native_primitive" else "periodic"
            record.update(raw_observation_seq=record["observation"]["observation_seq"], raw_observation_source=source)
            observations[(record["entity_id"], record["observation_seq"])] = sequence
            raw.append({**common, "kind": "observation", "source": source, "t_game_ms": record["t_game_ms"],
                        "observation": deepcopy(record["observation"])})
        elif record["kind"] == "decision":
            reference = observations[(record["entity_id"], record["observation_seq"])]
            record.update(raw_decision_id=1, raw_observation_record_seq=reference,
                          effective_primitive=0 if record["action_id"] is None else 4, raw_intent=4)
            raw.append({**common, "kind": "decision", "source": "native", "decision_id": 1,
                        "observation_record_seq": reference, "execution_id": record["execution_id"],
                        "t_game_ms": record["t_game_ms"], "effective_primitive": record["effective_primitive"], "native_intent": 4,
                        "action_id": record["action_id"], "action_mask": deepcopy(record["action_mask"]),
                        "decision_valid": record["decision_valid"]})
        else:
            record.update(source="native", effective_primitive=0 if record["action_id"] is None else 4)
            raw.append({**common, "kind": "execution", "source": "native", "decision_id": 1,
                        "execution_id": record["execution_id"], "t_game_ms": record["t_game_ms"],
                        "phase": record["phase"], "effective_primitive": record["effective_primitive"],
                        **{key: record[key] for key in ("reason", "physical_cancelled", "execution_incomplete", "segment_started", "pose_before", "pose_after")
                           if key in record}})
    capture = path / "raw-trajectory.jsonl"
    capture.write_text("".join(json.dumps(record)+"\n" for record in raw))
    manifest["capture_file"] = "raw-trajectory.jsonl"
    manifest["source_integrity"].update(capture_generation=1, first_capture_record_seq=raw[0]["record_seq"],
                                        last_capture_record_seq=raw[-1]["record_seq"], capture_records=len(raw),
                                        capture_kind_counts={kind: sum(row["kind"] == kind for row in raw)
                                                             for kind in {row["kind"] for row in raw}})
    manifest["source_files"] = [item for item in manifest["source_files"] if item["path"] != "raw-trajectory.jsonl"]
    manifest["source_files"].append({"path": "raw-trajectory.jsonl", "sha256": sha256(capture)})
    packets = path / "capture-packets.jsonl"
    packets.write_text(json.dumps({"packet_seq": 1, "stats": {"capture_generation": 1, "capture_failed": False,
                       "capture_failures_total": 0, "dropped_total": 0, "dropped_since_drain": 0,
                       "reset_discarded_total": 0, "queue_size": 0, "record_seq_high_watermark": raw[-1]["record_seq"]},
                       "event_count": len(raw), "first_record_seq": raw[0]["record_seq"], "last_record_seq": raw[-1]["record_seq"]})+"\n")
    manifest["capture_packets_file"] = "capture-packets.jsonl"
    manifest["source_integrity"]["capture_packets"] = 1
    manifest["source_files"] = [item for item in manifest["source_files"] if item["path"] != "capture-packets.jsonl"]
    manifest["source_files"].append({"path": "capture-packets.jsonl", "sha256": sha256(packets)})
    integrity = path / "source-integrity.json"
    integrity.write_text(json.dumps({"runner_status": {"body_observation": {"dropped_total": 0, "event_dropped_total": 0,
                                      "invalid_numeric_rows": 0, "missed_period_buckets": 0}}, "control_events": {"dropped_total": 0}})+"\n")
    manifest["source_integrity_file"] = "source-integrity.json"
    manifest["source_files"] = [item for item in manifest["source_files"] if item["path"] != "source-integrity.json"]
    manifest["source_files"].append({"path": "source-integrity.json", "sha256": sha256(integrity)})
    manifest["counts"] = {kind: sum(record["kind"] == kind for record in records)
                          for kind in ("observation", "decision", "execution", "transition")}
    (path / "manifest.json").write_text(json.dumps(manifest)+"\n")
    (path / "records.jsonl").write_text("".join(json.dumps(record)+"\n" for record in records))


def load(path):
    return read_json(path / "manifest.json"), [json.loads(line) for line in (path / "records.jsonl").read_text().splitlines()]


def test_exact_teacher_link_is_distinct_from_periodic_body_sequence(tmp_path):
    item = validate_episode(episode(tmp_path / "first"))
    assert item.observations[(11, 1)]["observation"]["observation_seq"] == 88
    assert item.summary["expert_labels"] == item.summary["usable_bc_labels"] == 1
    assert item.summary["accepted"] == item.summary["started"] == item.summary["completed"] == 1


@pytest.mark.parametrize("masked", [False, True])
def test_accepted_scripted_action_checks_mask_even_without_bc_label(tmp_path, masked):
    path = tmp_path / "scripted"
    path.mkdir()
    initial = {"session_epoch": 7, "world_epoch": 9, "game_ms": 100, "actors": [{"entity_id": 11}]}
    recorder = TrajectoryRecorder(path, episode_id="scripted", initial=initial,
                                  metadata={"group": {"setup_family": "unit", "seed": 17}, "ood": False})
    observed = observation(88, 100, generation=4)
    observed.update(can_decide=True, action_mask_valid=True)
    observed["action_mask"][6] = observed["availability_mask"][6] = not masked
    after = observation(99, 150, generation=4)
    after.update(can_decide=True, action_mask_valid=True)
    common = {"schema_version": 1, "capture_generation": 1, "session_epoch": 7, "world_epoch": 9,
              "entity_id": 11, "control_generation": 4, "sim_step": 3}
    event = {"session_epoch": 7, "world_epoch": 9, "entity_id": 11, "control_generation": 4,
             "command_id": 1, "execution_id": 51, "observation_seq": 88,
             "action_id": 6, "effective_primitive": 4, "phase": "accepted", "t_game_ms": 100, "reason": ""}
    raw = [{**common, "record_seq": 1, "kind": "observation", "source": "periodic",
            "t_game_ms": 100, "observation": observed}]
    for sequence, phase, time in ((2, "accepted", 100), (3, "started", 100), (4, "completed", 133)):
        raw.append({**common, "record_seq": sequence, "kind": "control", "source": "scripted", "t_game_ms": time,
                    "body_control_event": {**event, "phase": phase, "t_game_ms": time}})
    raw.append({**common, "record_seq": 5, "kind": "observation", "source": "periodic", "t_game_ms": 150,
                "observation": after})
    final = {"game_ms": 150, "terminated": False, "truncated": True, "infrastructure": False,
             "outcome": "time_limit", "reason": "time_limit"}
    recorder.ingest({"capture_generation": 1, "events": raw, "dropped_total": 0, "dropped_since_drain": 0,
                     "reset_discarded_total": 0, "capture_failed": False, "capture_failures_total": 0,
                     "queue_size": 0, "record_seq_high_watermark": 5}, final=final)
    recorder.finish(final, source_files=[], runner_status={"body_observation": {"dropped_total": 0,
                    "event_dropped_total": 0, "invalid_numeric_rows": 0, "missed_period_buckets": 0}},
                    control_events={"dropped_total": 0})
    # Both fixtures contain a real converted acceptance and matching source SHA;
    # neither has an expert/BC label, which must not bypass action validation.
    if masked:
        with pytest.raises(DatasetValidationError, match="outside recorded mask"):
            validate_episode(path)
    else:
        validated = validate_episode(path)
        assert validated.summary["accepted"] == validated.summary["started"] == 1
        assert validated.summary["expert_labels"] == validated.summary["usable_bc_labels"] == 0


def declared_scripted_approach(path, *, physical_progress=True, certificate_after_command=False,
                              teacher_module=demo_teacher, received_damage=False):
    path.mkdir()
    teacher = teacher_module
    scenario = "native_duel" if teacher.TEACHER_ID == combat_teacher.TEACHER_ID else "reset_stress"
    config_file = path / "teacher-config.json"
    config_file.write_text(json.dumps({"teacher_id": teacher.TEACHER_ID, "schema_version": 1})+"\n")
    code_file = path / "teacher-source.py"
    code_file.write_bytes(Path(teacher.__file__).read_bytes())
    registry = {"schema_version": 1, "teachers": {teacher.TEACHER_ID: {
        "module": Path(teacher.__file__).stem, "provider": teacher.PROVIDER,
        "code_sha256": sha256(code_file), "config_sha256": sha256(config_file),
        "controlled_role": "primary", "allowed_actions": [1], "allowed_scenarios": [scenario]}}}
    initial = {"session_epoch": 7, "world_epoch": 9, "game_ms": 100, "primary_entity_id": 11,
               "opponent_entity_id": 12, "actors": [{"entity_id": 11}]}
    before = observation(88, 100, generation=4)
    before.update(can_decide=True, action_mask_valid=True)
    before["self"].update(hp=300, down=False, position_world_cm=[0, 0, 0], yaw_rad=0)
    before["target"].update(entity_id=12, visible=True, attack_anim=False, prehit=False, in_w_range=False,
                            fight_distance_cm=600, g_range_cm=300, focus_5=True, focus_30=True, melee_line_clear=True)
    after = deepcopy(before)
    after.update(t_game_ms=733, observation_seq=99, sim_step=22)
    after["self"]["position_world_cm"] = [450, 0, 0] if physical_progress else [0, 0, 0]
    if received_damage:
        after["self"]["hp"] = 230
    after["target"].update(fight_distance_cm=150 if physical_progress else 600, in_w_range=physical_progress)
    certificate_kwargs = {"command_id": 1, "certificate_seq": 1,
                          "wall_monotonic": 3.0 if certificate_after_command else 1.0}
    if teacher.TEACHER_ID == combat_teacher.TEACHER_ID:
        certificate = teacher.select_certificate(before, 1, [], **certificate_kwargs)
    else:
        certificate = teacher.select_certificate(before, 1, {"goal": "defensive_cycle", "last_completed_action": None,
                                                "completed_command_ids": [], "variant_index": 0}, [], **certificate_kwargs)
    assert certificate is not None
    certificate_file = path / "teacher-certificates.jsonl"
    certificate_file.write_text(json.dumps(certificate)+"\n")
    recorder = TrajectoryRecorder(path, episode_id="new-approved-approach", initial=initial,
        metadata={"group": {"setup_family": "unit", "seed": 17}, "ood": False, "scenario": "reset_stress",
                  "config_sha256": sha256(config_file), "teacher_provenance": {"teacher_id": teacher.TEACHER_ID,
                  "code_file": code_file.name, "config_file": config_file.name, "certificates_file": certificate_file.name}})
    recorder.metadata["scenario"] = scenario
    common = {"schema_version": 1, "capture_generation": 1, "session_epoch": 7, "world_epoch": 9,
              "entity_id": 11, "control_generation": 4, "sim_step": 3}
    event = {"session_epoch": 7, "world_epoch": 9, "entity_id": 11, "control_generation": 4,
             "command_id": 1, "execution_id": 51, "observation_seq": 88, "provider": teacher.PROVIDER,
             "action_id": 1, "effective_primitive": 2, "phase": "accepted", "t_game_ms": 100, "reason": ""}
    raw = [{**common, "record_seq": 1, "kind": "observation", "source": "periodic", "t_game_ms": 100, "observation": before}]
    for sequence, phase, time in ((2, "accepted", 100), (3, "started", 100), (4, "completed", 700)):
        raw.append({**common, "record_seq": sequence, "kind": "control", "source": "scripted", "t_game_ms": time,
                    "body_control_event": {**event, "phase": phase, "t_game_ms": time}})
    raw.append({**common, "record_seq": 5, "kind": "observation", "source": "periodic", "t_game_ms": 733, "observation": after})
    final = {"game_ms": 733, "terminated": False, "truncated": True, "infrastructure": False, "outcome": "time_limit", "reason": "time_limit"}
    recorder.ingest({"capture_generation": 1, "events": raw, "dropped_total": 0, "dropped_since_drain": 0,
                     "reset_discarded_total": 0, "capture_failed": False, "capture_failures_total": 0,
                     "queue_size": 0, "record_seq_high_watermark": 5}, final=final)
    mechanics = [{"phase": "observation_actor_mapping", "actor_id": 1, "observation_entity_id": 11}]
    if received_damage:
        mechanics.append({"phase": "damage", "damage_kind": "melee", "actor_id": 1, "target_id": 2,
                          "t_game": 200, "hp_before": 300, "hp_after": 230})
    (path / "events.jsonl").write_text("".join(json.dumps(row)+"\n" for row in mechanics))
    (path / "arena-events.jsonl").write_text(json.dumps({"kind": "reset_ready", "arena": initial})+"\n")
    (path / "ipc.jsonl").write_text(json.dumps({"direction": "request", "wall_monotonic": 2.0,
           "value": {"cmd": "body_action", "entity_id": 11, "command_id": 1, "control_generation": 4,
                     "provider": teacher.PROVIDER, "observation_seq": 88}})+"\n")
    recorder.finish(final, source_files=[code_file, config_file, certificate_file, path / "events.jsonl", path / "ipc.jsonl",
                                      path / "arena-events.jsonl"],
                    runner_status={"body_observation": {"dropped_total": 0, "event_dropped_total": 0,
                    "invalid_numeric_rows": 0, "missed_period_buckets": 0}}, control_events={"dropped_total": 0})
    return registry


def test_scripted_expert_requires_registered_causal_teacher_and_real_action_effect(tmp_path):
    path = tmp_path / "new-demonstration"
    registry = declared_scripted_approach(path)
    original_hash = sha256(path / "records.jsonl")
    assert validate_episode(path).summary["expert_labels"] == 0
    manifest = approve_scripted_demonstration(path, registry)
    assert manifest["records_file"] == "records-approved.jsonl"
    assert sha256(path / "records.jsonl") == original_hash
    assert validate_episode(path, teacher_registry=registry).summary["usable_bc_labels"] == 1
    with pytest.raises(DatasetValidationError, match="declared teacher"):
        validate_episode(path)


def test_episode_success_or_completion_cannot_substitute_for_physical_expert_effect(tmp_path):
    path = tmp_path / "no-progress"
    registry = declared_scripted_approach(path, physical_progress=False)
    manifest = approve_scripted_demonstration(path, registry)
    assert manifest["scripted_expert_approval"]["approved"] == []
    assert validate_episode(path, teacher_registry=registry).summary["usable_bc_labels"] == 0


def test_scripted_teacher_certificate_cannot_be_produced_after_command(tmp_path):
    path = tmp_path / "future-certificate"
    registry = declared_scripted_approach(path, certificate_after_command=True)
    with pytest.raises(DatasetValidationError, match="produced after"):
        approve_scripted_demonstration(path, registry)
    assert not (path / "records-approved.jsonl").exists()


def test_unregistered_code_and_stage6_diagnostics_cannot_be_promoted(tmp_path):
    path = tmp_path / "unknown-code"
    registry = declared_scripted_approach(path)
    registry["teachers"][demo_teacher.TEACHER_ID]["code_sha256"] = "0"*64
    with pytest.raises(DatasetValidationError, match="not in declared registry"):
        approve_scripted_demonstration(path, registry)
    old = episode(tmp_path / "stage6-native")
    with pytest.raises(DatasetValidationError, match="collection-time teacher provenance"):
        approve_scripted_demonstration(old, registry)


def test_new_combat_teacher_binding_preserves_old_defensive_teacher_semantics(tmp_path):
    combat = tmp_path / "new-combat-approach"
    combat_registry = declared_scripted_approach(combat, teacher_module=combat_teacher, received_damage=True)
    approve_scripted_demonstration(combat, combat_registry)
    assert validate_episode(combat, teacher_registry=combat_registry).summary["usable_bc_labels"] == 1
    defensive = tmp_path / "old-defensive-approach"
    defensive_registry = declared_scripted_approach(defensive, received_damage=True)
    approved = approve_scripted_demonstration(defensive, defensive_registry)
    assert approved["scripted_expert_approval"]["approved"] == []


def test_teacher_id_cannot_select_another_allowlisted_verifier(tmp_path):
    path = tmp_path / "swapped-module"
    registry = declared_scripted_approach(path)
    registry["teachers"][demo_teacher.TEACHER_ID]["module"] = "body_combat_teacher"
    with pytest.raises(DatasetValidationError, match="unapproved teacher verifier"):
        approve_scripted_demonstration(path, registry)


def test_composite_registry_preserves_both_original_approval_contexts(tmp_path):
    old_path, new_path = tmp_path / "immutable-old", tmp_path / "new-combat"
    old_registry = declared_scripted_approach(old_path)
    new_registry = declared_scripted_approach(new_path, teacher_module=combat_teacher)
    approve_scripted_demonstration(old_path, old_registry)
    approve_scripted_demonstration(new_path, new_registry)
    old_manifest = old_path / "trajectory-manifest.json"
    old_hash = sha256(old_manifest)
    merged = merge_teacher_registries([old_registry, new_registry])
    assert registry_sha256(merged) not in (registry_sha256(old_registry), registry_sha256(new_registry))
    assert validate_episode(old_path, teacher_registry=merged).summary["usable_bc_labels"] == 1
    assert validate_episode(new_path, teacher_registry=merged).summary["usable_bc_labels"] == 1
    assert sha256(old_manifest) == old_hash
    assert read_json(old_manifest)["scripted_expert_approval"]["registry_sha256"] == registry_sha256(old_registry)


def test_composite_registry_rejects_changed_entry_missing_or_forged_context(tmp_path):
    old_path, new_path = tmp_path / "immutable-old", tmp_path / "new-combat"
    old_registry = declared_scripted_approach(old_path)
    new_registry = declared_scripted_approach(new_path, teacher_module=combat_teacher)
    approve_scripted_demonstration(old_path, old_registry)
    merged = merge_teacher_registries([old_registry, new_registry])
    changed = deepcopy(merged)
    # Replace the pooled entry: deepcopy retains aliases between the pool and
    # its frozen contexts, so mutating it in place would also forge the SHA.
    changed["teachers"][demo_teacher.TEACHER_ID] = {
        **changed["teachers"][demo_teacher.TEACHER_ID], "provider": "another-provider"}
    with pytest.raises(DatasetValidationError, match="entry differs"):
        validate_episode(old_path, teacher_registry=changed)
    missing = deepcopy(merged)
    missing["original_registries"].pop(registry_sha256(old_registry))
    with pytest.raises(DatasetValidationError, match="missing original"):
        validate_episode(old_path, teacher_registry=missing)
    forged = deepcopy(merged)
    forged["original_registries"][registry_sha256(old_registry)]["teachers"][demo_teacher.TEACHER_ID]["provider"] = "forged"
    with pytest.raises(DatasetValidationError, match="missing original"):
        validate_episode(old_path, teacher_registry=forged)
    conflicting = deepcopy(old_registry)
    conflicting["teachers"][demo_teacher.TEACHER_ID]["code_sha256"] = "0"*64
    with pytest.raises(DatasetValidationError, match="conflicting teacher"):
        merge_teacher_registries([old_registry, conflicting])


def test_uncompleted_native_segment_has_explicit_final_transition(tmp_path):
    item = validate_episode(episode(tmp_path / "pending", pending=True))
    assert item.summary["uncompleted"] == 1
    assert item.summary["completed"] == 0


def test_unmapped_native_primitive_is_diagnostic_without_invented_label(tmp_path):
    item = validate_episode(episode(tmp_path / "internal", diagnostic=True))
    assert item.summary["expert_labels"] == item.summary["usable_bc_labels"] == 0
    assert item.summary["started"] == 1


@pytest.mark.parametrize("mutation,reason", [
    ("record_gap", "record gap"), ("mixed_episode", "mixed episode"), ("wrong_epoch", "episode epoch"),
    ("mask", "outside recorded mask"), ("stale_teacher", "teacher snapshot is stale"),
    ("future_event", "future event"), ("future_result", "future result"),
    ("future_target", "future target"), ("hidden_target", "hidden target"),
    ("duplicate_decision", "duplicate decision"), ("orphan_execution", "lacks preceding decision"),
    ("bad_execution", "execution ID mismatch"), ("outcome_before_start", "precedes decision"),
    ("duration", "duration mismatch"), ("backwards_next", "points backwards"),
    ("continue_label", "continue/unknown action"), ("periodic_expert", "exact pre-choice"),
    ("spoofed_mask", "mask differs from exact recorded"),
    ("false_loss", "loss label disagrees"), ("oracle", "oracle mixed"),
    ("nonfinite", "nonfinite JSON"), ("dropped", "gap/drop"), ("missing_transition", "transition annotation")])
def test_invalid_links_fail_closed(tmp_path, mutation, reason):
    path = episode(tmp_path / "episode")
    manifest, records = load(path)
    obs, decision = records[:2]
    if mutation == "record_gap":
        records[1]["record_seq"] += 1
    elif mutation == "mixed_episode":
        records[1]["episode_id"] = "other"
    elif mutation == "wrong_epoch":
        records[1]["world_epoch"] += 1
    elif mutation == "mask":
        decision["action_mask"][6] = False
    elif mutation == "spoofed_mask":
        obs["observation"]["action_mask"][6] = False
        obs["observation"]["availability_mask"][6] = False
    elif mutation == "stale_teacher":
        decision["t_game_ms"] = 101
    elif mutation == "future_event":
        obs["observation"]["events"] = [{"source_game_ms": 101}]
    elif mutation == "future_result":
        obs["observation"]["last_result"] = {"valid": True, "source_game_ms": 101}
    elif mutation == "future_target":
        obs["observation"]["target"]["last_seen_game_ms"] = 101
    elif mutation == "hidden_target":
        obs["observation"]["target"]["current_valid"] = False
    elif mutation == "duplicate_decision":
        records.insert(2, deepcopy(decision))
    elif mutation == "orphan_execution":
        records[2]["decision_id"] = "nonexistent"
    elif mutation == "bad_execution":
        records[2]["execution_id"] = 999
    elif mutation == "outcome_before_start":
        records[2]["t_game_ms"] = 99
    elif mutation == "duration":
        records[-1]["duration_ms"] += 1
    elif mutation == "backwards_next":
        records[-1]["next_observation_seq"] = 1
    elif mutation == "continue_label":
        decision["action_id"] = obs["bc_label"] = 0
    elif mutation == "periodic_expert":
        obs["observation"]["phase"] = "post_simulation_post_animation"
    elif mutation == "false_loss":
        obs["bc_label"] = 7
    elif mutation == "oracle":
        obs["observation"]["debug_oracle"] = {"hp": 300}
    elif mutation == "nonfinite":
        obs["observation"]["self"]["hp_fraction"] = float("nan")
    elif mutation == "dropped":
        manifest["source_integrity"]["dropped_capture_records"] = 1
    elif mutation == "missing_transition":
        records.pop()
    if mutation != "record_gap":
        for index, record in enumerate(records, 1):
            record["record_seq"] = index
    save(path, manifest, records)
    with pytest.raises(DatasetValidationError, match=reason):
        validate_episode(path)


def test_partial_jsonl_and_duplicate_json_keys_rejected(tmp_path):
    path = episode(tmp_path / "episode")
    original = (path / "records.jsonl").read_text()
    (path / "records.jsonl").write_text(original.rstrip("\n"))
    with pytest.raises(DatasetValidationError, match="partial JSONL"):
        validate_episode(path)
    (path / "records.jsonl").write_text(original)
    text = (path / "manifest.json").read_text().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')
    (path / "manifest.json").write_text(text)
    with pytest.raises(DatasetValidationError, match="duplicate JSON key"):
        validate_episode(path)


def test_hash_bound_raw_evidence(tmp_path):
    path = episode(tmp_path / "episode")
    raw = path / "raw.jsonl"
    raw.write_text('{"raw": true}\n')
    manifest, records = load(path)
    manifest["source_files"] = [{"path": "raw.jsonl", "sha256": sha256(raw)}]
    save(path, manifest, records)
    validate_episode(path)
    raw.write_text('{"raw": false}\n')
    with pytest.raises(DatasetValidationError, match="SHA mismatch"):
        validate_episode(path)


def test_conversion_cannot_drop_raw_observation_and_renumber(tmp_path):
    path = episode(tmp_path / "episode")
    manifest, records = load(path)
    # Keep the immutable raw journal, remove the converted final observation.
    records = [record for record in records if not (record["kind"] == "observation" and record["observation_seq"] == 2)]
    for index, record in enumerate(records, 1):
        record["record_seq"] = index
    manifest["counts"]["observation"] -= 1
    (path / "records.jsonl").write_text("".join(json.dumps(record)+"\n" for record in records))
    (path / "manifest.json").write_text(json.dumps(manifest)+"\n")
    with pytest.raises(DatasetValidationError, match="dropped or duplicated raw observations"):
        validate_episode(path)


def test_raw_capture_has_independently_checked_global_gaps(tmp_path):
    path = episode(tmp_path / "episode")
    manifest, _ = load(path)
    capture = path / "raw-trajectory.jsonl"
    raw = [json.loads(line) for line in capture.read_text().splitlines()]
    raw[2]["record_seq"] += 50
    capture.write_text("".join(json.dumps(record)+"\n" for record in raw))
    manifest["source_files"][0]["sha256"] = sha256(capture)
    (path / "manifest.json").write_text(json.dumps(manifest)+"\n")
    with pytest.raises(DatasetValidationError, match="raw capture record gap"):
        validate_episode(path)


def test_explicit_unstarted_native_queue_skip_is_diagnostic(tmp_path):
    path = episode(tmp_path / "skip", diagnostic=True)
    manifest, records = load(path)
    records = [record for record in records if not (record["kind"] == "execution" and record["phase"] == "started")]
    final_execution = next(record for record in records if record["kind"] == "execution" and record["phase"] == "completed")
    final_execution.update(phase="cancelled", reason="consumed_without_start")
    records[-1]["execution_status"] = "cancelled"
    for index, record in enumerate(records, 1):
        record["record_seq"] = index
    save(path, manifest, records)
    item = validate_episode(path)
    assert item.summary["started"] == 0
    assert item.summary["cancelled"] == 1
    assert item.summary["usable_bc_labels"] == 0


def test_canonical_dt_is_not_repeated_native_periodic_dt(tmp_path):
    path = episode(tmp_path / "episode")
    manifest, records = load(path)
    assert records[0]["sequence_dt_game_ms"] == 0
    assert records[0]["observation"]["dt_game_ms"] == 50
    records[0]["sequence_dt_game_ms"] = 50
    (path / "records.jsonl").write_text("".join(json.dumps(record)+"\n" for record in records))
    with pytest.raises(DatasetValidationError, match="canonical sequence dt"):
        validate_episode(path)


def make_corpus(root, *, repeats=False):
    paths = [episode(root / f"seed-{seed}", seed=seed) for seed in range(1, 11)]
    paths += [episode(root / "ood", seed=31, ood=True)]
    if repeats:
        paths += [episode(root / "seed-1-mirrored", seed=1)]
    return paths


def add_periodic_before_native_choice(path, *, anchor_time=100, physically_eligible=True, available=True):
    manifest, records = load(path)
    first = deepcopy(records[0])
    first.update(observation_seq=1, decision_valid=False, bc_label=None)
    first["observation"].update(observation_seq=77, t_game_ms=anchor_time,
                                 phase="post_simulation_post_animation", can_decide=False,
                                 action_mask_valid=False, native_decision_eligible=physically_eligible, periodic=True)
    first["observation"]["availability_mask"][6] = available
    manifest["initial"]["game_ms"] = anchor_time
    for record in records:
        if "observation_seq" in record:
            record["observation_seq"] += 1
        if "next_observation_seq" in record:
            record["next_observation_seq"] += 1
    records.insert(0, first)
    for index, record in enumerate(records, 1):
        record["record_seq"] = index
    save(path, manifest, records)


def test_grouped_splits_preserve_repeats_and_separate_ood(tmp_path):
    paths = make_corpus(tmp_path, repeats=True)
    report = build_dataset(paths, tmp_path / "dataset")
    entries = {row["episode_id"]: row for row in report["episodes"]}
    assert entries["seed-1"]["split"] == entries["seed-1-mirrored"]["split"]
    assert entries["ood"]["split"] == "ood"
    assert {row["split"] for row in report["episodes"]} == {"train", "eval", "test", "ood"}
    assert not report["diagnostic_bc_coverage_ready"]
    assert len(report["coverage"]["train"]["insufficient_classes"]) == 11
    assert "wall" in report["coverage"]["train"]["missing_conditions"]


def test_periodic_model_view_keeps_only_causal_periodic_memory_updates(tmp_path):
    paths = make_corpus(tmp_path)
    for path in paths:
        add_periodic_before_native_choice(path)
    report = build_dataset(paths, tmp_path / "periodic", sampling_view="periodic-v1")
    assert report["model_sampling"]["kind"] == "periodic_only"
    assert report["feature_version"] == PERIODIC_FEATURE_VERSION
    assert all(row["aligned_labels"] == 1 for row in report["model_alignment"].values())
    chunks = list(OrderedSequenceLoader(tmp_path / "periodic"))
    assert chunks and all(row["phase"] == "post_simulation_post_animation" for chunk in chunks for row in chunk["observations"])
    assert [row["dt_game_ms"] for row in chunks[0]["observations"]] == [0, 50]
    assert [row["loss_mask"] for row in chunks[0]["observations"]] == [True, False]


def test_prepared_model_view_excludes_preparation_and_opponent_memory(tmp_path):
    path = episode(tmp_path/"cold-scene")
    add_periodic_before_native_choice(path)
    captured = validate_episode(path)
    anchor = next(row for row in captured.records if row["kind"] == "observation" and row["observation"]["periodic"])
    prep = deepcopy(anchor)
    prep.update(observation_seq=0, raw_capture_seq=0, t_game_ms=67)
    prep["observation"].update(observation_seq=66, t_game_ms=67)
    opponent = deepcopy(anchor)
    opponent.update(entity_id=12, raw_capture_seq=anchor["raw_capture_seq"]+1)
    opponent["observation"]["entity_id"] = 12
    captured.records = [prep, opponent]+captured.records
    captured.manifest["model_sequence_start"] = {
        "entity_id": anchor["entity_id"], "raw_capture_seq": anchor["raw_capture_seq"], "t_game_ms": anchor["t_game_ms"]}
    view = periodic_model_view(captured)
    assert {row["entity_id"] for row in view.records} == {11}
    assert [row["t_game_ms"] for row in view.records] == [100, 150]
    assert [row["sequence_dt_game_ms"] for row in view.records] == [0, 50]
    assert prep in captured.records and opponent in captured.records  # original evidence stays intact


def test_prepared_model_view_cannot_replace_anchor_with_later_periodic_row(tmp_path):
    path = episode(tmp_path/"wrong-cold-scene")
    add_periodic_before_native_choice(path)
    captured = validate_episode(path)
    exact_teacher = next(row for row in captured.records if row["kind"] == "observation" and
                         row["observation"]["phase"] == "pre_native_primitive")
    captured.manifest["model_sequence_start"] = {"entity_id": 11, "raw_capture_seq": exact_teacher["raw_capture_seq"], "t_game_ms": 100}
    with pytest.raises(DatasetValidationError, match="lost its exact first anchor"):
        periodic_model_view(captured)


def test_existing_teacher_cannot_declare_reactive_cold_scene(tmp_path):
    path = episode(tmp_path/"unregistered-cold-scene")
    captured = validate_episode(path)
    captured.manifest["model_sequence_start"] = {"schema_version": 1, "kind": "prepared_reaction_anchor"}
    with pytest.raises(DatasetValidationError, match="registered reactive defence"):
        dataset_module.validate_prepared_sequence_start(captured)


def test_prepared_activation_binds_transport_id_separately_from_intent():
    request = {"cmd": "arena_reaction_activate", "observation_seq": 5, "mode": "lockstep"}
    response = {"id": 25, "arena": {"reaction": {"phase": "active"}}}
    before, prepared = {"entity_id": 1, "state": "before"}, {"entity_id": 1, "state": "prepared"}
    rows = [{"direction": "response", "value": {"actors": [before, prepared]}},
            {"direction": "request", "value": {"id": 25, **request}},
            {"direction": "response", "value": response}]
    dataset_module.validate_prepared_activation_ipc(iter(rows), request, response, before, prepared)
    extra = deepcopy(rows)
    extra[1]["value"]["unrecorded_flag"] = True
    with pytest.raises(DatasetValidationError, match="unique actual IPC"):
        dataset_module.validate_prepared_activation_ipc(iter(extra), request, response, before, prepared)
    wrong_id = deepcopy(rows)
    wrong_id[1]["value"]["id"] = 26
    with pytest.raises(DatasetValidationError, match="response/transport ID"):
        dataset_module.validate_prepared_activation_ipc(iter(wrong_id), request, response, before, prepared)


@pytest.mark.parametrize("transport_id", [None, 0, -1, True, "25"])
def test_prepared_activation_requires_positive_integer_transport_id(transport_id):
    request = {"cmd": "arena_reaction_activate"}
    response = {"id": transport_id, "arena": {"reaction": {"phase": "active"}}}
    status = {"entity_id": 1}
    rows = [{"direction": "response", "value": {"actors": [status]}},
            {"direction": "request", "value": {"id": transport_id, **request}},
            {"direction": "response", "value": response}]
    with pytest.raises(DatasetValidationError, match="transport ID"):
        dataset_module.validate_prepared_activation_ipc(iter(rows), request, response, status, status)


def test_streaming_build_preserves_exact_batch_statistics_and_coverage(tmp_path):
    paths = make_corpus(tmp_path)
    for path in paths:
        add_periodic_before_native_choice(path)
    projected = [periodic_model_view(validate_episode(path)) for path in paths]
    assignment = dataset_module.split_groups(projected)
    expected_statistics = dataset_module.train_statistics(projected, assignment, feature_version=PERIODIC_FEATURE_VERSION)
    expected_coverage = {split: dataset_module._coverage([episode for episode in projected if assignment[dataset_module.group_key(episode.manifest)] == split],
                                                       min_labels=20, included_actions=tuple(range(1, 12)))
                         for split in ("train", "eval", "test", "ood")}
    actual = build_dataset(paths, tmp_path / "streamed", sampling_view="periodic-v1")
    assert read_json(tmp_path / "streamed" / "normalization.json") == expected_statistics
    assert actual["coverage"] == expected_coverage


def test_build_releases_full_episode_before_validating_the_next(tmp_path, monkeypatch):
    paths = make_corpus(tmp_path)
    references = []
    original = dataset_module.validate_episode

    def checked_validate(path, **kwargs):
        assert all(reference() is None for reference in references)
        episode = original(path, **kwargs)
        references.append(weakref.ref(episode))
        return episode

    monkeypatch.setattr(dataset_module, "validate_episode", checked_validate)
    build_dataset(paths, tmp_path / "bounded-build")
    assert len(references) == len(paths) and all(reference() is None for reference in references)


def test_loader_releases_projected_episode_before_loading_the_next(tmp_path, monkeypatch):
    paths = make_corpus(tmp_path)
    for path in paths:
        add_periodic_before_native_choice(path)
    build_dataset(paths, tmp_path / "bounded-loader", sampling_view="periodic-v1")
    references = []
    original = dataset_module.periodic_model_view

    def checked_project(episode, **kwargs):
        assert all(reference() is None for reference in references)
        projected = original(episode, **kwargs)
        references.append(weakref.ref(projected))
        return projected

    monkeypatch.setattr(dataset_module, "periodic_model_view", checked_project)
    chunks = list(OrderedSequenceLoader(tmp_path / "bounded-loader"))
    assert chunks and len(references) > 1 and all(reference() is None for reference in references)


def test_full_episode_loader_has_no_chunk_resets_prefix_replay_or_locked_tick_drop(tmp_path):
    paths = make_corpus(tmp_path)
    for path in paths:
        add_periodic_before_native_choice(path)
        manifest, records = load(path)
        records[-1].update(terminated=False, truncated=False, infrastructure=False)
        last = next(record for record in reversed(records) if record["kind"] == "observation")
        for offset in range(1, 41):
            row = deepcopy(last)
            row["observation_seq"] = last["observation_seq"]+offset
            row["observation"].update(observation_seq=last["observation"]["observation_seq"]+offset,
                                       t_game_ms=last["t_game_ms"]+50*offset,
                                       sim_step=(last["t_game_ms"]+50*offset)//33)
            records.append(row)
        manifest["final"]["t_game_ms"] = last["t_game_ms"]+2000
        for index, record in enumerate(records, 1):
            record["record_seq"] = index
        save(path, manifest, records)
    build_dataset(paths, tmp_path / "full-stream", sampling_view="periodic-v1")
    streams = list(FullEpisodeLoader(tmp_path / "full-stream", split="eval"))
    assert streams
    for stream in streams:
        rows = stream["observations"]
        assert len(rows) == 42 and stream["burn_in_length"] == 0 and stream["end_of_stream"]
        assert sum(row["reset"] for row in rows) == 1 and rows[0]["reset"]
        assert all(row["burn_in"] is False for row in rows)
        assert len({row["observation_seq"] for row in rows}) == len(rows)
        assert sum(row["loss_mask"] for row in rows) == 1


def test_periodic_model_view_excludes_event_only_rows_and_recomputes_dt(tmp_path):
    path = episode(tmp_path / "episode")
    add_periodic_before_native_choice(path)
    manifest, records = load(path)
    event_row = deepcopy(records[0])
    event_row.update(observation_seq=3)
    event_row["observation"].update(observation_seq=91, t_game_ms=110, periodic=False, event_only=True)
    records.insert(6, event_row)
    records[-2]["observation_seq"] = 4
    records[-1]["next_observation_seq"] = 4
    for index, record in enumerate(records, 1):
        record["record_seq"] = index
    save(path, manifest, records)
    view = periodic_model_view(validate_episode(path))
    assert len(view.observations) == 2
    assert all(row["observation"]["periodic"] for row in view.records)
    assert [row["sequence_dt_game_ms"] for row in view.records] == [0, 50]


@pytest.mark.parametrize("change,reason", [("old", "periodic_anchor_too_old"),
                                          ("locked", "periodic_anchor_physically_locked"),
                                          ("unavailable", "action_unavailable_at_periodic_anchor")])
def test_periodic_alignment_drops_unobservable_or_invalid_label(tmp_path, change, reason):
    path = episode(tmp_path / "episode")
    add_periodic_before_native_choice(path, anchor_time=20 if change == "old" else 100,
                                     physically_eligible=change != "locked", available=change != "unavailable")
    projected = periodic_model_view(validate_episode(path))
    assert projected.summary["model_alignment"]["aligned_labels"] == 0
    assert projected.summary["model_alignment"]["dropped_by_reason"] == {reason: 1}
    assert not any(row["bc_label"] is not None for row in projected.records)


def test_combined_dataset_inherits_existing_groups_and_rejects_override(tmp_path):
    paths = make_corpus(tmp_path)
    before = build_dataset(paths, tmp_path / "stage6")
    paths.append(episode(tmp_path / "seed-1-new-teacher", seed=1))
    after = build_dataset(paths, tmp_path / "stage7", existing_assignments=tmp_path / "stage6" / "dataset.json",
                          sampling_view="periodic-v1")
    assert before["groups"] == after["groups"]
    fixed_key = next(key for key, split in before["groups"].items() if split == "eval")
    with pytest.raises(DatasetValidationError, match="changes an inherited split"):
        build_dataset(paths, tmp_path / "bad", existing_assignments=before["groups"], group_overrides={fixed_key: "train"})


def test_live_packing_has_same_periodic_dt_and_owner_independent_goal_semantics():
    row = observation(1, 100)
    names = canonical_feature_names(PERIODIC_FEATURE_VERSION)
    normalizer = {"feature_version": PERIODIC_FEATURE_VERSION, "feature_names": names,
                  "mean": {name: 0 for name in names}, "std": {name: 1 for name in names}}
    row["goal"] = {"kind": "native_combat", "move_hint": 1}
    native = pack_features(row, normalizer, sequence_dt_game_ms=33)
    row["goal"]["kind"] = "combat"
    assert pack_features(row, normalizer, sequence_dt_game_ms=33) == native
    assert native[names.index("dt_game_ms")] == 33
    row["phase"] = "pre_native_primitive"
    with pytest.raises(DatasetValidationError, match="periodic snapshots only"):
        pack_features(row, normalizer, sequence_dt_game_ms=0)


def test_shared_policy_mask_excludes_continue_only_for_real_decision_opportunity():
    raw = [True]*12
    row = {"availability_mask": raw, "native_decision_eligible": True}
    assert policy_action_mask(row) == [False]+[True]*11
    assert raw == [True]*12
    row["native_decision_eligible"] = False
    assert policy_action_mask(row) == raw
    row.update(native_decision_eligible=True, availability_mask=[True]+[False]*11)
    assert policy_action_mask(row) == [True]+[False]*11


def test_empty_terminal_raw_mask_has_memory_only_continue_and_is_preserved(tmp_path):
    paths = make_corpus(tmp_path)
    for path in paths:
        add_periodic_before_native_choice(path)
        manifest, records = load(path)
        final = next(record for record in reversed(records) if record["kind"] == "observation")
        final["observation"].update(native_decision_eligible=False, can_decide=False,
                                    availability_mask=[False]*12, action_mask=[False]*12)
        save(path, manifest, records)
    build_dataset(paths, tmp_path / "model-view", sampling_view="periodic-v1")
    chunks = list(OrderedSequenceLoader(tmp_path / "model-view"))
    terminal = chunks[0]["observations"][-1]
    assert terminal["raw_recorded_action_mask"] == [False]*12
    assert terminal["action_mask"] == [True]+[False]*11
    assert terminal["label"] is None and not terminal["loss_mask"]


def test_empty_raw_mask_cannot_receive_an_expert_label(tmp_path):
    path = episode(tmp_path / "episode")
    add_periodic_before_native_choice(path)
    manifest, records = load(path)
    records[0]["observation"]["availability_mask"] = [False]*12
    save(path, manifest, records)
    view = periodic_model_view(validate_episode(path))
    first = view.records[0]
    assert first["bc_label"] is None
    assert view.summary["model_alignment"]["dropped_by_reason"] == {"action_unavailable_at_periodic_anchor": 1}
    assert first["observation"]["availability_mask"] == [False]*12


def test_normalizer_and_class_weights_ignore_eval_test_ood(tmp_path):
    paths = make_corpus(tmp_path)
    first = build_dataset(paths, tmp_path / "first")
    original = read_json(tmp_path / "first" / "normalization.json")
    for entry in first["episodes"]:
        if entry["split"] == "train":
            continue
        path = Path(entry["path"])
        manifest, records = load(path)
        for row in records:
            if row["kind"] == "observation":
                row["observation"]["self"]["hp_fraction"] = 100000
        save(path, manifest, records)
    build_dataset(paths, tmp_path / "second")
    assert read_json(tmp_path / "second" / "normalization.json") == original
    assert original["class_weights"][0] == 0
    assert original["class_weights"][6] == 1
    assert all(original["class_weights"][i] == 0 for i in range(12) if i != 6)


def test_unseen_constant_binary_feature_uses_unit_scale_without_eval_fit(tmp_path):
    paths = make_corpus(tmp_path)
    path = tmp_path / "ood"
    manifest, records = load(path)
    for record in records:
        if record["kind"] == "observation":
            record["observation"]["self"]["skill"] = 1
    save(path, manifest, records)
    build_dataset(paths, tmp_path / "dataset")
    normalizer = read_json(tmp_path / "dataset" / "normalization.json")
    assert normalizer["variance"]["self.skill.valid"] == 0
    assert "self.skill.valid" in normalizer["constant_features"]
    assert normalizer["std"]["self.skill.valid"] == 1
    index = normalizer["feature_names"].index("self.skill.valid")
    chunks = list(OrderedSequenceLoader(tmp_path / "dataset", split="ood"))
    assert chunks[0]["observations"][0]["features"][index] == 1


def test_actor_features_exclude_diagnostic_and_future_label_fields():
    obs = observation(1, 100)
    initial = actor_features(obs)
    obs["self"].update({"queued_primitive": 6, "choice_id": 111})
    obs.update({"reward": 1, "terminated": True, "decision_id": 12, "model_logits": [1]*12})
    obs.update(can_decide=not obs["can_decide"], action_mask_valid=not obs["action_mask_valid"])
    assert actor_features(obs) == initial


def test_loader_keeps_lock_ticks_and_only_expert_opportunity_loss(tmp_path):
    paths = make_corpus(tmp_path)
    build_dataset(paths, tmp_path / "dataset")
    chunks = list(OrderedSequenceLoader(tmp_path / "dataset", chunk_length=1, burn_in=1))
    assert chunks
    first = chunks[0]
    assert first["reset"] and first["burn_in_length"] == 0
    assert first["observations"][0]["loss_mask"]
    second = chunks[1]
    assert second["burn_in_length"] == 1
    assert second["observations"][0]["burn_in"] and not second["observations"][0]["loss_mask"]
    assert not second["observations"][1]["burn_in"] and not second["observations"][1]["loss_mask"]
    assert second["observations"][1]["label"] is None


def test_loader_rejects_changed_validated_episode(tmp_path):
    paths = make_corpus(tmp_path)
    report = build_dataset(paths, tmp_path / "dataset")
    train = next(row for row in report["episodes"] if row["split"] == "train")
    path = Path(train["path"])
    manifest, records = load(path)
    records[0]["observation"]["self"]["hp_fraction"] = .9
    save(path, manifest, records)
    with pytest.raises(DatasetValidationError, match="changed after validation"):
        list(OrderedSequenceLoader(tmp_path / "dataset"))


def test_related_group_cannot_be_split_into_ood(tmp_path):
    paths = make_corpus(tmp_path)
    paths += [episode(tmp_path / "seed-1-false-ood", seed=1, ood=True)]
    with pytest.raises(DatasetValidationError, match="mixes OOD"):
        build_dataset(paths, tmp_path / "dataset")


def test_three_independent_groups_required(tmp_path):
    paths = [episode(tmp_path / f"repeat-{index}", seed=17) for index in range(3)]
    with pytest.raises(DatasetValidationError, match="three independent"):
        build_dataset(paths, tmp_path / "dataset")
