"""The soak must retain evidence and reject real gate failures in short runs too."""

import json
from collections import deque
from dataclasses import dataclass
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


async def test_observers_chain_and_retain_fights_beyond_the_pilot_deque(tmp_path, monkeypatch):
    now = [0.0]
    history = deque(maxlen=32)
    pilot = SimpleNamespace(
        people={}, active_count=0, _completed=lambda fight, summary: history.append(summary),
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
                ))
    checks = metrics.write_result(eligible=40, captured=38, release_issues=[])
    assert len(history) == 32 and len(metrics.summaries) == 40
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
