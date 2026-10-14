"""Actual receipt ordering and recovery contracts; no engine or model forwards."""
from collections import Counter, OrderedDict
import io
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import body_policy_diagnostics as delivery
import body_combat_evaluation as evaluation
from body_arena_env import ArenaInfrastructureError, EpisodeIdentity


def row(sequence=1, *, periodic=True, generation=7):
    return {"session_epoch": 1, "world_epoch": 2, "entity_id": 4,
            "control_generation": generation, "observation_seq": sequence, "t_game_ms": 100,
            "periodic": periodic, "can_decide": True, "action_mask_valid": True,
            "action_mask": [True]*12, "goal": {"version": 3}, "target": {"entity_id": 5}}


def actor(sequence=2, *, generation=7, timeout=5000):
    return {"session_epoch": 1, "world_epoch": 2, "entity_id": 4,
            "control_generation": generation, "observation_seq": sequence,
            "observed_game_ms": 100, "goal_version": 3, "target_entity_id": 5,
            "owner": "scripted", "provider": "stage8-gru", "release_pending": False,
            "control_allowed": True, "can_decide": True, "watchdog_timeout_ms": timeout}


def arena():
    return {"session_epoch": 1, "world_epoch": 2, "game_ms": 100,
            "terminated": False, "truncated": False,
            "actors": [{"entity_id": 4, "hp": 300}, {"entity_id": 5, "hp": 300}]}


class Runner:
    mode = "realtime"
    last_request_ns = 1000
    last_response_ns = 2000

    def __init__(self):
        self.requests, self.responses = [], []
        self.received = {}

    def call(self, request):
        self.requests.append(request)
        return self.responses.pop(0)


def environment(runner):
    env = delivery.DiagnosticEpisodeEnv(runner, Path("arena.bench"), owner_provider="stage8-gru",
                                        poll_ms=0, heartbeat_interval_ms=250, max_stale_refresh_attempts=2)
    env.identity = EpisodeIdentity(1, 2, 3, 4)
    env.arena = arena()
    env.leases[4] = {key: row()[key] for key in delivery.LEASE_KEYS}
    env.command_ids[4] = 0
    env.latest[4] = row()
    return env


def test_heartbeat_deadlines_use_actual_short_watchdog_and_new_generation_has_fresh_deadline(monkeypatch):
    runner = Runner()
    runner.responses = [{"accepted": True, "body_control": {"actors": [actor(timeout=500)]}}]*3
    env = environment(runner)
    now = [1_000_000_000]
    monkeypatch.setattr(delivery.time, "monotonic_ns", lambda: now[0])
    env.heartbeat()
    now[0] += 100_000_000
    env.heartbeat()
    assert len(runner.requests) == 1
    now[0] += 25_000_000
    env.heartbeat()
    assert len(runner.requests) == 2
    env.leases[4]["control_generation"] = 8
    env.heartbeat()
    assert len(runner.requests) == 3 and runner.requests[-1]["control_generation"] == 8


def stale(sequence=2, **updates):
    status = actor(sequence)
    status.update(updates)
    return {"accepted": False, "reason": "stale_observation", "arena": arena(),
            "body_control": {"actors": [status]}}


def test_strict_stale_refresh_retains_refusal_and_requires_new_periodic_same_lease_before_inference():
    runner = Runner()
    runner.responses = [stale(), {"observations": []}, {"observations": [row(2, periodic=False)]},
                        {"observations": [row(3)]}, {"accepted": True, "execution_id": 9}]
    env = environment(runner)
    assert env.try_act(4, 9) is None
    receipt = env.command_responses[0]
    assert receipt["response"]["accepted"] is False and receipt["stale_refresh_pending"] is True
    assert not env.fallbacks and 4 in env.leases and env.fresh_observations_required
    env.pull()
    assert env.fresh_observations_required
    env.pull()
    assert env.fresh_observations_required
    env.pull()
    assert not env.fresh_observations_required
    # A fresh inference may choose a different action; old action9 is never
    # resubmitted on sequence3. Both actual attempts stay in raw receipts.
    assert env.try_act(4, 5) == 2
    attempts = [r for r in runner.requests if r["cmd"] == "body_action"]
    assert [(r["observation_seq"], r["action_id"]) for r in attempts] == [(1, 9), (3, 5)]
    assert len(env.command_responses) == 2 and not env._stale_refresh_attempts


