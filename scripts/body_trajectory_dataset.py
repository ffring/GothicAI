#!/usr/bin/env python3
"""Fail-closed trajectory validation, grouped splits and ordered BC sequences.

This module uses only the standard library. It reads recorded engine evidence;
it never simulates combat, loads a model or constructs expert labels from poses.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
import importlib
import struct
from pathlib import Path
from typing import Any, Iterator, Sequence

ACTIONS = ("continue", "approach", "turn_to_target", "strafe_left", "strafe_right", "jump_back",
           "attack_front", "attack_left", "attack_right", "block", "wait_200", "wait_300")
SCHEMA_VERSION = 1
FEATURE_VERSION = "body-actor-numeric-v1"
PERIODIC_FEATURE_VERSION = "body-periodic-numeric-v1"
IDENTITY = ("session_epoch", "world_epoch", "control_generation", "entity_id")
EXECUTION_ENDS = {"completed", "cancelled", "rejected"}
OBSERVATION_PHASES = {"post_simulation_post_animation", "pre_native_primitive"}
REQUIRED_CONDITIONS = ("wall", "target_visibility_lost", "bad_distance")
PRIMITIVE_ACTION = {2: 1, 12: 2, 13: 2, 7: 3, 8: 4, 3: 5, 4: 6, 5: 7, 6: 8, 9: 9, 10: 10, 11: 11}
APPROVED_TEACHER_MODULES = {"defensive-melee-scripted-v1": "body_demonstration_teacher",
                            "observed-melee-combat-v1": "body_combat_teacher",
                            "observed-reactive-defence-v1": "body_defence_teacher",
                            "observed-safe-peer-v1": "body_safe_peer_teacher"}


class DatasetValidationError(ValueError):
    """Recorded evidence is incomplete, inconsistent or unsafe for training."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DatasetValidationError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def registry_sha256(registry: dict[str, Any]) -> str:
    require(isinstance(registry, dict) and registry.get("schema_version") == 1 and isinstance(registry.get("teachers"), dict),
            "invalid declared teacher registry")
    return hashlib.sha256(json.dumps(registry, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def merge_teacher_registries(registries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Preserve every frozen approval's registry hash while combining sources."""
    require(bool(registries), "no teacher registries to merge")
    teachers = {}
    contexts = {}
    for registry in registries:
        digest = registry_sha256(registry)
        for teacher_id, entry in registry["teachers"].items():
            require(teacher_id not in teachers or teachers[teacher_id] == entry, "conflicting teacher registry entry")
            teachers[teacher_id] = entry
        candidates = {digest: registry, **registry.get("original_registries", {})}
        for original_hash, original in candidates.items():
            require(registry_sha256(original) == original_hash, "original teacher registry context SHA differs")
            require(original_hash not in contexts or contexts[original_hash] == original, "conflicting original registry context")
            contexts[original_hash] = original
    for original in contexts.values():
        require(all(teachers.get(teacher_id) == entry for teacher_id, entry in original["teachers"].items()),
                "original teacher entry differs from merged registry")
    return {"schema_version": 1, "teachers": teachers, "original_registries": contexts}


def original_teacher_registry(registry: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Resolve immutable per-episode approval context without rewriting it."""
    digest = registry_sha256(registry)
    expected = manifest.get("scripted_expert_approval", {}).get("registry_sha256", digest)
    if expected == digest:
        return registry
    context = registry.get("original_registries", {}).get(expected)
    require(isinstance(context, dict) and registry_sha256(context) == expected, "missing original teacher registry approval context")
    require(all(registry["teachers"].get(teacher_id) == entry for teacher_id, entry in context["teachers"].items()),
            "original teacher entry differs from merged registry")
    return context


def strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise DatasetValidationError(f"nonfinite JSON number: {value}")

    def real(value):
        result = float(value)
        require(math.isfinite(result), f"nonfinite JSON number: {value}")
        return result

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=real)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise DatasetValidationError(str(error)) from error


def read_json(path: Path) -> dict[str, Any]:
    value = strict_json(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON object required: {path}")
    return value


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Parse every line strictly without retaining unrelated journal payloads."""
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            require(line.endswith("\n"), f"partial JSONL line {line_number}: {path}")
            require(bool(line.strip()), f"blank JSONL line {line_number}: {path}")
            value = strict_json(line)
            require(isinstance(value, dict), f"JSONL object required at {path}:{line_number}")
            yield value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))


def integer(value: Any, name: str, *, minimum: int = 0) -> int:
    require(type(value) is int and value >= minimum, f"invalid integer {name}: {value!r}")
    return value


def finite(value: Any, name: str) -> float:
    require(type(value) in (int, float) and math.isfinite(value), f"nonfinite/nonnumeric {name}")
    return float(value)


def identity(row: dict[str, Any]) -> tuple[int, ...]:
    return tuple(integer(row.get(key), key) for key in IDENTITY)


def record_time(row: dict[str, Any]) -> int:
    return integer(row.get("t_game_ms"), "t_game_ms")


def actor_features(observation: dict[str, Any], *, sequence_dt_game_ms: int | None = None) -> dict[str, float]:
    """A fixed actor-only view, excluding oracle, IDs, pose labels and future events.

    Every nullable value carries an explicit validity bit. Null is not turned
    into knowledge of a hidden target. These names/order are shared by the
    train-only normalizer and sequence loader.
    """
    features: dict[str, float] = {}

    def add(name: str, value: Any) -> None:
        features[name + ".valid"] = float(value is not None)
        if value is None:
            features[name] = 0.0
        elif type(value) is bool:
            features[name] = float(value)
        else:
            features[name] = finite(value, name)

    add("dt_game_ms", observation.get("dt_game_ms") if sequence_dt_game_ms is None else sequence_dt_game_ms)
    own = observation.get("self", {})
    target = observation.get("target", {})
    require(isinstance(own, dict) and isinstance(target, dict), "self/target must be objects")
    for key in ("hp_fraction", "weapon_state", "weapon_length_cm", "skill", "skill_talent", "skill_valid",
                "dead", "unconscious", "down", "in_air", "swim", "dive", "attack_anim", "prehit",
                "defence_active", "jumpback_active", "def_window", "par_window", "interruptible", "velocity_valid"):
        add("self." + key, own.get(key))
    for key in ("present", "visible", "current_valid", "yaw_error_rad", "fight_distance_cm", "attack_range_cm",
                "g_range_cm", "closeup_range_cm", "in_w_range", "in_g_range", "in_closeup", "focus_30",
                "focus_5", "melee_line_clear", "hp_fraction", "weapon_state", "attack_anim", "prehit",
                "defence_active", "jumpback_active", "down", "last_known_age_ms"):
        add("target." + key, target.get(key))
    for prefix, section, keys in (("self", own, ("linear_velocity_cm_s",)),
                                  ("target", target, ("relative_position_cm", "relative_velocity_cm_s"))):
        for key in keys:
            vector = section.get(key)
            require(vector is None or (isinstance(vector, list) and len(vector) == 3), f"invalid vector {prefix}.{key}")
            for axis in range(3):
                add(f"{prefix}.{key}.{axis}", None if vector is None else vector[axis])
    gates = own.get("gates", {})
    require(isinstance(gates, dict), "self.gates must be object")
    for key in ("fai_wait_remaining_ms", "ani_wait_remaining_ms", "ai_wait_remaining_ms", "output_wait_remaining_ms"):
        add("self.gates." + key, gates.get(key))
    # Ownership/admin flags remain in the raw evidence and validation gates.
    # Physical eligibility is meaningful under both native and policy owners.
    add("native_decision_eligible", observation.get("native_decision_eligible"))
    goal = observation.get("goal", {})
    require(isinstance(goal, dict), "goal must be object")
    add("goal.native_combat", goal.get("kind") == "native_combat")
    add("goal.native_idle", goal.get("kind") == "native_idle")
    add("goal.move_hint", goal.get("move_hint"))
    obstacles = observation.get("local_obstacles", {})
    require(isinstance(obstacles, dict), "local_obstacles must be object")
    rays = obstacles.get("rays", [])
    require(isinstance(rays, list) and len(rays) in (0, 16), "local obstacle ray count must be 0 or 16")
    for index in range(16):
        ray = rays[index] if rays else {}
        require(isinstance(ray, dict), "obstacle ray must be object")
        add(f"ray.{index}.distance_cm", ray.get("distance_cm"))
        add(f"ray.{index}.hit", ray.get("hit"))
    return features


def validate_observation(observation: dict[str, Any]) -> None:
    require(observation.get("schema_version") == 1, "unsupported body observation schema")
    identity(observation)
    integer(observation.get("observation_seq"), "observation_seq", minimum=1)
    now = record_time(observation)
    sim_step = integer(observation.get("sim_step"), "observation sim_step")
    integer(observation.get("dt_game_ms"), "dt_game_ms")
    require(observation.get("phase") in OBSERVATION_PHASES, "unknown observation phase")
    require("debug_oracle" not in observation and "oracle" not in observation, "oracle mixed into actor observation")
    for flag in ("can_decide", "native_decision_eligible", "action_mask_valid"):
        require(type(observation.get(flag)) is bool, f"observation missing boolean {flag}")
    availability = observation.get("availability_mask")
    require(isinstance(availability, list) and len(availability) == len(ACTIONS) and
            all(type(bit) is bool for bit in availability), "invalid availability mask")
    action_mask = observation.get("action_mask")
    if action_mask is not None or observation["action_mask_valid"]:
        require(isinstance(action_mask, list) and len(action_mask) == len(ACTIONS) and
                all(type(bit) is bool for bit in action_mask), "invalid observation action mask")
    if observation["action_mask_valid"]:
        require(action_mask == availability and observation.get("mask_kind") == "shared_melee_resolver",
                "shared observation masks differ")
    require(not observation["can_decide"] or observation["action_mask_valid"], "eligible observation lacks action mask")
    target = observation.get("target", {})
    if target.get("current_valid") is False:
        for key in ("position_world_cm", "relative_position_cm", "linear_velocity_cm_s", "relative_velocity_cm_s",
                    "yaw_error_rad", "fight_distance_cm", "attack_range_cm", "g_range_cm", "closeup_range_cm",
                    "in_w_range", "in_g_range", "in_closeup", "focus_30", "focus_5", "melee_line_clear",
                    "hp", "hp_max", "hp_fraction", "weapon_state", "body_state_raw", "attack_anim", "prehit",
                    "defence_active", "jumpback_active", "down"):
            require(target.get(key) is None, f"hidden target has current feature {key}")
    for key in ("last_seen_game_ms",):
        value = target.get(key)
        require(value is None or integer(value, key) <= now, f"future target feature {key}")
    last_result = observation.get("last_result", {})
    if last_result.get("valid"):
        require(integer(last_result.get("source_game_ms"), "result source time") <= now, "future result in observation")
        require(integer(last_result.get("source_sim_step"), "result source step") <= sim_step, "future result step in observation")
    for event in observation.get("events", []):
        require(integer(event.get("source_game_ms"), "event source time") <= now, "future event in observation")
        require(integer(event.get("source_sim_step"), "event source step") <= sim_step, "future event step in observation")
    actor_features(observation)


def record_features(record: dict[str, Any]) -> dict[str, float]:
    builder = periodic_actor_features if record.get("model_view_version") == 1 else actor_features
    return builder(record["observation"], sequence_dt_game_ms=record["sequence_dt_game_ms"])


def periodic_actor_features(observation: dict[str, Any], *, sequence_dt_game_ms: int) -> dict[str, float]:
    """Stage 7 semantic goal aliases share one owner-independent feature view."""
    goal = dict(observation.get("goal", {}))
    if goal.get("kind") in ("combat", "native_combat"):
        goal["kind"] = "native_combat"
    elif goal.get("kind") in ("idle", "native_idle"):
        goal["kind"] = "native_idle"
    return actor_features({**observation, "goal": goal}, sequence_dt_game_ms=sequence_dt_game_ms)


def canonical_feature_names(feature_version: str = PERIODIC_FEATURE_VERSION) -> list[str]:
    require(feature_version in (FEATURE_VERSION, PERIODIC_FEATURE_VERSION), "unsupported feature packing version")
    return list(actor_features({"self": {}, "target": {}, "local_obstacles": {}}))


def policy_action_mask(observation: dict[str, Any]) -> list[bool]:
    """Shared model head vocabulary; raw engine availability stays unchanged."""
    availability = observation.get("availability_mask")
    require(isinstance(availability, list) and len(availability) == len(ACTIONS) and
            all(type(bit) is bool for bit in availability), "invalid policy availability mask")
    require(type(observation.get("native_decision_eligible")) is bool, "policy physical eligibility missing")
    mask = list(availability)
    if not any(mask):
        # A frozen terminal or ownership boundary still updates memory. This
        # synthetic head-only continue never becomes an API command or label.
        return [True]+[False]*(len(ACTIONS)-1)
    if observation["native_decision_eligible"] and any(mask[1:]):
        mask[0] = False
    return mask


def pack_features(observation: dict[str, Any], normalizer: dict[str, Any], *, sequence_dt_game_ms: int) -> list[float]:
    """Shared stdlib packing for the periodic live runtime and recorded sequences."""
    validate_observation(observation)
    integer(sequence_dt_game_ms, "sequence_dt_game_ms")
    version = normalizer.get("feature_version")
    names = canonical_feature_names(version)
    require(normalizer.get("feature_names") == names, "normalizer feature schema mismatch")
    if version == PERIODIC_FEATURE_VERSION:
        require(observation["phase"] == "post_simulation_post_animation" and observation.get("periodic") is True,
                "model memory accepts periodic snapshots only")
        raw = periodic_actor_features(observation, sequence_dt_game_ms=sequence_dt_game_ms)
    else:
        raw = actor_features(observation, sequence_dt_game_ms=sequence_dt_game_ms)
    result = []
    for name in names:
        mean = finite(normalizer["mean"][name], "normalizer mean")
        scale = finite(normalizer["std"][name], "normalizer std")
        require(scale > 0, "normalizer std must be positive")
        result.append(finite((raw[name]-mean)/scale, "normalized feature"))
    return result


@dataclass
class ValidatedEpisode:
    path: Path
    manifest: dict[str, Any]
    records: list[dict[str, Any]]
    observations: dict[tuple[int, int], dict[str, Any]]
    decisions: dict[tuple[int, str], dict[str, Any]]
    transitions: dict[tuple[int, str], dict[str, Any]]
    summary: dict[str, Any]


def manifest_path(path: Path) -> Path:
    return path / ("trajectory-manifest.json" if (path / "trajectory-manifest.json").is_file() else "manifest.json")


def records_path(path: Path, manifest: dict[str, Any]) -> Path:
    name = manifest.get("records_file", "records.jsonl")
    require(name in ("records.jsonl", "records-approved.jsonl"), "unsupported trajectory records file")
    return path / name


def validate_raw_links(path: Path, manifest: dict[str, Any], records: list[dict[str, Any]]) -> None:
    """Bind conversion to the complete globally ordered native capture journal."""
    capture_name = manifest.get("capture_file", manifest.get("raw_capture_file"))
    require(isinstance(capture_name, str) and capture_name, "raw capture file missing")
    capture_path = (path / capture_name).resolve()
    require(capture_path.is_relative_to(path) and capture_path.is_file(), "raw capture path invalid")
    listed = {item.get("path"): item.get("sha256") for item in manifest.get("source_files", [])}
    require(listed.get(capture_name) == sha256(capture_path), "raw capture is not bound to source SHA")
    raw_rows = read_jsonl(capture_path)
    require(bool(raw_rows), "raw capture empty")
    integrity = manifest["source_integrity"]
    by_sequence = {}
    raw_counts = Counter()
    previous = None
    for raw in raw_rows:
        sequence = integer(raw.get("record_seq"), "raw record_seq", minimum=1)
        require(sequence not in by_sequence and (previous is None or sequence == previous+1), "raw capture record gap/duplicate")
        require(raw.get("capture_generation") == integrity.get("capture_generation"), "raw capture generation mismatch")
        require(raw.get("session_epoch") == manifest["session_epoch"] and raw.get("world_epoch") == manifest["world_epoch"],
                "raw capture crosses episode epoch")
        record_time(raw)
        require(raw.get("kind") in ("observation", "decision", "execution", "control"), "unknown raw capture kind")
        by_sequence[sequence] = raw
        raw_counts[raw.get("kind")] += 1
        previous = sequence
    require(integrity.get("first_capture_record_seq") == raw_rows[0]["record_seq"] and
            integrity.get("last_capture_record_seq") == raw_rows[-1]["record_seq"] and
            integrity.get("capture_records") == len(raw_rows), "raw capture count/range differs")
    require(integrity.get("capture_kind_counts") == dict(raw_counts), "raw capture kind counts differ")
    packet_name = manifest.get("capture_packets_file")
    require(isinstance(packet_name, str), "capture packet evidence missing")
    packet_path = (path / packet_name).resolve()
    require(packet_path.is_relative_to(path) and packet_path.is_file() and listed.get(packet_name) == sha256(packet_path),
            "capture packet evidence is not bound to source SHA")
    packets = read_jsonl(packet_path)
    require(len(packets) == integrity.get("capture_packets") and packets, "capture packet count differs")
    offset = 0
    for index, packet in enumerate(packets, 1):
        require(packet.get("packet_seq") == index, "capture packet gap/duplicate")
        stats = packet.get("stats", {})
        require(stats.get("capture_generation") == integrity["capture_generation"], "packet capture generation mismatch")
        require(stats.get("capture_failed") is False and all(stats.get(key) == 0 for key in
                ("capture_failures_total", "dropped_total", "dropped_since_drain", "reset_discarded_total")),
                "native packet reports capture failure/loss")
        count = integer(packet.get("event_count"), "packet event_count")
        require(offset+count <= len(raw_rows), "packet event count exceeds raw capture")
        if count:
            require(packet.get("first_record_seq") == raw_rows[offset]["record_seq"] and
                    packet.get("last_record_seq") == raw_rows[offset+count-1]["record_seq"], "packet raw capture range differs")
        else:
            require(packet.get("first_record_seq") is None and packet.get("last_record_seq") is None,
                    "empty packet has raw capture range")
        offset += count
    require(offset == len(raw_rows) and packets[-1]["stats"].get("queue_size") == 0 and
            packets[-1]["stats"].get("record_seq_high_watermark") == raw_rows[-1]["record_seq"],
            "capture did not drain all emitted records")
    integrity_name = manifest.get("source_integrity_file")
    require(isinstance(integrity_name, str), "native source integrity evidence missing")
    integrity_path = (path / integrity_name).resolve()
    require(integrity_path.is_relative_to(path) and integrity_path.is_file() and listed.get(integrity_name) == sha256(integrity_path),
            "native source integrity is not bound to source SHA")
    evidence = read_json(integrity_path)
    observer = evidence.get("runner_status", {}).get("body_observation", {})
    control = evidence.get("control_events", {})
    require(all(type(observer.get(key)) is int and observer[key] == 0 for key in
                ("dropped_total", "event_dropped_total", "invalid_numeric_rows", "missed_period_buckets")) and
            type(control.get("dropped_total")) is int and control["dropped_total"] == 0,
            "native observer/control evidence reports missing data")
    require(integrity.get("gap_count") == integrity.get("missed_period_buckets") == observer["missed_period_buckets"] and
            integrity.get("dropped_observations") == observer["dropped_total"] and
            integrity.get("dropped_events") == observer["event_dropped_total"]+control["dropped_total"] and
            integrity.get("invalid_numeric_rows") == observer["invalid_numeric_rows"] and
            integrity.get("dropped_control_events") == control["dropped_total"], "manifest loss counters differ from native evidence")
    mapped: dict[str, set[int]] = defaultdict(set)
    canonical_observations: dict[tuple[int, int], dict[str, Any]] = {}
    canonical_decisions: dict[tuple[int, str], dict[str, Any]] = {}
    for record in records:
        if record["kind"] == "transition":
            continue
        sequence = record.get("raw_capture_seq", record.get("raw_record_seq"))
        raw = by_sequence.get(sequence)
        require(raw is not None, "canonical record lacks raw capture event")
        require(record.get("capture_generation") == raw["capture_generation"], "canonical capture generation mismatch")
        require(record.get("entity_id") == raw.get("entity_id") and record.get("t_game_ms") == raw["t_game_ms"],
                "canonical event identity/time differs from raw source")
        mapped[record["kind"]].add(sequence)
        if record["kind"] == "observation":
            require(raw.get("kind") == "observation" and record.get("observation") == raw.get("observation"),
                    "observation differs from raw native capture")
            require(record.get("raw_observation_seq") == raw["observation"]["observation_seq"] and
                    record.get("raw_observation_source") == raw.get("source"), "raw observation provenance differs")
            canonical_observations[(record["entity_id"], record["observation_seq"])] = record
        elif record["kind"] == "decision":
            canonical_decisions[decision_key(record)] = record
            anchor = canonical_observations.get((record["entity_id"], record["observation_seq"]))
            require(anchor is not None, "raw-linked decision lacks observation")
            anchor_raw_sequence = anchor.get("raw_capture_seq", anchor.get("raw_record_seq"))
            require(anchor_raw_sequence < sequence, "decision uses future/raw-later observation")
            require(record.get("raw_observation_record_seq") == anchor_raw_sequence, "decision raw observation reference differs")
            if record["source"] == "native":
                require(raw.get("kind") == "decision" and raw.get("source") == "native", "native teacher lacks raw choice event")
                require(raw.get("observation_record_seq") == anchor_raw_sequence and
                        raw.get("decision_id") == record.get("raw_decision_id"), "native teacher exact choice reference differs")
                for key in ("execution_id", "action_id", "effective_primitive", "action_mask", "decision_valid"):
                    require(raw.get(key) == record.get(key), f"native decision {key} differs from raw choice")
                require(raw.get("native_intent") == record.get("raw_intent"), "native intent differs from raw choice")
            else:
                event = raw.get("body_control_event", {})
                require(raw.get("kind") == "control" and event.get("phase") == "accepted", "scripted decision lacks raw acceptance")
                for key in ("execution_id", "action_id", "command_id", "control_generation"):
                    require(event.get(key) == record.get(key), f"scripted decision {key} differs from raw acceptance")
                require(event.get("observation_seq") == anchor["raw_observation_seq"], "scripted raw sampled observation differs")
        elif record["kind"] == "execution":
            decision = canonical_decisions.get(decision_key(record))
            require(decision is not None, "raw-linked execution lacks decision")
            if record["source"] == "native":
                require(raw.get("kind") == "execution" and raw.get("decision_id") == decision.get("raw_decision_id"),
                        "native execution lacks raw lifecycle event")
                event = raw
            else:
                require(raw.get("kind") == "control", "scripted execution lacks raw control lifecycle")
                event = raw.get("body_control_event", {})
                require(event.get("command_id") == decision.get("command_id"), "scripted execution command differs")
            for key in ("execution_id", "phase", "effective_primitive"):
                require(event.get(key) == record.get(key), f"execution {key} differs from raw lifecycle")
            require(event.get("t_game_ms") == record["t_game_ms"], "execution time differs from raw lifecycle")
            for key in ("reason", "physical_cancelled", "execution_incomplete", "segment_started", "pose_before", "pose_after"):
                require(event.get(key) == record.get(key), f"execution {key} proof differs from raw lifecycle")
    expected_observations = {raw["record_seq"] for raw in raw_rows if raw["kind"] == "observation"}
    expected_native_decisions = {raw["record_seq"] for raw in raw_rows if raw["kind"] == "decision"}
    expected_native_executions = {raw["record_seq"] for raw in raw_rows if raw["kind"] == "execution"}
    require(mapped["observation"] == expected_observations, "conversion dropped or duplicated raw observations")
    require(expected_native_decisions <= mapped["decision"], "conversion dropped native choice event")
    require(expected_native_executions <= mapped["execution"], "conversion dropped native execution event")
    for raw in raw_rows:
        if raw["kind"] == "control":
            event = raw.get("body_control_event", {})
            if event.get("command_id") and event.get("phase") == "accepted":
                require(raw["record_seq"] in mapped["decision"] and raw["record_seq"] in mapped["execution"],
                        "conversion dropped scripted accepted decision")
            elif event.get("command_id") and event.get("phase") in ("started", "consumed", "completed", "cancelled", "rejected"):
                require(raw["record_seq"] in mapped["execution"], "conversion dropped scripted execution")


def decision_key(row: dict[str, Any]) -> tuple[int, str]:
    entity = integer(row.get("entity_id"), "entity_id", minimum=1)
    require(isinstance(row.get("decision_id"), (str, int)) and str(row["decision_id"]), "missing decision ID")
    return entity, str(row["decision_id"])


def validate_episode(path: Path | str, *, teacher_registry: dict[str, Any] | None = None,
                     _original_records: bool = False) -> ValidatedEpisode:
    """Validate every link before admitting an episode to a dataset."""
    path = Path(path).resolve()
    manifest = read_json(manifest_path(path))
    require(manifest.get("schema_version") == SCHEMA_VERSION, "unsupported trajectory manifest schema")
    require(manifest.get("writer_closed") is True, "trajectory writer has not closed durably")
    episode_id = manifest.get("episode_id")
    require(isinstance(episode_id, (str, int)) and str(episode_id), "manifest missing episode ID")
    records = read_jsonl(path / "records.jsonl" if _original_records else records_path(path, manifest))
    require(bool(records), "empty episode records")
    observations: dict[tuple[int, int], dict[str, Any]] = {}
    decisions: dict[tuple[int, str], dict[str, Any]] = {}
    executions: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    transitions: dict[tuple[int, str], dict[str, Any]] = {}
    expected_epoch = (integer(manifest.get("session_epoch"), "manifest session_epoch"),
                      integer(manifest.get("world_epoch"), "manifest world_epoch"))
    actors = manifest.get("actors")
    require(isinstance(actors, list) and actors, "manifest actor list missing")
    actor_ids = [integer(actor.get("entity_id") if isinstance(actor, dict) else actor, "actor ID", minimum=1)
                 for actor in actors]
    require(len(actor_ids) == len(set(actor_ids)), "duplicate manifest actor")
    sequences: dict[int, int] = {}
    times: dict[int, int] = {}
    counts = Counter()
    observation_orders: dict[tuple[int, int], int] = {}
    initial_time = integer(manifest.get("initial", {}).get("game_ms"), "initial time")
    for index, record in enumerate(records, 1):
        require(record.get("record_seq") == index, "silent record gap or duplicate record_seq")
        require(record.get("episode_id") == episode_id, "mixed episode records")
        kind = record.get("kind")
        require(kind in ("observation", "decision", "execution", "transition"), "unknown record kind")
        counts[kind] += 1
        require(identity(record)[:2] == expected_epoch, "mixed episode epoch")
        entity = record["entity_id"]
        require(entity in actor_ids, "record actor outside manifest")
        if kind == "observation":
            observation = record.get("observation")
            require(isinstance(observation, dict), "missing observation payload")
            validate_observation(observation)
            require(identity(observation) == identity(record), "observation wrapper identity mismatch")
            require(record_time(record) == record_time(observation), "observation wrapper time mismatch")
            sequence = integer(record.get("observation_seq"), "dataset observation_seq", minimum=1)
            key = (entity, sequence)
            require(key not in observations, "duplicate observation")
            require(sequence > sequences.get(entity, 0), "backwards actor observation sequence")
            require(observation["t_game_ms"] >= times.get(entity, 0), "backwards actor observation time")
            require(record.get("sequence_dt_game_ms") == observation["t_game_ms"]-times.get(entity, initial_time),
                    "canonical sequence dt mismatch")
            require(record.get("raw_dt_game_ms") == observation["dt_game_ms"], "raw observation dt provenance mismatch")
            sequences[entity], times[entity] = sequence, observation["t_game_ms"]
            observations[key], observation_orders[key] = record, index
        elif kind == "decision":
            key = decision_key(record)
            require(key not in decisions, "duplicate decision")
            anchor = observations.get((entity, record.get("observation_seq")))
            require(anchor is not None, "decision lacks preceding observation")
            require(identity(anchor) == identity(record), "decision generation mismatch")
            require(record_time(anchor["observation"]) <= record_time(record), "decision precedes observation")
            action = record.get("action_id")
            valid = record.get("decision_valid")
            require(type(valid) is bool, "decision_valid missing")
            require(action is None or (type(action) is int and 0 <= action < len(ACTIONS)), "unknown action")
            require(not valid or (action is not None and action > 0), "continue/unknown action is not a decision label")
            require(action == PRIMITIVE_ACTION.get(record.get("effective_primitive")), "action does not match effective normalized primitive")
            require(record.get("source") in ("native", "scripted"), "unknown decision source")
            require(type(record.get("expert")) is bool and type(record.get("accepted")) is bool,
                    "decision expert/accepted missing")
            require(not (valid or record.get("source") == "scripted") or record.get("eligible") is True,
                    "decision made while locked")
            require(record.get("expert") is False or record.get("source") == "native" or
                    (teacher_registry is not None and manifest.get("scripted_expert_approval") is not None and not _original_records),
                    "scripted expert lacks declared teacher approval")
            require(not record["expert"] or valid, "diagnostic/native noop marked expert")
            mask = record.get("action_mask")
            if valid or record["source"] == "scripted":
                require(isinstance(mask, list) and len(mask) == len(ACTIONS) and all(type(bit) is bool for bit in mask),
                        "decision lacks valid recorded mask")
                require(type(action) is int and action > 0, "scripted action cannot be unknown/continue")
                require(mask[action] is True, "label outside recorded mask")
                anchor_mask = anchor["observation"].get("action_mask")
                require(anchor.get("label_mask", anchor_mask) == anchor_mask, "label mask differs from captured shared mask")
                require(mask == anchor_mask, "decision mask differs from exact recorded observation mask")
            if record["expert"] and record["source"] == "native":
                require(anchor["observation"].get("phase") == "pre_native_primitive", "native expert lacks exact pre-choice observation")
                require(record_time(anchor["observation"]) == record_time(record), "native teacher snapshot is stale")
                require(anchor["observation"].get("native_decision_eligible") is True, "native teacher snapshot is ineligible")
                require(anchor["observation"].get("action_mask_valid") is True and
                        anchor["observation"].get("mask_kind") == "shared_melee_resolver",
                        "native teacher lacks valid shared resolver snapshot mask")
            elif record["source"] == "scripted":
                require(anchor["observation"].get("can_decide") is True and
                        anchor["observation"].get("action_mask_valid") is True, "scripted decision lacks eligibility")
            require(anchor.get("decision_valid") is valid and anchor.get("bc_label") == (action if record["expert"] else None),
                    "observation loss label disagrees with decision")
            decisions[key] = record
        elif kind == "execution":
            key = decision_key(record)
            decision = decisions.get(key)
            require(decision is not None, "execution lacks preceding decision")
            require(identity(record) == identity(decision), "execution generation mismatch")
            require(record_time(record) >= record_time(decision), "execution precedes decision")
            require(record.get("execution_id") == decision.get("execution_id"), "execution ID mismatch")
            require(record.get("action_id") == decision["action_id"], "effective action mismatch")
            phase = record.get("phase")
            require(phase in {"accepted", "started", "continued", "consumed"} | EXECUTION_ENDS, "unknown execution phase")
            existing = executions[key]
            require(not existing or record_time(record) >= record_time(existing[-1]), "backwards execution time")
            require(not any(row["phase"] in EXECUTION_ENDS for row in existing), "execution after ended command")
            require(not any(row["phase"] == phase for row in existing), "duplicate execution phase")
            if phase == "started":
                require(not any(row["phase"] in ("started", "consumed") for row in existing) and decision["accepted"],
                        "unaccepted or twice-started command")
            elif phase == "accepted":
                require(not existing and decision["accepted"], "duplicate or contradictory acceptance")
            elif phase == "consumed":
                require(any(row["phase"] == "started" for row in existing) or record.get("source") == "native",
                        "consume without start")
            elif phase == "completed":
                require(any(row["phase"] == "started" for row in existing), "completion without start")
            elif phase == "rejected":
                require((not existing and not decision["accepted"]) or
                        (record.get("source") == "native" and not any(row["phase"] == "started" for row in existing) and
                         record.get("reason") == "consumed_without_start"), "rejection contradicts acceptance")
            existing.append(record)
        else:
            key = decision_key(record)
            require(key in decisions, "transition lacks decision")
            require(key not in transitions, "duplicate transition")
            transitions[key] = record
    require(all(counts.get(key, 0) == manifest.get("counts", {}).get(key, 0) for key in
                ("observation", "decision", "execution", "transition")), "manifest record counts differ")
    source_integrity = manifest.get("source_integrity", {})
    require(all(source_integrity.get(key) == 0 for key in ("gap_count", "dropped_observations", "dropped_events", "dropped_capture_records",
                                                          "reset_discarded_total", "capture_failures_total")) and
            source_integrity.get("capture_failed") is False,
            "raw recording has missing or unverified gap/drop counters")
    for item in manifest.get("source_files", []):
        require(isinstance(item, dict) and isinstance(item.get("path"), str), "invalid source file manifest")
        source = (path / item["path"]).resolve()
        require(source.is_relative_to(path), "source evidence escapes episode directory")
        require(source.is_file() and sha256(source) == item.get("sha256"), "source evidence SHA mismatch")
    validate_raw_links(path, manifest, records)
    final = manifest.get("final", {})
    require(isinstance(final, dict), "missing final outcome")
    final_time = integer(final.get("t_game_ms"), "final time")
    for flag in ("terminated", "truncated", "infrastructure"):
        require(type(final.get(flag)) is bool, "missing final outcome flags")
    require(sum(bool(final[flag]) for flag in ("terminated", "truncated", "infrastructure")) == 1,
            "episode must have one explicit ended classification")
    accepted, started, completed, cancelled, rejected, uncompleted = (0,) * 6
    labelled_observations = set()
    for key, decision in decisions.items():
        anchor_key = (key[0], decision["observation_seq"])
        require(anchor_key not in labelled_observations, "multiple decisions from one observation")
        labelled_observations.add(anchor_key)
        require(record_time(decision) <= final_time, "decision after final state")
        lifecycle = executions[key]
        phases = [row["phase"] for row in lifecycle]
        require(not lifecycle or record_time(lifecycle[-1]) <= final_time, "execution after final state")
        accepted += decision["accepted"]
        started += "started" in phases
        completed += "completed" in phases
        cancelled += "cancelled" in phases
        rejected += "rejected" in phases
        if "consumed" in phases and "started" not in phases:
            require(lifecycle[-1]["phase"] in ("rejected", "cancelled") and
                    lifecycle[-1].get("reason") == "consumed_without_start" and
                    decision.get("source") == "native", "unstarted consumed primitive lacks explicit native noop outcome")
        require(decision["accepted"] or phases == ["rejected"], "unaccepted decision lacks rejection")
        transition = transitions.get(key)
        require(transition is not None, "decision lacks ended/uncompleted transition annotation")
        require(identity(transition) == identity(decision), "transition generation mismatch")
        require(transition.get("observation_seq") == decision["observation_seq"], "transition observation mismatch")
        next_seq = transition.get("next_observation_seq")
        next_record = observations.get((key[0], next_seq))
        require(next_record is not None or (final["infrastructure"] and transition.get("uncompleted") is True and
                transition.get("usable_for_bc") is False and next_seq is None), "transition lacks next observation")
        if next_record is None:
            require(transition.get("execution_status") == "uncompleted", "missing final observation not annotated")
            require(transition.get("duration_ms") == final_time-record_time(decision), "missing-final duration mismatch")
            uncompleted += 1
            continue
        next_time = record_time(next_record["observation"])
        require(next_seq > decision["observation_seq"] and next_time >= record_time(decision), "transition points backwards")
        require(next_record["record_seq"] > observation_orders[anchor_key], "next observation is not subsequent")
        require(next_time <= final_time, "transition next observation after episode")
        require(transition.get("duration_ms") == next_time-record_time(decision), "transition duration mismatch")
        outcome_time = record_time(lifecycle[-1]) if lifecycle else record_time(decision)
        require(next_time >= outcome_time, "next observation precedes execution outcome")
        recording_end = bool(lifecycle and lifecycle[-1].get("physical_cancelled") is False and
                             lifecycle[-1].get("execution_incomplete") is True)
        if recording_end:
            require(lifecycle[-1].get("reason") == "recorder_disabled", "unknown instrumentation censoring")
        expected_status = phases[-1] if phases and phases[-1] in EXECUTION_ENDS and not recording_end else "episode_ended"
        require(transition.get("execution_status") == expected_status, "transition hides unfinished execution")
        if expected_status == "episode_ended":
            uncompleted += 1
            require(transition.get("uncompleted") is True, "unfinished execution not annotated")
            require(next_time == final_time, "unfinished execution lacks actual final observation")
        else:
            require(transition.get("uncompleted") is False, "ended execution marked unfinished")
            require(next_record.get("raw_capture_seq", next_record.get("raw_record_seq")) >
                    lifecycle[-1].get("raw_capture_seq", lifecycle[-1].get("raw_record_seq")),
                    "next observation causally precedes execution outcome")
        if transition.get("raw_next_observation_record_seq") is not None:
            require(transition["raw_next_observation_record_seq"] ==
                    next_record.get("raw_capture_seq", next_record.get("raw_record_seq")), "transition raw next reference mismatch")
        for flag in ("terminated", "truncated", "infrastructure"):
            require(type(transition.get(flag)) is bool, "transition missing outcome flag")
            require(not transition[flag] or (final[flag] and next_time == final_time), "transition has premature outcome flag")
        require(type(transition.get("usable_for_bc")) is bool, "usable_for_bc annotation missing")
        boundary = identity(next_record) != identity(decision)
        require(not boundary or (next_time == final_time and final["truncated"] and final.get("reason") == "target_visibility_lost"
                and transition.get("usable_for_bc") is False), "transition crosses control generation")
        require(not transition.get("usable_for_bc") or (decision["expert"] and decision["accepted"] and
                    "started" in phases and expected_status == "completed" and not final["infrastructure"]),
                "unsafe transition marked usable for BC")
    for key, record in observations.items():
        require(record_time(record["observation"]) <= final_time, "observation after final state")
        if key not in labelled_observations:
            require(record.get("decision_valid") is False and record.get("bc_label") is None,
                    "locked/periodic observation invents a label")
    require(len(transitions) == len(decisions), "extra transition")
    group = manifest.get("group")
    require(isinstance(group, dict) and isinstance(group.get("setup_family"), str) and group["setup_family"],
            "manifest missing setup family")
    integer(group.get("seed"), "group seed")
    require(type(manifest.get("ood")) is bool, "explicit OOD classification missing")
    summary = {"episode_id": episode_id, "counts": dict(counts), "accepted": accepted, "started": started,
               "completed": completed, "cancelled": cancelled, "rejected": rejected, "uncompleted": uncompleted,
               "physical_cancellations": sum(any(row.get("phase") == "cancelled" and row.get("physical_cancelled") is True
                                                 for row in lifecycle) for lifecycle in executions.values()),
               "instrumentation_censored": sum(any(row.get("phase") == "cancelled" and row.get("reason") == "recorder_disabled"
                                                    and row.get("execution_incomplete") is True and row.get("physical_cancelled") is False
                                                    for row in lifecycle) for lifecycle in executions.values()),
               "native_noop_skips": sum(any(row.get("reason") == "consumed_without_start" for row in lifecycle)
                                         for lifecycle in executions.values()),
               "expert_labels": sum(row["expert"] for row in decisions.values()),
               "usable_bc_labels": sum(bool(row.get("usable_for_bc")) for row in transitions.values()),
               "outcome": {key: final[key] for key in ("terminated", "truncated", "infrastructure", "reason") if key in final}}
    result = ValidatedEpisode(path, manifest, records, observations, decisions, transitions, summary)
    if manifest.get("model_sequence_start") is not None:
        validate_prepared_sequence_start(result)
    if manifest.get("records_file") == "records-approved.jsonl" and not _original_records:
        require(teacher_registry is not None, "approved scripted dataset requires declared teacher registry")
        original = validate_episode(path, teacher_registry=None, _original_records=True)
        expected, approval = approve_scripted_records(original, teacher_registry)
        require(expected == records and approval == manifest.get("scripted_expert_approval"), "approved scripted records/proofs differ")
    return result


def validate_prepared_activation_ipc(rows: Iterator[dict[str, Any]], request: dict[str, Any], response: dict[str, Any],
                                     before: dict[str, Any], prepared: dict[str, Any]) -> None:
    """Bind intent to its actual positive transport ID and matching reply."""
    pending, matched, status_before, status_after = None, 0, False, False
    for row in rows:
        value = row.get("value", {})
        if row.get("direction") == "response":
            actors = value.get("actors", [])
            status_before |= before in actors
            status_after |= prepared in actors
        if row.get("direction") == "request" and {key: item for key, item in value.items() if key != "id"} == request:
            require(pending is None, "duplicate prepared activation request")
            pending = integer(value.get("id"), "activation IPC transport ID", minimum=1)
        elif pending is not None:
            require(row.get("direction") == "response" and value == response and
                    type(value.get("id")) is int and value["id"] == pending,
                    "activation differs from actual IPC response/transport ID")
            pending = None
            matched += 1
    require(pending is None and matched == 1 and status_before and status_after,
            "prepared activation/status lacks unique actual IPC binding")


def validate_prepared_sequence_start(episode: ValidatedEpisode) -> dict[str, Any]:
    """Bind a cold recurrent scene to actual held preparation and activation.

    Reset/capture clocks and every preparation row remain in the raw episode.
    Only the explicitly proven prepared primary stream starts model memory.
    """
    manifest, path = episode.manifest, episode.path
    start = manifest.get("model_sequence_start")
    require(isinstance(start, dict) and start.get("schema_version") == 1 and
            start.get("kind") == "prepared_reaction_anchor", "unsupported model scene boundary")
    require(manifest.get("teacher_provenance", {}).get("teacher_id") == "observed-reactive-defence-v1",
            "prepared model scene lacks registered reactive defence teacher")
    require(start.get("entity_id") == manifest["initial"]["primary_entity_id"], "prepared scene is not the controlled primary")
    matches = [row for row in episode.observations.values() if row.get("raw_capture_seq") == start.get("raw_capture_seq")]
    require(len(matches) == 1, "prepared scene lacks a unique actual capture")
    wrapper, = matches
    anchor = wrapper["observation"]
    require(identity(wrapper) == identity(start) and anchor["t_game_ms"] == start.get("t_game_ms"),
            "prepared scene identity/time differs from capture")
    require(wrapper.get("raw_observation_source") == "periodic" and anchor.get("periodic") is True and
            anchor.get("event_only") is False and anchor["phase"] == "post_simulation_post_animation" and
            anchor.get("native_decision_eligible") is True and anchor.get("can_decide") is True and
            anchor.get("action_mask_valid") is True and anchor["action_mask"][9] is True,
            "prepared scene is not an actual eligible periodic block opportunity")
    require(not any(row["entity_id"] == start["entity_id"] and row["raw_capture_seq"] < start["raw_capture_seq"]
                    for row in episode.decisions.values()), "prepared model scene has earlier primary commands")
    sources = {item["path"]: item["sha256"] for item in manifest["source_files"]}
    require(all(sources.get(name) == sha256(path/name) for name in ("events.jsonl", "ipc.jsonl")),
            "prepared scene physical/IPC evidence is not source bound")

    def bound(key):
        name = start.get(key)
        require(isinstance(name, str) and name, "prepared scene missing "+key)
        file = (path/name).resolve()
        require(file.is_relative_to(path) and file.is_file() and sources.get(name) == sha256(file),
                "prepared scene source is not bound: "+key)
        return read_json(file)

    preparation, activation = bound("preparation_file"), bound("activation_file")
    # The ordinary RPC envelope and unified capture envelope independently
    # serialize the same snapshot. Permit only the already audited finite
    # IEEE-754 roundtrip; identities, fields, masks and clocks stay exact.
    from body_bc_report import json_digest, native_wire_relation
    wire_anchor = preparation.get("anchor")
    try:
        relation = native_wire_relation(anchor, wire_anchor, max_ulps=2)
    except ValueError as error:
        raise DatasetValidationError(str(error)) from error
    normalizer_name = manifest.get("preparation_normalizer_file")
    require(normalizer_name == "preparation-normalizer.json" and sources.get(normalizer_name) == preparation.get("normalizer_sha256"),
            "prepared snapshot normalization is not source bound")
    normalizer = read_json(path/normalizer_name)
    capture_features = pack_features(anchor, normalizer, sequence_dt_game_ms=0)
    wire_features = pack_features(wire_anchor, normalizer, sequence_dt_game_ms=0)
    require(len(capture_features) == len(wire_features) == 184, "prepared snapshot feature schema differs")
    capture_bytes = struct.pack("<"+"f"*184, *capture_features)
    wire_bytes = struct.pack("<"+"f"*184, *wire_features)
    require(capture_bytes == wire_bytes, "prepared wire/capture recurrent float32 features differ")
    binding = bound("anchor_binding_file")
    expected_binding = {"schema_version": 1, "kind": "prepared_anchor_wire_binding", "raw_capture_seq": wrapper["raw_capture_seq"],
        "wire_anchor_sha256": json_digest(wire_anchor), "capture_anchor_sha256": json_digest(anchor), "relation": relation,
        "feature_count": 184, "first_sequence_dt_game_ms": 0,
        "capture_float32_features_sha256": hashlib.sha256(capture_bytes).hexdigest(),
        "wire_float32_features_sha256": hashlib.sha256(wire_bytes).hexdigest(), "original_wire_and_capture_preserved": True}
    require(binding == expected_binding, "prepared wire/capture binding proof differs")
    require(preparation.get("anchor_sha256") == json_digest(wire_anchor) and
            preparation.get("reset_ready") == manifest["initial"] and preparation.get("prepared_game_ms") == anchor["t_game_ms"] and
            preparation.get("preparation_game_ms") == anchor["t_game_ms"]-manifest["initial"]["game_ms"] and
            preparation.get("first_sequence_dt_game_ms") == 0, "prepared scene source anchor/reset/clock differs")
    own, target = anchor.get("self", {}), anchor.get("target", {})
    require(own.get("hp") == own.get("hp_max") == target.get("hp") == target.get("hp_max") == 300 and
            own.get("attack_anim") is False and own.get("defence_active") is False and
            target.get("entity_id") == manifest["initial"]["opponent_entity_id"] and
            all(target.get(key) is True for key in ("present", "visible", "current_valid", "in_w_range", "melee_line_clear", "attack_anim", "prehit")),
            "prepared scene lacks an undamaged real visible incoming attack")
    before, prepared = preparation.get("primary_before", {}), preparation.get("primary_prepared", {})
    require(before.get("control_generation") == prepared.get("control_generation") == anchor["control_generation"] and
            before.get("purity_state", {}).get("rng_state") is not None and
            before["purity_state"]["rng_state"] == prepared.get("purity_state", {}).get("rng_state") and
            before["purity_state"].get("queue") == prepared.get("purity_state", {}).get("queue") == [0]*6,
            "prepared scene changed primary ownership, RNG or queue")
    armed = preparation.get("prepared_before_activation", {})
    require(armed.get("game_ms") == anchor["t_game_ms"] and
            all(armed.get(key) == manifest["initial"][key] for key in ("session_epoch", "world_epoch", "primary_entity_id", "opponent_entity_id")) and
            all(all(actor.get(key) == (300 if key == "hp" else 0) for key in ("hp", "received_damage", "outgoing_damage", "blocked_hits"))
                for actor in armed.get("actors", [])) and len(armed.get("actors", [])) == 2,
            "prepared scene setup changed or had a hit before activation")
    reaction = armed.get("reaction", {})
    require(reaction == preparation.get("native_reaction") and reaction.get("enabled") is True and reaction.get("phase") == "armed" and
            reaction.get("periodic") is True and reaction.get("observation_seq") == anchor["observation_seq"] and
            reaction.get("game_ms") == anchor["t_game_ms"] and reaction.get("sim_step") == anchor["sim_step"],
            "prepared scene lacks matching native armed proof")
    request, response = activation.get("request", {}), activation.get("response", {})
    active = response.get("arena", {})
    require(request.get("cmd") == "arena_reaction_activate" and request.get("provider") == "scripted" and request.get("mode") == "lockstep" and
            all(request.get(key) == anchor[key] for key in ("session_epoch", "world_epoch", "observation_seq")) and
            request.get("primary_entity_id") == anchor["entity_id"] and request.get("primary_control_generation") == anchor["control_generation"] and
            request.get("issued_game_ms") == anchor["t_game_ms"] and activation.get("prepared_start") == active and
            activation.get("initial_game_ms") == anchor["t_game_ms"] and activation.get("first_sequence_dt_game_ms") == 0 and
            activation.get("preparation_file") == start["preparation_file"] and
            activation.get("preparation_sha256") == sources[start["preparation_file"]] and
            active.get("game_ms") == anchor["t_game_ms"] and active.get("reaction", {}).get("phase") == "active" and
            active["reaction"].get("activation_game_ms") == anchor["t_game_ms"] and active["reaction"].get("activation_provider") == "scripted",
            "prepared scene activation changed the exact cold anchor")
    controls = [row["body_control_event"] for row in iter_jsonl(path/manifest["capture_file"]) if row.get("kind") == "control"]
    for name, phase in (("attacker_accepted", "accepted"), ("attacker_started", "started")):
        attacker = preparation.get(name)
        require(isinstance(attacker, dict) and attacker in controls and attacker.get("entity_id") == manifest["initial"]["opponent_entity_id"] and
                attacker.get("phase") == phase and attacker.get("action_id") == 6 and attacker.get("t_game_ms", math.inf) <= anchor["t_game_ms"],
                "prepared attack lacks its actual accepted/started lifecycle")
    actor_ids = set()
    for row in iter_jsonl(path/"events.jsonl"):
        if row.get("phase") == "arena_actor_mapping" and row.get("observation_entity_id") == anchor["entity_id"]:
            actor_ids.add(row.get("actor_id"))
        if manifest["initial"]["game_ms"] < row.get("t_game", -1) <= anchor["t_game_ms"]:
            require(row.get("phase") not in ("hit_attempt", "damage", "defence_result"), "prepared scene already has a hit")
            require(not (row.get("actor_id") in actor_ids and row.get("phase") in ("native_choice", "native_consume")),
                    "primary native chooser ran during preparation")
    require(len(actor_ids) == 1 and None not in actor_ids, "prepared scene lacks unique primary native mapping")
    validate_prepared_activation_ipc(iter_jsonl(path/"ipc.jsonl"), request, response, before, prepared)
    return start


def approve_scripted_records(episode: ValidatedEpisode, teacher_registry: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Recompute each approved teacher label from immutable causal engine evidence.

    Live scripted records stay non-expert. A declared code/config registry,
    pre-command certificate and completed physical effect are all necessary.
    Failed demonstrations remain diagnostics and never become BC labels.
    """
    manifest, path = episode.manifest, episode.path
    teacher_registry = original_teacher_registry(teacher_registry, manifest)
    registry_hash = registry_sha256(teacher_registry)
    provenance = manifest.get("teacher_provenance")
    require(isinstance(provenance, dict), "scripted demonstrations lack collection-time teacher provenance")
    teacher_id = provenance.get("teacher_id")
    entry = teacher_registry["teachers"].get(teacher_id)
    require(isinstance(entry, dict), "unknown scripted expert teacher")
    require(entry.get("module") == APPROVED_TEACHER_MODULES.get(teacher_id) and teacher_id in APPROVED_TEACHER_MODULES,
            "unapproved teacher verifier module")
    require(entry.get("controlled_role") == "primary", "unsupported scripted teacher role")
    require(manifest.get("scenario") in entry.get("allowed_scenarios", []), "teacher does not cover this scenario")
    require(manifest.get("config_sha256") == entry.get("config_sha256"), "teacher collection config SHA differs")
    sources = {item["path"]: item["sha256"] for item in manifest["source_files"]}

    def bound_file(key: str) -> Path:
        name = provenance.get(key)
        require(isinstance(name, str) and name, f"teacher provenance missing {key}")
        file = (path / name).resolve()
        require(file.is_relative_to(path) and file.is_file() and sources.get(name) == sha256(file), f"teacher {key} source SHA differs")
        return file

    code_file, config_file = bound_file("code_file"), bound_file("config_file")
    certificate_file = bound_file("certificates_file")
    require(sha256(code_file) == entry.get("code_sha256") and sha256(config_file) == entry.get("config_sha256"),
            "teacher code/config is not in declared registry")
    module = importlib.import_module(entry["module"])
    require(sha256(Path(module.__file__).resolve()) == entry["code_sha256"], "teacher verifier code differs from registered source")
    mechanics_path, ipc_path, arena_path = path / "events.jsonl", path / "ipc.jsonl", path / "arena-events.jsonl"
    require(sources.get("events.jsonl") == sha256(mechanics_path) and sources.get("ipc.jsonl") == sha256(ipc_path),
            "teacher physical/command evidence is not source bound")
    require(sources.get("arena-events.jsonl") == sha256(arena_path), "teacher setup evidence is not source bound")
    initial_verified = False
    for row in iter_jsonl(arena_path):
        if row.get("kind") == "reset_ready" and row.get("arena") == manifest["initial"]:
            initial_verified = True
    require(initial_verified,
            "teacher initial setup lacks native reset proof")
    # The declared verifier reads only mappings and combat outcomes. Parsing is
    # still exhaustive; response snapshots and RNG diagnostics are hash-bound
    # and checked by the raw validator, then released rather than held again.
    mechanics_phases = {"observation_actor_mapping", "arena_actor_mapping", "hit_attempt", "damage", "defence_result"}
    combat_teacher = teacher_id == "observed-melee-combat-v1"
    defence_teacher = teacher_id == "observed-reactive-defence-v1"
    safe_peer_teacher = teacher_id == "observed-safe-peer-v1"
    if defence_teacher:
        validate_prepared_sequence_start(episode)
    if combat_teacher or safe_peer_teacher:
        mechanics_phases.update(("physics_step", "visibility_diagnostic"))
    mechanics = [row for row in iter_jsonl(mechanics_path) if row.get("phase") in mechanics_phases]
    captured_config = read_json(config_file) if combat_teacher or defence_teacher or safe_peer_teacher else None
    if defence_teacher:
        preparation_source = captured_config.get("preparation_source", {})
        helper_name = manifest.get("preparation_helper_file")
        require(isinstance(helper_name, str) and isinstance(sources.get(helper_name), str) and
                len(sources[helper_name]) == 64 and isinstance(sources.get("preparation-config.json"), str) and
                sources.get(helper_name) == manifest.get("preparation_helper_sha256") ==
                preparation_source.get("helper_sha256") and sources.get("preparation-config.json") == preparation_source.get("config_sha256"),
                "reactive defence preparation helper/config differs from declared frozen source")
        require(sources.get("preparation-normalizer.json") == captured_config.get("reference", {}).get("normalizer_sha256"),
                "reactive defence snapshot normalization differs from declared frozen reference")
    if safe_peer_teacher:
        require(captured_config.get("train_seeds") == [181, 191, 193, 197, 199, 211] and
                not any(captured_config.get(key) for key in ("eval_seeds", "test_seeds", "ood_seeds")) and
                captured_config.get("allowed_actions") == [2, 5, 11] and manifest.get("seed") in captured_config["train_seeds"] and
                manifest.get("declared_split") == "train" and manifest.get("ood") is False and
                manifest.get("opponent_provenance") == "causal_scripted_moving_peer_nonexpert",
                "SafePeer teacher scope is not the declared train-only moving-peer curriculum")
        visibility_file = path / "visibility-integrity.json"
        require(sources.get("visibility-integrity.json") == sha256(visibility_file), "SafePeer visibility integrity is not source bound")
        visibility_integrity = read_json(visibility_file)
        require(visibility_integrity.get("enabled") is True and visibility_integrity.get("capture_failed") is False and
                type(visibility_integrity.get("dropped_total")) is int and visibility_integrity["dropped_total"] == 0 and
                type(visibility_integrity.get("records_total")) is int and visibility_integrity["records_total"] > 0,
                "SafePeer native visibility proof failed, dropped or absent")
    ipc = [row for row in iter_jsonl(ipc_path) if row.get("direction") == "request" and
           row.get("value", {}).get("cmd") == "body_action"]
    certificates = read_jsonl(certificate_file)
    certs = {}
    for index, certificate in enumerate(certificates, 1):
        require(certificate.get("certificate_seq") == index, "teacher certificate gap/duplicate")
        require(certificate.get("teacher_id") == teacher_id and certificate.get("source_provider") == entry.get("provider"),
                "teacher certificate provenance differs")
        key = (integer(certificate.get("entity_id"), "certificate entity", minimum=1),
               integer(certificate.get("control_generation"), "certificate generation"),
               integer(certificate.get("command_id"), "certificate command", minimum=1))
        require(key not in certs, "duplicate teacher command certificate")
        require(certificate.get("session_epoch") == manifest["session_epoch"] and certificate.get("world_epoch") == manifest["world_epoch"],
                "teacher certificate crosses episode epoch")
        certs[key] = certificate
    raw_by_sequence = {row["record_seq"]: row for row in iter_jsonl(path / manifest["capture_file"]) if
                       row.get("kind") == "control" and row.get("body_control_event", {}).get("phase") == "accepted"}
    executions = defaultdict(list)
    for record in episode.records:
        if record["kind"] == "execution":
            executions[decision_key(record)].append(record)
    approved = {}
    diagnostics = []
    primary = manifest["initial"].get("primary_entity_id")
    require(type(primary) is int, "teacher arena primary role missing")
    for key, decision in episode.decisions.items():
        if decision["source"] != "scripted" or key[0] != primary:
            continue
        certificate = certs.get((key[0], decision["control_generation"], decision.get("command_id")))
        require(certificate is not None, "registered teacher command lacks causal certificate")
        require(decision["action_id"] in entry.get("allowed_actions", []), "teacher action is not approved by registry")
        if combat_teacher and decision["action_id"] in (3, 4, 10, 11):
            require(captured_config.get("maneuver_rule_status") == "enabled_after_paired_motor_probe",
                    "combat maneuver rule lacks enabled paired motor proof declaration")
        anchor = episode.observations[(key[0], decision["observation_seq"])]
        before = anchor["observation"]
        require(certificate.get("observation_seq") == anchor["raw_observation_seq"] and
                certificate.get("action_id") == decision["action_id"] and certificate.get("t_game_ms") == before["t_game_ms"],
                "teacher certificate does not reference exact pre-command snapshot")
        require(anchor["raw_observation_source"] == "periodic" and before["phase"] == "post_simulation_post_animation" and
                before.get("periodic") is True and
                anchor["raw_capture_seq"] < decision["raw_capture_seq"], "teacher uses a post-command capture")
        accepted = raw_by_sequence[decision["raw_capture_seq"]].get("body_control_event", {})
        require(accepted.get("provider") == entry["provider"], "native control accepted another teacher provider")
        requests = [row for row in ipc if row.get("direction") == "request" and row.get("value", {}).get("cmd") == "body_action" and
                    row["value"].get("entity_id") == key[0] and row["value"].get("command_id") == decision["command_id"] and
                    row["value"].get("control_generation") == decision["control_generation"]]
        require(len(requests) == 1 and requests[0]["value"].get("provider") == entry["provider"], "teacher command has no unique raw IPC request")
        require(finite(certificate.get("wall_monotonic"), "certificate wall time") <=
                finite(requests[0].get("wall_monotonic"), "IPC command wall time"), "teacher certificate was produced after the command")
        history = []
        for prior_key, prior in episode.decisions.items():
            if prior_key[0] != key[0] or identity(prior) != identity(decision):
                continue
            ended = next((row for row in executions[prior_key] if row["phase"] == "completed"), None)
            if ended and ended["t_game_ms"] <= before["t_game_ms"] and ended["raw_capture_seq"] < anchor["raw_capture_seq"]:
                history.append({"action_id": prior["action_id"], "command_id": prior.get("command_id"),
                                "entity_id": key[0], "t_game_ms": ended["t_game_ms"], "phase": "completed"})
        history.sort(key=lambda row: (row["t_game_ms"], row["command_id"] or 0))
        precondition = module.verify_precondition(certificate, before, history)
        require(isinstance(precondition, dict) and type(precondition.get("passed")) is bool, "invalid teacher precondition result")
        require(precondition["passed"], f"teacher precondition failed: {precondition.get('reason')}")
        transition = episode.transitions[key]
        lifecycle = executions[key]
        after_row = episode.observations.get((key[0], transition.get("next_observation_seq")))
        if not (decision["accepted"] and any(row["phase"] == "started" for row in lifecycle) and
                any(row["phase"] == "completed" for row in lifecycle) and transition["execution_status"] == "completed" and
                after_row is not None and not manifest["final"]["infrastructure"] and identity(after_row) == identity(decision)):
            diagnostics.append({"decision_id": key[1], "action_id": decision["action_id"], "reason": "execution_not_completed"})
            continue
        evidence = {"mechanics": mechanics, "executions": lifecycle, "decision": decision,
                    "transition": transition, "initial": manifest["initial"], "final": manifest["final"]}
        if defence_teacher:
            require(decision["action_id"] == 9 and anchor["raw_capture_seq"] >= manifest["model_sequence_start"]["raw_capture_seq"],
                    "reactive defence label precedes its scene or is not block")
            evidence["model_sequence_start"] = manifest["model_sequence_start"]
        if combat_teacher or safe_peer_teacher:
            evidence["observations"] = [row for row in episode.observations.values() if row["entity_id"] == key[0]]
            evidence["decisions"] = [row for row in episode.decisions.values() if row["entity_id"] == key[0]]
        outcome = module.verify_outcome(decision["action_id"], before, after_row["observation"], evidence)
        require(isinstance(outcome, dict) and type(outcome.get("passed")) is bool, "invalid teacher outcome result")
        if outcome["passed"]:
            approved[key] = {"decision_id": key[1], "entity_id": key[0], "action_id": decision["action_id"],
                             "certificate_seq": certificate["certificate_seq"], "precondition": precondition, "outcome": outcome}
        else:
            diagnostics.append({"decision_id": key[1], "action_id": decision["action_id"], "reason": outcome.get("reason", "physical_effect_not_verified")})
    records = [dict(row) for row in episode.records]
    approved_observations = {(key[0], episode.decisions[key]["observation_seq"]): episode.decisions[key]["action_id"] for key in approved}
    for record in records:
        if record["kind"] == "observation" and (record["entity_id"], record["observation_seq"]) in approved_observations:
            record.update(decision_valid=True, bc_label=approved_observations[(record["entity_id"], record["observation_seq"])])
        elif record["kind"] == "decision" and decision_key(record) in approved:
            record.update(decision_valid=True, expert=True)
        elif record["kind"] == "transition" and decision_key(record) in approved:
            record["usable_for_bc"] = True
    approval = {"schema_version": 1, "teacher_id": teacher_id, "registry_sha256": registry_hash,
                "original_records_sha256": sha256(path / "records.jsonl"),
                "certificates_sha256": sha256(certificate_file), "approved": list(approved.values()), "diagnostics": diagnostics}
    return records, approval


def group_key(manifest: dict[str, Any]) -> str:
    """Roles, position swaps and repeated providers share this one key."""
    return json.dumps([manifest["group"]["setup_family"], manifest["group"]["seed"]], separators=(",", ":"))


def split_groups(episodes: Sequence[ValidatedEpisode], *, split_seed: int = 20261013,
                 existing_assignments: dict[str, str] | None = None,
                 group_overrides: dict[str, str] | None = None) -> dict[str, str]:
    groups: dict[str, bool] = {}
    for episode in episodes:
        key = group_key(episode.manifest)
        require(key not in groups or groups[key] == episode.manifest["ood"], "related group mixes OOD and in-distribution")
        groups[key] = episode.manifest["ood"]
    normal = [key for key, ood in groups.items() if not ood]
    normal.sort(key=lambda key: hashlib.sha256(f"{split_seed}:{key}".encode()).hexdigest())
    require(len(normal) >= 3, "at least three independent non-OOD groups required for train/eval/test")
    exact = [len(normal)*ratio for ratio in (.70, .15, .15)]
    counts = [math.floor(value) for value in exact]
    remainder_order = sorted(range(3), key=lambda index: (-(exact[index]-counts[index]), index))
    for index in remainder_order[:len(normal)-sum(counts)]:
        counts[index] += 1
    for index in range(3):
        if counts[index] == 0:
            donor = max(range(3), key=lambda candidate: counts[candidate])
            counts[donor] -= 1
            counts[index] = 1
    train_count, eval_count, test_count = counts
    require(train_count > 0, "split leaves no training group")
    assignment = {}
    for index, key in enumerate(normal):
        assignment[key] = "train" if index < train_count else "eval" if index < train_count+eval_count else "test"
    assignment.update({key: "ood" for key, ood in groups.items() if ood})
    inherited = existing_assignments or {}
    overrides = group_overrides or {}
    for key, split in {**inherited, **overrides}.items():
        require(isinstance(key, str) and split in ("train", "eval", "test", "ood"), "invalid fixed group assignment")
        require(key not in inherited or key not in overrides or inherited[key] == overrides[key],
                "group override changes an inherited split")
        if key in groups:
            require((split == "ood") == groups[key], "fixed assignment mixes OOD and in-distribution")
            assignment[key] = split
    require({"train", "eval", "test"} <= set(assignment.values()), "fixed assignments leave an empty train/eval/test split")
    return assignment


def periodic_model_view(episode: ValidatedEpisode, *, max_label_age_game_ms: int = 67) -> ValidatedEpisode:
    """Project labels causally; teacher-only captures never update model memory."""
    require(type(max_label_age_game_ms) is int and 0 <= max_label_age_game_ms <= 67, "invalid model alignment age")
    periodic = [record for record in episode.records if record["kind"] == "observation" and
                record["raw_observation_source"] == "periodic" and
                record["observation"]["phase"] == "post_simulation_post_animation" and
                record["observation"].get("periodic") is True]
    scene_start = episode.manifest.get("model_sequence_start")
    if scene_start is not None:
        periodic = [record for record in periodic if record["entity_id"] == scene_start["entity_id"] and
                    record["raw_capture_seq"] >= scene_start["raw_capture_seq"]]
        require(periodic and periodic[0]["raw_capture_seq"] == scene_start["raw_capture_seq"],
                "prepared scene projection lost its exact first anchor")
    observations = {}
    last_times = {}
    for original in periodic:
        record = dict(original)
        record.update(decision_valid=False, bc_label=None)
        record["model_view_version"] = 1
        now = record["t_game_ms"]
        initial_time = scene_start["t_game_ms"] if scene_start is not None else episode.manifest["initial"]["game_ms"]
        record["sequence_dt_game_ms"] = now-last_times.get(record["entity_id"], initial_time)
        last_times[record["entity_id"]] = now
        observations[(record["entity_id"], record["observation_seq"])] = record
    decisions = {key: dict(value, recorded_expert=value["expert"], expert=False, decision_valid=False)
                 for key, value in episode.decisions.items()}
    transitions = {key: dict(value, usable_for_bc=False) for key, value in episode.transitions.items()}
    candidates = defaultdict(list)
    dropped = Counter()
    details = []
    for key, original in episode.decisions.items():
        if not original["expert"]:
            continue
        usable = episode.transitions[key]["usable_for_bc"]
        exact = episode.observations[(key[0], original["observation_seq"])]
        if original["source"] == "scripted":
            anchor = observations.get((key[0], original["observation_seq"]))
        else:
            prior = [row for row in observations.values() if identity(row) == identity(original) and
                     row["raw_capture_seq"] < exact["raw_capture_seq"] and row["t_game_ms"] <= original["t_game_ms"]]
            anchor = max(prior, key=lambda row: row["raw_capture_seq"], default=None)
        reason = None
        age = None if anchor is None else original["t_game_ms"]-anchor["t_game_ms"]
        if anchor is None:
            reason = "no_causal_periodic_anchor"
        elif age < 0 or age > max_label_age_game_ms:
            reason = "periodic_anchor_too_old"
        elif anchor["observation"]["native_decision_eligible"] is not True:
            reason = "periodic_anchor_physically_locked"
        elif anchor["observation"]["availability_mask"][original["action_id"]] is not True:
            reason = "action_unavailable_at_periodic_anchor"
        if reason:
            if usable:
                dropped[reason] += 1
                details.append({"entity_id": key[0], "decision_id": key[1], "action_id": original["action_id"],
                                "reason": reason, "age_game_ms": age})
        else:
            candidates[(anchor["entity_id"], anchor["observation_seq"])].append((key, original, age, usable))
    aligned = Counter()
    for anchor_key, choices in candidates.items():
        if len(choices) != 1:
            dropped["multiple_decisions_on_periodic_anchor"] += sum(usable for _, _, _, usable in choices)
            details.extend({"entity_id": key[0], "decision_id": key[1], "action_id": original["action_id"],
                            "reason": "multiple_decisions_on_periodic_anchor", "age_game_ms": age}
                           for key, original, age, usable in choices if usable)
            continue
        key, original, age, usable = choices[0]
        if not usable:
            continue
        anchor = observations[anchor_key]
        decisions[key].update(expert=True, decision_valid=True, observation_seq=anchor_key[1],
                              teacher_observation_seq=original["observation_seq"],
                              alignment_age_game_ms=age, action_mask=anchor["observation"]["availability_mask"])
        transitions[key].update(usable_for_bc=True, observation_seq=anchor_key[1])
        anchor.update(decision_valid=True, bc_label=original["action_id"], model_decision_id=original["decision_id"])
        aligned[original["action_id"]] += 1
    summary = dict(episode.summary)
    summary["model_alignment"] = {"periodic_observations": len(observations),
                                  "raw_usable_labels": sum(row["usable_for_bc"] for row in episode.transitions.values()),
                                  "aligned_labels": sum(aligned.values()),
                                  "aligned_by_action": {ACTIONS[action]: aligned[action] for action in range(1, 12)},
                                  "dropped_by_reason": dict(dropped), "dropped_labels": details}
    return ValidatedEpisode(episode.path, episode.manifest, list(observations.values()), observations,
                            decisions, transitions, summary)


def _coverage(episodes: Sequence[ValidatedEpisode], *, min_labels: int, included_actions: Sequence[int]) -> dict[str, Any]:
    counts, usable = Counter(), Counter()
    conditions: dict[str, Counter] = defaultdict(Counter)
    outcomes: dict[int, Counter] = defaultdict(Counter)
    execution_outcomes: dict[int, Counter] = defaultdict(Counter)
    sources: dict[str, Counter] = defaultdict(Counter)
    condition_observations = Counter()
    for episode in episodes:
        geometry_proof = episode.manifest.get("initial", {}).get("setup", {}).get("geometry_proof") or {}
        proven_wall = (geometry_proof.get("kind") == "existing_static_wall" and all(geometry_proof.get(flag) is True for flag in
                       ("wall_ray_hit", "capsule_move_blocked", "capsule_block_is_land", "initial_capsules_free",
                        "safe_outward_move", "grounded_placements")) and episode.manifest.get("scripted_expert_approval") is not None)

        def near_proven_wall(observation):
            return proven_wall and any(ray.get("hit") is True and type(ray.get("distance_cm")) in (int, float) and
                                       ray["distance_cm"] <= 300 for ray in observation.get("local_obstacles", {}).get("rays", []))

        for row in episode.observations.values():
            target = row["observation"].get("target", {})
            if target.get("current_valid") is False:
                condition_observations["target_visibility_lost"] += 1
            if target.get("current_valid") is True and target.get("in_w_range") is False:
                condition_observations["bad_distance"] += 1
            # A random hit in an obstacle ray does not establish a wall task.
            # Only a declared independently verified wall scenario or an
            # actual blocked line to an otherwise attackable target counts.
            if (near_proven_wall(row["observation"]) or episode.manifest.get("verified_conditions", {}).get("wall") is True or
                    (target.get("current_valid") is True and target.get("in_w_range") is True and
                     target.get("focus_5") is True and target.get("melee_line_clear") is False)):
                condition_observations["wall"] += 1
        for key, decision in episode.decisions.items():
            if not decision["expert"]:
                continue
            action = decision["action_id"]
            counts[action] += 1
            transition = episode.transitions[key]
            usable[action] += bool(transition.get("usable_for_bc"))
            if transition.get("usable_for_bc"):
                sources[decision["source"]][action] += 1
            execution_outcomes[action][transition["execution_status"]] += 1
            outcome = "terminated" if episode.manifest["final"]["terminated"] else "truncated" if episode.manifest["final"]["truncated"] else "infrastructure"
            outcomes[action][outcome] += 1
            observation = episode.observations[(key[0], decision["observation_seq"])]["observation"]
            target = observation.get("target", {})
            state_conditions = set()
            if target.get("current_valid") is False:
                state_conditions.add("target_visibility_lost")
            if target.get("in_w_range") is False:
                state_conditions.add("bad_distance")
            if ((near_proven_wall(observation) and action in (3, 4, 5) and transition.get("usable_for_bc")) or
                    episode.manifest.get("verified_conditions", {}).get("wall") is True or
                    (target.get("current_valid") is True and target.get("in_w_range") is True and
                     target.get("focus_5") is True and target.get("melee_line_clear") is False)):
                state_conditions.add("wall")
            for condition in state_conditions:
                conditions[condition][action] += 1
    missing = [{"action_id": action, "action": ACTIONS[action], "usable_expert_labels": usable[action],
                "required": min_labels, "missing": max(0, min_labels-usable[action])}
               for action in included_actions if usable[action] < min_labels]
    missing_states = [condition for condition in REQUIRED_CONDITIONS if not condition_observations[condition]]
    return {"minimum_true_expert_labels_per_included_class": min_labels,
            "included_actions": list(included_actions), "expert_labels_by_action": {ACTIONS[i]: counts[i] for i in range(1, 12)},
            "usable_labels_by_action": {ACTIONS[i]: usable[i] for i in range(1, 12)},
            "episode_outcomes_by_action": {ACTIONS[i]: dict(outcomes[i]) for i in range(1, 12)},
            "execution_outcomes_by_action": {ACTIONS[i]: dict(execution_outcomes[i]) for i in range(1, 12)},
            "usable_labels_by_source": {source: {ACTIONS[i]: counts[i] for i in range(1, 12)} for source, counts in sources.items()},
            "condition_observations": {name: condition_observations[name] for name in REQUIRED_CONDITIONS},
            "condition_labels": {name: {ACTIONS[i]: count for i, count in counter.items()} for name, counter in sorted(conditions.items())},
            "insufficient_classes": missing, "missing_conditions": missing_states,
            "diagnostic_bc_coverage_ready": not missing and not missing_states,
            "limitations": ["Episode outcomes are diagnostics, not per-action success labels.",
                            "Coverage does not establish model quality or dataset sufficiency for release."]}


class _TrainStatisticsAccumulator:
    """Identical Welford order with only feature-sized state between episodes."""
    def __init__(self, feature_version: str):
        self.feature_version = feature_version
        self.count = 0
        self.means: dict[str, float] = {}
        self.m2: dict[str, float] = {}
        self.actions = Counter()
        self.ids = []

    def add(self, episode: ValidatedEpisode) -> None:
        self.ids.append(episode.manifest["episode_id"])
        for record in episode.observations.values():
            values = record_features(record)
            if self.means:
                require(values.keys() == self.means.keys(), "actor feature schema changed within dataset")
            self.count += 1
            for key, value in values.items():
                delta = value-self.means.get(key, 0.0)
                self.means[key] = self.means.get(key, 0.0)+delta/self.count
                self.m2[key] = self.m2.get(key, 0.0)+delta*(value-self.means[key])
        for key, decision in episode.decisions.items():
            if episode.transitions[key].get("usable_for_bc"):
                self.actions[decision["action_id"]] += 1

    def finish(self) -> dict[str, Any]:
        count, means, m2, actions = self.count, self.means, self.m2, self.actions
        require(count > 0, "train split has no observations")
        nonzero = sum(bool(actions[i]) for i in range(1, 12))
        total = sum(actions.values())
        weights = [0.0] + [total/(nonzero*actions[i]) if actions[i] else 0.0 for i in range(1, 12)]
        variance = {key: max(0.0, m2[key]/count) for key in means}
        constant_features = [key for key in means if math.sqrt(variance[key]) < 1e-6]
        return {"feature_version": self.feature_version, "source_split": "train", "episode_ids": self.ids,
                "observation_count": count, "feature_names": list(means), "mean": means,
                "variance": variance, "constant_features": constant_features,
                "std": {key: 1.0 if key in constant_features else math.sqrt(variance[key]) for key in means},
                "std_rule": "Train population standard deviation; values below 1e-6 use unit scale to keep unseen constant/binary features bounded.",
                "class_counts": [actions[i] for i in range(12)], "class_weights": weights,
                "missing_training_classes": [ACTIONS[i] for i in range(1, 12) if actions[i] == 0]}


def train_statistics(episodes: Sequence[ValidatedEpisode], assignment: dict[str, str], *,
                     feature_version: str = FEATURE_VERSION) -> dict[str, Any]:
    accumulator = _TrainStatisticsAccumulator(feature_version)
    for episode in episodes:
        if assignment[group_key(episode.manifest)] == "train":
            accumulator.add(episode)
    return accumulator.finish()


def _merge_coverage(target: dict[str, Any], incoming: dict[str, Any], *, min_labels: int) -> None:
    """Add only integer sufficient statistics; preserve the public report shape."""
    flat = ("expert_labels_by_action", "usable_labels_by_action", "condition_observations")
    nested = ("episode_outcomes_by_action", "execution_outcomes_by_action", "usable_labels_by_source", "condition_labels")
    for name in flat:
        for key, count in incoming[name].items():
            target[name][key] = target[name].get(key, 0)+count
    for name in nested:
        for outer, counts in incoming[name].items():
            accumulator = target[name].setdefault(outer, {})
            for key, count in counts.items():
                accumulator[key] = accumulator.get(key, 0)+count
    target["insufficient_classes"] = [{"action_id": action, "action": ACTIONS[action],
        "usable_expert_labels": target["usable_labels_by_action"][ACTIONS[action]], "required": min_labels,
        "missing": max(0, min_labels-target["usable_labels_by_action"][ACTIONS[action]])}
        for action in target["included_actions"] if target["usable_labels_by_action"][ACTIONS[action]] < min_labels]
    target["missing_conditions"] = [name for name in REQUIRED_CONDITIONS if not target["condition_observations"][name]]
    target["diagnostic_bc_coverage_ready"] = not target["insufficient_classes"] and not target["missing_conditions"]
    target["condition_labels"] = dict(sorted(target["condition_labels"].items()))


def build_dataset(episode_paths: Sequence[Path | str], output: Path | str, *, split_seed: int = 20261013,
                  min_labels: int = 20, included_actions: Sequence[int] = tuple(range(1, 12)),
                  sampling_view: str = "capture-v1", max_label_age_game_ms: int = 67,
                  existing_assignments: dict[str, str] | Path | str | None = None,
                  group_overrides: dict[str, str] | None = None,
                  teacher_registry: dict[str, Any] | None = None) -> dict[str, Any]:
    require(min_labels >= 20, "diagnostic coverage threshold cannot be below 20")
    require(bool(included_actions) and len(included_actions) == len(set(included_actions)) and
            all(type(action) is int and 1 <= action < 12 for action in included_actions), "invalid included action set")
    require(sampling_view in ("capture-v1", "periodic-v1"), "unsupported model sampling view")
    # Splits need only tiny manifests. Retaining every full validated journal
    # here multiplied corpus RAM and prevented training beside the engine.
    headers = [ValidatedEpisode(Path(path).resolve(), read_json(manifest_path(Path(path).resolve())), [], {}, {}, {}, {})
               for path in episode_paths]
    require(bool(headers), "no episodes")
    ids = [str(episode.manifest["episode_id"]) for episode in headers]
    require(len(ids) == len(set(ids)), "duplicate episode ID across dataset")
    epochs = [(episode.manifest["session_epoch"], episode.manifest["world_epoch"]) for episode in headers]
    require(len(epochs) == len(set(epochs)), "one engine episode epoch included more than once")
    action_versions = {episode.manifest.get("action_version") for episode in headers}
    feature_versions = {episode.manifest.get("feature_version") for episode in headers}
    require(len(action_versions) == len(feature_versions) == 1 and None not in action_versions | feature_versions,
            "mixed or unspecified action/feature versions")
    require(action_versions == feature_versions == {1}, "unsupported recorder action/feature version")
    inherited_source = None
    if isinstance(existing_assignments, (str, Path)):
        inherited_path = Path(existing_assignments).resolve()
        inherited = read_json(inherited_path)
        require(inherited.get("validated") is True and inherited.get("schema_version") == 1, "unvalidated inherited dataset")
        existing_assignments = inherited["groups"]
        inherited_source = {"path": str(inherited_path), "sha256": sha256(inherited_path)}
    assignment = split_groups(headers, split_seed=split_seed, existing_assignments=existing_assignments,
                              group_overrides=group_overrides)
    feature_version = PERIODIC_FEATURE_VERSION if sampling_view == "periodic-v1" else FEATURE_VERSION
    statistics = _TrainStatisticsAccumulator(feature_version)
    coverage = {split: _coverage([], min_labels=min_labels, included_actions=included_actions)
                for split in ("train", "eval", "test", "ood")}
    index = []
    for header in headers:
        episode = validate_episode(header.path, teacher_registry=teacher_registry)
        require(episode.manifest == header.manifest, "episode manifest changed during dataset build")
        if sampling_view == "periodic-v1":
            episode = periodic_model_view(episode, max_label_age_game_ms=max_label_age_game_ms)
        split = assignment[group_key(episode.manifest)]
        index.append({"episode_id": episode.manifest["episode_id"], "path": str(episode.path),
                      "manifest_file": manifest_path(episode.path).name,
                      "manifest_sha256": sha256(manifest_path(episode.path)),
                      "records_file": records_path(episode.path, episode.manifest).name,
                      "records_sha256": sha256(records_path(episode.path, episode.manifest)),
                      "group": group_key(episode.manifest), "split": split,
                      "summary": episode.summary})
        if split == "train":
            statistics.add(episode)
        _merge_coverage(coverage[split], _coverage([episode], min_labels=min_labels, included_actions=included_actions),
                        min_labels=min_labels)
        del episode
    normalization = statistics.finish()
    report = {"schema_version": SCHEMA_VERSION, "validated": True, "episode_count": len(headers),
              "split_seed": split_seed, "requested_split_ratios": {"train": .70, "eval": .15, "test": .15},
              "split_unit": "whole setup_family + seed groups; both actors and all related repeats stay together",
              "actions": list(ACTIONS), "feature_version": feature_version,
              "recorder_feature_version": next(iter(feature_versions)), "action_version": next(iter(action_versions)),
              "groups": assignment, "episodes": index, "coverage": coverage,
              "diagnostic_bc_coverage_ready": coverage["train"]["diagnostic_bc_coverage_ready"],
              "normalization": "normalization.json", "normalization_sha256": None,
              "limitations": ["No learner, optimizer or on-policy PPO batch is produced.",
                              "Dataset replay verifies recorded links; it does not reproduce the game world.",
                              "Coverage reports missing labels and conditions; validation alone does not authorize training."]}
    if sampling_view == "periodic-v1":
        report["model_sampling"] = {"kind": "periodic_only", "version": 1,
                                    "max_label_age_game_ms": max_label_age_game_ms,
                                    "ambiguous": "drop_all", "recurrent_updates": "periodic_only"}
        report["model_alignment"] = {str(entry["episode_id"]): entry["summary"]["model_alignment"] for entry in index}
        starts = {str(header.manifest["episode_id"]): header.manifest["model_sequence_start"]
                  for header in headers if header.manifest.get("model_sequence_start") is not None}
        if starts:
            report["model_sequence_starts"] = starts
    if existing_assignments is not None:
        report["inherited_assignments"] = {"source": inherited_source, "groups": existing_assignments,
                                           "overrides": group_overrides or {}}
    if teacher_registry is not None:
        report["teacher_registry"] = teacher_registry
        report["teacher_registry_sha256"] = registry_sha256(teacher_registry)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    normalization_path = output / "normalization.json"
    normalization_path.write_text(json.dumps(normalization, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    report["normalization_sha256"] = sha256(normalization_path)
    (output / "dataset.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (output / "coverage.md").write_text(coverage_markdown(report), encoding="utf-8")
    return report


def coverage_markdown(report: dict[str, Any]) -> str:
    lines = ["# Body trajectory dataset", "", f"Validated episodes: {report['episode_count']}.", "",
             "Splits keep setup family, seed, both roles and related repeats together. OOD is separate.", "",
             "| Split | Groups | Episodes | Usable expert labels | Coverage gate |", "|---|---:|---:|---:|---|"]
    for split in ("train", "eval", "test", "ood"):
        coverage = report["coverage"][split]
        lines.append(f"| {split} | {sum(value == split for value in report['groups'].values())} | "
                     f"{sum(row['split'] == split for row in report['episodes'])} | "
                     f"{sum(coverage['usable_labels_by_action'].values())} | "
                     f"{'ready' if coverage['diagnostic_bc_coverage_ready'] else 'insufficient'} |")
    threshold = report["coverage"]["train"]["minimum_true_expert_labels_per_included_class"]
    teacher_description = "native or independently proven scripted" if report.get("teacher_registry") else "native"
    lines += ["", f"Train coverage requires {threshold} actual usable {teacher_description} expert labels for every included action.", "",
              "| Action | Train labels | Missing |", "|---|---:|---:|"]
    coverage = report["coverage"]["train"]
    for action in coverage["included_actions"]:
        count = coverage["usable_labels_by_action"][ACTIONS[action]]
        lines.append(f"| {ACTIONS[action]} | {count} | {max(0, threshold-count)} |")
    lines += ["", "Missing train conditions: " + (", ".join(coverage["missing_conditions"]) or "none") + ".", "",
              "Normalization and class weights use train observations and usable expert labels only.", "",
              "Episode outcomes do not establish per-action success. No model quality or release claim is made.", ""]
    return "\n".join(lines)


class OrderedSequenceLoader:
    """Deterministic actor sequences with explicit reset and burn-in semantics.

    Chunks never cross episode, actor or control-generation boundaries. The
    burn-in prefix updates memory while loss_mask stays false. Hidden state is
    reset at each yielded chunk; the prefix reconstructs only recorded context.
    """
    def __init__(self, dataset: Path | str, *, split: str = "train", chunk_length: int = 32, burn_in: int = 8):
        require(split in ("train", "eval", "test", "ood"), "unknown dataset split")
        require(type(chunk_length) is int and chunk_length > 0 and type(burn_in) is int and burn_in >= 0,
                "invalid chunk/burn-in length")
        self.path = Path(dataset).resolve()
        self.report = read_json(self.path / "dataset.json")
        require(self.report.get("schema_version") == SCHEMA_VERSION and self.report.get("validated") is True,
                "unvalidated or unsupported dataset")
        self.normalizer = read_json(self.path / "normalization.json")
        require(sha256(self.path / "normalization.json") == self.report.get("normalization_sha256"), "normalizer SHA mismatch")
        require(self.normalizer.get("source_split") == "train" and self.normalizer.get("feature_version") == self.report.get("feature_version")
                and self.normalizer.get("feature_version") in (FEATURE_VERSION, PERIODIC_FEATURE_VERSION),
                "invalid normalizer provenance")
        require(self.report.get("action_version") == self.report.get("recorder_feature_version") == 1,
                "unsupported dataset action/feature version")
        expected_names = canonical_feature_names(self.normalizer["feature_version"])
        names = self.normalizer.get("feature_names")
        require(names == expected_names and isinstance(self.normalizer.get("mean"), dict) and
                isinstance(self.normalizer.get("std"), dict) and set(names) == set(self.normalizer["mean"]) == set(self.normalizer["std"]),
                "normalizer feature schema mismatch")
        for name in names:
            finite(self.normalizer["mean"][name], f"normalizer mean {name}")
            require(finite(self.normalizer["std"][name], f"normalizer std {name}") > 0, "normalizer std must be positive")
        class_counts, weights = self.normalizer.get("class_counts"), self.normalizer.get("class_weights")
        require(isinstance(class_counts, list) and len(class_counts) == 12 and
                all(type(value) is int and value >= 0 for value in class_counts), "invalid normalizer class counts")
        require(isinstance(weights, list) and len(weights) == 12 and weights[0] == 0 and
                all(finite(value, "class weight") >= 0 for value in weights), "invalid normalizer class weights")
        require(set(self.normalizer.get("episode_ids", [])) ==
                {row["episode_id"] for row in self.report["episodes"] if row["split"] == "train"},
                "normalizer includes non-train episodes")
        self.split, self.chunk_length, self.burn_in = split, chunk_length, burn_in
        self.teacher_registry = self.report.get("teacher_registry")
        if self.teacher_registry is not None:
            require(registry_sha256(self.teacher_registry) == self.report.get("teacher_registry_sha256"), "teacher registry SHA mismatch")

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for entry in self.report["episodes"]:
            if entry["split"] != self.split:
                continue
            # The child generator frame (including raw/projected rows) is
            # released before validation begins for the next episode.
            yield from self._iter_episode(entry)

    def _iter_episode(self, entry: dict[str, Any]) -> Iterator[dict[str, Any]]:
        path = Path(entry["path"])
        require(sha256(path / entry["manifest_file"]) == entry["manifest_sha256"] and
                sha256(path / entry.get("records_file", "records.jsonl")) == entry["records_sha256"], "dataset episode changed after validation")
        episode = validate_episode(path, teacher_registry=self.teacher_registry)
        if self.report.get("model_sampling", {}).get("kind") == "periodic_only":
            episode = periodic_model_view(episode, max_label_age_game_ms=self.report["model_sampling"]["max_label_age_game_ms"])
        require(group_key(episode.manifest) == entry["group"] and
                self.report["groups"].get(entry["group"]) == self.split, "dataset split/group mismatch")
        streams: list[tuple[tuple[int, ...], list[dict[str, Any]]]] = []
        active: dict[int, tuple[tuple[int, ...], list[dict[str, Any]]]] = {}
        for record in episode.records:
            if record["kind"] == "observation":
                stream_id = identity(record)
                current = active.get(record["entity_id"])
                if current is None or current[0] != stream_id:
                    current = (stream_id, [])
                    streams.append(current)
                    active[record["entity_id"]] = current
                current[1].append(record)
        decision_by_observation = {(row["entity_id"], row["observation_seq"]): row for row in episode.decisions.values()}
        seen_actors = set()
        for stream_id, rows in streams:
            first_actor_stream = stream_id[-1] not in seen_actors
            seen_actors.add(stream_id[-1])
            for start in range(0, len(rows), self.chunk_length):
                prefix_start = max(0, start-self.burn_in)
                selected = rows[prefix_start:start+self.chunk_length]
                prefix_length = start-prefix_start
                items = []
                for index, record in enumerate(selected):
                    observation = record["observation"]
                    label = record["bc_label"]
                    decision = decision_by_observation.get((record["entity_id"], record["observation_seq"]))
                    usable = bool(decision and episode.transitions[decision_key(decision)].get("usable_for_bc"))
                    features = pack_features(observation, self.normalizer, sequence_dt_game_ms=record["sequence_dt_game_ms"])
                    mask = decision.get("action_mask") if decision and usable else observation.get("action_mask")
                    if mask is None:
                        mask = observation["availability_mask"]
                    raw_mask = list(observation["availability_mask"])
                    if self.normalizer["feature_version"] == PERIODIC_FEATURE_VERSION:
                        mask = policy_action_mask(observation)
                        require(label is None or not usable or (raw_mask[label] and mask[label]), "model label outside raw/derived mask")
                    items.append({"observation_seq": record["observation_seq"], "t_game_ms": observation["t_game_ms"],
                                  "source_observation_seq": record.get("raw_observation_seq"),
                                  "raw_capture_seq": record.get("raw_capture_seq"),
                                  "decision_id": decision["decision_id"] if decision and usable else None,
                                  "decision_source": decision["source"] if decision and usable else None,
                                  "observation_source": record.get("raw_observation_source"),
                                  "periodic": observation.get("periodic"),
                                  "phase": observation["phase"], "dt_game_ms": record["sequence_dt_game_ms"],
                                  "raw_dt_game_ms": observation["dt_game_ms"],
                                  "features": features, "label": label, "action_mask": mask,
                                  "raw_recorded_action_mask": raw_mask,
                                  "loss_mask": index >= prefix_length and usable and label is not None,
                                  "burn_in": index < prefix_length, "reset": index == 0})
                yield {"episode_id": episode.manifest["episode_id"], "identity": list(stream_id), "split": self.split,
                       "reset": True, "burn_in_length": prefix_length, "observations": items,
                       "reset_reason": ("fragment_prefix" if start else "episode_actor_start" if first_actor_stream
                                        else "control_generation_changed"),
                       "end_of_stream": start+self.chunk_length >= len(rows),
                       "stream_boundary": ("episode_end" if rows[-1]["t_game_ms"] == episode.manifest["final"]["t_game_ms"]
                                           else "control_generation_changed") if start+self.chunk_length >= len(rows) else None,
                       "episode_outcome": {key: episode.manifest["final"][key] for key in
                                           ("terminated", "truncated", "infrastructure", "reason")},
                       "episode_end_at_end": start+self.chunk_length >= len(rows) and
                                             rows[-1]["t_game_ms"] == episode.manifest["final"]["t_game_ms"]}

class FullEpisodeLoader(OrderedSequenceLoader):
    """One contiguous real actor/generation stream, with no artificial resets.

    Every periodic tick participates in memory, including locked and terminal
    rows. Loss masks still select only causally aligned usable expert labels.
    Only actual episode/entity/control-generation boundaries reset state.
    """
    def __init__(self, dataset: Path | str, *, split: str = "eval"):
        super().__init__(dataset, split=split, chunk_length=(1 << 63)-1, burn_in=0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episodes", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--split-seed", type=int, default=20261013)
    parser.add_argument("--min-labels", type=int, default=20)
    parser.add_argument("--sampling-view", choices=("capture-v1", "periodic-v1"), default="capture-v1")
    parser.add_argument("--existing-assignments", type=Path)
    parser.add_argument("--teacher-registry", type=Path)
    args = parser.parse_args(argv)
    registry = read_json(args.teacher_registry) if args.teacher_registry else None
    report = build_dataset(args.episodes, args.output, split_seed=args.split_seed, min_labels=args.min_labels,
                           sampling_view=args.sampling_view, existing_assignments=args.existing_assignments,
                           teacher_registry=registry)
    print(json.dumps({"validated": True, "episodes": report["episode_count"],
                      "diagnostic_bc_coverage_ready": report["diagnostic_bc_coverage_ready"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
