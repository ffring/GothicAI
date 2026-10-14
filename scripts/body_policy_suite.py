#!/usr/bin/env python3
"""Stage 7 frozen-policy diagnostics in the real dev-ubuntu headless arena.

Only periodic observations advance the models. Commands use the latest exact
periodic native row, through the same physical arbiter used by stage 5. The
suite records outcomes without converting poor BC behavior into a pass flag.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from body_arena_env import ArenaInfrastructureError, ArenaRunner, EpisodeEnv, EpisodeStep, LEASE_KEYS
from body_headless_suite import process_sample, service_snapshot, sha256, write_json
from body_observe_suite import provenance

PACKAGE = Path(__file__).resolve().parents[1] / "body-service/src"
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))
from gothic_body.runtime import PolicyRuntime

PROFILE = {"sampling_version": "periodic-v1", "decision_anchor": "latest_periodic_exact",
           "feature_version": "body-periodic-numeric-v1", "periodic_rate_hz": 20,
           "event_only_model_updates": False, "event_only_commands": False}


def native_fallback_boundary(initial, final, control, mechanics):
    """Classify actual native execution, allowing ordinary terminal cleanup.

    Native choice/consume hooks cannot be emitted by the body override. A
    generic animation attempt can carry an old choice ID while selecting Idle,
    and therefore does not establish native continuation at the final frame.
    """
    restored = [row for row in control if row.get("entity_id") == initial["primary_entity_id"] and
                row.get("phase") == "ownership" and row.get("reason") == "native_restored"]
    for row in restored:
        if any(row.get(key) != final[key] for key in ("session_epoch", "world_epoch")) or \
                type(row.get("t_game_ms")) is not int or row["t_game_ms"] < 0:
            raise ArenaInfrastructureError("native restoration has invalid episode identity/time")
    before = [row for row in restored if row["t_game_ms"] < final["game_ms"]]
    at_final = [row for row in restored if row["t_game_ms"] == final["game_ms"]]
    continued = []
    if at_final:
        ids = {row.get("actor_id") for row in mechanics if
               row.get("phase") in ("arena_actor_mapping", "observation_actor_mapping") and
               row.get("observation_entity_id") == initial["primary_entity_id"]}
        if len(ids) != 1 or None in ids:
            raise ArenaInfrastructureError("final native restoration lacks unique physical actor mapping")
        continued = [row for row in mechanics if row.get("actor_id") in ids and
                     row.get("t_game") == final["game_ms"] and
                     row.get("phase") in ("native_choice", "native_consume")]
    return {"restorations_before_final": before, "same_final_native_continuations": continued}


class PolicyArenaRunner(ArenaRunner):
    def __init__(self, args):
        self.arena_options: dict[str, Any] = {}
        super().__init__(args)

    def call(self, command):
        if command.get("cmd") == "reset" and isinstance(command.get("arena"), dict):
            command = {**command, "arena": {**command["arena"], **self.arena_options}}
        return super().call(command)


class PolicyEpisodeEnv(EpisodeEnv):
    """Local policy adapter; older stage 5 provider semantics remain unchanged."""
    def __init__(self, runner, scenario, *, owner_provider, watchdog_ms=5000):
        super().__init__(runner, scenario, watchdog_ms=watchdog_ms)
        self.owner_provider = owner_provider
        self.pulled: list[dict[str, Any]] = []
        self.command_responses: list[dict[str, Any]] = []
        self.fallbacks: list[dict[str, Any]] = []

    def reset(self, **kwargs):
        self.pulled.clear()
        self.command_responses.clear()
        self.fallbacks.clear()
        fair_native = kwargs.get("provider") == "native_with_scripted_opponent"
        if fair_native:
            kwargs = {**kwargs, "provider": "native"}
        initial = super().reset(**kwargs)
        if fair_native:
            self.acquire(initial["opponent_entity_id"])
            self.provider = "native_with_scripted_opponent"
        return initial

    def pull(self):
        rows = super().pull()
        self.pulled.extend(rows)
        return rows

    def acquire(self, entity):
        actor = self.body_status(entity)
        response = self.runner.call({"cmd": "body_acquire", **{key: actor[key] for key in LEASE_KEYS},
                                     "target_entity_id": actor["target_entity_id"], "provider": self.owner_provider,
                                     "body_control": True, "mode": "lockstep", "timeout_ms": self.watchdog_ms})
        if response.get("accepted") is not True:
            raise ArenaInfrastructureError(f"policy acquire rejected: {response.get('reason')}")
        self.leases[entity] = {key: response[key] for key in LEASE_KEYS}
        self.command_ids[entity] = 0
        self.latest.pop(entity, None)

    def step(self, substeps=1):
        if self.identity is None:
            raise ArenaInfrastructureError("step requires successful reset")
        if self.arena.get("terminated") or self.arena.get("truncated"):
            return EpisodeStep(self.arena, (), (), 0)
        for entity, lease in list(self.leases.items()):
            response = self.runner.call({"cmd": "body_heartbeat", **lease, "provider": self.owner_provider})
            if response.get("accepted") is not True:
                # A native visibility loss can release ownership at a safe
                # boundary before the microtask timeout. It is a candidate
                # failure, with all subsequent native behavior uncredited.
                actor = self.body_status(entity)
                if actor.get("control_generation") != lease["control_generation"]:
                    self.leases.pop(entity)
                    self.fallbacks.append({"entity_id": entity, "reason": response.get("reason"),
                                           "t_game_ms": self.arena.get("game_ms"), "kind": "native_ownership_loss"})
                elif response.get("reason") == "release_pending" and actor.get("release_pending") is True:
                    # Native release is deferred through a protected pose or a
                    # locomotion stop. Keep the exact lease until restoration,
                    # process its locked history, and let the bounded episode
                    # advance toward the real guard's safe boundary.
                    continue
                else:
                    raise ArenaInfrastructureError(f"policy heartbeat rejected: {response.get('reason')}")
        response = self.runner.call({"cmd": "arena_step", "substeps": substeps})
        arena = self._arena(response)
        observations = self.pull()
        events = self.runner.call({"cmd": "body_events"}).get("events")
        if not isinstance(events, list):
            raise ArenaInfrastructureError("body event packet has no events")
        self.events.extend(events)
        return EpisodeStep(arena, observations, tuple(events), response.get("advanced_substeps", 0))

    def act(self, entity, action):
        if self.arena.get("terminated") or self.arena.get("truncated") or self.arena.get("infrastructure"):
            raise ArenaInfrastructureError("terminal episode cannot accept a new decision")
        if type(action) is not int or not 1 <= action <= 11 or not self.eligible(entity, action):
            raise ArenaInfrastructureError("policy selected unavailable new primitive")
        row = self.latest[entity]
        self.command_ids[entity] += 1
        command_id = self.command_ids[entity]
        request = {"cmd": "body_action", **self.leases[entity], "provider": self.owner_provider,
                   "goal_version": row["goal"]["version"], "target_entity_id": row["target"]["entity_id"],
                   "command_id": command_id, "action_id": action, "observation_seq": row["observation_seq"],
                   "issued_game_ms": row["t_game_ms"], "ttl_ms": 1000}
        response = self.runner.call(request)
        self.command_responses.append({"request": request, "response": response})
        if response.get("accepted") is not True:
            raise ArenaInfrastructureError(f"fresh policy decision rejected: {response.get('reason')}")
        self.decisions.append({**request, "execution_id": response["execution_id"]})
        self.latest.pop(entity, None)
        return command_id

    def release_to_native(self, entity, reason):
        lease = self.leases.pop(entity, None)
        if lease is not None:
            response = self.runner.call({"cmd": "body_release", **lease, "provider": self.owner_provider})
            self.fallbacks.append({"entity_id": entity, "reason": reason, "kind": "model_fallback",
                                   "t_game_ms": self.arena.get("game_ms"), "request": {
                                       "cmd": "body_release", **lease, "provider": self.owner_provider}, "response": response})


def record_model_rows(env, policy, primary, stream, journal, stats, *, command_enabled):
    rows, env.pulled = env.pulled, []
    last_response = None
    for row in rows:
        if row["entity_id"] != primary:
            continue
        if row.get("periodic") is not True:
            stats["event_only_observations"] += 1
            if row.get("can_decide") is True:
                stats["event_only_eligible_observations"] += 1
            continue
        stats["periodic_observations"] += 1
        if row.get("can_decide") is True and row.get("action_mask_valid") is True:
            stats["eligible_periodic_observations"] += 1
        lease = env.leases.get(primary)
        if lease is None or any(row.get(key) != lease[key] for key in LEASE_KEYS):
            stats["unowned_periodic_observations"] += 1
            journal.write(json.dumps({"kind": "excluded_observation", "reason": "outside_policy_generation",
                                      "observation": row, "profile": PROFILE}, separators=(",", ":"))+"\n")
            continue
        stream[primary] += 1
        request = {"stream_seq": stream[primary], "observation": row, "source": "periodic_observer",
                   "now_game_ms": env.arena["game_ms"], "checkpoint_sha256": policy.checkpoint_sha256,
                   "command_enabled": command_enabled,
                   "memory_only": not command_enabled or row.get("can_decide") is not True}
        response = policy.process(request)
        journal.write(json.dumps({"kind": "model_input", "request": request, "response": response,
                                  "profile": PROFILE}, allow_nan=False, separators=(",", ":"))+"\n")
        stats["model_processed"] += int(response["model_processed"])
        stats["runtime_fallbacks"] += int(response["status"] == "fallback")
        if response["status"] == "fallback" and command_enabled:
            env.release_to_native(primary, response["reason"])
        last_response = response
    journal.flush()
    return last_response


def rollout(env, policy, case, config, *, model_journal):
    primary, opponent = env.arena["primary_entity_id"], env.arena["opponent_entity_id"]
    stats, stream = Counter(), Counter()
    adversary_sent = False
    profile_budget = config.get("arena", {}).get("max_episode_substeps", 2000)
    # reset has already drained old-generation initial rows; these are logged
    # explicitly but cannot enter the newly acquired actor's recurrent state.
    record_model_rows(env, policy, primary, stream, model_journal, stats, command_enabled=False)
    env.start()
    for _ in range(profile_budget):
        record_model_rows(env, policy, primary, stream, model_journal, stats,
                          command_enabled=not bool(env.arena.get("terminated") or env.arena.get("truncated")))
        if env.arena.get("terminated") or env.arena.get("truncated"):
            break
        if case["task"] == "block" and not adversary_sent:
            if env.eligible(opponent, 6):
                env.act(opponent, 6)
                adversary_sent = True
            elif env.eligible(opponent, 2):
                env.act(opponent, 2)
        row = env.latest.get(primary)
        if row is not None and env.eligible(primary):
            if row.get("periodic") is True:
                stats["latest_eligible_periodic_opportunities"] += 1
                response = policy.arbitrate(row, stream_seq=stream[primary])
                entry = {"kind": "arbitration", "observation": row, "response": response, "profile": PROFILE}
                if response.get("action_id") is not None and response["status"] == "decision":
                    command = env.act(primary, response["action_id"])
                    entry.update(command_id=command, execution_id=env.decisions[-1]["execution_id"],
                                 command_request=env.command_responses[-1]["request"],
                                 command_response=env.command_responses[-1]["response"])
                    stats["accepted_model_commands"] += 1
                    stats[f"action_{response['action_id']}"] += 1
                elif response["status"] == "fallback":
                    env.release_to_native(primary, response["reason"])
                    stats["runtime_fallbacks"] += 1
                model_journal.write(json.dumps(entry, allow_nan=False, separators=(",", ":"))+"\n")
                model_journal.flush()
            else:
                stats["event_only_latest_eligible_skips"] += 1
        if env.step(1).advanced_substeps == 0 and not (env.arena.get("terminated") or env.arena.get("truncated")):
            raise ArenaInfrastructureError("running policy episode made no simulation progress")
    else:
        raise ArenaInfrastructureError("policy episode exceeded declared game-step budget")
    result = env.finalize()
    record_model_rows(env, policy, primary, stream, model_journal, stats, command_enabled=False)
    final_game_ms = result["final"]["game_ms"]
    mechanics = [json.loads(line) for line in (env.output / "events.jsonl").read_text().splitlines() if line]
    fallback_boundary = native_fallback_boundary(env.initial, result["final"], env.events, mechanics)
    native_handoffs = fallback_boundary["restorations_before_final"]
    if native_handoffs:
        env.fallbacks.extend({"kind": "native_ownership_handoff", "reason": event.get("release_reason"),
                              "t_game_ms": event["t_game_ms"], "event": event} for event in native_handoffs)
    if fallback_boundary["same_final_native_continuations"]:
        env.fallbacks.append({"kind": "native_execution_at_final_restoration", "t_game_ms": final_game_ms,
                              "mechanical_events": fallback_boundary["same_final_native_continuations"]})
    completed = {event.get("execution_id") for event in env.events if event.get("entity_id") == primary and
                 event.get("phase") == "completed"}
    requested = [command for command in env.decisions if command["entity_id"] == primary]
    stats["completed_model_commands"] = sum(command["execution_id"] in completed for command in requested)
    opportunities = stats["latest_eligible_periodic_opportunities"]
    result.update(model_stats=dict(stats), runtime_status=policy.status(), fallback_events=env.fallbacks,
                  native_fallback_boundary=fallback_boundary,
                  neural_control_fraction=stats["accepted_model_commands"]/opportunities if opportunities else 0.0,
                  control_fraction_denominator="fresh latest eligible periodic opportunities",
                  no_fallback_credit=not bool(env.fallbacks) and result["final"].get("outcome") != "control_lost",
                  command_responses=env.command_responses)
    before_clear = env.output / "policy-final-before-clear.json"
    write_json(before_clear, {"final": result["final"], "runtime_status": result["runtime_status"],
                              "model_stats": result["model_stats"], "checkpoint_sha256": policy.checkpoint_sha256})
    terminal_request = {"cmd": "end_episode", "session_epoch": env.identity.session_epoch,
                        "world_epoch": env.identity.world_epoch, "final_game_ms": final_game_ms,
                        "final_before_clear_file": before_clear.name, "final_before_clear_sha256": sha256(before_clear)}
    terminal_response = policy.end_episode(env.identity.session_epoch, env.identity.world_epoch)
    if terminal_response.get("accepted") is not True:
        raise ArenaInfrastructureError("terminal policy barrier rejected matching native episode")
    model_journal.write(json.dumps({"kind": "terminal_barrier", "request": terminal_request,
                                   "response": terminal_response, "profile": PROFILE}, allow_nan=False,
                                  separators=(",", ":"))+"\n")
    model_journal.flush()
    result["terminal_barrier"] = {"request": terminal_request, "response": terminal_response}
    return result


def native_rollout(env, config, case):
    env.start()
    advanced = 0
    budget = config.get("arena", {}).get("max_episode_substeps", 2000)
    opponent = env.arena["opponent_entity_id"]
    adversary_sent = False
    while advanced < budget:
        if case["task"] == "block" and not adversary_sent:
            if env.eligible(opponent, 6):
                env.act(opponent, 6)
                adversary_sent = True
            elif env.eligible(opponent, 2):
                env.act(opponent, 2)
        step = env.step(min(30 if case["task"] == "native_duel" else 1, budget-advanced))
        advanced += step.advanced_substeps
        if step.done:
            return env.finalize()
        if not step.advanced_substeps:
            raise ArenaInfrastructureError("native reference episode made no simulation progress")
    raise ArenaInfrastructureError("native reference episode exceeded game-step budget")


def run_service_probe(checkpoint, row, output, *, initial_game_ms, device="cpu", inference_cpu_threads=2):
    """Real JSONL service faults, after the engine closes; no physics emulation."""
    output.mkdir()
    request = {"cmd": "observe", "stream_seq": 1, "observation": row}
    gap = deepcopy(request)
    gap["stream_seq"] = 3
    changed = deepcopy(request)
    changed["checkpoint_sha256"] = "changed-mid-episode"
    stale_epoch = deepcopy(request)
    stale_epoch["observation"]["world_epoch"] += 1
    commands = [{"cmd": "reset", "session_epoch": row["session_epoch"], "world_epoch": row["world_epoch"],
                 "initial_game_ms": initial_game_ms},
                request, deepcopy(request), gap, deepcopy(request), stale_epoch, changed,
                {"cmd": "end_episode", "session_epoch": row["session_epoch"], "world_epoch": row["world_epoch"]},
                deepcopy(request),
                {"cmd": "reset", "session_epoch": row["session_epoch"], "world_epoch": row["world_epoch"],
                 "initial_game_ms": initial_game_ms},
                {"cmd": "end_episode", "session_epoch": row["session_epoch"], "world_epoch": row["world_epoch"]+1},
                deepcopy(request), {"cmd": "status"}, {"cmd": "close"}]
    commands = [{"id": index+1, **command} for index, command in enumerate(commands)]
    payload = "".join(json.dumps(command, allow_nan=False)+"\n" for command in commands)
    thread_args = [] if inference_cpu_threads == 2 else ["--inference-cpu-threads", str(inference_cpu_threads)]
    completed = subprocess.run([sys.executable, "-m", "gothic_body", "serve", "--checkpoint", str(checkpoint),
                                "--device", device, *thread_args, "--journal", str(output / "service-journal.jsonl")],
                               input=payload, text=True, capture_output=True, timeout=60,
                               env={**os.environ, "PYTHONPATH": str(PACKAGE), "OMP_NUM_THREADS": "2",
                                    "OPENBLAS_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"})
    (output / "request.jsonl").write_text(payload)
    (output / "response.jsonl").write_text(completed.stdout)
    (output / "stderr.txt").write_text(completed.stderr)
    rows = [json.loads(line) for line in completed.stdout.splitlines()]
    checks = {"clean_exit": completed.returncode == 0, "response_count": len(rows) == len(commands)}
    if len(rows) == len(commands):
        checks.update(healthy_processed=rows[1].get("model_processed") is True,
                      duplicate_cached=rows[2].get("status") == "cached" and not rows[2].get("model_processed"),
                      gap_fallback=rows[3].get("reason") == "unexpected_gap" and not rows[3].get("action_id"),
                      stale_fallback=rows[4].get("reason") == "stale_sequence" and not rows[4].get("model_processed"),
                      stale_epoch=rows[5].get("reason") == "stale_epoch",
                      frozen_checkpoint=rows[6].get("reason") == "checkpoint_changed_mid_episode",
                      terminal_clears=rows[7].get("reason") == "episode_ended" and rows[7].get("hidden_reset") is True,
                      post_terminal_rejected=rows[8].get("reason") == "episode_ended" and not rows[8].get("model_processed"),
                      stale_terminal_rejected=rows[10].get("reason") == "stale_episode_end" and not rows[10].get("hidden_reset"),
                      fresh_reset_processed=rows[11].get("model_processed") is True and rows[11].get("hidden_reset") is True,
                      terminal_state_empty=rows[7].get("runtime_after_clear", {}).get("actors") == 0,
                      fresh_reset_numerical_parity=rows[1].get("logits") == rows[11].get("logits") and
                                                  rows[1].get("features_sha256") == rows[11].get("features_sha256") and
                                                  rows[1].get("sequence_dt_game_ms") == rows[11].get("sequence_dt_game_ms"))
    result = {"schema_version": 1, "checkpoint_sha256": sha256(checkpoint), "checks": checks,
              "passed": all(checks.values()), "exit_code": completed.returncode,
              "request_sha256": sha256(output / "request.jsonl"), "response_sha256": sha256(output / "response.jsonl"),
              "journal_sha256": sha256(output / "service-journal.jsonl") if (output / "service-journal.jsonl").is_file() else None}
    write_json(output / "probe.json", result)
    if not result["passed"]:
        raise ArenaInfrastructureError(f"real JSONL service invariant probe failed: {checks}")
    return result


def run_suite(args, config):
    result = {"schema_version": 1, "kind": "body_policy_stage7_suite", "status": "infrastructure_failure",
              "profile": PROFILE, "cases": [], "resources": [], "quality_gate_evaluated": False}
    runner = None
    started, probes = time.monotonic(), {}
    try:
        manifest = provenance(args)
        manifest.update(kind="body_policy_stage7", profile=PROFILE, device=args.device,
                        driver_sha256=sha256(Path(__file__)), checkpoint_frozen_per_episode=True,
                        checkpoint_sha256={key: sha256(path) for key, path in
                                           (("gru", args.gru_checkpoint), ("mlp", args.mlp_checkpoint))})
        manifest["policy_code_sha256"] = {str(path.relative_to(PACKAGE)): sha256(path)
                                          for path in sorted((PACKAGE / "gothic_body").glob("*.py"))}
        manifest["feature_packer_sha256"] = sha256(Path(__file__).with_name("body_trajectory_dataset.py"))
        write_json(args.output / "manifest.json", manifest)
        policies = {name: PolicyRuntime(path, device=args.device, max_actors=4) for name, path in
                    (("gru", args.gru_checkpoint), ("mlp", args.mlp_checkpoint))}
        runner = PolicyArenaRunner(args)
        result["runner_pid"] = runner.process.pid
        declared = config["arena"]["cases"]
        if args.smoke:
            declared = declared[:1]
        for case in declared:
            for provider in ("native", "gru", "mlp"):
                name = f"{case['name']}-{provider}"
                if provider != "native":
                    checkpoint = getattr(args, provider+"_checkpoint")
                    if sha256(checkpoint) != policies[provider].checkpoint_sha256:
                        raise ArenaInfrastructureError("checkpoint file changed during frozen-policy suite")
                runner.arena_options = case.get("arena", {})
                env = PolicyEpisodeEnv(runner, args.scenario, owner_provider=f"stage7-policy-{provider}")
                initial = env.reset(output=args.output / name, task=case["task"], seed=case["seed"], side=case["side"],
                                    distance_cm=case["distance_cm"], timeout_ms=case["timeout_ms"],
                                    provider=("native" if case["task"] == "native_duel" else "native_with_scripted_opponent")
                                    if provider == "native" else "scripted")
                record = {"name": name, "case": case, "policy": provider, "runner_pid": runner.process.pid,
                          "initial": initial, "profile": PROFILE, "episode_id": initial["episode_id"],
                          "checkpoint_sha256": None if provider == "native" else policies[provider].checkpoint_sha256,
                          "normalizer_sha256": None if provider == "native" else policies[provider].normalizer_sha256,
                          "model_journal": None if provider == "native" else f"{name}/policy-inputs.jsonl"}
                if provider == "native":
                    record.update(native_rollout(env, config, case))
                else:
                    policy = policies[provider]
                    policy.begin_episode(initial["session_epoch"], initial["world_epoch"],
                                         initial_game_ms=initial["game_ms"])
                    with (args.output / name / "policy-inputs.jsonl").open("x") as journal:
                        record.update(rollout(env, policy, case, config, model_journal=journal))
                    entries = (args.output / name / "policy-inputs.jsonl").read_text().splitlines()
                    if provider not in probes:
                        for line in entries:
                            entry = json.loads(line)
                            if entry["kind"] == "model_input" and entry["response"].get("model_processed"):
                                probes[provider] = (entry["request"]["observation"], initial["game_ms"])
                                break
                    record["model_journal_sha256"] = sha256(args.output / name / "policy-inputs.jsonl")
                write_json(args.output / name / "episode.json", record)
                result["cases"].append(record)
                write_json(args.output / "suite.json", result)
                print(f"body policy [{name}] native outcome={record['final'].get('outcome')} commands={record.get('model_stats',{}).get('accepted_model_commands',0)}", flush=True)
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode:
            raise ArenaInfrastructureError(f"runner did not close cleanly: {runner.process.returncode}")
        result["service_probes"] = {name: run_service_probe(path, probes[name][0], args.output / f"serve-probe-{name}",
                                                          initial_game_ms=probes[name][1], device=args.device)
                                    for name, path in (("gru", args.gru_checkpoint), ("mlp", args.mlp_checkpoint))}
        result.update(status="collected", suite_wall_s=time.monotonic()-started, services_after=service_snapshot(),
                      resources=runner.samples, collector_resource=process_sample(os.getpid()),
                      limitations=["Diagnostic BC arena outcomes are independently gated; collected does not mean task-success ready.",
                                   "Locked periodic inputs update memory; exact native teacher captures and event-only rows never do.",
                                   "Native takeover, target visibility loss and game-time timeout cannot earn model credit."])
        manifest.update(services_after=result["services_after"], completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        write_json(args.output / "manifest.json", manifest)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
        print(f"body policy suite failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        if runner is not None:
            result["resources"] = runner.samples
            runner.terminate()
        write_json(args.output / "suite.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "config", "scenario",
                 "melee-scenario", "native-reference", "output", "gru-checkpoint", "mlp-checkpoint"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("native simulations and models run only on dev-ubuntu, never on the Mac")
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    args.output.mkdir(exist_ok=True)
    return run_suite(args, json.loads(args.config.read_text()))


if __name__ == "__main__":
    raise SystemExit(main())
