"""Stage 8 collector invariants using protocol receipts, without models/physics."""
from collections import Counter, OrderedDict
import io
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_arena_env import EpisodeIdentity
from body_policy_diagnostics import (DiagnosticEpisodeEnv, DiagnosticRunner,
                                     observation_key, process_rows)
from body_arena_env import ArenaInfrastructureError
import pytest


def observation(*, entity=4, sequence=1, generation=7, periodic=True):
    return {"session_epoch": 1, "world_epoch": 2, "entity_id": entity,
            "control_generation": generation, "observation_seq": sequence, "t_game_ms": 100,
            "periodic": periodic, "can_decide": True, "action_mask_valid": True,
            "action_mask": [False]+[True]*11, "goal": {"version": 3}, "target": {"entity_id": 5}}


class ReceiptRunner:
    mode = "realtime"
    last_request_ns = 1000
    last_response_ns = 2000

    def __init__(self, *, action_accepted=True):
        self.requests = []
        self.received = {}
        self.action_accepted = action_accepted

    def call(self, request):
        self.requests.append(request)
        command = request["cmd"]
        if command == "body_heartbeat":
            return {"accepted": True}
        if command == "arena_status":
            return {"sim_step": 10, "arena": {"game_ms": 100, "terminated": False, "truncated": False}}
        if command == "body_observations":
            return {"sim_step": 10, "arena": {"game_ms": 100, "terminated": False, "truncated": False}, "observations": []}
        if command == "body_events":
            return {"events": []}
        if command == "body_action":
            return {"accepted": self.action_accepted, "reason": "accepted" if self.action_accepted else "stale_observation",
                    "execution_id": 9 if self.action_accepted else None}
        if command == "body_release":
            return {"accepted": True, "reason": "release_pending"}
        raise AssertionError(command)


def env(runner):
    value = DiagnosticEpisodeEnv(runner, Path("arena.bench"), owner_provider="stage8-gru", poll_ms=0)
    value.identity = EpisodeIdentity(1, 2, 3, 4)
    value.arena = {"game_ms": 100, "terminated": False, "truncated": False}
    value.leases[4] = {"session_epoch": 1, "world_epoch": 2, "entity_id": 4, "control_generation": 7}
    value.command_ids[4] = 0
    value.latest[4] = observation()
    return value


def test_realtime_step_only_polls_real_engine_and_never_requests_lockstep_or_fabricates_a_tick():
    runner = ReceiptRunner()
    value = env(runner)
    value.native_sim_step = 9
    step = value.step()
    assert step.advanced_substeps == 1 and value.native_sim_step == 10
    assert [request["cmd"] for request in runner.requests] == ["body_heartbeat", "body_events", "body_observations"]


def test_realtime_stale_command_is_recorded_once_and_loses_native_success_credit():
    runner = ReceiptRunner(action_accepted=False)
    value = env(runner)
    assert value.try_act(4, 4) is None
    assert not value.decisions and 4 not in value.leases
    assert value.command_responses[0]["request"]["observation_seq"] == 1
    assert value.command_responses[0]["response"]["reason"] == "stale_observation"
    assert value.fallbacks[0]["kind"] == "model_fallback"
    assert [request["cmd"] for request in runner.requests] == ["body_action", "body_release"]


def test_realtime_accepted_command_retains_exact_epoch_mask_anchor_and_native_execution_id():
    runner = ReceiptRunner()
    value = env(runner)
    assert value.try_act(4, 4) == 1
    request = runner.requests[0]
    assert (request["session_epoch"], request["world_epoch"], request["control_generation"],
            request["observation_seq"], request["issued_game_ms"]) == (1, 2, 7, 1, 100)
    assert value.decisions[0]["execution_id"] == 9 and 4 not in value.latest


