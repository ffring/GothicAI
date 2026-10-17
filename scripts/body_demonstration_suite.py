#!/usr/bin/env python3
"""Collect physically proved scripted demonstrations in the Linux CPU arena.

The teacher supplies decisions; the shared native resolver, animations,
collision and damage supply their effects. Expert approval happens separately
after journals have closed, preserving the original live records.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from body_arena_env import ArenaInfrastructureError, ArenaRunner, EpisodeEnv
from body_demonstration_teacher import PROVIDER, TEACHER_ID, select_certificate
from body_headless_suite import service_snapshot, sha256, write_json
from body_observe_suite import provenance
from body_trajectory_recorder import RecorderError, TrajectoryRecorder, approve_scripted_demonstration
from body_trajectory_suite import bind_closed_sources, drain_capture


class DemonstrationRunner(ArenaRunner):
    """Separate provider namespace and reset-only geometry; old adapters stay intact."""
    def __init__(self, args):
        self.reset_extras: dict[str, Any] = {}
        super().__init__(args)

    def call(self, command: dict[str, Any]) -> dict[str, Any]:
        command = dict(command)
        if command.get("provider") == "stage5-scripted":
            command["provider"] = PROVIDER
        if command.get("cmd") == "reset" and isinstance(command.get("arena"), dict):
            command["arena"] = {**command["arena"], **self.reset_extras}
        # Rewriting occurs before the native request and its full IPC journal.
        return super().call(command)


class DemonstrationCycle:
    def __init__(self, env: EpisodeEnv, recorder: TrajectoryRecorder, config: dict[str, Any], output: Path,
                 *, variant_index: int, wall_deadline: float):
        self.env, self.recorder, self.config = env, recorder, config
        self.variant_index, self.wall_deadline = variant_index, wall_deadline
        self.primary = env.initial["primary_entity_id"]
        self.opponent = env.initial["opponent_entity_id"]
        self.history: list[dict[str, Any]] = []
        self.completed: set[tuple[int, int]] = set()
        self.certificates = (output / "teacher-certificates.jsonl").open("x", encoding="utf-8")
        self.certificate_seq = 0
        self.advanced = 2

    def context(self) -> dict[str, Any]:
        return {"goal": "defensive_cycle", "variant_index": self.variant_index,
                "completed_command_ids": [row["command_id"] for row in self.history],
                "last_completed_action": self.history[-1]["action_id"] if self.history else None}

    def step(self) -> None:
        if time.monotonic() > self.wall_deadline:
            raise RuntimeError("demonstration collection exceeded its declared wall budget")
        step = self.env.step()
        self.advanced += step.advanced_substeps
        drain_capture(self.env.runner, self.recorder, self.config, final=step.arena if step.done else None)
        if self.advanced > self.config["budgets"]["max_episode_substeps"]:
            raise RuntimeError("demonstration exceeded its game-step budget")
        for event in step.events:
            if event.get("phase") != "completed":
                continue
            identity = (event["entity_id"], event["command_id"])
            self.completed.add(identity)
            if event["entity_id"] == self.primary:
                self.history.append({"action_id": event["action_id"], "command_id": event["command_id"],
                                     "entity_id": self.primary, "t_game_ms": event["t_game_ms"], "phase": "completed"})

    def wait_ready(self, entity: int, action: int, *, teacher: bool) -> dict[str, Any]:
        for _ in range(self.config["budgets"]["max_command_substeps"]):
            row = self.env.latest.get(entity)
            if row is not None and row.get("periodic") is True and self.env.eligible(entity, action):
                if not teacher:
                    return row
                candidate = select_certificate(row, action, self.context(), self.history,
                                               command_id=self.env.command_ids[entity]+1,
                                               certificate_seq=self.certificate_seq+1,
                                               wall_monotonic=time.monotonic())
                if candidate is not None:
                    return row
            if self.env.arena.get("terminated") or self.env.arena.get("truncated"):
                raise RuntimeError(f"demonstration ended before action {action} passed its physical rule")
            self.step()
        raise RuntimeError(f"action {action} never passed its bounded fresh periodic guard")

    def wait_completed(self, entity: int, command: int) -> None:
        for _ in range(self.config["budgets"]["max_command_substeps"]):
            if (entity, command) in self.completed:
                return
            events = [event for event in self.env.events if event.get("entity_id") == entity and event.get("command_id") == command]
            cancelled = next((event for event in events if event.get("phase") in ("cancelled", "rejected")), None)
            if cancelled is not None:
                raise RuntimeError(f"demonstration action cancelled: {cancelled.get('reason')}")
            if self.env.arena.get("terminated") or self.env.arena.get("truncated"):
                raise RuntimeError("episode ended with an unfinished demonstration command")
            self.step()
        raise RuntimeError("demonstration command failed to complete within its native segment budget")

    def act(self, action: int) -> int:
        row = self.wait_ready(self.primary, action, teacher=True)
        self.certificate_seq += 1
        command = self.env.command_ids[self.primary]+1
        certificate = select_certificate(row, action, self.context(), self.history, command_id=command,
                                         certificate_seq=self.certificate_seq, wall_monotonic=time.monotonic())
        if certificate is None:
            raise RuntimeError("teacher guard changed without a simulation step")
        self.certificates.write(json.dumps(certificate, separators=(",", ":"), allow_nan=False)+"\n")
        self.certificates.flush()
        if self.env.act(self.primary, action) != command:
            raise RuntimeError("teacher command identity differs from its causal certificate")
        self.wait_completed(self.primary, command)
        return command

    def periodic_actor(self, entity: int) -> dict[str, Any]:
        for _ in range(self.config["budgets"]["max_command_substeps"]):
            row = self.env.latest.get(entity)
            if row is not None and row.get("periodic") is True and self.env.eligible(entity):
                return row
            if self.env.arena.get("terminated") or self.env.arena.get("truncated"):
                raise RuntimeError("demonstration ended before a fresh actor decision")
            self.step()
        raise RuntimeError("actor did not recover a fresh periodic decision")

    def ensure_attack_ready(self, action: int) -> None:
        corrections = 0
        for _ in range(self.config["budgets"]["max_command_substeps"]):
            row = self.periodic_actor(self.primary)
            target = row["target"]
            if target.get("in_w_range") is True and target.get("focus_5") is True and self.env.eligible(self.primary, action):
                return
            correction = 2 if target.get("focus_5") is False and self.env.eligible(self.primary, 2) else 1 if target.get("in_w_range") is False and self.env.eligible(self.primary, 1) else None
            if correction is None:
                self.step()
                continue
            if corrections >= self.config["budgets"]["max_range_corrections"]:
                raise RuntimeError("strikes required more than the declared physical range corrections")
            self.act(correction)
            corrections += 1
        raise RuntimeError("a real approach/turn did not restore the strike's range and focus")

    def opponent_attack_ready(self) -> None:
        # Knockback and parry can change range. Corrections by the adversary
        # preserve the primary's completed defense history used by its teacher.
        corrections = 0
        for _ in range(self.config["budgets"]["max_command_substeps"]):
            row = self.periodic_actor(self.opponent)
            if self.env.eligible(self.opponent, 6):
                return
            target = row["target"]
            correction = 2 if target.get("focus_5") is False and self.env.eligible(self.opponent, 2) else 1 if target.get("in_w_range") is False and self.env.eligible(self.opponent, 1) else None
            if correction is None:
                self.step()
                continue
            if corrections >= self.config["budgets"]["max_range_corrections"]:
                raise RuntimeError("adversary exceeded its declared real range corrections")
            self.wait_completed(self.opponent, self.env.act(self.opponent, correction))
            corrections += 1
        raise RuntimeError("adversary could not restore actual melee range")

    def stimulate_attack(self, defense: int) -> int:
        # The adversary is a test stimulus, never an approved expert role.
        self.opponent_attack_ready()
        self.wait_ready(self.primary, defense, teacher=False)
        self.wait_ready(self.opponent, 6, teacher=False)
        return self.env.act(self.opponent, 6)

    def establish_safe_distance(self) -> None:
        for attempt in range(self.config["budgets"]["max_opponent_retreats"]+1):
            row = self.periodic_actor(self.primary)
            target = row["target"]
            if target.get("fight_distance_cm", 0) >= max(350, target.get("g_range_cm", 350)):
                return
            if attempt == self.config["budgets"]["max_opponent_retreats"]:
                break
            self.wait_ready(self.opponent, 5, teacher=False)
            self.wait_completed(self.opponent, self.env.act(self.opponent, 5))
        raise RuntimeError("real adversary retreats did not establish the declared safe distance")

    def run(self) -> None:
        self.act(2)
        self.act(1)
        for action in (6, 7, 8):
            self.ensure_attack_ready(action)
            self.act(action)
        adversary = self.stimulate_attack(9)
        self.act(9)
        self.wait_completed(self.opponent, adversary)
        adversary = self.stimulate_attack(5)
        self.act(5)
        self.wait_completed(self.opponent, adversary)
        self.establish_safe_distance()
        self.act(11)
        # Existing native trajectories cover lateral movement and wait_200.
        # This supplement collects the declared deficits, with genuine extra
        # approach/turn corrections retained as separate proved decisions.
        if not set(self.config["coverage"]["actions"]).issubset(row["action_id"] for row in self.history):
            raise RuntimeError("demonstration cycle did not physically complete each declared class")
        while not (self.env.arena.get("terminated") or self.env.arena.get("truncated")):
            self.step()
        if self.env.arena.get("outcome") != "time_limit":
            raise RuntimeError("demonstration did not end as the declared time-limit truncation")

    def close(self) -> None:
        self.certificates.flush()
        os.fsync(self.certificates.fileno())
        self.certificates.close()


def declared_cases(config: dict[str, Any], *, smoke: bool = False) -> list[dict[str, Any]]:
    cases = []
    for split in ("train", "eval", "test"):
        for seed in config[split+"_seeds"]:
            for variant in config[split+"_variants"]:
                cases.append({"name": f"demo-{split}-{seed}-{variant}", "seed": seed, "split": split,
                              "variant_index": variant, "geometry": config["primary_geometry"], **config["variants"][variant]})
    for index, case in enumerate(config["near_wall_cases"]):
        group = json.dumps([config["setup_family"], case["seed"]], separators=(",", ":"))
        cases.append({**case, "variant_index": index, "geometry": "near_wall",
                      "split": config["inherited_group_assignments"][group], "center_offset_cm": [0, 0], "separation_yaw_deg": 0})
    for index, case in enumerate(config["ood_cases"]):
        cases.append({**case, "variant_index": index, "geometry": config["primary_geometry"], "split": "ood"})
    if smoke:
        selection = config["smoke"]
        return [case for case in cases if
                (case["geometry"] == config["primary_geometry"] and case["split"] == "train" and
                 case["seed"] == selection["primary_train_seed"] and case["variant_index"] in selection["primary_variants"]) or
                (case["geometry"] == "near_wall" and selection["include_all_near_wall_cases"]) or
                (case["split"] == "ood" and selection["include_all_ood_cases"])]
    return cases


def collect_case(env: EpisodeEnv, args, config, case, provenance_data, wall_deadline) -> dict[str, Any]:
    output = args.output / "episodes" / case["name"]
    env.runner.reset_extras = {key: case[key] for key in ("yaw_offset_deg", "center_offset_cm", "separation_yaw_deg", "geometry")}
    initial = env.reset(output=output, task="reset_stress", seed=case["seed"], side=case["side"],
                        distance_cm=case["distance_cm"], timeout_ms=config["budgets"]["episode_timeout_game_ms"], provider="scripted")
    teacher_file = Path(__file__).with_name("body_demonstration_teacher.py")
    (output / "teacher-source.py").write_bytes(teacher_file.read_bytes())
    (output / "teacher-config.json").write_bytes(args.config.read_bytes())
    metadata = {"provider": PROVIDER, "teacher": TEACHER_ID, "model": None, "scenario": "reset_stress",
                "seed": case["seed"], "side": case["side"], "ood": case["split"] == "ood",
                "group": {"setup_family": config["setup_family"], "seed": case["seed"]}, "declared_split": case["split"],
                "setup": env.runner.reset_extras, "config_sha256": sha256(args.config), "setup_sha256": sha256(args.scenario),
                "binary_sha256": provenance_data["binary_sha256"], "source_sha256": provenance_data["source_sha256"],
                "save_sha256": provenance_data["save_sha256"], "assets": provenance_data["assets"],
                "simulation": config["simulation"], "conditions": [],
                "teacher_provenance": {"teacher_id": TEACHER_ID, "code_file": "teacher-source.py",
                                       "config_file": "teacher-config.json", "certificates_file": "teacher-certificates.jsonl"}}
    recorder = TrajectoryRecorder(output, episode_id=case["name"], initial=initial, metadata=metadata,
                                  writer_capacity=config["budgets"]["writer_queue_capacity"])
    cycle = None
    try:
        env.runner.call({"cmd": "body_trajectory_watch", "enabled": True, "queue_capacity": config["budgets"]["native_queue_capacity"]})
        env.start()
        drain_capture(env.runner, recorder, config)
        cycle = DemonstrationCycle(env, recorder, config, output, variant_index=case["variant_index"], wall_deadline=wall_deadline)
        cycle.run()
        cycle.close()
        cycle = None
        episode = env.finalize()
        drain_capture(env.runner, recorder, config, final=episode["final"])
        status, controls = env.runner.call({"cmd": "status"}), env.runner.call({"cmd": "body_events"})
        env.runner.call({"cmd": "body_trajectory_watch", "enabled": False})
        drain_capture(env.runner, recorder, config, final=episode["final"])
        manifest = recorder.finish(episode["final"], source_files=[output / "teacher-source.py", output / "teacher-config.json",
                                  output / "teacher-certificates.jsonl"], runner_status=status, control_events=controls)
        record = {**case, "provider": PROVIDER, "capture": True, "episode_dir": str(output.relative_to(args.output)),
                  "manifest_file": f"episodes/{case['name']}/trajectory-manifest.json", "initial": initial,
                  "final": episode["final"], "counts": manifest["counts"]}
        write_json(output / "episode.json", record)
        return record
    finally:
        if cycle is not None:
            cycle.close()
        if not recorder.closed:
            recorder.abort()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "config", "scenario", "melee-scenario", "native-reference", "output", "existing-dataset"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("real demonstration collection runs only on dev-ubuntu")
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    args.output.mkdir(exist_ok=True)
    (args.output / "episodes").mkdir(exist_ok=True)
    config = json.loads(args.config.read_text())
    existing = json.loads(args.existing_dataset.read_text())
    if existing["groups"] != config["inherited_group_assignments"]:
        raise RuntimeError("declared group assignments differ from the immutable stage 6 dataset")
    teacher_file = Path(__file__).with_name("body_demonstration_teacher.py")
    registry = {"schema_version": 1, "teachers": {TEACHER_ID: {"provider": PROVIDER, "module": "body_demonstration_teacher",
                "code_sha256": sha256(teacher_file), "config_sha256": sha256(args.config), "controlled_role": "primary",
                "allowed_actions": config["coverage"]["actions"], "allowed_scenarios": ["reset_stress"]}}}
    write_json(args.output / "teacher-registry.json", registry)
    suite = {"schema_version": 1, "kind": "body_demonstrations_stage7_suite", "status": "infrastructure_failure",
             "suite_scope": "smoke" if args.smoke else "full", "episodes": [], "teacher_registry": "teacher-registry.json",
             "existing_dataset": str(args.existing_dataset), "existing_dataset_sha256": sha256(args.existing_dataset)}
    runner = None
    try:
        provenance_data = provenance(args)
        provenance_data.update(kind="body_demonstrations_stage7", suite_scope=suite["suite_scope"],
                               teacher_code_sha256=sha256(teacher_file), collector_sha256=sha256(Path(__file__)))
        write_json(args.output / "manifest.json", provenance_data)
        runner = DemonstrationRunner(args)
        suite["runner_pid"] = runner.process.pid
        env = EpisodeEnv(runner, args.scenario, watchdog_ms=config["budgets"]["provider_watchdog_wall_ms"])
        wall_deadline = time.monotonic()+config["budgets"]["collection_wall_timeout_s"]
        for case in declared_cases(config, smoke=args.smoke):
            record = collect_case(env, args, config, case, provenance_data, wall_deadline)
            suite["episodes"].append(record)
            write_json(args.output / "suite.json", suite)
            print(f"body demonstrations [{case['name']}] declared real actions completed", flush=True)
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode != 0:
            raise RecorderError("demonstration engine did not close cleanly")
        bind_closed_sources(args, suite["episodes"])
        for record in suite["episodes"]:
            output = args.output / record["episode_dir"]
            approved = approve_scripted_demonstration(output, registry)
            proof = approved["scripted_expert_approval"]
            counts = {action: sum(item["action_id"] == action for item in proof["approved"]) for action in range(1, 12)}
            if any(counts[action] < 1 for action in config["coverage"]["actions"]):
                raise RuntimeError(f"physical teacher proof failed: {record['name']}: {proof['diagnostics']}")
            record["approved_actions"] = counts
            record["approval_sha256"] = sha256(output / "records-approved.jsonl")
        # The parent BC suite builds the canonical combined dataset once,
        # after this collection has closed and its physical proofs are bound.
        suite.update(status="collected", services_after=service_snapshot(), resources=runner.samples)
        provenance_data.update(services_after=suite["services_after"], completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        write_json(args.output / "manifest.json", provenance_data)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        suite["error"] = str(error)
        print(f"body demonstration collection failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        if runner is not None:
            suite["resources"] = runner.samples
            runner.terminate()
        write_json(args.output / "suite.json", suite)


if __name__ == "__main__":
    raise SystemExit(main())