@pytest.mark.parametrize("changed", [{"control_generation": 8}, {"provider": "another-owner"},
    {"goal_version": 4}, {"target_entity_id": 6}, {"owner": "native"}, {"release_pending": True},
    {"control_allowed": False}, {"observation_seq": 1}])
def test_stale_refusal_with_changed_control_never_gets_refresh_credit(changed):
    runner = Runner()
    runner.responses = [stale(**changed), {"accepted": True}]
    env = environment(runner)
    assert env.try_act(4, 9) is None
    assert not env.command_responses[0].get("stale_refresh_pending")
    assert env.fallbacks and 4 not in env.leases
    assert [r["cmd"] for r in runner.requests] == ["body_action", "body_release"]


def test_stale_recovery_budget_is_finite_and_never_erases_failed_receipts():
    runner = Runner()
    runner.responses = [stale(2), stale(4), stale(6), {"accepted": True}]
    env = environment(runner)
    for sequence in (1, 3, 5):
        env.latest[4] = row(sequence)
        assert env.try_act(4, 9) is None
    assert len(env.command_responses) == 3
    assert [r.get("recovery_attempt") for r in env.command_responses] == [1, 2, None]
    assert env.fallbacks and runner.requests[-1]["cmd"] == "body_release"


def masked(sequence=1, *, game_ms=133, **updates):
    status = actor(sequence)
    status.update(t_game_ms=game_ms, action_mask=[True]*12)
    status["action_mask"][8] = False
    status.update(updates)
    return {"accepted": False, "reason": "masked", "arena": {**arena(), "game_ms": game_ms},
            "body_control": {"actors": [status]}}


def test_actual_live_mask_change_same_sequence_waits_for_new_periodic_and_never_resends_old_action():
    runner = Runner()
    runner.responses = [masked(), {"observations": []}, {"observations": [row(2, periodic=False)]},
                        {"observations": [{**row(3), "t_game_ms": 150}]},
                        {"accepted": True, "execution_id": 9}]
    env = environment(runner)
    assert env.try_act(4, 8) is None
    receipt = env.command_responses[0]
    assert receipt["response"]["accepted"] is False and receipt["refresh_reason"] == "masked"
    assert receipt["native_current_observation_seq"] == receipt["request"]["observation_seq"] == 1
    assert receipt["native_current_action_mask"][8] is False
    assert receipt["native_current_game_ms"] == 133
    assert not env.decisions and not env.fallbacks and 4 in env.leases
    for _ in range(2):
        env.pull()
        assert env.fresh_observations_required
    env.pull()
    assert not env.fresh_observations_required
    assert env.try_act(4, 5) == 2
    attempts = [r for r in runner.requests if r["cmd"] == "body_action"]
    assert [(r["observation_seq"], r["action_id"]) for r in attempts] == [(1, 8), (3, 5)]
    assert len(env.command_responses) == 2 and not env._stale_refresh_attempts


@pytest.mark.parametrize("changed", [{"control_generation": 8}, {"provider": "foreign-owner"},
    {"goal_version": 4}, {"target_entity_id": 6}, {"owner": "native"}, {"release_pending": True},
    {"control_allowed": False}, {"observation_seq": 0}, {"t_game_ms": 100},
    {"action_mask": [True]*12}, {"action_mask": [0]*12}, {"action_mask": [False]*11}])
def test_unproven_mask_change_is_not_a_recoverable_decision(changed):
    runner = Runner()
    runner.responses = [masked(**changed)]
    env = environment(runner)
    with pytest.raises(ArenaInfrastructureError, match="unproven realtime mask-change"):
        env.try_act(4, 8)
    assert not env.command_responses[0].get("stale_refresh_pending")
    assert not env.decisions and not env._stale_refresh_attempts
    assert [r["cmd"] for r in runner.requests] == ["body_action"]


