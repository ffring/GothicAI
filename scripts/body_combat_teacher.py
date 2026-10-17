#!/usr/bin/env python3
"""Independent, observational melee teacher; no native strategy or RNG calls.

The teacher uses fresh periodic observations. Native journals only verify an
already executed effect. Unresolved full-strafe visibility remains fail closed
until the separately recorded BIP/LOS diagnosis establishes a safe rule.
"""
from __future__ import annotations

import math
from typing import Any

TEACHER_ID = "observed-melee-combat-v1"
PROVIDER = "stage8-scripted-combat-teacher"
RULES = {1: "close_observed_range", 2: "restore_observed_focus", 3: "safe_left_maneuver",
         4: "safe_right_maneuver", 5: "evade_observed_strike", 6: "clear_front_opportunity",
         7: "clear_left_opportunity", 8: "clear_right_opportunity", 9: "defend_observed_strike",
         10: "safe_short_assessment", 11: "safe_long_assessment"}
IDENTITY = ("entity_id", "session_epoch", "world_epoch", "control_generation", "observation_seq", "t_game_ms")
MANEUVER_RULE = "ordinary_native_arc-v1"


def result(passed: bool, reason: str, **proof) -> dict[str, Any]:
    return {"passed": bool(passed), "reason": reason, "proof": proof}


def incoming(row: dict[str, Any]) -> bool:
    return row.get("target", {}).get("attack_anim") is True or row.get("target", {}).get("prehit") is True


def lateral_choice(row: dict[str, Any]) -> int | None:
    rays = row.get("local_obstacles", {}).get("rays", [])
    if len(rays) != 16:
        return None
    left = min(ray.get("distance_cm", 0) for ray in rays[1:8])
    right = min(ray.get("distance_cm", 0) for ray in rays[9:16])
    if min(left, right) > 250 or abs(left-right) < 20:
        return None
    return 3 if left > right else 4


def safe_lateral(row: dict[str, Any], action: int) -> dict[str, Any]:
    target, own = row.get("target", {}), row.get("self", {})
    relative = target.get("relative_position_cm")
    rays = row.get("local_obstacles", {}).get("rays", [])
    if not isinstance(relative, list) or len(relative) != 3 or len(rays) != 16:
        return result(False, "visible relative geometry or numeric collision fan missing")
    separation = math.hypot(relative[0], relative[2])
    fan = (0, 1, 2, 3, 4) if action == 3 else (0, 12, 13, 14, 15)
    clear = all(rays[index].get("hit") is False and rays[index].get("distance_cm", 0) >= 299 for index in fan)
    passed = (lateral_choice(row) == action and clear and not incoming(row) and 200 <= separation <= 451 and
              abs(target.get("yaw_error_rad", math.inf)) <= math.radians(40) and target.get("melee_line_clear") is True and
              own.get("in_air") is False and own.get("swim") is False and own.get("dive") is False)
    return result(passed, "observed asymmetric wall clearance and native-arc path passed" if passed else "native-arc observation guard failed",
                  separation_cm=separation, path_fan=list(fan), chosen_from_clearance=lateral_choice(row))


