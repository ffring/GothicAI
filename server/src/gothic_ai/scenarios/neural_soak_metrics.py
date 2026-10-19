"""Scenario-local P30 soak observations; no changes to policy or pilot behavior."""

from __future__ import annotations

import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..bodypilot import BodyPilot, FightSummary, PolicyClient
from ..game import GameLink

WAIT_200 = 10
FAULT_ENDINGS = frozenset({
    "policy_error", "policy_timeout", "body_action_rejected", "lease_lost",
    "observation_overflow", "pilot_error", "acquire_uncertain", "watch_refused",
    "bridge_error", "symbol_eval_timeout", "symbol_eval_failed",
    "body_watch_timeout", "body_watch_failed", "disconnected",
})


def percentile(values: list[float], fraction: float) -> float | None:
    """Use the pilot's nearest-rank definition, including the slowest tail sample."""
    return sorted(values)[math.ceil(len(values) * fraction) - 1] if values else None


def rss_bytes(pid: int) -> int | None:
    """Resident pages in Linux procfs; do not start an extra monitoring process."""
    try:
        return int(Path(f"/proc/{pid}/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def rss_window(samples: list[dict[str, Any]], key: str, seconds: float = 1200) -> dict[str, Any]:
    """Compare observed endpoints of the final 20 minutes, or all of a short smoke run."""
    if not samples:
        return {"ok": False, "reason": "no samples", "short_window": True}
    end = samples[-1]["elapsed_seconds"]
    selected = [sample for sample in samples if sample["elapsed_seconds"] >= end - seconds]
    first, last = selected[0], selected[-1]
    before, after = first.get(key), last.get(key)
    growth = (after / before - 1) * 100 if before and after is not None else None
    observed = last["elapsed_seconds"] - first["elapsed_seconds"]
    return {
        "ok": growth is not None and after * 100 <= before * 105,
        "start_seconds": first["elapsed_seconds"], "end_seconds": last["elapsed_seconds"],
        "observed_seconds": observed, "short_window": end < seconds,
        "start_bytes": before, "end_bytes": after, "growth_percent": growth,
    }


def scan_server_errors(path: Path) -> list[dict[str, Any]]:
    """Keep line references, avoiding private endpoint text from exception messages."""
    if not path.is_file():
        return [{"line": None, "kind": "server_log_missing"}]
    patterns = {
        "traceback": re.compile(r"Traceback \(most recent call last\)"),
        "protocol": re.compile(r"protocol mismatch|unparsed message from game"),
        "bridge": re.compile(
            r"body packet changes watched actors|neural-body (?:watch setup|ownership poll) failed"
            r"|game session failed|(?:ERROR|CRITICAL).*?(?:bridge|gothic_ai\.game|body command)",
            re.IGNORECASE,
        ),
    }
    found = []
    for line_number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        for kind, pattern in patterns.items():
            if pattern.search(line):
                found.append({"line": line_number, "kind": kind})
    return found


class SoakMetrics:
    """Use ``observe()`` around the scenario and call ``sample()`` on each polling tick.

    ``PolicyClient.call`` is wrapped by delegation, so nested ``_policy_resets``
    observations retain their behavior. Summary interception runs before the pilot's
    bounded deque can discard a completed fight. All wrappers are removed on exit.
    """

    def __init__(
        self, link: GameLink, pilot: BodyPilot, log_dir: Path, *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.link, self.pilot, self.log_dir, self.clock = link, pilot, Path(log_dir), clock
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.started = clock()
        self._last_sample = float("-inf")
        self.summaries: list[FightSummary] = []
        self.fight_records: list[dict[str, Any]] = []
        self._summary_indices: dict[tuple[int, ...], int] = {}
        self.samples: list[dict[str, Any]] = []
        self.actions_by_client: dict[PolicyClient, Counter[int]] = defaultdict(Counter)
        self.latencies_by_client: dict[PolicyClient, list[float]] = defaultdict(list)
        self.fallbacks_by_client: dict[PolicyClient, Counter[str]] = defaultdict(Counter)
        self.policy_errors: Counter[str] = Counter()
        self._clients_by_scope: dict[tuple[int, ...], PolicyClient] = {}
        self._service_window: list[float] = []
        self._latencies: list[float] = []
        self._intervals: list[float] = []
        self._previous_game: dict[tuple[int, ...], int] = {}
        self._max_batch_rows = self._max_batch_lag_game_ms = 0
        self._max_batch_rows_all = self._max_batch_lag_all = 0
        self._stream: Any = None
        self.data: dict[str, Any] = {}

    def event(self, kind: str, **data: Any) -> None:
        """Write a wave, load, stall or other scenario event into the same timeline."""
        row = {"kind": kind, "elapsed_seconds": round(self.clock() - self.started, 3), **data}
        encoded = json.dumps(row, ensure_ascii=False, allow_nan=False, default=self._json_default) + "\n"
        if self._stream is None:
            with (self.log_dir / "neural_soak.jsonl").open("a") as stream:
                stream.write(encoded)
        else:
            self._stream.write(encoded)
            self._stream.flush()

    @staticmethod
    def _json_default(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        raise TypeError(f"cannot serialize {type(value).__name__}")

    def _packet(self, packet: dict[str, Any]) -> None:
        rows = packet["observations"]
        if not rows:
            return
        size = len(rows)
        latest = max(row["t_game_ms"] for row in rows)
        lag = latest - min(row["t_game_ms"] for row in rows)
        self._max_batch_rows = max(self._max_batch_rows, size)
        self._max_batch_lag_game_ms = max(self._max_batch_lag_game_ms, lag)
        self._max_batch_rows_all = max(self._max_batch_rows_all, size)
        self._max_batch_lag_all = max(self._max_batch_lag_all, lag)
        for row in rows:
            identity = tuple(row[name] for name in (
                "session_epoch", "world_epoch", "entity_id", "control_generation",
            ))
            previous = self._previous_game.get(identity)
            current = row["t_game_ms"]
            if (row["control_generation"] > 0 and previous is not None
                    and current > previous and row.get("periodic", True)):
                self._intervals.append(current - previous)
            self._previous_game[identity] = max(previous or 0, current)

    @staticmethod
    def _lease_scope(summary: FightSummary) -> tuple[int, ...] | None:
        lease = summary.lease
        return (lease.session_epoch, lease.world_epoch, lease.entity_id,
                lease.control_generation) if lease is not None else None

    def sync_summaries(self) -> None:
        """Retain late native confirmations before the pilot's bounded history evicts them."""
        for summary in getattr(self.pilot, "summaries", ()):
            scope = self._lease_scope(summary)
            index = self._summary_indices.get(scope) if scope is not None else None
            if index is None or self.summaries[index] == summary:
                continue
            self.summaries[index] = summary
            self.fight_records[index]["summary"] = asdict(summary)
            self.event("release_update", summary=asdict(summary))

    def _completed(self, fight: Any, summary: FightSummary) -> None:
        self.summaries.append(summary)
        # BodyFight clears its mutable client/lease before invoking _completed.
        # A scoped immutable lease identifies the policy client from step input.
        scope = self._lease_scope(summary)
        if scope is not None:
            self._summary_indices[scope] = len(self.summaries) - 1
        client = self._clients_by_scope.get(scope)
        actions = self.actions_by_client.get(client, Counter())
        decisions = sum(actions.values())
        latency = self.latencies_by_client.get(client, [])
        record = {
            "summary": asdict(summary),
            # FightSummary.decisions is incremented only AFTER an accepted bridge
            # receipt. Policy decisions also include rejected/superseded commands.
            "policy_decisions": decisions, "accepted_commands": summary.decisions,
            "accepted_fraction": summary.decisions / decisions if decisions else None,
            "actions": {str(key): value for key, value in sorted(actions.items())},
            "wait_200_fraction": actions[WAIT_200] / decisions if decisions else None,
            "service_calls": len(latency), "observed_service_p95_ms": percentile(latency, 0.95),
            "observed_service_p99_ms": percentile(latency, 0.99),
            "policy_fallback_reasons": dict(self.fallbacks_by_client.get(client, {})),
        }
        self.fight_records.append(record)
        self.event("fight_end", **record)

    @contextmanager
    def observe(self) -> Iterator[SoakMetrics]:
        original_call = PolicyClient.call
        original_completed = self.pilot._completed
        had_completed = "_completed" in vars(self.pilot)
        self._stream = (self.log_dir / "neural_soak.jsonl").open("a")

        async def observed(client: PolicyClient, command: str, **fields: Any) -> dict[str, Any]:
            if command != "step":
                return await original_call(client, command, **fields)
            raw = fields.get("observation")
            if isinstance(raw, dict):
                scope = tuple(raw[name] for name in (
                    "session_epoch", "world_epoch", "entity_id", "control_generation",
                ))
                self._clients_by_scope[scope] = client
            start = self.clock()
            try:
                result = await original_call(client, command, **fields)
            except Exception as error:
                self.policy_errors[type(error).__name__] += 1
                raise
            else:
                action = result.get("action_id")
                if result.get("status") == "decision" and type(action) is int:
                    self.actions_by_client[client][action] += 1
                if result.get("status") == "fallback":
                    self.fallbacks_by_client[client][str(result.get("reason"))] += 1
                return result
            finally:
                elapsed = (self.clock() - start) * 1000
                self.latencies_by_client[client].append(elapsed)
                self._latencies.append(elapsed)
                self._service_window.append(elapsed)

        def completed(fight: Any, summary: FightSummary, *args: Any, **kwargs: Any) -> None:
            self.sync_summaries()
            self._completed(fight, summary)
            original_completed(fight, summary, *args, **kwargs)

        PolicyClient.call = observed
        self.pilot._completed = completed
        self.link.raw_body_listeners.append(self._packet)
        try:
            self.sample(force=True)
            yield self
        finally:
            self.sample(force=True)
            self.link.raw_body_listeners.remove(self._packet)
            PolicyClient.call = original_call
            if had_completed:
                self.pilot._completed = original_completed
            else:
                del self.pilot._completed
            self._stream.close()
            self._stream = None

    def sample(self, *, force: bool = False) -> dict[str, Any] | None:
        self.sync_summaries()
        now = self.clock()
        if not force and now - self._last_sample < 10:
            return None
        self._last_sample = now
        try:
            game_pid = int((self.log_dir / "game.pid").read_text().strip())
        except (OSError, ValueError):
            game_pid = None
        leased = {lease.npc.upper() for lease in self.link.body_leases.values()}
        orphans = sorted(npc for npc, entry in self.pilot.people.items() if (
            entry.activity == "body" and npc.upper() not in leased
        ))
        sample = {
            "elapsed_seconds": round(now - self.started, 3),
            "active_leases": [lease.model_dump(mode="json") for lease in self.link.body_leases.values()],
            "lease_count": len(self.link.body_leases), "active_count": self.pilot.active_count,
            "body_without_lease": orphans,
            "release_orphan_timeout": getattr(self.pilot, "release_orphan_timeout", 0),
            "game_rss_bytes": rss_bytes(game_pid) if game_pid else None,
            "server_rss_bytes": rss_bytes(os.getpid()),
            "game_interval_count": len(self._intervals),
            "game_interval_p95_ms": percentile(self._intervals, 0.95),
            "game_interval_max_ms": max(self._intervals, default=None),
            "max_batch_rows": self._max_batch_rows,
            "max_batch_lag_game_ms": self._max_batch_lag_game_ms,
            "service_calls": len(self._service_window),
            "service_p95_ms": percentile(self._service_window, 0.95),
            "service_p99_ms": percentile(self._service_window, 0.99),
        }
        self.samples.append(sample)
        self.event("sample", **sample)
        self._service_window.clear()
        self._intervals.clear()
        self._max_batch_rows = self._max_batch_lag_game_ms = 0
        return sample

    def write_result(
        self, *, eligible: int, captured: int, release_issues: list[Any],
        extra: dict[str, Any] | None = None,
        extra_checks: dict[str, str] | None = None,
    ) -> dict[str, str]:
        self.sample(force=True)
        accepted = sum(summary.decisions for summary in self.summaries)
        decisions = sum(sum(actions.values()) for actions in self.actions_by_client.values())
        fault_ends = Counter(summary.reason for summary in self.summaries if (
            summary.reason in FAULT_ENDINGS
            or summary.reason == "finish_deadline" and not (
                summary.release_confirmed and summary.release_late_seconds is not None
            )
            or summary.reason.startswith("policy_fallback:")
            and summary.reason != "policy_fallback:observation_too_old"
        ))
        finish_deadlines = [summary for summary in self.summaries
                            if summary.reason == "finish_deadline"]
        late_releases = [summary.release_late_seconds for summary in self.summaries
                         if summary.release_confirmed and summary.release_late_seconds is not None]
        orphan_timeouts = getattr(self.pilot, "release_orphan_timeout", 0)
        timeouts = sum(summary.policy_timeouts for summary in self.summaries)
        faulty_fallbacks: Counter[str] = Counter()
        for fallbacks in self.fallbacks_by_client.values():
            faulty_fallbacks.update({reason: count for reason, count in fallbacks.items()
                                    if reason != "observation_too_old"})
        game_rss = rss_window(self.samples, "game_rss_bytes")
        server_rss = rss_window(self.samples, "server_rss_bytes")
        p95, p99 = percentile(self._latencies, 0.95), percentile(self._latencies, 0.99)
        log_errors = scan_server_errors(self.log_dir / "server.log")
        checks: dict[str, str] = {}

        def check(name: str, ok: bool, detail: str) -> None:
            checks[name] = f"{'done' if ok else 'failed'} ({detail})"

        check("fault_fallbacks", not (
            fault_ends or timeouts or faulty_fallbacks or self.policy_errors or orphan_timeouts
        ),
              f"ends={dict(fault_ends)}, timeouts={timeouts}, fallbacks={dict(faulty_fallbacks)}, "
              f"policy_errors={dict(self.policy_errors)}, release_orphan_timeout={orphan_timeouts}")
        capture_fraction = captured / eligible if eligible else None
        check("capture_rate", capture_fraction is not None and capture_fraction >= 0.95,
              f"captured={captured}/{eligible}, minimum=95%")
        accept_fraction = accepted / decisions if decisions else None
        check("accepted_commands", accept_fraction is not None and accept_fraction >= 0.95,
              f"accepted={accepted}/{decisions}, minimum=95%")
        check("release_clean", not release_issues, f"issues={len(release_issues)}, limit=2s")
        for name, window in (("game_rss", game_rss), ("server_rss", server_rss)):
            short = window["short_window"] and window.get("growth_percent") is not None
            check(name, window["ok"] or short,
                  f"growth_percent={window.get('growth_percent')}, limit=5%, "
                  f"assessment={'20-minute gate deferred to full run' if short else 'final 20 minutes'}, "
                  f"window_seconds={window.get('observed_seconds')}, short_window={window['short_window']}")
        check("service_latency", p95 is not None and p99 is not None and p95 <= 20 and p99 <= 20,
              f"p95_ms={p95}, p99_ms={p99}, limit=20ms, calls={len(self._latencies)}")
        check("server_log", not log_errors, f"errors={log_errors}, log=server.log")
        checks.update(extra_checks or {})
        self.data = {
            "status": "done" if all(value.startswith("done") for value in checks.values()) else "failed",
            "elapsed_seconds": round(self.clock() - self.started, 3),
            "checks": checks,
            "metrics": {
                "eligible": eligible, "captured": captured, "capture_fraction": capture_fraction,
                "policy_decisions": decisions, "accepted_commands": accepted,
                "accepted_fraction": accept_fraction,
                "action_rejections": dict(sum((Counter(dict(summary.action_rejections))
                                                for summary in self.summaries), Counter())),
                "fault_endings": dict(fault_ends), "policy_timeouts": timeouts,
                "faulty_policy_fallbacks": dict(faulty_fallbacks), "policy_errors": dict(self.policy_errors),
                "fallbacks": sum(summary.fallbacks for summary in self.summaries),
                "release_orphan_timeout": orphan_timeouts,
                "finish_deadline_count": len(finish_deadlines),
                "finish_deadline_unconfirmed": sum(not (
                    summary.release_confirmed and summary.release_late_seconds is not None
                ) for summary in finish_deadlines),
                "release_late_count": len(late_releases),
                "release_late_max_seconds": max(late_releases, default=None),
                "release_issues": release_issues, "game_rss": game_rss, "server_rss": server_rss,
                "service_calls": len(self._latencies), "service_p95_ms": p95, "service_p99_ms": p99,
                "max_batch_rows": self._max_batch_rows_all,
                "max_batch_lag_game_ms": self._max_batch_lag_all,
                "server_log_errors": log_errors,
                "end_reasons": dict(Counter(summary.reason for summary in self.summaries)),
                "long_fights": [record for record in self.fight_records if (
                    (record["summary"]["fight_seconds"] or 0) > 60
                    and record["summary"]["reason"] not in {"hero_down", "target_down"}
                )],
            },
            "fights": self.fight_records, "extra": extra or {},
        }
        (self.log_dir / "neural_soak_result.json").write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, allow_nan=False,
                       default=self._json_default) + "\n"
        )
        self.event("result", status=self.data["status"], checks=checks)
        return checks
