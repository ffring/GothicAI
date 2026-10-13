#!/usr/bin/env python3
"""Read-only validation/reporting for stage 3 native numeric observations.

No simulation or model is started by this module. A partial or unverifiable
stream is reported incomplete, never silently converted into a passed gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import statistics
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "evals/npc-body/body-observe-v1.json"


def strict_json(text: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError(f"non-finite JSON value {value}")

    value = json.loads(text, object_pairs_hook=unique, parse_constant=constant)
    check_finite(value)
    return value


def read_json(path: Path) -> dict[str, Any]:
    value = strict_json(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: JSON object required")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    result = list(iter_jsonl(path))
    if not result:
        raise ValueError(f"{path.name}: no records")
    return result


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    """Stream large native journals without retaining physics/pose rows in RAM."""
    with path.open() as source:
        for line_no, line in enumerate(source, 1):
            if not line.strip():
                raise ValueError(f"{path.name}:{line_no}: empty record")
            try:
                value = strict_json(line)
            except (ValueError, TypeError) as error:
                raise ValueError(f"{path.name}:{line_no}: {error}") from error
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}:{line_no}: JSON object required")
            yield value


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def integer(value: Any, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def check_finite(value: Any, path: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path}: non-finite number")
    if isinstance(value, dict):
        for key, child in value.items():
            check_finite(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_finite(child, f"{path}[{index}]")


def stats(values: Iterable[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "min": None, "median": None, "p95": None, "max": None}
    return {"n": len(ordered), "min": ordered[0], "median": statistics.median(ordered),
            "p95": ordered[max(0, math.ceil(0.95*len(ordered))-1)], "max": ordered[-1]}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence_path(run_dir: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("nonempty evidence path required")
    path = run_dir / relative
    if not path.resolve().is_relative_to(run_dir.resolve()):
        raise ValueError("evidence path escapes run directory")
    if not path.is_file():
        raise ValueError(f"missing evidence {relative}")
    return path


def compact_resources(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact actual /proc samples, without copying the sampled arrays."""
    if not samples:
        return {"samples": 0}
    def span(key: str) -> list[float] | None:
        values = [row.get(key) for row in samples]
        return [min(values), max(values)] if all(number(value) for value in values) else None
    first, last = samples[0], samples[-1]
    def difference(key: str) -> float | None:
        return last[key]-first[key] if number(first.get(key)) and number(last.get(key)) else None
    user, system = difference("cpu_user_s"), difference("cpu_system_s")
    wall = difference("t_wall_monotonic")
    cpu = user+system if number(user) and number(system) else None
    return {"samples": len(samples), "rss_kib_range": span("rss_kib"),
            "rss_first_kib": first.get("rss_kib"), "rss_last_kib": last.get("rss_kib"),
            "rss_delta_kib": difference("rss_kib"), "process_hwm_kib_range": span("peak_rss_kib"),
            "threads_range": span("threads"), "file_descriptors_range": span("file_descriptors"),
            "sample_span_wall_s": wall, "cpu_user_delta_s": user, "cpu_system_delta_s": system,
            "cpu_total_delta_s": cpu, "mean_busy_cpu_cores": cpu/wall if number(cpu) and number(wall) and wall > 0 else None,
            "forbidden_device_descriptors": sorted({value for row in samples for value in row.get("forbidden_device_descriptors", [])}),
            "display_socket_descriptors": sorted({value for row in samples for value in row.get("display_socket_descriptors", [])})}


def attach_runtime_metadata(report: dict[str, Any], suite: dict[str, Any]) -> dict[str, Any]:
    """Enrich an already validated compact report from its small suite metadata.

    This never revalidates excluded raw journals or changes readiness gates.
    It is also used by summarize when complete raw evidence is available.
    """
    by_name = {run["name"]: run for run in suite.get("runs", []) if isinstance(run, dict) and "name" in run}
    for item in report.get("runs", []):
        run = by_name.get(item["name"], {})
        final = run.get("final", {})
        elapsed, simulated = final.get("elapsed_wall_ms"), final.get("simulated_ms")
        item["runtime"] = {"elapsed_wall_ms_after_reset": elapsed, "simulated_ms": simulated,
                           "simulated_wall_ratio_after_reset": simulated/elapsed if number(simulated) and number(elapsed) and elapsed > 0 else None,
                           "cold_reset_wall_ms": final.get("reset_wall_ms"), "max_realtime_lag_ms": final.get("max_realtime_lag_ms"),
                           "resources": compact_resources(run.get("resources", []))}
        if isinstance(final.get("output_dir"), str):
            report.setdefault("provenance", {})["remote_run_dir"] = str(PurePosixPath(final["output_dir"]).parent)
    return report


