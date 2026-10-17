#!/usr/bin/env python3
"""Independent defensive melee teacher and replayable physical proof rules.

Selection uses the same fresh periodic numeric row available to a model, plus
past completed self commands. Evaluation may read native collision/damage
telemetry to check an outcome; that telemetry never enters action selection.
"""
from __future__ import annotations

import math
from typing import Any

TEACHER_ID = "defensive-melee-scripted-v1"
PROVIDER = "stage7-scripted-teacher"
RULES = {1: "approach_outside_range", 2: "turn_off_axis", 3: "clear_lateral_left",
         4: "clear_lateral_right", 5: "retreat_from_observed_attack", 6: "clear_front_strike",
         7: "clear_left_followup", 8: "clear_right_followup", 9: "block_observed_attack",
         10: "assess_after_lateral_move", 11: "hold_after_defensive_retreat"}


def result(passed: bool, reason: str, **proof) -> dict[str, Any]:
    return {"passed": bool(passed), "reason": reason, "proof": proof}


def incoming(row: dict[str, Any]) -> bool:
    target = row.get("target", {})
    return target.get("attack_anim") is True or target.get("prehit") is True


def lateral_clear(row: dict[str, Any], action: int) -> bool:
    rays = row.get("local_obstacles", {}).get("rays", [])
    if len(rays) != 16:
        return False
    ray = rays[4 if action == 3 else 12]
    return ray.get("hit") is False and ray.get("distance_cm", 0) >= 299


