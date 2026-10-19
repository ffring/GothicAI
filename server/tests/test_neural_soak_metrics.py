"""The soak must retain evidence and reject real gate failures in short runs too."""

import json
from collections import deque
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

from gothic_ai.bodypilot import PolicyClient
from gothic_ai.protocol import BodyLease
from gothic_ai.scenarios.neural_load import _policy_resets
from gothic_ai.scenarios.neural_soak_metrics import SoakMetrics, rss_window, scan_server_errors


def test_rss_uses_final_twenty_minutes_and_enforces_five_percent():
    samples = [
        {"elapsed_seconds": 0, "rss": 10_000},
        {"elapsed_seconds": 600, "rss": 1000},
        {"elapsed_seconds": 1800, "rss": 1050},
    ]
    window = rss_window(samples, "rss")
    assert window["ok"] and not window["short_window"]
    assert window["start_seconds"] == 600
    samples[-1]["rss"] = 1051
    assert not rss_window(samples, "rss")["ok"]


def test_short_or_missing_rss_is_explicit():
    samples = [{"elapsed_seconds": 0, "rss": 100}, {"elapsed_seconds": 180, "rss": 106}]
    assert rss_window(samples, "rss")["short_window"]
    assert not rss_window(samples, "rss")["ok"]
    assert not rss_window(samples, "missing")["ok"]


def test_log_errors_keep_line_references_without_private_endpoints(tmp_path):
    path = tmp_path / "server.log"
    path.write_text("INFO ready\nWARNING protocol mismatch: game 1, server 2\n"
                    "Traceback (most recent call last):\n"
                    "ERROR bridge tcp://private.invalid:123 failed\n")
    errors = scan_server_errors(path)
    assert errors == [{"line": 2, "kind": "protocol"}, {"line": 3, "kind": "traceback"},
                      {"line": 4, "kind": "bridge"}]
    assert "private" not in json.dumps(errors)


@dataclass
class Summary:
    npc: str = "BANDIT"
    decisions: int = 0
    reason: str = "target_lost"
    policy_timeouts: int = 0
    fallbacks: int = 0
    action_rejections: tuple = ()
    fight_seconds: float = 10
    lease: BodyLease | None = None
    release_confirmed: bool = True
    release_late_seconds: float | None = None
    end_trigger: str = "target_lost"


async def test_observers_chain_and_retain_fights_beyond_the_pilot_deque(tmp_path, monkeypatch):
    now = [0.0]
    history = deque(maxlen=32)
    completions = []

    def completed(fight, summary, finish_at, release_deadline):
        history.append(summary)
        completions.append((finish_at, release_deadline))

    pilot = SimpleNamespace(
        people={}, active_count=0, _completed=completed, summaries=history,
    )
    link = SimpleNamespace(body_leases={}, raw_body_listeners=[])
    monkeypatch.setattr("gothic_ai.scenarios.neural_soak_metrics.rss_bytes", lambda pid: 1000)
    (tmp_path / "game.pid").write_text("123")
    (tmp_path / "server.log").write_text("INFO ready\n")

    async def policy_call(client, command, **fields):
        now[0] += 0.001
        return {"ok": True, "status": "decision", "action_id": 10}

    monkeypatch.setattr(PolicyClient, "call", policy_call)
    client = PolicyClient("tcp://unused.invalid:123")
    metrics = SoakMetrics(link, pilot, tmp_path, clock=lambda: now[0])
    with metrics.observe():
        with _policy_resets() as resets:
            await client.call("reset", seed=1)
            assert resets[client] == [{"seed": 1}]
            for index in range(40):
                client = PolicyClient("tcp://unused.invalid:123")
                lease = BodyLease(
                    npc="BANDIT", provider="neural-body", mode="realtime", session_epoch=1,
                    world_epoch=1, entity_id=1, control_generation=index + 1, goal_version=1,
                    target_entity_id=2, acquired_observation_seq=1,
                )
                scope = lease.model_dump(include={
                    "session_epoch", "world_epoch", "entity_id", "control_generation",
                })
                await client.call("step", observation=scope)
                # _finish clears these fields before it publishes the summary.
                pilot._completed(SimpleNamespace(client=None, lease=None), Summary(
                    decisions=int(index < 37), lease=lease,
                ), now[0], now[0] + 2)
    checks = metrics.write_result(eligible=40, captured=38, release_issues=[])
    assert len(history) == 32 and len(metrics.summaries) == 40
    assert len(completions) == 40
    assert all(deadline - finish_at == pytest.approx(2) for finish_at, deadline in completions)
    assert checks["capture_rate"].startswith("done")
    assert checks["accepted_commands"].startswith("failed")  # 37/40, not 37/(37+0).
    assert metrics.data["metrics"]["policy_decisions"] == 40
    assert metrics.data["metrics"]["accepted_commands"] == 37
    assert all(record["policy_decisions"] == 1 for record in metrics.fight_records)
    assert all(record["service_calls"] == 1 for record in metrics.fight_records)
    assert all(record["wait_200_fraction"] == 1 for record in metrics.fight_records)
    assert PolicyClient.call is policy_call
    assert not link.raw_body_listeners
    lines = [json.loads(line) for line in (tmp_path / "neural_soak.jsonl").read_text().splitlines()]
    assert sum(line["kind"] == "fight_end" for line in lines) == 40
    assert metrics.data["metrics"]["game_rss"]["short_window"]


