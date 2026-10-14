"""Four-actor load requires distinct native identities, without model/engine work."""
from collections import Counter
import io
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_arena_env import ArenaInfrastructureError, EpisodeIdentity, LEASE_KEYS
from body_policy_load import (physical_actor_ids, process_physical_rows, cadence_reset_deadline,
                              arbitrate_physical_actors)
from body_service_load import FOUR_BURST_CADENCE
from body_policy_diagnostics import DiagnosticEpisodeEnv, append_json
import body_policy_load as load_module


def arena():
    return {"load_fixture": True, "actor_pairs": [
        {"pair_index": 0, "primary_entity_id": 11, "opponent_entity_id": 15},
        {"pair_index": 1, "primary_entity_id": 20, "opponent_entity_id": 21}],
        "actors": [{"entity_id": entity} for entity in (11, 15, 20, 21)]}


def test_four_actor_proof_uses_actual_engine_ids_without_assuming_contiguous_numbers():
    assert physical_actor_ids(arena()) == [11, 15, 20, 21]


@pytest.mark.parametrize("change", ["duplicate", "missing_actor", "wrong_actor", "missing_pair", "not_fixture"])
def test_four_actor_load_cannot_be_satisfied_by_reused_rows_or_incomplete_native_mapping(change):
    value = arena()
    if change == "duplicate":
        value["actor_pairs"][1]["opponent_entity_id"] = 11
    elif change == "missing_actor":
        value["actors"].pop()
    elif change == "wrong_actor":
        value["actors"][-1]["entity_id"] = 100
    elif change == "missing_pair":
        value["actor_pairs"].pop()
    else:
        value["load_fixture"] = False
    with pytest.raises(ArenaInfrastructureError):
        physical_actor_ids(value)


class IndependentReceiptPolicy:
    checkpoint_sha256 = "frozen"

    def __init__(self):
        self.inputs = []

    def process(self, request):
        self.inputs.append(request)
        return {"model_processed": True, "status": "processed", "inference_ms": 1}


def test_each_original_physical_row_updates_only_its_independent_runtime_once():
    actors = physical_actor_ids(arena())
    rows = [{"session_epoch": 1, "world_epoch": 2, "entity_id": entity, "control_generation": entity+100,
             "observation_seq": index+1, "t_game_ms": 100, "periodic": True, "can_decide": False}
            for index, entity in enumerate(actors)]
    policies = {entity: IndependentReceiptPolicy() for entity in actors}
    value = SimpleNamespace(pulled=list(rows), arena={"game_ms": 100}, leases={
        row["entity_id"]: {key: row[key] for key in ("entity_id", "session_epoch", "world_epoch", "control_generation")}
        for row in rows}, runner=SimpleNamespace(received={}), persist=append_json)
    streams, stats = Counter(), Counter()
    process_physical_rows(value, policies, streams, io.StringIO(), io.StringIO(), stats, command_enabled=True)
    assert stats["model_processed"] == 4 and not value.pulled
    assert streams == Counter({entity: 1 for entity in actors})
    for row in rows:
        requests = policies[row["entity_id"]].inputs
        assert len(requests) == 1 and requests[0]["observation"] is row and requests[0]["memory_only"] is True


def test_native_guard_load_wait_uses_paced_step_and_keeps_memory_until_actual_handoff(monkeypatch):
    identity = {"session_epoch": 1, "world_epoch": 2, "entity_id": 4, "control_generation": 7}
    source = {**identity, "observation_seq": 1, "t_game_ms": 100, "periodic": True,
              "can_decide": True, "control_allowed": True, "action_mask_valid": True,
              "action_mask": [True]*12, "goal": {"version": 3}, "target": {"entity_id": 5}}
    status = {**identity, "goal_version": 3, "target_entity_id": 5, "owner": "scripted",
              "provider": "stage8-load-gru", "release_pending": False, "control_allowed": False,
              "can_decide": False, "action_mask": [False]*12, "observation_seq": 2,
              "t_game_ms": 133, "watchdog_timeout_ms": 5000}
    running = {"session_epoch": 1, "world_epoch": 2, "game_ms": 133, "started": True,
               "terminated": False, "truncated": False,
               "actors": [{"entity_id": 4, "hp": 300}, {"entity_id": 5, "hp": 300}]}
    locked = {**source, "observation_seq": 3, "t_game_ms": 150, "can_decide": False,
              "control_allowed": False, "action_mask": [False]*12, "target": {"entity_id": 5, "visible": False}}
    final = {**running, "game_ms": 233, "truncated": True,
             "reason": "target_visibility_lost", "outcome": "control_lost"}
    native = {**status, "control_generation": 0, "owner": "native", "provider": "", "goal_version": 0}
    class Runner:
        mode = "realtime"
        last_request_ns = 1000
        last_response_ns = 2000
        received = {}
        def __init__(self):
            self.requests = []
            self.responses = [
                {"accepted": False, "reason": "target_invalid", "execution_id": 0,
                 "arena": running, "body_control": {"actors": [status]}},
                {"accepted": True, "arena": running, "body_control": {"actors": [status]}},
                {"events": [], "arena": {**running, "game_ms": 150}, "body_control": {"actors": [status]}},
                {"observations": [locked], "arena": {**running, "game_ms": 150}, "body_control": {"actors": [status]}},
                {"events": [], "arena": final, "body_control": {"actors": [native]}},
                {"observations": [], "arena": final, "body_control": {"actors": [native]}}]
        def call(self, request):
            self.requests.append(request)
            return self.responses.pop(0)
        def begin_dispatch_window(self): pass
        def end_dispatch_window(self): pass
    class Policy(IndependentReceiptPolicy):
        def arbitrate(self, row, stream_seq):
            return {"status": "decision", "action_id": 1}
    runner = Runner()
    env = DiagnosticEpisodeEnv(runner, Path("arena.bench"), owner_provider="stage8-load-gru",
                               poll_ms=0, heartbeat_interval_ms=250)
    env.identity = EpisodeIdentity(1, 2, 3, 4)
    env.arena = {**running, "game_ms": 100}
    env.initial = {"primary_entity_id": 4, "opponent_entity_id": 5}
    env.leases[4] = {key: identity[key] for key in LEASE_KEYS}
    env.command_ids[4] = 0
    env.latest[4] = source
    env.start = lambda: None
    step_count = []
    actual_step = env.step
    def step():
        step_count.append(True)
        return actual_step()
    env.step = step
    policy = Policy()
    def finish(env, policies, streams, journal, latency, stats):
        return {"model_stats": dict(stats), "fallback_events": env.fallbacks,
                "pending": dict(env.native_guard_waits)}
    monkeypatch.setattr(load_module, "finish_load", finish)
    result = load_module.load_rollout(env, {4: policy}, {"duration_ms": 1000}, io.StringIO(), io.StringIO())
    assert len(step_count) == 2
    assert [r["cmd"] for r in runner.requests] == ["body_action", "body_heartbeat", "body_events",
                                                  "body_observations", "body_events", "body_observations"]
    assert len(policy.inputs) == 1 and policy.inputs[0]["observation"] is locked
    assert policy.inputs[0]["memory_only"] is True and policy.inputs[0]["command_enabled"] is False
    assert result["model_stats"]["rejected_model_commands"] == result["model_stats"]["native_guard_wait_requests"] == 1
    assert result["model_stats"]["accepted_model_commands"] == 0 and not result["pending"]
    assert result["fallback_events"][0]["kind"] == "native_guard_handoff"


