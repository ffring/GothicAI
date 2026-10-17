#!/usr/bin/env python3
"""Stage 5 real-engine arena collection on dev-ubuntu, without graphics/model.

This orchestration never computes collision, damage, victory or action masks.
The independent reporter reads native journals after every episode has closed.
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
from body_headless_suite import service_snapshot, sha256, write_json
from body_observe_suite import provenance


def setup_values(config: dict[str, Any], name: str) -> dict[str, int]:
    setup = config["setups"][name]
    return {key: setup[key] for key in ("distance_cm", "timeout_ms")}


def open_episode(env: EpisodeEnv, args: argparse.Namespace, config: dict[str, Any], *, name: str,
                 scope: str, task: str, setup: str, seed: int = 17, side: int = 0,
                 provider: str = "scripted", episode_index: int = 0, start: bool = True) -> dict[str, Any]:
    recurrent_before = list(env.recurrent_token) if env.recurrent_token is not None else None
    initial = env.reset(output=args.output / name, task=task, seed=seed, side=side,
                        provider=provider, **setup_values(config, setup))
    record = {"name": name, "scope": scope, "scenario": task, "setup_name": setup, "setup": setup_values(config, setup),
              "provider": provider, "seed": seed, "side": side, "episode_index": episode_index,
              "runner_pid": env.runner.process.pid, "episode_id": initial["episode_id"],
              "recurrent_before": recurrent_before, "recurrent_after": list(env.recurrent_token or ()),
              "ipc_file": f"{name}/ipc.jsonl", "body_events_file": f"{name}/body-control-events.jsonl",
              "mechanics_file": f"{name}/events.jsonl", "arena_events_file": f"{name}/arena-events.jsonl",
              "observations_file": f"{name}/observations.jsonl"}
    if start:
        env.start()
    return record


def finish_episode(env: EpisodeEnv, args: argparse.Namespace, record: dict[str, Any]) -> dict[str, Any]:
    record.update(env.finalize())
    write_json(args.output / record["name"] / "episode.json", record)
    return record


def choose_when_ready(env: EpisodeEnv, entity: int, action: int, *, max_substeps: int) -> int:
    """Wait for real eligibility and use an actual turn prerequisite if needed."""
    for _ in range(max_substeps):
        if env.eligible(entity, action):
            return env.act(entity, action)
        if action in (6, 7, 8) and env.eligible(entity, 2):
            env.wait_command(entity, env.act(entity, 2), max_substeps)
        if env.step().done:
            raise ArenaInfrastructureError("episode ended before the requested native decision was eligible")
    raise ArenaInfrastructureError("native body failed to recover a bounded decision opportunity")


def reset_mutation(env: EpisodeEnv, config: dict[str, Any]) -> dict[str, Any]:
    """Leave HP, motion and a live command dirty using only ordinary body API."""
    primary = env.arena["primary_entity_id"]
    budget = config["budgets"]["max_command_substeps"]
    attack = choose_when_ready(env, primary, 6, max_substeps=budget)
    env.wait_command(primary, attack, budget)
    # Jump back changes the real position while preserving facing. A strafe at
    # this short setup distance legitimately leaves the target outside vision,
    # which hands control back to native before we can leave an active wait.
    movement = choose_when_ready(env, primary, config["reset_movement_action"], max_substeps=budget)
    env.wait_command(primary, movement, budget)
    wait = choose_when_ready(env, primary, 11, max_substeps=budget)
    # End the episode while a real native wait is active. Reset must clear this
    # lease/execution and physical mutations, rather than just reload clean data.
    for _ in range(4):
        step = env.step()
        if any(event.get("entity_id") == primary and event.get("command_id") == wait and
               event.get("phase") == "started" for event in step.events):
            return {"attack_command_id": attack, "movement_command_id": movement, "active_wait_command_id": wait}
        if step.done:
            raise ArenaInfrastructureError("reset mutation ended before an actual wait started")
    raise ArenaInfrastructureError("reset mutation did not leave an actual native wait")


def policy_episode(env: EpisodeEnv, config: dict[str, Any], task: str) -> None:
    provider = ScriptedProvider()
    provider.reset(env.recurrent_token or ())
    primary, opponent = env.arena["primary_entity_id"], env.arena["opponent_entity_id"]
    budget = config["budgets"]["max_episode_substeps"]
    adversary_sent = False
    for _ in range(budget):
        if env.arena.get("terminated") or env.arena.get("truncated"):
            return
        if task == "block" and not adversary_sent:
            if env.eligible(opponent, 6):
                env.act(opponent, 6)
                adversary_sent = True
            elif env.eligible(opponent, 2):
                env.act(opponent, 2)
        row = env.latest.get(primary)
        if row is not None:
            action = provider.choose(row, task)
            if action is not None:
                env.act(primary, action)
        if env.step().done:
            return
    raise ArenaInfrastructureError("episode exceeded the declared game-step budget")


def native_episode(env: EpisodeEnv, config: dict[str, Any]) -> None:
    budget = config["budgets"]["max_episode_substeps"]
    advanced = 0
    while advanced < budget:
        step = env.step(min(30, budget-advanced))
        advanced += step.advanced_substeps
        if step.done:
            return
        if step.advanced_substeps <= 0:
            raise ArenaInfrastructureError("running native episode made no simulation progress")
    raise ArenaInfrastructureError("native episode exceeded its declared game-step budget")


def native_off(runner: ArenaRunner, args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    """Keep the historical melee scenario byte-for-byte for native regression."""
    output = args.output / "native-off"
    output.mkdir()
    runner.episode_log(output)
    runner.call({"cmd": "reset", "scenario": str(args.melee_scenario), "output_dir": str(output), "mode": "lockstep"})
    runner.call({"cmd": "body_watch", "enabled": False})
    while True:
        final = runner.call({"cmd": "advance", "substeps": 300})
        runner.sample()
        if final.get("done"):
            break
    runner.call({"cmd": "status"})
    archived = json.loads((args.native_reference / "manifest.json").read_text())
    if sha256(args.melee_scenario) != archived["scenario_sha256"]:
        raise ArenaInfrastructureError("native-off scenario differs from the archived baseline")
    candidate = {**manifest, **{key: archived[key] for key in ("scenario_sha256", "config_sha256")},
                 "kind": "body_arena_native_off", "headless": True, "mode": "lockstep", "body_control_enabled": False}
    write_json(output / "manifest.json", candidate)
    return {"name": "native-off", "events_file": "native-off/events.jsonl", "final": final}


def run_suite(args: argparse.Namespace, config: dict[str, Any]) -> int:
    result: dict[str, Any] = {"schema_version": 1, "kind": "body_arena_stage5_suite", "status": "infrastructure_failure",
                              "full_stage5_evidence": False, "reset_cycles": [], "cases": [], "paired": [],
                              "throughput": [], "resources": []}
    runner, manifest = None, {}
    suite_started = time.monotonic()
    try:
        manifest = provenance(args)
        manifest.update(kind="body_arena_stage5", observer_ownership="episode scoped scripted leases or native AI",
                        suite_scope="smoke" if args.smoke else "full", driver_sha256=sha256(Path(__file__)),
                        environment_adapter_sha256=sha256(Path(__file__).with_name("body_arena_env.py")),
                        reporter_sha256=sha256(Path(__file__).with_name("body_arena_report.py")))
        write_json(args.output / "manifest.json", manifest)
        runner = ArenaRunner(args)
        result["runner_pid"] = runner.process.pid
        if not args.smoke:
            result["native_off"] = native_off(runner, args, manifest)
        env = EpisodeEnv(runner, args.scenario, watchdog_ms=config["budgets"]["provider_watchdog_wall_ms"])
        reset_count = 3 if args.smoke else config["reset_count"]
        for index in range(reset_count):
            record = open_episode(env, args, config, name=f"reset-{index:03d}", scope="reset", task="reset_stress",
                                  setup="reset_stress", seed=config["reset_seed"], episode_index=index)
            record["mutation"] = reset_mutation(env, config)
            result["reset_cycles"].append(finish_episode(env, args, record))
            write_json(args.output / "suite.json", result)
            if index % 10 == 0 or index+1 == reset_count:
                print(f"body arena [reset] {index+1}/{reset_count} dirty episodes restored", flush=True)
        # The first load establishes the reference. Each of the 100 dirty
        # episodes needs a subsequent reset with the *same* seed/setup, so the
        # final cleanup is the hundredth restoration after an actual mutation.
        record = open_episode(env, args, config, name="reset-cleanup", scope="reset_cleanup", task="reset_stress",
                              setup="reset_stress", seed=config["reset_seed"], episode_index=reset_count, provider="native", start=False)
        result["reset_cleanup"] = finish_episode(env, args, record)
        write_json(args.output / "suite.json", result)
        for name, task, provider in (("distance", "distance", "scripted"), ("attack", "attack", "scripted"),
                                     ("block", "block", "scripted"), ("native_terminal", "native_duel", "native"),
                                     ("duel_timeout", "native_duel", "native")):
            record = open_episode(env, args, config, name=name, scope="micro", task=task, setup=name, provider=provider)
            policy_episode(env, config, task) if provider == "scripted" else native_episode(env, config)
            result["cases"].append(finish_episode(env, args, record))
            write_json(args.output / "suite.json", result)
            print(f"body arena [{name}] native {record['final'].get('outcome')} recorded", flush=True)
        if not args.smoke:
            for seed in config["paired_seeds"]:
                for side in config["paired_sides"]:
                    for provider in config["paired_providers"]:
                        name = f"paired-{seed}-{side}-{provider}"
                        record = open_episode(env, args, config, name=name, scope="paired", task="native_duel", setup="paired",
                                              seed=seed, side=side, provider=provider)
                        record["pair_id"] = f"seed-{seed}-side-{side}"
                        policy_episode(env, config, "native_duel") if provider == "scripted" else native_episode(env, config)
                        result["paired"].append(finish_episode(env, args, record))
                        write_json(args.output / "suite.json", result)
                        print(f"body arena [{name}] native {record['final'].get('outcome')} recorded", flush=True)
            count = config["setups"]["throughput"]["episodes"]
            for index in range(count):
                record = open_episode(env, args, config, name=f"throughput-{index:03d}", scope="throughput", task="native_duel",
                                      setup="throughput", seed=config["paired_seeds"][index % len(config["paired_seeds"])],
                                      side=index % 2, provider="scripted", episode_index=index)
                policy_episode(env, config, "native_duel")
                result["throughput"].append(finish_episode(env, args, record))
                write_json(args.output / "suite.json", result)
                print(f"body arena [throughput] {index+1}/{count} episodes recorded", flush=True)
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode != 0:
            raise ArenaInfrastructureError(f"runner did not close cleanly: {runner.process.returncode}")
        result.update(status="collected", suite_wall_s=time.monotonic()-suite_started,
                      services_after=service_snapshot(), resources=runner.samples)
        manifest.update(services_after=result["services_after"], completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        write_json(args.output / "manifest.json", manifest)
        write_json(args.output / "suite.json", result)
        if not args.smoke:
            completed = subprocess.run([sys.executable, str(Path(__file__).with_name("body_arena_report.py")), str(args.output),
                                        "--config", str(args.config), "--native-reference", str(args.native_reference),
                                        "--output-dir", str(args.output / "validated")], timeout=180, check=False)
            report = json.loads((args.output / "validated/body-arena.json").read_text())
            result["validation"] = {"exit_code": completed.returncode, "status": report["status"], "issues": report["issues"]}
            if completed.returncode != 0 or report.get("full_stage5_evidence") is not True:
                result["status"] = "failed"
                raise RuntimeError(f"independent arena evidence validation failed: {report['issues'][:8]}")
            result.update(status="passed", full_stage5_evidence=True)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
        print(f"body arena suite failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        if runner is not None:
            result["resources"] = runner.samples
            runner.terminate()
        write_json(args.output / "suite.json", result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "config", "scenario", "melee-scenario", "native-reference", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("native episodes run only on dev-ubuntu, never the Mac")
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    args.output.mkdir(exist_ok=True)
    config = json.loads(args.config.read_text())
    return run_suite(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
