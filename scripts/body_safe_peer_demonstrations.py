#!/usr/bin/env python3
"""Collect train-only causal safe alternatives against a real scripted moving peer.

No model is loaded. This new curriculum cannot modify or silently reassign the
immutable parent corpus. Calibration/smoke are distinct diagnostic runs; only a
fully proven production corpus can be admitted by the separate child suite.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from body_arena_env import ArenaInfrastructureError, ArenaRunner, EpisodeEnv
from body_headless_suite import sha256, write_json
from body_observe_suite import provenance
from body_safe_peer_teacher import PROVIDER, TEACHER_ID, choose, select_certificate
from body_trajectory_dataset import periodic_model_view, validate_episode
from body_trajectory_recorder import RecorderError, TrajectoryRecorder, approve_scripted_demonstration
from body_trajectory_suite import bind_closed_sources, drain_capture

TRAIN_SEEDS = (181, 191, 193, 197, 199, 211)


def declared_cases(config: dict[str, Any], *, smoke: bool = False, calibration: bool = False) -> list[dict[str, Any]]:
    if smoke and calibration:
        raise ValueError("smoke and calibration are separate collection scopes")
    if (config["train_seeds"] != list(TRAIN_SEEDS) or config["sides"] != [0, 1] or
            config["eval_seeds"] or config["test_seeds"] or config["ood_seeds"] or
            config["allowed_actions"] != [2, 5, 11]):
        raise ValueError("SafePeer is fixed train-only; no held-out groups or other expert actions")
    variants = config["variants"]
    if len(variants) != 6 or Counter(row["expected_action_id"] for row in variants) != {2: 2, 5: 2, 11: 2}:
        raise ValueError("exactly two declared observable variants per action are required")
    if smoke or calibration:
        variants = [next(row for row in variants if row["expected_action_id"] == action) for action in (2, 5, 11)]
    return [{"name": f"safe-peer-train-{seed}-{side}-{variant['id']}", "seed": seed, "side": side,
             "split": "train", "task": "reset_stress", "geometry": "open", "timeout_ms": config["timeout_game_ms"],
             **variant} for seed in (TRAIN_SEEDS[:1] if smoke or calibration else TRAIN_SEEDS)
            for side in config["sides"] for variant in variants]


def validate_parent(config: dict[str, Any], parent: dict[str, Any]) -> dict[str, str]:
    actual = Counter(entry["split"] for entry in parent["episodes"])
    if (parent.get("validated") is not True or parent.get("episode_count") != 207 or
            actual != Counter({"train": 148, "eval": 20, "test": 19, "ood": 20}) or
            parent.get("feature_version") != "body-periodic-numeric-v1" or
            parent.get("model_sampling", {}).get("kind") != "periodic_only"):
        raise ValueError("parent must be the immutable verified 207-episode defence corpus")
    assignments = {}
    for seed in TRAIN_SEEDS:
        group = json.dumps([config["setup_family"], seed], separators=(",", ":"))
        if parent["groups"].get(group) != "train":
            raise ValueError("SafePeer seed family does not inherit parent train membership")
        assignments[group] = "train"
    return assignments


class SafePeerRunner(ArenaRunner):
    def __init__(self, args):
        self.reset_extras: dict[str, Any] = {}
        super().__init__(args)

    def call(self, command):
        command = dict(command)
        if command.get("provider") == "stage5-scripted":
            command["provider"] = PROVIDER
        if command.get("cmd") == "reset" and isinstance(command.get("arena"), dict):
            command["arena"] = {**command["arena"], **self.reset_extras}
        return super().call(command)


def peer_choose(row: dict[str, Any], case: dict[str, Any], peer_history: list[dict[str, Any]]) -> int | None:
    """Declared deterministic stimulus from the peer's own legal current state."""
    mask, target, own = row.get("action_mask"), row.get("target", {}), row.get("self", {})
    if (row.get("periodic") is not True or row.get("can_decide") is not True or row.get("action_mask_valid") is not True or
            not isinstance(mask, list) or len(mask) != 12 or mask != row.get("availability_mask") or
            target.get("current_valid") is not True or target.get("visible") is not True or
            any(own.get(key) is not False for key in ("in_air", "swim", "dive"))):
        return None
    completed = [entry["action_id"] for entry in peer_history]
    if len(completed) >= 3:
        return None
    if target.get("focus_5") is False and mask[2]:
        return 2
    movement = case["peer_motion_action"]
    if movement not in completed:
        if mask[movement] is not True:
            return None
        rays = row.get("local_obstacles", {}).get("rays", [])
        fan = range(1, 8) if movement == 3 else range(9, 16) if movement == 4 else (0,)
        if len(rays) != 16 or any(rays[index].get("hit") is not False or rays[index].get("distance_cm", 0) < 299 for index in fan):
            return None
        return movement
    if case["peer_attack_after_motion"] and 6 not in completed and mask[6] is True:
        return 6
    return None