def test_mask_change_and_stale_sequence_share_one_finite_refresh_budget():
    runner = Runner()
    runner.responses = [stale(2), masked(3), masked(5, game_ms=166), {"accepted": True}]
    env = environment(runner)
    for sequence, action in ((1, 9), (3, 8), (5, 8)):
        env.latest[4] = row(sequence)
        assert env.try_act(4, action) is None
    assert len(env.command_responses) == 3
    assert [r.get("recovery_attempt") for r in env.command_responses] == [1, 2, None]
    assert [r["response"]["reason"] for r in env.command_responses] == ["stale_observation", "masked", "masked"]
    assert not env.decisions and env.fallbacks and 4 not in env.leases
    assert runner.requests[-1]["cmd"] == "body_release"


def target_guard(sequence=2, **updates):
    status = actor(sequence)
    status.update(t_game_ms=133, control_allowed=False, can_decide=False, action_mask=[False]*12)
    status.update(updates)
    return {"accepted": False, "reason": "target_invalid", "execution_id": 0,
            "arena": {**arena(), "game_ms": 133, "started": True}, "body_control": {"actors": [status]}}


def recovered_row(sequence, *, periodic=True):
    value = {**row(sequence, periodic=periodic), "t_game_ms": 150, "control_allowed": True}
    value["target"] = {**value["target"], "visible": True, "present": True, "current_valid": True}
    return value


def guard_observations(rows, *, terminal=False, native=False):
    status = actor(max((r["observation_seq"] for r in rows), default=3))
    status.update(t_game_ms=233 if terminal else 150,
                  control_allowed=not native, action_mask=[False]*12 if native else [True]*12)
    if native:
        status.update(owner="native", control_generation=0, provider="", goal_version=0)
    return {"observations": rows, "arena": {**arena(), "started": True,
            "game_ms": 233 if terminal else 150, "truncated": terminal,
            "reason": "target_visibility_lost" if terminal else "", "outcome": "control_lost" if terminal else "running"},
            "body_control": {"actors": [status]}}


def test_target_guard_hold_preserves_refusal_and_every_periodic_memory_row_until_actual_native_terminal():
    runner = Runner()
    locked = {**row(3), "t_game_ms": 150, "can_decide": False, "control_allowed": False,
              "action_mask": [False]*12, "target": {"entity_id": 5, "visible": False}}
    response = guard_observations([locked])
    response["body_control"]["actors"][0].update(control_allowed=False, can_decide=False, action_mask=[False]*12)
    runner.responses = [target_guard(), response, guard_observations([], terminal=True, native=True)]
    env = environment(runner)
    assert env.try_act(4, 1) is None
    receipt = env.command_responses[0]
    assert receipt["native_guard_wait_pending"] and not receipt.get("stale_refresh_pending")
    assert receipt["response"]["accepted"] is False and receipt["response"]["execution_id"] == 0
    assert env.fresh_observations_required and not env.eligible(4, 1) and not env.fallbacks
    env.pull()
    class MemoryPolicy:
        checkpoint_sha256 = "frozen"
        def __init__(self): self.requests = []
        def process(self, request):
            self.requests.append(request)
            return {"model_processed": True, "status": "processed", "inference_ms": 1}
    policy = MemoryPolicy()
    delivery.process_rows(env, policy, (4,), Counter(), io.StringIO(), io.StringIO(), Counter(),
                          shadow=False, command_enabled=True)
    assert len(policy.requests) == 1
    assert policy.requests[0]["memory_only"] is True and policy.requests[0]["command_enabled"] is False
    assert locked["action_mask"] == [False]*12 and env.native_guard_waits
    env.pull()
    assert env.arena["outcome"] == "control_lost" and not env.native_guard_waits
    assert [r["phase"] for r in env._native_guard_records] == ["begin", "native_handoff", "native_terminal"]
    assert env.fallbacks[0]["kind"] == "native_guard_handoff" and not env.decisions
    assert [r["cmd"] for r in runner.requests] == ["body_action", "body_observations", "body_observations"]


