#!/usr/bin/env python3
"""Collect independent combat-teacher bouts in the real Linux headless arena."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from body_arena_env import ArenaRunner, EpisodeEnv
from body_combat_teacher import PROVIDER, TEACHER_ID, choose, select_certificate
from body_headless_suite import sha256, write_json
from body_observe_suite import provenance
from body_trajectory_recorder import RecorderError, TrajectoryRecorder, approve_scripted_demonstration
from body_trajectory_suite import bind_closed_sources, drain_capture


def verify_maneuver_enable(args, config, binary_sha):
    """Bind permission to collect arcs to the frozen, actual motor comparison."""
    if config["maneuver_rule_status"] != "enabled_after_paired_motor_probe":
        return {"enabled": False, "reason": "paired_motor_evidence_pending"}
    evidence = config["maneuver_enable_evidence"]
    data = args.output.parent.parent.resolve()
    root = (data/evidence["run"]).resolve()
    if root.parent != data or evidence["binary_sha256"] != binary_sha or config["microcurriculum"]["safe_endpoint_focus_degrees"] != 30:
        raise RecorderError("maneuver motor run/binary/endpoint policy binding differs")
    required = {"reference/gothic-body-runner", "validation/visibility-comparison.json",
                "setup-probe/parity/headless-parity.json", "validation/probe/body-combat.json"}
    if set(evidence["files"]) != required:
        raise RecorderError("maneuver motor evidence inventory differs")
    for name, sha in evidence["files"].items():
        path = (root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha256(path) != sha:
            raise RecorderError(f"maneuver motor evidence changed: {name}")
    report = json.loads((root/"validation/probe/body-combat.json").read_text())
    parity = json.loads((root/"setup-probe/parity/headless-parity.json").read_text())
    comparison = json.loads((root/"validation/visibility-comparison.json").read_text())
    if (report.get("phase_ready") is not True or report.get("issues") or parity.get("status") != "matched" or parity.get("issues") or
            parity.get("candidate_metadata", {}).get("binary_sha256") != binary_sha or
            {row["seed"] for row in parity["episodes"] if row.get("status") == "matched"} != {17, 29, 43}):
        raise RecorderError("actual motor probe or unchanged native parity did not pass")
    for side in (0, 1):
        name = f"old-duel-29-{side}-gru"
        paired = next((row for row in comparison["comparisons"] if row["name"] == name), {})
        before, after = comparison["before"][name], comparison["after"][name]
        if (not all(paired.get(key) is True for key in ("same_case_and_mode", "same_checkpoint_and_normalizer", "comparable")) or
                before["case"] != after["case"] or before["checkpoint_sha256"] != after["checkpoint_sha256"] or before["normalizer_sha256"] != after["normalizer_sha256"]):
            raise RecorderError("motor comparison changes case/checkpoint/normalizer")
    actual = comparison["after"]["old-duel-29-0-gru"]
    directory = Path(actual["directory"]).resolve()
    if not directory.is_relative_to(root):
        raise RecorderError("actual motor proof episode escapes its frozen run")
    for name, sha in actual["source_sha256"].items():
        path = (directory/name).resolve()
        if not path.is_relative_to(directory) or sha256(path) != sha:
            raise RecorderError("actual motor proof raw sources changed")
    from body_trajectory_dataset import iter_jsonl
    controls = list(iter_jsonl(directory/"body-control-events.jsonl"))
    observations = list(iter_jsonl(directory/"observations.jsonl"))
    last_sequences = {arc["trace"]["last"]["seq"] for arc in actual["owned_strafes"] if arc["finished_phase"] == "completed"}
    endpoints = {row["seq"]: row for row in iter_jsonl(directory/"events.jsonl") if row.get("seq") in last_sequences}
    chosen = []
    for action in (3, 4):
        arc = next((row for row in actual["owned_strafes"] if row["entity_id"] == actual["primary_entity_id"] and
                    row["action_id"] == action and row["finished_phase"] == "completed" and row["trace"]["last"]["actual_can_see"] is True), None)
        if arc is None:
            raise RecorderError("actual motor proof lacks both primary completed visible arc directions")
        start = next((row for row in controls if row["event_seq"] == arc["started_event_seq"]), {})
        end = next((row for row in controls if row["event_seq"] == arc["finished_event_seq"]), {})
        if any(row.get("phase") != phase or row.get("action_id") != action or row.get("command_id") != arc["command_id"] or
               row.get("execution_id") != arc["execution_id"] or row.get("entity_id") != arc["entity_id"] for row, phase in ((start, "started"), (end, "completed"))):
            raise RecorderError("actual motor arc lifecycle differs from raw native controls")
        native_endpoint = endpoints.get(arc["trace"]["last"]["seq"], {})
        if not (native_endpoint.get("phase") == "visibility_diagnostic" and native_endpoint.get("source_phase") == "controller_segment_completed" and
                native_endpoint.get("command_id") == arc["command_id"] and native_endpoint.get("execution_id") == arc["execution_id"] and
                native_endpoint.get("entity_id") == arc["entity_id"] and native_endpoint.get("t_game") == end["t_game_ms"] and
                native_endpoint.get("actual_can_see") is True):
            raise RecorderError("actual completed motor endpoint is not visibly proven by its raw native journal")
        endpoint = arc["first_eligible_periodic_before_next_command"]
        row = next((row for row in observations if row.get("entity_id") == arc["entity_id"] and row["observation_seq"] == endpoint["observation_seq"]), {})
        if not (row.get("periodic") is True and row.get("can_decide") is True and row.get("control_generation") == arc["control_generation"] and
                row.get("t_game_ms") == endpoint["game_ms"] >= end["t_game_ms"] and row.get("snapshot_wall_monotonic_ns") == endpoint["snapshot_wall_monotonic_ns"] < endpoint["before_next_command"]["wall_monotonic_ns"] and
                row.get("target", {}).get("visible") is True and row["target"].get("current_valid") is True and row["target"].get("focus_30") is True and row["target"].get("melee_line_clear") is True and
                row.get("self", {}).get("in_air") is False):
            raise RecorderError("actual motor endpoint differs from its visible eligible focus30 raw observation")
        chosen.append({"action_id": action, "command_id": arc["command_id"], "execution_id": arc["execution_id"],
                       "endpoint_observation_seq": row["observation_seq"], "focus_30": True, "focus_5": row["target"]["focus_5"]})
    return {"enabled": True, "motor_run": str(root), "binary_sha256": binary_sha, "files": evidence["files"],
            "raw_proof_episode": str(directory), "raw_sha256": actual["source_sha256"], "primary_both_directions": chosen,
            "native_parity": "matched", "same_checkpoint_and_cases": True, "quality_admission": False}


class CombatRunner(ArenaRunner):
    def __init__(self, args):
        self.setup_options: dict[str, Any] = {}
        super().__init__(args)

    def call(self, command):
        command = dict(command)
        if command.get("provider") == "stage5-scripted":
            command["provider"] = PROVIDER
        if command.get("cmd") == "reset" and isinstance(command.get("arena"), dict):
            command["arena"] = {**command["arena"], **self.setup_options}
        return super().call(command)


def collection_scope(args) -> str:
    return "preflight" if args.geometry_preflight else "calibration" if args.calibration else "smoke" if args.smoke else "full"


def declared_cases(config: dict[str, Any], *, smoke: bool = False, calibration: bool = False,
                   geometry_preflight: bool = False) -> list[dict[str, Any]]:
    if sum((smoke, calibration, geometry_preflight)) > 1:
        raise ValueError("collection modes are mutually exclusive")
    if calibration:
        return [dict(case) for case in config["calibration"]["cases"]]
    cases = []
    clearances = config["microcurriculum"]["wall_clearance_by_seed_cm"]
    if config["microcurriculum"]["apply_clearance_to_full_bouts"] is not False:
        raise ValueError("full native bouts retain their verified legacy wall layout")
    for split, seeds in (("train", config["train_seeds"]), ("eval", config["eval_seeds"]), ("ood", config["ood_seeds"])):
        for index, seed in enumerate(seeds):
            # Both physical sides and both environments share the seed group.
            distance = config["ood_distances_cm"][index] if split == "ood" else config["distances_cm"][index % len(config["distances_cm"])]
            for geometry in ([config["ood_geometry"]] if split == "ood" else config["geometries"]):
                for side in config["sides"]:
                    case = {"name": f"combat-{split}-{seed}-{geometry}-{side}", "seed": seed, "split": split,
                                  "side": side, "geometry": geometry, "distance_cm": distance,
                                  "yaw_offset_deg": (config["ood_yaw_offsets_deg"] if split == "ood" else config["yaw_offsets_deg"])[side], "center_offset_cm": [0, 0],
                                  "separation_yaw_deg": 0, "task": "native_duel", "timeout_ms": config["timeout_game_ms"]}
                    cases.append(case)
    micro = config["microcurriculum"]
    if micro["enabled"]:
        for seed in config["train_seeds"]:
            for variant in micro["variants"]:
                case = {"name": f"maneuver-train-{seed}-{variant['id']}", "seed": seed, "split": "train", "side": variant["side"],
                              "geometry": micro["geometry"], "distance_cm": variant["distance_cm"], "yaw_offset_deg": variant["yaw_offset_deg"],
                              "center_offset_cm": [0, 0], "separation_yaw_deg": 0, "task": micro["scenario"],
                              "timeout_ms": micro["timeout_game_ms"], "curriculum": "safe_native_arc_then_settle",
                              "variant_id": variant["id"], "expected_action_id": variant["expected_action_id"],
                              "calibration_case": variant["calibration_case"], "wall_clearance_cm": clearances[str(seed)],
                              "opponent": micro["opponent"]}
                if "wall_tangent_sign" in variant:
                    case["wall_tangent_sign"] = variant["wall_tangent_sign"]
                cases.append(case)
    if geometry_preflight:
        return [case for case in cases if case["task"] == "reset_stress"]
    return [case for case in cases if case["seed"] == config["smoke"]["train_seed"] and case["split"] == "train"] if smoke else cases


def wall_clearance_evidence(initial: dict[str, Any], case: dict[str, Any]) -> dict[str, Any] | None:
    """Require the requested variation to move real, source-recorded geometry."""
    if "wall_clearance_cm" not in case:
        return None
    setup = initial["initial_state"]["setup"]
    geometry = setup["geometry_proof"]
    requested = case["wall_clearance_cm"]
    actual, error, tolerance = (geometry.get(key) for key in
                                ("actual_wall_clearance_cm", "wall_clearance_error_cm", "wall_clearance_tolerance_cm"))
    if (case["geometry"] != "near_wall" or setup.get("wall_clearance_cm") != requested or
            geometry.get("requested_wall_clearance_cm") != requested or
            any(type(value) not in (int, float) or not math.isfinite(value) for value in (requested, actual, error, tolerance)) or
            not 60 <= requested <= 180 or not 60 <= actual <= 180 or tolerance != 20 or abs(error) > tolerance or
            not math.isclose(error, actual-requested, abs_tol=1e-3) or
            not math.isclose(geometry.get("obstacle_distance_cm", float("inf")), actual, abs_tol=1e-3)):
        raise RecorderError("requested wall clearance differs from actual native geometry proof")
    def vector(key):
        value = geometry.get(key)
        if not isinstance(value, list) or len(value) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
            raise RecorderError("actual native geometry proof lacks a finite normal-placement vector")
        return value

    def unit_xz(key):
        value = vector(key)
        if abs(value[1]) > 1e-5 or not math.isclose(math.hypot(value[0], value[2]), 1, abs_tol=1e-5):
            raise RecorderError("actual native geometry proof has a non-unit horizontal normal/tangent")
        return value

    placement = unit_xz("placement_wall_normal_xz_world")
    tangent = unit_xz("placement_wall_tangent_world")
    normal = unit_xz("actual_wall_normal_xz_world")
    direction = unit_xz("wall_direction_world")
    unit_xz("search_ray_direction_world")
    vector("placement_wall_anchor_cm")
    center, point, raw_normal = vector("primary_collision_center_cm"), vector("wall_point_cm"), vector("wall_normal")
    dot = lambda left, right: sum(a*b for a, b in zip(left, right))
    horizontal_length = math.hypot(raw_normal[0], raw_normal[2])
    raw_orientation = -1 if dot(raw_normal, direction) > 0 else 1
    normal_actual, normal_error, normal_tolerance = (geometry.get(key) for key in
        ("actual_wall_normal_clearance_cm", "wall_normal_clearance_error_cm", "wall_normal_clearance_tolerance_cm"))
    measured = dot([c-p for c, p in zip(center, point)], normal)
    if (geometry.get("placement_basis") != "wall_normal_xz" or
            geometry.get("requested_wall_normal_clearance_cm") != requested or
            horizontal_length <= 1e-5 or abs(raw_normal[1]) > .35 or
            not math.isclose(normal[0], raw_orientation*raw_normal[0]/horizontal_length, abs_tol=1e-5) or
            not math.isclose(normal[2], raw_orientation*raw_normal[2]/horizontal_length, abs_tol=1e-5) or
            abs(dot(placement, tangent)) > 1e-5 or dot(placement, normal) < .95 or
            any(not math.isclose(a, -b, abs_tol=1e-5) for a, b in zip(direction, placement)) or
            any(type(value) not in (int, float) or not math.isfinite(value) for value in (normal_actual, normal_error, normal_tolerance)) or
            not 60 <= normal_actual <= 180 or normal_tolerance != 20 or abs(normal_error) > normal_tolerance or
            not math.isclose(normal_error, normal_actual-requested, abs_tol=1e-3) or
            not math.isclose(measured, normal_actual, abs_tol=.02)):
        raise RecorderError("requested wall normal clearance differs from actual native geometry proof")
    evidence = {"requested_cm": requested, "actual_cm": actual, "error_cm": error, "tolerance_cm": tolerance,
            "normal_requested_cm": requested, "normal_actual_cm": normal_actual,
            "normal_error_cm": normal_error, "normal_tolerance_cm": normal_tolerance, "placement_basis": "wall_normal_xz",
            "geometry_proof": geometry, "training_admission": "requires_actual_command_outcome"}
    if "wall_tangent_sign" not in case and ("wall_tangent_sign" in setup or
            "requested_wall_tangent_sign" in geometry or "placement_tangent_sign" in geometry):
        raise RecorderError("native placement unexpectedly imposed a wall tangent sign")
    if "wall_tangent_sign" in case:
        sign = case["wall_tangent_sign"]
        actual_sign = geometry.get("placement_tangent_sign")
        actors = {actor["role"]: actor for actor in initial["initial_state"]["actors"]}
        primary, opponent = actors["primary"], actors["opponent"]
        positions = [primary.get("pos"), opponent.get("pos")]
        if (type(sign) is not int or sign not in (-1, 1) or type(actual_sign) is not int or actual_sign != sign or
                type(setup.get("wall_tangent_sign")) is not int or setup["wall_tangent_sign"] != sign or
                type(geometry.get("requested_wall_tangent_sign")) is not int or geometry["requested_wall_tangent_sign"] != sign or
                primary.get("physical_side") != case["side"] or opponent.get("physical_side") != 1-case["side"] or
                any(not isinstance(pos, list) or len(pos) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in pos) for pos in positions)):
            raise RecorderError("wall tangent sign differs from actual native placement or physical side")
        measured_tangent = dot([b-a for a, b in zip(*positions)], tangent)
        measured_normal = dot([b-a for a, b in zip(*positions)], placement)
        expected_tangent = sign*case["distance_cm"]*(1 if case["side"] == 0 else -1)
        if not math.isclose(measured_tangent, expected_tangent, abs_tol=.05) or abs(measured_normal) > .05:
            raise RecorderError("wall tangent sign differs from actual native actor coordinates")
        evidence.update(requested_tangent_sign=sign, actual_tangent_sign=actual_sign,
                        primary_relative_tangent_sign=sign*(1 if case["side"] == 0 else -1),
                        pair_tangent_projection_cm=measured_tangent, expected_pair_tangent_projection_cm=expected_tangent,
                        pair_normal_projection_cm=measured_normal, pair_projection_tolerance_cm=.05)
    return evidence


def verify_numeric_wall_clearance(initial: dict[str, Any], evidence: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Audit the ordinary 16-ray observer independently of the placement proof."""
    if (row.get("entity_id") != initial["primary_entity_id"] or row.get("session_epoch") != initial["session_epoch"] or
            row.get("world_epoch") != initial["world_epoch"] or row.get("periodic") is not True or
            row.get("phase") != "post_simulation_post_animation"):
        raise RecorderError("normal clearance lacks a current primary periodic observation")
    rays, yaw = row.get("local_obstacles", {}).get("rays"), row.get("self", {}).get("yaw_rad")
    if not isinstance(rays, list) or len(rays) != 16 or type(yaw) not in (int, float) or not math.isfinite(yaw):
        raise RecorderError("normal clearance lacks ordinary numeric rays/yaw")
    normal = evidence["geometry_proof"]["actual_wall_normal_xz_world"]
    candidates = []
    for index, ray in enumerate(rays):
        angle, distance = ray.get("angle_rad"), ray.get("distance_cm")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (angle, distance)) or not 0 <= distance <= 300:
            raise RecorderError("normal clearance contains an invalid ordinary numeric ray")
        direction = [math.cos(yaw+angle), 0, math.sin(yaw+angle)]
        toward_cosine = -sum(a*b for a, b in zip(direction, normal))
        candidates.append((math.acos(max(-1, min(1, toward_cosine))), index, distance*toward_cosine, direction))
    angle, index, projected, direction = min(candidates)
    ray = rays[index]
    if ray.get("hit") is not True or angle > math.pi/16+1e-6 or abs(projected-evidence["normal_actual_cm"]) > 2:
        raise RecorderError("ordinary numeric ray does not independently confirm primary wall normal clearance")
    return {"entity_id": row["entity_id"], "session_epoch": row["session_epoch"], "world_epoch": row["world_epoch"],
            "control_generation": row["control_generation"], "observation_seq": row["observation_seq"], "t_game_ms": row["t_game_ms"],
            "ray_index": index, "ray_distance_cm": ray["distance_cm"], "ray_world_direction": direction,
            "normal_projection_cm": projected, "normal_error_cm": projected-evidence["normal_actual_cm"],
            "angle_to_actual_wall_normal_rad": angle, "angle_limit_rad": math.pi/16, "projection_tolerance_cm": 2}


