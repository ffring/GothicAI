#!/usr/bin/env python3
"""Validate stage 5 from the CPU engine's journals without launching a game."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from body_api_report import (digest, evidence_path, integer, native_parity, read_json,
                             service_identity, strict_json, validate_lifecycle)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "evals/npc-body/body-arena-v1.json"
ACTOR_FIELDS = ("role", "symbol", "physical_side", "hp", "hp_max", "str", "dex", "onehand",
                "onehand_talent", "edge_protection", "blunt_protection", "weapon_symbol", "weapon",
                "armor_symbol", "armor", "weapon_damage", "weapon_length", "weapon_state", "immortal", "pos", "yaw_deg",
                "external_control", "current_state", "ai_queue_size", "ai_overlay_queue_size", "fight_queue",
                "primitive", "waits_ms", "down", "dead", "unconscious", "owner", "control_generation",
                "goal_version", "command_id", "execution_id", "release_pending", "control_allowed",
                "can_decide", "action_mask", "blocked_hits", "received_damage", "outgoing_damage",
                "body_state", "pose", "interruptible", "segment_remaining_ms", "native_eligible",
                "attack_anim", "prehit", "defence", "jump_back")
IDENTITY = ("session_epoch", "world_epoch", "control_generation", "entity_id", "command_id")


def number(value: Any, minimum: float = 0) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= minimum


def read_rows(path: Path, *, empty: bool = False) -> list[dict[str, Any]]:
    rows = []
    for index, line in enumerate(path.read_text().splitlines(), 1):
        if not line:
            raise ValueError(f"{path.name}:{index}: empty record")
        value = strict_json(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{index}: object required")
        rows.append(value)
    if not rows and not empty:
        raise ValueError(f"{path.name}: no records")
    return rows


def timed_pairs(rows: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any], float, float]]:
    pending, pairs, previous = {}, [], None
    for row in rows:
        stamp, value = row.get("wall_monotonic"), row.get("value")
        if not number(stamp) or previous is not None and stamp < previous:
            raise ValueError("IPC wall clock absent or moves backwards")
        previous = stamp
        if not isinstance(value, dict) or not integer(value.get("id"), 1):
            raise ValueError("IPC lacks a request ID")
        identity = value["id"]
        if row.get("direction") == "request":
            if identity in pending:
                raise ValueError("duplicate pending IPC request")
            pending[identity] = (value, stamp)
        elif row.get("direction") == "response":
            request, started = pending.pop(identity, (None, None))
            if request is None:
                raise ValueError("IPC response without request")
            pairs.append((request, value, started, stamp))
        else:
            raise ValueError("invalid IPC direction")
    if pending:
        raise ValueError("unanswered IPC request")
    return pairs


def actors(arena: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = arena.get("actors")
    if not isinstance(rows, list) or len(rows) != 2 or any(not isinstance(row, dict) for row in rows):
        raise ValueError("native arena must contain two actors")
    result = {row.get("role"): row for row in rows}
    if set(result) != {"primary", "opponent"}:
        raise ValueError("native arena actor roles missing or duplicated")
    if len({row.get("entity_id") for row in rows}) != 2 or any(not integer(row.get("entity_id"), 1) for row in rows):
        raise ValueError("native arena actor IDs missing or duplicated")
    return result


def semantic_initial(arena: dict[str, Any]) -> dict[str, Any]:
    """Compare complete reset semantics while runtime entity/epoch IDs change."""
    rows = actors(arena)
    ids = {row["entity_id"]: role for role, row in rows.items()}
    result = {}
    for role, row in rows.items():
        if any(field not in row for field in ACTOR_FIELDS):
            raise ValueError("native reset lacks a required actor field")
        target = ids.get(row.get("target_entity_id"))
        if target is None or target == role:
            raise ValueError("native reset target is not the other actor")
        result[role] = {field: row[field] for field in ACTOR_FIELDS}
        result[role]["target_role"] = target
    return result


def clean_reset(arena: dict[str, Any], profile: dict[str, Any] | None = None) -> list[str]:
    issues = []
    semantic_initial(arena)
    if arena.get("ready") is not True or arena.get("started") is not False or arena.get("outcome") != "running":
        issues.append("reset has no ready, unstarted native arena")
    for key in ("episode_id", "runner_generation", "session_epoch", "world_epoch"):
        if not integer(arena.get(key), 1):
            issues.append(f"reset has no valid {key}")
    if any(arena.get(role+"_entity_id") != row["entity_id"] for role, row in actors(arena).items()):
        issues.append("reset role IDs differ from the actor table")
    for role, row in actors(arena).items():
        for key, expected in (profile or {}).items():
            if key in ACTOR_FIELDS and row.get(key) != expected:
                issues.append(f"{role}: reset {key} differs from preregistered profile")
        for key in ("hp", "hp_max", "weapon_symbol", "weapon_damage", "weapon_length"):
            if not integer(row.get(key), 1):
                issues.append(f"{role}: invalid reset {key}")
        if row["hp"] != row["hp_max"] or row.get("immortal") is not False:
            issues.append(f"{role}: reset must be mortal at full health")
        if row.get("weapon_state") != 2:
            issues.append(f"{role}: reset requires a drawn one-handed melee weapon")
        if row.get("interruptible") is not True or row.get("native_eligible") is not True or row.get("segment_remaining_ms") != 0:
            issues.append(f"{role}: reset retains an ineligible or locked animation segment")
        if any(row.get(key) is not False for key in ("attack_anim", "prehit", "defence", "jump_back")):
            issues.append(f"{role}: reset retains an active attack or defence pose")
        if not integer(row.get("body_state")) or not isinstance(row.get("pose"), str):
            issues.append(f"{role}: reset native body state/pose is missing")
        if row.get("owner") != "native" or row.get("external_control") is not False:
            issues.append(f"{role}: reset retains an external owner")
        for key in ("control_generation", "goal_version", "command_id", "execution_id", "ai_queue_size", "ai_overlay_queue_size"):
            if row.get(key) != 0:
                issues.append(f"{role}: reset retains {key}")
        if row.get("release_pending") is not False or any(row.get(key) is not False for key in ("down", "dead", "unconscious")):
            issues.append(f"{role}: reset retains release/terminal state")
        queue, waits = row.get("fight_queue"), row.get("waits_ms")
        if not isinstance(queue, list) or not queue or any(value != 0 for value in queue):
            issues.append(f"{role}: reset fight queue is not empty")
        if not isinstance(waits, dict) or set(waits) != {"fai", "ani", "ai", "output"} or any(value != 0 for value in waits.values()):
            issues.append(f"{role}: reset retains a timing lock")
        if not isinstance(row.get("pos"), list) or len(row["pos"]) != 3 or any(not number(value, -1e9) for value in row["pos"]) or not number(row.get("yaw_deg"), -1e9):
            issues.append(f"{role}: reset position/yaw invalid")
        if any(row.get(key) != 0 for key in ("blocked_hits", "received_damage", "outgoing_damage")):
            issues.append(f"{role}: reset retains combat counters")
    return issues


def distance(arena: dict[str, Any]) -> float:
    rows = actors(arena)
    left, right = rows["primary"]["pos"], rows["opponent"]["pos"]
    return math.hypot(left[0]-right[0], left[2]-right[2])


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    location = (len(ordered)-1)*p
    low, high = math.floor(location), math.ceil(location)
    return ordered[low]+(ordered[high]-ordered[low])*(location-low)


def native_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in IDENTITY)


def command_evidence(events: list[dict[str, Any]], observations: list[dict[str, Any]],
                     pairs: list[tuple[dict[str, Any], dict[str, Any], float, float]],
                     terminal: dict[str, Any]) -> dict[str, Any]:
    issues = validate_lifecycle(events)["issues"] if events else []
    groups = defaultdict(list)
    for row in events:
        if integer(row.get("command_id"), 1):
            groups[native_identity(row)].append(row)
    sampled = {(row.get("session_epoch"), row.get("world_epoch"), row.get("control_generation"),
                row.get("entity_id"), row.get("observation_seq")): row for row in observations}
    requests = {native_identity(request): (request, response) for request, response, _, _ in pairs
                if request.get("cmd") == "body_action"}
    counts = Counter()
    for identity, rows in groups.items():
        accepted = [row for row in rows if row.get("phase") == "accepted"]
        if not accepted:
            continue
        command = accepted[0]
        if command.get("action_id") == 0:
            counts["continue_commands"] += 1
            continue
        counts["accepted_decisions"] += 1
        request, response = requests.get(identity, (None, None))
        if request is None or response.get("accepted") is not True:
            issues.append(f"accepted decision {identity} has no matching accepted raw request")
            continue
        if any(command.get(key) != request.get(key) for key in ("action_id", "goal_version", "observation_seq", "target_entity_id")):
            issues.append(f"decision {identity} contradicts its accepted native receipt")
        sample_key = (*identity[:4], request.get("observation_seq"))
        observation = sampled.get(sample_key)
        action = request.get("action_id")
        mask = observation.get("action_mask") if observation else None
        if (not integer(action) or action >= 12 or not isinstance(mask, list) or len(mask) != 12 or
                any(type(value) is not bool for value in mask) or mask[action] is not True or
                observation.get("can_decide") is not True or observation.get("action_mask_valid") is not True or
                observation.get("t_game_ms") != request.get("issued_game_ms")):
            issues.append(f"decision {identity} has no fresh, legal native observation")
        starts = [row for row in rows if row.get("phase") == "started"]
        ends = [row for row in rows if row.get("phase") in ("completed", "cancelled")]
        if starts:
            counts["started_decisions"] += 1
            if ends:
                counts[ends[-1]["phase"]+"_decisions"] += 1
                counts["policy_transitions"] += 1
            elif terminal.get("terminated") is True:
                # A real death/task terminal may interrupt a protected physical
                # segment. The final engine sample remains this transition's end.
                counts["terminal_decisions"] += 1
                counts["policy_transitions"] += 1
            elif terminal.get("truncated") is True and terminal.get("outcome") in ("time_limit", "control_lost"):
                counts["control_lost_decisions" if terminal.get("outcome") == "control_lost" else "truncated_decisions"] += 1
                counts["policy_transitions"] += 1
            else:
                counts["pending_decisions"] += 1
        else:
            counts["not_started_decisions"] += 1
    return {"issues": issues, **{key: counts[key] for key in ("accepted_decisions", "started_decisions",
            "completed_decisions", "cancelled_decisions", "terminal_decisions", "truncated_decisions", "control_lost_decisions", "pending_decisions",
            "not_started_decisions", "continue_commands", "policy_transitions")}}


def linked_mechanics(initial: dict[str, Any], final: dict[str, Any], events: list[dict[str, Any]],
                     mechanics: list[dict[str, Any]]) -> dict[str, Any]:
    native_ids = defaultdict(set)
    for row in mechanics:
        if row.get("phase") in ("observation_actor_mapping", "arena_actor_mapping"):
            native_ids[row.get("observation_entity_id")].add(row.get("actor_id"))
    profiles = actors(initial)
    primary = native_ids[profiles["primary"]["entity_id"]]
    opponent = native_ids[profiles["opponent"]["entity_id"]]
    start, end = final.get("start_game_ms", 0), final.get("game_ms", 0)
    active = [row for row in mechanics if number(row.get("t_game")) and start <= row["t_game"] <= end]
    hits = [row for row in active if row.get("phase") == "hit_attempt" and row.get("result") == "applied"]
    damages = [row for row in active if row.get("phase") == "damage" and row.get("damage_kind") == "melee"
               and integer(row.get("hp_before")) and integer(row.get("hp_after")) and row["hp_before"] > row["hp_after"]]
    outgoing = [row for row in damages if row.get("actor_id") in opponent and row.get("target_id") in primary]
    linked = [row for row in outgoing if any(hit.get("actor_id") in primary and hit.get("target_id") in opponent and
                                             hit.get("t_game") == row["t_game"] for hit in hits)]
    blocks = [row for row in active if row.get("phase") == "defence_result" and row.get("blocked") is True and
              row.get("damage_kind") == "melee" and row.get("actor_id") in primary and row.get("target_id") in opponent and
              any(hit.get("actor_id") in opponent and hit.get("target_id") in primary and hit.get("t_game") == row["t_game"] for hit in hits)]
    counters, counter_issues = {}, []
    for role, entity_ids, attacker_ids in (("primary", primary, opponent), ("opponent", opponent, primary)):
        incoming = sum(row["hp_before"]-row["hp_after"] for row in damages
                       if row.get("actor_id") in entity_ids and row.get("target_id") in attacker_ids)
        blocked = sum(row.get("phase") == "defence_result" and row.get("blocked") is True and
                      row.get("actor_id") in entity_ids and row.get("target_id") in attacker_ids for row in active)
        outgoing_loss = sum(row["hp_before"]-row["hp_after"] for row in damages
                            if row.get("actor_id") in attacker_ids and row.get("target_id") in entity_ids)
        counters[role] = {"received_damage": incoming, "outgoing_damage": outgoing_loss, "blocked_hits": blocked}
        for key, measured in counters[role].items():
            if actors(final)[role].get(key) != measured:
                counter_issues.append(f"{role}: final {key} does not reconcile with raw native combat")
        if actors(initial)[role]["hp"]-actors(final)[role]["hp"] != incoming:
            counter_issues.append(f"{role}: actual HP loss does not reconcile with raw native damage")
    return {"linked_attack_hits": len(linked), "linked_opponent_hp_loss": sum(row["hp_before"]-row["hp_after"] for row in linked),
            "linked_blocked_hits": len(blocks), "primary_mapping": sorted(primary), "opponent_mapping": sorted(opponent),
            "raw_counters": counters, "issues": counter_issues}


def mirror_issues(left: dict[str, Any], right: dict[str, Any], *, position_tolerance: float = .01,
                  yaw_tolerance: float = .001) -> list[str]:
    issues, first, second = [], actors(left), actors(right)
    for role, other in (("primary", "opponent"), ("opponent", "primary")):
        a, b = first[role], second[other]
        if a.get("physical_side") != b.get("physical_side"):
            issues.append(f"{role}: mirrored placement did not exchange physical side")
        if any(abs(x-y) > position_tolerance for x, y in zip(a["pos"], b["pos"])):
            issues.append(f"{role}: mirrored positions did not exchange actor roles")
        angle = abs((a["yaw_deg"]-b["yaw_deg"]+180) % 360-180)
        if angle > yaw_tolerance:
            issues.append(f"{role}: mirrored yaw does not match the exchanged position")
    for role in ("primary", "opponent"):
        if first[role].get("physical_side") == second[role].get("physical_side"):
            issues.append(f"{role}: side flag did not change its actual physical placement")
    return issues


def control_loss_issues(record: dict[str, Any], initial: dict[str, Any], final: dict[str, Any],
                        events: list[dict[str, Any]], observations: list[dict[str, Any]],
                        mechanics: list[dict[str, Any]]) -> list[str]:
    """An alive candidate losing its body guard is an explicit episode boundary.

    It neither awards a win nor turns watchdog/disconnect into game-time timeout.
    The native ownership journal must prove the actual LOS handoff boundary.
    """
    issues, final_actors = [], actors(final)
    primary = final_actors["primary"]
    if (record.get("scope") not in ("paired", "throughput") or record.get("provider") != "scripted" or
            initial.get("scenario") != "native_duel" or final.get("provider") != "scripted"):
        issues.append("control_lost is restricted to a scripted paired/throughput native duel")
    if (final.get("outcome") != "control_lost" or final.get("reason") != "target_visibility_lost" or
            final.get("truncated") is not True or final.get("terminated") is not False or
            final.get("infrastructure") is not False):
        issues.append("control_lost must be a distinct alive LOS truncation without a winner")
    if any(final.get(key) not in (None, 0, "") for key in ("winner", "winner_entity_id")):
        issues.append("control_lost cannot award a winner")
    if any(not integer(actor.get("hp"), 1) or any(actor.get(key) is not False for key in ("dead", "down", "unconscious"))
           for actor in final_actors.values()):
        issues.append("control_lost cannot replace an actual actor death/down")
    if primary.get("target_entity_id") != final_actors["opponent"]["entity_id"]:
        issues.append("control_lost cannot replace a changed/missing target binding")
    loss = [row for row in events if row.get("phase") == "ownership" and
            row.get("reason") in ("release_pending", "native_restored") and
            row.get("release_reason") == "target_visibility_lost" and row.get("entity_id") == primary["entity_id"] and
            row.get("session_epoch") == final.get("session_epoch") and row.get("world_epoch") == final.get("world_epoch") and
            row.get("t_game_ms") == final.get("game_ms") and integer(row.get("control_generation"), 1)]
    if not loss:
        return issues+["control_lost has no same-frame native primary LOS ownership event"]
    boundary = loss[0]
    acquired = [row for row in events if row.get("phase") == "ownership" and row.get("reason") == "acquired" and
                all(row.get(key) == boundary.get(key) for key in ("entity_id", "session_epoch", "world_epoch", "control_generation")) and
                row.get("event_seq", math.inf) < boundary.get("event_seq", 0)]
    if not acquired:
        issues.append("control_lost ownership event has no matching acquired generation")
    if boundary.get("reason") == "release_pending" and boundary.get("release_pending") is not True:
        issues.append("control_lost release_pending event lacks its actual pending flag")
    if loss[-1].get("reason") == "release_pending":
        if primary.get("release_pending") is not True or primary.get("owner") != "scripted" or primary.get("control_generation") != boundary.get("control_generation"):
            issues.append("control_lost release_pending does not match the frozen native owner")
    if loss[-1].get("reason") == "native_restored":
        if loss[-1].get("release_pending") is not False or primary.get("owner") != "native" or primary.get("control_generation") != 0:
            issues.append("control_lost native_restored does not match the frozen native owner")
    samples = [row for row in observations if row.get("entity_id") == primary["entity_id"] and
               row.get("session_epoch") == final.get("session_epoch") and row.get("world_epoch") == final.get("world_epoch") and
               row.get("t_game_ms") == final.get("game_ms")]
    if (not samples or samples[-1].get("target", {}).get("entity_id") != final_actors["opponent"]["entity_id"] or
            samples[-1].get("target", {}).get("visible") is not False or
            samples[-1].get("target", {}).get("current_valid") is not False):
        issues.append("control_lost has no actual final hidden-target numeric sample")
    if any(row.get("entity_id") == primary["entity_id"] and row.get("phase") in ("accepted", "started") and
           row.get("event_seq", 0) > boundary.get("event_seq", math.inf) for row in events):
        issues.append("control_lost allowed another policy command after its ownership boundary")
    ids = {row.get("actor_id") for row in mechanics if row.get("phase") in ("arena_actor_mapping", "observation_actor_mapping") and
           row.get("observation_entity_id") == primary["entity_id"]}
    if any(row.get("actor_id") in ids and number(row.get("t_game")) and
           ((row.get("phase") in ("native_choice", "native_consume") and row["t_game"] >= boundary["t_game_ms"]) or
            (row.get("phase") == "native_attempt" and (row["t_game"] > boundary["t_game_ms"] or
             row["t_game"] == boundary["t_game_ms"] and row.get("accepted") is True and integer(row.get("choice_id"), 1))))
           for row in mechanics):
        issues.append("control_lost continued primary native combat after its ownership boundary")
    return issues


def validate_episode(record: dict[str, Any], root: Path, config: dict[str, Any]) -> dict[str, Any]:
    issues, hashes = [], {}
    files = {}
    for key in ("ipc_file", "body_events_file", "mechanics_file", "arena_events_file", "observations_file"):
        path = evidence_path(root, record.get(key))
        files[key] = read_rows(path, empty=key == "body_events_file")
        hashes[record[key]] = digest(path)
    pairs = timed_pairs(files["ipc_file"])
    native = files["arena_events_file"]
    initial_rows = [row.get("arena") for row in native if row.get("kind") == "reset_ready"]
    terminal_rows = [row.get("arena") for row in native if row.get("kind") in ("terminal", "pre_reset")]
    if len(initial_rows) != 1 or not all(isinstance(row, dict) for row in initial_rows):
        raise ValueError("exactly one native reset_ready snapshot required")
    initial = initial_rows[0]
    issues += clean_reset(initial, config.get("arena_profile"))
    cleanup = record.get("scope") == "reset_cleanup"
    if cleanup and not terminal_rows:
        terminal_rows = [row.get("arena") for row in native if row.get("kind") == "snapshot"]
    if not terminal_rows or not all(isinstance(row, dict) for row in terminal_rows):
        raise ValueError("final native terminal/pre_reset snapshot missing")
    final = terminal_rows[-1]
    if any(final.get(key) != initial.get(key) for key in ("episode_id", "runner_generation", "session_epoch", "world_epoch", "scenario", "seed", "side")):
        issues.append("episode final mixes another reset identity")
    for key in ("scenario", "seed", "side"):
        if record.get(key) != initial.get(key):
            issues.append(f"episode driver {key} contradicts native reset")
    for key in ("distance_cm", "timeout_ms"):
        if record.get("setup", {}).get(key) != initial.get(key):
            issues.append(f"episode driver setup {key} contradicts native reset")
    expected_token = [initial.get(key) for key in ("session_epoch", "world_epoch", "runner_generation")]
    if record.get("recurrent_after") != expected_token or record.get("recurrent_before") == expected_token:
        issues.append("recurrent reset token does not advance with native reset")
    if record.get("initial") != initial:
        issues.append("copied initial state differs from the native reset journal")
    if not any(row.get("arena") == record.get("final") for row in native):
        issues.append("copied final state has no corresponding native snapshot")
    if initial.get("initial_state", {}).get("actors") != initial.get("actors") or final.get("initial_state") != initial.get("initial_state"):
        issues.append("sealed canonical initial state differs from native reset or changed during episode")
    reset_pairs = [(request, response, before, after) for request, response, before, after in pairs if request.get("cmd") == "reset"]
    if len(reset_pairs) != 1:
        issues.append("episode must have exactly one raw reset")
    else:
        if reset_pairs[0][1].get("arena") != initial:
            issues.append("raw reset reply differs from journal reset_ready")
    if any(response.get("ok") is False for _, response, _, _ in pairs):
        issues.append("episode has a native IPC error")
    if any(request.get("cmd") == "body_fixture" for request, _, _, _ in pairs):
        issues.append("episode uses a fixture mutation instead of arena execution")
    started = [(request, response, before, after) for request, response, before, after in pairs if request.get("cmd") == "arena_start"]
    if not cleanup and (len(started) != 1 or started[0][1].get("arena", {}).get("started") is not True):
        issues.append("episode has no real arena_start")
    if not cleanup and final.get("provider") != record.get("provider"):
        issues.append("episode provider differs from native ownership setup")
    for request, response, _, _ in pairs:
        if request.get("cmd") == "body_events" and response.get("dropped_total") != 0:
            issues.append("control event queue lost data")
    if cleanup:
        if final.get("started") is not False or actors(final) != actors(initial):
            issues.append("reset cleanup advanced an episode before the canonical state was recorded")
    elif final.get("infrastructure") is True:
        if record.get("scope") != "reset" or final.get("reason") != "reset_requested":
            issues.append("infrastructure outcome cannot pass an evaluation episode")
    elif final.get("terminated") is True:
        if final.get("truncated") is not False or final.get("outcome") not in ("task_success", "death_or_down"):
            issues.append("invalid terminal outcome")
    elif final.get("truncated") is True:
        if final.get("outcome") == "control_lost":
            issues += control_loss_issues(record, initial, final, files["body_events_file"], files["observations_file"], files["mechanics_file"])
        elif final.get("outcome") != "time_limit" or final.get("terminated") is not False or final.get("elapsed_game_ms", -1) < final.get("timeout_ms", math.inf):
            issues.append("time limit is not a genuine truncation")
    else:
        issues.append("evaluation lacks a terminal/truncation outcome")
    final_actors = actors(final)
    if final.get("outcome") == "death_or_down" and not any(row.get("down") is True or row.get("dead") is True or row.get("unconscious") is True or row.get("hp", 1) <= 0 for row in final_actors.values()):
        issues.append("death outcome has no actual down/dead actor")
    if initial.get("scenario") == "native_duel" and final.get("outcome") == "task_success":
        issues.append("duel timeout/task marker cannot be a win")
    observations = files["observations_file"]
    for role, actor in final_actors.items():
        rows = [row for row in observations if row.get("entity_id") == actor["entity_id"] and
                row.get("session_epoch") == final.get("session_epoch") and row.get("world_epoch") == final.get("world_epoch") and
                row.get("t_game_ms") == final.get("game_ms")]
        if not rows or rows[-1].get("self", {}).get("hp") != actor.get("hp"):
            issues.append(f"{role}: final native observation absent before reset")
    commands = command_evidence(files["body_events_file"], observations, pairs, final)
    issues += commands.pop("issues")
    combat = linked_mechanics(initial, final, files["body_events_file"], files["mechanics_file"])
    issues += combat.pop("issues")
    if record.get("scope") == "reset":
        mutation = record.get("mutation", {})
        primary = final_actors["primary"]
        recipe = (("attack_command_id", 6, "completed"),
                  ("movement_command_id", config.get("reset_movement_action", 5), "completed"),
                  ("active_wait_command_id", 11, "started"))
        for name, action, phase in recipe:
            command_id = mutation.get(name)
            if not integer(command_id, 1) or not any(row.get("entity_id") == primary["entity_id"] and
                    row.get("command_id") == command_id and row.get("action_id") == action and row.get("phase") == phase
                    for row in files["body_events_file"]):
                issues.append(f"reset mutation {name} has no real native action {action} {phase}")
        if primary.get("command_id") != mutation.get("active_wait_command_id"):
            issues.append("reset mutation final snapshot does not retain its actual wait command")
    if record.get("provider") == "scripted" and record.get("scope") != "reset":
        scenario = initial.get("scenario")
        if scenario == "distance":
            if final.get("outcome") != "task_success" or distance(initial) <= 220 or distance(final) > 220.001:
                issues.append("distance task has no actual approach into the declared range")
            if final.get("distance_hold_ms") != 200 or not number(final.get("distance_held_ms"), 200):
                issues.append("distance task did not hold the declared range for 200 game milliseconds")
            if not any(row.get("phase") == "started" and row.get("action_id") == 1 for row in files["body_events_file"]):
                issues.append("distance task has no real approach execution")
        elif scenario == "attack":
            if final.get("outcome") != "task_success" or not combat["linked_attack_hits"] or not combat["linked_opponent_hp_loss"] or final_actors["opponent"]["hp"] >= actors(initial)["opponent"]["hp"]:
                issues.append("attack task has no linked native hit and opponent HP loss")
        elif scenario == "block":
            if final.get("outcome") != "task_success" or not combat["linked_blocked_hits"] or final_actors["primary"]["hp"] != actors(initial)["primary"]["hp"]:
                issues.append("defence task has no actual blocked hit without HP loss")
            if not any(row.get("phase") == "started" and row.get("action_id") == 9 for row in files["body_events_file"]):
                issues.append("defence task has no real block execution")
    reset_s = reset_pairs[0][3]-reset_pairs[0][2] if len(reset_pairs) == 1 else 0
    total_s = pairs[-1][3]-pairs[0][2] if pairs else 0
    rollout_s = pairs[-1][3]-started[0][2] if len(started) == 1 else 0
    if not number(reset_s, 1e-9) or not number(total_s, 1e-9) or not cleanup and not number(rollout_s, 1e-9):
        issues.append("actual episode/reset IPC wall timing invalid")
    substeps = sum(response.get("advanced_substeps", 0) for request, response, _, _ in pairs if request.get("cmd") == "arena_step")
    return {"name": record.get("name"), "scope": record.get("scope"), "provider": record.get("provider"),
            "scenario": initial.get("scenario"), "seed": initial.get("seed"), "side": initial.get("side"),
            "status": "complete" if not issues else "incomplete", "issues": issues,
            "initial": initial, "final": final, "initial_semantics": semantic_initial(initial),
            "reset_wall_s": reset_s, "rollout_wall_s": rollout_s, "total_wall_s": total_s,
            "advanced_substeps": substeps, "runner_pid": record.get("runner_pid"),
            "usable_for_actor_update": final.get("outcome") != "control_lost" and not final.get("infrastructure"),
            "recurrent_before": record.get("recurrent_before"), "recurrent_after": record.get("recurrent_after"),
            "commands": commands, "mechanics": combat, "raw_sha256": hashes}


def throughput_report(episodes: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    decisions = sum(row["commands"]["policy_transitions"] for row in episodes)
    usable_decisions = sum(row["commands"]["policy_transitions"] for row in episodes
                           if row.get("usable_for_actor_update") is True)
    total = sum(row["total_wall_s"] for row in episodes)
    rollout = sum(row["rollout_wall_s"] for row in episodes)
    rate = decisions/total if total > 0 else 0
    usable_rate = usable_decisions/total if total > 0 else 0
    substeps = sum(row.get("advanced_substeps", 0) for row in episodes)
    gate = config.get("throughput_gate", {})
    small = gate.get("small_ppo_decisions", 32768)
    budget = gate.get("max_collection_wall_s", 21600)
    projection = {str(size): size/usable_rate if usable_rate else None for size in (small, 100000, 1000000)}
    diagnostic_projection = {str(size): size/rate if rate else None for size in (small, 100000, 1000000)}
    return {"measured_episodes": len(episodes), "policy_transitions": decisions, "total_wall_s": total,
            "actor_update_transitions": usable_decisions, "discarded_transitions": decisions-usable_decisions,
            "actor_update_episodes": sum(row.get("usable_for_actor_update") is True for row in episodes),
            "discarded_episodes": sum(row.get("usable_for_actor_update") is not True for row in episodes),
            "rollout_wall_s": rollout, "episodes_s": len(episodes)/total if total > 0 else 0,
            "policy_transitions_s": rate, "rollout_policy_transitions_s": decisions/rollout if rollout > 0 else 0,
            "actor_update_transitions_s": usable_rate,
            "advanced_substeps": substeps, "substeps_s": substeps/total if total > 0 else 0,
            "decision_counts": {key: sum(row["commands"].get(key, 0) for row in episodes) for key in (
                "accepted_decisions", "started_decisions", "completed_decisions", "cancelled_decisions",
                "terminal_decisions", "truncated_decisions", "control_lost_decisions", "pending_decisions", "not_started_decisions", "continue_commands")},
            "reset_p50_s": percentile([row["reset_wall_s"] for row in episodes], .5),
            "reset_p95_s": percentile([row["reset_wall_s"] for row in episodes], .95),
            "collection_projection_s": projection, "diagnostic_collection_projection_s": diagnostic_projection,
            "small_ppo_collection_gate": bool(usable_rate and small/usable_rate <= budget),
            "small_ppo_collection_feasible": bool(usable_rate and small/usable_rate <= budget),
            "gate": {"small_ppo_decisions": small, "max_collection_wall_s": budget},
            "scope": "One CPU runner, scripted provider, actual native execution transitions; reset and IPC included. Collection feasibility uses only actor-update-usable episodes and includes the wall time spent on discarded control_lost fragments. Optimizer/model cost excluded."}


def summarize(root: Path, config: dict[str, Any], native_reference: Path | None = None) -> dict[str, Any]:
    issues, episodes, parity, resources_summary = [], [], None, {}
    try:
        manifest, suite = read_json(root/"manifest.json"), read_json(root/"suite.json")
        for key, expected in (("headless", True), ("graphics_device_created", False), ("window_created", False), ("audio_device_created", False), ("model", None)):
            if key not in manifest or manifest[key] != expected:
                issues.append(f"invalid provenance: {key}")
        if manifest.get("suite_scope") != "full":
            issues.append("a full suite is required; smoke cannot establish stage 5 readiness")
        records, seen = [], set()
        for key in ("cases", "reset_cycles", "paired", "throughput"):
            rows = suite.get(key, [])
            if not isinstance(rows, list):
                raise ValueError(f"suite {key} is not a list")
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("episode record is not an object")
                identity = row.get("arena_events_file")
                if identity in seen:
                    continue
                seen.add(identity)
                records.append(row)
        if isinstance(suite.get("reset_cleanup"), dict):
            records.append(suite["reset_cleanup"])
        for record in records:
            try:
                episode = validate_episode(record, root, config)
                episodes.append(episode)
                issues += [f"{record.get('name')}: {item}" for item in episode["issues"]]
            except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
                issues.append(f"{record.get('name')}: {error}")
        ordered = sorted(episodes, key=lambda row: row["initial"]["runner_generation"])
        if any(row.get("runner_pid") != suite.get("runner_pid") for row in episodes):
            issues.append("arena episodes do not all attest the same runner PID")
        for left, right in zip(ordered, ordered[1:]):
            if (left["initial"]["session_epoch"] != right["initial"]["session_epoch"] or
                    right["initial"]["runner_generation"] <= left["initial"]["runner_generation"] or
                    right["initial"]["world_epoch"] <= left["initial"]["world_epoch"] or
                    right["initial"]["episode_id"] <= left["initial"]["episode_id"]):
                issues.append("reset identities do not advance within one runner process")
        groups = defaultdict(list)
        for row in episodes:
            identity = tuple(row["initial"].get(key) for key in ("scenario", "seed", "side", "distance_cm", "timeout_ms"))
            groups[identity].append(row)
        for identity, rows in groups.items():
            if any(row["initial_semantics"] != rows[0]["initial_semantics"] for row in rows[1:]):
                issues.append(f"initial state differs for identical setup/seed: {identity}")
        resets = [row for row in episodes if row["scope"] == "reset"]
        if len(resets) != config.get("reset_count", 100):
            issues.append("100 sequential dirty-state reset cycles are required")
        for row in resets:
            before, after = actors(row["initial"]), actors(row["final"])
            if not row["mechanics"]["linked_attack_hits"] or not row["mechanics"]["linked_opponent_hp_loss"]:
                issues.append(f"{row['name']}: reset never followed actual melee damage")
            if not any(actor.get("pos") != before[role].get("pos") for role, actor in after.items()):
                issues.append(f"{row['name']}: reset never followed actual motion")
            if not any(actor.get("command_id", 0) > 0 and actor.get("owner") == "scripted" and
                       actor.get("execution_id", 0) > 0 and actor.get("waits_ms", {}).get("fai", 0) > 0
                       for actor in after.values()):
                issues.append(f"{row['name']}: reset never followed an owned active execution lock")
            index = ordered.index(row)
            if index+1 >= len(ordered) or ordered[index+1]["initial_semantics"] != row["initial_semantics"]:
                issues.append(f"{row['name']}: subsequent reset did not restore the complete canonical state")
            if index+1 < len(ordered) and ordered[index+1]["recurrent_before"] != row["recurrent_after"]:
                issues.append(f"{row['name']}: next reset has no prior recurrent boundary")
        paired = [row for row in episodes if row["scope"] == "paired"]
        for seed in config.get("paired_seeds", [17, 29, 43]):
            for side in (0, 1):
                selected = [row for row in paired if row["seed"] == seed and row["side"] == side]
                if Counter(row["provider"] for row in selected) != {"native": 1, "scripted": 1}:
                    issues.append(f"paired seed {seed} side {side}: both providers required exactly once")
                elif selected[0]["initial_semantics"] != selected[1]["initial_semantics"]:
                    issues.append(f"paired seed {seed} side {side}: initial profiles differ")
            for provider in ("native", "scripted"):
                selected = [row for row in paired if row["seed"] == seed and row["provider"] == provider]
                sides = {row["side"]: row for row in selected}
                if set(sides) == {0, 1}:
                    issues += [f"paired seed {seed} {provider}: {item}" for item in mirror_issues(
                        sides[0]["initial"], sides[1]["initial"],
                        position_tolerance=config.get("budgets", {}).get("reset_position_tolerance_cm", .01),
                        yaw_tolerance=config.get("budgets", {}).get("reset_yaw_tolerance_deg", .001))]
        for scenario in ("distance", "attack", "block"):
            if not any(row["scenario"] == scenario and row["provider"] == "scripted" and row["status"] == "complete" for row in episodes):
                issues.append(f"validated scripted {scenario} task missing")
        for name in config.get("required_micro_cases", ["distance", "attack", "block", "native_terminal", "duel_timeout"]):
            selected = [row for row in episodes if row["scope"] == "micro" and row["name"] == name]
            if len(selected) != 1:
                issues.append(f"{name}: exactly one required micro episode is needed")
            elif name == "native_terminal" and selected[0]["final"].get("outcome") != "death_or_down":
                issues.append("native_terminal: actual death/down outcome required")
            elif name == "duel_timeout" and selected[0]["final"].get("outcome") != "time_limit":
                issues.append("duel_timeout: actual game-time truncation required")
        if native_reference is None:
            issues.append("archived pre-refactor native reference required")
        else:
            parity = native_parity(native_reference, root/"native-off")
            if parity.get("status") != "matched" or sorted(row.get("seed") for row in parity.get("episodes", [])) != [17, 29, 43]:
                issues.append("strict legacy native-off mechanics parity failed")
        if service_identity(manifest.get("services_before")) != service_identity(manifest.get("services_after", suite.get("services_after"))):
            issues.append("production ASR/TTS service identity changed")
        resources = suite.get("resources")
        if not isinstance(resources, list) or not resources:
            issues.append("actual runner resource evidence missing")
        else:
            for sample in resources:
                if sample.get("pid") != suite.get("runner_pid") or not integer(sample.get("rss_kib"), 1):
                    issues.append("resource evidence does not describe the single runner PID")
                if sample.get("forbidden_device_descriptors") != [] or sample.get("display_socket_descriptors") != []:
                    issues.append("runner opened a GPU/audio/display device")
            if all(number(sample.get("peak_rss_kib")) for sample in resources):
                resources_summary["peak_rss_mib"] = max(sample["peak_rss_kib"] for sample in resources)/1024
            for key in ("threads", "file_descriptors"):
                if all(integer(sample.get(key)) for sample in resources):
                    values = [sample[key] for sample in resources]
                    resources_summary[key] = {"minimum": min(values), "maximum": max(values), "span": max(values)-min(values)}
            for key in ("cpu_user_s", "cpu_system_s"):
                if all(number(sample.get(key)) for sample in resources):
                    resources_summary[key] = max(sample[key] for sample in resources)-min(sample[key] for sample in resources)
    except (OSError, ValueError, KeyError, TypeError) as error:
        issues.append(str(error))
    throughput = throughput_report([row for row in episodes if row["scope"] == "throughput"], config)
    if throughput["measured_episodes"] != config.get("setups", {}).get("throughput", {}).get("episodes", 12):
        issues.append("throughput does not contain the declared number of scripted benchmark episodes")
    if not throughput["measured_episodes"] or not throughput["policy_transitions"]:
        issues.append("real policy transition throughput was not measured")
    # Readiness means the environment works. The separately reported speed gate
    # decides whether collecting a small PPO batch is practical on this server.
    return {"schema_version": 1, "kind": "body_arena_stage5_report", "status": "complete" if not issues else "incomplete",
            "full_stage5_evidence": not issues, "issues": issues, "episodes": episodes,
            "reset_count": sum(row["scope"] == "reset" for row in episodes), "native_parity": parity,
            "resources": resources_summary,
            "reset_latency": {"samples": sum(row["scope"] == "reset" for row in episodes),
                "p50_s": percentile([row["reset_wall_s"] for row in episodes if row["scope"] == "reset"], .5),
                "p95_s": percentile([row["reset_wall_s"] for row in episodes if row["scope"] == "reset"], .95)},
            "throughput": throughput, "next_step": "small PPO collection is feasible" if not issues and throughput["small_ppo_collection_gate"] else "improve usable collection yield or server throughput before substantial PPO collection",
            "limitations": ["One runner, CPU headless simulation; no model or optimizer was run.",
                            "Reproducibility covers initial semantic state, not bitwise identity of complete fights.",
                            "control_lost marks a candidate LOS boundary; it is neither a win nor a time-limit truncation, and its fragment is excluded from actor updates.",
                            "Paired diagnostic seeds and mirrored placements do not establish learner quality."]}


def markdown(report: dict[str, Any]) -> str:
    throughput = report["throughput"]
    lines = ["# Headless arena validation", "", f"Status: **{report['status']}**. Resets: {report['reset_count']}.", "",
             f"Diagnostic executed policy transitions: {throughput['policy_transitions']}; end-to-end {throughput['policy_transitions_s']:.3f}/s; episodes {throughput['episodes_s']:.3f}/s.",
             f"Actor-update-usable transitions: {throughput['actor_update_transitions']}; end-to-end {throughput['actor_update_transitions_s']:.3f}/s including discarded episode time.",
             f"Discarded benchmark episodes: {throughput['discarded_episodes']}/{throughput['measured_episodes']}; discarded transitions: {throughput['discarded_transitions']}.",
             f"Small PPO collection gate: {throughput['small_ppo_collection_gate']}. {report['next_step']}.", "",
             "Collection forecasts exclude model inference, optimizer and future trajectory-recorder overhead.", "",
             "| Decisions | Diagnostic collection seconds | Actor-update collection seconds |", "|---:|---:|---:|"]
    for size, usable in throughput["collection_projection_s"].items():
        diagnostic = throughput["diagnostic_collection_projection_s"][size]
        lines.append(f"| {size} | {diagnostic:.2f}" if diagnostic is not None else f"| {size} | unavailable")
        lines[-1] += f" | {usable:.2f} |" if usable is not None else " | unavailable |"
    lines += ["", "| Episode | Scenario | Provider | Outcome | Status | Transitions |", "|---|---|---|---|---|---:|"]
    for row in report["episodes"]:
        lines.append(f"| {row['name']} | {row['scenario']} | {row['provider']} | {row['final'].get('outcome')} | {row['status']} | {row['commands']['policy_transitions']} |")
    lines += ["", *[f"- {issue}" for issue in report["issues"]], "", *[f"- {item}" for item in report["limitations"]], ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--native-reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.run.resolve(), read_json(args.config), args.native_reference.resolve())
    result["config_sha256"] = digest(args.config)
    result["reporter_sha256"] = digest(Path(__file__).resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/"body-arena.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    (args.output_dir/"body-arena.md").write_text(markdown(result)+"\n")
    print(f"body arena {result['status']}: {args.output_dir/'body-arena.md'}")
    return 0 if result["full_stage5_evidence"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
