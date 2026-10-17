"""Scheduling admission audit fixtures only; no process, game or model execution."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import body_pending_input_probe as probe


def saved_fixture(tmp_path):
    output = tmp_path/"probe"; episode = output/"episode"; episode.mkdir(parents=True)
    def write(name, value):
        path = output/name
        path.write_text(json.dumps(value)+"\n")
    def journal(name, rows):
        (output/name).write_text("".join(json.dumps(row)+"\n" for row in rows))
    identity = {"entity_id": 1, "session_epoch": 3, "world_epoch": 4, "control_generation": 7}
    row = {**identity, "observation_seq": 5, "periodic": True, "can_decide": True,
           "action_mask_valid": True, "action_mask": [True]*12, "t_game_ms": 133,
           "goal": {"version": 1}, "target": {"entity_id": 2}}
    final = {"outcome": "time_limit", "infrastructure": False,
             "actors": [{"entity_id": entity, "hp": 300, "owner": "scripted", "release_pending": False} for entity in (1, 2)]}
    requests = [{"id": 1, "cmd": "reset"}, {"id": 2, "cmd": "body_observations"},
                {"id": 3, "cmd": "body_action", **identity, "observation_seq": 5, "issued_game_ms": 133,
                 "action_id": 11, "command_id": 1, "ttl_ms": 100, "goal_version": 1, "target_entity_id": 2},
                {"id": 4, "cmd": "close"}]
    native_times = (15, 45, 65, 95)
    responses = [{"id": i+1, "ok": True, "schema_version": 1, "clock_domain": "linux-clock-monotonic",
                  "wall_monotonic_ns": stamp, "sim_step": (0, 1, 1, 2)[i], "game_ms": (100, 133, 133, 166)[i],
                  "graphics_device_created": False, "window_created": False, "audio_device_created": False}
                 for i, stamp in enumerate(native_times)]
    responses[1]["observations"] = [row]
    responses[2].update(accepted=True, execution_id=9)
    diag = {"enabled": True, "oracle_only": True, "rows_emitted": 7, "dropped_total": 0, "capacity": 65536}
    responses[3]["scheduling_diagnostics"] = diag
    ipc = []
    for request, response, sent, received in zip(requests, responses, (10, 25, 55, 85), (20, 50, 70, 100)):
        ipc.extend([{"direction": "request", "wall_monotonic_ns": sent, "value": request},
                    {"direction": "response", "wall_monotonic_ns": received, "value": response}])
    common = {"schema_version": 1, "kind": "runner_scheduling", "oracle_only": True,
              "clock_domain": "linux-clock-monotonic", "runner_generation": 1}
    scheduling = []
    for phase, stamp, tick, step, game, due, extras in [
        ("input_read", 30, 0, 0, 100, 29, {"request_id": 2, "command": "body_observations", "admitted_before_due_tick": False}),
        ("tick_begin", 32, 1, 0, 100, 29, {}), ("tick_end", 40, 1, 1, 133, 29, {}),
        ("input_read", 60, 1, 1, 133, 59, {"request_id": 3, "command": "body_action", "admitted_before_due_tick": True}),
        ("tick_begin", 75, 2, 1, 133, 59, {}), ("tick_end", 80, 2, 2, 166, 59, {}),
        ("input_read", 90, 2, 2, 166, 89, {"request_id": 4, "command": "close", "admitted_before_due_tick": False})]:
        x = {**common, "seq": len(scheduling)+1, "phase": phase, "wall_monotonic_ns": stamp,
             "tick_id": tick, "sim_step": step, "game_ms": game, "tick_due_wall_monotonic_ns": due, **extras}
        if phase == "input_read":
            x["max_ready_commands_per_due_tick"] = 1
        scheduling.append(x)
    write("manifest.json", {"driver_sha256": probe.sha256(Path(probe.__file__)), "model": None,
                            "scheduling_diagnostics": True, "binary_sha256": "a"*64,
                            "source_sha256": "b"*64, "engine_library_sha256": "c"*64,
                            "helper_inventory": probe.helper_inventory()})
    write("probe.json", {"status": "collected", "profile": probe.PROFILE, "model": None,
                         "close_response": responses[3], "final": final})
    journal("episode/ipc.jsonl", ipc); journal("episode/observer-timing.jsonl", scheduling)
    journal("episode/observations.jsonl", [row])
    journal("episode/body-control-events.jsonl", [{**identity, "phase": "started", "command_id": 1,
                                                  "action_id": 11, "execution_id": 9}])
    journal("collector-transport.jsonl", [])
    return output, scheduling, ipc


def rewrite(output, name, values):
    (output/name).write_text("".join(json.dumps(row)+"\n" for row in values))


def test_real_receipt_chain_audit_binds_body_admission_query_refresh_and_cap_one(tmp_path):
    output, _, _ = saved_fixture(tmp_path)
    result = probe.audit_saved_output(output)
    assert result["passed"] and result["model"] is None and len(result["checks"]) == 4
    assert result["chains"][0]["command"] == "body_action"
    assert result["chains"][0]["input_read_ns"] < result["chains"][0]["tick_begin_ns"]
    assert result["query_chains"][0]["tick_end_ns"] < result["query_chains"][0]["response_ns"]


def test_command_cannot_be_claimed_read_before_tick_if_native_marker_is_inside_step(tmp_path):
    output, scheduling, _ = saved_fixture(tmp_path)
    bad = copy.deepcopy(scheduling)
    bad[3]["phase"] = "tick_begin"
    rewrite(output, "episode/observer-timing.jsonl", bad)
    with pytest.raises(probe.ArenaInfrastructureError):
        probe.audit_saved_output(output)


def test_due_query_reply_must_follow_actual_post_step_state(tmp_path):
    output, _, ipc = saved_fixture(tmp_path)
    bad = copy.deepcopy(ipc)
    bad[3]["value"]["sim_step"] = 0
    rewrite(output, "episode/ipc.jsonl", bad)
    with pytest.raises(probe.ArenaInfrastructureError, match="query replied"):
        probe.audit_saved_output(output)


def test_probe_does_not_relax_actual_action_mask_or_hide_diagnostic_drops(tmp_path):
    output, _, ipc = saved_fixture(tmp_path)
    bad = copy.deepcopy(ipc)
    bad[3]["value"]["observations"][0]["action_mask"][11] = False
    rewrite(output, "episode/ipc.jsonl", bad)
    with pytest.raises(probe.ArenaInfrastructureError, match="source/mask"):
        probe.audit_saved_output(output)
    rewrite(output, "episode/ipc.jsonl", ipc)
    result = json.loads((output/"probe.json").read_text())
    result["close_response"]["scheduling_diagnostics"]["dropped_total"] = 1
    (output/"probe.json").write_text(json.dumps(result))
    with pytest.raises(probe.ArenaInfrastructureError, match="capture failed"):
        probe.audit_saved_output(output)


def test_fixed_write_flood_contains_only_64_legitimate_alternating_lease_heartbeats():
    leases = {entity: {"entity_id": entity, "session_epoch": 3, "world_epoch": 4, "control_generation": entity+10}
              for entity in (1, 2)}
    commands = probe.write_flood(leases)
    assert len(commands) == 64 and all(command["cmd"] == "body_heartbeat" for command in commands)
    assert [command["entity_id"] for command in commands] == [1, 2]*32
    assert all(all(command[key] == leases[command["entity_id"]][key] for key in probe.LEASE_KEYS)
               and command["provider"] == probe.PROVIDER for command in commands)


def test_declared_flood_queries_actions_terminal_and_setup_remain_bounded_without_a_process():
    profile = probe.PROFILE
    assert profile["maximum_batches"]*(profile["batch_size"]+profile["query_batch_size"])+4+64+20 <= 2200
    runner = probe.ProbeRunner.__new__(probe.ProbeRunner)
    with pytest.raises(probe.ArenaInfrastructureError, match="64 complete"):
        runner.batch([{"cmd": "body_heartbeat"}]*65)
