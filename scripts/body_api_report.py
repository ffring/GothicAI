#!/usr/bin/env python3
"""Fail-closed stage 4 validation from real runner journals; no engine is launched."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "evals/npc-body/body-api-v1.json"
EPOCH_FIELDS = ("session_epoch", "world_epoch", "control_generation")
LIFECYCLE = {"accepted", "started", "continued", "completed", "cancelled", "rejected"}
PURE_EXPECTED = {
    "valid_command": "allowed", "stale_session": "stale_session", "stale_world": "stale_world",
    "stale_generation": "stale_generation", "stale_goal": "stale_goal", "unknown_target": "target_invalid",
    "stale_observation": "stale_observation", "game_expiry": "expired", "realtime_wall_expiry": "expired_wall",
    "lockstep_wall_not_game_expiry": "allowed", "bounded_single_slot": "command_busy",
    "continue_locked_execution": "allowed", "collision_mask": "masked", "lock_mask": "locked",
    "native_wait_promotion": "3", "policy_wait_same_promotion": "3", "native_attack_focus_normalization": "12"
}


def integer(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def strict_json(text: str) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value
    def constant(value):
        raise ValueError(f"non-finite JSON constant: {value}")
    value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    def finite(item):
        if type(item) is float and not math.isfinite(item):
            raise ValueError("non-finite JSON number")
        if isinstance(item, dict):
            for child in item.values():
                finite(child)
        elif isinstance(item, list):
            for child in item:
                finite(child)
    finite(value)
    return value


def read_json(path: Path) -> dict[str, Any]:
    value = strict_json(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: object required")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for index, line in enumerate(path.read_text().splitlines(), 1):
        if not line:
            raise ValueError(f"{path.name}:{index}: empty record")
        value = strict_json(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{index}: object required")
        rows.append(value)
    if not rows:
        raise ValueError(f"{path.name}: no records")
    return rows


def evidence_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("missing evidence path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("evidence path escapes run")
    if not path.is_file():
        raise ValueError(f"evidence missing: {relative}")
    return path


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            result.update(chunk)
    return result.hexdigest()


def service_identity(snapshot: Any) -> dict[str, dict[str, str]]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("services"), dict):
        raise ValueError("production service snapshots missing")
    result = {}
    for name in ("gothic-voxtral", "gothic-voxcpm"):
        text = snapshot["services"].get(name)
        if not isinstance(text, str):
            raise ValueError(f"production service snapshot missing: {name}")
        values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        identity = {key: values.get(key, "") for key in ("ActiveState", "SubState", "MainPID")}
        # A host without the unit installed (gate host apart from the service host) has
        # nothing to protect; an installed but stopped service is still a failure.
        absent = (values.get("LoadState") == "not-found" and identity["ActiveState"] == "inactive" and
                  identity["SubState"] == "dead" and identity["MainPID"] == "0")
        if absent:
            identity["LoadState"] = "not-found"
        elif identity["ActiveState"] != "active" or identity["SubState"] != "running" or not identity["MainPID"].isdigit() or int(identity["MainPID"]) <= 0:
            raise ValueError(f"production service not active with an actual PID: {name}")
        result[name] = identity
    return result


def event_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in (*EPOCH_FIELDS, "entity_id", "command_id"))


def validate_pure_gate(value: dict[str, Any]) -> list[str]:
    issues = []
    checks = value.get("checks")
    if not isinstance(checks, list):
        return ["native value-gate self-test checks missing"]
    for name, expected in PURE_EXPECTED.items():
        found = [check for check in checks if isinstance(check, dict) and check.get("name") == name]
        if len(found) != 1 or found[0].get("actual") != expected or found[0].get("expected") != expected:
            issues.append(f"native value gate {name}: actual result missing or differs from contract")
    purity = [check for check in checks if isinstance(check, dict) and check.get("name") == "mask_repeat_purity"]
    if len(purity) != 1 or not isinstance(purity[0].get("actual"), str) or len(purity[0]["actual"]) != 12 or set(purity[0]["actual"])-{"0", "1"} or purity[0]["actual"] != purity[0].get("expected"):
        issues.append("native value gate: repeated mask bytes changed or absent")
    return issues


def validate_scene(rows: list[dict[str, Any]]) -> list[str]:
    issues = []
    for row in rows:
        name = row.get("kind")
        if isinstance(name, str) and (name.endswith("_error") or name == "error"):
            issues.append(f"native scenario failure: {name}: {row.get('reason', 'no reason')}")
    setups = [row for row in rows if row.get("kind") == "body_api_setup"]
    if len(setups) != 1:
        return issues+["exactly one real body_api_setup required"]
    setup = setups[0]
    actors = setup.get("actors")
    if setup.get("ownership") != "native" or setup.get("seed") != 17 or not isinstance(actors, list) or len(actors) != 2:
        return issues+["body API scene must initialize exactly two native mortal actors"]
    if len({actor.get("entity_id") for actor in actors}) != 2 or {actor.get("symbol") for actor in actors} != {"BDT_1003_BANDIT_M", "PAL_255_RITTER"}:
        issues.append("body API scene runtime IDs/script instances are not distinct")
    for actor in actors:
        if not integer(actor.get("entity_id"), 1) or type(actor.get("hp")) is not int or actor.get("hp") != 1000000 or actor.get("hp_max") != 1000000 or actor.get("immortal") is not False:
            issues.append("body API scene mortality/high-HP setup differs from declared fixture")
    return issues


def validate_lifecycle(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Check event ordering independently from queue consumption/driver responses."""
    issues, commands, coverage = [], defaultdict(list), Counter()
    previous_event = None
    for index, row in enumerate(rows):
        seq = row.get("event_seq")
        if not integer(seq, 1) or previous_event is not None and seq != previous_event+1:
            issues.append(f"row {index}: missing/duplicate/out-of-order native event sequence")
        previous_event = seq
        if row.get("event") != "body_control_event" or row.get("schema_version") != 1:
            issues.append(f"row {index}: invalid native event schema")
        for key in (*EPOCH_FIELDS, "entity_id", "t_game_ms", "t_wall_ms"):
            if not integer(row.get(key), 1 if key in ("session_epoch", "world_epoch", "entity_id") else 0):
                issues.append(f"row {index}: invalid {key}")
        phase = row.get("phase")
        if phase in LIFECYCLE and integer(row.get("command_id"), 1):
            commands[event_identity(row)].append(row)
    for key, events in commands.items():
        phases = [row["phase"] for row in events]
        counts = Counter(phases)
        # Identical retransmission may create a rejected duplicate event. It may
        # never add a second accepted/start/completion for the original command.
        for phase in ("accepted", "started", "continued", "completed", "cancelled"):
            if counts[phase] > 1:
                issues.append(f"command {key}: repeated {phase}")
        activity = [phase for phase in phases if phase != "rejected"]
        if activity and activity[0] != "accepted":
            issues.append(f"command {key}: lifecycle lacks initial accepted")
        if "started" in phases and "continued" in phases:
            issues.append(f"command {key}: cannot be both new start and continuation")
        if "completed" in phases and not any(phase in phases for phase in ("started", "continued")):
            issues.append(f"command {key}: completion without engine start/continuation")
        if "completed" in phases and "cancelled" in phases:
            issues.append(f"command {key}: two terminal states")
        terminal = [i for i, phase in enumerate(activity) if phase in ("completed", "cancelled")]
        if terminal and terminal[0] != len(activity)-1:
            issues.append(f"command {key}: activity after terminal")
        active = [row for row in events if row["phase"] in ("started", "continued")]
        for row in active:
            if not integer(row.get("execution_id"), 1):
                issues.append(f"command {key}: no native execution identity")
            if row["phase"] == "started":
                before, after = row.get("pose_before"), row.get("pose_after")
                reason = row.get("reason")
                if not isinstance(before, str) or not isinstance(after, str) or not after:
                    issues.append(f"command {key}: no native pose/segment identity")
                elif before == after and not (reason == "wait_started" and integer(row.get("remaining_lock_game_ms"), 1) or reason == "movement_started" and row.get("effective_primitive") in (2, 12)):
                    issues.append(f"command {key}: start has no changed native segment/movement/wait")
                if row.get("segment_started") is not True:
                    issues.append(f"command {key}: native segment start marker missing")
            else:
                if not any(other.get("phase") == "started" and other.get("execution_id") == row.get("execution_id") and other.get("t_game_ms", 0) <= row.get("t_game_ms", 0) for other in rows):
                    issues.append(f"command {key}: continuation has no existing native execution")
        if active and "completed" in phases:
            action = next((row.get("action_id") for row in events if integer(row.get("action_id")) and row.get("action_id") < 12), None)
            if integer(action) and action < 12:
                coverage[action] += 1
    return {"status": "complete" if not issues else "incomplete", "issues": issues,
            "command_count": len(commands), "events": len(rows), "completed_action_coverage": dict(coverage)}


