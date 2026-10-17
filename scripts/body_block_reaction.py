#!/usr/bin/env python3
"""Separate reaction-fixture diagnostic using immutable stage 8 policies.

Only the native engine prepares an incoming attack and holds the defender.
Preparation is raw evidence, never recurrent model history or quality credit.
The original neutral block results remain unchanged. This client runs on Linux
and explicitly imports its policy, normalizer and arena adapters from v6.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import time


class ReactionFixtureError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ReactionFixtureError(message)


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")


def ready_anchor(row, arena):
    """Only an actual current periodic, visible incoming attack can arm play."""
    target = row.get("target", {})
    own = row.get("self", {})
    return (row.get("phase") == "post_simulation_post_animation" and row.get("periodic") is True and
            row.get("event_only") is False and row.get("t_game_ms") == arena.get("game_ms") and
            row.get("session_epoch") == arena.get("session_epoch") and row.get("world_epoch") == arena.get("world_epoch") and
            row.get("entity_id") == arena.get("primary_entity_id") and
            row.get("native_decision_eligible") is True and own.get("hp") == own.get("hp_max") == 300 and
            own.get("attack_anim") is False and own.get("defence_active") is False and
            target.get("entity_id") == arena.get("opponent_entity_id") and
            all(target.get(key) is True for key in ("present", "visible", "current_valid", "in_w_range", "melee_line_clear", "attack_anim", "prehit")) and
            target.get("hp") == target.get("hp_max") == 300)


def preparation_proof(initial, prepared, before, after, control, mechanics, anchor):
    """Fail closed on any defender action, RNG draw, hit or artificial scene."""
    primary, opponent = initial["primary_entity_id"], initial["opponent_entity_id"]
    require(ready_anchor(anchor, prepared), "armed source is not a fresh actual periodic incoming attack")
    require(all(initial[key] == prepared[key] for key in ("session_epoch", "world_epoch", "primary_entity_id", "opponent_entity_id")),
            "reaction preparation changed episode identity")
    for key in ("hp", "received_damage", "outgoing_damage", "blocked_hits"):
        require(all(a[key] == (300 if key == "hp" else 0) for a in prepared["actors"]), "hit or HP change before reaction activation")
    require(before["purity_state"]["rng_state"] == after["purity_state"]["rng_state"], "primary RNG advanced during held preparation")
    require(before["purity_state"]["queue"] == after["purity_state"]["queue"] == [0]*6, "primary fight queue changed during preparation")
    require(before["control_generation"] == after["control_generation"] == anchor["control_generation"], "preparation ownership generation changed")
    require(not any(x.get("entity_id") == primary and x.get("phase") in ("accepted", "started", "completed", "cancelled") and
                    (x.get("command_id", 0) > 0 or (type(x.get("action_id")) is int and x["action_id"] > 0)) for x in control),
            "primary primitive occurred before activation")
    mapped = {x.get("actor_id") for x in mechanics if x.get("phase") == "arena_actor_mapping" and x.get("observation_entity_id") == primary}
    require(len(mapped) == 1 and None not in mapped, "primary has no unique real native actor mapping")
    require(not any(x.get("actor_id") in mapped and x.get("phase") in ("native_choice", "native_consume") for x in mechanics),
            "primary native chooser executed before reaction activation")
    require(not any(x.get("phase") in ("hit_attempt", "damage", "defence_result") and x.get("t_game", 0) > initial["game_ms"] for x in mechanics),
            "hit evidence occurred before activation")
    started = [x for x in control if x.get("entity_id") == opponent and x.get("phase") == "started" and x.get("action_id") == 6]
    require(len(started) == 1 and started[0]["t_game_ms"] <= anchor["t_game_ms"], "incoming attack lacks a unique real started command")
    accepted = [x for x in control if x.get("entity_id") == opponent and x.get("phase") == "accepted" and
                x.get("command_id") == started[0]["command_id"] and x.get("execution_id") == started[0]["execution_id"] and x.get("action_id") == 6]
    require(len(accepted) == 1, "incoming attack start lacks its accepted API receipt")
    require(all(a.get("command_id", 0) == 0 for a in prepared["actors"] if a["entity_id"] == primary), "primary command ID exists during preparation")
    return {"anchor": anchor, "anchor_sha256": json_sha(anchor), "primary_before": before, "primary_prepared": after,
            "attacker_accepted": accepted[0], "attacker_started": started[0],
            "prepared_game_ms": anchor["t_game_ms"], "preparation_game_ms": anchor["t_game_ms"]-initial["game_ms"],
            "primary_commands_before_activation": 0, "primary_native_choices_before_activation": 0,
            "primary_rng_unchanged": True, "preparation_quality_credit": False}


def load_frozen(args, config):
    require(args.policy_code_root == args.reference_run / "code", "policy root must be the explicit immutable reference code")
    require(args.reference_run.name == config["reference"]["run_name"], "unexpected policy reference run")
    for name in ("gru", "mlp"):
        path = getattr(args, name+"_checkpoint")
        require(path == args.reference_run / "models" / name / "best.pt" and
                file_sha(path) == config["reference"][name+"_checkpoint_sha256"], "selected frozen checkpoint differs")
    require(file_sha(args.reference_run / "dataset/normalization.json") == config["reference"]["normalizer_sha256"], "frozen normalization differs")
    for path in reversed((args.policy_code_root / "body-service/src", args.policy_code_root / "scripts")):
        sys.path.insert(0, str(path))
    modules = {name: importlib.import_module(name) for name in ("body_policy_diagnostics", "body_policy_suite", "body_trajectory_dataset", "gothic_body.runtime")}
    for module in modules.values():
        require(Path(module.__file__).resolve().is_relative_to(args.policy_code_root), "policy import escaped the explicit immutable code root")
    return modules


def prepare(env, case, config, api, journal, normalizer):
    primary, opponent = env.initial["primary_entity_id"], env.initial["opponent_entity_id"]
    before = env.body_status(primary)
    start = env.runner.call({"cmd": "arena_start", "provider": "scripted"})
    env._arena(start)
    env.rollout_started = time.monotonic()
    api.append_json(journal, {"kind": "preparation_start", "response": start, "quality_credit": False})
    sent = False
    for _ in range(config["preparation"]["max_substeps"]):
        if not sent and env.eligible(opponent, 6):
            env.try_act(opponent, 6)
            sent = True
            api.append_json(journal, {"kind": "attacker_command", "receipt": env.command_responses[-1], "quality_credit": False})
        step = env.step(1)
        if step.done:
            raise ReactionFixtureError("arena terminated during held preparation")
        if env.arena["game_ms"]-env.initial["game_ms"] > config["preparation"]["max_game_ms"]:
            raise ReactionFixtureError("held incoming-attack preparation exceeded its declared game-time budget")
        reaction = env.arena.get("reaction", {})
        if reaction.get("phase") != "armed":
            continue
        require(sent, "native reaction armed before the real attacker command")
        require(reaction.get("enabled") is True and reaction.get("periodic") is True,
                "native reaction armed source is not explicitly ordinary periodic")
        row = env.latest.get(primary)
        require(isinstance(row, dict) and ready_anchor(row, env.arena), "engine armed without a current periodic incoming-attack row")
        require(reaction.get("observation_seq") == row["observation_seq"], "native armed source sequence differs")
        require(reaction.get("game_ms") == row["t_game_ms"] and reaction.get("sim_step") == row["sim_step"],
                "native armed source clock differs")
        after = env.body_status(primary)
        mechanics = [json.loads(line) for line in (env.output / "events.jsonl").read_text().splitlines() if line]
        proof = preparation_proof(env.initial, env.arena, before, after, env.events, mechanics, row)
        for source, native_key in (("command_id", "opponent_command_id"), ("execution_id", "opponent_execution_id"),
                                   ("t_game_ms", "opponent_started_game_ms"), ("sim_step", "opponent_started_sim_step"),
                                   ("event_seq", "opponent_started_event_seq")):
            require(proof["attacker_started"].get(source) == reaction.get(native_key), "native armed attacker execution differs")
        proof.update(reset_ready=env.initial, prepared_before_activation=env.arena, native_reaction=reaction)
        features = api.feature_module.pack_features(row, normalizer, sequence_dt_game_ms=0)
        proof.update(prepared_anchor_feature_count=len(features), normalizer_sha256=config["reference"]["normalizer_sha256"],
                     prepared_anchor_normalized_features_sha256=json_sha(features), first_sequence_dt_game_ms=0)
        api.write_json(env.output / "preparation.json", proof)
        return row, proof
    raise ReactionFixtureError("no actual prepared windup within bounded preparation")


def activate(env, provider, anchor, proof, api, journal):
    primary, opponent = env.initial["primary_entity_id"], env.initial["opponent_entity_id"]
    request = {"cmd": "arena_reaction_activate", "session_epoch": env.identity.session_epoch,
               "world_epoch": env.identity.world_epoch, "primary_entity_id": primary, "opponent_entity_id": opponent,
               "primary_control_generation": env.leases[primary]["control_generation"],
               "opponent_control_generation": env.leases[opponent]["control_generation"],
               "observation_seq": anchor["observation_seq"], "issued_game_ms": anchor["t_game_ms"],
               "provider": "native" if provider == "native" else "scripted", "mode": "lockstep"}
    response = env.runner.call(request)
    prepared = env._arena(response)
    require(prepared["game_ms"] == anchor["t_game_ms"] and prepared.get("reaction", {}).get("phase") == "active",
            "reaction activation did not preserve the common prepared game-time boundary")
    require(prepared["reaction"].get("activation_game_ms") == anchor["t_game_ms"] and
            prepared["reaction"].get("activation_provider") == request["provider"], "native reaction activation identity differs")
    if provider == "native":
        env.leases.pop(primary)
        require(env.body_status(primary)["owner"] == "native", "native reaction activation did not restore native primary")
    else:
        status = env.body_status(primary)
        require(status["control_generation"] == anchor["control_generation"] and
                status["observation_seq"] == anchor["observation_seq"] and status["can_decide"] is True,
                "activation invalidated the exact periodic neural command anchor")
    # No pre-arm row enters the new model's memory. The actual prepared source
    # defines its new scene boundary, giving a first dt of exactly zero.
    for row in env.pulled:
        if row["entity_id"] == primary and api.observation_key(row) != api.observation_key(anchor):
            api.append_json(journal, {"kind": "excluded_observation", "reason": "before_reaction_scene_boundary",
                                     "observation": row, "quality_credit": False})
    env.pulled = [anchor]
    env.latest[primary] = anchor
    record = {"request": request, "response": response, "prepared_start": prepared,
              "initial_game_ms": anchor["t_game_ms"], "first_sequence_dt_game_ms": 0,
              "preparation_file": "preparation.json", "preparation_sha256": api.sha256(env.output / "preparation.json")}
    api.append_json(journal, {"kind": "reaction_activation", **record})
    api.write_json(env.output / "activation.json", record)
    return record


def rollout(env, policy, case, config, api, journal, latency):
    primary = env.initial["primary_entity_id"]
    stats, streams, seen = Counter(), Counter(), set()
    first_step = env.native_sim_step
    deadline = time.monotonic()+config["rollout"]["wall_timeout_s"]
    while time.monotonic() < deadline:
        if policy is not None:
            api.process_rows(env, policy, (primary,), streams, journal, latency, stats, shadow=False,
                             command_enabled=not bool(env.arena.get("terminated") or env.arena.get("truncated")))
        if env.arena.get("terminated") or env.arena.get("truncated"):
            return api.finish_episode(env, policy, streams, journal, latency, stats, shadow=False)
        row = env.latest.get(primary)
        if policy is not None and row is not None and env.eligible(primary) and row.get("periodic") is True:
            key = api.observation_key(row)
            if key not in seen:
                seen.add(key)
                stats["latest_eligible_periodic_opportunities"] += 1
                before = time.monotonic_ns()
                response = policy.arbitrate(row, stream_seq=streams[primary])
                entry = {"kind": "arbitration", "observation": row, "response": response, "profile": api.PROFILE,
                         "timing": {"collector_received_wall_monotonic_ns": env.runner.received.get(key),
                                    "arbitration_begin_wall_monotonic_ns": before, "arbitration_end_wall_monotonic_ns": time.monotonic_ns()}}
                if response.get("status") == "decision" and response.get("action_id") is not None:
                    command = env.try_act(primary, response["action_id"])
                    receipt = env.command_responses[-1]
                    entry.update(command_id=command, command_request=receipt["request"], command_response=receipt["response"],
                                 execution_id=receipt["response"].get("execution_id"), terminal_censored=receipt.get("terminal_transition") is True)
                    entry["timing"]["command_sent_wall_monotonic_ns"] = receipt["sent_wall_monotonic_ns"]
                    stats["accepted_model_commands"] += int(command is not None)
                    stats["terminal_censored_commands"] += int(entry["terminal_censored"])
                    stats["rejected_model_commands"] += int(command is None and not entry["terminal_censored"])
                    if command is not None:
                        stats[f"action_{response['action_id']}"] += 1
                elif response.get("status") == "fallback":
                    stats["runtime_fallbacks"] += 1
                    env.release_to_native(primary, response["reason"])
                api.append_json(journal, entry)
                api.append_json(latency, {"kind": "arbitration", "observation_key": list(key), **entry["timing"],
                                          "action_id": response.get("action_id"), "command_id": entry.get("command_id"),
                                          "execution_id": entry.get("execution_id"), "shadow": False})
        step = env.step(1)
        require(step.advanced_substeps > 0 or step.done, "active reaction fixture made no real simulation progress")
        require(env.native_sim_step-first_step <= config["rollout"]["max_substeps"], "reaction rollout exceeded substep budget")
    raise ReactionFixtureError("reaction rollout wall watchdog expired")


def run(args, config):
    result = {"schema_version": 1, "kind": "body_block_reaction_diagnostic", "status": "infrastructure_failure",
              "quality_gate_evaluated": False, "original_v6_credit": False, "cases": []}
    runner = None
    try:
        modules = load_frozen(args, config)
        api, runtime = modules["body_policy_diagnostics"], modules["gothic_body.runtime"]
        normalizer = json.loads((args.reference_run / "dataset/normalization.json").read_text())
        require(config["scope"] == "known_seed_diagnostic_only" and config["policies"] == ["native", "gru", "mlp"] and config["cases"] == [
            {"name": f"block-reaction-223-{side}", "task": "block", "seed": 223, "side": side, "distance_cm": 150, "timeout_ms": 5000}
            for side in (0, 1)], "known diagnostic case matrix differs; screening requires a separate preregistration")
        manifest = api.provenance(args)
        manifest.update(kind=result["kind"], scope=config["scope"], mode="lockstep", profile=api.PROFILE,
                        config_file=str(args.config), driver_sha256=file_sha(__file__), policy_code_root=str(args.policy_code_root),
                        reference_run=str(args.reference_run), reference_environment_sha256=file_sha(args.reference_run / "environment.json"),
                        checkpoint_sha256={name: file_sha(getattr(args, name+"_checkpoint")) for name in ("gru", "mlp")},
                        policy_modules={name: {"path": module.__file__, "sha256": file_sha(module.__file__)} for name, module in modules.items()},
                        initialization="actual prepared incoming attack; zero hidden state; first periodic dt0",
                        prepared_scene_changes_core_features=False, original_v6_credit=False)
        api.write_json(args.output / "manifest.json", manifest)
        # Allocate both selected policies before touching an engine episode.
        policies = {name: runtime.PolicyRuntime(getattr(args, name+"_checkpoint"), device="cpu", max_actors=1) for name in ("gru", "mlp")}
        runner = api.DiagnosticRunner(args)
        runner.mode = "lockstep"
        for case in config["cases"]:
            for provider in config["policies"]:
                name = case["name"]+"-"+provider
                runner.arena_options = {"reaction_hold": True}
                env = api.DiagnosticEpisodeEnv(runner, args.scenario, owner_provider="stage8-block-reaction",
                                               command_ttl_ms=config["runtime"]["command_ttl_ms"])
                initial = env.reset(output=args.output / name, task="block", seed=case["seed"], side=case["side"],
                                    distance_cm=case["distance_cm"], timeout_ms=case["timeout_ms"], provider="scripted")
                watch = runner.call({"cmd": "body_visibility_watch", "enabled": True})
                require(watch.get("ok") is not False, "native visibility capture was not enabled")
                policy = policies.get(provider)
                record = {"name": name, "case": case, "policy": provider, "mode": "lockstep", "initial": initial,
                          "shadow": False, "profile": api.PROFILE, "command_ttl_ms": env.command_ttl_ms,
                          "scope": config["scope"], "original_v6_credit": False, "visibility_watch": watch,
                          "checkpoint_sha256": policy.checkpoint_sha256 if policy else None,
                          "normalizer_sha256": policy.normalizer_sha256 if policy else None}
                with (env.output / "policy-inputs.jsonl").open("x") as journal, (env.output / "latency.jsonl").open("x") as latency:
                    anchor, proof = prepare(env, case, config, api, journal, normalizer)
                    record["activation"] = activate(env, provider, anchor, proof, api, journal)
                    record["prepared_start"] = record["activation"]["prepared_start"]
                    if policy is not None:
                        require(policy.normalizer_sha256 == config["reference"]["normalizer_sha256"], "runtime uses another frozen normalizer")
                        policy.begin_episode(initial["session_epoch"], initial["world_epoch"], initial_game_ms=anchor["t_game_ms"])
                    record.update(rollout(env, policy, case, config, api, journal, latency))
                record.update(model_journal_sha256=file_sha(env.output / "policy-inputs.jsonl"), latency_sha256=file_sha(env.output / "latency.jsonl"))
                api.write_json(env.output / "episode.json", record)
                result["cases"].append(record)
                api.write_json(args.output / "suite.json", result)
                print(f"reaction [{name}] outcome={record['final']['outcome']} reason={record['final']['reason']}", flush=True)
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        require(runner.process.returncode == 0, "reaction runner did not close cleanly")
        result.update(status="collected", resources=runner.samples, services_after=api.service_snapshot())
        manifest["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        api.write_json(args.output / "manifest.json", manifest)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
        print(f"reaction diagnostic failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        if runner is not None:
            result["resources"] = runner.samples
            runner.terminate()
        write_json(args.output / "suite.json", result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "source", "engine-library", "scenario", "melee-scenario",
                 "native-reference", "reference-run", "policy-code-root", "config", "output", "gru-checkpoint", "mlp-checkpoint"):
        parser.add_argument("--"+name, type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("reaction simulations and policies run only on dev-ubuntu, never the Mac")
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    args.output.mkdir(exist_ok=False)
    return run(args, json.loads(args.config.read_text()))


if __name__ == "__main__":
    raise SystemExit(main())