def registry(args, config):
    return {"schema_version": 1, "teachers": {TEACHER_ID: {
        "provider": PROVIDER, "module": "body_safe_peer_teacher", "controlled_role": "primary",
        "allowed_actions": [2, 5, 11], "allowed_scenarios": ["reset_stress"],
        "code_sha256": sha256(Path(__file__).with_name("body_safe_peer_teacher.py")), "config_sha256": sha256(args.config)}}}


def collect(env, args, config, case, proof, deadline):
    directory = args.output/"episodes"/case["name"]
    env.runner.reset_extras = {"geometry": "open", "yaw_offset_deg": case["yaw_offset_deg"]}
    initial = env.reset(output=directory, task="reset_stress", seed=case["seed"], side=case["side"],
                        distance_cm=case["distance_cm"], timeout_ms=case["timeout_ms"], provider="scripted")
    teacher_file = Path(__file__).with_name("body_safe_peer_teacher.py")
    (directory/"teacher-source.py").write_bytes(teacher_file.read_bytes())
    (directory/"teacher-config.json").write_bytes(args.config.read_bytes())
    scope = "calibration" if args.calibration else "smoke" if args.smoke else "full"
    metadata = {"provider": PROVIDER, "teacher": TEACHER_ID, "model": None, "scenario": "reset_stress",
                "seed": case["seed"], "side": case["side"], "ood": False, "declared_split": "train",
                "group": {"setup_family": config["setup_family"], "seed": case["seed"]},
                "setup": {**env.runner.reset_extras, "distance_cm": case["distance_cm"], "timeout_ms": case["timeout_ms"]},
                "config_sha256": sha256(args.config), "setup_sha256": sha256(args.scenario),
                **{key: proof[key] for key in ("binary_sha256", "source_sha256", "save_sha256", "assets")},
                "simulation": config["simulation"], "conditions": [], "collection_scope": scope,
                "curriculum_variant": case["id"], "expected_action_is_diagnostic_only": True,
                "opponent_provenance": "causal_scripted_moving_peer_nonexpert",
                "teacher_provenance": {"teacher_id": TEACHER_ID, "code_file": "teacher-source.py", "config_file": "teacher-config.json",
                                       "certificates_file": "teacher-certificates.jsonl"}}
    recorder = TrajectoryRecorder(directory, episode_id=case["name"], initial=initial, metadata=metadata,
                                  writer_capacity=config["budgets"]["writer_queue_capacity"])
    certificates = (directory/"teacher-certificates.jsonl").open("x", encoding="utf-8")
    primary, peer = initial["primary_entity_id"], initial["opponent_entity_id"]
    histories = {primary: [], peer: []}
    seen, advanced, sequence, primary_failed = {}, 2, 0, False
    try:
        status = env.runner.call({"cmd": "status"})
        if status.get("body_observation", {}).get("sample_period_ms") != 50:
            raise RecorderError("SafePeer must retain the original 20Hz periodic observer")
        env.runner.call({"cmd": "body_visibility_watch", "enabled": True})
        env.runner.call({"cmd": "body_trajectory_watch", "enabled": True, "queue_capacity": config["budgets"]["native_queue_capacity"]})
        first = env.start()
        drain_capture(env.runner, recorder, config, final=first.arena if first.done else None)
        while not (env.arena.get("terminated") or env.arena.get("truncated")):
            if time.monotonic() > deadline or advanced > config["budgets"]["max_episode_substeps"]:
                raise RecorderError("SafePeer exceeded its declared wall/substep budget")
            # The peer starts genuine motion before the primary's first decision.
            # No simulator tick occurs between inspecting a row and its command.
            peer_row = env.latest.get(peer)
            peer_issued = False
            if peer_row is not None and peer_row.get("periodic") is True and peer_row["observation_seq"] > seen.get(peer, 0):
                seen[peer] = peer_row["observation_seq"]
                action = peer_choose(peer_row, case, histories[peer])
                if action is not None and env.eligible(peer, action):
                    env.act(peer, action)
                    peer_issued = True
            row = env.latest.get(primary)
            if row is not None and row.get("periodic") is True and row["observation_seq"] > seen.get(primary, 0) and not peer_issued:
                seen[primary] = row["observation_seq"]
                action = choose(row, histories[primary]) if env.eligible(primary) and not primary_failed else None
                if action is not None and sequence < config["budgets"]["max_primary_commands"]:
                    command = env.command_ids[primary]+1
                    sequence += 1
                    cert = select_certificate(row, action, histories[primary], command_id=command,
                                              certificate_seq=sequence, wall_monotonic=time.monotonic())
                    if cert is None:
                        raise RecorderError("causal SafePeer guard changed without an engine step")
                    certificates.write(json.dumps(cert, separators=(",", ":"), allow_nan=False)+"\n")
                    certificates.flush()
                    if env.act(primary, action) != command:
                        raise RecorderError("SafePeer command does not match its pre-command certificate")
            step = env.step()
            advanced += step.advanced_substeps
            for event in step.events:
                entity = event.get("entity_id")
                if entity in histories and event.get("phase") == "completed":
                    histories[entity].append({"entity_id": entity, "command_id": event["command_id"], "action_id": event["action_id"],
                                              "phase": "completed", "t_game_ms": event["t_game_ms"]})
                if entity == primary and event.get("phase") in ("cancelled", "rejected"):
                    primary_failed = True
            drain_capture(env.runner, recorder, config, final=step.arena if step.done else None)
        episode = env.finalize()
        drain_capture(env.runner, recorder, config, final=episode["final"])
        status, controls = env.runner.call({"cmd": "status"}), env.runner.call({"cmd": "body_events"})
        visibility = env.runner.call({"cmd": "body_control_status"}).get("visibility_diagnostics", {})
        if (visibility.get("enabled") is not True or visibility.get("capture_failed") is not False or
                type(visibility.get("dropped_total")) is not int or visibility["dropped_total"] != 0 or
                type(visibility.get("records_total")) is not int or visibility["records_total"] <= 0):
            raise RecorderError("posthoc native visibility proof was disabled, failed, dropped or absent")
        write_json(directory/"visibility-integrity.json", visibility)
        env.runner.call({"cmd": "body_trajectory_watch", "enabled": False})
        drain_capture(env.runner, recorder, config, final=episode["final"])
        certificates.flush()
        os.fsync(certificates.fileno())
        manifest = recorder.finish(episode["final"], source_files=[directory/"teacher-source.py", directory/"teacher-config.json",
                                  directory/"teacher-certificates.jsonl", directory/"visibility-integrity.json"], runner_status=status, control_events=controls)
        record = {**case, "provider": PROVIDER, "capture": True, "episode_dir": str(directory.relative_to(args.output)),
                  "manifest_file": f"episodes/{case['name']}/trajectory-manifest.json", "counts": manifest["counts"],
                  "initial": initial, "final": episode["final"], "primary_completed_commands": len(histories[primary]),
                  "peer_completed_commands": len(histories[peer]), "peer_expert": False, "primary_failed_command": primary_failed}
        write_json(directory/"episode.json", record)
        return record
    finally:
        certificates.close()
        if not recorder.closed:
            recorder.abort()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "config", "scenario", "melee-scenario",
                 "native-reference", "existing-dataset", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--calibration", action="store_true")
    parser.add_argument("--serial-workers", action="store_true", dest="native_serial_workers")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("SafePeer runs only on dev-ubuntu; no Mac simulation or model")
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    args.output.mkdir()
    (args.output/"episodes").mkdir()
    config, parent = json.loads(args.config.read_text()), json.loads(args.existing_dataset.read_text())
    groups = validate_parent(config, parent)
    configured_serial = config["runtime"]["native_serial_workers"]
    if type(configured_serial) is not bool or (args.native_serial_workers and not configured_serial):
        raise ValueError("native serial worker flag disagrees with preregistered configuration")
    args.native_serial_workers = configured_serial
    if config["parent"]["dataset_sha256"] != sha256(args.existing_dataset):
        raise ValueError("fill the exact immutable parent dataset SHA before freezing collection")
    cases = declared_cases(config, smoke=args.smoke, calibration=args.calibration)
    teacher_registry = registry(args, config)
    write_json(args.output/"teacher-registry.json", teacher_registry)
    suite = {"schema_version": 1, "kind": "body_safe_peer_demonstrations_stage8_suite", "status": "infrastructure_failure",
             "suite_scope": "calibration" if args.calibration else "smoke" if args.smoke else "full", "episodes": [],
             "teacher_registry": "teacher-registry.json", "existing_dataset": str(args.existing_dataset),
             "existing_dataset_sha256": sha256(args.existing_dataset), "new_group_assignments": groups,
             "automatic_training_admission": False, "model": None}
    runner = None
    try:
        proof = provenance(args)
        proof.update(kind="body_safe_peer_demonstrations_stage8", teacher_code_sha256=sha256(Path(__file__).with_name("body_safe_peer_teacher.py")),
                     collector_sha256=sha256(Path(__file__)), dataset_validator_sha256=sha256(Path(__file__).with_name("body_trajectory_dataset.py")),
                     existing_dataset_sha256=sha256(args.existing_dataset), native_serial_workers=configured_serial)
        write_json(args.output/"manifest.json", proof)
        runner = SafePeerRunner(args)
        suite["runner_pid"] = runner.process.pid
        env = EpisodeEnv(runner, args.scenario, watchdog_ms=config["budgets"]["provider_watchdog_wall_ms"])
        deadline = time.monotonic()+config["budgets"]["collection_wall_timeout_s"]
        for case in cases:
            suite["episodes"].append(collect(env, args, config, case, proof, deadline))
            write_json(args.output/"suite.json", suite)
            print(f"SafePeer [{case['name']}] causal raw episode retained", flush=True)
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode != 0:
            raise RecorderError("SafePeer native runner did not close successfully")
        bind_closed_sources(args, suite["episodes"])
        counts = Counter()
        missing = []
        for record in suite["episodes"]:
            directory = args.output/record["episode_dir"]
            approved = approve_scripted_demonstration(directory, teacher_registry)
            view = periodic_model_view(validate_episode(directory, teacher_registry=teacher_registry), max_label_age_game_ms=67)
            usable = Counter(decision["action_id"] for key, decision in view.decisions.items() if decision.get("source") == "scripted" and
                             key[0] == record["initial"]["primary_entity_id"] and decision.get("expert") is True and
                             view.transitions[key].get("usable_for_bc") is True)
            record.update(approved_actions={str(action): usable[action] for action in range(1, 12)},
                          approval_diagnostics=approved["scripted_expert_approval"]["diagnostics"],
                          approval_sha256=sha256(directory/"records-approved.jsonl"), model_alignment=view.summary["model_alignment"])
            if usable[record["expected_action_id"]] < 1:
                missing.append(record["name"])
            counts.update(usable)
            write_json(directory/"episode.json", record)
            write_json(args.output/"suite.json", suite)
        minimum = 2 if args.smoke or args.calibration else 24
        ready = not missing and all(counts[action] >= minimum for action in (2, 5, 11))
        suite.update(status="collected" if ready else "insufficient_physical_coverage", missing_expected_scenes=missing,
                     new_train_safe_peer_coverage={"counts": {str(action): counts[action] for action in (2, 5, 11)},
                                                   "minimum_per_action": minimum, "ready": ready}, resources=runner.samples,
                     dataset_phase="diagnostic_calibration_only_no_automatic_merge" if args.calibration else
                                   "diagnostic_smoke_only_no_automatic_merge" if args.smoke else "deferred_to_parent_canonical_dataset")
        proof["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(args.output/"manifest.json", proof)
        return 0 if ready else 2
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        suite["error"] = str(error)
        print(f"SafePeer collection failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        if runner is not None:
            suite["resources"] = runner.samples
            runner.terminate()
        write_json(args.output/"suite.json", suite)


if __name__ == "__main__":
    raise SystemExit(main())