def test_cadence_floor_is_calculated_before_reset_using_latest_actual_service_burst_start():
    rows = [{"service": service, "request_begin_wall_monotonic_ns": 100_000_000_000+index*1_000_000}
            for index, service in enumerate(("asr", "tts", "decider", "gemma"))]
    rows.append({"service": "asr", "request_begin_wall_monotonic_ns": 110_000_000_000})
    assert cadence_reset_deadline({"requests": rows}, FOUR_BURST_CADENCE) == 120_003_000_000
    assert cadence_reset_deadline(None, FOUR_BURST_CADENCE) is None
    assert cadence_reset_deadline({"requests": []}, None) is None


@pytest.mark.parametrize("change", ["missing_service", "missing_clock", "zero_clock"])
def test_cadence_floor_cannot_be_inferred_from_absent_service_receipts(change):
    rows = [{"service": service, "request_begin_wall_monotonic_ns": 100_000_000_000}
            for service in ("asr", "tts", "decider", "gemma")]
    if change == "missing_service":
        rows.pop()
    elif change == "missing_clock":
        rows[-1].pop("request_begin_wall_monotonic_ns")
    else:
        rows[-1]["request_begin_wall_monotonic_ns"] = 0
    with pytest.raises(ArenaInfrastructureError):
        cadence_reset_deadline({"requests": rows}, FOUR_BURST_CADENCE)


def test_stale_refresh_stops_dispatch_batch_and_preserves_rejected_opportunity_without_credit():
    actors = physical_actor_ids(arena())
    rows = {entity: {"session_epoch": 1, "world_epoch": 2, "entity_id": entity,
                     "control_generation": entity+100, "observation_seq": index+1,
                     "t_game_ms": 100, "periodic": True} for index, entity in enumerate(actors)}
    calls = []
    class Policy:
        def arbitrate(self, row, *, stream_seq):
            calls.append(row["entity_id"])
            return {"status": "decision", "action_id": 6}
    value = SimpleNamespace(latest=rows, eligible=lambda entity: True, fresh_observations_required=False,
                            runner=SimpleNamespace(received={}), command_responses=[], persist=append_json)
    def reject(entity, action):
        value.command_responses.append({"response": {"accepted": False, "reason": "stale_observation"},
                                       "request": {"entity_id": entity, "action_id": action},
                                       "sent_wall_monotonic_ns": 1, "stale_refresh_pending": True,
                                       "recovery_attempt": 1})
        value.fresh_observations_required = True
        return None
    value.try_act = reject
    stats, journal = Counter(), io.StringIO()
    arbitrate_physical_actors(value, {entity: Policy() for entity in actors}, Counter(),
                              journal, io.StringIO(), stats, set())
    assert calls == actors[:1]
    assert stats["latest_eligible_periodic_opportunities"] == 1
    assert stats["rejected_model_commands"] == 1 and stats["accepted_model_commands"] == 0
    assert stats["stale_refresh_requests"] == 1 and stats["runtime_fallbacks"] == 0
    import json
    receipt = json.loads(journal.getvalue())
    assert receipt["command_id"] is None and receipt["command_response"]["accepted"] is False
    assert receipt["command_response"]["reason"] == "stale_observation"