def test_target_guard_can_resume_only_from_new_visible_periodic_and_same_native_lease():
    runner = Runner()
    event = recovered_row(3, periodic=False)
    fresh = recovered_row(4)
    runner.responses = [target_guard(), guard_observations([event]), guard_observations([fresh]),
                        {"accepted": True, "execution_id": 9}]
    env = environment(runner)
    assert env.try_act(4, 1) is None
    env.pull()
    assert env.fresh_observations_required and not env.eligible(4, 5)
    env.pull()
    assert not env.native_guard_waits and not env.fresh_observations_required
    assert env.try_act(4, 5) == 2
    assert [r["phase"] for r in env._native_guard_records] == ["begin", "recovered"]
    assert [(r["observation_seq"], r["action_id"]) for r in runner.requests if r["cmd"] == "body_action"] == [(1, 1), (4, 5)]
    assert not env.fallbacks and len(env.command_responses) == 2


@pytest.mark.parametrize("changed", [{"control_generation": 8}, {"provider": "foreign"},
    {"goal_version": 4}, {"target_entity_id": 6}, {"owner": "native"}, {"release_pending": True},
    {"control_allowed": True}, {"action_mask": [True]*12}, {"action_mask": [0]*12}])
def test_unproven_target_guard_never_gets_native_boundary_wait_credit(changed):
    runner = Runner()
    runner.responses = [target_guard(**changed), {"accepted": True}]
    env = environment(runner)
    assert env.try_act(4, 1) is None
    assert not env.native_guard_waits and not env.command_responses[0].get("native_guard_wait_pending")
    assert not env.decisions and env.fallbacks
    assert [r["cmd"] for r in runner.requests] == ["body_action", "body_release"]


def test_guard_wait_does_not_renew_a_native_handoff_or_discard_owned_history_before_terminal():
    runner = Runner()
    handoff = guard_observations([], native=True)
    handoff.update(accepted=False, reason="stale_generation")
    runner.responses = [target_guard(), handoff, guard_observations([], terminal=True, native=True)]
    env = environment(runner)
    env.try_act(4, 1)
    env.heartbeat()
    assert env.native_guard_waits[4]["status"] == "native_handoff" and 4 in env.leases
    count = len(runner.requests)
    env.heartbeat()
    assert len(runner.requests) == count and env.fresh_observations_required
    env.pull()
    assert not env.native_guard_waits and not env.eligible(4, 1)
    assert not any(r["cmd"] == "body_release" for r in runner.requests)
    assert len(env.fallbacks) == 1


def test_target_guard_wait_is_bounded_and_foreign_goal_during_wait_remains_a_protocol_failure(monkeypatch):
    clock = [1_000_000_000]
    monkeypatch.setattr(delivery.time, "monotonic_ns", lambda: clock[0])
    runner = Runner()
    runner.responses = [target_guard(), guard_observations([])]
    env = environment(runner)
    env.try_act(4, 1)
    clock[0] += 5_000_000_001
    with pytest.raises(ArenaInfrastructureError, match="existing watchdog bound"):
        env.pull()
    assert not any(r["cmd"] == "body_release" for r in runner.requests)
    clock[0] = 1_000_000_000
    changed = guard_observations([])
    changed["body_control"]["actors"][0]["goal_version"] = 4
    runner = Runner()
    runner.responses = [target_guard(), changed]
    env = environment(runner)
    env.try_act(4, 1)
    with pytest.raises(ArenaInfrastructureError, match="goal/target changed"):
        env.pull()
    assert not env.decisions


class PollClock:
    def __init__(self):
        self.now = 1_000_000_000
        self.sleeps = []

    def monotonic_ns(self):
        return self.now

    def sleep(self, seconds):
        duration = round(seconds*1e9)
        self.sleeps.append(duration)
        self.now += duration


def tick_hint(clock, *, deadline=None, sim_step=0, **updates):
    result = {"mode": "realtime", "clock_domain": "linux-clock-monotonic",
              "wall_monotonic_ns": clock.now, "next_tick_wall_monotonic_ns": deadline or clock.now+20_000_000,
              "sim_step": sim_step, "arena": {**arena(), "started": True}}
    result.update(updates)
    return result