def verify_precondition(cert: dict[str, Any], before: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    action = cert.get("action_id")
    if type(action) is not int or action not in RULES or cert.get("rule_id") != RULES[action]:
        return result(False, "unknown teacher rule")
    if cert.get("teacher_id") != TEACHER_ID or cert.get("source_provider") != PROVIDER:
        return result(False, "teacher provenance differs")
    for key in ("entity_id", "session_epoch", "world_epoch", "control_generation", "observation_seq", "t_game_ms"):
        if cert.get(key) != before.get(key):
            return result(False, "certificate does not reference its exact periodic row", field=key)
    mask = before.get("action_mask")
    target, own = before.get("target", {}), before.get("self", {})
    if (before.get("phase") != "post_simulation_post_animation" or before.get("periodic") is not True or
            before.get("can_decide") is not True or before.get("action_mask_valid") is not True or
            not isinstance(mask, list) or len(mask) != 12 or mask[action] is not True or
            target.get("visible") is not True or target.get("current_valid") is not True or
            own.get("hp", 0) <= 0 or own.get("down") is True):
        return result(False, "freshness, visibility, ownership or mask guard failed")
    actual_history = [item for item in history if item.get("entity_id") == before["entity_id"] and
                      item.get("phase") == "completed" and item.get("t_game_ms", math.inf) <= before["t_game_ms"]]
    context = cert.get("context", {})
    if context.get("goal") != "defensive_cycle":
        return result(False, "unknown teacher goal")
    last_action = actual_history[-1]["action_id"] if actual_history else None
    strikes = [item["action_id"] for item in actual_history if item["action_id"] in (6, 7, 8)]
    last_strike = strikes[-1] if strikes else None
    command_ids = [item["command_id"] for item in actual_history]
    if context.get("last_completed_action") != last_action or context.get("completed_command_ids") != command_ids:
        return result(False, "teacher context contains unrecorded or future history")
    distance, safe_range = target.get("fight_distance_cm"), target.get("g_range_cm")
    if not isinstance(distance, (int, float)) or not isinstance(safe_range, (int, float)):
        return result(False, "visible distance/range missing")
    safe = distance >= max(350, safe_range) and target.get("in_w_range") is False
    threat = incoming(before)
    focused_clear = target.get("in_w_range") is True and target.get("focus_5") is True and target.get("melee_line_clear") is True
    if action == 1:
        allowed = target.get("in_w_range") is False and not threat
    elif action == 2:
        allowed = abs(target.get("yaw_error_rad", 0)) > math.radians(5) and target.get("focus_5") is False
    elif action in (3, 4):
        allowed = safe and not threat and lateral_clear(before, action)
    elif action == 5:
        allowed = threat and target.get("in_w_range") is True and target.get("focus_30") is True and last_action == 9
    elif action in (6, 7, 8):
        # Real hits can push the opponent out of range. Approach/turn corrections
        # preserve the completed strike sequence instead of inventing a hit.
        allowed = focused_clear and not threat and (action == 6 or last_strike == (6 if action == 7 else 7))
    elif action == 9:
        allowed = threat and target.get("in_w_range") is True and target.get("focus_30") is True
    elif action == 10:
        allowed = safe and not threat and last_action in (3, 4)
    else:
        allowed = safe and not threat and last_action == 5
    return result(allowed, "declared physical guard passed" if allowed else "declared physical guard failed",
                  distance_cm=distance, safe_range_cm=safe_range, incoming_attack=threat, last_completed_action=last_action)


def select_certificate(row: dict[str, Any], action_id: int, context: dict[str, Any],
                       history: list[dict[str, Any]], *, command_id: int, certificate_seq: int,
                       wall_monotonic: float) -> dict[str, Any] | None:
    cert = {"schema_version": 1, "teacher_id": TEACHER_ID, "source_provider": PROVIDER,
            "certificate_seq": certificate_seq, "command_id": command_id, "action_id": action_id,
            "rule_id": RULES[action_id], "wall_monotonic": wall_monotonic, "context": context,
            **{key: row[key] for key in ("entity_id", "session_epoch", "world_epoch", "control_generation",
                                         "observation_seq", "t_game_ms")}}
    return cert if verify_precondition(cert, row, history)["passed"] else None


def _distance(left: list[float], right: list[float]) -> float:
    return math.hypot(left[0]-right[0], left[2]-right[2])


def _native_ids(entity: int, mechanics: list[dict[str, Any]]) -> set[int]:
    return {row["actor_id"] for row in mechanics if row.get("phase") in ("observation_actor_mapping", "arena_actor_mapping") and
            row.get("observation_entity_id") == entity and type(row.get("actor_id")) is int}


def verify_outcome(action: int, before: dict[str, Any], after: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    lifecycle = evidence.get("executions", [])
    starts = [row for row in lifecycle if row.get("phase") == "started"]
    ends = [row for row in lifecycle if row.get("phase") == "completed"]
    if len(starts) != 1 or len(ends) != 1 or any(row.get("phase") in ("cancelled", "rejected") for row in lifecycle):
        return result(False, "teacher action lacks one actual completed execution")
    start, end = starts[0], ends[0]
    if end.get("t_game_ms", -1) < start.get("t_game_ms", math.inf):
        return result(False, "execution time is reversed")
    if before.get("entity_id") != after.get("entity_id") or before.get("control_generation") != after.get("control_generation"):
        return result(False, "action crossed an ownership boundary")
    own_before, own_after = before.get("self", {}), after.get("self", {})
    if own_before.get("hp") != own_after.get("hp"):
        return result(False, "defensive demonstration received damage")
    at, moved = own_before.get("position_world_cm"), own_after.get("position_world_cm")
    if not isinstance(at, list) or not isinstance(moved, list) or len(at) != 3 or len(moved) != 3:
        return result(False, "actual positions missing")
    displacement = _distance(at, moved)
    prior_target, next_target = before.get("target", {}), after.get("target", {})
    mechanics = evidence.get("mechanics", [])
    actor_ids = _native_ids(before["entity_id"], mechanics)
    target_ids = _native_ids(prior_target["entity_id"], mechanics)
    active = [row for row in mechanics if isinstance(row.get("t_game"), int) and start["t_game_ms"] <= row["t_game"] <= end["t_game_ms"]]
    hits = [row for row in active if row.get("phase") == "hit_attempt"]
    damages = [row for row in active if row.get("phase") == "damage" and row.get("damage_kind") == "melee" and
               type(row.get("hp_before")) is int and type(row.get("hp_after")) is int and row["hp_before"] > row["hp_after"]]
    if action == 1:
        allowed = displacement >= 20 and next_target.get("in_w_range") is True and next_target.get("fight_distance_cm", math.inf) < prior_target.get("fight_distance_cm", 0)
        return result(allowed, "actual approach enters melee range" if allowed else "approach made no verified progress", displacement_cm=displacement)
    if action == 2:
        old_angle, new_angle = abs(prior_target.get("yaw_error_rad", 0)), abs(next_target.get("yaw_error_rad", math.inf))
        rotated = abs((own_after.get("yaw_rad", 0)-own_before.get("yaw_rad", 0)+math.pi) % (2*math.pi)-math.pi)
        allowed = rotated > math.radians(2) and new_angle < old_angle and next_target.get("focus_5") is True
        return result(allowed, "actual body rotation restores attack focus" if allowed else "turn did not restore focus", before_angle_rad=old_angle, after_angle_rad=new_angle)
    if action in (3, 4):
        yaw = own_before.get("yaw_rad", 0)
        lateral = (moved[0]-at[0])*(-math.sin(yaw))+(moved[2]-at[2])*math.cos(yaw)
        allowed = displacement >= 5 and lateral*(1 if action == 3 else -1) > 5
        return result(allowed, "actual clear lateral motion" if allowed else "strafe has no physical displacement in the chosen direction", displacement_cm=displacement, lateral_cm=lateral)
    if action == 5:
        attempts = [row for row in hits if row.get("actor_id") in target_ids and row.get("target_id") in actor_ids]
        applied = [row for row in attempts if row.get("result") == "applied"]
        # commitDamage calls an in-range delivery "applied" before takeDamage
        # checks the native jump defense. Only its same-tick, mapped defense
        # result proves that such a delivery was actually avoided.
        jump_defenses = [row for row in active if row.get("phase") == "defence_result" and
                         row.get("damage_kind") == "melee" and row.get("jumped_back") is True and
                         row.get("actor_id") in actor_ids and row.get("target_id") in target_ids]
        pairs = []
        used_defenses: set[int] = set()
        for hit in sorted(applied, key=lambda row: row.get("seq", -1)):
            if type(hit.get("seq")) is not int:
                continue
            matches = [defense for defense in jump_defenses if type(defense.get("seq")) is int and
                       defense["seq"] > hit["seq"] and defense["seq"] not in used_defenses and
                       defense["t_game"] == hit["t_game"] and defense["actor_id"] == hit["target_id"] and
                       defense["target_id"] == hit["actor_id"]]
            if matches:
                defense = min(matches, key=lambda row: row["seq"])
                used_defenses.add(defense["seq"])
                pairs.append({"hit_seq": hit["seq"], "defence_seq": defense["seq"]})
        misses = [row for row in attempts if row.get("result") in ("out_of_range", "out_of_focus")]
        received = [row for row in damages if row.get("actor_id") in actor_ids]
        old_distance, new_distance = prior_target.get("fight_distance_cm", math.inf), next_target.get("fight_distance_cm", 0)
        unresolved = len(attempts)-len(pairs)-len(misses)
        allowed = bool(attempts) and unresolved == 0 and not received and displacement >= 50 and new_distance-old_distance >= 50
        return result(allowed, "native jump defense or explicit miss avoids the observed attack" if allowed else "retreat has no verified avoided attack",
                      opponent_attempts=len(attempts), jump_defended_attempts=len(pairs), jump_defended_pairs=pairs,
                      explicit_miss_attempts=len(misses),
                      unresolved_attempts=unresolved, received_melee_damage=len(received), displacement_cm=displacement,
                      separation_gain_cm=new_distance-old_distance)
    if action in (6, 7, 8):
        applied = [row for row in hits if row.get("result") == "applied" and row.get("actor_id") in actor_ids and row.get("target_id") in target_ids]
        losses = [row for row in damages if row.get("actor_id") in target_ids and row.get("target_id") in actor_ids]
        linked = [loss for loss in losses if any(hit["t_game"] == loss["t_game"] for hit in applied)]
        allowed = bool(linked) and next_target.get("hp", math.inf) < prior_target.get("hp", -1)
        return result(allowed, "actual mapped melee hit and HP loss" if allowed else "strike has no actual mapped damage", target_hp_loss=sum(row["hp_before"]-row["hp_after"] for row in linked))
    if action == 9:
        blocked = [row for row in active if row.get("phase") == "defence_result" and row.get("blocked") is True and
                   row.get("actor_id") in actor_ids and row.get("target_id") in target_ids]
        return result(bool(blocked), "actual incoming melee hit was blocked" if blocked else "block has no native defended hit", blocked_hits=len(blocked))
    if action in (10, 11):
        expected = 200 if action == 10 else 300
        elapsed = end["t_game_ms"]-start["t_game_ms"]
        losses = [row for row in damages if row.get("actor_id") in actor_ids]
        allowed = (start.get("reason") == "wait_started" and end.get("reason") == "wait_elapsed" and
                   start.get("effective_primitive") == action and elapsed >= expected and
                   displacement < 2 and not losses)
        return result(allowed, "actual safe stationary wait elapsed" if allowed else "wait was transformed, interrupted or unsafe", elapsed_game_ms=elapsed, displacement_cm=displacement)
    return result(False, "unknown action")
