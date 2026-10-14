#!/usr/bin/env python3
"""Collect and validate the first native body corpus on dev-ubuntu.

The engine enqueues causal snapshots and events in memory on its simulation
thread. A separate Python writer persists them. The engine computes every
observation, mask, chosen primitive and physical outcome.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from body_arena_env import ArenaInfrastructureError, ArenaRunner, EpisodeEnv, ScriptedProvider
from body_arena_suite import native_off
from body_headless_suite import service_snapshot, sha256, write_json
from body_observe_suite import provenance
from body_trajectory_recorder import RecorderError, TrajectoryRecorder


def drain_capture(runner: ArenaRunner, recorder: TrajectoryRecorder, config: dict[str, Any],
                  *, final: dict[str, Any] | None = None) -> dict[str, Any]:
    # The simulation is paused in lockstep while this queue is drained/written.
    # No unbounded queue, timestamp merge, second teacher call or label oracle.
    for _ in range(64):
        packet = runner.call({"cmd": "body_trajectory_events", "max": config["budgets"]["drain_max"]})
        recorder.ingest(packet, final=final)
        if packet["queue_size"] == 0:
            if recorder.last_raw_seq is not None and recorder.last_raw_seq != packet["record_seq_high_watermark"]:
                raise RecorderError("fully drained native capture does not reach its high watermark")
            return packet
    raise RecorderError("native capture queue exceeded bounded drain command budget")


def run_episode(env: EpisodeEnv, args: argparse.Namespace, config: dict[str, Any], provenance_data: dict[str, Any],
                *, name: str, task: str, seed: int, side: int, distance_cm: int, timeout_ms: int,
                provider: str, ood: bool = False, ood_reason: str = "", capture: bool = True) -> dict[str, Any]:
    output = args.output / "episodes" / name
    initial = env.reset(output=output, task=task, seed=seed, side=side, distance_cm=distance_cm,
                        timeout_ms=timeout_ms, provider=provider)
    metadata = {"provider": provider, "teacher": "native-engine" if provider == "native" else None,
                "model": None, "scenario": task, "seed": seed, "side": side, "ood": ood,
                "ood_reason": ood_reason, "group": {"setup_family": config["native_duels"]["setup_family"], "seed": seed},
                "setup": {"distance_cm": distance_cm, "timeout_ms": timeout_ms},
                "setup_sha256": sha256(args.scenario), "config_sha256": sha256(args.config),
                "source_sha256": provenance_data["source_sha256"], "binary_sha256": provenance_data["binary_sha256"],
                "save_sha256": provenance_data["save_sha256"], "assets": provenance_data["assets"],
                "simulation": config["simulation"], "conditions": []}
    recorder = None
    started = time.monotonic()
    try:
        if capture:
            recorder = TrajectoryRecorder(output, episode_id=name, initial=initial, metadata=metadata,
                                          writer_capacity=config["budgets"]["writer_queue_capacity"])
            env.runner.call({"cmd": "body_trajectory_watch", "enabled": True,
                             "queue_capacity": config["budgets"]["native_queue_capacity"]})
        env.start()
        if recorder is not None:
            drain_capture(env.runner, recorder, config, final=env.arena if env.arena.get("terminated") or env.arena.get("truncated") else None)
        scripted = ScriptedProvider()
        scripted.reset(env.recurrent_token or ())
        adversary_sent = False
        primary, opponent = initial["primary_entity_id"], initial["opponent_entity_id"]
        advanced = 2
        while not (env.arena.get("terminated") or env.arena.get("truncated")):
            if advanced >= config["budgets"]["max_episode_substeps"]:
                raise ArenaInfrastructureError("trajectory episode exceeded its game-step budget")
            if provider == "scripted":
                if task == "block" and not adversary_sent:
                    if env.eligible(opponent, 6):
                        env.act(opponent, 6)
                        adversary_sent = True
                    elif env.eligible(opponent, 2):
                        env.act(opponent, 2)
                row = env.latest.get(primary)
                if row is not None:
                    action = scripted.choose(row, task)
                    if action is not None:
                        env.act(primary, action)
            step = env.step(config["budgets"]["native_step_substeps"] if provider == "native" else 1)
            advanced += step.advanced_substeps
            if recorder is not None:
                drain_capture(env.runner, recorder, config, final=step.arena if step.done else None)
            if step.advanced_substeps <= 0 and not step.done:
                raise ArenaInfrastructureError("trajectory simulation made no progress")
        episode = env.finalize()
        # Stop tracking at the frozen terminal sample. Instrumentation censoring
        # stays explicit; stopping the recorder does not stop a physical action.
        if recorder is not None:
            drain_capture(env.runner, recorder, config, final=episode["final"])
            runner_status = env.runner.call({"cmd": "status"})
            control_events = env.runner.call({"cmd": "body_events"})
            env.runner.call({"cmd": "body_trajectory_watch", "enabled": False})
            final_packet = drain_capture(env.runner, recorder, config, final=episode["final"])
            recorded = recorder.finish(episode["final"], source_files=[], runner_status=runner_status, control_events=control_events)
        else:
            final_packet, recorded = None, None
        record = {"name": name, "provider": provider, "scenario": task, "seed": seed, "side": side,
                  "ood": ood, "capture": capture, "episode_dir": str(output.relative_to(args.output)),
                  "manifest_file": f"episodes/{name}/trajectory-manifest.json" if capture else None,
                  "raw_capture_file": f"episodes/{name}/raw-trajectory.jsonl" if capture else None,
                  "events_file": f"episodes/{name}/events.jsonl", "arena_events_file": f"episodes/{name}/arena-events.jsonl",
                  "ipc_file": f"episodes/{name}/ipc.jsonl", "initial": initial, "final": episode["final"],
                  "wall_s": time.monotonic()-started, "advanced_substeps": advanced,
                  "counts": recorded["counts"] if recorded else None, "capture_final": final_packet}
        write_json(output / "episode.json", record)
        if provider == "native" and (record["final"].get("outcome") != "death_or_down" or record["final"].get("terminated") is not True):
            raise RuntimeError(f"native demonstration is not a complete duel: {name}: {record['final'].get('outcome')}")
        if provider == "scripted" and record["final"].get("outcome") != "task_success":
            raise RuntimeError(f"scripted diagnostic microtask failed: {name}: {record['final'].get('outcome')}")
        return record
    finally:
        if recorder is not None and not recorder.closed:
            recorder.abort()


def bind_closed_sources(args: argparse.Namespace, episodes: list[dict[str, Any]]) -> None:
    """Native pre-reset journals are stable only after the engine has closed."""
    for episode in episodes:
        if not episode["capture"]:
            continue
        output = args.output / episode["episode_dir"]
        path = output / "trajectory-manifest.json"
        manifest = json.loads(path.read_text())
        files = {entry["path"]: entry for entry in manifest["source_files"]}
        for name in ("events.jsonl", "observations.jsonl", "body-control-events.jsonl", "arena-events.jsonl", "ipc.jsonl"):
            source = output / name
            if not source.is_file():
                raise RecorderError(f"closed native source is missing: {source}")
            files[name] = {"path": name, "sha256": sha256(source)}
        manifest["source_files"] = list(files.values())
        write_json(path, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "native-reference", "source", "engine-library", "config", "scenario", "melee-scenario", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("native corpus collection runs only on dev-ubuntu, never the Mac")
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    config = json.loads(args.config.read_text())
    args.output.mkdir(exist_ok=True)
    (args.output / "episodes").mkdir(exist_ok=True)
    suite = {"schema_version": 1, "kind": "body_trajectories_stage6_suite", "status": "infrastructure_failure",
             "full_stage6_evidence": False, "episodes": [], "resources": [], "suite_scope": "smoke" if args.smoke else "full"}
    runner, provenance_data = None, {}
    try:
        provenance_data = provenance(args)
        provenance_data.update(kind="body_trajectories_stage6", suite_scope=suite["suite_scope"],
                               observer_ownership="native teacher and explicitly non-expert scripted diagnostics",
                               recorder_sha256=sha256(Path(__file__).with_name("body_trajectory_recorder.py")),
                               suite_sha256=sha256(Path(__file__)),
                               reporter_sha256=sha256(Path(__file__).with_name("body_trajectory_report.py")),
                               dataset_validator_sha256=sha256(Path(__file__).with_name("body_trajectory_dataset.py")))
        write_json(args.output / "manifest.json", provenance_data)
        runner = ArenaRunner(args)
        suite["runner_pid"] = runner.process.pid
        if not args.smoke:
            suite["native_off"] = native_off(runner, args, provenance_data)
        env = EpisodeEnv(runner, args.scenario, watchdog_ms=config["budgets"]["provider_watchdog_wall_ms"])
        # A cold load differs in shared presentation RNG draws from a warmed
        # resource cache. Preserve one complete OFF duel before the strict
        # OFF/ON comparison, including the otherwise unused fallback variant.
        suite["recording_warmup"] = []
        for index in range(config["parity_warmup_episodes"]):
            warmup = run_episode(env, args, config, provenance_data, name=f"parity-warmup-{index}",
                                 task="native_duel", provider="native", capture=False, **config["parity"])
            suite["recording_warmup"].append(warmup)
            write_json(args.output / "suite.json", suite)
        suite["recording_off"] = run_episode(env, args, config, provenance_data, name="parity-off", task="native_duel",
                                             provider="native", capture=False, **config["parity"])
        native = config["native_duels"]
        seeds = native["seeds"][:1] if args.smoke else native["seeds"]
        for seed in seeds:
            for side in native["sides"]:
                record = run_episode(env, args, config, provenance_data, name=f"native-{seed}-{side}", task="native_duel",
                                     provider="native", seed=seed, side=side, distance_cm=native["distance_cm"], timeout_ms=native["timeout_ms"])
                suite["episodes"].append(record)
                if seed == config["parity"]["seed"] and side == config["parity"]["side"]:
                    suite["recording_on"] = record
                write_json(args.output / "suite.json", suite)
                print(f"body trajectories [{record['name']}] {record['counts']} native rows recorded", flush=True)
        for case in config["micro_cases"]:
            record = run_episode(env, args, config, provenance_data, name=f"scripted-{case['name']}", task=case["task"],
                                 provider="scripted", **{key: case[key] for key in ("seed", "side", "distance_cm", "timeout_ms")})
            suite["episodes"].append(record)
            write_json(args.output / "suite.json", suite)
            print(f"body trajectories [{record['name']}] explicitly non-expert rows recorded", flush=True)
        if not args.smoke:
            for case in config["ood_cases"]:
                record = run_episode(env, args, config, provenance_data, name=case["name"], task=case["task"], provider="native",
                                     ood=True, ood_reason=case["ood_reason"],
                                     **{key: case[key] for key in ("seed", "side", "distance_cm", "timeout_ms")})
                suite["episodes"].append(record)
                write_json(args.output / "suite.json", suite)
                print(f"body trajectories [{record['name']}] separate OOD rows recorded", flush=True)
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode != 0:
            raise RecorderError(f"native runner did not close cleanly: {runner.process.returncode}")
        bind_closed_sources(args, suite["episodes"])
        from body_trajectory_dataset import build_dataset, validate_episode
        for episode in suite["episodes"]:
            validate_episode(args.output / episode["episode_dir"])
        if not args.smoke:
            dataset = build_dataset([args.output / episode["episode_dir"] for episode in suite["episodes"]],
                                    args.output / "dataset", split_seed=config["splits"]["seed"],
                                    min_labels=config["coverage"]["min_native_labels_per_class"],
                                    included_actions=config["coverage"]["included_actions"])
            suite["dataset"] = {"validated": dataset["validated"], "episode_count": dataset["episode_count"],
                                "diagnostic_bc_coverage_ready": dataset["diagnostic_bc_coverage_ready"],
                                "path": "dataset/dataset.json"}
        suite.update(status="collected", services_after=service_snapshot(), resources=runner.samples)
        provenance_data.update(services_after=suite["services_after"], completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        write_json(args.output / "manifest.json", provenance_data)
        write_json(args.output / "suite.json", suite)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        suite["error"] = str(error)
        print(f"body trajectories suite failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        if runner is not None:
            suite["resources"] = runner.samples
            runner.terminate()
        write_json(args.output / "suite.json", suite)


if __name__ == "__main__":
    raise SystemExit(main())