def test_native_future_tick_hint_waits_in_at_most_five_ms_wakes_without_empty_rpcs(monkeypatch):
    clock, runner = PollClock(), Runner()
    monkeypatch.setattr(delivery.time, "monotonic_ns", clock.monotonic_ns)
    monkeypatch.setattr(delivery.time, "sleep", clock.sleep)
    env = environment(runner)
    env.native_deadline_polling = True
    env.poll_ms = 5
    env.leases.clear()
    env._last_numeric_sim_step = 0
    env._arena(tick_hint(clock))
    assert env._wait_native_deadline() is True
    assert clock.now == 1_020_000_000 and clock.sleeps == [5_000_000]*4
    assert not runner.requests
    record = env._deadline_wait_records[0]
    assert record["sleep_calls"] == 4 and record["actual_sleep_ns"] == 20_000_000
    assert record["end_wall_monotonic_ns"] == record["next_tick_wall_monotonic_ns"]


def test_deadline_wait_renews_earliest_real_lease_before_native_tick_without_extending_initial_wait(monkeypatch):
    clock, runner = PollClock(), Runner()
    monkeypatch.setattr(delivery.time, "monotonic_ns", clock.monotonic_ns)
    monkeypatch.setattr(delivery.time, "sleep", clock.sleep)
    env = environment(runner)
    env.native_deadline_polling = True
    env.poll_ms = 5
    env._last_numeric_sim_step = 0
    env._arena(tick_hint(clock))
    identity = tuple(env.leases[4][key] for key in delivery.LEASE_KEYS)
    env._heartbeat_deadlines[identity] = clock.now+10_000_000
    request_times = []
    def call(request):
        runner.requests.append(request)
        request_times.append(clock.now)
        return {**tick_hint(clock, deadline=1_020_000_000), "accepted": True,
                "body_control": {"actors": [actor(timeout=500)]}}
    runner.call = call
    assert env._wait_native_deadline() is True
    assert request_times == [1_010_000_000]
    assert [r["cmd"] for r in runner.requests] == ["body_heartbeat"]
    assert clock.now == 1_020_000_000
    assert env._heartbeat_deadlines[identity] == 1_135_000_000
    assert max(clock.sleeps) <= 5_000_000


@pytest.mark.parametrize("updates", [{"next_tick_wall_monotonic_ns": None},
    {"next_tick_wall_monotonic_ns": "1020000000"}, {"next_tick_wall_monotonic_ns": True},
    {"next_tick_wall_monotonic_ns": 0}, {"clock_domain": "remote-wall-clock"},
    {"mode": "lockstep"}, {"paused": True}, {"arena": {**arena(), "started": False}}])
def test_absent_or_invalid_native_deadline_preserves_legacy_polling(updates, monkeypatch):
    clock, runner = PollClock(), Runner()
    monkeypatch.setattr(delivery.time, "monotonic_ns", clock.monotonic_ns)
    monkeypatch.setattr(delivery.time, "sleep", clock.sleep)
    env = environment(runner)
    env.native_deadline_polling = True
    env.poll_ms = 5
    env._arena(tick_hint(clock, **updates))
    assert env._wait_native_deadline() is False
    assert not clock.sleeps and not runner.requests


def test_overdue_or_already_advanced_tick_never_waits_and_default_profile_ignores_hints(monkeypatch):
    clock, runner = PollClock(), Runner()
    monkeypatch.setattr(delivery.time, "monotonic_ns", clock.monotonic_ns)
    monkeypatch.setattr(delivery.time, "sleep", clock.sleep)
    env = environment(runner)
    env.poll_ms = 5
    env._arena(tick_hint(clock))
    assert env._wait_native_deadline() is False and env._native_tick_hint is None
    env.native_deadline_polling = True
    env._arena(tick_hint(clock, deadline=clock.now-1))
    assert env._wait_native_deadline() is False
    env._last_numeric_sim_step = 0
    env._arena(tick_hint(clock, sim_step=1))
    assert env._wait_native_deadline() is True
    assert not clock.sleeps and not runner.requests


def buffered_runner():
    runner = delivery.DiagnosticRunner.__new__(delivery.DiagnosticRunner)
    runner.ipc, runner.timing = io.StringIO(), io.StringIO()
    runner.received = OrderedDict()
    runner._evidence_pending, runner._evidence_pending_bytes, runner._dispatch_window = [], 0, False
    runner.last_request_ns = runner.last_response_ns = 0
    return runner


