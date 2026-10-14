#!/usr/bin/env python3
"""Measure native versus four real policy NPCs in one headless engine world.

Two native duel pairs are placed and validated by the engine. Every policy
actor has an independent runtime; no row or virtual actor is duplicated to
claim load. This phase measures runtime/ownership and never grants behavioral
success credit or changes checkpoint selection.
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

from body_policy_diagnostics import (DiagnosticEpisodeEnv, DiagnosticRunner, PROFILE, TIMING_PROFILE,
                                     append_json, feature_module, observation_key, process_rows, startup_warmup)
from body_arena_env import ArenaInfrastructureError, EpisodeIdentity, LEASE_KEYS
from body_headless_suite import process_sample, service_snapshot, sha256, write_json
from body_observe_suite import provenance
from body_policy_suite import PACKAGE, native_fallback_boundary
from body_service_load import (ServiceLoadWorker, bind_window, compare_cohorts,
                               validate_request_cadence)
from gothic_body.runtime import PolicyRuntime
from body_collector_gc import CollectorGCScope


def physical_actor_ids(arena):
    pairs = arena.get("actor_pairs")
    if arena.get("load_fixture") is not True or not isinstance(pairs, list) or len(pairs) != 2:
        raise ArenaInfrastructureError("load requires two real native arena duel pairs")
    ids = [row[key] for row in pairs for key in ("primary_entity_id", "opponent_entity_id")]
    actors = arena.get("actors", [])
    if (len(ids) != 4 or len(set(ids)) != 4 or any(type(entity) is not int or entity <= 0 for entity in ids) or
            len(actors) != 4 or {row.get("entity_id") for row in actors} != set(ids)):
        raise ArenaInfrastructureError("load actor identities do not prove four distinct physical NPCs")
    return ids


def cadence_reset_deadline(previous_phase, cadence):
    """Any cadence wait precedes reset/acquire, never consuming an active lease."""
    cadence = validate_request_cadence(cadence)
    if cadence is None or previous_phase is None:
        return None
    rows = previous_phase.get("requests", [])
    starts = []
    for service in ("asr", "tts", "decider", "gemma"):
        times = [row.get("request_begin_wall_monotonic_ns") for row in rows if row.get("service") == service]
        if not times or any(type(value) is not int or value <= 0 for value in times):
            raise ArenaInfrastructureError("service cadence requires actual previous burst clocks")
        starts.append(min(times))
    return max(starts)+cadence["minimum_actual_start_interval_ms"]*1_000_000


def prepare_policy_pools(args, load, runtime_config, checkpoint_sha256):
    """Allocate and warm every independent actor runtime before any reset."""
    steps = runtime_config.get("startup_warmup_steps", 0)
    if steps and args.device != "cpu":
        raise ArenaInfrastructureError("declared four-actor startup warmup is CPU only")
    pools, warmups = {}, {}
    for provider in load["policies"]:
        if provider == "native":
            continue
        if provider not in ("gru", "mlp"):
            raise ValueError("unknown four actor load provider")
        pool = [PolicyRuntime(getattr(args, provider+"_checkpoint"), device=args.device, max_actors=1,
                              inference_cpu_threads=runtime_config.get("inference_cpu_threads", 2))
                for _ in range(4)]
        if any(policy.checkpoint_sha256 != checkpoint_sha256[provider] for policy in pool):
            raise ArenaInfrastructureError("four actor frozen checkpoint changed")
        pools[provider] = pool
        if steps:
            warmups[provider] = [{**startup_warmup(policy, steps), "runtime_pool_slot": slot}
                                 for slot, policy in enumerate(pool)]
    return pools, warmups


class FourActorEnv(DiagnosticEpisodeEnv):
    """Reset the explicit native four-NPC fixture, leaving old arenas unchanged."""
    def reset(self, *, output, task, seed, side, distance_cm, timeout_ms, provider):
        if task != "native_duel" or provider not in ("native", "scripted"):
            raise ValueError("four actor load uses a native duel fixture")
        output.mkdir()
        self.runner.episode_log(output)
        self.output, self.provider = output, provider
        self.identity = self.recurrent_token = None
        for container in (self.latest, self.processed_sequences, self.leases, self.command_ids,
                          self.decisions, self.events, self.pulled, self.command_responses, self.fallbacks):
            container.clear()
        self.rollout_started = 0.0
        self.last_running_status_wall_ns = 0
        self.reset_started = time.monotonic()
        response = self.runner.call({"cmd": "reset", "scenario": str(self.scenario), "output_dir": str(output),
                                     "mode": "realtime", "arena": {"scenario": task, "seed": seed, "side": side,
                                     "distance_cm": distance_cm, "timeout_ms": timeout_ms, "load_fixture": True}})
        arena = self._arena(response)
        if arena.get("ready") is not True or arena.get("started") is not False:
            raise ArenaInfrastructureError("four actor reset did not produce a stable unstarted arena")
        ids = physical_actor_ids(arena)
        self.initial = json.loads(json.dumps(arena))
        self.identity = EpisodeIdentity(arena["session_epoch"], arena["world_epoch"],
                                        arena["runner_generation"], arena["episode_id"])
        self.recurrent_token = (self.identity.session_epoch, self.identity.world_epoch, self.identity.generation)
        self.pull()
        if provider == "scripted":
            for entity in ids:
                self.acquire(entity)
        self.reset_wall_s = time.monotonic()-self.reset_started
        self.runner.sample()
        return self.initial


def process_physical_rows(env, policies, streams, journal, latency, stats, *, command_enabled, seen=None):
    rows, env.pulled = env.pulled, []
    # Actors whose newest row can decide go first, and each one's command is sent as
    # soon as its own runtime has stepped: four serial memory updates must not eat the
    # inter-tick window. The sort is stable and keyed per actor, so every actor's own
    # rows keep their order and every row is still processed exactly once.
    newest = {row["entity_id"]: row for row in rows}
    rows = sorted(rows, key=lambda row: newest[row["entity_id"]].get("can_decide") is not True)
    remaining = Counter(row["entity_id"] for row in rows)
    for row in rows:
        entity = row["entity_id"]
        remaining[entity] -= 1
        if entity not in policies:
            continue
        # The common collector receives this one original physical row exactly
        # once. The independent runtime keyed by its actual entity owns memory.
        env.pulled = [row]
        process_rows(env, policies[entity], (entity,), streams, journal, latency, stats,
                     shadow=False, command_enabled=command_enabled)
        if (seen is not None and command_enabled and remaining[entity] == 0 and
                not (env.arena.get("terminated") or env.arena.get("truncated"))):
            arbitrate_physical_actors(env, {entity: policies[entity]}, streams, journal, latency, stats, seen)


def arbitrate_physical_actors(env, policies, streams, journal, latency, stats, seen):
    if getattr(env, "fresh_observations_required", False):
        # An empty/event-only drain has not supplied the rejected actor's new
        # periodic state. Other actors from the old batch remain undispatched.
        return
    for entity, policy in policies.items():
        row = env.latest.get(entity)
        if row is None or row.get("periodic") is not True or not env.eligible(entity):
            continue
        key = observation_key(row)
        if key in seen:
            continue
        seen.add(key)
        stats["latest_eligible_periodic_opportunities"] += 1
        begin = time.monotonic_ns()
        response = policy.arbitrate(row, stream_seq=streams[entity])
        finish = time.monotonic_ns()
        entry = {"kind": "arbitration", "observation": row, "response": response, "profile": PROFILE,
                 "timing": {"collector_received_wall_monotonic_ns": env.runner.received.get(key),
                            "arbitration_begin_wall_monotonic_ns": begin,
                            "arbitration_end_wall_monotonic_ns": finish}}
        if response.get("status") == "decision" and response.get("action_id") is not None:
            command_id = env.try_act(entity, response["action_id"])
            receipt = env.command_responses[-1]
            entry.update(command_id=command_id, execution_id=receipt["response"].get("execution_id"),
                         command_request=receipt["request"], command_response=receipt["response"])
            entry["terminal_censored"] = receipt.get("terminal_transition") is True
            entry["stale_refresh_pending"] = receipt.get("stale_refresh_pending") is True
            entry["decision_missed"] = receipt.get("decision_missed") is True
            entry["native_guard_wait_pending"] = receipt.get("native_guard_wait_pending") is True
            if entry["native_guard_wait_pending"]:
                entry["native_guard_wait"] = receipt["native_guard_wait"]
                stats["native_guard_wait_requests"] += 1
            if entry["stale_refresh_pending"]:
                entry["recovery_attempt"] = receipt["recovery_attempt"]
            entry["timing"]["command_sent_wall_monotonic_ns"] = receipt["sent_wall_monotonic_ns"]
            stats["accepted_model_commands"] += int(command_id is not None)
            stats["terminal_censored_commands"] += int(entry["terminal_censored"])
            stats["rejected_model_commands"] += int(command_id is None and not entry["terminal_censored"])
            stats[f"actor_{entity}_accepted_commands"] += int(command_id is not None)
        elif response.get("status") == "fallback":
            stats["runtime_fallbacks"] += 1
            env.release_to_native(entity, response["reason"])
        env.persist(journal, entry)
        env.persist(latency, {"kind": "arbitration", "observation_key": list(key), **entry["timing"],
                              "action_id": response.get("action_id"), "command_id": entry.get("command_id"),
                              "execution_id": entry.get("execution_id"), "shadow": False})
        if getattr(env, "arena", {}).get("terminated") or getattr(env, "arena", {}).get("truncated"):
            return
        if getattr(env, "fresh_observations_required", False):
            # The rejected command remains a rejected journaled opportunity.
            # Stop dispatching other actors from that same old snapshot batch.
            if not getattr(env, "native_guard_waits", {}):
                stats["stale_refresh_requests"] += 1
            return


def finish_load(env, policies, streams, journal, latency, stats):
    env.runner.end_dispatch_window()
    result = env.finalize()
    process_physical_rows(env, policies, streams, journal, latency, stats, command_enabled=False)
    final_native_status = env.runner.call({"cmd": "body_control_status"})
    mechanics = [json.loads(line) for line in (env.output / "events.jsonl").read_text().splitlines() if line]
    boundaries = {str(entity): native_fallback_boundary({**env.initial, "primary_entity_id": entity},
                                                       result["final"], env.events, mechanics)
                  for entity in policies}
    status = {str(entity): policy.status() for entity, policy in policies.items()}
    before = env.output / "policy-final-before-clear.json"
    write_json(before, {"final": result["final"], "runtimes": status, "model_stats": dict(stats),
                        "policy_actor_ids": list(policies)})
    barriers = {}
    for entity, policy in policies.items():
        request = {"cmd": "end_episode", "entity_id": entity, "session_epoch": env.identity.session_epoch,
                   "world_epoch": env.identity.world_epoch, "final_game_ms": result["final"]["game_ms"],
                   "final_before_clear_file": before.name, "final_before_clear_sha256": sha256(before)}
        response = policy.end_episode(env.identity.session_epoch, env.identity.world_epoch)
        if response.get("accepted") is not True:
            raise ArenaInfrastructureError("four actor terminal barrier refused a matching epoch")
        append_json(journal, {"kind": "terminal_barrier", "request": request, "response": response,
                              "profile": PROFILE, "wall_monotonic_ns": time.monotonic_ns()})
        barriers[str(entity)] = {"request": request, "response": response}
    opportunities = stats["latest_eligible_periodic_opportunities"]
    result.update(runtime_status=status, model_stats=dict(stats), terminal_barriers=barriers,
                  native_fallback_boundaries=boundaries, fallback_events=env.fallbacks,
                  command_responses=env.command_responses, physical_actor_ids=physical_actor_ids(env.initial),
                  policy_actor_ids=list(policies), independent_runtime_count=len(policies),
                  neural_control_fraction=stats["accepted_model_commands"]/opportunities if opportunities else None,
                  control_fraction_denominator="unique latest eligible periodic opportunities across actual policy NPCs",
                  no_fallback_credit=False, behavioral_quality_evaluated=False,
                  perception_evidence={"file": "events.jsonl", "event_phase": "visibility_diagnostic",
                                       "rows": sum(row.get("phase") == "visibility_diagnostic" for row in mechanics),
                                       "final_native_status": final_native_status, "oracle_only": True})
    return result


def load_rollout(env, policies, config, journal, latency, *, worker=None, provider=None, cohort=0, minimum_neural_fraction=.95):
    try:
        return _load_rollout(env, policies, config, journal, latency, worker=worker,
                             provider=provider, cohort=cohort, minimum_neural_fraction=minimum_neural_fraction)
    finally:
        env.runner.end_dispatch_window()


def _load_rollout(env, policies, config, journal, latency, *, worker=None, provider=None, cohort=0, minimum_neural_fraction=.95):
    stats, streams, seen = Counter(), Counter(), set()
    process_physical_rows(env, policies, streams, journal, latency, stats, command_enabled=False)
    env.start()
    service_request, service_result, window = None, None, None
    if worker is not None:
        start_status = env.runner.call({"cmd": "body_control_status"})
        env._arena(start_status)
        actual = {row["entity_id"]: row for row in start_status.get("actors", [])}
        actors = physical_actor_ids(env.initial)
        expected_owner = "scripted" if policies else "native"
        if (set(actual) != set(actors) or env.arena.get("terminated") or env.arena.get("truncated") or
                any(row.get("owner") != expected_owner or row.get("release_pending") is True for row in actual.values()) or
                start_status.get("clock_domain") != "linux-clock-monotonic" or type(start_status.get("wall_monotonic_ns")) is not int):
            raise ArenaInfrastructureError("service cohort lacks four healthy actual actors and a native clock anchor")
        if policies and any(actual[entity].get(key) != env.leases[entity][key] for entity in actors for key in LEASE_KEYS):
            raise ArenaInfrastructureError("service cohort initial four owners differ from native leases")
        window = {"start_wall_monotonic_ns": start_status["wall_monotonic_ns"], "clock_domain": "linux-clock-monotonic",
                  "session_epoch": env.identity.session_epoch, "world_epoch": env.identity.world_epoch,
                  "episode_id": env.identity.episode_id, "physical_actor_ids": actors, "policy_actor_ids": list(policies),
                  "provider": provider, "cohort": cohort, "native_start_status": start_status}
        service_request = worker.begin(f"during-{provider}-{cohort}", count=5, warmup=2 if cohort == 0 else 0, window=window)
        # Service-window admission happens before delivery of the latest batch.
        # Its RPC/worker setup cannot age a row used for the first dispatch.
        env.runner.begin_dispatch_window()
        env.pull()
    deadline = time.monotonic()+config["duration_ms"]/1000+30
    polls = 0
    while time.monotonic() <= deadline:
        process_physical_rows(env, policies, streams, journal, latency, stats,
                              command_enabled=not bool(env.arena.get("terminated") or env.arena.get("truncated")),
                              seen=seen)
        if env.arena.get("terminated") or env.arena.get("truncated"):
            if worker is not None and service_result is None:
                service_result = worker.take_completed(service_request)
            if worker is not None and service_result is None:
                # No client request may keep running after the actual arena
                # terminal. Cancellation preserves partial receipts as failure.
                worker.cancel()
                service_result = worker.finish(service_request, timeout=5)
            result = finish_load(env, policies, streams, journal, latency, stats)
            if service_result is not None:
                healthy = (not env.fallbacks and stats["runtime_fallbacks"] == 0 and
                           result["final"].get("outcome") == "time_limit" and
                           result["final"].get("elapsed_game_ms", 0) >= config["duration_ms"] and
                           all(not any(boundary.values()) for boundary in result["native_fallback_boundaries"].values()))
                if policies:
                    healthy = healthy and result["neural_control_fraction"] is not None and result["neural_control_fraction"] >= minimum_neural_fraction
                binding = {**window, **bind_window(service_result["requests"], window["start_wall_monotonic_ns"],
                                                  env.last_running_status_wall_ns, healthy),
                           "episode_file": str(env.output / "episode.json"),
                           "native_final": result["final"], "final_native_status": result["perception_evidence"]["final_native_status"],
                           "fallback_events": result["fallback_events"]}
                service_result["window_binding"] = binding
                write_json(worker.output / service_result["phase"] / "window-binding.json", binding)
                result["service_workload"] = service_result
            return result
        arbitrate_physical_actors(env, policies, streams, journal, latency, stats, seen)
        env.runner.end_dispatch_window()
        # Parsing a completed service burst can be large; it is proof work,
        # after command dispatch rather than between inference and body_action.
        if worker is not None and service_result is None:
            service_result = worker.take_completed(service_request)
        if getattr(env, "fresh_observations_required", False):
            if getattr(env, "native_guard_waits", {}):
                # A physical guard classification waits through native ticks.
                # Use the normal paced heartbeat/events/numeric delivery rather
                # than busy-draining an engine that has not advanced yet.
                env.step()
                polls += 1
                continue
            # Only the shared runtime helper can request this bounded refresh.
            # Actual new periodic rows must update all actor memories before
            # a fresh inference/arbitration; the old command is never retried.
            env.runner.begin_dispatch_window()
            env.pull()
            continue
        env.step()
        polls += 1
        if polls > 10000:
            raise ArenaInfrastructureError("four actor realtime collector exceeded bounded poll budget")
    raise ArenaInfrastructureError("four actor load wall watchdog expired")


def run_suite(args, config):
    load = config["load"]
    result = {"schema_version": 1, "kind": "body_policy_stage8_four_actor_load", "status": "infrastructure_failure",
              "cases": [], "profile": PROFILE, "timing_profile": TIMING_PROFILE,
              "behavioral_quality_evaluated": False}
    runner = None
    worker = None
    gc_scope, manifest = None, None
    try:
        manifest = provenance(args)
        manifest.update(kind=result["kind"], driver_sha256=sha256(Path(__file__)), profile=PROFILE,
                        common_driver_sha256=sha256(Path(__file__).with_name("body_policy_diagnostics.py")),
                        feature_packer_sha256=sha256(Path(feature_module.__file__)),
                        feature_packer_path=str(Path(feature_module.__file__).resolve()),
                        timing_profile=TIMING_PROFILE, device=args.device, policy_runtimes="one per actual physical NPC",
                        checkpoint_sha256={name: sha256(getattr(args, name+"_checkpoint")) for name in ("gru", "mlp")})
        manifest["policy_code_sha256"] = {str(path.relative_to(PACKAGE)): sha256(path)
                                          for path in sorted((PACKAGE / "gothic_body").glob("*.py"))}
        write_json(args.output / "manifest.json", manifest)
        runtime_config = config["runtime"]
        args.compact_replies = runtime_config.get("compact_replies", False)
        if getattr(args,"native_serial_workers",False) and runtime_config.get("native_serial_workers",False) is not True:
            raise ValueError("native serial CLI/config profile differs")
        args.native_serial_workers = runtime_config.get("native_serial_workers", False)
        pools, warmups = prepare_policy_pools(args, load, runtime_config, manifest["checkpoint_sha256"])
        if warmups:
            write_json(args.output / "startup-warmup.json", warmups)
        manifest.update(compact_replies=args.compact_replies, native_serial_workers=args.native_serial_workers, startup_warmup=warmups,
                        startup_warmup_before_any_arena=True,
                        startup_runtime_counts={name: len(pool) for name, pool in pools.items()})
        write_json(args.output / "manifest.json", manifest)
        gc_scope = CollectorGCScope(
            runtime_config.get("collector_static_gc_freeze", False),
            normalize_bootstrap=runtime_config.get("collector_bootstrap_gc_reset", False),
        )
        if gc_scope.enabled:
            manifest["collector_gc"] = gc_scope.enter()
            write_json(args.output / "collector-gc.json", gc_scope.profile)
            write_json(args.output / "manifest.json", manifest)
        if not args.no_service_load:
            worker = ServiceLoadWorker(args.config, args.output / "services")
            print("stage8 protected-service baseline starts before any engine lease or game tick", flush=True)
            request = worker.begin("before", count=20, warmup=2)
            baseline = worker.finish(request, timeout=130)
            if baseline.get("status") != "collected":
                raise ArenaInfrastructureError("protected-service baseline was not collected completely")
            result["service_baseline"] = baseline
            result["service_worker"] = worker.ready
        cohorts = {provider: [] for provider in load["policies"]}
        cadence = validate_request_cadence(config["services"].get("request_cadence"))
        runner = DiagnosticRunner(args)
        runner.mode = "realtime"
        runner.arena_options = {"load_fixture": True}
        for provider in load["policies"]:
            if provider not in ("native", "gru", "mlp"):
                raise ValueError("unknown four actor load provider")
            pool = [] if provider == "native" else pools[provider]
            for cohort in range(1 if args.no_service_load else load["cohorts_per_provider"]):
                name = f"four-actor-{provider}-{cohort}"
                deadline_ns = cadence_reset_deadline(cohorts[provider][-1] if cohorts[provider] else None,
                                                     cadence) if worker is not None else None
                if deadline_ns is not None:
                    remaining = (deadline_ns-time.monotonic_ns())/1e9
                    if remaining > 0:
                        time.sleep(remaining)
                env = FourActorEnv(runner, args.scenario, owner_provider="stage8-load-"+provider,
                                   poll_ms=runtime_config["poll_ms"], command_ttl_ms=runtime_config["command_ttl_ms"],
                                   heartbeat_interval_ms=runtime_config.get("heartbeat_interval_ms", 0),
                                   max_stale_refresh_attempts=runtime_config.get("max_stale_refresh_attempts", 0),
                                   native_deadline_polling=runtime_config.get("native_deadline_polling", False))
                initial = env.reset(output=args.output / name, task="native_duel", seed=load["seed"], side=load["side"],
                                    distance_cm=load["distance_cm"], timeout_ms=load["duration_ms"],
                                    provider="native" if provider == "native" else "scripted")
                runner.call({"cmd": "body_visibility_watch", "enabled": True})
                actors = physical_actor_ids(initial)
                policies = dict(zip(actors, pool))
                for policy in policies.values():
                    policy.begin_episode(initial["session_epoch"], initial["world_epoch"], initial_game_ms=initial["game_ms"])
                record = {"name": name, "cohort": cohort, "policy": provider, "initial": initial, "mode": "realtime",
                          "checkpoint_sha256": {str(entity): policy.checkpoint_sha256 for entity, policy in policies.items()},
                          "normalizer_sha256": {str(entity): policy.normalizer_sha256 for entity, policy in policies.items()},
                          "runtime_startup_slots": {str(entity): slot for slot, entity in enumerate(policies)},
                          "runner_pid": runner.process.pid, "profile": PROFILE}
                if cadence is not None:
                    record["request_cadence"] = cadence
                    record["cadence_reset_not_before_wall_monotonic_ns"] = deadline_ns
                with (env.output / "policy-inputs.jsonl").open("x") as journal, (env.output / "latency.jsonl").open("x") as latency:
                    record.update(load_rollout(env, policies, load, journal, latency, worker=worker, provider=provider, cohort=cohort,
                                              minimum_neural_fraction=config["quality"]["minimum_healthy_neural_control_fraction"]))
                record["model_journal_sha256"] = sha256(env.output / "policy-inputs.jsonl")
                record["latency_sha256"] = sha256(env.output / "latency.jsonl")
                write_json(env.output / "episode.json", record)
                result["cases"].append(record)
                if worker is not None:
                    cohorts[provider].append(record["service_workload"])
                    comparison = compare_cohorts(baseline, cohorts)
                    write_json(worker.output / "services.json", {"schema_version": 1, "before": baseline,
                                                                  "during": cohorts, "comparison": comparison,
                                                                  "quality_gate_evaluated": False})
                    result["service_comparison"] = comparison
                write_json(args.output / "suite.json", result)
                print(f"stage8 [{name}] real actors={len(actors)} runtimes={len(policies)} outcome={record['final'].get('outcome')}", flush=True)
            policies.clear()
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode:
            raise ArenaInfrastructureError("four actor runner did not close cleanly")
        result.update(status="collected", services_after=service_snapshot(), resources=runner.samples,
                      collector_resource=process_sample(os.getpid()))
        manifest.update(completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(), services_after=result["services_after"])
        write_json(args.output / "manifest.json", manifest)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
        print(f"four actor load failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        try:
            if runner is not None:
                result["resources"] = runner.samples
                runner.terminate()
            if worker is not None:
                worker.close()
        finally:
            if gc_scope is not None and gc_scope.enabled:
                manifest["collector_gc"] = gc_scope.close()
                write_json(args.output / "collector-gc.json", gc_scope.profile)
                write_json(args.output / "manifest.json", manifest)
            write_json(args.output / "suite.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial-workers",dest="native_serial_workers",action="store_true")
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "config", "scenario",
                 "melee-scenario", "native-reference", "output", "gru-checkpoint", "mlp-checkpoint"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--no-service-load", action="store_true", help="fixture diagnostic only; cannot satisfy final service-load gate")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("physical NPC load and model forwards run only on dev-ubuntu")
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    args.output.mkdir(exist_ok=False)
    return run_suite(args, json.loads(args.config.read_text()))


if __name__ == "__main__":
    raise SystemExit(main())
