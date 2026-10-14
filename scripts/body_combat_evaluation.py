#!/usr/bin/env python3
"""Preregistered evaluation v2 with a visible incoming-attack block initializer.

This extension leaves the known reaction diagnostic and frozen v6 policy code
unchanged. Sixty non-block screening cases use the original reset and rollout.
The twenty block cases use the same real held preparation for every provider.
Weights and normalization are fixed by the new declaration before measurement.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


class EvaluationError(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise EvaluationError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")


def imports_and_declaration(args, config):
    reference = config["reference"]
    require(args.reference_run.name == reference["run_name"], "weights reference run differs from preregistration")
    require(args.policy_code_root == args.reference_run.parent / reference["policy_code_run_name"] / "code",
            "policy code root differs from the explicit frozen declaration")
    for name in ("gru", "mlp"):
        checkpoint = getattr(args, name+"_checkpoint")
        require(checkpoint == args.reference_run / "models" / name / "best.pt" and sha(checkpoint) == reference[name+"_checkpoint_sha256"],
                "fixed selected checkpoint differs from preregistration")
    norm_path = args.reference_run / "dataset/normalization.json"
    require(sha(norm_path) == reference["normalizer_sha256"], "fixed normalizer differs from preregistration")
    source_config_path = (args.reference_run.parent / config["bindings"].get("original_stage8_config_run_name", "stage8-combat-v6") /
                          "code/evals/npc-body/body-stage8-v1.json")
    require(sha(source_config_path) == config["bindings"]["original_stage8_config_sha256"], "original 80-case declaration changed")
    original = json.loads(source_config_path.read_text())
    helper_path = Path(__file__).with_name("body_block_reaction.py")
    helper_config_path = Path(__file__).resolve().parents[1] / "evals/npc-body/body-block-reaction-v1.json"
    require(sha(helper_path) == config["bindings"]["preparation_helper_sha256"] and
            sha(helper_config_path) == config["bindings"]["preparation_config_sha256"], "known reaction preparation source changed")
    spec = importlib.util.spec_from_file_location("frozen_reaction_preparation", helper_path)
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    spec.loader.exec_module(helper)
    for path in reversed((args.policy_code_root / "body-service/src", args.policy_code_root / "scripts")):
        sys.path.insert(0, str(path))
    modules = {name: importlib.import_module(name) for name in config["bindings"]["frozen_policy_modules"]}
    for name, module in modules.items():
        require(Path(module.__file__).resolve().is_relative_to(args.policy_code_root) and
                sha(module.__file__) == config["bindings"]["frozen_policy_modules"][name], "frozen original evaluation module differs: "+name)
    require(config["screening"]["cases"] == original["screening"]["cases"] and
            config["screening"]["seeds"] == original["screening"]["seeds"] and
            config["screening"]["policies"] == original["screening"]["policies"] == ["native", "gru", "mlp"],
            "fresh screening matrix must retain every original case, order and provider")
    require(len(config["screening"]["cases"]) == 80 and
            sum(case["task"] == "block" for case in config["screening"]["cases"]) == 20,
            "screening must have exactly 60 ordinary and 20 reaction cases")
    known = json.loads(helper_config_path.read_text())
    require(config["realtime-reaction"]["cases"] == known["cases"] and
            config["realtime-reaction"]["policies"] == ["native", "gru", "mlp"], "known realtime reaction matrix differs")
    require(config["preparation"] == known["preparation"], "reaction physical preparation differs from the known frozen helper")
    require(config["selection_allowed"] is False and config["retraining_allowed"] is False and
            config["screening"]["selection_allowed"] is False, "evaluation cannot select or retrain models")
    return helper, modules, original, json.loads(norm_path.read_text())


def activate_prepared(env, provider, anchor, mode, api, journal):
    """Actual engine handoff; world time stays at the prepared source boundary."""
    primary, opponent = env.initial["primary_entity_id"], env.initial["opponent_entity_id"]
    request = {"cmd": "arena_reaction_activate", "session_epoch": env.identity.session_epoch,
               "world_epoch": env.identity.world_epoch, "primary_entity_id": primary, "opponent_entity_id": opponent,
               "primary_control_generation": env.leases[primary]["control_generation"],
               "opponent_control_generation": env.leases[opponent]["control_generation"],
               "observation_seq": anchor["observation_seq"], "issued_game_ms": anchor["t_game_ms"],
               "provider": "native" if provider == "native" else "scripted", "mode": mode}
    preparation_sha = sha(env.output / "preparation.json")
    env.runner.begin_dispatch_window()
    response = env.runner.call(request)
    prepared = env._arena(response)
    reaction = prepared.get("reaction", {})
    require(prepared["game_ms"] == anchor["t_game_ms"] and reaction.get("phase") == "active" and
            reaction.get("activation_game_ms") == anchor["t_game_ms"] and reaction.get("activation_provider") == request["provider"],
            "reaction activation changed the actual shared scene boundary")
    # Switch only the adapter's polling mode after the native endpoint has
    # rebased the real scheduler and lease watchdogs. Preparation stays lockstep.
    env.runner.mode = mode
    # Activation is a full native atomic snapshot, even with compact ordinary
    # replies enabled. Another status RPC after realtime rebase would race the
    # exact anchor and has no stronger identity or ownership evidence.
    status = env.response_actor(response, primary)
    require(status is not None and
            all(status.get(key) == request[key] for key in ("session_epoch", "world_epoch")) and
            status.get("target_entity_id") == anchor["target"]["entity_id"],
            "activation has no unique authoritative primary control snapshot")
    if provider == "native":
        env.leases.pop(primary)
        require(status["owner"] == "native" and status["control_generation"] == 0,
                "native activation did not restore native primary")
    else:
        require(status["control_generation"] == anchor["control_generation"] and
                status["goal_version"] == anchor["goal"]["version"] and
                status["owner"] == "scripted" and status["provider"] == env.owner_provider and
                status["release_pending"] is False and status["control_allowed"] is True and
                status["observation_seq"] == anchor["observation_seq"] and
                status["observed_game_ms"] == anchor["t_game_ms"] and status["can_decide"] is True,
                "activation invalidated the real periodic neural anchor")
    for row in env.preparation_rows:
        if row["entity_id"] == primary and api.observation_key(row) != api.observation_key(anchor):
            env.persist(journal, {"kind": "excluded_observation", "reason": "before_reaction_scene_boundary",
                                     "observation": row, "quality_credit": False})
    env.pulled = [anchor]
    env.latest[primary] = anchor
    record = {"request": request, "response": response, "prepared_start": prepared,
              "initial_game_ms": anchor["t_game_ms"], "first_sequence_dt_game_ms": 0,
              "preparation_file": "preparation.json", "preparation_sha256": preparation_sha,
              "native_mode_rebase": mode, "mode_rebase_game_advance": 0,
              "control_snapshot_source": "activation.response.body_control.actors",
              "post_activation_status_rpc": False}
    env.persist(journal, {"kind": "reaction_activation", **record})
    env.runner.persist_json_file(env.output / "activation.json", record)
    return record


def environment_type(api):
    class EvaluationEnv(api.DiagnosticEpisodeEnv):
        def start(self):
            if not hasattr(self, "reaction_anchor"):
                return super().start()
            require(not hasattr(self, "activation_record"), "reaction activation cannot be repeated")
            # Frozen rollout's initial command-disabled drain sees no rows.
            # This start callback then performs the actual native activation
            # and exposes the first prepared row to its ordinary enabled loop.
            self.activation_record = activate_prepared(self, self.reaction_provider, self.reaction_anchor,
                                                       self.reaction_mode, api, self.reaction_journal)
            return api.EpisodeStep(self.arena, (), (), 0)
    return EvaluationEnv


def run(args, config):
    result = {"schema_version": 2, "kind": "body_stage8_evaluation_protocol_repair", "phase": args.phase,
              "status": "infrastructure_failure", "quality_gate_evaluated": False, "original_v6_credit": False,
              "selection_allowed": False, "cases": []}
    runner = None
    gc_scope, manifest = None, None
    try:
        helper, modules, original, normalizer = imports_and_declaration(args, config)
        api, runtime = modules["body_policy_diagnostics"], modules["gothic_body.runtime"]
        phase = config[args.phase]
        require(phase["mode"] == ("realtime" if args.phase == "realtime-reaction" else "lockstep"), "evaluation mode declaration differs")
        manifest = api.provenance(args)
        manifest.update(kind=result["kind"], schema_version=2, phase=args.phase, profile=api.PROFILE,
                        timing_profile=api.TIMING_PROFILE, config_file=str(args.config), driver_sha256=sha(__file__),
                        declared_before_first_case_wall_monotonic_ns=time.monotonic_ns(),
                        reference_run=str(args.reference_run), policy_code_root=str(args.policy_code_root),
                        reference_environment_sha256=sha(args.reference_run / "environment.json"),
                        checkpoint_sha256={name: sha(getattr(args, name+"_checkpoint")) for name in ("gru", "mlp")},
                        normalizer_sha256=config["reference"]["normalizer_sha256"], checkpoint_frozen_per_episode=True,
                        policy_modules={name: {"path": module.__file__, "sha256": sha(module.__file__)} for name, module in modules.items()},
                        policy_code_sha256={path.name: sha(path) for path in sorted((args.policy_code_root / "body-service/src/gothic_body").glob("*.py"))},
                        preparation_helper={"path": helper.__file__, "sha256": sha(helper.__file__)},
                        original_stage8_config_sha256=config["bindings"]["original_stage8_config_sha256"],
                        ordinary_rollout="declared current policy delivery; original v6 physical cases unchanged",
                        reaction_rollout=config["reaction_rollout"], device="cpu", original_v6_credit=False)
        write_json(args.output / "manifest.json", manifest)
        policies = {name: runtime.PolicyRuntime(getattr(args, name+"_checkpoint"), device="cpu", max_actors=1,
                                               inference_cpu_threads=config["runtime"].get("inference_cpu_threads", 2))
                    for name in ("gru", "mlp")}
        warmups = {name: api.startup_warmup(policy, config["runtime"].get("startup_warmup_steps", 0))
                   for name, policy in policies.items()}
        write_json(args.output / "startup-warmup.json", warmups)
        args.compact_replies = config["runtime"].get("compact_replies", False)
        if getattr(args,"native_serial_workers",False) and config["runtime"].get("native_serial_workers",False) is not True:
            raise ValueError("native serial CLI/config profile differs")
        args.native_serial_workers = config["runtime"].get("native_serial_workers", False)
        manifest.update(startup_warmup=warmups, startup_warmup_before_any_arena=True,
                        compact_replies=args.compact_replies,native_serial_workers=args.native_serial_workers)
        write_json(args.output / "manifest.json", manifest)
        gc_scope = api.CollectorGCScope(
            config["runtime"].get("collector_static_gc_freeze", False),
            normalize_bootstrap=config["runtime"].get("collector_bootstrap_gc_reset", False),
        )
        if gc_scope.enabled:
            manifest["collector_gc"] = gc_scope.enter()
            write_json(args.output / "collector-gc.json", gc_scope.profile)
            write_json(args.output / "manifest.json", manifest)
        runner = api.DiagnosticRunner(args)
        Env = environment_type(api)
        probes = {}
        for case in phase["cases"]:
            reaction_case = case["task"] == "block"
            for provider in phase["policies"]:
                runner.mode = "lockstep" if reaction_case else phase["mode"]
                runner.arena_options = {**case.get("arena", {}), **({"reaction_hold": True} if reaction_case else {})}
                policy = policies.get(provider)
                name = case["name"]+"-"+provider
                env = Env(runner, args.scenario, owner_provider="stage8-evaluation-v2" if reaction_case else "stage8-"+provider,
                          poll_ms=config["runtime"]["poll_ms"], command_ttl_ms=config["runtime"]["command_ttl_ms"],
                          heartbeat_interval_ms=config["runtime"].get("heartbeat_interval_ms", 0),
                          max_stale_refresh_attempts=config["runtime"].get("max_stale_refresh_attempts", 0),
                          native_deadline_polling=config["runtime"].get("native_deadline_polling", False))
                arena_provider = "scripted" if reaction_case or provider != "native" else (
                    "native" if case["task"] == "native_duel" else "native_with_scripted_opponent")
                initial = env.reset(output=args.output / name, task=case["task"], seed=case["seed"], side=case["side"],
                                    distance_cm=case["distance_cm"], timeout_ms=case["timeout_ms"], provider=arena_provider)
                watch = runner.call({"cmd": "body_visibility_watch", "enabled": True})
                require(watch.get("ok") is not False, "native visibility capture was not enabled")
                record = {"name": name, "case": case, "policy": provider, "phase": args.phase, "shadow": False,
                          "mode": phase["mode"], "initial": initial, "profile": api.PROFILE, "timing_profile": api.TIMING_PROFILE,
                          "command_ttl_ms": env.command_ttl_ms, "visibility_watch": watch, "runner_pid": runner.process.pid,
                          "original_v6_credit": False, "initializer": "reaction_prepared_v1" if reaction_case else "frozen_v6_original",
                          "checkpoint_sha256": policy.checkpoint_sha256 if policy else None,
                          "normalizer_sha256": policy.normalizer_sha256 if policy else None}
                with (env.output / "policy-inputs.jsonl").open("x") as journal, (env.output / "latency.jsonl").open("x") as latency:
                    rollout_case = case
                    initial_time = initial["game_ms"]
                    if reaction_case:
                        anchor, proof = helper.prepare(env, case, config, api, journal, normalizer)
                        initial_time = anchor["t_game_ms"]
                        env.reaction_anchor, env.reaction_provider, env.reaction_mode = anchor, provider, phase["mode"]
                        env.reaction_journal = journal
                        env.preparation_rows = env.pulled
                        env.pulled = []
                        # Only the helper's one-time attack-injection branch
                        # is suppressed. The real engine task remains block.
                        rollout_case = {**case, "task": config["reaction_rollout"]["task_alias"]}
                        record.update(model_initial_game_ms=initial_time, first_sequence_dt_game_ms=0,
                                      frozen_rollout_case=rollout_case, preparation_sha256=sha(env.output / "preparation.json"))
                    if policy is not None:
                        require(policy.normalizer_sha256 == config["reference"]["normalizer_sha256"], "runtime normalization differs from fixed reference")
                        policy.begin_episode(initial["session_epoch"], initial["world_epoch"], initial_game_ms=initial_time)
                    record.update(api.rollout(env, policy, rollout_case, phase, journal, latency, shadow=False))
                    if reaction_case:
                        record.update(activation=env.activation_record, prepared_start=env.activation_record["prepared_start"])
                        final = record["final"]
                        prep_ms = initial_time-final["start_game_ms"]
                        active_ms = final["game_ms"]-initial_time
                        require(prep_ms >= 0 and active_ms >= 0 and prep_ms+active_ms == final["elapsed_game_ms"],
                                "Native held-start timeout clock changed at reaction activation")
                        record["reaction_durations"] = {"arena_start_game_ms": final["start_game_ms"],
                            "prepared_game_ms": initial_time, "final_game_ms": final["game_ms"],
                            "preparation_game_ms": prep_ms, "active_game_ms": active_ms,
                            "total_game_ms": final["elapsed_game_ms"], "declared_timeout_ms": case["timeout_ms"],
                            "preparation_included_in_timeout": True}
                record.update(model_journal_sha256=sha(env.output / "policy-inputs.jsonl"), latency_sha256=sha(env.output / "latency.jsonl"))
                if policy is not None and provider not in probes:
                    for entry in map(json.loads, (env.output / "policy-inputs.jsonl").read_text().splitlines()):
                        if entry.get("kind") == "model_input" and entry["response"].get("model_processed"):
                            probes[provider] = (entry["request"]["observation"], initial_time)
                            break
                write_json(env.output / "episode.json", record)
                result["cases"].append(record)
                write_json(args.output / "suite.json", result)
                print(f"evaluation [{name}] mode={phase['mode']} initializer={record['initializer']} outcome={record['final']['outcome']}", flush=True)
        require(len(result["cases"]) == len(phase["cases"])*3, "whole preregistered episode matrix incomplete")
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        require(runner.process.returncode == 0, "evaluation runner did not close cleanly")
        if phase.get("service_probes") is True:
            require(set(probes) == {"gru", "mlp"}, "no actual processed input for both model fault probes")
            result["service_probes"] = {name: api.run_service_probe(getattr(args, name+"_checkpoint"), row,
                args.output / ("serve-probe-"+name), initial_game_ms=start, device="cpu",
                inference_cpu_threads=config["runtime"].get("inference_cpu_threads", 2)) for name, (row, start) in probes.items()}
        result.update(status="collected", profile=api.PROFILE, timing_profile=api.TIMING_PROFILE,
                      services_after=api.service_snapshot(), resources=runner.samples, collector_resource=api.process_sample(os.getpid()))
        manifest["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(args.output / "manifest.json", manifest)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
        print(f"evaluation v2 failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        try:
            if runner is not None:
                result["resources"] = runner.samples
                runner.terminate()
        finally:
            if gc_scope is not None and gc_scope.enabled:
                manifest["collector_gc"] = gc_scope.close()
                write_json(args.output / "collector-gc.json", gc_scope.profile)
                write_json(args.output / "manifest.json", manifest)
            write_json(args.output / "suite.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial-workers",dest="native_serial_workers",action="store_true")
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "scenario", "melee-scenario",
                 "native-reference", "reference-run", "policy-code-root", "config", "output", "gru-checkpoint", "mlp-checkpoint"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--phase", choices=("realtime-reaction", "screening"), required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("native evaluation and model forwards run only on dev-ubuntu, never the Mac")
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    args.output.mkdir(exist_ok=False)
    return run(args, json.loads(args.config.read_text()))


if __name__ == "__main__":
    raise SystemExit(main())