def test_dispatch_buffer_preserves_original_transport_clocks_and_flushes_only_after_receipt(monkeypatch):
    runner = buffered_runner()
    clock = iter((1_000_000, 2_000_000))
    monkeypatch.setattr(delivery.time, "monotonic_ns", lambda: next(clock))
    journal = io.StringIO()
    runner.begin_dispatch_window()
    runner.record("request", {"id": 1, "cmd": "body_action"})
    runner.persist_json(journal, {"kind": "model_input", "captured_ns": 500_000})
    runner.record("response", {"id": 1, "accepted": True, "observations": [row()]})
    assert not runner.ipc.getvalue() and not journal.getvalue() and not runner.timing.getvalue()
    assert runner.last_request_ns == 1_000_000 and runner.last_response_ns == 2_000_000
    assert runner.received[delivery.observation_key(row())] == 2_000_000
    runner.end_dispatch_window()
    ipc = list(map(json.loads, runner.ipc.getvalue().splitlines()))
    assert [r["direction"] for r in ipc] == ["request", "response"]
    assert [r["wall_monotonic"] for r in ipc] == [.001, .002]
    assert json.loads(journal.getvalue())["captured_ns"] == 500_000


def test_dispatch_evidence_buffer_is_bounded_and_fails_before_losing_a_record():
    runner = buffered_runner()
    runner.begin_dispatch_window()
    with pytest.raises(ArenaInfrastructureError, match="bounded"):
        runner.persist_json(runner.ipc, {"value": "x"*(4*1024*1024)})
    assert not runner._evidence_pending and not runner.ipc.getvalue()


def test_activation_uses_its_authoritative_control_reply_with_no_post_rebase_status_rpc(tmp_path):
    runner = buffered_runner()
    runner.mode = "lockstep"
    runner.requests = []
    response = {"sim_step": 10, "arena": {**arena(), "reaction": {"phase": "active",
                "activation_game_ms": 100, "activation_provider": "scripted"}},
                "body_control": {"actors": [actor(1)]}}
    def call(request):
        runner.requests.append(request)
        runner.record("request", request)
        runner.record("response", response)
        return response
    runner.call = call
    env = environment(runner)
    env.initial = {"primary_entity_id": 4, "opponent_entity_id": 5}
    env.leases[5] = {**env.leases[4], "entity_id": 5, "control_generation": 8}
    env.output = tmp_path
    (tmp_path/"preparation.json").write_text("{}\n")
    env.preparation_rows = [row()]
    journal = io.StringIO()
    record = evaluation.activate_prepared(env, "gru", row(), "realtime", delivery, journal)
    assert [r["cmd"] for r in runner.requests] == ["arena_reaction_activate"]
    assert record["post_activation_status_rpc"] is False and runner.mode == "realtime"
    assert env.latest[4] == row() and env.pulled == [row()]
    assert not journal.getvalue() and not (tmp_path/"activation.json").exists()
    runner.end_dispatch_window()
    assert json.loads((tmp_path/"activation.json").read_text())["response"] == response


def test_startup_backend_warmup_discards_hidden_without_changing_runtime_or_issuing_commands():
    class Backend:
        device = "cpu"
        def __init__(self):
            self.inputs = []
        def step(self, features, mask, state):
            self.inputs.append((features, mask, state))
            return [0.0]*12, {"returned_hidden": len(self.inputs)}
    class Policy:
        checkpoint_sha256, normalizer_sha256 = "frozen-weight", "frozen-norm"
        metadata = {"feature_version": "body-periodic-numeric-v1"}
        states, active, actor_clocks = {}, {}, {}
        initial_game_ms = None
        def __init__(self):
            self.backend = Backend()
        def status(self):
            return {"actors": 0, "expected_epochs": None, "counters": {}}
    policy = Policy()
    proof = delivery.startup_warmup(policy, 2)
    assert len(policy.backend.inputs) == 2
    assert all(len(features) == 184 and set(features) == {0.0} and mask == [True]*12 and state is None
               for features, mask, state in policy.backend.inputs)
    assert proof["runtime_before"] == proof["runtime_after"] and proof["commands"] == 0
    assert proof["model_process_calls"] == 0 and proof["quality_credit"] is False