def verify_precondition(cert: dict[str, Any], before: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    action = cert.get("action_id")
    if type(action) is not int or action not in RULES or cert.get("rule_id") != RULES[action]:
        return result(False, "unknown combat teacher rule")
    if cert.get("teacher_id") != TEACHER_ID or cert.get("source_provider") != PROVIDER or any(cert.get(key) != before.get(key) for key in IDENTITY):
        return result(False, "certificate provenance or exact periodic identity differs")
    target, own, mask = before.get("target", {}), before.get("self", {}), before.get("action_mask")
    if (before.get("periodic") is not True or before.get("phase") != "post_simulation_post_animation" or
            before.get("can_decide") is not True or before.get("action_mask_valid") is not True or
            not isinstance(mask, list) or len(mask) != 12 or mask[action] is not True or
            target.get("visible") is not True or target.get("current_valid") is not True or target.get("down") is True or
            own.get("hp", 0) <= 0 or own.get("down") is True):
        return result(False, "periodic opportunity, target visibility or physical mask guard failed")
    past = [row for row in history if row.get("entity_id") == before["entity_id"] and row.get("phase") == "completed" and
            row.get("t_game_ms", math.inf) <= before["t_game_ms"]]
    context = cert.get("context", {})
    if context.get("objective") != "native_melee_bout" or context.get("completed_command_ids") != [row["command_id"] for row in past]:
        return result(False, "context contains unrecorded or future commands")
    distance, g_range = target.get("fight_distance_cm"), target.get("g_range_cm")
    if type(distance) not in (int, float) or type(g_range) not in (int, float):
        return result(False, "observed native distances missing")
    threat = incoming(before)
    if action == 1:
        allowed = target.get("in_w_range") is False and not threat
    elif action == 2:
        allowed = target.get("focus_5") is False and abs(target.get("yaw_error_rad", 0)) > math.radians(5)
    elif action in (3, 4):
        if context.get("maneuver_rule") != MANEUVER_RULE:
            return result(False, "native arc rule is not enabled for this certificate")
        return safe_lateral(before, action)
    elif action == 5:
        rays = before.get("local_obstacles", {}).get("rays", [])
        back = rays[8] if len(rays) == 16 else {}
        allowed = (threat and target.get("in_w_range") is True and target.get("focus_30") is True and
                   back.get("hit") is False and back.get("distance_cm", 0) >= 150)
    elif action in (6, 7, 8):
        allowed = (not threat and target.get("in_w_range") is True and target.get("focus_5") is True and
                   target.get("melee_line_clear") is True and target.get("defence_active") is not True)
    elif action == 9:
        allowed = threat and target.get("in_w_range") is True and target.get("focus_30") is True
    else:
        recent_arc = bool(past and past[-1].get("action_id") in (3, 4) and 0 <= before["t_game_ms"]-past[-1]["t_game_ms"] <= 200)
        expected = 10 if distance < 425 else 11
        allowed = (context.get("maneuver_rule") == MANEUVER_RULE and recent_arc and action == expected and
                   not threat and target.get("in_w_range") is False and distance >= max(350, g_range))
    return result(allowed, "observational physical rule passed" if allowed else "observational physical rule failed",
                  incoming_attack=threat, distance_cm=distance, focus_5=target.get("focus_5"))


def context(history: list[dict[str, Any]], *, maneuver_enabled: bool = False) -> dict[str, Any]:
    return {"objective": "native_melee_bout", "completed_command_ids": [row["command_id"] for row in history],
            "maneuver_rule": MANEUVER_RULE if maneuver_enabled else None}


def select_certificate(row: dict[str, Any], action: int, history: list[dict[str, Any]], *, command_id: int,
                       certificate_seq: int, wall_monotonic: float, maneuver_enabled: bool = False) -> dict[str, Any] | None:
    cert = {"schema_version": 1, "teacher_id": TEACHER_ID, "source_provider": PROVIDER, "action_id": action,
            "rule_id": RULES[action], "command_id": command_id, "certificate_seq": certificate_seq,
            "wall_monotonic": wall_monotonic, "context": context(history, maneuver_enabled=maneuver_enabled), **{key: row[key] for key in IDENTITY}}
    return cert if verify_precondition(cert, row, history)["passed"] else None


def choose(row: dict[str, Any], history: list[dict[str, Any]], *, allow_maneuver: bool = False, maneuvers_only: bool = False) -> int | None:
    """React to the observed fight; never cycle through a prescribed label list."""
    if maneuvers_only:
        if history and history[-1].get("action_id") in (10, 11):
            return None
        choices = (10, 11) if history and history[-1].get("action_id") in (3, 4) else (3, 4)
    elif incoming(row):
        health = row.get("self", {}).get("hp_fraction", 1.)
        choices = (5, 9, 2) if health < .35 else (9, 5, 2)
    else:
        target = row.get("target", {})
        settle = (10, 11) if allow_maneuver and history and history[-1].get("action_id") in (3, 4) else ()
        lateral = (3, 4) if allow_maneuver else ()
        # Actual melee bouts restore precise facing/range and take damage
        # opportunities first. The isolated curriculum has its explicit
        # observed-wall-and-settling objective instead of an endless wall loop.
        choices = (2, 1) if target.get("focus_5") is False else (6, 7, 8, 1)+settle+lateral
    for action in choices:
        cert = {"teacher_id": TEACHER_ID, "source_provider": PROVIDER, "action_id": action, "rule_id": RULES[action],
                "context": context(history, maneuver_enabled=allow_maneuver), **{key: row.get(key) for key in IDENTITY}}
        if verify_precondition(cert, row, history)["passed"]:
            return action
    return None


def verify_safe_arc(action: int, before: dict[str, Any], after: dict[str, Any], evidence: dict[str, Any],
                    start: dict[str, Any], end: dict[str, Any], displacement: float,
                    own_ids: set[int]) -> dict[str, Any]:
    wrappers = evidence.get("observations", [])
    decisions = evidence.get("decisions", [])
    decision = evidence.get("decision", {})
    next_commands = [row for row in decisions if row.get("entity_id") == before["entity_id"] and row.get("accepted") is True and
                     row.get("raw_capture_seq", 0) > decision.get("raw_capture_seq", math.inf)]
    boundary = min((row["raw_capture_seq"] for row in next_commands), default=math.inf)
    samples = [row for row in wrappers if isinstance(row.get("observation"), dict) and row.get("raw_capture_seq", math.inf) < boundary and
               row["observation"].get("entity_id") == before["entity_id"] and
               row["observation"].get("t_game_ms", -1) >= start["t_game_ms"]]
    samples.sort(key=lambda row: row["raw_capture_seq"])
    periodic = [row["observation"] for row in samples if row.get("raw_observation_source") == "periodic" and
                row["observation"].get("periodic") is True and row["observation"].get("phase") == "post_simulation_post_animation"]
    during = [row for row in periodic if row["t_game_ms"] <= end["t_game_ms"]]
    settled = next((row for row in periodic if row["t_game_ms"] >= end["t_game_ms"] and row.get("can_decide") is True and
                    row.get("action_mask_valid") is True), None)
    completed = [row["observation"] for row in samples if row["observation"]["t_game_ms"] == end["t_game_ms"]]
    visible = lambda row: row.get("target", {}).get("visible") is True and row.get("target", {}).get("current_valid") is True
    if not (during and settled and any(visible(row) for row in completed) and visible(settled) and
            settled["target"].get("focus_30") is True and settled["target"].get("melee_line_clear") is True and
            settled["self"].get("in_air") is False and settled["self"].get("swim") is False and settled["self"].get("dive") is False):
        return result(False, "safe arc lacks visible actual completion and first settled eligible focus")
    limit = settled["t_game_ms"]
    relevant = [row["observation"] for row in samples if row["observation"]["t_game_ms"] <= limit]
    if any(any(row.get(key) != before.get(key) for key in IDENTITY[:4]) or row.get("self", {}).get("hp") != before["self"]["hp"] for row in relevant):
        return result(False, "maneuver crossed ownership or received HP damage")
    if during[0]["t_game_ms"] > start["t_game_ms"]+67 or during[-1]["t_game_ms"] < end["t_game_ms"]-67 or any(
            right["t_game_ms"]-left["t_game_ms"] > 67 for left, right in zip(during, during[1:])):
        return result(False, "maneuver periodic visibility evidence has a gap")
    gap_start, maximum_gap = None, 0
    for row in during:
        if visible(row):
            if gap_start is not None:
                maximum_gap = max(maximum_gap, row["t_game_ms"]-gap_start)
                gap_start = None
        else:
            gates = row.get("self", {}).get("gates", {})
            protected = row.get("self", {}).get("interruptible") is False or any(gates.get(key) is True for key in ("fai_wait", "ani_wait", "ai_wait", "output_wait"))
            if row["t_game_ms"] >= end["t_game_ms"] or not protected:
                return result(False, "visibility gap is outside a protected actual strafe segment")
            gap_start = row["t_game_ms"] if gap_start is None else gap_start
    if gap_start is not None:
        maximum_gap = max(maximum_gap, end["t_game_ms"]-gap_start)
    mechanics = evidence.get("mechanics", [])
    native = [row for row in mechanics if row.get("actor_id") in own_ids and start["t_game_ms"] <= row.get("t_game", -1) <= limit]
    diagnostics = [row for row in native if row.get("phase") == "visibility_diagnostic"]
    physics = [row for row in native if row.get("phase") == "physics_step" and row.get("movealgo_tick") is True and row["t_game"] <= end["t_game_ms"]]
    if not diagnostics or not physics or min(row["t_game"] for row in diagnostics) > start["t_game_ms"]+67 or max(row["t_game"] for row in diagnostics) < end["t_game_ms"]-67:
        return result(False, "maneuver lacks actual native LOS and movement evidence")
    physics.sort(key=lambda row: (row["t_game"], row.get("seq", 0)))
    if physics[0]["t_game"] > start["t_game_ms"]+34 or physics[-1]["t_game"] < end["t_game_ms"]-34 or any(
            right["t_game"]-left["t_game"] > 34 for left, right in zip(physics, physics[1:])):
        return result(False, "maneuver physics evidence does not cover its actual execution")
    for row in diagnostics:
        rays = [ray for ray in (row.get("center_ray"), row.get("head_ray")) if isinstance(ray, dict)]
        # A pose-cone reason takes priority in the engine's diagnostic string;
        # inspect the SAME unconditional ray for actual range and occlusion.
        if not any(ray.get("range_pass") is True and ray.get("ray_hit") is False for ray in rays):
            return result(False, "maneuver has an actual geometry/range visibility rejection")
        if row.get("actual_can_see") is False and (row.get("reason") != "outside_pose_cone" or row["t_game"] >= end["t_game_ms"] or
                                                  not (row.get("native_fai_wait") is True or row.get("native_script_wait") is True)):
            return result(False, "native visibility failure is not a transient protected pose cone")
    invalid = [row for row in diagnostics if row.get("actual_can_see") is False]
    for index, row in enumerate(diagnostics):
        if row.get("actual_can_see") is False:
            recovered = next((later["t_game"] for later in diagnostics[index+1:] if later.get("actual_can_see") is True), limit)
            maximum_gap = max(maximum_gap, recovered-row["t_game"])
    motion = sum(math.hypot(row.get("moved_dx", 0), row.get("moved_dz", 0)) for row in physics)
    safe_physics = all(row.get("grounded") is True and row.get("collision_failed_count") == 0 and row.get("collision_prefall") is False for row in physics)
    losses = [row for row in native if row.get("phase") == "damage" and row.get("damage_kind") == "melee" and row.get("hp_before", 0) > row.get("hp_after", 0)]
    no_new_command = not any(start["t_game_ms"] <= row.get("t_game_ms", -1) < end["t_game_ms"] for row in next_commands)
    at, moved, yaw = before["self"]["position_world_cm"], after["self"]["position_world_cm"], before["self"].get("yaw_rad", 0)
    lateral = (moved[0]-at[0])*(-math.sin(yaw))+(moved[2]-at[2])*math.cos(yaw)
    direction = lateral*(1 if action == 3 else -1) > 10
    passed = 50 <= displacement <= 350 and direction and maximum_gap <= 100 and safe_physics and motion >= 50 and not losses and no_new_command
    return result(passed, "actual native arc keeps geometry visibility, focus and collision safety" if passed else "native arc physical safety gate failed",
                  displacement_cm=displacement, lateral_cm=lateral, measured_path_cm=motion, physics_steps=len(physics), visibility_samples=len(diagnostics),
                  periodic_samples=len(during), transient_pose_samples=len(invalid), maximum_invalid_gap_ms=maximum_gap,
                  settled_t_game_ms=limit, settled_observation_seq=settled["observation_seq"], no_new_command_during_arc=no_new_command)


def verify_outcome(action: int, before: dict[str, Any], after: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    lifecycle = evidence.get("executions", [])
    starts = [row for row in lifecycle if row.get("phase") == "started"]
    ends = [row for row in lifecycle if row.get("phase") == "completed"]
    if len(starts) != 1 or len(ends) != 1 or any(row.get("phase") in ("cancelled", "rejected") for row in lifecycle):
        return result(False, "command lacks one genuine completed execution")
    if any(before.get(key) != after.get(key) for key in IDENTITY[:4]) or ends[0]["t_game_ms"] < starts[0]["t_game_ms"]:
        return result(False, "effect crossed a generation or time boundary")
    positions = (before.get("self", {}).get("position_world_cm"), after.get("self", {}).get("position_world_cm"))
    if any(not isinstance(value, list) or len(value) != 3 for value in positions):
        return result(False, "actual positions missing")
    at, moved = positions
    displacement = math.hypot(moved[0]-at[0], moved[2]-at[2])
    target_before, target_after = before.get("target", {}), after.get("target", {})
    mechanics = evidence.get("mechanics", [])
    mappings = {entity: {row["actor_id"] for row in mechanics if row.get("phase") in ("arena_actor_mapping", "observation_actor_mapping") and
                row.get("observation_entity_id") == entity} for entity in (before["entity_id"], target_before["entity_id"])}
    own_ids, target_ids = mappings[before["entity_id"]], mappings[target_before["entity_id"]]
    active = [row for row in mechanics if type(row.get("t_game")) is int and starts[0]["t_game_ms"] <= row["t_game"] <= ends[0]["t_game_ms"]]
    damage = [row for row in active if row.get("phase") == "damage" and row.get("damage_kind") == "melee" and
              type(row.get("hp_before")) is int and type(row.get("hp_after")) is int and row["hp_before"] > row["hp_after"]]
    if action == 1:
        passed = displacement >= 20 and target_after.get("in_w_range") is True and target_after.get("fight_distance_cm", math.inf) < target_before.get("fight_distance_cm", 0)
        return result(passed, "actual range correction" if passed else "approach made no verified range progress", displacement_cm=displacement)
    if action == 2:
        rotated = abs((after["self"].get("yaw_rad", 0)-before["self"].get("yaw_rad", 0)+math.pi) % (2*math.pi)-math.pi)
        passed = rotated > math.radians(2) and target_after.get("focus_5") is True and abs(target_after.get("yaw_error_rad", math.inf)) < abs(target_before.get("yaw_error_rad", 0))
        return result(passed, "actual facing correction" if passed else "turn made no verified focus progress", rotation_rad=rotated)
    if action in (6, 7, 8):
        hits = [row for row in active if row.get("phase") == "hit_attempt" and row.get("result") == "applied" and
                row.get("actor_id") in own_ids and row.get("target_id") in target_ids]
        losses = [row for row in damage if row.get("actor_id") in target_ids and row.get("target_id") in own_ids and
                  any(hit["t_game"] == row["t_game"] for hit in hits)]
        passed = bool(losses) and target_after.get("hp", math.inf) < target_before.get("hp", -1)
        return result(passed, "actual offensive hit and target HP loss" if passed else "offensive execution caused no verified damage",
                      target_hp_loss=sum(row["hp_before"]-row["hp_after"] for row in losses),
                      own_hp_loss=sum(row["hp_before"]-row["hp_after"] for row in damage if row.get("actor_id") in own_ids))
    if action in (3, 4):
        return verify_safe_arc(action, before, after, evidence, starts[0], ends[0], displacement, own_ids)
    own_losses = [row for row in damage if row.get("actor_id") in own_ids]
    unchanged_hp = before["self"].get("hp") == after["self"].get("hp")
    if action in (5, 9):
        attempts = [row for row in active if row.get("phase") == "hit_attempt" and row.get("actor_id") in target_ids and row.get("target_id") in own_ids]
        deliveries = [row for row in attempts if row.get("result") == "applied"]
        flag = "jumped_back" if action == 5 else "blocked"
        defenses = [row for row in active if row.get("phase") == "defence_result" and row.get("damage_kind") == "melee" and
                    row.get(flag) is True and row.get("actor_id") in own_ids and row.get("target_id") in target_ids]
        pairs, used = [], set()
        for hit in sorted(deliveries, key=lambda row: row.get("seq", -1)):
            matches = [row for row in defenses if type(hit.get("seq")) is int and type(row.get("seq")) is int and
                       row["seq"] > hit["seq"] and row["seq"] not in used and row["t_game"] == hit["t_game"] and
                       row["actor_id"] == hit["target_id"] and row["target_id"] == hit["actor_id"]]
            if matches:
                defense = min(matches, key=lambda row: row["seq"])
                used.add(defense["seq"])
                pairs.append({"hit_seq": hit["seq"], "defence_seq": defense["seq"]})
        misses = [row for row in attempts if row.get("result") in ("out_of_range", "out_of_focus")]
        gain = target_after.get("fight_distance_cm", 0)-target_before.get("fight_distance_cm", math.inf)
        passed = unchanged_hp and not own_losses and bool(pairs)
        if action == 5:
            passed = unchanged_hp and not own_losses and bool(attempts) and len(pairs)+len(misses) == len(attempts) and displacement >= 50 and gain >= 50
        return result(passed, "actual native threat defense" if passed else "defense effect lacks a verified avoided hit",
                      native_defense=flag, defense_pairs=pairs, explicit_misses=len(misses), displacement_cm=displacement,
                      separation_gain_cm=gain, own_melee_damage=len(own_losses))
    if action in (10, 11):
        expected = 200 if action == 10 else 300
        elapsed = ends[0]["t_game_ms"]-starts[0]["t_game_ms"]
        passed = (unchanged_hp and not own_losses and displacement < 2 and elapsed >= expected and
                  starts[0].get("reason") == "wait_started" and ends[0].get("reason") == "wait_elapsed" and
                  starts[0].get("effective_primitive") == action)
        return result(passed, "actual stationary safe wait" if passed else "wait was transformed, unsafe or interrupted", elapsed_game_ms=elapsed)
    return result(False, "unknown combat effect")