def test_actual_terminal_refusal_preserves_raw_packet_without_inventing_acceptance_or_fallback():
    runner = ReceiptRunner()
    raw = {"ok": True, "accepted": False, "reason": "arena_terminal", "sim_step": 10,
           "arena": {"session_epoch": 1, "world_epoch": 2, "game_ms": 100,
                     "terminated": True, "truncated": False, "infrastructure": False}}
    runner.call = lambda request: raw
    value = env(runner)
    assert value.try_act(4, 4) is None
    assert value.arena["terminated"] and not value.decisions and not value.fallbacks
    receipt = value.command_responses[0]
    assert receipt["response"] is raw and receipt["terminal_transition"] is True
    assert raw["accepted"] is False and raw["reason"] == "arena_terminal"


def test_newer_observation_envelope_closes_episode_before_an_old_latest_row_can_be_issued():
    runner = ReceiptRunner()
    final = {"session_epoch": 1, "world_epoch": 2, "game_ms": 133,
             "terminated": True, "truncated": False, "infrastructure": False}
    row = observation(sequence=2, generation=0)
    runner.call = lambda request: {"sim_step": 10, "arena": final, "observations": [row]}
    value = env(runner)
    value.pull()
    assert value.arena is final and value.latest[4] is row
    with pytest.raises(ArenaInfrastructureError, match="unavailable"):
        value.try_act(4, 4)


def test_status_from_an_older_native_step_cannot_replace_current_arena_context():
    value = env(ReceiptRunner())
    value.native_sim_step = 10
    with pytest.raises(ArenaInfrastructureError, match="backwards"):
        value._arena({"sim_step": 9, "arena": {"game_ms": 100, "terminated": False, "truncated": False}})
    assert value.native_sim_step == 10


class MemoryReceiptPolicy:
    checkpoint_sha256 = "frozen"

    def __init__(self):
        self.requests = []

    def process(self, request):
        self.requests.append(request)
        return {"model_processed": True, "status": "processed", "inference_ms": 1}


def test_shadow_reads_real_unowned_actor_rows_without_acquiring_or_issuing_commands():
    runner = ReceiptRunner()
    value = env(runner)
    value.leases.clear()
    row = observation(generation=0)
    value.pulled = [row, observation(sequence=2, generation=0, periodic=False)]
    runner.received[observation_key(row)] = 1000
    policy, stats = MemoryReceiptPolicy(), Counter()
    journal, latency = io.StringIO(), io.StringIO()
    process_rows(value, policy, (4,), Counter(), journal, latency, stats, shadow=True, command_enabled=True)
    assert len(policy.requests) == 1 and policy.requests[0]["observation"] is row
    assert policy.requests[0]["memory_only"] is True and policy.requests[0]["command_enabled"] is False
    assert stats["model_processed"] == 1 and stats["event_only_observations"] == 1
    assert not runner.requests and not value.leases


def test_owned_policy_never_processes_a_different_generation_as_current_memory():
    value = env(ReceiptRunner())
    value.pulled = [observation(generation=8)]
    policy, stats = MemoryReceiptPolicy(), Counter()
    journal = io.StringIO()
    process_rows(value, policy, (4,), Counter(), journal, io.StringIO(), stats,
                 shadow=False, command_enabled=True)
    assert not policy.requests and stats["unowned_periodic_observations"] == 1
    assert "outside_policy_generation" in journal.getvalue()


def test_collector_observation_receipts_are_bounded_and_do_not_mutate_engine_rows():
    value = DiagnosticRunner.__new__(DiagnosticRunner)
    value.ipc, value.timing = io.StringIO(), io.StringIO()
    value.received = OrderedDict()
    value.last_request_ns = value.last_response_ns = 0
    row = observation(sequence=2049)
    original = dict(row)
    for sequence in range(1, 2050):
        value.record("response", {"id": sequence, "observations": [observation(sequence=sequence)]})
    assert len(value.received) == 2048 and observation_key(observation(sequence=1)) not in value.received
    value.record("response", {"id": 2050, "observations": [row]})
    assert row == original and observation_key(row) in value.received