@pytest.mark.parametrize("reason", [
    "finish_deadline", "bridge_error", "symbol_eval_timeout", "body_watch_failed", "lease_lost",
])
def test_fault_endings_fail_even_without_policy_fallback(tmp_path, reason):
    link = SimpleNamespace(body_leases={})
    pilot = SimpleNamespace(people={}, active_count=0)
    metrics = SoakMetrics(link, pilot, tmp_path)
    metrics.summaries.append(Summary(reason=reason))
    checks = metrics.write_result(eligible=1, captured=1, release_issues=[])
    assert checks["fault_fallbacks"].startswith("failed")
    assert metrics.data["metrics"]["fault_endings"] == {reason: 1}


def test_late_release_updates_retained_snapshots_before_history_eviction(tmp_path):
    lease = BodyLease(
        npc="BANDIT", provider="neural-body", mode="realtime", session_epoch=1,
        world_epoch=1, entity_id=1, control_generation=1, goal_version=1,
        target_entity_id=2, acquired_observation_seq=1,
    )
    pending = Summary(reason="finish_deadline", release_confirmed=False, lease=lease)
    history = deque(maxlen=1)
    pilot = SimpleNamespace(
        people={}, active_count=0, summaries=history, release_orphan_timeout=0,
        _completed=lambda fight, summary, *args: history.append(summary),
    )
    metrics = SoakMetrics(
        SimpleNamespace(body_leases={}, raw_body_listeners=[]), pilot, tmp_path,
    )
    with metrics.observe():
        pilot._completed(None, pending, 1.0, 3.0)
        # PR253 replaces the frozen summary instead of mutating the first snapshot.
        confirmed = replace(pending, release_confirmed=True, release_late_seconds=2.75)
        history[0] = confirmed
        pilot._completed(None, Summary(), 4.0, 6.0)
        assert len(history) == 1 and history[0].lease is None
        assert metrics.summaries[0] is confirmed
        assert metrics.fight_records[0]["summary"]["release_confirmed"]
        assert metrics.fight_records[0]["summary"]["release_late_seconds"] == 2.75

    checks = metrics.write_result(eligible=1, captured=1, release_issues=[])
    assert checks["fault_fallbacks"].startswith("done")
    assert checks["release_clean"].startswith("done")
    assert metrics.data["metrics"]["finish_deadline_count"] == 1
    assert metrics.data["metrics"]["finish_deadline_unconfirmed"] == 0
    assert metrics.data["metrics"]["release_late_count"] == 1
    assert metrics.data["metrics"]["release_late_max_seconds"] == 2.75
    updates = [json.loads(line) for line in (tmp_path / "neural_soak.jsonl").read_text().splitlines()
               if json.loads(line)["kind"] == "release_update"]
    assert len(updates) == 1
    assert updates[0]["summary"]["end_trigger"] == "target_lost"
    assert updates[0]["summary"]["release_late_seconds"] == 2.75


