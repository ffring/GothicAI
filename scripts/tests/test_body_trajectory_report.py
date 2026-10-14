"""Independent admission-gate tests; no game, model, remote call or simulation."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("body_trajectory_report", Path(__file__).parents[1]/"body_trajectory_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def trace(actor=3, target=4, entity=1, other=2):
    return [{"kind": "body_combat", "seq": 1, "t_game": 100, "actor_id": actor, "target_id": target,
             "actor": "BDT_1003_BANDIT_M", "target": "PAL_255_RITTER", "phase": "arena_actor_mapping",
             "observation_entity_id": entity, "role": "primary"},
            {"kind": "body_combat", "seq": 2, "t_game": 100, "actor_id": target, "target_id": actor,
             "actor": "PAL_255_RITTER", "target": "BDT_1003_BANDIT_M", "phase": "arena_actor_mapping",
             "observation_entity_id": other, "role": "opponent"},
            {"kind": "body_combat", "seq": 3, "t_game": 133, "actor_id": actor, "target_id": target,
             "actor": "BDT_1003_BANDIT_M", "target": "PAL_255_RITTER", "phase": "physics_step",
             "x": 150., "y": 0., "z": 0., "yaw": 180., "collision_failed_count": 0},
            {"kind": "body_combat", "seq": 4, "t_game": 133, "actor_id": actor, "target_id": target,
             "actor": "BDT_1003_BANDIT_M", "target": "PAL_255_RITTER", "phase": "native_choice",
             "choice_id": 1, "primitive": 4, "queue": [4, 5, 6, 0, 0, 0]},
            {"kind": "body_combat", "seq": 5, "t_game": 166, "actor_id": actor, "target_id": target,
             "actor": "BDT_1003_BANDIT_M", "target": "PAL_255_RITTER", "phase": "animation_branch_draw",
             "selected_variant": 1, "fallback_variant": 2}]


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row)+"\n" for row in rows))


def test_runtime_identity_normalization_keeps_the_same_physical_roles():
    assert report.normalized_mechanics(trace()) == report.normalized_mechanics(trace(10, 20, 50, 60))


@pytest.mark.parametrize("index,key,value", [(2, "x", 151.), (2, "yaw", 179.), (2, "collision_failed_count", 1),
                                             (3, "queue", [4, 5, 0, 0, 0, 0]), (3, "primitive", 5),
                                             (4, "selected_variant", 0), (4, "fallback_variant", 0)])
def test_recording_parity_does_not_hide_any_behavior_or_rng_trace_difference(index, key, value):
    left, right = trace(), trace()
    right[index][key] = value
    assert report.normalized_mechanics(left) != report.normalized_mechanics(right)


def packet_fixture(root):
    response = {"id": 1, "schema_version": 1, "type": "body_trajectory_events", "capture_generation": 4,
                "capture_failed": False, "capture_failures_total": 0, "dropped_total": 0,
                "dropped_since_drain": 0, "reset_discarded_total": 0, "queue_size": 0,
                "record_seq_high_watermark": 7, "events": [{"record_seq": 7}]}
    native_status = {"id": 2, "body_observation": {"dropped_total": 0, "event_dropped_total": 0,
                      "invalid_numeric_rows": 0, "missed_period_buckets": 0}}
    controls = {"id": 3, "dropped_total": 0, "events": []}
    pairs = [("body_trajectory_events", response), ("status", native_status), ("body_events", controls)]
    ipc = []
    for index, (cmd, value) in enumerate(pairs, 1):
        ipc += [{"direction": "request", "wall_monotonic": float(index), "value": {"id": index, "cmd": cmd}},
                {"direction": "response", "wall_monotonic": index+.1, "value": value}]
    packet = {"packet_seq": 1, "stats": {key: value for key, value in response.items() if key != "events"},
              "event_count": 1, "first_record_seq": 7, "last_record_seq": 7}
    write_rows(root/"capture-packets.jsonl", [packet])
    (root/"source-integrity.json").write_text(json.dumps({"runner_status": native_status, "control_events": controls}))
    manifest = {"capture_packets_file": "capture-packets.jsonl", "source_integrity_file": "source-integrity.json",
                "source_integrity": {"capture_generation": 4, "capture_records": 1}, "writer_closed": True}
    return manifest, ipc, packet


def test_capture_counters_are_bound_to_the_actual_engine_rpc(tmp_path):
    manifest, ipc, _ = packet_fixture(tmp_path)
    result = report.recorder_packets(tmp_path, manifest, ipc)
    assert result == {"packets": 1, "records": 1, "fully_drained": True, "writer_closed": True}


def test_fabricated_zero_capture_drop_cannot_override_actual_native_reply(tmp_path):
    manifest, ipc, _ = packet_fixture(tmp_path)
    ipc[1]["value"]["dropped_total"] = 1
    with pytest.raises(ValueError, match="stats differ"):
        report.recorder_packets(tmp_path, manifest, ipc)


def test_truthfully_recorded_native_drop_still_blocks_corpus_admission(tmp_path):
    manifest, ipc, packet = packet_fixture(tmp_path)
    ipc[1]["value"]["dropped_total"] = packet["stats"]["dropped_total"] = 1
    write_rows(tmp_path/"capture-packets.jsonl", [packet])
    with pytest.raises(ValueError, match="lost records"):
        report.recorder_packets(tmp_path, manifest, ipc)


def test_capture_must_reach_the_native_high_watermark(tmp_path):
    manifest, ipc, packet = packet_fixture(tmp_path)
    ipc[1]["value"]["record_seq_high_watermark"] = packet["stats"]["record_seq_high_watermark"] = 8
    write_rows(tmp_path/"capture-packets.jsonl", [packet])
    with pytest.raises(ValueError, match="high watermark"):
        report.recorder_packets(tmp_path, manifest, ipc)


def test_integrity_zero_claim_needs_a_matching_raw_native_response(tmp_path):
    manifest, ipc, _ = packet_fixture(tmp_path)
    value = json.loads((tmp_path/"source-integrity.json").read_text())
    value["runner_status"]["id"] = 999
    (tmp_path/"source-integrity.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="matching native RPC"):
        report.recorder_packets(tmp_path, manifest, ipc)


def test_open_writer_cannot_be_a_complete_durable_corpus(tmp_path):
    manifest, ipc, _ = packet_fixture(tmp_path)
    manifest["writer_closed"] = False
    with pytest.raises(ValueError, match="writer has not closed"):
        report.recorder_packets(tmp_path, manifest, ipc)


def test_driver_pass_and_coverage_flags_do_not_establish_full_readiness(tmp_path):
    (tmp_path/"manifest.json").write_text(json.dumps({"suite_scope": "smoke"}))
    (tmp_path/"suite.json").write_text(json.dumps({"suite_scope": "smoke", "status": "passed", "full_stage6_evidence": True}))
    value = report.summarize(tmp_path, {})
    assert value["full_stage6_evidence"] is False
    assert value["status"] == "incomplete"
    assert any("smoke" in issue for issue in value["issues"])


def warmup_fixture(monkeypatch, root):
    config = {"parity_warmup_episodes": 1, "parity": {"seed": 17, "side": 0, "distance_cm": 400, "timeout_ms": 60000}}
    records = []
    for name, epoch, capture in (("parity-warmup-0", 2, False), ("parity-off", 3, False), ("native-17-0", 4, True)):
        records.append({"name": name, "episode_dir": "episodes/"+name, "events_file": name+".jsonl", "capture": capture,
                        "provider": "native", "scenario": "native_duel", "seed": 17, "side": 0,
                        "initial": {"world_epoch": epoch, "session_epoch": 7, "distance_cm": 400, "timeout_ms": 60000}})
        write_rows(root/(name+".jsonl"), trace())
    monkeypatch.setattr(report, "semantic_initial", lambda value: {key: value[key] for key in ("distance_cm", "timeout_ms")})
    monkeypatch.setattr(report, "arena_evidence", lambda root, row: ({"final": {"outcome": "death_or_down"}}, {row["events_file"]: "sha"}))
    suite = {"recording_warmup": [records[0]], "recording_off": records[1], "recording_on": records[2]}
    return suite, config, [records[2]]


def test_cold_warmup_is_preserved_but_excluded_from_dataset(tmp_path, monkeypatch):
    suite, config, records = warmup_fixture(monkeypatch, tmp_path)
    results, hashes = report.recording_warmup_evidence(tmp_path, suite, config, records)
    assert results[0]["capture"] is False and results[0]["excluded_from_dataset"] is True
    assert results[0]["ordered_events"] == 5 and hashes == {"parity-warmup-0.jsonl": "sha"}


@pytest.mark.parametrize("mutation,reason", [("absent", "warmup episode"), ("capture", "recording-off"),
                                             ("setup", "physical setup"), ("alias", "exact collected"),
                                             ("order", "reset order"), ("corpus", "training corpus")])
def test_warmup_gate_rejects_missing_or_misbound_evidence(tmp_path, monkeypatch, mutation, reason):
    suite, config, records = warmup_fixture(monkeypatch, tmp_path)
    if mutation == "absent": suite["recording_warmup"] = []
    elif mutation == "capture": suite["recording_warmup"][0]["capture"] = True
    elif mutation == "setup": suite["recording_warmup"][0]["initial"]["distance_cm"] = 401
    elif mutation == "alias": suite["recording_on"] = {**suite["recording_on"], "events_file": "fabricated.jsonl"}
    elif mutation == "order": suite["recording_warmup"][0]["initial"]["world_epoch"] = 5
    elif mutation == "corpus": suite["recording_warmup"][0]["episode_dir"] = records[0]["episode_dir"]
    with pytest.raises(ValueError, match=reason):
        report.recording_warmup_evidence(tmp_path, suite, config, records)


@pytest.mark.parametrize("key,changed", [("seed", 17), ("side", 0), ("ood", False), ("distance_cm", 400)])
def test_expected_ood_name_cannot_replace_declared_physical_coverage(key, changed):
    declared = {"provider": "native", "scenario": "native_duel", "seed": 151, "side": 1, "ood": True,
                "distance_cm": 800, "timeout_ms": 60000}
    record = {"name": "ood-distance-800", "capture": True, **{field: value for field, value in declared.items() if field not in ("distance_cm", "timeout_ms")},
              "initial": {"distance_cm": 800, "timeout_ms": 60000}}
    if key == "distance_cm": record["initial"][key] = changed
    else: record[key] = changed
    with pytest.raises(ValueError, match=key):
        report.require_declared_episode(record, declared)


def service_fixture():
    return {"services": {"gothic-voxtral": "ActiveState=active\nSubState=running\nMainPID=123\nCPUUsageNSec=1",
                         "gothic-voxcpm": "ActiveState=inactive\nSubState=dead\nMainPID=0\nMemoryCurrent=[not set]"}}


def test_unchanged_inactive_baseline_is_explicit_without_claiming_availability():
    before, after = service_fixture(), service_fixture()
    after["services"]["gothic-voxtral"] = after["services"]["gothic-voxtral"].replace("CPUUsageNSec=1", "CPUUsageNSec=10")
    value = report.production_services(before, after)
    assert value["unchanged"] is True and value["services_all_active"] is False
    assert value["baseline_inactive"] == ["gothic-voxcpm"]
    assert "availability was not established" in value["notes"][0]


def test_unchanged_service_gate_still_rejects_restart_and_state_changes():
    for name, replacement in (("gothic-voxtral", "ActiveState=active\nSubState=running\nMainPID=124"),
                              ("gothic-voxcpm", "ActiveState=active\nSubState=running\nMainPID=321")):
        after = service_fixture(); after["services"][name] = replacement
        with pytest.raises(ValueError, match="identity changed"):
            report.production_services(service_fixture(), after)


def test_missing_malformed_or_inconsistent_service_identity_is_rejected():
    for bad in (None, "ActiveState=inactive\nSubState=dead", "ActiveState=inactive\nSubState=dead\nMainPID=no",
                "ActiveState=active\nSubState=running\nMainPID=0", "ActiveState=inactive\nSubState=dead\nMainPID=5",
                "ActiveState=inactive\nSubState=dead\nMainPID=0\nMainPID=0"):
        before = service_fixture(); before["services"]["gothic-voxcpm"] = bad
        with pytest.raises(ValueError): report.production_services(before, before)


def test_changed_reporter_requires_explicit_narrow_regrade_and_preserves_both_hashes():
    with pytest.raises(ValueError, match="explicit reporter regrade"):
        report.reporter_binding("collected-sha", "new-sha", False)
    value = report.reporter_binding("collected-sha", "new-sha", True)
    assert value["collected_reporter_sha256"] == "collected-sha" and value["actual_reporter_sha256"] == "new-sha"
    assert value["reporter_regrade_reason"] == report.REPORTER_REGRADE_REASON
