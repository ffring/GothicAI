#!/usr/bin/env python3
"""Collect source-bound reactive block demonstrations on the Linux runner.

All preparation remains in raw/canonical capture. The explicit prepared scene
boundary excludes it only from the recurrent training view. No models run here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import time
from types import SimpleNamespace
from typing import Any

from body_arena_env import ArenaInfrastructureError, ArenaRunner, EpisodeEnv, LEASE_KEYS
from body_bc_report import json_digest, native_wire_relation
from body_defence_teacher import PROVIDER, TEACHER_ID, select_certificate
from body_headless_suite import sha256, write_json
from body_observe_suite import provenance
from body_trajectory_recorder import RecorderError, TrajectoryRecorder, approve_scripted_demonstration
from body_trajectory_suite import bind_closed_sources, drain_capture
import body_trajectory_dataset as feature_module


def append_json(stream, record):
    stream.write(json.dumps(record, separators=(",", ":"), allow_nan=False)+"\n")
    stream.flush()


def observation_key(row):
    return tuple(row[key] for key in (*LEASE_KEYS, "observation_seq"))


def prepared_anchor_binding(captured, anchor, normalizer):
    """Bind both original encodings; never replace the received command row."""
    relation = native_wire_relation(captured["observation"], anchor, max_ulps=2)
    features = [feature_module.pack_features(row, normalizer, sequence_dt_game_ms=0)
                for row in (captured["observation"], anchor)]
    if any(len(row) != 184 for row in features):
        raise RecorderError("prepared anchor does not use the frozen 184-feature schema")
    packed = [struct.pack("<"+"f"*len(row), *row) for row in features]
    if packed[0] != packed[1]:
        raise RecorderError("prepared anchor JSON roundtrip changed actual model float32 inputs")
    return {"schema_version": 1, "kind": "prepared_anchor_wire_binding", "raw_capture_seq": captured["raw_capture_seq"],
            "wire_anchor_sha256": json_digest(anchor), "capture_anchor_sha256": json_digest(captured["observation"]),
            "relation": relation, "feature_count": 184, "first_sequence_dt_game_ms": 0,
            "capture_float32_features_sha256": hashlib.sha256(packed[0]).hexdigest(),
            "wire_float32_features_sha256": hashlib.sha256(packed[1]).hexdigest(),
            "original_wire_and_capture_preserved": True}


def declared_cases(config: dict[str, Any], *, smoke: bool = False) -> list[dict[str, Any]]:
    result = []
    groups = (("train", config["train_seeds"][:1] if smoke else config["train_seeds"]),) if smoke else (
        ("train", config["train_seeds"]), ("eval", config["eval_seeds"]),
        ("test", config["reserved_test_seeds"]), ("ood", config["ood_seeds"]))
    for split, seeds in groups:
        for seed in seeds:
            for side in config["sides"]:
                for distance in config["distances_cm"]:
                    result.append({"name": f"defence-{split}-{seed}-side{side}-distance{distance}",
                                   "split": split, "seed": seed, "side": side, "distance_cm": distance,
                                   "task": "reset_stress", "timeout_ms": config["timeout_game_ms"], "geometry": "open"})
    if len({case["name"] for case in result}) != len(result):
        raise RecorderError("reactive defence case IDs are not unique")
    return result


def load_preparation(args, config):
    binding = config["preparation_source"]
    if (args.reaction_helper.name != "body_block_reaction.py" or
            sha256(args.reaction_helper) != binding["helper_sha256"] or
            sha256(args.reaction_config) != binding["config_sha256"] or
            sha256(args.normalizer) != config["reference"]["normalizer_sha256"]):
        raise RecorderError("frozen reaction preparation helper, config or normalization differs")
    captured_config = json.loads(args.reaction_config.read_text())
    if captured_config["preparation"] != config["preparation"]:
        raise RecorderError("reactive demonstration preparation differs from the measured frozen protocol")
    spec = importlib.util.spec_from_file_location("body_defence_frozen_reaction_helper", args.reaction_helper)
    if spec is None or spec.loader is None:
        raise RecorderError("cannot load the explicit frozen preparation helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    api = SimpleNamespace(append_json=append_json, write_json=write_json, observation_key=observation_key,
                          sha256=sha256, feature_module=feature_module)
    return module, api, json.loads(args.normalizer.read_text())


class DefenceRunner(ArenaRunner):
    def call(self, command):
        command = dict(command)
        if command.get("provider") == "stage5-scripted":
            command["provider"] = PROVIDER
        if command.get("cmd") == "reset":
            command["arena"] = {**command["arena"], "reaction_hold": True, "geometry": "open"}
        return super().call(command)


class DefenceEpisodeEnv(EpisodeEnv):
    """Small no-model adapter for the measured prepare/activate helper."""
    def __init__(self, runner, scenario, *, watchdog_ms):
        super().__init__(runner, scenario, watchdog_ms=watchdog_ms)
        self.pulled, self.command_responses = [], []
        self.native_sim_step = 0
        self.recorder = None
        self.capture_config = None

    def reset(self, **kwargs):
        self.pulled.clear()
        self.command_responses.clear()
        self.native_sim_step = 0
        return super().reset(**kwargs)

    def _arena(self, response):
        self.native_sim_step = response.get("sim_step", self.native_sim_step)
        return super()._arena(response)

    def pull(self):
        rows = super().pull()
        self.pulled.extend(rows)
        return rows

    def act(self, entity, action):
        if self.arena.get("terminated") or self.arena.get("truncated") or self.arena.get("infrastructure"):
            raise ArenaInfrastructureError("terminal defence episode cannot accept a command")
        if action not in (6, 9) or not self.eligible(entity, action):
            raise ArenaInfrastructureError("defence preparation selected an unavailable ordinary primitive")
        row = self.latest[entity]
        self.command_ids[entity] += 1
        request = {"cmd": "body_action", **self.leases[entity], "provider": PROVIDER,
                   "goal_version": row["goal"]["version"], "target_entity_id": row["target"]["entity_id"],
                   "command_id": self.command_ids[entity], "action_id": action, "observation_seq": row["observation_seq"],
                   "issued_game_ms": row["t_game_ms"], "ttl_ms": 1000}
        response = self.runner.call(request)
        self.command_responses.append({"request": request, "response": response})
        if response.get("accepted") is not True:
            raise ArenaInfrastructureError(f"actual defence command rejected: {response.get('reason')}")
        self.decisions.append({**request, "execution_id": response["execution_id"]})
        self.latest.pop(entity, None)
        return request["command_id"]

    try_act = act

    def step(self, substeps=1):
        step = super().step(substeps)
        if self.recorder is not None:
            drain_capture(self.runner, self.recorder, self.capture_config, final=step.arena if step.done else None)
        return step


def registry(args, config):
    return {"schema_version": 1, "teachers": {TEACHER_ID: {
        "provider": PROVIDER, "module": "body_defence_teacher", "code_sha256": sha256(Path(__file__).with_name("body_defence_teacher.py")),
        "config_sha256": sha256(args.config), "controlled_role": "primary", "allowed_actions": [9],
        "allowed_scenarios": ["reset_stress"]}}}


def collect(env, args, config, case, proof, helper, api, normalizer, deadline):
    directory = args.output/"episodes"/case["name"]
    initial = env.reset(output=directory, task=case["task"], seed=case["seed"], side=case["side"],
                        distance_cm=case["distance_cm"], timeout_ms=case["timeout_ms"], provider="scripted")
    for source, destination in ((Path(__file__).with_name("body_defence_teacher.py"), "teacher-source.py"),
                                (args.config, "teacher-config.json"), (args.reaction_helper, "preparation-helper.py"),
                                (args.reaction_config, "preparation-config.json"), (args.normalizer, "preparation-normalizer.json")):
        (directory/destination).write_bytes(source.read_bytes())
    metadata = {"provider": PROVIDER, "teacher": TEACHER_ID, "model": None, "scenario": case["task"],
                "seed": case["seed"], "side": case["side"], "ood": case["split"] == "ood", "declared_split": case["split"],
                "group": {"setup_family": config["setup_family"], "seed": case["seed"]},
                "setup": {"reaction_hold": True, "geometry": "open", "distance_cm": case["distance_cm"], "timeout_ms": case["timeout_ms"]},
                "config_sha256": sha256(args.config), "setup_sha256": sha256(args.scenario),
                **{key: proof[key] for key in ("binary_sha256", "source_sha256", "save_sha256", "assets")},
                "simulation": config["simulation"], "conditions": ["actual_incoming_attack"],
                "collection_scope": "smoke" if args.smoke else "full", "opponent_provenance": "scripted_actual_front_attack_nonexpert",
                "preparation_helper_file": "preparation-helper.py", "preparation_helper_sha256": sha256(args.reaction_helper),
                "preparation_normalizer_file": "preparation-normalizer.json",
                "teacher_provenance": {"teacher_id": TEACHER_ID, "code_file": "teacher-source.py", "config_file": "teacher-config.json",
                                       "certificates_file": "teacher-certificates.jsonl"}}
    recorder = TrajectoryRecorder(directory, episode_id=case["name"], initial=initial, metadata=metadata,
                                  writer_capacity=config["budgets"]["writer_queue_capacity"])
    env.recorder, env.capture_config = recorder, config
    primary, opponent = initial["primary_entity_id"], initial["opponent_entity_id"]
    try:
        with (directory/"preparation-journal.jsonl").open("x") as journal, (directory/"teacher-certificates.jsonl").open("x") as certificates:
            env.runner.call({"cmd": "body_trajectory_watch", "enabled": True, "queue_capacity": config["budgets"]["native_queue_capacity"]})
            anchor, preparation = helper.prepare(env, case, config, api, journal, normalizer)
            drain_capture(env.runner, recorder, config)
            captured = recorder.periodic_refs.get(tuple(anchor[key] for key in ("session_epoch", "world_epoch", "control_generation", "entity_id", "observation_seq")))
            if captured is None:
                raise RecorderError("actual prepared periodic anchor is missing from ordered native capture")
            # RapidJSON's captured encoding may differ from the received JSON
            # by two representable double steps. Identity, keys and masks remain
            # exact; both originals and their model-input equality are recorded.
            write_json(directory/"anchor-binding.json", prepared_anchor_binding(captured, anchor, normalizer))
            metadata["prepared_anchor_binding_file"] = "anchor-binding.json"
            metadata["model_sequence_start"] = {"schema_version": 1, "kind": "prepared_reaction_anchor",
                **{key: anchor[key] for key in ("entity_id", "session_epoch", "world_epoch", "control_generation", "t_game_ms")},
                "raw_capture_seq": captured["raw_capture_seq"], "preparation_file": "preparation.json", "activation_file": "activation.json",
                "anchor_binding_file": "anchor-binding.json"}
            activation = helper.activate(env, "scripted", anchor, preparation, api, journal)
            # Activation changes neither the snapshot nor either body's owner.
            for entity in (primary, opponent):
                status = env.body_status(entity)
                if status["control_generation"] != env.leases[entity]["control_generation"] or status.get("owner") != "scripted":
                    raise RecorderError("reaction activation lost a held scripted body lease")
            cert = select_certificate(anchor, command_id=env.command_ids[primary]+1, wall_monotonic=time.monotonic())
            if cert is None:
                raise RecorderError("genuine prepared windup does not permit the observed block teacher")
            append_json(certificates, cert)
            command = env.act(primary, 9)
            if command != cert["command_id"]:
                raise RecorderError("block certificate and accepted native command differ")
            history, substeps = [], 0
            while not (env.arena.get("terminated") or env.arena.get("truncated")):
                if time.monotonic() > deadline or substeps >= config["budgets"]["max_episode_substeps"]:
                    raise RecorderError("reactive defence exceeded its declared wall or substep budget")
                step = env.step()
                substeps += step.advanced_substeps
                if step.advanced_substeps <= 0 and not step.done:
                    raise RecorderError("reactive defence simulation made no physical progress")
                history.extend(event for event in step.events if event.get("entity_id") == primary and event.get("command_id") == command and
                               event.get("phase") in ("completed", "cancelled", "rejected"))
            episode = env.finalize()
            drain_capture(env.runner, recorder, config, final=episode["final"])
            status, controls = env.runner.call({"cmd": "status"}), env.runner.call({"cmd": "body_events"})
            env.runner.call({"cmd": "body_trajectory_watch", "enabled": False})
            drain_capture(env.runner, recorder, config, final=episode["final"])
            certificates.flush()
            os.fsync(certificates.fileno())
        manifest = recorder.finish(episode["final"], source_files=[directory/name for name in (
            "teacher-source.py", "teacher-config.json", "teacher-certificates.jsonl", "preparation-helper.py", "preparation-config.json",
            "preparation.json", "activation.json", "preparation-journal.jsonl", "anchor-binding.json", "preparation-normalizer.json")], runner_status=status, control_events=controls)
        record = {**case, "provider": PROVIDER, "capture": True, "episode_dir": str(directory.relative_to(args.output)),
                  "manifest_file": f"episodes/{case['name']}/trajectory-manifest.json", "initial": initial, "final": episode["final"],
                  "prepared_start": activation["prepared_start"], "model_sequence_start": metadata["model_sequence_start"],
                  "counts": manifest["counts"], "primary_completed_commands": sum(event["phase"] == "completed" for event in history),
                  "requested_primary_commands": 1, "preparation_quality_credit": False}
        write_json(directory/"episode.json", record)
        return record
    finally:
        env.recorder = None
        if not recorder.closed:
            recorder.abort()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "config", "scenario", "melee-scenario",
                 "native-reference", "output", "reaction-helper", "reaction-config", "normalizer"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("actual reactive defence collection runs only on dev-ubuntu")
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    args.output.mkdir()
    (args.output/"episodes").mkdir()
    config = json.loads(args.config.read_text())
    teacher_registry = registry(args, config)
    write_json(args.output/"teacher-registry.json", teacher_registry)
    suite = {"schema_version": 1, "kind": "body_reactive_defence_demonstrations", "status": "infrastructure_failure",
             "suite_scope": "smoke" if args.smoke else "full", "episodes": [], "teacher_registry": "teacher-registry.json",
             "new_group_assignments": config["group_assignments"], "automatic_training_admission": False}
    runner = None
    try:
        helper, api, normalizer = load_preparation(args, config)
        proof = provenance(args)
        proof.update(kind=suite["kind"], suite_scope=suite["suite_scope"], collector_sha256=sha256(Path(__file__)),
                     teacher_code_sha256=sha256(Path(__file__).with_name("body_defence_teacher.py")),
                     preparation_source=config["preparation_source"], models_run=False)
        write_json(args.output/"manifest.json", proof)
        runner = DefenceRunner(args)
        env = DefenceEpisodeEnv(runner, args.scenario, watchdog_ms=config["budgets"]["provider_watchdog_wall_ms"])
        deadline = time.monotonic()+config["budgets"]["collection_wall_timeout_s"]
        for case in declared_cases(config, smoke=args.smoke):
            suite["episodes"].append(collect(env, args, config, case, proof, helper, api, normalizer, deadline))
            write_json(args.output/"suite.json", suite)
            print(f"reactive defence [{case['name']}] captured", flush=True)
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode != 0:
            raise RecorderError("reactive defence backend did not close successfully")
        bind_closed_sources(args, suite["episodes"])
        for record in suite["episodes"]:
            directory = args.output/record["episode_dir"]
            manifest = approve_scripted_demonstration(directory, teacher_registry)
            evidence = manifest["scripted_expert_approval"]
            record.update(approved_actions={action: sum(row["action_id"] == action for row in evidence["approved"]) for action in range(1, 12)},
                          approval_diagnostics=evidence["diagnostics"], approval_sha256=sha256(directory/"records-approved.jsonl"))
            write_json(args.output/"suite.json", suite)
        counts = {split: sum(record["approved_actions"][9] for record in suite["episodes"] if record["split"] == split)
                  for split in ("train", "eval", "test", "ood")}
        required = {"train": len(config["distances_cm"])*len(config["sides"])*(1 if args.smoke else len(config["train_seeds"]))}
        if not args.smoke:
            required.update({split: len(config["distances_cm"])*len(config["sides"])*len(config[key])
                             for split, key in (("eval", "eval_seeds"), ("test", "reserved_test_seeds"), ("ood", "ood_seeds"))})
        suite["reactive_block_coverage"] = {"counts": counts, "required": required, "ready": all(counts[key] >= value for key, value in required.items()),
                                            "mechanics": "completed9 plus actual paired blocked melee hit; no HP damage"}
        if not suite["reactive_block_coverage"]["ready"]:
            raise RecorderError("genuine reactive block coverage is incomplete; failed raw episodes are retained")
        suite.update(status="collected", resources=runner.samples, dataset_phase="deferred_to_parent_canonical_dataset")
        proof["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(args.output/"manifest.json", proof)
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        suite["error"] = str(error)
        print(f"reactive defence demonstrations failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        if runner is not None:
            suite["resources"] = runner.samples
            runner.terminate()
        write_json(args.output/"suite.json", suite)


if __name__ == "__main__":
    raise SystemExit(main())