def test_sample_and_result_sync_late_release_even_without_another_completion(tmp_path):
    lease = BodyLease(
        npc="BANDIT", provider="neural-body", mode="realtime", session_epoch=1,
        world_epoch=1, entity_id=1, control_generation=1, goal_version=1,
        target_entity_id=2, acquired_observation_seq=1,
    )
    pending = Summary(reason="finish_deadline", release_confirmed=False, lease=lease)
    history = deque([pending])
    pilot = SimpleNamespace(people={}, active_count=0, summaries=history, release_orphan_timeout=0)
    metrics = SoakMetrics(SimpleNamespace(body_leases={}), pilot, tmp_path)
    metrics._completed(None, pending)
    metrics.sample(force=True)
    history[0] = replace(pending, release_confirmed=True, release_late_seconds=4.0)
    metrics.sample()  # Sync runs even if the ten-second sample interval has not elapsed.
    assert metrics.summaries[0] is history[0]
    assert metrics.fight_records[0]["summary"]["release_late_seconds"] == 4.0
    history[0] = replace(history[0], release_late_seconds=4.25)
    metrics.write_result(eligible=1, captured=1, release_issues=[])
    saved = json.loads((tmp_path / "neural_soak_result.json").read_text())
    assert saved["fights"][0]["summary"]["release_late_seconds"] == 4.25
    assert saved["metrics"]["release_late_max_seconds"] == 4.25


def test_orphan_release_timeout_fails_zero_fault_gate_and_is_sampled(tmp_path):
    pilot = SimpleNamespace(people={}, active_count=0, release_orphan_timeout=1)
    metrics = SoakMetrics(SimpleNamespace(body_leases={}), pilot, tmp_path)
    sample = metrics.sample(force=True)
    assert sample["release_orphan_timeout"] == 1
    checks = metrics.write_result(eligible=1, captured=1, release_issues=[])
    assert checks["fault_fallbacks"].startswith("failed")
    assert "release_orphan_timeout=1" in checks["fault_fallbacks"]
    assert metrics.data["metrics"]["release_orphan_timeout"] == 1


def test_extra_check_failure_is_in_the_only_final_result(tmp_path, monkeypatch):
    link = SimpleNamespace(body_leases={})
    pilot = SimpleNamespace(people={}, active_count=0)
    monkeypatch.setattr("gothic_ai.scenarios.neural_soak_metrics.rss_bytes", lambda pid: 1000)
    (tmp_path / "game.pid").write_text("123")
    (tmp_path / "server.log").write_text("INFO ready\n")
    metrics = SoakMetrics(link, pilot, tmp_path)
    metrics.summaries.append(Summary(decisions=1))
    client = PolicyClient("tcp://unused.invalid:123")
    metrics.actions_by_client[client][10] = 1
    metrics._latencies.append(1.0)
    load_check = "failed (old lease survived loading)"

    checks = metrics.write_result(
        eligible=1, captured=1, release_issues=[], extra_checks={"load": load_check},
    )

    assert all(value.startswith("done") for name, value in checks.items() if name != "load")
    assert checks["load"] == load_check
    assert metrics.data["status"] == "failed"
    saved = json.loads((tmp_path / "neural_soak_result.json").read_text())
    assert saved["status"] == "failed" and saved["checks"]["load"] == load_check
    lines = [json.loads(line) for line in (tmp_path / "neural_soak.jsonl").read_text().splitlines()]
    results = [line for line in lines if line["kind"] == "result"]
    assert len(results) == 1
    assert results[0]["status"] == "failed" and results[0]["checks"]["load"] == load_check


def test_game_intervals_ignore_unleased_resubscription_gaps(tmp_path):
    link = SimpleNamespace(body_leases={})
    pilot = SimpleNamespace(people={}, active_count=0)
    metrics = SoakMetrics(link, pilot, tmp_path)

    def row(game_ms, generation):
        return {
            "session_epoch": 1, "world_epoch": 1, "entity_id": 7,
            "control_generation": generation, "periodic": True, "t_game_ms": game_ms,
        }

    metrics._packet({"observations": [row(100, 0)]})
    metrics._packet({"observations": [row(200, 1), row(250, 1)]})
    # The same unleased actor reappears in a later wave. This 90-second
    # subscription gap must not be reported as a stalled leased observation.
    metrics._packet({"observations": [row(90_100, 0)]})
    metrics._packet({"observations": [row(90_200, 2), row(90_250, 2)]})

    sample = metrics.sample(force=True)
    assert sample["game_interval_count"] == 2
    assert sample["game_interval_p95_ms"] == 50
    assert sample["game_interval_max_ms"] == 50