def paired_ipc(rows: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pending, pairs = {}, []
    for row in rows:
        value = row.get("value")
        if not isinstance(value, dict) or not integer(value.get("id"), 1):
            raise ValueError("IPC row lacks request identity")
        if row.get("direction") == "request":
            if value["id"] in pending:
                raise ValueError("duplicate IPC request identity")
            pending[value["id"]] = value
        elif row.get("direction") == "response":
            request = pending.pop(value["id"], None)
            if request is None:
                raise ValueError("response without matching request")
            pairs.append((request, value))
        else:
            raise ValueError("invalid IPC direction")
    if pending:
        raise ValueError("IPC contains unanswered request")
    return pairs


def validate_case(case: dict[str, Any], ipc: list[dict[str, Any]], events: list[dict[str, Any]],
                  config: dict[str, Any], mechanics: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    name, issues = case.get("name"), []
    pairs = paired_ipc(ipc)
    issues += validate_scene(mechanics or [])
    if any(response.get("ok") is False for _, response in pairs):
        issues.append("runner rejected an infrastructure/fixture command")
    actions = [(request, response) for request, response in pairs if request.get("cmd") == "body_action"]
    name_actions = config["actions"]
    lifecycle = validate_lifecycle(events)
    issues += lifecycle["issues"]
    if name in name_actions and not lifecycle["completed_action_coverage"].get(name_actions.index(name)):
        issues.append(f"{name}: no successful native lifecycle")
    attack_mechanics = None
    if name in ("attack_front", "attack_left", "attack_right"):
        start = next((row for row in events if row.get("phase") == "started" and row.get("action_id") == name_actions.index(name)), None)
        end = next((row for row in events if start is not None and row.get("phase") == "completed" and event_identity(row) == event_identity(start)), None)
        applied, damage = [], []
        if start is not None and end is not None and mechanics:
            source_ids = {row["actor_id"] for row in mechanics if row.get("phase") == "observation_actor_mapping" and row.get("observation_entity_id") == start.get("entity_id")}
            target_ids = {row["actor_id"] for row in mechanics if row.get("phase") == "observation_actor_mapping" and row.get("observation_entity_id") == start.get("target_entity_id")}
            active = [row for row in mechanics if integer(row.get("t_game")) and start["t_game_ms"] <= row["t_game"] <= end["t_game_ms"]]
            applied = [row for row in active if row.get("phase") == "hit_attempt" and row.get("result") == "applied" and row.get("actor_id") in source_ids and row.get("target_id") in target_ids]
            damage = [row for row in active if row.get("phase") == "damage" and row.get("damage_kind") == "melee" and row.get("actor_id") in target_ids and row.get("target_id") in source_ids and integer(row.get("hp_before")) and integer(row.get("hp_after")) and row["hp_before"] > row["hp_after"]]
        if not applied or not damage or not any(hit.get("t_game") == loss.get("t_game") for hit in applied for loss in damage):
            issues.append(f"{name}: no linked actual native hit and target HP loss during execution")
        attack_mechanics = {"applied_hits": len(applied), "target_hp_loss": sum(row["hp_before"]-row["hp_after"] for row in damage)}
    if name == "mask_purity":
        pure = [response for request, response in pairs if request.get("cmd") == "body_self_test"]
        if len(pure) != 1:
            issues.append("one actual native value-gate self-test required")
        else:
            issues += validate_pure_gate(pure[0])
        snapshots = [response.get("actors") for request, response in pairs if request.get("cmd") == "body_control_status"][-3:]
        if len(snapshots) < 3 or any(snapshot != snapshots[0] for snapshot in snapshots):
            issues.append("mask queries mutated native resolver state")
        if not snapshots or not isinstance(snapshots[0], list) or not snapshots[0] or not all(actor.get("purity_state") for actor in snapshots[0]):
            issues.append("missing native queue/RNG/gate/pose purity evidence")
    current_masks = {}
    for request, response in pairs:
        for actor in response.get("actors", []):
            if isinstance(actor, dict):
                current_masks[actor.get("entity_id")] = actor
        if request.get("cmd") != "body_action":
            continue
        mask = response.get("validation_mask")
        action = request.get("action_id")
        if mask is None and response.get("accepted") is True and response.get("duplicate") is not True:
            actor = current_masks.get(request.get("entity_id"))
            if actor is None or any(actor.get(key) != request.get(key) for key in (*EPOCH_FIELDS, "observation_seq", "goal_version")):
                issues.append("accepted action has no matching native resolver snapshot")
            else:
                mask = actor.get("action_mask")
        if mask is None and response.get("accepted") is True and response.get("duplicate") is not True:
            issues.append("accepted action lacks a recorded resolver mask")
        if mask is not None:
            if not isinstance(mask, list) or len(mask) != 12 or any(type(bit) is not bool for bit in mask):
                issues.append("action response has invalid resolver mask")
            elif integer(action) and action < 12 and response.get("accepted") is True and not mask[action]:
                issues.append("accepted action contradicts its recorded mask")
    refusal = set(config["refusal_reasons"])
    if name in refusal:
        expected = case.get("expected_reason")
        expected_menu = [expected] if isinstance(expected, str) else case.get("expected_reasons")
        configured_menu = config["refusal_reasons"].get(name, [])
        if not isinstance(expected_menu, list) or not expected_menu or any(reason not in configured_menu for reason in expected_menu):
            issues.append(f"{name}: refusal reason was not declared by the contract")
            expected_menu = []
        refused = [row for row in events if row.get("phase") == "rejected" and row.get("reason") in expected_menu]
        matching_responses = [(request, response) for request, response in pairs if request.get("cmd") == ("body_disconnect" if name == "stale_disconnect" else "body_action") and response.get("reason") in expected_menu and (response.get("accepted") is False or name == "duplicate" and response.get("duplicate") is True)]
        if not matching_responses:
            issues.append(f"{name}: expected native refusal missing")
        for row in refused:
            identity = event_identity(row)
            # A duplicate refers to an already running original. Check the
            # original started count, rather than forbidding its first start.
            started = sum(event_identity(other) == identity and other.get("phase") in ("started", "continued") for other in events)
            if started > (1 if name == "duplicate" else 0):
                issues.append(f"{name}: invalid request started execution")
    if name in ("death", "target_gone"):
        mutation = next((request for request, _ in pairs if request.get("cmd") == "body_fixture" and request.get("kind") == ("hp" if name == "death" else "target_gone")), None)
        entity = mutation.get("entity_id") if mutation else None
        if mutation is None or name == "death" and mutation.get("hp") != 0:
            issues.append(f"{name}: actual native mutation request missing")
        status_rows = [actor for request, response in pairs if request.get("cmd") == "body_control_status" for actor in response.get("actors", []) if actor.get("entity_id") == entity]
        if not status_rows or status_rows[-1].get("owner") != "native" or status_rows[-1].get("control_generation") != 0:
            issues.append(f"{name}: invalidated body did not return to one native owner")
        if any(row.get("phase") in ("started", "continued") and row.get("entity_id") == entity for row in events):
            issues.append(f"{name}: external action started on an invalidated actor")
        if name == "death":
            numeric = [row for _, response in pairs for row in response.get("observations", []) if isinstance(row, dict) and row.get("entity_id") == entity]
            if not any(row.get("self", {}).get("hp") == 0 and row.get("self", {}).get("down") is True and row.get("control_allowed") is False for row in numeric):
                issues.append("death: actual HP zero/down state not observed after mutation")
        elif not any(actor.get("target_entity_id") == 0 and actor.get("owner") == "native" for actor in status_rows):
            issues.append("target_gone: native target was not actually cleared at the boundary")
    if name == "collision_mask":
        geometry = [response.get("fixture_geometry") for request, response in pairs if request.get("cmd") == "body_fixture" and request.get("kind") == "occluded"]
        if len(geometry) != 1 or not isinstance(geometry[0], dict) or any(geometry[0].get(key) is not True for key in ("center_ray_hit", "head_ray_hit", "melee_line_hit")) or geometry[0].get("target_visible") is not False or not integer(geometry[0].get("search_attempts"), 1):
            issues.append("collision mask has no actual native wall/ray fixture evidence")
    if name == "range_mask":
        observations = [row for _, response in pairs for row in response.get("observations", []) if isinstance(row, dict)]
        if not any(row.get("target", {}).get("in_w_range") is False and row.get("control_allowed") is True for row in observations):
            issues.append("range mask has no native out-of-weapon-range owned snapshot")
    if name in ("accepted_expired", "goal_changed_pending", "reset"):
        seq = case.get("cancel_command_id")
        target = [row for row in events if row.get("command_id") == seq]
        phases = [row.get("phase") for row in target]
        if not integer(seq, 1) or "accepted" not in phases or "cancelled" not in phases or "started" in phases or "continued" in phases:
            issues.append(f"{name}: accepted stale pending command was not cancelled before start")
    if name == "ownership":
        owners = [row for row in events if row.get("phase") == "ownership"]
        restored = next((row for row in owners if row.get("reason") == "native_restored" and row.get("release_reason") == "native_script_takeover"), None)
        if restored is None:
            issues.append("native story takeover missing")
        started = next((row for row in events if row.get("phase") == "started" and row.get("action_id") == 6), None)
        pending = next((row for row in owners if row.get("reason") == "release_pending" and row.get("release_reason") == "native_script_takeover"), None)
        if started is None or pending is None or not integer(pending.get("remaining_lock_game_ms"), 1):
            issues.append("ownership conflicts did not occur during a real protected attack")
        elif started.get("pose_after") != pending.get("pose_after") or pending.get("execution_id") not in (0, started.get("execution_id")):
            issues.append("native takeover mutated the protected segment before its boundary")
        if restored is not None and (restored.get("owner") != "native" or restored.get("remaining_lock_game_ms") != 0):
            issues.append("native story takeover returned before the safe boundary")
        resumed = False
        if restored is not None and mechanics:
            actor_ids = {row["actor_id"] for row in mechanics if row.get("phase") == "observation_actor_mapping" and row.get("observation_entity_id") == restored.get("entity_id")}
            resumed = any(row.get("phase") == "native_choice" and row.get("actor_id") in actor_ids and row.get("t_game", -1) >= restored.get("t_game_ms", 0) for row in mechanics)
        if not resumed:
            issues.append("native story takeover produced no resumed native combat choice")
        for kind, reason in (("soft_action", "legacy_npc_act_rejected"), ("attach_control", "legacy_attach_rejected"), ("dialog_control", "dialogue_owned")):
            if not any(request.get("cmd") == "body_fixture" and request.get("kind") == kind for request, response in pairs) or not any(row.get("reason") == reason for row in events):
                issues.append(f"ownership: no real legacy guard evidence for {kind}")
        if not any(request.get("cmd") == "body_acquire" and response.get("reason") == "already_owned" and response.get("accepted") is False for request, response in pairs):
            issues.append("ownership: second provider was not refused")
    if name == "pause_resume":
        pause = [response for request, response in pairs if request.get("cmd") == "pause"]
        resume = [response for request, response in pairs if request.get("cmd") == "resume"]
        if len(pause) != 1 or len(resume) != 1 or pause[0].get("game_ms") != resume[0].get("game_ms"):
            issues.append("pause/resume advanced game time or missing commands")
        if not any(row.get("phase") == "ownership" and row.get("release_reason") == "pause" for row in events):
            issues.append("pause failed to invalidate body lease")
        if not any(row.get("phase") == "ownership" and row.get("reason") == "native_restored" and row.get("release_reason") == "pause" for row in events):
            issues.append("resume did not return the invalidated lease to native at its boundary")
    if name in ("realtime_timeout", "realtime_disconnect"):
        start = next((row for row in events if row.get("phase") == "ownership" and row.get("reason") == "release_pending" and row.get("release_reason") in ("watchdog_timeout", "disconnect")), None)
        returned = next((row for row in events if row.get("phase") == "ownership" and row.get("reason") == "native_restored" and row.get("release_reason") in ("watchdog_timeout", "disconnect")), None)
        if start is None or returned is None:
            issues.append("no measured provider-loss/native-return events")
        else:
            budget = start.get("remaining_lock_game_ms")
            measured = returned.get("t_wall_ms", -1)-start.get("t_wall_ms", 0)
            if not integer(budget) or measured < 0 or measured > budget+config["budgets"]["native_return_quantization_ms"]:
                issues.append("native return exceeded remaining primitive lock budget")
            last_heartbeat = start.get("last_heartbeat_wall_ms")
            watchdog = start.get("watchdog_timeout_ms")
            if name == "realtime_timeout" and (not integer(last_heartbeat) or watchdog != config["budgets"]["provider_watchdog_wall_ms"] or start.get("t_wall_ms", -1)-last_heartbeat > watchdog+config["budgets"]["native_return_quantization_ms"]):
                issues.append("wall watchdog did not detect provider timeout within budget")
        resumed = False
        if returned is not None and mechanics:
            mapped = {row["actor_id"] for row in mechanics if row.get("phase") == "observation_actor_mapping" and row.get("observation_entity_id") == returned.get("entity_id")}
            resumed = any(row.get("phase") == "native_choice" and row.get("actor_id") in mapped and row.get("t_game", -1) >= returned.get("t_game_ms", 0) for row in mechanics)
        if not resumed:
            issues.append("owner release did not demonstrate a resumed native choice")
    if name == "lockstep_timeout":
        statuses = [response for _, response in pairs if response.get("infrastructure_failure") is True]
        if not statuses or not any(row.get("phase") == "ownership" and row.get("reason") == "infrastructure_failure" for row in events):
            issues.append("lockstep provider loss resumed as an ordinary episode")
        if any(row.get("phase") == "native_resume" for row in events):
            issues.append("lockstep provider loss silently resumed native AI")
    if name == "action_chain":
        recipe = config["action_chain"]
        accepted = [row for row in events if row.get("phase") == "accepted" and row.get("action_id") != 2]
        if [row.get("action_id") for row in accepted] != [name_actions.index(action) for action in recipe] or len({tuple(row.get(key) for key in (*EPOCH_FIELDS, "entity_id")) for row in accepted}) != 1:
            issues.append("action chain did not execute the declared order under one lease")
        reset_indices = [index for index, (request, _) in enumerate(pairs) if request.get("cmd") == "reset"]
        release_indices = [index for index, (request, _) in enumerate(pairs) if request.get("cmd") == "body_release"]
        chain_action_indices = [index for index, (request, _) in enumerate(pairs) if request.get("cmd") == "body_action"]
        if len(reset_indices) != 1 or len(release_indices) != 1 or not chain_action_indices or release_indices[0] < max(chain_action_indices):
            issues.append("action chain reset/released between choices")
        mutation_indices = [index for index, (request, _) in enumerate(pairs) if request.get("cmd") == "body_fixture"]
        if any(index > min(chain_action_indices, default=-1) for index in mutation_indices):
            issues.append("action chain used a fixture to recover the live body")
        rows = [row for _, response in pairs for row in response.get("observations", []) if isinstance(row, dict)]
        for command in accepted[1:]:
            snapshot = next((row for row in rows if row.get("entity_id") == command.get("entity_id") and row.get("observation_seq") == command.get("observation_seq")), None)
            if snapshot is None or snapshot.get("can_decide") is not True or snapshot.get("self", {}).get("body_state_base") != 0:
                issues.append("same-lease next choice has no real idle/ready native snapshot")
        for action in recipe:
            check = validate_case({"name": action}, ipc, events, config, mechanics)
            issues += [f"chain {action}: {issue}" for issue in check["issues"]]
    return {"name": name, "status": "complete" if not issues else "incomplete", "issues": issues,
            "lifecycle": lifecycle, "request_count": len(pairs), "attack_mechanics": attack_mechanics}


def native_parity(reference: Path, candidate: Path) -> dict[str, Any]:
    # Import only when needed: report-unit tests need no engine dependencies.
    sys.path.insert(0, str(Path(__file__).parent))
    import body_headless_parity as parity
    import body_baseline_report as baseline
    ref, ref_issues = baseline.read_events(reference / "events.jsonl")
    cand, cand_issues = baseline.read_events(candidate / "events.jsonl")
    result = parity.compare(ref, cand, read_json(reference / "manifest.json"), read_json(candidate / "manifest.json"),
                            read_json(baseline.DEFAULT_CONFIG), read_json(parity.DEFAULT_PARITY_CONFIG),
                            "full", ref_issues, cand_issues)
    # Reuse the strict mechanics comparator, while naming the evidence that this
    # stage actually collected: two headless traces across the body refactor.
    result["comparison_kind"] = "archived_pre_refactor_headless_parity"
    result["scope"] = ("Archived pre-refactor headless native-off traces (stage 3) versus current headless "
                       "native-off traces, using the full ordered mechanics, root-motion and collision comparator.")
    return result


def summarize(root: Path, config: dict[str, Any], native_reference: Path | None = None) -> dict[str, Any]:
    issues, cases, hashes = [], [], {}
    try:
        manifest, suite = read_json(root / "manifest.json"), read_json(root / "suite.json")
        if manifest.get("suite_scope") != "full":
            issues.append("full stage 4 run required; smoke cannot pass readiness")
        for key, expected in (("headless", True), ("graphics_device_created", False), ("window_created", False), ("audio_device_created", False), ("model", None)):
            if key not in manifest or manifest[key] != expected:
                issues.append(f"invalid provenance: {key}")
        for case in suite.get("cases", []):
            try:
                ipc_path = evidence_path(root, case.get("ipc_file"))
                event_path = evidence_path(root, case.get("events_file"))
                mechanics_path = evidence_path(root, case.get("mechanics_file")) if case.get("mechanics_file") else None
                result = validate_case(case, read_jsonl(ipc_path), read_jsonl(event_path), config,
                                       read_jsonl(mechanics_path) if mechanics_path else None)
                hashes[case["ipc_file"]] = digest(ipc_path)
                hashes[case["events_file"]] = digest(event_path)
                if mechanics_path:
                    hashes[case["mechanics_file"]] = digest(mechanics_path)
                cases.append(result)
                issues += [f"{case.get('name')}: {item}" for item in result["issues"]]
            except (OSError, ValueError, KeyError, TypeError) as error:
                issues.append(f"{case.get('name')}: {error}")
        names = [case["name"] for case in cases]
        for name in config["actions"]+config["required_cases"]:
            if names.count(name) != 1:
                issues.append(f"{name}: exactly one raw case required")
        if native_reference is None:
            issues.append("archived pre-refactor native reference required")
            parity_report = None
        else:
            candidate = root / "native-off"
            parity_report = native_parity(native_reference, candidate)
            if parity_report.get("status") != "matched" or sorted(episode.get("seed") for episode in parity_report.get("episodes", [])) != config["native_seeds"]:
                issues.append("ordered archived native-off traces did not match all declared seeds")
            for path in (native_reference / "events.jsonl", candidate / "events.jsonl"):
                hashes[str(path)] = digest(path)
        before, after = manifest.get("services_before"), manifest.get("services_after")
        if service_identity(before) != service_identity(after):
            issues.append("production service snapshots missing or changed")
        resources = suite.get("resources")
        if not isinstance(resources, list) or not resources:
            issues.append("missing actual /proc resource evidence")
        else:
            for sample in resources:
                if sample.get("pid") != suite.get("runner_pid") or not integer(sample.get("rss_kib"), 1):
                    issues.append("resource sample does not attest the actual runner process")
                for key in ("forbidden_device_descriptors", "display_socket_descriptors"):
                    if sample.get(key) != []:
                        issues.append(f"resource descriptor evidence invalid: {key}")
    except (OSError, ValueError, KeyError, TypeError) as error:
        issues.append(str(error))
        parity_report = None
    return {"schema_version": 1, "scenario": config["scenario"], "status": "complete" if not issues else "incomplete",
            "full_stage4_evidence": not issues, "issues": issues, "cases": cases,
            "native_parity": parity_report, "raw_sha256": hashes, "limitations": config["limitations"]}


def markdown(report: dict[str, Any]) -> str:
    lines = ["# Body API validation", "", f"Status: **{report['status']}**.", "", "| Case | Status | Native events | Commands |", "|---|---|---:|---:|"]
    for case in report["cases"]:
        lines.append(f"| {case['name']} | {case['status']} | {case['lifecycle']['events']} | {case['lifecycle']['command_count']} |")
    lines += ["", *[f"- {issue}" for issue in report["issues"]], "", *[f"- {item}" for item in report["limitations"]], ""]
    return "\n".join(lines)+"\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--native-reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.run.resolve(), read_json(args.config), args.native_reference.resolve())
    report["config_sha256"] = digest(args.config)
    report["reporter_sha256"] = digest(Path(__file__).resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "body-api.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    (args.output_dir / "body-api.md").write_text(markdown(report))
    print(f"body API validation {report['status']}: {args.output_dir / 'body-api.md'}")
    return 0 if report["full_stage4_evidence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
