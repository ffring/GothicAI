#!/usr/bin/env python3
"""Training-speed feature packing, float-for-float equal to body_trajectory_dataset.pack_features.

The reference packer (frozen in the gate bindings) builds the actor view three times per call:
inside validate_observation, for canonical_feature_names and for the features themselves, and it
re-checks every normalizer value each time. This packer applies the same validation rules, builds
the view once in canonical order and checks a normalizer's vectors once per normalizer object.
Every value goes through the same float operations, so the output lists are equal bit for bit.

verify  packs every periodic row of runner observation journals with both packers and compares.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import struct
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import body_trajectory_dataset as reference  # noqa: E402
from body_trajectory_dataset import (ACTIONS, FEATURE_VERSION, OBSERVATION_PHASES, PERIODIC_FEATURE_VERSION,  # noqa: E402
                                     DatasetValidationError, integer, require)

SELF_KEYS = ("hp_fraction", "weapon_state", "weapon_length_cm", "skill", "skill_talent", "skill_valid",
             "dead", "unconscious", "down", "in_air", "swim", "dive", "attack_anim", "prehit",
             "defence_active", "jumpback_active", "def_window", "par_window", "interruptible", "velocity_valid")
TARGET_KEYS = ("present", "visible", "current_valid", "yaw_error_rad", "fight_distance_cm", "attack_range_cm",
               "g_range_cm", "closeup_range_cm", "in_w_range", "in_g_range", "in_closeup", "focus_30",
               "focus_5", "melee_line_clear", "hp_fraction", "weapon_state", "attack_anim", "prehit",
               "defence_active", "jumpback_active", "down", "last_known_age_ms")
GATE_KEYS = ("fai_wait_remaining_ms", "ani_wait_remaining_ms", "ai_wait_remaining_ms", "output_wait_remaining_ms")
HIDDEN_TARGET_KEYS = ("position_world_cm", "relative_position_cm", "linear_velocity_cm_s", "relative_velocity_cm_s",
                      "yaw_error_rad", "fight_distance_cm", "attack_range_cm", "g_range_cm", "closeup_range_cm",
                      "in_w_range", "in_g_range", "in_closeup", "focus_30", "focus_5", "melee_line_clear",
                      "hp", "hp_max", "hp_fraction", "weapon_state", "body_state_raw", "attack_anim", "prehit",
                      "defence_active", "jumpback_active", "down")
_isfinite = math.isfinite


def _add(out: list, value: Any, name: str) -> None:
    # reference actor_features.add: validity bit first, then the value
    if value is None:
        out.append(0.0)
        out.append(0.0)
    elif type(value) is bool:
        out.append(1.0)
        out.append(float(value))
    elif (type(value) is int or type(value) is float) and _isfinite(value):
        out.append(1.0)
        out.append(float(value))
    else:
        raise DatasetValidationError(f"nonfinite/nonnumeric {name}")


def _actor_values(observation: dict[str, Any], dt: Any, *, periodic_goal: bool) -> list[float]:
    out: list[float] = []
    _add(out, dt, "dt_game_ms")
    own = observation.get("self", {})
    target = observation.get("target", {})
    require(isinstance(own, dict) and isinstance(target, dict), "self/target must be objects")
    for key in SELF_KEYS:
        _add(out, own.get(key), "self."+key)
    for key in TARGET_KEYS:
        _add(out, target.get(key), "target."+key)
    for prefix, section, key in (("self", own, "linear_velocity_cm_s"), ("target", target, "relative_position_cm"),
                                 ("target", target, "relative_velocity_cm_s")):
        vector = section.get(key)
        require(vector is None or (isinstance(vector, list) and len(vector) == 3), f"invalid vector {prefix}.{key}")
        for axis in range(3):
            _add(out, None if vector is None else vector[axis], f"{prefix}.{key}.{axis}")
    gates = own.get("gates", {})
    require(isinstance(gates, dict), "self.gates must be object")
    for key in GATE_KEYS:
        _add(out, gates.get(key), "self.gates."+key)
    _add(out, observation.get("native_decision_eligible"), "native_decision_eligible")
    goal = observation.get("goal", {})
    if periodic_goal:
        # reference periodic_actor_features copies the goal into a dict before mapping its aliases
        goal = dict(goal)
        kind = goal.get("kind")
        if kind in ("combat", "native_combat"):
            goal["kind"] = "native_combat"
        elif kind in ("idle", "native_idle"):
            goal["kind"] = "native_idle"
    require(isinstance(goal, dict), "goal must be object")
    _add(out, goal.get("kind") == "native_combat", "goal.native_combat")
    _add(out, goal.get("kind") == "native_idle", "goal.native_idle")
    _add(out, goal.get("move_hint"), "goal.move_hint")
    obstacles = observation.get("local_obstacles", {})
    require(isinstance(obstacles, dict), "local_obstacles must be object")
    rays = obstacles.get("rays", [])
    require(isinstance(rays, list) and len(rays) in (0, 16), "local obstacle ray count must be 0 or 16")
    for index in range(16):
        ray = rays[index] if rays else {}
        require(isinstance(ray, dict), "obstacle ray must be object")
        _add(out, ray.get("distance_cm"), f"ray.{index}.distance_cm")
        _add(out, ray.get("hit"), f"ray.{index}.hit")
    return out


def _validate(observation: dict[str, Any]) -> None:
    """reference.validate_observation without its trailing actor_features build (done once by the caller)."""
    require(observation.get("schema_version") == 1, "unsupported body observation schema")
    reference.identity(observation)
    integer(observation.get("observation_seq"), "observation_seq", minimum=1)
    now = reference.record_time(observation)
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
        for key in HIDDEN_TARGET_KEYS:
            require(target.get(key) is None, f"hidden target has current feature {key}")
    value = target.get("last_seen_game_ms")
    require(value is None or integer(value, "last_seen_game_ms") <= now, "future target feature last_seen_game_ms")
    last_result = observation.get("last_result", {})
    if last_result.get("valid"):
        require(integer(last_result.get("source_game_ms"), "result source time") <= now, "future result in observation")
        require(integer(last_result.get("source_sim_step"), "result source step") <= sim_step, "future result step in observation")
    for event in observation.get("events", []):
        require(integer(event.get("source_game_ms"), "event source time") <= now, "future event in observation")
        require(integer(event.get("source_sim_step"), "event source step") <= sim_step, "future event step in observation")


class _Normalizer:
    __slots__ = ("source", "version", "pairs")

    def __init__(self, normalizer: dict[str, Any]):
        version = normalizer.get("feature_version")
        names = reference.canonical_feature_names(version)
        require(normalizer.get("feature_names") == names, "normalizer feature schema mismatch")
        pairs = []
        for name in names:
            mean = reference.finite(normalizer["mean"][name], "normalizer mean")
            scale = reference.finite(normalizer["std"][name], "normalizer std")
            require(scale > 0, "normalizer std must be positive")
            pairs.append((mean, scale))
        self.source, self.version, self.pairs = normalizer, version, tuple(pairs)


_cache: list[_Normalizer] = []


def _prepared(normalizer: dict[str, Any]) -> _Normalizer:
    for entry in _cache:
        if entry.source is normalizer:
            return entry
    entry = _Normalizer(normalizer)
    _cache.append(entry)
    del _cache[:-8]
    return entry


def pack_features(observation: dict[str, Any], normalizer: dict[str, Any], *, sequence_dt_game_ms: int) -> list[float]:
    """Drop-in for body_trajectory_dataset.pack_features with the same result and failures."""
    _validate(observation)
    integer(sequence_dt_game_ms, "sequence_dt_game_ms")
    prepared = _prepared(normalizer)
    if prepared.version == PERIODIC_FEATURE_VERSION:
        require(observation["phase"] == "post_simulation_post_animation" and observation.get("periodic") is True,
                "model memory accepts periodic snapshots only")
        raw = _actor_values(observation, sequence_dt_game_ms, periodic_goal=True)
    else:
        require(prepared.version == FEATURE_VERSION, "unsupported feature packing version")
        raw = _actor_values(observation, sequence_dt_game_ms, periodic_goal=False)
    result = []
    for value, (mean, scale) in zip(raw, prepared.pairs):
        normalized = (value-mean)/scale
        if not _isfinite(normalized):
            raise DatasetValidationError("nonfinite/nonnumeric normalized feature")
        result.append(normalized)
    return result


def verify(args: argparse.Namespace) -> int:
    normalizer = json.loads(args.normalizer.read_text())
    checked, mismatched, failures = 0, 0, 0
    for path in sorted(p for root in args.root for p in root.rglob("observations.jsonl")):
        previous: dict[int, int] = {}
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row.get("periodic") is not True or row.get("phase") != "post_simulation_post_animation":
                continue
            entity = row["entity_id"]
            dt = row["t_game_ms"]-previous.get(entity, row["t_game_ms"]-row["dt_game_ms"])
            previous[entity] = row["t_game_ms"]
            try:
                expected = reference.pack_features(row, normalizer, sequence_dt_game_ms=dt)
            except DatasetValidationError:
                expected = None
            try:
                actual = pack_features(row, normalizer, sequence_dt_game_ms=dt)
            except DatasetValidationError:
                actual = None
            same = (expected is None and actual is None) or (
                expected is not None and actual is not None and len(expected) == len(actual) and
                struct.pack(f"{len(expected)}d", *expected) == struct.pack(f"{len(actual)}d", *actual))
            checked += 1
            mismatched += not same
            failures += expected is None
            if checked >= args.limit:
                break
        if checked >= args.limit:
            break
    print(json.dumps({"rows": checked, "bit_mismatches": mismatched, "rows_both_rejected": failures}), flush=True)
    return 0 if checked and not mismatched else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("verify")
    check.add_argument("--normalizer", type=Path, required=True)
    check.add_argument("--root", type=Path, action="append", required=True)
    check.add_argument("--limit", type=int, default=1_000_000)
    args = parser.parse_args()
    return verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