def registry(config, config_file, teacher_file):
    return {"schema_version": 1, "teachers": {TEACHER_ID: {"provider": PROVIDER, "module": config["teacher_module"],
            "code_sha256": sha256(teacher_file), "config_sha256": sha256(config_file), "controlled_role": "primary",
            "allowed_actions": config["allowed_actions"], "allowed_scenarios": ["native_duel", "reset_stress"]}}}


def collect(env, args, config, case, proof, deadline):
    directory = args.output/"episodes"/case["name"]
    env.runner.setup_options = {key: case[key] for key in ("geometry", "yaw_offset_deg", "center_offset_cm", "separation_yaw_deg", "wall_clearance_cm", "wall_tangent_sign") if key in case}
    initial = env.reset(output=directory, task=case["task"], seed=case["seed"], side=case["side"],
                        distance_cm=case["distance_cm"], timeout_ms=case["timeout_ms"], provider="scripted")
    clearance_proof = wall_clearance_evidence(initial, case)
    teacher_file = Path(__file__).with_name("body_combat_teacher.py")
    (directory/"teacher-source.py").write_bytes(teacher_file.read_bytes())
    (directory/"teacher-config.json").write_bytes(args.config.read_bytes())
    metadata = {"provider": PROVIDER, "teacher": TEACHER_ID, "model": None, "scenario": case["task"], "seed": case["seed"],
                "side": case["side"], "ood": case["split"] == "ood", "declared_split": case["split"],
                "group": {"setup_family": config["setup_family"], "seed": case["seed"]}, "setup": env.runner.setup_options,
                "config_sha256": sha256(args.config), "setup_sha256": sha256(args.scenario),
                **{key: proof[key] for key in ("binary_sha256", "source_sha256", "save_sha256", "assets")},
                "simulation": config["simulation"], "conditions": [], "curriculum": case.get("curriculum"),
                "curriculum_variant": case.get("variant_id"), "calibration_case": case.get("calibration_case"),
                "wall_clearance_evidence": clearance_proof,
                "collection_scope": collection_scope(args),
                "opponent_provenance": "scripted_hold_nonexpert" if case["task"] == "reset_stress" else "native_ai",
                "teacher_provenance": {"teacher_id": TEACHER_ID, "code_file": "teacher-source.py", "config_file": "teacher-config.json",
                                       "certificates_file": "teacher-certificates.jsonl"}}
    recorder = TrajectoryRecorder(directory, episode_id=case["name"], initial=initial, metadata=metadata,
                                  writer_capacity=config["budgets"]["writer_queue_capacity"])
    certificates = (directory/"teacher-certificates.jsonl").open("x", encoding="utf-8")
    primary, history, certificate_seq, advanced = initial["primary_entity_id"], [], 0, 2
    seen_periodic = 0
    enabled = config["maneuver_rule_status"] == "enabled_after_paired_motor_probe"
    micro = case["task"] == "reset_stress"
    micro_failed = False
    try:
        if micro and not enabled:
            raise RecorderError("maneuver curriculum cannot run before the actual paired motor proof enables its declaration")
        env.runner.call({"cmd": "body_visibility_watch", "enabled": True})
        env.runner.call({"cmd": "body_trajectory_watch", "enabled": True, "queue_capacity": config["budgets"]["native_queue_capacity"]})
        env.start()
        if clearance_proof is not None:
            clearance_proof["numeric_projection"] = verify_numeric_wall_clearance(initial, clearance_proof, env.latest.get(primary, {}))
        drain_capture(env.runner, recorder, config)
        while not (env.arena.get("terminated") or env.arena.get("truncated")):
            if time.monotonic() > deadline or advanced > config["budgets"]["max_episode_substeps"]:
                raise RecorderError("combat collection exceeded its predeclared wall/game budget")
            row = env.latest.get(primary)
            if row is not None and row.get("periodic") is True and row["observation_seq"] > seen_periodic:
                seen_periodic = row["observation_seq"]
                can_issue = not micro_failed and (not micro or certificate_seq < config["microcurriculum"]["max_primary_commands"])
                action = choose(row, history, allow_maneuver=enabled, maneuvers_only=micro) if env.eligible(primary) and can_issue else None
                if action is not None:
                    command = env.command_ids[primary]+1
                    certificate_seq += 1
                    cert = select_certificate(row, action, history, command_id=command, certificate_seq=certificate_seq,
                                              wall_monotonic=time.monotonic(), maneuver_enabled=enabled)
                    if cert is None:
                        raise RecorderError("combat teacher guard changed without a simulation step")
                    certificates.write(json.dumps(cert, separators=(",", ":"), allow_nan=False)+"\n")
                    certificates.flush()
                    if env.act(primary, action) != command:
                        raise RecorderError("combat teacher certificate references another command")
            step = env.step()
            advanced += step.advanced_substeps
            for event in step.events:
                if event.get("entity_id") == primary and event.get("phase") == "completed":
                    history.append({"entity_id": primary, "action_id": event["action_id"], "command_id": event["command_id"],
                                    "phase": "completed", "t_game_ms": event["t_game_ms"]})
                if micro and event.get("entity_id") == primary and event.get("phase") in ("cancelled", "rejected"):
                    micro_failed = True
            drain_capture(env.runner, recorder, config, final=step.arena if step.done else None)
        episode = env.finalize()
        drain_capture(env.runner, recorder, config, final=episode["final"])
        status, controls = env.runner.call({"cmd": "status"}), env.runner.call({"cmd": "body_events"})
        visibility = env.runner.call({"cmd": "body_control_status"}).get("visibility_diagnostics", {})
        if (visibility.get("enabled") is not True or visibility.get("capture_failed") is not False or
                type(visibility.get("dropped_total")) is not int or visibility["dropped_total"] != 0 or
                type(visibility.get("records_total")) is not int or visibility["records_total"] <= 0):
            raise RecorderError("native visibility proof was disabled, failed, dropped or absent")
        write_json(directory/"visibility-integrity.json", visibility)
        env.runner.call({"cmd": "body_trajectory_watch", "enabled": False})
        drain_capture(env.runner, recorder, config, final=episode["final"])
        certificates.flush()
        os.fsync(certificates.fileno())
        manifest = recorder.finish(episode["final"], source_files=[directory/"teacher-source.py", directory/"teacher-config.json",
                                  directory/"teacher-certificates.jsonl", directory/"visibility-integrity.json"], runner_status=status, control_events=controls)
        record = {**case, "provider": PROVIDER, "capture": True, "episode_dir": str(directory.relative_to(args.output)),
                  "manifest_file": f"episodes/{case['name']}/trajectory-manifest.json", "initial": initial, "final": episode["final"],
                  "counts": manifest["counts"], "primary_completed_commands": len(history), "native_opponent": not micro,
                  "wall_clearance_evidence": clearance_proof,
                  "micro_failed_command": micro_failed, "visibility_integrity_file": "visibility-integrity.json"}
        write_json(directory/"episode.json", record)
        return record
    finally:
        certificates.close()
        if not recorder.closed:
            recorder.abort()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "config", "scenario", "melee-scenario", "native-reference", "output", "existing-dataset"):
        parser.add_argument("--"+name, type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--calibration", action="store_true", help="Collect only the separately declared train-only geometry calibration; never merge automatically.")
    mode.add_argument("--geometry-preflight", action="store_true", help="Check all 48 declared train micro geometries without automatic training admission.")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("native combat collection runs on dev-ubuntu")
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    args.output.mkdir()
    (args.output/"episodes").mkdir()
    config, existing = json.loads(args.config.read_text()), json.loads(args.existing_dataset.read_text())
    groups = config["new_group_assignments"]
    if set(groups).intersection(existing["groups"]):
        raise RecorderError("new combat seed groups overlap the immutable corpus")
    for case in declared_cases(config, calibration=args.calibration, geometry_preflight=args.geometry_preflight):
        key = json.dumps([config["setup_family"], case["seed"]], separators=(",", ":"))
        if groups[key] != case["split"]:
            raise RecorderError("new combat cases disagree with declared group assignments")
    teacher_file = Path(__file__).with_name("body_combat_teacher.py")
    teacher_registry = registry(config, args.config, teacher_file)
    write_json(args.output/"teacher-registry.json", teacher_registry)
    suite = {"schema_version": 1, "kind": "body_combat_demonstrations_stage8_suite", "status": "infrastructure_failure",
             "suite_scope": collection_scope(args), "episodes": [], "teacher_registry": "teacher-registry.json",
             "existing_dataset": str(args.existing_dataset), "existing_dataset_sha256": sha256(args.existing_dataset),
             "new_group_assignments": groups, "safe_maneuver_rule_status": config["maneuver_rule_status"],
             "automatic_training_admission": False if args.calibration or args.geometry_preflight else None}
    runner = None
    try:
        proof = provenance(args)
        enable_proof = verify_maneuver_enable(args, config, proof["binary_sha256"])
        suite["maneuver_enable_evidence"] = enable_proof
        proof.update(kind="body_combat_demonstrations_stage8", suite_scope=suite["suite_scope"],
                     teacher_code_sha256=sha256(teacher_file), collector_sha256=sha256(Path(__file__)), maneuver_enable_evidence=enable_proof)
        write_json(args.output/"manifest.json", proof)
        runner = CombatRunner(args)
        suite["runner_pid"] = runner.process.pid
        env = EpisodeEnv(runner, args.scenario, watchdog_ms=config["budgets"]["provider_watchdog_wall_ms"])
        deadline = time.monotonic()+config["budgets"]["collection_wall_timeout_s"]
        for case in declared_cases(config, smoke=args.smoke, calibration=args.calibration, geometry_preflight=args.geometry_preflight):
            suite["episodes"].append(collect(env, args, config, case, proof, deadline))
            write_json(args.output/"suite.json", suite)
            print(f"combat teacher [{case['name']}] full native bout recorded", flush=True)
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode != 0:
            raise RecorderError("combat engine did not close successfully")
        bind_closed_sources(args, suite["episodes"])
        for record in suite["episodes"]:
            directory = args.output/record["episode_dir"]
            approved = approve_scripted_demonstration(directory, teacher_registry)
            evidence = approved["scripted_expert_approval"]
            record["approved_actions"] = {action: sum(row["action_id"] == action for row in evidence["approved"]) for action in range(1, 12)}
            record["approval_diagnostics"] = evidence["diagnostics"]
            record["approval_sha256"] = sha256(directory/"records-approved.jsonl")
            write_json(args.output/"suite.json", suite)
        new_counts = {action: sum(record["approved_actions"][action] for record in suite["episodes"] if record["split"] == "train")
                      for action in (3, 4, 10, 11)}
        required = config["coverage"]["minimum_new_train_usable_actions"]
        suite["new_train_maneuver_coverage"] = {"counts": new_counts, "required": required,
                                               "ready": all(new_counts[int(action)] >= count for action, count in required.items())}
        if not (args.smoke or args.calibration or args.geometry_preflight) and not suite["new_train_maneuver_coverage"]["ready"]:
            raise RecorderError("physically approved new maneuver/wait coverage is insufficient; all raw failed demonstrations are retained")
        suite.update(status="collected", resources=runner.samples,
                     dataset_phase="diagnostic_geometry_preflight_only_no_automatic_merge" if args.geometry_preflight else
                                   "diagnostic_calibration_only_no_automatic_merge" if args.calibration else "deferred_to_parent_canonical_dataset")
        proof.update(completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        write_json(args.output/"manifest.json", proof)
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        suite["error"] = str(error)
        print(f"combat demonstrations failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        if runner is not None:
            suite["resources"] = runner.samples
            runner.terminate()
        write_json(args.output/"suite.json", suite)


if __name__ == "__main__":
    raise SystemExit(main())
