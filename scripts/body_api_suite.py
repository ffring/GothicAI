#!/usr/bin/env python3
"""Stage 4 scripted-provider/fault suite in the real CPU runner on dev-ubuntu.

This driver sends primitives through the shared body API. It never animates,
calculates damage, or simulates an NPC in Python. Test fixtures only establish
initial states and inject failures through explicit native test endpoints.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from body_headless_suite import service_snapshot, sha256, write_json
from body_observe_suite import ObserverRunner, provenance

ACTIONS = ("continue", "approach", "turn_to_target", "strafe_left", "strafe_right", "jump_back",
           "attack_front", "attack_left", "attack_right", "block", "wait_200", "wait_300")
PROVIDER = "stage4-scripted"


def brief(value: dict[str, Any]) -> str:
    """Keep diagnostics readable; full requests, replies and RNG stay in raw IPC."""
    return json.dumps({key: value[key] for key in ("reason", "error", "accepted", "entity_id", "control_generation", "goal_version", "command_id", "execution_id", "owner", "release_pending", "action_mask", "can_decide") if key in value}, ensure_ascii=False, separators=(",", ":"))


class ScriptedRunner(ObserverRunner):
    """Full IPC evidence, with a separate journal for each fresh-world case."""
    def record(self, direction: str, value: dict[str, Any]) -> None:
        self.ipc.write(json.dumps({"direction": direction, "wall_monotonic": time.monotonic(),
                                   "value": value}, ensure_ascii=False, separators=(",", ":"))+"\n")
        self.ipc.flush()

    @contextmanager
    def case_log(self, output: Path):
        self.ipc.close()
        self.ipc = (output / "ipc.jsonl").open("w", encoding="utf-8")
        yield
        self.ipc.flush()

    def call(self, command: dict[str, Any]) -> dict[str, Any]:
        return self.request(command, allow_error=True)


def actor_status(runner: ScriptedRunner, entity_id: int | None = None) -> dict[str, Any]:
    status = runner.call({"cmd": "body_control_status"})
    actors = status.get("actors")
    if not isinstance(actors, list):
        raise RuntimeError(f"body_control_status has no actor array: {brief(status)}")
    actor = next((row for row in actors if entity_id is None or row.get("entity_id") == entity_id), None)
    if actor is None:
        raise RuntimeError(f"requested native actor absent from body_control_status: {entity_id}")
    return actor


def lease_fields(actor: dict[str, Any]) -> dict[str, Any]:
    return {key: actor[key] for key in ("entity_id", "session_epoch", "world_epoch", "control_generation")}


def acquire(runner: ScriptedRunner, entity: int, mode: str, timeout_ms: int) -> dict[str, Any]:
    actor = actor_status(runner, entity)
    response = runner.call({"cmd": "body_acquire", **lease_fields(actor), "provider": PROVIDER,
                            "body_control": True, "mode": mode, "timeout_ms": timeout_ms,
                            "target_entity_id": actor["target_entity_id"]})
    if response.get("accepted") is not True:
        raise RuntimeError(f"cannot acquire scripted body: {brief(response)}")
    return actor_status(runner, entity)


def heartbeat(runner: ScriptedRunner, entity: int) -> None:
    actor = actor_status(runner, entity)
    response = runner.call({"cmd": "body_heartbeat", **lease_fields(actor), "provider": PROVIDER})
    if response.get("accepted") is not True:
        raise RuntimeError(f"scripted heartbeat failed: {brief(response)}")


def action_request(actor: dict[str, Any], command_id: int, action_id: int, ttl_ms: int = 1000) -> dict[str, Any]:
    return {"cmd": "body_action", **lease_fields(actor), "provider": PROVIDER,
            "goal_version": actor["goal_version"], "target_entity_id": actor["target_entity_id"],
            "command_id": command_id, "action_id": action_id,
            "observation_seq": actor["observation_seq"], "issued_game_ms": actor["observed_game_ms"],
            "ttl_ms": ttl_ms}


def advance(runner: ScriptedRunner, entity: int, substeps: int = 1) -> dict[str, Any]:
    heartbeat(runner, entity)
    result = runner.call({"cmd": "advance", "substeps": substeps})
    runner.sample()
    runner.call({"cmd": "body_observations", "max": 256})
    return result


def prepare(runner: ScriptedRunner, actor: dict[str, Any], action_id: int) -> None:
    result = runner.call({"cmd": "body_fixture", "kind": "prepare", "entity_id": actor["entity_id"],
                          "target_entity_id": actor["target_entity_id"], "action_id": action_id})
    if result.get("ok") is False:
        raise RuntimeError(f"native fixture preparation failed: {brief(result)}")


def finish_command(runner: ScriptedRunner, args: argparse.Namespace, entity: int,
                   command_id: int, label: str) -> None:
    for _ in range(args.max_substeps):
        advance(runner, entity)
        events = runner.call({"cmd": "body_events"}).get("events", [])
        terminal = next((event for event in events if event.get("command_id") == command_id and event.get("phase") in ("completed", "cancelled")), None)
        if terminal is not None:
            if terminal.get("phase") == "cancelled":
                raise RuntimeError(f"scripted {label} cancelled: {brief(terminal)}")
            return
    raise RuntimeError(f"scripted {label} failed to reach a terminal engine event")


def finish_handoff(runner: ScriptedRunner, args: argparse.Namespace, entity: int) -> None:
    """Keep advancing the actual legal segment until the arbiter returns native."""
    remaining = args.max_substeps
    while remaining:
        current = actor_status(runner, entity)
        if current.get("owner") == "native" and current.get("control_generation") == 0:
            # Let the deferred native routine make an actual next combat choice.
            runner.call({"cmd": "advance", "substeps": 30})
            runner.call({"cmd": "body_events"})
            runner.sample()
            return
        substeps = min(10, remaining)
        response = runner.call({"cmd": "advance", "substeps": substeps})
        if response.get("ok") is False or response.get("infrastructure_failure") is True:
            raise RuntimeError(f"native handoff infrastructure failure: {brief(response)}")
        runner.call({"cmd": "body_observations", "max": 256})
        runner.sample()
        remaining -= substeps
    raise RuntimeError(f"native handoff exceeded bounded {args.max_substeps} substeps: {brief(current)}")


def setup(runner: ScriptedRunner, args: argparse.Namespace, output: Path, mode: str = "lockstep") -> dict[str, Any]:
    runner.call({"cmd": "reset", "scenario": str(args.scenario), "output_dir": str(output), "mode": mode})
    runner.call({"cmd": "body_watch", "enabled": True, "rate_hz": 20, "max_actors": 4})
    for _ in range(12):
        runner.call({"cmd": "advance", "substeps": 10})
        runner.call({"cmd": "body_observations", "max": 256})
        status = runner.call({"cmd": "body_control_status"})
        if status.get("actors"):
            return status["actors"][0]
    raise RuntimeError("scenario failed to initialize native melee actors")


def run_primitive(runner: ScriptedRunner, args: argparse.Namespace, name: str, output: Path) -> dict[str, Any]:
    action_id = ACTIONS.index(name)
    actor = setup(runner, args, output)
    prepare(runner, actor, 11 if action_id == 0 else action_id)
    actor = acquire(runner, actor["entity_id"], "lockstep", args.timeout_ms)
    entity = actor["entity_id"]
    advance(runner, entity, 2)
    command_id = 1
    # Correct orientation through a real allowed primitive. The provider never
    # weakens focus checks or edits the body to make an attack pass its mask.
    current = actor_status(runner, entity)
    if action_id in (6, 7, 8) and current.get("action_mask", [False]*12)[action_id] is False:
        if current.get("action_mask", [False]*12)[2] is not True:
            raise RuntimeError(f"scripted {name} is unavailable without an allowed turn: {brief(current)}")
        response = runner.call(action_request(current, command_id, 2))
        if response.get("accepted") is not True:
            raise RuntimeError(f"attack orientation prerequisite rejected: {brief(response)}")
        finish_command(runner, args, entity, command_id, "attack orientation")
        command_id += 1
        advance(runner, entity, 2)
    if action_id == 0:
        request = action_request(actor_status(runner, entity), 1, 11)
        response = runner.call(request)
        if response.get("accepted") is not True:
            raise RuntimeError(f"continue prerequisite wait rejected: {brief(response)}")
        advance(runner, entity)
        command_id = 2
    actor = actor_status(runner, entity)
    response = runner.call(action_request(actor, command_id, action_id))
    if response.get("accepted") is not True:
        raise RuntimeError(f"scripted {name} rejected at prepared state: {brief(response)}")
    finish_command(runner, args, entity, command_id, name)
    runner.call({"cmd": "body_release", **lease_fields(actor_status(runner, entity)), "provider": PROVIDER})
    runner.sample()
    return {"name": name, "command_id": command_id, "mode": "lockstep"}


def run_fault(runner: ScriptedRunner, args: argparse.Namespace, name: str, output: Path) -> dict[str, Any]:
    mode = "realtime" if name.startswith("realtime_") else "lockstep"
    actor = setup(runner, args, output, mode)
    prepare(runner, actor, 1 if name == "range_mask" else 6 if name in ("ownership", "realtime_disconnect", "action_chain") else 11)
    actor = acquire(runner, actor["entity_id"], mode, args.timeout_ms)
    entity = actor["entity_id"]
    advance(runner, entity, 2)
    actor = actor_status(runner, entity)
    result: dict[str, Any] = {"name": name, "mode": mode}
    command = action_request(actor, 1, 11)
    if name == "action_chain":
        command_id = 1
        result["commands"] = []
        for requested in (6, 11, 8):
            for _ in range(args.max_substeps):
                current = actor_status(runner, entity)
                mask = current.get("action_mask", [False]*12)
                if current.get("can_decide") is True and mask[requested] is True:
                    break
                if requested in (6, 8) and current.get("can_decide") is True and mask[2] is True:
                    response = runner.call(action_request(current, command_id, 2))
                    if response.get("accepted") is not True:
                        raise RuntimeError(f"chain orientation rejected: {brief(response)}")
                    finish_command(runner, args, entity, command_id, "chain orientation")
                    command_id += 1
                advance(runner, entity, 2)
            else:
                raise RuntimeError(f"healthy same-lease provider never recovered {ACTIONS[requested]}: {brief(current)}")
            response = runner.call(action_request(current, command_id, requested))
            if response.get("accepted") is not True:
                raise RuntimeError(f"same-lease {ACTIONS[requested]} rejected: {brief(response)}")
            finish_command(runner, args, entity, command_id, ACTIONS[requested])
            result["commands"].append({"command_id": command_id, "action_id": requested})
            command_id += 1
        runner.call({"cmd": "body_release", **lease_fields(actor_status(runner, entity)), "provider": PROVIDER})
    elif name == "mask_purity":
        runner.call({"cmd": "body_self_test"})
        for _ in range(3):
            runner.call({"cmd": "body_control_status"})
    elif name in ("realtime_timeout", "realtime_disconnect", "lockstep_timeout"):
        if name == "realtime_disconnect":
            command["action_id"] = 6
        runner.call(command)
        advance(runner, entity)
        if name == "realtime_disconnect":
            runner.call({"cmd": "body_disconnect", **lease_fields(actor_status(runner, entity)), "provider": PROVIDER})
        # Wait only on dev-ubuntu, bounded below 60 s. Wall watchdog is pumped
        # even when the provider sends no command or lockstep does not advance.
        time.sleep((args.timeout_ms+700)/1000)
        runner.call({"cmd": "status"})
        runner.call({"cmd": "body_control_status"})
        if name != "lockstep_timeout":
            runner.call({"cmd": "body_fixture", "kind": "restore_native", "entity_id": entity,
                         "target_entity_id": actor["target_entity_id"]})
        runner.call({"cmd": "advance", "substeps": 30})
        runner.call({"cmd": "body_events"})
    elif name == "pause_resume":
        runner.call(command)
        advance(runner, entity)
        runner.call({"cmd": "pause"})
        time.sleep(.15)
        runner.call({"cmd": "resume"})
        runner.call({"cmd": "body_action", **command, "command_id": 2})
        runner.call({"cmd": "advance", "substeps": 30})
        runner.call({"cmd": "body_control_status"})
    elif name == "ownership":
        attack = {**command, "action_id": 6}
        response = runner.call(attack)
        if response.get("accepted") is not True:
            raise RuntimeError(f"ownership attack prerequisite rejected: {brief(response)}")
        for _ in range(6):
            advance(runner, entity)
            native = runner.call({"cmd": "body_events"}).get("events", [])
            started = next((row for row in native if row.get("phase") == "started" and row.get("command_id") == 1), None)
            if started:
                break
        else:
            raise RuntimeError("ownership fixture never reached actual attack start")
        result["protected_execution_id"] = started["execution_id"]
        other = {"cmd": "body_acquire", **lease_fields(actor), "provider": "other-provider",
                 "body_control": True, "mode": mode, "timeout_ms": args.timeout_ms}
        runner.call(other)
        for kind in ("soft_action", "attach_control", "dialog_control"):
            runner.call({"cmd": "body_fixture", "kind": kind, "entity_id": entity})
            runner.call({"cmd": "body_control_status"})
        runner.call({"cmd": "body_fixture", "kind": "script_takeover", "entity_id": entity})
        runner.call({**command, "command_id": 2})
        runner.call({"cmd": "body_fixture", "kind": "restore_native", "entity_id": entity,
                     "target_entity_id": actor["target_entity_id"]})
        finish_handoff(runner, args, entity)
    elif name in ("accepted_expired", "goal_changed_pending", "reset"):
        command = action_request(actor_status(runner, entity), 1, 10, ttl_ms=50 if name == "accepted_expired" else 1000)
        response = runner.call(command)
        if response.get("accepted") is not True:
            raise RuntimeError(f"pending cancellation prerequisite rejected: {brief(response)}")
        result["cancel_command_id"] = 1
        if name == "accepted_expired":
            runner.call({"cmd": "body_fixture", "kind": "lock_wait", "entity_id": entity, "duration_ms": 300})
        if name == "goal_changed_pending":
            current = actor_status(runner, entity)
            runner.call({"cmd": "body_goal", **lease_fields(current), "provider": PROVIDER,
                         "goal_version": current["goal_version"]+1, "target_entity_id": current["target_entity_id"]})
        elif name == "reset":
            # Reset closes and flushes the old native journal. The new world has
            # a separate output directory so no cancellation evidence is lost.
            next_output = output / "reset-world"
            next_output.mkdir()
            runner.call({"cmd": "reset", "scenario": str(args.scenario), "output_dir": str(next_output)})
            runner.call(command)
        if name != "reset":
            for _ in range(20):
                advance(runner, entity)
            runner.call({"cmd": "body_events"})
    else:
        fault = dict(command)
        reasons = {"mask_false": "weapon_mode", "duplicate": "accepted", "out_of_order": "out_of_order",
                   "stale_session": "stale_session", "stale_world": "stale_world", "stale_generation": "stale_generation",
                   "stale_goal": "stale_goal", "expired": "expired", "unknown_target": "target_invalid",
                   "stale_observation": "stale_observation", "stale_disconnect": "stale_generation",
                   "range_mask": "masked", "collision_mask": "target_invalid"}
        if name in ("death", "target_gone"):
            # Native death/state/target mutation requests a safe lease release.
            # The IPC pump may already have reached its boundary before the
            # following stale action arrives; both actual branches are refusals.
            result["expected_reasons"] = ["release_pending", "not_owned"]
        else:
            result["expected_reason"] = reasons[name]
        if name in ("duplicate", "out_of_order"):
            runner.call({**command, "command_id": 2})
            fault["command_id"] = 2 if name == "duplicate" else 1
        elif name == "stale_observation":
            fault["observation_seq"] += 123
        elif name == "stale_disconnect":
            fault = {"cmd": "body_disconnect", **lease_fields(actor), "provider": PROVIDER}
            fault["control_generation"] += 123
        elif name.startswith("stale_"):
            key = {"stale_session": "session_epoch", "stale_world": "world_epoch",
                   "stale_generation": "control_generation", "stale_goal": "goal_version"}[name]
            fault[key] += 123
        elif name == "unknown_target":
            fault["target_entity_id"] = 2**62
        elif name == "expired":
            # Wait for a nonsampling substep: latest observation stays unchanged
            # but its one-game-ms TTL genuinely expires before receipt.
            for _ in range(6):
                advance(runner, entity)
                current = actor_status(runner, entity)
                now = runner.call({"cmd": "status"})["game_ms"]
                if now > current["observed_game_ms"]:
                    fault = action_request(current, 1, 11, ttl_ms=1)
                    break
            else:
                raise RuntimeError("could not establish an actually expired observation TTL")
        elif name == "mask_false":
            runner.call({"cmd": "body_fixture", "kind": "weapon_undrawn", "entity_id": entity})
            fault["action_id"] = 6
        elif name == "range_mask":
            fault["action_id"] = 6
        elif name == "collision_mask":
            runner.call({"cmd": "body_fixture", "kind": "occluded", "entity_id": entity,
                         "target_entity_id": actor["target_entity_id"]})
            runner.call({"cmd": "body_control_status"})
            fault["action_id"] = 6
        elif name == "death":
            runner.call({"cmd": "body_fixture", "kind": "hp", "entity_id": entity, "hp": 0})
            runner.call({"cmd": "body_control_status"})
        elif name == "target_gone":
            runner.call({"cmd": "body_fixture", "kind": "target_gone", "entity_id": entity})
            runner.call({"cmd": "body_control_status"})
        runner.call(fault)
        # Duplicate keeps the original lease; invalidated cases need no heartbeat.
        runner.call({"cmd": "advance", "substeps": 20})
        runner.call({"cmd": "body_observations", "max": 256})
        runner.call({"cmd": "body_control_status"})
        runner.call({"cmd": "body_events"})
    runner.sample()
    return result


def run_native(runner: ScriptedRunner, args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    output = args.output / "native-off"
    output.mkdir()
    with runner.case_log(output):
        runner.call({"cmd": "reset", "scenario": str(args.melee_scenario), "output_dir": str(output), "mode": "lockstep"})
        runner.call({"cmd": "body_watch", "enabled": False})
        while True:
            response = runner.call({"cmd": "advance", "substeps": 300})
            runner.sample()
            if response.get("done"):
                break
        runner.call({"cmd": "status"})
    # Native parity keeps the archived combat/observer configuration provenance;
    # body API config is separate and never substituted for the combat scenario.
    archived = json.loads((args.native_reference / "manifest.json").read_text())
    candidate = {**manifest, **{key: archived[key] for key in ("scenario_sha256", "config_sha256")},
                 "kind": "body_api_native_off", "headless": True, "mode": "lockstep", "body_control_enabled": False}
    if sha256(args.melee_scenario) != archived["scenario_sha256"]:
        raise RuntimeError("native-off scenario bytes differ from archived pre-refactor reference")
    write_json(output / "manifest.json", candidate)
    return {"name": "native-off", "final": response, "events_file": "native-off/events.jsonl"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial-workers",dest="native_serial_workers",action="store_true")
    for name in ("runner", "assets", "save", "reference", "native-reference", "source", "engine-library", "config", "scenario", "melee-scenario", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("native simulation runs only on dev-ubuntu, never the Mac")
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.resolve())
    config = json.loads(args.config.read_text())
    args.timeout_ms = config["budgets"]["provider_watchdog_wall_ms"]
    args.max_substeps = config["budgets"]["case_substeps_max"]
    args.output.mkdir(exist_ok=True)
    result: dict[str, Any] = {"schema_version": 1, "kind": "body_api_stage4_suite", "status": "infrastructure_failure", "full_stage4_evidence": False, "cases": [], "resources": []}
    runner = None
    manifest = {}
    try:
        manifest = provenance(args)
        manifest.update(kind="body_api_stage4", observer_ownership="scripted body lease", suite_scope="smoke" if args.smoke else "full")
        write_json(args.output / "manifest.json", manifest)
        runner = ScriptedRunner(args)
        result["runner_pid"] = runner.process.pid
        if not args.smoke:
            result["native_off"] = run_native(runner, args, manifest)
        names = ["wait_300", "duplicate", "mask_purity"] if args.smoke else list(ACTIONS)+config["required_cases"]
        for name in names:
            output = args.output / name
            output.mkdir()
            with runner.case_log(output):
                case = run_primitive(runner, args, name, output) if name in ACTIONS else run_fault(runner, args, name, output)
                case.update(ipc_file=f"{name}/ipc.jsonl", events_file=f"{name}/body-control-events.jsonl",
                            mechanics_file=f"{name}/events.jsonl")
                result["cases"].append(case)
            write_json(args.output / "suite.json", result)
            print(f"body API [{name}] evidence recorded", flush=True)
        runner.call({"cmd": "close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode != 0:
            raise RuntimeError(f"runner did not close cleanly: {runner.process.returncode}")
        result.update(status="collected", resources=runner.samples, services_after=service_snapshot())
        manifest.update(services_after=result["services_after"], completed_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        write_json(args.output / "manifest.json", manifest)
        write_json(args.output / "suite.json", result)
        if not args.smoke:
            completed = subprocess.run([sys.executable, str(Path(__file__).with_name("body_api_report.py")), str(args.output), "--config", str(args.config), "--native-reference", str(args.native_reference), "--output-dir", str(args.output / "validated")], check=False, timeout=180)
            validation = json.loads((args.output / "validated/body-api.json").read_text())
            result["validation"] = {"exit_code": completed.returncode, "status": validation["status"], "issues": validation["issues"]}
            if completed.returncode != 0 or not validation["full_stage4_evidence"]:
                result["status"] = "failed"
                raise RuntimeError(f"independent raw-evidence validation failed: {validation['issues'][:8]}")
            result.update(status="passed", full_stage4_evidence=True)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result["error"] = str(error)
        print(f"body API suite failed: {error}", file=sys.stderr, flush=True)
        return 2
    finally:
        if runner is not None:
            result["resources"] = runner.samples
            runner.terminate()
        write_json(args.output / "suite.json", result)


if __name__ == "__main__":
    raise SystemExit(main())
