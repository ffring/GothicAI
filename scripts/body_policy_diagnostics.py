#!/usr/bin/env python3
"""Stage 8 paired perception, shadow and realtime policy diagnostics.

The engine supplies every physical state and animation event. Visibility
diagnostics are audit-only oracle telemetry and never enter policy features.
This driver keeps the stage 7 frozen suite unchanged and writes collected
evidence; an independent report decides which implementation/quality gates pass.
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

# A new diagnostic driver may use the immutable stage 7 policy implementation.
# Python otherwise puts this new script directory ahead of PYTHONPATH; make
# the chosen policy/helper source explicit before importing any project module.
_bootstrap = argparse.ArgumentParser(add_help=False)
_bootstrap.add_argument("--policy-code-root", type=Path)
_bootstrap_args, _ = _bootstrap.parse_known_args()
if _bootstrap_args.policy_code_root is not None:
    _policy_root = _bootstrap_args.policy_code_root.resolve()
    for _path in reversed((_policy_root / "body-service/src", _policy_root / "scripts")):
        sys.path.insert(0, str(_path))

from body_arena_env import ArenaInfrastructureError, EpisodeStep, LEASE_KEYS
from body_headless_suite import process_sample, service_snapshot, sha256, write_json
from body_observe_suite import provenance
from body_policy_suite import (PACKAGE, PolicyArenaRunner, PolicyEpisodeEnv, PROFILE,
                               native_fallback_boundary, run_service_probe)
from gothic_body.runtime import PolicyRuntime
import body_policy_suite as policy_suite_module
import body_trajectory_dataset as feature_module
from body_collector_gc import CollectorGCScope


TIMING_PROFILE = {
    "clock": "Linux CLOCK_MONOTONIC, nanoseconds",
    "collector_clock": "time.monotonic_ns",
    "native_clock": "std::chrono::steady_clock; independently checked against IPC bounds",
    "network": "same-host JSONL pipes; no Mac gateway",
    "animation_latency": "native accepted/started/completed events, separately from inference",
    "oracle_visibility_enters_features": False,
}


def observation_key(row):
    return tuple(row[key] for key in (*LEASE_KEYS, "observation_seq"))


def append_json(stream, row):
    stream.write(json.dumps(row, allow_nan=False, separators=(",", ":"))+"\n")
    stream.flush()


def terminal_refusal(response):
    arena = response.get("arena", {})
    native_refusal = (response.get("accepted") is False and response.get("reason") == "arena_terminal")
    legacy_refusal = (response.get("ok") is False and response.get("error") == "arena terminal state rejects new body decisions")
    return ((native_refusal or legacy_refusal) and
            arena.get("infrastructure") is not True and
            (arena.get("terminated") is True or arena.get("truncated") is True))


class DiagnosticRunner(PolicyArenaRunner):
    """Real engine transport with bounded, separate collector timing evidence."""
    def __init__(self, args):
        self.mode = "lockstep"
        self.received: OrderedDict[tuple[int, ...], int] = OrderedDict()
        self.timing = None
        self.last_request_ns = self.last_response_ns = 0
        self.last_response_value = None
        self._evidence_pending = []
        self._evidence_pending_bytes = 0
        self._dispatch_window = False
        super().__init__(args)

    def begin_dispatch_window(self):
        """Buffer proof persistence, never transport, through one fresh dispatch."""
        self._dispatch_window = True

    def _persist(self, destination, encoded):
        if getattr(self, "_dispatch_window", False):
            size = len(encoded.encode("utf-8"))
            if self._evidence_pending_bytes + size > 4*1024*1024:
                raise ArenaInfrastructureError("critical-path evidence exceeds bounded 4 MiB buffer")
            self._evidence_pending.append((destination, encoded))
            self._evidence_pending_bytes += size
        elif isinstance(destination, Path):
            destination.write_text(encoded)
        else:
            destination.write(encoded)
            destination.flush()

    def persist_json(self, stream, row):
        self._persist(stream, json.dumps(row, allow_nan=False, separators=(",", ":"))+"\n")

    def persist_json_file(self, path, row):
        self._persist(Path(path), json.dumps(row, allow_nan=False, indent=2)+"\n")

    def end_dispatch_window(self):
        self._dispatch_window = False
        pending, self._evidence_pending = self._evidence_pending, []
        self._evidence_pending_bytes = 0
        for destination, encoded in pending:
            self._persist(destination, encoded)

    def episode_log(self, output):
        self.end_dispatch_window()
        if self.timing is not None:
            self.timing.close()
        self.received.clear()
        super().episode_log(output)
        self.timing = (output / "collector-transport.jsonl").open("x")

    def record(self, direction, value):
        now = time.monotonic_ns()
        # Capture transport time before serialization/persistence. Deferring a
        # disk flush must never move a request/receipt's audit timestamp.
        self.persist_json(self.ipc, {"direction": direction, "wall_monotonic": now/1e9, "value": value})
        if direction == "request":
            self.last_request_ns = now
        else:
            self.last_response_ns = now
            self.last_response_value = value
            for row in value.get("observations", []):
                self.received[observation_key(row)] = now
                while len(self.received) > 2048:
                    self.received.popitem(last=False)
        if self.timing is not None:
            self.persist_json(self.timing, {"kind": "ipc_clock", "direction": direction,
                                     "wall_monotonic_ns": now, "id": value.get("id"),
                                     "cmd": value.get("cmd"), "game_ms": value.get("game_ms"),
                                     "native_wall_monotonic_ns": value.get("wall_monotonic_ns")})

    def call(self, command):
        if command.get("cmd") in ("reset", "body_acquire"):
            command = {**command, "mode": self.mode}
        try:
            return super().call(command)
        except ArenaInfrastructureError:
            # A free-running native tick can terminate between the collector's
            # final status and its command. Preserve the exact refusal rather
            # than synthesizing an accepted action or an infrastructure fault.
            if command.get("cmd") == "body_action" and isinstance(self.last_response_value, dict) and terminal_refusal(self.last_response_value):
                return self.last_response_value
            raise

    def terminate(self):
        try:
            self.end_dispatch_window()
            super().terminate()
        finally:
            if self.timing is not None:
                self.timing.close()
                self.timing = None


class DiagnosticEpisodeEnv(PolicyEpisodeEnv):
    def __init__(self, runner, scenario, *, owner_provider, poll_ms=5, command_ttl_ms=1000,
                 heartbeat_interval_ms=0, max_stale_refresh_attempts=0, native_deadline_polling=False):
        super().__init__(runner, scenario, owner_provider=owner_provider)
        self.poll_ms = poll_ms
        self.last_poll_ns = 0
        self.native_sim_step = 0
        self.command_ttl_ms = command_ttl_ms
        self.last_running_status_wall_ns = 0
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.max_stale_refresh_attempts = max_stale_refresh_attempts
        self._heartbeat_deadlines = {}
        self._lease_watchdogs = {}
        self._stale_refresh_attempts = Counter()
        self._fresh_refresh_sources = {}
        self.fresh_observations_required = False
        self.native_deadline_polling = native_deadline_polling
        self._native_tick_hint = None
        self._last_numeric_sim_step = None
        self._deadline_polling_counts = Counter()
        self._deadline_wait_records = []
        self.native_guard_waits = {}
        self._native_guard_records = []

    def reset(self, **kwargs):
        self._heartbeat_deadlines.clear()
        self._lease_watchdogs.clear()
        self._stale_refresh_attempts.clear()
        self._fresh_refresh_sources.clear()
        self.fresh_observations_required = False
        self._native_tick_hint = None
        self._last_numeric_sim_step = None
        self._deadline_polling_counts.clear()
        self._deadline_wait_records.clear()
        self.native_guard_waits.clear()
        self._native_guard_records.clear()
        return super().reset(**kwargs)

    def persist(self, stream, row):
        method = getattr(self.runner, "persist_json", None)
        (method or append_json)(stream, row)

    @staticmethod
    def response_actor(response, entity):
        actors = response.get("body_control", {}).get("actors", response.get("actors", []))
        matched = [actor for actor in actors if actor.get("entity_id") == entity]
        return matched[0] if len(matched) == 1 else None

    def eligible(self, entity, action=None):
        if self.native_guard_waits:
            return False
        return super().eligible(entity, action)

    def _refresh_required(self):
        self.fresh_observations_required = bool(self._fresh_refresh_sources or self.native_guard_waits)

    def _update_native_guard_waits(self, response, rows=()):
        for entity, wait in list(self.native_guard_waits.items()):
            now = time.monotonic_ns()
            if (self.arena["game_ms"]-wait["begin_game_ms"] > wait["wait_bound_ms"] or
                    now-wait["begin_wall_monotonic_ns"] > wait["wait_bound_ms"]*1_000_000):
                raise ArenaInfrastructureError("native target guard wait exceeded existing watchdog bound")
            actor = self.response_actor(response, entity)
            if actor is not None:
                if any(actor.get(key) != wait[key] for key in ("session_epoch", "world_epoch", "entity_id")):
                    raise ArenaInfrastructureError("native target guard belongs to another actor epoch")
                native_handoff = actor.get("owner") == "native" and actor.get("control_generation") == 0
                same_owner = (all(actor.get(key) == wait[key] for key in LEASE_KEYS) and
                    actor.get("owner") == "scripted" and actor.get("provider") == self.owner_provider and
                    actor.get("goal_version") == wait["goal_version"] and
                    actor.get("target_entity_id") == wait["target_entity_id"])
                if not (native_handoff or same_owner):
                    raise ArenaInfrastructureError("native target guard ownership/goal/target changed unexpectedly")
                if native_handoff and wait["status"] != "native_handoff":
                    wait["status"] = "native_handoff"
                    self._native_guard_records.append({**wait, "phase": "native_handoff",
                        "game_ms": self.arena["game_ms"], "wall_monotonic_ns": now,
                        "native_response_id": response.get("id"), "native_actor": dict(actor)})
                    self.fallbacks.append({"kind": "native_guard_handoff", "entity_id": entity,
                        "reason": "target_invalid", "t_game_ms": self.arena["game_ms"],
                        "native_response_id": response.get("id")})
            if self.arena.get("terminated") or self.arena.get("truncated"):
                self._native_guard_records.append({**wait, "phase": "native_terminal",
                    "game_ms": self.arena["game_ms"], "wall_monotonic_ns": now,
                    "reason": self.arena.get("reason"), "outcome": self.arena.get("outcome"),
                    "native_response_id": response.get("id")})
                self.native_guard_waits.pop(entity)
                continue
            for row in rows:
                target = row.get("target", {})
                if (wait["status"] == "pending" and actor is not None and same_owner and
                        actor.get("control_allowed") is True and actor.get("release_pending") is False and
                        row.get("periodic") is True and all(row.get(key) == wait[key] for key in LEASE_KEYS) and
                        row["observation_seq"] > wait["native_current_observation_seq"] and
                        row["t_game_ms"] > wait["begin_game_ms"] and row.get("control_allowed") is True and
                        row.get("action_mask_valid") is True and row.get("goal", {}).get("version") == wait["goal_version"] and
                        target.get("entity_id") == wait["target_entity_id"] and target.get("visible") is True and
                        target.get("present") is True and target.get("current_valid") is True):
                    self._native_guard_records.append({**wait, "phase": "recovered",
                        "game_ms": row["t_game_ms"], "wall_monotonic_ns": now,
                        "observation_seq": row["observation_seq"], "native_response_id": response.get("id")})
                    self.native_guard_waits.pop(entity)
                    break
        self._refresh_required()

    def _arena(self, response):
        if self.identity is not None and isinstance(response.get("arena"), dict):
            native = response["arena"]
            for key in ("session_epoch", "world_epoch"):
                if key in native and native[key] != getattr(self.identity, key):
                    raise ArenaInfrastructureError("native status belongs to another episode epoch")
            if response.get("sim_step", self.native_sim_step) < self.native_sim_step or native.get("game_ms", 0) < self.arena.get("game_ms", 0):
                raise ArenaInfrastructureError("native status clock moved backwards")
        self.native_sim_step = response.get("sim_step", self.native_sim_step)
        arena = super()._arena(response)
        if self.native_deadline_polling:
            self._native_tick_hint = None
            deadline = response.get("next_tick_wall_monotonic_ns")
            stamp = response.get("wall_monotonic_ns")
            if (type(deadline) is int and deadline > 0 and type(stamp) is int and stamp > 0 and
                    response.get("clock_domain") == "linux-clock-monotonic" and
                    response.get("mode") == "realtime" and arena.get("started") is True and
                    not (arena.get("terminated") or arena.get("truncated") or response.get("paused"))):
                self._native_tick_hint = {"next_tick_wall_monotonic_ns": deadline,
                    "native_response_wall_monotonic_ns": stamp, "response_id": response.get("id"),
                    "sim_step": self.native_sim_step, "game_ms": arena.get("game_ms")}
                self._deadline_polling_counts["valid_hints"] += 1
            elif deadline is not None:
                self._deadline_polling_counts["invalid_hints"] += 1
        if (arena.get("started") is True and not (arena.get("terminated") or arena.get("truncated")) and
                response.get("clock_domain") == "linux-clock-monotonic" and type(response.get("wall_monotonic_ns")) is int):
            self.last_running_status_wall_ns = response["wall_monotonic_ns"]
        self._update_native_guard_waits(response)
        return arena

    def heartbeat(self):
        for entity, lease in list(self.leases.items()):
            if self.native_guard_waits.get(entity, {}).get("status") == "native_handoff":
                continue
            identity = tuple(lease[key] for key in LEASE_KEYS)
            now = time.monotonic_ns()
            interval = min(self.heartbeat_interval_ms, 250, self._lease_watchdogs.get(identity, self.watchdog_ms)/4)
            if interval > 0 and now < self._heartbeat_deadlines.get(identity, 0):
                continue
            response = self.runner.call({"cmd": "body_heartbeat", **lease, "provider": self.owner_provider})
            self._deadline_polling_counts["heartbeat_requests"] += 1
            native_actor = self.response_actor(response, entity)
            timeout = native_actor.get("watchdog_timeout_ms") if native_actor is not None else None
            if type(timeout) is int and timeout > 0:
                self._lease_watchdogs[identity] = timeout
                interval = min(self.heartbeat_interval_ms, 250, timeout/4)
            # Schedule from request time so transport latency cannot extend a
            # watchdog. A new generation has its own renewal deadline.
            self._heartbeat_deadlines[identity] = now+int(max(0, interval)*1e6)
            if isinstance(response.get("arena"), dict):
                self._arena(response)
            if self.arena.get("terminated") or self.arena.get("truncated"):
                return
            if self.native_guard_waits.get(entity, {}).get("status") == "native_handoff":
                continue
            if response.get("accepted") is True:
                continue
            actor = native_actor or self.body_status(entity)
            if actor.get("control_generation") != lease["control_generation"]:
                self.leases.pop(entity)
                self.fallbacks.append({"kind": "native_ownership_loss", "entity_id": entity,
                                       "reason": response.get("reason"), "t_game_ms": self.arena["game_ms"]})
            elif response.get("reason") == "release_pending" and actor.get("release_pending") is True:
                continue
            else:
                raise ArenaInfrastructureError(f"diagnostic heartbeat rejected: {response.get('reason')}")

    def pull(self):
        response = self.runner.call({"cmd": "body_observations", "max": 256})
        self._deadline_polling_counts["numeric_queries"] += 1
        if isinstance(response.get("arena"), dict):
            self._arena(response)
        self._last_numeric_sim_step = response.get("sim_step", self.native_sim_step)
        rows = response.get("observations", response.get("rows"))
        if not isinstance(rows, list):
            raise ArenaInfrastructureError("numeric observation packet has no rows")
        for row in rows:
            if self.identity is not None and any(row.get(key) != getattr(self.identity, key) for key in ("session_epoch", "world_epoch")):
                raise ArenaInfrastructureError("numeric observation belongs to another episode epoch")
            entity, sequence = row["entity_id"], row["observation_seq"]
            if sequence <= self.processed_sequences.get(entity, 0):
                raise ArenaInfrastructureError("repeated or backwards numeric observation")
            self.processed_sequences[entity] = sequence
            lease = self.leases.get(entity)
            terminal = self.arena.get("terminated") or self.arena.get("truncated")
            if terminal or lease is None or all(row.get(key) == lease[key] for key in LEASE_KEYS):
                self.latest[entity] = row
            refresh = self._fresh_refresh_sources.get(entity)
            if (refresh is not None and row.get("periodic") is True and
                    all(row.get(key) == refresh[key] for key in LEASE_KEYS) and
                    row["observation_seq"] > refresh["observation_seq"]):
                self._fresh_refresh_sources.pop(entity)
        self.pulled.extend(rows)
        self._update_native_guard_waits(response, rows)
        return tuple(rows)

    def _wait_native_deadline(self):
        """Wait only while the known native tick has not yet become due."""
        hint = self._native_tick_hint
        now = time.monotonic_ns()
        if (not self.native_deadline_polling or hint is None or self.poll_ms <= 0 or
                hint["next_tick_wall_monotonic_ns"] <= now or
                (self.leases and self.heartbeat_interval_ms <= 0)):
            if self.native_deadline_polling:
                self._deadline_polling_counts["legacy_poll_batches"] += 1
            return False
        # A heartbeat may have observed a completed physical tick already.
        # Drain it now instead of following its newly scheduled next deadline.
        if self._last_numeric_sim_step is not None and hint["sim_step"] > self._last_numeric_sim_step:
            self._deadline_polling_counts["already_advanced_batches"] += 1
            return True
        if len(self._deadline_wait_records) >= 65536:
            raise ArenaInfrastructureError("native deadline wait evidence exceeded bounded capacity")
        record = {"kind": "native_deadline_wait", "begin_wall_monotonic_ns": now, **hint,
                  "poll_ms": self.poll_ms, "sleep_calls": 0, "requested_sleep_ns": 0,
                  "actual_sleep_ns": 0}
        initial_deadline = hint["next_tick_wall_monotonic_ns"]
        while not (self.arena.get("terminated") or self.arena.get("truncated")):
            hint = self._native_tick_hint
            now = time.monotonic_ns()
            if hint is None or now >= min(initial_deadline, hint["next_tick_wall_monotonic_ns"]):
                break
            if self._last_numeric_sim_step is not None and hint["sim_step"] > self._last_numeric_sim_step:
                break
            deadline = min(initial_deadline, hint["next_tick_wall_monotonic_ns"])
            identities = [tuple(lease[key] for key in LEASE_KEYS) for lease in self.leases.values()]
            heartbeat_due = min((self._heartbeat_deadlines.get(identity, now) for identity in identities),
                                default=deadline)
            if heartbeat_due <= now:
                self.heartbeat()
                continue
            wake = min(deadline, heartbeat_due, now+int(min(self.poll_ms, 5)*1e6))
            duration = wake-now
            before = time.monotonic_ns()
            time.sleep(duration/1e9)
            after = time.monotonic_ns()
            record["sleep_calls"] += 1
            record["requested_sleep_ns"] += duration
            record["actual_sleep_ns"] += max(0, after-before)
        record["end_wall_monotonic_ns"] = time.monotonic_ns()
        self._deadline_wait_records.append(record)
        self._deadline_polling_counts["deadline_wait_batches"] += 1
        self._deadline_polling_counts["sleep_calls"] += record["sleep_calls"]
        return True

    def finalize(self):
        result = super().finalize()
        result["native_deadline_polling"] = {"enabled": self.native_deadline_polling,
            "clock_domain": "linux-clock-monotonic", "counters": dict(self._deadline_polling_counts),
            "wait_records": list(self._deadline_wait_records)}
        result["native_guard_waits"] = {"records": list(self._native_guard_records),
                                        "pending": list(self.native_guard_waits.values())}
        return result

    def step(self, substeps=1):
        if self.runner.mode == "lockstep":
            return super().step(substeps)
        if self.identity is None:
            raise ArenaInfrastructureError("realtime polling requires an episode reset")
        if self.arena.get("terminated") or self.arena.get("truncated"):
            return EpisodeStep(self.arena, (), (), 0)
        self.heartbeat()
        if not self._wait_native_deadline():
            remaining = self.poll_ms/1000-(time.monotonic_ns()-self.last_poll_ns)/1e9
            if remaining > 0:
                time.sleep(remaining)
        if self.arena.get("terminated") or self.arena.get("truncated"):
            return EpisodeStep(self.arena, (), (), 0)
        self.last_poll_ns = time.monotonic_ns()
        before = self.native_sim_step
        response = self.runner.call({"cmd": "body_events"})
        self._deadline_polling_counts["event_queries"] += 1
        if isinstance(response.get("arena"), dict):
            self._arena(response)
        events = response.get("events")
        if not isinstance(events, list):
            raise ArenaInfrastructureError("realtime native events missing")
        self.events.extend(events)
        # Numeric delivery is the final RPC before inference. Status/control
        # polling after this point can supersede its exact periodic anchor.
        begin = getattr(self.runner, "begin_dispatch_window", None)
        if begin is not None:
            begin()
        observations = self.pull()
        return EpisodeStep(self.arena, observations, tuple(events), max(0, self.native_sim_step-before))

    def try_act(self, entity, action):
        """Record races against a free-running engine as real rejected decisions."""
        if self.runner.mode == "lockstep" and self.command_ttl_ms == 1000:
            return self.act(entity, action)
        if self.arena.get("terminated") or self.arena.get("truncated") or not self.eligible(entity, action):
            raise ArenaInfrastructureError("realtime attempted an unavailable sampled primitive")
        row = self.latest[entity]
        self.command_ids[entity] += 1
        request = {"cmd": "body_action", **self.leases[entity], "provider": self.owner_provider,
                   "goal_version": row["goal"]["version"], "target_entity_id": row["target"]["entity_id"],
                   "command_id": self.command_ids[entity], "action_id": action,
                   "observation_seq": row["observation_seq"], "issued_game_ms": row["t_game_ms"], "ttl_ms": self.command_ttl_ms}
        response = self.runner.call(request)
        receipt = {"request": request, "response": response,
                   "sent_wall_monotonic_ns": self.runner.last_request_ns,
                   "received_wall_monotonic_ns": self.runner.last_response_ns}
        self.command_responses.append(receipt)
        self.latest.pop(entity, None)
        if isinstance(response.get("arena"), dict):
            self._arena(response)
        if terminal_refusal(response):
            if any(response["arena"].get(key) != request[key] for key in ("session_epoch", "world_epoch")):
                raise ArenaInfrastructureError("terminal decision refusal belongs to another episode")
            self._arena(response)
            receipt["terminal_transition"] = True
            return None
        if response.get("accepted") is True:
            self._stale_refresh_attempts.pop(entity, None)
            self._fresh_refresh_sources.pop(entity, None)
            self._refresh_required()
            self.decisions.append({**request, "execution_id": response["execution_id"]})
            return request["command_id"]
        if self.runner.mode == "lockstep":
            raise ArenaInfrastructureError(f"fresh lockstep decision refused: {response.get('reason')}")
        if response.get("reason") not in ("stale_observation", "masked", "expired", "expired_wall", "target_invalid",
                                           "command_busy", "release_pending", "not_owned", "stale_generation"):
            raise ArenaInfrastructureError(f"unexpected realtime decision refusal: {response.get('reason')}")
        actor = self.response_actor(response, entity)
        live_actors = {item.get("entity_id"): item for item in self.arena.get("actors", [])}
        # A current physical mask may change between periodic samples even
        # while its latest raw sequence remains unchanged. Both races require
        # native proof of healthy ownership and a fresh periodic inference;
        # neither permits resending the refused action.
        healthy_control = (actor is not None and
            all(actor.get(key) == request[key] for key in LEASE_KEYS) and
            actor.get("owner") == "scripted" and actor.get("provider") == self.owner_provider and
            actor.get("release_pending") is False and actor.get("control_allowed") is True and
            actor.get("goal_version") == request["goal_version"] and
            actor.get("target_entity_id") == request["target_entity_id"] and
            live_actors.get(entity, {}).get("hp", 0) > 0 and
            live_actors.get(request["target_entity_id"], {}).get("hp", 0) > 0 and
            not (self.arena.get("terminated") or self.arena.get("truncated") or self.arena.get("infrastructure")))
        native_sequence = actor.get("observation_seq") if actor is not None else None
        native_mask = actor.get("action_mask") if actor is not None else None
        native_guard_loss = (response.get("reason") == "target_invalid" and actor is not None and
            type(response.get("execution_id")) is int and response["execution_id"] == 0 and
            all(actor.get(key) == request[key] for key in LEASE_KEYS) and
            actor.get("owner") == "scripted" and actor.get("provider") == self.owner_provider and
            actor.get("release_pending") is False and actor.get("control_allowed") is False and
            actor.get("goal_version") == request["goal_version"] and actor.get("target_entity_id") == request["target_entity_id"] and
            live_actors.get(entity, {}).get("hp", 0) > 0 and live_actors.get(request["target_entity_id"], {}).get("hp", 0) > 0 and
            type(native_sequence) is int and native_sequence >= request["observation_seq"] and
            type(actor.get("t_game_ms")) is int and actor["t_game_ms"] >= request["issued_game_ms"] and
            isinstance(native_mask, list) and len(native_mask) == 12 and
            all(type(value) is bool and value is False for value in native_mask) and
            not (self.arena.get("terminated") or self.arena.get("truncated") or self.arena.get("infrastructure")))
        if native_guard_loss:
            timeout = actor.get("watchdog_timeout_ms", self.watchdog_ms)
            if type(timeout) is not int or timeout <= 0:
                raise ArenaInfrastructureError("native target guard has no valid existing watchdog bound")
            wait = {**self.leases[entity], "status": "pending", "provider": self.owner_provider,
                "goal_version": request["goal_version"], "target_entity_id": request["target_entity_id"],
                "refused_observation_seq": request["observation_seq"], "native_current_observation_seq": native_sequence,
                "begin_game_ms": actor["t_game_ms"], "begin_wall_monotonic_ns": time.monotonic_ns(),
                "wait_bound_ms": min(timeout, self.watchdog_ms), "native_current_action_mask": list(native_mask)}
            self.native_guard_waits[entity] = wait
            self._native_guard_records.append({**wait, "phase": "begin", "native_response_id": response.get("id")})
            receipt.update(native_guard_wait_pending=True, native_guard_wait=dict(wait))
            self._fresh_refresh_sources.pop(entity, None)
            self._refresh_required()
            return None
        healthy_stale = (healthy_control and response.get("reason") == "stale_observation" and
                         type(native_sequence) is int and native_sequence > request["observation_seq"])
        healthy_mask_change = (healthy_control and response.get("reason") == "masked" and
            type(native_sequence) is int and native_sequence >= request["observation_seq"] and
            type(actor.get("t_game_ms")) is int and actor["t_game_ms"] > request["issued_game_ms"] and
            isinstance(native_mask, list) and len(native_mask) == 12 and
            all(type(value) is bool for value in native_mask) and native_mask[action] is False)
        if response.get("reason") == "masked" and not healthy_mask_change:
            raise ArenaInfrastructureError("unproven realtime mask-change refusal")
        if (healthy_stale or healthy_mask_change) and self._stale_refresh_attempts[entity] < self.max_stale_refresh_attempts:
            self._stale_refresh_attempts[entity] += 1
            receipt.update(stale_refresh_pending=True, recovery_attempt=self._stale_refresh_attempts[entity],
                           refresh_reason=response["reason"],
                           superseded_observation_seq=request["observation_seq"],
                           native_current_observation_seq=actor["observation_seq"])
            if healthy_mask_change:
                receipt.update(native_current_action_mask=list(native_mask),
                               native_current_game_ms=actor["t_game_ms"])
            self.fresh_observations_required = True
            self._fresh_refresh_sources[entity] = {key: request[key] for key in (*LEASE_KEYS, "observation_seq")}
            return None
        self._fresh_refresh_sources.pop(entity, None)
        self._refresh_required()
        if healthy_stale or healthy_mask_change:
            # Ownership is healthy and nothing ran: the decision is simply missed. It stays
            # in the control-fraction denominator, so a collector that keeps missing fails
            # the >=95% gate instead of crashing the arena through an explicit release.
            self._stale_refresh_attempts.pop(entity, None)
            receipt.update(decision_missed=True, refresh_reason=response["reason"],
                           superseded_observation_seq=request["observation_seq"],
                           native_current_observation_seq=actor["observation_seq"])
            return None
        self.release_to_native(entity, "realtime_decision_rejected:"+str(response.get("reason")))
        return None


def process_rows(env, policy, actors, streams, journal, latency, stats, *, shadow, command_enabled):
    command_enabled = command_enabled and not bool(getattr(env, "native_guard_waits", {}))
    rows, env.pulled = env.pulled, []
    for row in rows:
        entity = row["entity_id"]
        if entity not in actors:
            continue
        if row.get("periodic") is not True:
            stats["event_only_observations"] += 1
            continue
        stats["periodic_observations"] += 1
        if not shadow:
            lease = env.leases.get(entity)
            if lease is None or any(row.get(key) != lease[key] for key in LEASE_KEYS):
                env.persist(journal, {"kind": "excluded_observation", "reason": "outside_policy_generation",
                                      "observation": row, "profile": PROFILE})
                stats["unowned_periodic_observations"] += 1
                continue
        streams[entity] += 1
        request = {"stream_seq": streams[entity], "observation": row, "source": "periodic_observer",
                   "now_game_ms": env.arena["game_ms"], "checkpoint_sha256": policy.checkpoint_sha256,
                   "command_enabled": command_enabled and not shadow,
                   "memory_only": shadow or not command_enabled or row.get("can_decide") is not True}
        begin = time.monotonic_ns()
        response = policy.process(request)
        finish = time.monotonic_ns()
        clock = {"collector_received_wall_monotonic_ns": env.runner.received.get(observation_key(row)),
                 "process_begin_wall_monotonic_ns": begin, "process_end_wall_monotonic_ns": finish}
        env.persist(journal, {"kind": "model_input", "request": request, "response": response,
                              "profile": PROFILE, "timing": clock, "shadow": shadow})
        env.persist(latency, {"kind": "model_step", "observation_key": list(observation_key(row)),
                              **clock, "model_step_ms": response.get("inference_ms"),
                              "model_processed": response.get("model_processed"), "shadow": shadow})
        stats["model_processed"] += int(response.get("model_processed") is True)
        stats["runtime_fallbacks"] += int(response.get("status") == "fallback")
        if response.get("status") == "fallback" and not shadow and command_enabled:
            env.release_to_native(entity, response["reason"])


def finish_episode(env, policy, streams, journal, latency, stats, *, shadow):
    end = getattr(env.runner, "end_dispatch_window", None)
    if end is not None:
        end()
    result = env.finalize()
    primary = env.initial["primary_entity_id"]
    if policy is not None:
        process_rows(env, policy, (primary,), streams, journal, latency, stats,
                     shadow=shadow, command_enabled=False)
    final_native_status = env.runner.call({"cmd": "body_control_status"})
    mechanics = [json.loads(line) for line in (env.output / "events.jsonl").read_text().splitlines() if line]
    boundary = native_fallback_boundary(env.initial, result["final"], env.events, mechanics) if not shadow else None
    if boundary is not None:
        if boundary["restorations_before_final"] or boundary["same_final_native_continuations"]:
            env.fallbacks.append({"kind": "native_execution_boundary", "evidence": boundary})
    opportunities = stats["latest_eligible_periodic_opportunities"]
    result.update(model_stats=dict(stats), fallback_events=env.fallbacks,
                  native_fallback_boundary=boundary,
                  neural_control_fraction=(stats["accepted_model_commands"]/opportunities if opportunities else None),
                  control_fraction_denominator="unique latest eligible periodic opportunities",
                  no_fallback_credit=policy is not None and not shadow and not bool(env.fallbacks) and result["final"].get("outcome") != "control_lost",
                  command_responses=env.command_responses)
    if policy is not None:
        result["runtime_status"] = policy.status()
        before = env.output / "policy-final-before-clear.json"
        write_json(before, {"final": result["final"], "runtime_status": result["runtime_status"],
                            "model_stats": dict(stats), "checkpoint_sha256": policy.checkpoint_sha256})
        request = {"cmd": "end_episode", "session_epoch": env.identity.session_epoch,
                   "world_epoch": env.identity.world_epoch, "final_game_ms": result["final"]["game_ms"],
                   "final_before_clear_file": before.name, "final_before_clear_sha256": sha256(before)}
        response = policy.end_episode(env.identity.session_epoch, env.identity.world_epoch)
        if not response.get("accepted"):
            raise ArenaInfrastructureError("diagnostic matching terminal barrier rejected")
        append_json(journal, {"kind": "terminal_barrier", "request": request, "response": response,
                              "wall_monotonic_ns": time.monotonic_ns(), "profile": PROFILE, "shadow": shadow})
        result["terminal_barrier"] = {"request": request, "response": response}
    result["perception_evidence"] = {"event_phase": "visibility_diagnostic", "file": "events.jsonl",
                                     "rows": sum(row.get("phase") == "visibility_diagnostic" for row in mechanics),
                                     "final_native_status": final_native_status,
                                     "oracle_only": True}
    return result


def rollout(env, policy, case, phase_config, journal, latency, *, shadow=False, scripted_strafe=False):
    try:
        return _rollout(env, policy, case, phase_config, journal, latency,
                        shadow=shadow, scripted_strafe=scripted_strafe)
    finally:
        end = getattr(env.runner, "end_dispatch_window", None)
        if end is not None:
            end()


def _rollout(env, policy, case, phase_config, journal, latency, *, shadow=False, scripted_strafe=False):
    primary, opponent = env.initial["primary_entity_id"], env.initial["opponent_entity_id"]
    stats, streams, seen = Counter(), Counter(), set()
    block_attack_sent = strafe_sent = False
    if policy is not None:
        process_rows(env, policy, (primary,), streams, journal, latency, stats,
                     shadow=shadow, command_enabled=False)
    env.start()
    budget = phase_config.get("max_episode_substeps", 2200)
    initial_step = env.native_sim_step
    deadline = time.monotonic()+case["timeout_ms"]/1000+phase_config.get("wall_overhead_s", 30)
    iterations = 0
    while time.monotonic() <= deadline:
        if policy is not None:
            process_rows(env, policy, (primary,), streams, journal, latency, stats,
                         shadow=shadow, command_enabled=not bool(env.arena.get("terminated") or env.arena.get("truncated")))
        if env.arena.get("terminated") or env.arena.get("truncated"):
            return finish_episode(env, policy, streams, journal, latency, stats, shadow=shadow)
        if case["task"] == "block" and not block_attack_sent and not env.fresh_observations_required:
            if env.eligible(opponent, 6):
                env.try_act(opponent, 6)
                block_attack_sent = True
            elif env.eligible(opponent, 2):
                env.try_act(opponent, 2)
        row = env.latest.get(primary)
        if (not env.fresh_observations_required and scripted_strafe and not strafe_sent and
                row is not None and row.get("periodic") is True and env.eligible(primary, 4)):
            before = time.monotonic_ns()
            command_id = env.try_act(primary, 4)
            append_json(journal, {"kind": "scripted_strafe", "observation": row, "action_id": 4,
                                  "command_id": command_id, "command_receipt": env.command_responses[-1],
                                  "wall_monotonic_ns": before, "source": "explicit_diagnostic_command"})
            strafe_sent = True
        elif (not env.fresh_observations_required and policy is not None and not shadow and
                row is not None and env.eligible(primary)):
            key = observation_key(row)
            if row.get("periodic") is True and key not in seen:
                seen.add(key)
                stats["latest_eligible_periodic_opportunities"] += 1
                begin = time.monotonic_ns()
                response = policy.arbitrate(row, stream_seq=streams[primary])
                finish = time.monotonic_ns()
                entry = {"kind": "arbitration", "observation": row, "response": response, "profile": PROFILE,
                         "timing": {"collector_received_wall_monotonic_ns": env.runner.received.get(key),
                                    "arbitration_begin_wall_monotonic_ns": begin,
                                    "arbitration_end_wall_monotonic_ns": finish}}
                if response.get("status") == "decision" and response.get("action_id") is not None:
                    command_id = env.try_act(primary, response["action_id"])
                    receipt = env.command_responses[-1]
                    entry.update(command_id=command_id, command_request=receipt["request"], command_response=receipt["response"],
                                 execution_id=receipt["response"].get("execution_id"))
                    entry["terminal_censored"] = receipt.get("terminal_transition") is True
                    entry["stale_refresh_pending"] = receipt.get("stale_refresh_pending") is True
                    entry["decision_missed"] = receipt.get("decision_missed") is True
                    entry["native_guard_wait_pending"] = receipt.get("native_guard_wait_pending") is True
                    if entry["native_guard_wait_pending"]:
                        entry["native_guard_wait"] = receipt["native_guard_wait"]
                        stats["native_guard_wait_requests"] += 1
                    if entry["stale_refresh_pending"]:
                        entry["recovery_attempt"] = receipt["recovery_attempt"]
                        entry["refresh_reason"] = receipt["refresh_reason"]
                        stats["stale_refresh_requests"] += 1
                    if entry["decision_missed"]:
                        entry["refresh_reason"] = receipt["refresh_reason"]
                        stats["missed_decisions"] += 1
                    stats["accepted_model_commands"] += int(command_id is not None)
                    stats["terminal_censored_commands"] += int(entry["terminal_censored"])
                    stats["rejected_model_commands"] += int(command_id is None and not entry["terminal_censored"])
                    if command_id is not None:
                        stats[f"action_{response['action_id']}"] += 1
                    entry["timing"]["command_sent_wall_monotonic_ns"] = receipt.get("sent_wall_monotonic_ns", env.runner.last_request_ns)
                elif response.get("status") == "fallback":
                    stats["runtime_fallbacks"] += 1
                    env.release_to_native(primary, response["reason"])
                env.persist(journal, entry)
                env.persist(latency, {"kind": "arbitration", "observation_key": list(key), **entry["timing"],
                                      "action_id": response.get("action_id"), "command_id": entry.get("command_id"),
                                      "execution_id": entry.get("execution_id"), "shadow": False})
            elif row.get("periodic") is not True:
                stats["event_only_latest_eligible_skips"] += 1
        end = getattr(env.runner, "end_dispatch_window", None)
        if end is not None:
            end()
        step = env.step(1)
        iterations += 1
        if env.runner.mode == "lockstep" and not step.advanced_substeps and not step.done:
            raise ArenaInfrastructureError("diagnostic lockstep episode made no simulation progress")
        if env.native_sim_step-initial_step > budget or iterations > 20000:
            raise ArenaInfrastructureError("diagnostic episode exceeded bounded step/poll budget")
    raise ArenaInfrastructureError("diagnostic episode wall watchdog expired")


def startup_warmup(policy, steps):
    """Explicit backend-only startup work; no episode, stream, cache or commands."""
    if type(steps) is not int or not 0 <= steps <= 8:
        raise ValueError("startup warmup must have a bounded 0..8 step count")
    before = policy.status()
    if (before["actors"] or before["expected_epochs"] is not None or policy.states or
            policy.active or policy.actor_clocks or policy.initial_game_ms is not None):
        raise ArenaInfrastructureError("startup warmup requires an empty runtime before any episode")
    dimensions = len(feature_module.canonical_feature_names(policy.metadata.get("feature_version", "body-periodic-numeric-v1")))
    if dimensions != 184:
        raise ArenaInfrastructureError("startup warmup feature binding differs from the frozen 184 features")
    timings = []
    for index in range(steps):
        begin = time.monotonic_ns()
        logits, hidden = policy.backend.step([0.0]*dimensions, [True]*12, None)
        finish = time.monotonic_ns()
        del logits, hidden
        timings.append({"step": index+1, "begin_wall_monotonic_ns": begin,
                        "end_wall_monotonic_ns": finish, "elapsed_ms": (finish-begin)/1e6})
    after = policy.status()
    if before != after or policy.states or policy.active or policy.actor_clocks:
        raise ArenaInfrastructureError("backend startup warmup changed runtime episode memory")
    return {"kind": "body_policy_startup_warmup", "steps": steps, "timings": timings,
            "checkpoint_sha256": policy.checkpoint_sha256, "normalizer_sha256": policy.normalizer_sha256,
            "device": getattr(policy.backend, "device", "cpu"), "feature_count": dimensions,
            "feature_values": "synthetic_zero", "head_mask": [True]*12, "state_in": None,
            "returned_hidden_discarded_each_step": True, "model_process_calls": 0,
            "backend_execution_profile": dict(getattr(policy.backend, "execution_profile", {})),
            "commands": 0, "quality_credit": False, "runtime_before": before, "runtime_after": after,
            "backend_source_sha256": sha256(Path(sys.modules[type(policy.backend).__module__].__file__))}


def run_suite(args, config):
    phase_config = config[args.phase]
    cases = phase_config["cases"]
    if args.case:
        cases = [case for case in cases if case["name"] in args.case]
        if {case["name"] for case in cases} != set(args.case):
            raise ValueError("unknown case; measurements use only config-declared cases")
    result = {"schema_version": 1, "kind": "body_policy_stage8_diagnostics", "phase": args.phase,
              "status": "infrastructure_failure", "cases": [], "quality_gate_evaluated": False,
              "profile": PROFILE, "timing_profile": TIMING_PROFILE}
    runner = None
    gc_scope, manifest = None, None
    policies = {}
    try:
        manifest = provenance(args)
        manifest.update(kind=result["kind"], phase=args.phase, profile=PROFILE, timing_profile=TIMING_PROFILE,
                        driver_sha256=sha256(Path(__file__)), device=args.device,
                        checkpoint_sha256={name: sha256(getattr(args, name+"_checkpoint")) for name in ("gru", "mlp")},
                        frozen_stage7_suite_sha256=sha256(Path(policy_suite_module.__file__)),
                        frozen_stage7_suite_path=str(Path(policy_suite_module.__file__).resolve()),
                        feature_packer_sha256=sha256(Path(feature_module.__file__)),
                        feature_packer_path=str(Path(feature_module.__file__).resolve()),
                        policy_code_root=str(_bootstrap_args.policy_code_root.resolve()) if _bootstrap_args.policy_code_root else None,
                        checkpoint_frozen_per_episode=True)
        if args.phase == "probe":
            for name, value in manifest["checkpoint_sha256"].items():
                if value != config["reference"][name+"_checkpoint_sha256"]:
                    raise ArenaInfrastructureError("reference probe checkpoint differs from immutable stage 7 weights")
            if _bootstrap_args.policy_code_root is None:
                raise ArenaInfrastructureError("reference probe requires explicit immutable --policy-code-root")
        manifest["policy_code_sha256"] = {str(path.relative_to(PACKAGE)): sha256(path)
                                          for path in sorted((PACKAGE / "gothic_body").glob("*.py"))}
        write_json(args.output / "manifest.json", manifest)
        runtime_config = config.get("runtime", {})
        args.compact_replies = runtime_config.get("compact_replies", False)
        if getattr(args,"native_serial_workers",False) and runtime_config.get("native_serial_workers",False) is not True:
            raise ValueError("native serial CLI/config profile differs")
        args.native_serial_workers = runtime_config.get("native_serial_workers", False)
        if args.native_serial_workers and not hasattr(sys.modules["body_observe_suite"],"native_worker_arguments"):
            raise ArenaInfrastructureError("serial workers are unsupported by the immutable legacy native launcher")
        warmups = {}
        if runtime_config.get("startup_warmup_steps", 0):
            if args.device != "cpu":
                raise ArenaInfrastructureError("declared startup warmup is CPU only")
            for name in ("gru", "mlp"):
                policies[name] = PolicyRuntime(getattr(args, name+"_checkpoint"), device=args.device, max_actors=4,
                                              inference_cpu_threads=runtime_config.get("inference_cpu_threads", 2))
                warmups[name] = startup_warmup(policies[name], runtime_config["startup_warmup_steps"])
            write_json(args.output / "startup-warmup.json", warmups)
        manifest.update(compact_replies=args.compact_replies, native_serial_workers=args.native_serial_workers, startup_warmup=warmups,
                        startup_warmup_before_any_arena=True)
        write_json(args.output / "manifest.json", manifest)
        gc_scope = CollectorGCScope(
            runtime_config.get("collector_static_gc_freeze", False),
            normalize_bootstrap=runtime_config.get("collector_bootstrap_gc_reset", False),
        )
        if gc_scope.enabled:
            manifest["collector_gc"] = gc_scope.enter()
            write_json(args.output / "collector-gc.json", gc_scope.profile)
            write_json(args.output / "manifest.json", manifest)
        runner = DiagnosticRunner(args)
        runner.mode = "realtime" if args.phase in ("shadow", "realtime") else "lockstep"
        probes = {}
        for case in cases:
            for provider in case.get("policies", phase_config.get("policies", ("native", "gru", "mlp"))):
                if provider not in ("native", "scripted_strafe", "gru", "mlp"):
                    raise ValueError("unknown diagnostic provider")
                shadow = args.phase == "shadow" and provider in ("gru", "mlp")
                policy = None
                if provider in ("gru", "mlp"):
                    checkpoint = getattr(args, provider+"_checkpoint")
                    if sha256(checkpoint) != manifest["checkpoint_sha256"][provider]:
                        raise ArenaInfrastructureError("frozen checkpoint changed")
                    if provider not in policies:
                        policies[provider] = PolicyRuntime(checkpoint, device=args.device, max_actors=4,
                                                          inference_cpu_threads=runtime_config.get("inference_cpu_threads", 2))
                    policy = policies[provider]
                name = case["name"]+"-"+provider
                runner.arena_options = case.get("arena", {})
                env = DiagnosticEpisodeEnv(runner, args.scenario, owner_provider="stage8-"+provider,
                                           poll_ms=config.get("runtime", {}).get("poll_ms", 5),
                                           command_ttl_ms=1000 if args.phase == "probe" else runtime_config.get("command_ttl_ms", 100),
                                           heartbeat_interval_ms=runtime_config.get("heartbeat_interval_ms", 0),
                                           max_stale_refresh_attempts=runtime_config.get("max_stale_refresh_attempts", 0),
                                           native_deadline_polling=runtime_config.get("native_deadline_polling", False))
                native = provider == "native" or shadow
                initial = env.reset(output=args.output / name, task=case["task"], seed=case["seed"], side=case["side"],
                                    distance_cm=case["distance_cm"], timeout_ms=case["timeout_ms"],
                                    provider=("native" if case["task"] == "native_duel" else "native_with_scripted_opponent")
                                    if native else "scripted")
                watch = runner.call({"cmd": "body_visibility_watch", "enabled": True})
                if watch.get("ok") is False:
                    raise ArenaInfrastructureError("native perception diagnostic was not enabled")
                if policy is not None:
                    policy.begin_episode(initial["session_epoch"], initial["world_epoch"], initial_game_ms=initial["game_ms"])
                record = {"name": name, "case": case, "policy": provider, "shadow": shadow,
                          "mode": runner.mode, "command_ttl_ms": env.command_ttl_ms,
                          "runner_pid": runner.process.pid, "initial": initial,
                          "profile": PROFILE, "visibility_watch": watch,
                          "checkpoint_sha256": policy.checkpoint_sha256 if policy is not None else None,
                          "normalizer_sha256": policy.normalizer_sha256 if policy is not None else None}
                with (env.output / "policy-inputs.jsonl").open("x") as journal, (env.output / "latency.jsonl").open("x") as latency:
                    record.update(rollout(env, policy, case, phase_config, journal, latency,
                                          shadow=shadow, scripted_strafe=provider == "scripted_strafe"))
                record["model_journal_sha256"] = sha256(env.output / "policy-inputs.jsonl")
                record["latency_sha256"] = sha256(env.output / "latency.jsonl")
                if policy is not None and provider not in probes:
                    with (env.output / "policy-inputs.jsonl").open() as stream:
                        for line in stream:
                            entry = json.loads(line)
                            if entry.get("kind") == "model_input" and entry.get("response", {}).get("model_processed"):
                                probes[provider] = (entry["request"]["observation"], initial["game_ms"])
                                break
                write_json(env.output / "episode.json", record)
                result["cases"].append(record)
                write_json(args.output / "suite.json", result)
                print(f"stage8 [{name}] mode={runner.mode} shadow={shadow} outcome={record['final'].get('outcome')}", flush=True)
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode:
            raise ArenaInfrastructureError("diagnostic runner did not close cleanly")
        if phase_config.get("service_probes", False):
            result["service_probes"] = {name: run_service_probe(getattr(args, name+"_checkpoint"), row,
                                                               args.output / ("serve-probe-"+name),
                                                               initial_game_ms=initial_time, device=args.device,
                                                               inference_cpu_threads=runtime_config.get("inference_cpu_threads", 2))
                                        for name, (row, initial_time) in probes.items()}
        result.update(status="collected", services_after=service_snapshot(), resources=runner.samples,
                      collector_resource=process_sample(os.getpid()))
        manifest.update(completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(), services_after=result["services_after"])
        write_json(args.output / "manifest.json", manifest)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
        print(f"stage8 diagnostics failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        try:
            if runner is not None:
                result["resources"] = runner.samples
                runner.terminate()
        finally:
            if gc_scope is not None and gc_scope.enabled:
                manifest["collector_gc"] = gc_scope.close()
                write_json(args.output / "collector-gc.json", gc_scope.profile)
                write_json(args.output / "manifest.json", manifest)
            write_json(args.output / "suite.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial-workers",dest="native_serial_workers",action="store_true")
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "config", "scenario",
                 "melee-scenario", "native-reference", "output", "gru-checkpoint", "mlp-checkpoint"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--phase", choices=("probe", "shadow", "realtime", "screening"), default="probe")
    parser.add_argument("--policy-code-root", type=Path, help="immutable code root for reference-policy probes")
    parser.add_argument("--case", action="append")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("native simulations and model forwards run only on dev-ubuntu")
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    args.output.mkdir(exist_ok=False)
    return run_suite(args, json.loads(args.config.read_text()))


if __name__ == "__main__":
    raise SystemExit(main())