def validate_stream(batches: list[dict[str, Any]], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate exported engine batches without trusting suite pass booleans.

    Sequence is global within one subscription generation, shared by periodic
    and event-only actor rows. Queue drops are explicitly counted by engine.
    """
    config = config or read_json(DEFAULT_CONFIG)
    budgets = config["budgets"]
    issues: list[str] = []
    intervals: list[float] = []
    event_delays: list[int] = []
    costs: list[float] = []
    rows: list[dict[str, Any]] = []
    previous_seq: dict[int, int] = {}
    previous_drop: dict[int, int] = {}
    previous_event_drop: dict[int, int] = {}
    previous_time: dict[tuple[int, int], int] = {}
    previous_periodic: dict[tuple[int, int], int] = {}
    actor_symbols: dict[tuple[int, int], int] = {}
    previous_position: dict[tuple[int, int], list[float]] = {}
    event_ids: set[tuple[int, int, int]] = set()
    sessions: set[int] = set()
    sequence_gaps = 0

    def require(condition: bool, text: str) -> bool:
        if not condition and len(issues) < 100:
            issues.append(text)
        return condition

    def vector(value: Any, label: str) -> bool:
        return require(isinstance(value, list) and len(value) == 3 and all(number(v) for v in value),
                       f"{label}: finite numeric vector[3] required")

    for batch_index, batch in enumerate(batches):
        prefix = f"batch {batch_index+1}"
        try:
            check_finite(batch)
        except ValueError as error:
            require(False, f"{prefix}: {error}")
        require(batch.get("kind") == "body_observations" and batch.get("schema_version") == 1,
                f"{prefix}: kind/schema mismatch")
        require(batch.get("phase") == "post_simulation_post_animation", f"{prefix}: wrong sampling phase")
        require(batch.get("sample_period_ms") == budgets["sample_period_game_ms"], f"{prefix}: sample period mismatch")
        generation = batch.get("generation")
        if not require(integer(generation, 1), f"{prefix}: positive generation required"):
            continue
        session = batch.get("session_epoch")
        require(integer(session, 1) and batch.get("world_epoch") == generation and
                batch.get("control_generation") == 0, f"{prefix}: passive session/world/control epochs invalid")
        if integer(session, 1):
            sessions.add(session)
        capacity = batch.get("queue_capacity")
        payload = batch.get("observations")
        if not require(integer(capacity, 1) and capacity <= 256 and isinstance(payload, list) and len(payload) <= capacity,
                       f"{prefix}: bounded observation queue required"):
            continue
        dropped, since = batch.get("dropped_total"), batch.get("dropped_since_drain")
        event_dropped = batch.get("event_dropped_total")
        if require(integer(event_dropped) and event_dropped >= previous_event_drop.get(generation, 0),
                   f"{prefix}: explicit monotonic bounded event loss counter required"):
            previous_event_drop[generation] = event_dropped
        if require(integer(dropped) and integer(since) and dropped-previous_drop.get(generation, 0) == since,
                   f"{prefix}: dropped counters missing or inconsistent"):
            previous_drop[generation] = dropped
        for row_index, row in enumerate(payload):
            label = f"{prefix} row {row_index+1}"
            if not require(isinstance(row, dict), f"{label}: object required"):
                continue
            rows.append(row)
            require(row.get("kind") == "body_observation" and row.get("schema_version") == 1 and
                    row.get("phase") == "post_simulation_post_animation" and row.get("session_epoch") == session and
                    row.get("world_epoch") == generation and row.get("control_generation") == 0,
                    f"{label}: row schema/phase/epochs invalid")
            require(row.get("generation") == generation, f"{label}: generation mismatch")
            seq = row.get("observation_seq")
            actor = row.get("entity_id")
            time = row.get("t_game_ms")
            step = row.get("sim_step")
            if not require(integer(seq, 1) and integer(actor, 1) and integer(time) and integer(step),
                           f"{label}: invalid runtime identity/time/sequence"):
                continue
            prev = previous_seq.get(generation, 0)
            require(seq > prev, f"{label}: duplicate/out-of-order observation sequence")
            if seq > prev:
                sequence_gaps += seq-prev-1
            previous_seq[generation] = seq
            key = (generation, actor)
            require(key not in previous_time or time >= previous_time[key], f"{label}: game clock moved backwards")
            expected_dt = time-previous_time[key] if key in previous_time else 0
            previous_time[key] = time
            symbol = row.get("script_symbol")
            require(integer(symbol), f"{label}: numeric script symbol required separately from runtime ID")
            require(key not in actor_symbols or symbol == actor_symbols[key], f"{label}: runtime ID changed script symbol")
            actor_symbols[key] = symbol
            require(integer(row.get("dt_game_ms")), f"{label}: invalid dt_game_ms")
            # A bounded queue may omit the previous actor row. Exact derivative
            # checks apply only when the stream has remained lossless.
            if dropped == 0:
                require(row.get("dt_game_ms") == expected_dt, f"{label}: dt does not match previous same-phase actor sample")
            require(type(row.get("periodic")) is bool and type(row.get("event_only")) is bool and
                    row.get("periodic") != row.get("event_only"), f"{label}: periodic/event-only flags inconsistent")
            require(type(row.get("state_changed")) is bool and type(row.get("motion_discontinuity")) is bool,
                    f"{label}: native state-edge/discontinuity flags required")
            require(not row.get("event_only") or row.get("state_changed") is True or bool(row.get("events")),
                    f"{label}: event-only row lacks a native event or state transition")
            if row.get("periodic") is True:
                if key in previous_periodic:
                    intervals.append(time-previous_periodic[key])
                previous_periodic[key] = time
            require(row.get("can_decide") is False and type(row.get("native_decision_eligible")) is bool and row.get("control_allowed") is False and
                    row.get("action_mask_valid") is False and row.get("mask_kind") == "conservative_native_availability",
                    f"{label}: passive/advisory mask semantics violated")
            mask = row.get("availability_mask")
            require(isinstance(mask, list) and len(mask) == 12 and all(type(v) is bool for v in mask),
                    f"{label}: 12 conservative availability booleans required")
            if isinstance(mask, list) and len(mask) == 12:
                require(mask[0] is True and (row.get("native_decision_eligible") or not any(mask[1:])),
                        f"{label}: availability contradicts native eligibility")
            own = row.get("self")
            target = row.get("target")
            if require(isinstance(own, dict) and isinstance(target, dict), f"{label}: self/target objects required"):
                pos_valid = vector(own.get("position_world_cm"), f"{label}.self.position_world_cm")
                hp, hp_max = own.get("hp"), own.get("hp_max")
                require(integer(hp) and integer(hp_max, 1) and hp <= hp_max, f"{label}: self HP invalid")
                require(number(own.get("yaw_rad")), f"{label}: finite own yaw required")
                require(number(own.get("hp_fraction")) and number(hp) and number(hp_max) and hp_max > 0 and
                        abs(own["hp_fraction"]-hp/hp_max) <= 1e-9, f"{label}: self HP fraction is inconsistent")
                velocity = own.get("linear_velocity_cm_s")
                velocity_valid = own.get("velocity_valid")
                require(type(velocity_valid) is bool and ((velocity_valid and vector(velocity, f"{label}.self.linear_velocity_cm_s")) or
                                                        (velocity_valid is False and velocity is None)),
                        f"{label}: own velocity availability invalid")
                require(row.get("motion_discontinuity") is not True or velocity_valid is False,
                        f"{label}: fixture teleport must invalidate own velocity")
                require(type(own.get("skill_valid")) is bool and integer(own.get("skill_talent")) and own["skill_talent"] <= 4 and
                        integer(own.get("skill")) and (own["skill_valid"] or own["skill"] == 0), f"{label}: skill value/validity/talent invalid")
                if pos_valid and velocity_valid is True and key in previous_position and expected_dt > 0 and dropped == 0 and isinstance(velocity, list):
                    expected = [(value-old)*1000/expected_dt for value, old in zip(own["position_world_cm"], previous_position[key])]
                    require(all(abs(value-want) <= max(0.005, abs(want)*2e-6) for value, want in zip(velocity, expected)),
                            f"{label}: own velocity does not derive from same-phase position/dt")
                if pos_valid:
                    previous_position[key] = own["position_world_cm"]
                for field in ("weapon_state", "weapon_symbol", "body_state_raw", "body_state_base", "body_flags", "choice_id"):
                    require(integer(own.get(field)), f"{label}: own enum/ID {field} invalid")
                if all(integer(own.get(field)) for field in ("body_state_raw", "body_state_base", "body_flags")):
                    require(own["body_state_base"] == own["body_state_raw"] & 31 and own["body_flags"] == own["body_state_raw"] & ~31,
                            f"{label}: body base/flags do not match native BS_MAX=31")
                for field in ("dead", "unconscious", "down", "in_air", "swim", "dive", "attack_anim", "prehit", "defence_active",
                              "jumpback_active", "def_window", "par_window", "interruptible"):
                    require(type(own.get(field)) is bool, f"{label}: own state flag {field} invalid")
                visible = target.get("visible")
                require(type(visible) is bool and target.get("current_valid") == visible and type(target.get("present")) is bool and
                        integer(target.get("entity_id")) and (not visible or target.get("present") is True),
                        f"{label}: target presence/visibility/validity invalid")
                require((target.get("present") is True and integer(target.get("entity_id"), 1)) or
                        (target.get("present") is False and target.get("entity_id") == 0), f"{label}: target runtime ID contradicts presence")
                if visible is False:
                    public_history = {"entity_id", "present", "visible", "current_valid", "last_known_position_world_cm",
                                      "last_known_velocity_cm_s", "last_seen_game_ms", "last_known_age_ms"}
                    for field, value in target.items():
                        if field not in public_history:
                            require(value is None, f"{label}: hidden target leaks current {field}")
                elif visible is True:
                    target_pos_valid = vector(target.get("position_world_cm"), f"{label}.target.position_world_cm")
                    relative_valid = vector(target.get("relative_position_cm"), f"{label}.target.relative_position_cm")
                    if pos_valid and target_pos_valid and relative_valid:
                        require(all(abs(delta-(opponent-self_value)) <= max(0.005, abs(delta)*2e-6)
                                    for delta, opponent, self_value in zip(target["relative_position_cm"], target["position_world_cm"], own["position_world_cm"])),
                                f"{label}: relative position does not equal target minus self in cm")
                    require(number(target.get("yaw_error_rad")) and abs(target["yaw_error_rad"]) <= math.pi+1e-9,
                            f"{label}: wrapped target yaw error invalid")
                    for field in ("fight_distance_cm", "attack_range_cm", "g_range_cm", "closeup_range_cm"):
                        require(number(target.get(field)) and target[field] >= 0, f"{label}: native cm distance/range {field} invalid")
                    target_hp, target_max = target.get("hp"), target.get("hp_max")
                    require(integer(target_hp) and integer(target_max, 1) and target_hp <= target_max and number(target.get("hp_fraction")) and
                            abs(target["hp_fraction"]-target_hp/target_max) <= 1e-9, f"{label}: visible target HP fraction invalid")
                last_known = target.get("last_known_position_world_cm")
                if last_known is not None:
                    vector(last_known, f"{label}.target.last_known_position_world_cm")
                    require(integer(target.get("last_known_age_ms")) and integer(target.get("last_seen_game_ms")) and
                            target["last_known_age_ms"] == time-target["last_seen_game_ms"], f"{label}: last-known target age inconsistent")
                else:
                    require(target.get("last_known_age_ms") is None and target.get("last_seen_game_ms") is None,
                            f"{label}: absent last-known position has populated history time")
                for field in ("linear_velocity_cm_s", "relative_velocity_cm_s", "last_known_velocity_cm_s"):
                    if target.get(field) is not None:
                        vector(target[field], f"{label}.target.{field}")
            obstacles = row.get("local_obstacles")
            if require(isinstance(obstacles, dict), f"{label}: local obstacle features required"):
                ray_range = obstacles.get("ray_range_cm")
                rays = obstacles.get("rays")
                if require(number(ray_range) and ray_range > 0 and isinstance(rays, list) and
                           len(rays) == budgets["local_ray_count"], f"{label}: 16 rays and positive cm range required"):
                    for ray_index, ray in enumerate(rays):
                        require(isinstance(ray, dict) and number(ray.get("angle_rad")) and number(ray.get("distance_cm")) and
                                0 <= ray["distance_cm"] <= ray_range and type(ray.get("hit")) is bool,
                                f"{label}: malformed/beyond-range local ray {ray_index}")
            cost = row.get("builder_wall_us")
            if require(number(cost) and cost >= 0, f"{label}: wall builder cost required"):
                costs.append(cost)
            events = row.get("events")
            if not require(isinstance(events, list), f"{label}: event list required"):
                continue
            require(len(events) <= 32, f"{label}: event queue exceeds native bound")
            for event_index, event in enumerate(events):
                event_label = f"{label} event {event_index+1}"
                if not require(isinstance(event, dict) and integer(event.get("event_id"), 1) and
                               integer(event.get("source_game_ms")) and integer(event.get("source_sim_step")),
                               f"{event_label}: real source event ID/time required"):
                    continue
                event_key = (generation, actor, event["event_id"])
                require(event_key not in event_ids, f"{event_label}: duplicate delivered event")
                event_ids.add(event_key)
                require(integer(event.get("code"), 1) and event["code"] <= 12 and
                        event.get("perspective") in ("self", "visible_target") and integer(event.get("source_entity_id"), 1),
                        f"{event_label}: event vocabulary/source invalid")
                delay = step-event["source_sim_step"]
                require(0 <= delay <= budgets["event_delivery_max_sim_steps"] and event["source_game_ms"] <= time,
                        f"{event_label}: event delivered after next sample tick or from future")
                event_delays.append(delay)
    require(bool(rows), "empty observation stream")
    require(len(sessions) == 1, "one stream cannot mix process session epochs")
    dropped_count = sum(previous_drop.values())
    require(sequence_gaps == dropped_count, "observation sequence gaps do not match declared dropped rows")
    interval_stats, cost_stats = stats(intervals), stats(costs)
    require(interval_stats["n"] > 0, "no periodic cadence evidence")
    times = [row["t_game_ms"] for row in rows if integer(row.get("t_game_ms"))]
    def target_of(row: dict[str, Any]) -> dict[str, Any]:
        value = row.get("target")
        return value if isinstance(value, dict) else {}
    return {"status": "complete" if not issues else "incomplete", "issues": issues,
            "batches": len(batches), "samples": len(rows), "actor_instances": len(actor_symbols),
            "actor_ids_by_generation": {str(g): sorted(actor for generation, actor in actor_symbols if generation == g)
                                         for g in sorted({generation for generation, _ in actor_symbols})},
            "session_epochs": sorted(sessions), "generations": sorted(previous_seq), "periodic_samples": sum(row.get("periodic") is True for row in rows),
            "event_only_samples": sum(row.get("event_only") is True for row in rows),
            "game_time_span_ms": max(times)-min(times) if times else None,
            "periodic_interval_game_ms": interval_stats, "builder_cost_wall_us": cost_stats,
            "events": len(event_ids), "event_delivery_sim_steps": stats(event_delays),
            "sequence_gaps": sequence_gaps, "dropped_total": dropped_count,
            "event_dropped_total": sum(previous_event_drop.values()),
            "visible_target_samples": sum(target_of(row).get("visible") is True for row in rows),
            "hidden_target_samples": sum(target_of(row).get("visible") is False for row in rows),
            "hidden_last_known_samples": sum(target_of(row).get("visible") is False and
                                               target_of(row).get("last_known_position_world_cm") is not None and
                                               integer(target_of(row).get("last_known_age_ms"), 1) for row in rows),
            "native_decision_eligible_true": sum(row.get("native_decision_eligible") is True for row in rows),
            "native_decision_eligible_false": sum(row.get("native_decision_eligible") is False for row in rows),
            "max_batch_rows": max((len(batch.get("observations", [])) for batch in batches), default=0)}


def summarize(suite: dict[str, Any], manifest: dict[str, Any], run_dir: Path,
              config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or read_json(DEFAULT_CONFIG)
    budgets = config["budgets"]
    issues: list[str] = []
    gates: list[dict[str, Any]] = []

    def gate(key: str, passed: bool, detail: str) -> None:
        gates.append({"id": key, "status": "passed" if passed else "failed", "detail": detail})
        if not passed:
            issues.append(f"{key}: {detail}")

    gate("suite", suite.get("status") == "passed" and suite.get("full_stage3_evidence") is True and
         suite.get("required_gate_failures") == [] and manifest.get("suite_status") == "passed" and
         manifest.get("suite_scope") == "full", "Completed full server suite; smoke/partial cannot confirm stage 3")
    gate("headless", manifest.get("headless") is True and manifest.get("renderer") == "none" and
         manifest.get("display") is None and all(manifest.get(key) is False for key in
                                                ("graphics_device_created", "window_created", "audio_device_created")),
         "Explicit device/window/audio attestation")
    raw_runs = suite.get("runs", [])
    if not isinstance(raw_runs, list):
        raw_runs = []
    compact: list[dict[str, Any]] = []
    timing_costs: list[float] = []
    for run in raw_runs:
        if not isinstance(run, dict):
            gate("run_schema", False, "run object required")
            continue
        name, role = run.get("name"), run.get("role")
        final = run.get("final", {})
        resources = run.get("resources", [])
        flags = all(final.get(key) is False for key in ("graphics_device_created", "window_created", "audio_device_created"))
        clean_proc = isinstance(resources, list) and bool(resources) and all(
            row.get("forbidden_device_descriptors") == [] and row.get("display_socket_descriptors") == [] for row in resources)
        gate(f"runtime:{name}", run.get("status") == "completed" and final.get("done") is True and flags and clean_proc and
             integer(run.get("sim_step"), 1) and number(run.get("simulated_ms")) and run["simulated_ms"] > 0,
             "Completed native simulation with actual /proc device checks")
        item = {key: run.get(key) for key in ("name", "role", "pair_id", "mode", "simulated_ms", "sim_step", "processing_wall_s",
                                             "scenario_sha256", "watch_enabled", "observations_file", "timings_file", "events_file")}
        if run.get("watch_enabled") is True:
            batches: list[dict[str, Any]] = []
            try:
                path = evidence_path(run_dir, run.get("observations_file"))
                batches = read_jsonl(path)
                stream = validate_stream(batches, config)
                item["observations_sha256"] = sha256(path)
            except (OSError, ValueError, KeyError, TypeError) as error:
                stream = {"status": "incomplete", "issues": [str(error)]}
            item["stream"] = stream
            observation_status = final.get("body_observation", {})
            item["observation_status"] = observation_status
            gate(f"bounded_state:{name}", observation_status.get("invalid_numeric_rows") == 0 and
                 integer(observation_status.get("queue_capacity"), 1) and observation_status["queue_capacity"] <= 256 and
                 integer(observation_status.get("max_queue_size")) and observation_status["max_queue_size"] <= observation_status["queue_capacity"] and
                 integer(observation_status.get("entity_registry_capacity"), 1) and integer(observation_status.get("entity_registry_size")) and
                 observation_status["entity_registry_size"] <= observation_status["entity_registry_capacity"],
                 "Actual native statistics: no invalid rows, bounded queue and runtime identity registry")
            gate(f"numeric:{name}", stream.get("status") == "complete", "; ".join(stream.get("issues", [])) or "Raw schema, identity, units/privacy, availability and bounded delivery")
            if role != "bounded_queue":
                gate(f"lossless:{name}", stream.get("dropped_total") == 0 and stream.get("event_dropped_total") == 0,
                     "Normal polling has no silent or declared row/event loss")
            if role == "privacy_edges":
                try:
                    privacy_evidence = validate_privacy(batches, read_jsonl(evidence_path(run_dir, run.get("events_file"))))
                except (OSError, ValueError, KeyError, TypeError) as error:
                    privacy_evidence = {"status": "incomplete", "issues": [str(error)]}
                item["privacy"] = privacy_evidence
                gate(f"privacy_oracle:{name}", privacy_evidence.get("status") == "complete",
                     "; ".join(privacy_evidence.get("issues", [])) or "Visible/occluded/hidden movement/reappearance compared with separate native truth")
            if role == "passive_realtime":
                try:
                    event_path = evidence_path(run_dir, run.get("events_file"))
                    ids = next(iter(stream.get("actor_ids_by_generation", {}).values()), [])
                    activity = validate_activity(event_path, budgets["passive_duration_game_ms"], ids)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    activity = {"status": "incomplete", "issues": [str(error)]}
                item["native_activity"] = activity
                gate(f"native_activity:{name}", activity.get("status") == "complete",
                     "; ".join(activity.get("issues", [])) or "Every actor has native choices, accepted attempts and hit attempts in each of ten 60 s buckets, plus actual HP loss")
            if role in ("paired_on", "passive_realtime"):
                by_gen = stream.get("actor_ids_by_generation", {})
                gate(f"actors:{name}", bool(by_gen) and all(len(ids) == budgets["observed_actor_count"] for ids in by_gen.values()),
                     "Four distinct runtime actors")
            try:
                timing_path = evidence_path(run_dir, run.get("timings_file"))
                timings = read_jsonl(timing_path)
                costs = [row.get("total_observer_wall_us") for row in timings]
                timed = (all(number(cost) and cost >= 0 for cost in costs) and
                         all(integer(row.get("actors")) and row["actors"] <= 4 and integer(row.get("sim_step")) and
                             row.get("kind") in ("sample_step", "packet_transport") and number(row.get("builder_wall_us")) and
                             0 <= row["builder_wall_us"] <= row["total_observer_wall_us"] for row in timings))
                gate(f"timing:{name}", bool(costs) and timed, "Measured total observer simulation-thread work per step")
                grouped: dict[int, dict[str, Any]] = {}
                wall_by_step: dict[int, int] = {}
                duplicate_sample_steps = False
                if timed:
                    for row in timings:
                        group = grouped.setdefault(row["sim_step"], {"actors": 0, "wall_us": 0})
                        group["actors"] = max(group["actors"], row["actors"])
                        group["wall_us"] += row["total_observer_wall_us"]
                        if row["kind"] == "sample_step":
                            if row["sim_step"] in wall_by_step:
                                duplicate_sample_steps = True
                            if integer(row.get("wall_sample_us")):
                                wall_by_step[row["sim_step"]] = row["wall_sample_us"]
                    four_actor_costs = [group["wall_us"] for group in grouped.values() if group["actors"] == budgets["observed_actor_count"]]
                    if role in ("paired_on", "passive_realtime"):
                        timing_costs.extend(four_actor_costs)
                    item["observer_cost_wall_us"] = stats(four_actor_costs)
                    item["packet_transport_wall_us"] = stats(row["total_observer_wall_us"] for row in timings if row["kind"] == "packet_transport")
                    if role == "passive_realtime":
                        wall_intervals = []
                        previous_wall: dict[int, int] = {}
                        missing_wall = 0
                        for batch in batches:
                            for row in batch.get("observations", []):
                                if row.get("periodic") is not True:
                                    continue
                                wall = wall_by_step.get(row.get("sim_step"))
                                if wall is None:
                                    missing_wall += 1
                                    continue
                                actor = row.get("entity_id")
                                if actor in previous_wall:
                                    wall_intervals.append((wall-previous_wall[actor])/1000)
                                previous_wall[actor] = wall
                        item["periodic_interval_wall_ms"] = stats(wall_intervals)
                        gate(f"wall_clock:{name}", missing_wall == 0 and not duplicate_sample_steps and bool(wall_intervals) and min(wall_intervals) > 0,
                             "Same-process steady_clock joins every periodic actor row to its actual emission step")
                item["timings_sha256"] = sha256(timing_path)
            except (OSError, ValueError, KeyError, TypeError) as error:
                gate(f"timing:{name}", False, str(error))
        compact.append(item)
    passive = [run for run in compact if run.get("role") == "passive_realtime"]
    gate("passive_10min", len(passive) == 1 and passive[0].get("mode") == "realtime" and
         number(passive[0].get("simulated_ms")) and passive[0]["simulated_ms"] >= budgets["passive_duration_game_ms"] and
         number(passive[0].get("stream", {}).get("game_time_span_ms")) and
         passive[0]["stream"]["game_time_span_ms"] >= budgets["passive_duration_game_ms"]-budgets["normal_realtime_interval_p95_game_ms"],
         "At least 600000 game ms in realtime with raw endpoint evidence")
    cadence = passive[0].get("stream", {}).get("periodic_interval_game_ms", {}) if len(passive) == 1 else {}
    gate("realtime_cadence", cadence.get("n", 0) > 0 and number(cadence.get("p95")) and
         cadence["p95"] <= budgets["normal_realtime_interval_p95_game_ms"], "Periodic interval p95 <= 100 game ms at configured 20 Hz")
    wall_cadence = passive[0].get("periodic_interval_wall_ms", {}) if len(passive) == 1 else {}
    gate("realtime_wall_cadence", wall_cadence.get("n", 0) > 0 and number(wall_cadence.get("p95")) and
         wall_cadence["p95"] <= budgets["normal_realtime_interval_p95_wall_ms"], "Actual emission periodic interval p95 <= 100 wall ms in realtime")
    event_streams = [run.get("stream", {}) for run in compact if run.get("stream")]
    event_count = sum(stream.get("events", 0) for stream in event_streams)
    event_max = max((stream.get("event_delivery_sim_steps", {}).get("max", 0) or 0 for stream in event_streams), default=0)
    gate("native_events", event_count > 0 and event_max <= budgets["event_delivery_max_sim_steps"],
         "Actual source event IDs reach samples by the next simulation step")
    privacy = [run.get("stream", {}) for run in compact if run.get("role") == "privacy_edges"]
    gate("privacy_coverage", bool(privacy) and sum(stream.get("visible_target_samples", 0) for stream in privacy) > 0 and
         sum(stream.get("hidden_last_known_samples", 0) for stream in privacy) > 0 and all(stream.get("status") == "complete" for stream in privacy),
         "Actual visible-to-hidden target with aged last-known position and no current privileged state")
    bounded = [run.get("stream", {}) for run in compact if run.get("role") == "bounded_queue"]
    gate("bounded_queue", bool(bounded) and all(stream.get("status") == "complete" and stream.get("dropped_total", 0) > 0 and
                                                stream.get("sequence_gaps") == stream.get("dropped_total") for stream in bounded),
         "Withheld drain provokes real bounded overflow with exact drop/sequence accounting")
    cost_summary = stats(timing_costs)
    gate("observer_cost", cost_summary["n"] > 0 and cost_summary["p95"] <= budgets["observer_cost_p95_wall_us"],
         "Total observer simulation-thread p95 <= 2000 wall us for four actors")
    on = {run.get("pair_id"): run for run in compact if run.get("role") == "paired_on"}
    off = {run.get("pair_id"): run for run in compact if run.get("role") == "paired_off"}
    paired_metrics: list[dict[str, Any]] = []
    rates: dict[str, list[float]] = {"on": [], "off": []}
    pairs_valid = (set(on) == set(off) and len(on) >= budgets["paired_repeats_min"] and all(integer(pair) for pair in on) and
                   len(on) == len([run for run in compact if run.get("role") == "paired_on"]) and
                   len(off) == len([run for run in compact if run.get("role") == "paired_off"]))
    for pair in sorted(on.keys() & off.keys(), key=str):
        left, right = off[pair], on[pair]
        valid = left.get("sim_step") == right.get("sim_step") and left.get("scenario_sha256") == right.get("scenario_sha256") and all(
            number(run.get("processing_wall_s")) and run["processing_wall_s"] > 0 and integer(run.get("sim_step"), 1) for run in (left, right))
        pairs_valid = pairs_valid and valid
        if valid:
            off_rate, on_rate = left["sim_step"]/left["processing_wall_s"], right["sim_step"]/right["processing_wall_s"]
            rates["off"].append(off_rate)
            rates["on"].append(on_rate)
            paired_metrics.append({"pair_id": pair, "off_substeps_per_s": off_rate, "on_substeps_per_s": on_rate,
                                   "drop_fraction": 1-on_rate/off_rate})
    median_off = statistics.median(rates["off"]) if rates["off"] else None
    median_on = statistics.median(rates["on"]) if rates["on"] else None
    drop = 1-median_on/median_off if median_off and median_on else None
    gate("throughput", pairs_valid and number(drop) and drop <= budgets["headless_throughput_drop_fraction"],
         "Same-scenario >=3 paired repeats: median processing throughput drop <= 5%")
    comparison = suite.get("comparisons", {}).get("native_observer_on_off", {})
    native = comparison.get("report", {})
    episodes = native.get("episodes", [])
    gate("native_parity", comparison.get("exit_code") == 0 and native.get("status") == "matched" and native.get("profile") == "full" and
         len(episodes) == 3 and [row.get("seed") for row in episodes] == [17, 29, 43] and
         all(row.get("status") == "matched" and row.get("difference_count") == 0 for row in episodes),
         "Ordered native choice/consume/animation/physics/hit/damage parity with watch disabled/enabled")
    keys = ("source_sha256", "binary_sha256", "engine_library_sha256", "save_sha256", "scenario_sha256", "config_sha256", "settings_sha256")
    gate("provenance", all(isinstance(manifest.get(key), str) and len(manifest[key]) == 64 for key in keys) and
         isinstance(manifest.get("assets"), list) and bool(manifest["assets"]) and manifest.get("model") is None and
         manifest.get("observer_ownership") == "passive", "Source/build/config/save/assets hashes; no model and passive ownership")
    report = {"schema_version": 1, "status": "complete" if not issues else "incomplete", "issues": issues, "gates": gates,
            "budgets": budgets, "runs": compact, "observer_cost_wall_us": cost_summary,
            "throughput": {"pairs": paired_metrics, "median_off_substeps_per_s": median_off, "median_on_substeps_per_s": median_on,
                           "drop_fraction": drop}, "events": {"delivered": event_count, "max_delivery_sim_steps": event_max},
            "native_parity": {key: native.get(key) for key in ("status", "profile", "comparison_kind", "episodes")},
            "provenance": {key: manifest.get(key) for key in (*keys, "host", "sim_fps", "time_multiplier", "recorded_at_utc", "completed_at_utc")},
            "services_before": manifest.get("services_before"), "services_after": suite.get("services_after"),
            "limits": config.get("limitations", [])}
    return attach_runtime_metadata(report, suite)


def validate_activity(events_path: Path, duration_ms: int = 600000,
                      observation_ids: list[int] | None = None) -> dict[str, Any]:
    """Prove four NPCs continue native combat throughout the passive interval."""
    issues: list[str] = []
    mapping: dict[int, int] = {}
    setup: dict[str, Any] | None = None
    ends: list[dict[str, Any]] = []
    buckets = math.ceil(duration_ms/60000)
    counts: dict[int, list[dict[str, int]]] = {}
    losses: dict[int, int] = {}
    previous_seq = 0
    trace_rows = 0
    for row in iter_jsonl(events_path):
        if row.get("kind") == "body_observe_error":
            issues.append(f"native runner error: {row.get('reason')}")
        if row.get("kind") == "body_observe_setup":
            if setup is not None:
                issues.append("multiple native observer setups in passive run")
            setup = row
            if not (integer(row.get("t_game_ms")) and row.get("duration_ms") == duration_ms and row.get("ownership") == "native"):
                issues.append("native setup duration/ownership/time mismatch")
        if row.get("kind") == "body_observe_end":
            ends.append(row)
        if row.get("kind") != "body_combat":
            continue
        trace_rows += 1
        seq = row.get("seq")
        if not integer(seq, 1) or seq <= previous_seq:
            issues.append("native body trace sequence duplicate/out of order")
        if integer(seq):
            previous_seq = seq
        actor = row.get("actor_id")
        if row.get("phase") == "observation_actor_mapping":
            entity = row.get("observation_entity_id")
            if not integer(actor, 1) or not integer(entity, 1) or actor in mapping or entity in mapping.values():
                issues.append("native actor/runtime observation mapping invalid or duplicated")
            else:
                mapping[actor] = entity
                counts[entity] = [{"native_choice": 0, "accepted_native_attempt": 0, "hit_attempt": 0} for _ in range(buckets)]
                losses[entity] = 0
            continue
        if actor not in mapping or setup is None or not integer(setup.get("t_game_ms")) or not integer(row.get("t_game")):
            continue
        elapsed = row["t_game"]-setup["t_game_ms"]
        if not 0 <= elapsed < duration_ms:
            continue
        entity = mapping[actor]
        bucket = elapsed//60000
        phase = row.get("phase")
        if phase == "native_choice":
            counts[entity][bucket]["native_choice"] += 1
        elif phase == "native_attempt" and row.get("accepted") is True:
            counts[entity][bucket]["accepted_native_attempt"] += 1
        elif phase == "hit_attempt":
            counts[entity][bucket]["hit_attempt"] += 1
        elif phase == "damage" and integer(row.get("hp_before")) and integer(row.get("hp_after")):
            losses[entity] += max(0, row["hp_before"]-row["hp_after"])
    if setup is None:
        issues.append("missing native observer setup")
    else:
        actors = setup.get("actors", [])
        if not (isinstance(actors, list) and len(actors) == 4 and all(isinstance(actor, dict) and actor.get("immortal") is False for actor in actors)):
            issues.append("four mortal native setup actors required")
    if len(mapping) != 4 or (observation_ids is not None and set(mapping.values()) != set(observation_ids)):
        issues.append("exact mapping of four observed runtime actors required")
    if len(ends) != 1 or not number(ends[0].get("elapsed_ms")) or ends[0]["elapsed_ms"] < duration_ms or ends[0].get("actors") != 4:
        issues.append("completed native observer duration footer required")
    for actor, actor_buckets in counts.items():
        for bucket, values in enumerate(actor_buckets):
            for key, value in values.items():
                if value < 1:
                    issues.append(f"actor {actor} bucket {bucket}: no {key}")
        if losses[actor] <= 0:
            issues.append(f"actor {actor}: no actual HP loss in passive episode")
    return {"status": "complete" if not issues else "incomplete", "issues": issues[:100],
            "duration_game_ms": duration_ms, "bucket_game_ms": 60000, "bucket_count": buckets,
            "trace_rows": trace_rows, "source_sha256": sha256(events_path),
            "actors": [{"entity_id": actor, "trace_actor_id": next(key for key, value in mapping.items() if value == actor),
                        "buckets": values, "actual_hp_loss": losses[actor]}
                       for actor, values in sorted(counts.items())]}


def validate_privacy(batches: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    """Join actual probe mutations to later observations; truth stays out of input."""
    issues: list[str] = []
    probes = [event for event in events if event.get("kind") == "body_observe_probe"]
    truth_samples = [event for event in events if event.get("kind") == "body_observe_probe_sample"]
    expected = ["visible", "occluded", "hidden_move", "reappear"]
    if [probe.get("case") for probe in probes] != expected:
        return {"status": "incomplete", "issues": ["four ordered native privacy probes required"]}
    rows = [row for batch in batches for row in batch.get("observations", []) if isinstance(row, dict)]
    selected: dict[str, dict[str, Any]] = {}
    for index, probe in enumerate(probes):
        case = probe["case"]
        time = probe.get("t_game_ms")
        upper = probes[index+1].get("t_game_ms") if index+1 < len(probes) else math.inf
        if not integer(time) or not number(upper) and upper != math.inf:
            issues.append(f"{case}: invalid native mutation timestamp")
            continue
        candidates = [row for row in rows if row.get("entity_id") == probe.get("actor_id") and isinstance(row.get("target"), dict) and
                      row["target"].get("entity_id") == probe.get("target_id") and
                      integer(row.get("t_game_ms")) and time < row["t_game_ms"] <= upper]
        if not candidates:
            issues.append(f"{case}: no following sample for runtime actor/target")
            continue
        row = min(candidates, key=lambda value: value["t_game_ms"])
        selected[case] = row
        exact_truth = [event for event in truth_samples if event.get("case") == case and
                       event.get("t_game_ms") == row["t_game_ms"] and event.get("sim_step") == row.get("sim_step") and
                       event.get("actor_id") == row.get("entity_id") and
                       event.get("target_id") == row["target"].get("entity_id")]
        if len(exact_truth) != 1:
            issues.append(f"{case}: one matching post-simulation native truth sample required")
            continue
        truth = exact_truth[0]
        target = row["target"]
        own = row.get("self", {})
        own_position, own_truth = own.get("position_world_cm"), truth.get("truth_self_position_cm")
        if not (isinstance(own_position, list) and isinstance(own_truth, list) and len(own_position) == len(own_truth) == 3 and
                all(number(a) and number(b) and abs(a-b) <= 0.01 for a, b in zip(own_position, own_truth))):
            issues.append(f"{case}: own position disagrees with native truth")
        if own.get("hp") != truth.get("truth_self_hp"):
            issues.append(f"{case}: own HP disagrees with native truth")
        yaw, truth_yaw = own.get("yaw_rad"), truth.get("truth_self_yaw_rad")
        if not (number(yaw) and number(truth_yaw) and abs(math.remainder(yaw-truth_yaw, 2*math.pi)) <= 1e-5):
            issues.append(f"{case}: own yaw disagrees with native truth")
        visible = case in ("visible", "reappear")
        if probe.get("visible") is not visible or truth.get("visible") is not visible or target.get("visible") is not visible or target.get("current_valid") is not visible:
            issues.append(f"{case}: native visibility and observed availability disagree")
        if not visible and not (truth.get("center_ray_hit") is True and truth.get("head_ray_hit") is True):
            issues.append(f"{case}: hidden fixture lacks real occlusion rays")
        if visible:
            actual = target.get("position_world_cm")
            target_truth = truth.get("truth_target_position_cm")
            if not (isinstance(actual, list) and isinstance(target_truth, list) and len(actual) == len(target_truth) == 3 and
                    all(number(a) and number(b) and abs(a-b) <= 0.01 for a, b in zip(actual, target_truth))):
                issues.append(f"{case}: visible position disagrees with native truth")
            if target.get("hp") != truth.get("truth_target_hp"):
                issues.append(f"{case}: visible HP disagrees with native truth")
    if "occluded" in selected and "hidden_move" in selected:
        before, after = (selected[case]["target"] for case in ("occluded", "hidden_move"))
        if not before.get("last_known_position_world_cm") or before.get("last_known_position_world_cm") != after.get("last_known_position_world_cm"):
            issues.append("hidden_move: last-known position followed unseen native movement")
        if before.get("last_known_velocity_cm_s") != after.get("last_known_velocity_cm_s") or before.get("last_seen_game_ms") != after.get("last_seen_game_ms"):
            issues.append("hidden_move: last-known velocity/time changed without visibility")
        if not (integer(before.get("last_known_age_ms")) and integer(after.get("last_known_age_ms")) and
                after["last_known_age_ms"] > before["last_known_age_ms"]):
            issues.append("hidden_move: last-known age failed to advance")
        occluded, moved = probes[1], probes[2]
        if moved.get("truth_target_position_cm") == occluded.get("truth_target_position_cm") or moved.get("truth_target_hp") == occluded.get("truth_target_hp"):
            issues.append("hidden_move: native probe did not actually change position and HP")
    return {"status": "complete" if not issues else "incomplete", "issues": issues,
            "cases": [{"case": case, "sample_t_game_ms": selected[case]["t_game_ms"],
                       "current_valid": selected[case]["target"].get("current_valid"),
                       "last_known_age_ms": selected[case]["target"].get("last_known_age_ms")}
                      for case in expected if case in selected]}


def fmt(value: Any, precision: int = 2) -> str:
    return f"{value:.{precision}f}" if number(value) else "нет данных"


def markdown(report: dict[str, Any], run_dir: Path, out: Path) -> str:
    link = lambda path: Path(os.path.relpath(path, out)).as_posix()
    passive = next((run for run in report.get("runs", []) if run.get("role") == "passive_realtime"), {})
    stream = passive.get("stream", {})
    wall_cadence = passive.get("periodic_interval_wall_ms", {})
    realtime_cost = passive.get("observer_cost_wall_us", {})
    transport = passive.get("packet_transport_wall_us", {})
    cost_runs = [run for run in report.get("runs", []) if number((run.get("observer_cost_wall_us") or {}).get("max"))]
    max_cost_run = max(cost_runs, key=lambda run: run["observer_cost_wall_us"]["max"]).get("name") if cost_runs else "нет данных"
    lines = ["# Этап 3: частые числовые наблюдения NPC", "",
             f"Статус: **{'подтверждён' if report['status'] == 'complete' else 'не подтверждён'}**.", "",
             f"[Схема/runtime]({link(ROOT / 'docs/npc-body-observations.md')}), [план]({link(ROOT / 'docs/npc-body-control-plan.md')}), "
             f"[конфигурация]({link(DEFAULT_CONFIG)}), [локальные metadata, ignored]({link(run_dir)}). "
             f"Полный raw evidence хранится на сервере: `{report.get('provenance', {}).get('remote_run_dir', 'нет данных')}`.", "",
             "| Критерий | Результат | Проверенная граница |", "|---|---|---|"]
    for gate in report.get("gates", []):
        lines.append(f"| {gate['id']} | {'пройден' if gate['status'] == 'passed' else 'провален'} | {gate['detail'].replace('|', '/')} |")
    if report["issues"]:
        lines += ["", "Причины отказа:", ""]+[f"- {issue}" for issue in report["issues"]]
    lines += ["", "## Частота, события и нагрузка", "",
              f"Пассивный realtime: {fmt(passive.get('simulated_ms'), 0)} игровых мс, {stream.get('samples', 'нет данных')} снимков; "
              f"периодические {stream.get('periodic_samples', '?')}, событийные {stream.get('event_only_samples', '?')}, "
              f"пропуски {stream.get('dropped_total', '?')}. Game и wall интервалы измеряются отдельно.", "",
              f"Период 50 мс при fixed 30 Hz квантован игровыми шагами 33/33/34 мс. "
              f"p95 периодических интервалов {fmt((stream.get('periodic_interval_game_ms') or {}).get('p95'), 0)} мс; "
              f"максимум {fmt((stream.get('periodic_interval_game_ms') or {}).get('max'), 0)} мс. "
              "Событийные дополнительные снимки не используются для искусственного улучшения periodic cadence.", "",
              f"Фактический wall интервал периодических снимков: p95 **{fmt(wall_cadence.get('p95'), 3)} мс**, "
              f"максимум **{fmt(wall_cadence.get('max'), 3)} мс**. Это emission timestamps одного engine process, "
              "а не время получения пакета Python клиентом.", "",
              f"В поток доставлено {report.get('events', {}).get('delivered', '?')} событий; "
              f"максимальная задержка от native source — {fmt(report.get('events', {}).get('max_delivery_sim_steps'), 0)} sim step. "
              "Это измерение доставки в числовой поток; latency GRU и боевых команд ещё не измерялась.", "",
              f"Добавленная работа наблюдений в simulation thread: p95 {fmt(report.get('observer_cost_wall_us', {}).get('p95'))} wall µs. "
              f"Максимум всех серий **{fmt(report.get('observer_cost_wall_us', {}).get('max'))} µs** отмечен в `{max_cost_run}`. "
              "Cost складывает sample и transport одного sim step для четырёх NPC: native hooks, признаки/видимость/16 лучей, "
              "сериализацию журнала, watch list и кодирование/отправку JSONL. Запись timing journal исключена из instrumentation; "
              "paired throughput учитывает её.", "",
              f"В десятиминутном realtime p95 **{fmt(realtime_cost.get('p95'))} µs**, максимум **{fmt(realtime_cost.get('max'))} µs** на sim step. "
              f"Пакетный transport отдельно: {transport.get('n', 'нет данных')} отправки, p95 **{fmt(transport.get('p95'))} µs**, "
              f"максимум **{fmt(transport.get('max'))} µs** на bulk пакет нескольких samples. "
              "Стоимость целого пакета не равна стоимости одного actor sample; transport включён в общую статистику шагов.", "",
              "| Парный прогон | Off, substeps/s | On, substeps/s | Падение |", "|---|---:|---:|---:|"]
    throughput = report.get("throughput", {})
    for pair in throughput.get("pairs", []):
        lines.append(f"| {pair['pair_id']} | {fmt(pair['off_substeps_per_s'])} | {fmt(pair['on_substeps_per_s'])} | {fmt(100*pair['drop_fraction'])}% |")
    drop = throughput.get("drop_fraction")
    lines += ["", f"Медиана off/on: {fmt(throughput.get('median_off_substeps_per_s'))} / "
              f"{fmt(throughput.get('median_on_substeps_per_s'))} substeps/s; падение {fmt(100*drop if number(drop) else None)}%. "
              "Парные серии используют одинаковые сценарии и sim steps; processing wall включает запрос advance и получение наблюдений. "
              "Отрицательное падение отражает шум парных измерений и не доказывает ускорение от подписки.", ""]
    lines += ["## Ресурсы и realtime pacing", "",
              "Ресурсы измерены через `/proc` между первым и последним sample каждой фазы после reset/watch. "
              "VmHWM — максимум всего общего engine process, включая предыдущие фазы; его нельзя трактовать как отдельный peak текущего эпизода.", "",
              "| Прогон | RSS min–max, MiB | VmHWM max, MiB | CPU user+sys, с | Среднее занятых CPU cores | Threads / FD min–max |",
              "|---|---:|---:|---:|---:|---|"]
    for run in report.get("runs", []):
        runtime = run.get("runtime", {})
        resources = runtime.get("resources", {})
        rss, hwm = resources.get("rss_kib_range"), resources.get("process_hwm_kib_range")
        threads, fds = resources.get("threads_range"), resources.get("file_descriptors_range")
        lines.append(f"| {run.get('name')} | {fmt(rss[0]/1024) if rss else '?'}–{fmt(rss[1]/1024) if rss else '?'} | "
                     f"{fmt(hwm[1]/1024) if hwm else '?'} | {fmt(resources.get('cpu_total_delta_s'))} | "
                     f"{fmt(resources.get('mean_busy_cpu_cores'))} | {threads[0] if threads else '?'}–{threads[1] if threads else '?'} / "
                     f"{fds[0] if fds else '?'}–{fds[1] if fds else '?'} |")
    runtime = passive.get("runtime", {})
    resources = runtime.get("resources", {})
    first_rss, last_rss, delta_rss = (resources.get(key) for key in ("rss_first_kib", "rss_last_kib", "rss_delta_kib"))
    lines += ["", f"Десятиминутный realtime: RSS **{fmt(first_rss/1024 if number(first_rss) else None)} → "
              f"{fmt(last_rss/1024 if number(last_rss) else None)} MiB**, дельта "
              f"**{fmt(delta_rss/1024 if number(delta_rss) else None)} MiB**. "
              f"Sim {fmt(runtime.get('simulated_ms'), 0)} / wall {fmt(runtime.get('elapsed_wall_ms_after_reset'), 0)} мс после reset, "
              f"ratio {fmt(runtime.get('simulated_wall_ratio_after_reset'), 5)}; "
              f"максимальное отставание pacing **{fmt(runtime.get('max_realtime_lag_ms'), 0)} мс**. "
              "Намеренное realtime ожидание не включается в processing throughput парных lockstep серий.", "",
              "## Корректность и границы", "",
              "Снимки имеют общую фазу `post_simulation_post_animation`, runtime ID экземпляра, generation и единую последовательность periodic/event rows. "
              "Переключение watch не захватывает тело. `can_decide=false`, `control_allowed=false`, `action_mask_valid=false`: "
              "12 значений `availability_mask` — консервативная доступность native примитивов; общий resolver и действительные action masks относятся к этапу 4.", "",
              "Текущие признаки невидимой цели отсутствуют. Последние известные положение и скорость маркируются своим возрастом; "
              "отдельный debug trace хранит истину для проверки, без включения в policy observations. "
              "Проверка hidden_move меняет настоящее положение/HP за преградой и проверяет замороженное last-known состояние.", "",
              "Нативная механика сравнивается при выключенной и включённой подписке: ordered choice/consume, animation, root motion/collisions, попадания/урон и исходы seeds 17/29/43. "
              "Такая parity подтверждает пассивность наблюдений, а не качество обученного контроллера.", "",
              "ASR/TTS и GPU проверялись только по состояниям units, shared GPU snapshot и устройствам процесса. Их latency не измерялась. "
              "Нейросеть, обучение, RPC захвата тела и внешний исполнитель ещё не реализованы этим этапом.", "",
              "| Provenance | Значение |", "|---|---|"]
    for key, value in report.get("provenance", {}).items():
        lines.append(f"| {key} | `{value}` |")
    lines += [""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        config = read_json(args.config)
        report = summarize(read_json(args.run_dir / "suite.json"), read_json(args.run_dir / "manifest.json"), args.run_dir, config)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"observation report: {error}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "body-observe.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)+"\n")
    (args.output_dir / "body-observe.md").write_text(markdown(report, args.run_dir, args.output_dir))
    print(f"observation report: {report['status']}; {len(report['issues'])} failed gates")
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
