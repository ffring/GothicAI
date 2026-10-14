#!/usr/bin/env python3
"""Causal alternatives to long lateral arcs against an actually moving peer.

Selection uses the fixed numeric policy view and the recorded physical mask.
The independent verifier reads source-bound execution/physics outcomes only
AFTER the command. No native choices, animation names, oracle visibility,
teacher task label, model, RNG or future trajectory enters selection.
"""
from __future__ import annotations

import math
from typing import Any

TEACHER_ID = "observed-safe-peer-v1"
PROVIDER = "stage8-scripted-safe-peer-teacher"
RULES = {2: "restore_observed_facing", 5: "evade_observed_incoming_strike", 11: "bounded_moving_peer_assessment"}
IDENTITY = ("entity_id", "session_epoch", "world_epoch", "control_generation")
CERT_IDENTITY = IDENTITY + ("observation_seq", "t_game_ms")
MOTION_WINDOW_MS = 2000
MIN_PEER_SPEED_CM_S = 20.
WAIT_HORIZON_S = .334
MAX_WAIT_BEARING_RAD = math.radians(20)


def result(passed: bool, reason: str, **proof) -> dict[str, Any]:
    return {"passed": bool(passed), "reason": reason, "proof": proof}


def number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def vector(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(number(item) for item in value)


def motion(row: dict[str, Any]) -> dict[str, float] | None:
    """World-frame bearing/range derivative from three existing numeric vectors."""
    own, target = row.get("self", {}), row.get("target", {})
    r, dv, v = target.get("relative_position_cm"), target.get("relative_velocity_cm_s"), own.get("linear_velocity_cm_s")
    if own.get("velocity_valid") is not True or not all(vector(value) for value in (r, dv, v)):
        return None
    d = math.hypot(r[0], r[2])
    if d <= 1:
        return None
    return {"separation_cm": d, "radial_cm_s": (r[0]*dv[0]+r[2]*dv[2])/d,
            "bearing_rad_s": (r[0]*dv[2]-r[2]*dv[0])/(d*d),
            "peer_speed_cm_s": math.hypot(dv[0]+v[0], dv[2]+v[2])}


def incoming(row: dict[str, Any]) -> bool:
    target = row.get("target", {})
    return target.get("attack_anim") is True or target.get("prehit") is True


def observed_guard(row: dict[str, Any], action: int) -> dict[str, Any]:
    """Only current numeric policy fields plus exact engine action-mask legality."""
    own, target, mask = row.get("self", {}), row.get("target", {}), row.get("action_mask")
    if (action not in RULES or row.get("periodic") is not True or row.get("phase") != "post_simulation_post_animation" or
            row.get("can_decide") is not True or row.get("native_decision_eligible") is not True or
            row.get("action_mask_valid") is not True or row.get("mask_kind") != "shared_melee_resolver" or
            not isinstance(mask, list) or len(mask) != 12 or any(type(bit) is not bool for bit in mask) or
            mask != row.get("availability_mask") or mask[action] is not True or
            target.get("visible") is not True or target.get("current_valid") is not True or target.get("down") is not False or
            not number(own.get("hp_fraction")) or own["hp_fraction"] <= 0 or own.get("down") is not False or
            any(own.get(key) is not False for key in ("in_air", "swim", "dive")) or
            target.get("melee_line_clear") is not True):
        return result(False, "current periodic visibility, geometry or shared physical mask failed")
    yaw, distance = target.get("yaw_error_rad"), target.get("fight_distance_cm")
    if not number(yaw) or not number(distance):
        return result(False, "current numeric bearing or distance missing")
    threat = incoming(row)
    if action == 2:
        allowed = not threat and target.get("focus_5") is False and math.radians(5) < abs(yaw) <= math.radians(40)
        return result(allowed, "observed focus correction" if allowed else "no safe unfocused turn opportunity", yaw_error_rad=yaw)
    if action == 5:
        rays = row.get("local_obstacles", {}).get("rays", [])
        back = rays[8] if len(rays) == 16 else {}
        # GRange is a current native-derived engagement bound, not a future
        # velocity prediction. Jump legality itself does not require WRange.
        ceiling = target.get("g_range_cm")
        allowed = (threat and number(ceiling) and 0 < distance <= ceiling and target.get("focus_30") is True and
                   back.get("hit") is False and number(back.get("distance_cm")) and back["distance_cm"] >= 150)
        return result(allowed, "observed near-threat and clear retreat" if allowed else "no real near-threat or safe retreat",
                      threat_distance_cm=distance, native_g_range_ceiling_cm=ceiling, in_w_range=target.get("in_w_range"))
    dynamics = motion(row)
    g_range = target.get("g_range_cm")
    if dynamics is None or not number(g_range):
        return result(False, "moving-peer velocity/distance unavailable")
    # Absolute yaw growth is conservative; there is no assumption of continued
    # clockwise motion, an animation pose cone, or an unobserved future attack.
    projected_bearing = abs(yaw)+abs(dynamics["bearing_rad_s"])*WAIT_HORIZON_S
    projected_distance = distance+min(0., dynamics["radial_cm_s"])*WAIT_HORIZON_S
    allowed = (not threat and target.get("focus_30") is True and target.get("in_w_range") is False and
               dynamics["peer_speed_cm_s"] >= MIN_PEER_SPEED_CM_S and
               projected_bearing <= MAX_WAIT_BEARING_RAD and projected_distance >= max(350., g_range+50))
    return result(allowed, "bounded observed moving-peer assessment" if allowed else "300ms wait exceeds observed safety margin",
                  **dynamics, projected_bearing_rad=projected_bearing, projected_distance_cm=projected_distance)


def choose(row: dict[str, Any], history: list[dict[str, Any]]) -> int | None:
    """One deterministic rule for every setup, side, seed and peer schedule."""
    del history  # Past completions attest certificates; they do not select a label.
    for action in ((5, 2, 11) if incoming(row) else (2, 11)):
        if observed_guard(row, action)["passed"]:
            return action
    return None


def verify_precondition(cert: dict[str, Any], before: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    action = cert.get("action_id")
    if (type(action) is not int or action not in RULES or cert.get("rule_id") != RULES[action] or
            cert.get("teacher_id") != TEACHER_ID or cert.get("source_provider") != PROVIDER or
            any(cert.get(key) != before.get(key) for key in CERT_IDENTITY)):
        return result(False, "teacher certificate or exact pre-command identity differs")
    past = [row for row in history if row.get("entity_id") == before["entity_id"] and row.get("phase") == "completed" and
            row.get("t_game_ms", math.inf) <= before["t_game_ms"]]
    if cert.get("context") != {"completed_command_ids": [row["command_id"] for row in past]}:
        return result(False, "certificate contains a task hint or unrecorded/future command")
    return observed_guard(before, action)


def select_certificate(row: dict[str, Any], action: int, history: list[dict[str, Any]], *, command_id: int,
                       certificate_seq: int, wall_monotonic: float) -> dict[str, Any] | None:
    cert = {"schema_version": 1, "teacher_id": TEACHER_ID, "source_provider": PROVIDER,
            "action_id": action, "rule_id": RULES.get(action), "command_id": command_id,
            "certificate_seq": certificate_seq, "wall_monotonic": wall_monotonic,
            "context": {"completed_command_ids": [entry["command_id"] for entry in history]},
            **{key: row[key] for key in CERT_IDENTITY}}
    return cert if verify_precondition(cert, row, history)["passed"] else None


def verify_outcome(action: int, before: dict[str, Any], after: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """A failed physical segment remains diagnostic; no intended action is a label."""
    lifecycle, decision = evidence.get("executions", []), evidence.get("decision", {})
    accepted = [row for row in lifecycle if row.get("phase") == "accepted"]
    starts = [row for row in lifecycle if row.get("phase") == "started"]
    ends = [row for row in lifecycle if row.get("phase") == "completed"]
    if (action not in RULES or len(accepted) != 1 or len(starts) != 1 or len(ends) != 1 or
            decision.get("accepted") is not True or decision.get("source") != "scripted" or decision.get("action_id") != action or
            any(decision.get(key) != before.get(key) for key in IDENTITY) or
            any(any(row.get(key) != decision.get(key) for key in IDENTITY + ("decision_id", "execution_id", "source")) or
                row.get("command_id", decision.get("command_id")) != decision.get("command_id") for row in lifecycle) or
            any(row.get("phase") in ("cancelled", "rejected") for row in lifecycle) or
            any(row.get("action_id") != action for row in lifecycle) or
            any(before.get(key) != after.get(key) for key in IDENTITY)):
        return result(False, "no single actual accepted/started/completed effect with unchanged identity")
    start, end = starts[0], ends[0]
    if not (before["t_game_ms"] <= start["t_game_ms"] <= end["t_game_ms"] <= after["t_game_ms"] and
            type(start.get("raw_capture_seq")) is int and type(end.get("raw_capture_seq")) is int and
            start["raw_capture_seq"] < end["raw_capture_seq"] and
            start.get("execution_id") == end.get("execution_id") == decision.get("execution_id") and
            start.get("command_id", decision.get("command_id")) == end.get("command_id", decision.get("command_id")) == decision.get("command_id")):
        return result(False, "execution timestamps or actual raw lifecycle identity/order differ")
    wrappers = [row for row in evidence.get("observations", []) if row.get("entity_id") == before["entity_id"] and
                isinstance(row.get("observation"), dict)]
    wrappers.sort(key=lambda row: row.get("raw_capture_seq", -1))
    anchor = next((row for row in wrappers if row["observation"].get("observation_seq") == before["observation_seq"] and
                   row["observation"].get("t_game_ms") == before["t_game_ms"] and row["observation"] == before), None)
    later_commands = [row for row in evidence.get("decisions", []) if row.get("accepted") is True and
                      row.get("entity_id") == before["entity_id"] and row.get("raw_capture_seq", 0) > decision.get("raw_capture_seq", math.inf)]
    boundary = min((row["raw_capture_seq"] for row in later_commands), default=math.inf)
    settled = next((row for row in wrappers if row.get("raw_capture_seq", math.inf) < boundary and
                    row["observation"].get("periodic") is True and row["observation"].get("t_game_ms", -1) >= end["t_game_ms"] and
                    row["observation"].get("can_decide") is True and row["observation"].get("action_mask_valid") is True), None)
    if (anchor is None or settled is None or settled["raw_capture_seq"] <= end.get("raw_capture_seq", 0) or
            decision.get("observation_seq") != anchor.get("observation_seq") or
            decision.get("raw_observation_record_seq") != anchor.get("raw_capture_seq") or
            anchor["raw_capture_seq"] >= decision.get("raw_capture_seq", 0) or
            accepted[0].get("raw_capture_seq") != decision.get("raw_capture_seq") or
            accepted[0].get("raw_capture_seq", math.inf) >= start["raw_capture_seq"] or
            not before["t_game_ms"] <= accepted[0].get("t_game_ms", -1) <= start["t_game_ms"]):
        return result(False, "exact anchor, accepted receipt/order or first settled pre-next-command row missing")
    relevant = [row["observation"] for row in wrappers if anchor["raw_capture_seq"] <= row["raw_capture_seq"] <= settled["raw_capture_seq"]]
    valid = lambda row: (all(row.get(key) == before.get(key) for key in IDENTITY) and
                         row.get("self", {}).get("hp") == before["self"].get("hp") and
                         row.get("target", {}).get("entity_id") == before["target"].get("entity_id") and
                         row["target"].get("visible") is True and row["target"].get("current_valid") is True and
                         row["target"].get("melee_line_clear") is True and row["target"].get("down") is False and
                         row["self"].get("down") is False and
                         all(row["self"].get(key) is False for key in ("in_air", "swim", "dive")))
    periodic = [row for row in relevant if row.get("periodic") is True and row.get("phase") == "post_simulation_post_animation"]
    if (not relevant or not all(valid(row) for row in relevant) or not periodic or
            periodic[0]["t_game_ms"] != before["t_game_ms"] or periodic[-1]["t_game_ms"] != settled["observation"]["t_game_ms"] or
            any(right["t_game_ms"]-left["t_game_ms"] > 67 for left, right in zip(periodic, periodic[1:])) or
            not any(row["t_game_ms"] == end["t_game_ms"] for row in relevant) or
            any(start["t_game_ms"] <= row.get("t_game_ms", -1) < end["t_game_ms"] for row in later_commands)):
        return result(False, "numeric trajectory has visibility/HP/ground/identity/gap loss or an overlapping command")
    settled_row = settled["observation"]
    if settled_row["target"].get("focus_5" if action == 2 else "focus_30") is not True:
        return result(False, "actual completion does not regain a visible settled focus")
    past = [row for row in wrappers if row["raw_capture_seq"] <= anchor["raw_capture_seq"] and
            before["t_game_ms"]-MOTION_WINDOW_MS <= row["observation"].get("t_game_ms", -1) <= before["t_game_ms"] and
            all(row["observation"].get(key) == before.get(key) for key in IDENTITY) and
            row["observation"].get("target", {}).get("entity_id") == before["target"]["entity_id"]]
    moving = [row for row in past if row["observation"].get("target", {}).get("visible") is True and
              row["observation"].get("target", {}).get("current_valid") is True and
              motion(row["observation"]) is not None and motion(row["observation"])["peer_speed_cm_s"] >= MIN_PEER_SPEED_CM_S]
    if not moving:
        return result(False, "no causally prior actual moving peer in the numeric stream")
    mechanics = evidence.get("mechanics", [])
    own_ids = {row["actor_id"] for row in mechanics if row.get("phase") in ("arena_actor_mapping", "observation_actor_mapping") and
               row.get("observation_entity_id") == before["entity_id"]}
    peer_ids = {row["actor_id"] for row in mechanics if row.get("phase") in ("arena_actor_mapping", "observation_actor_mapping") and
                row.get("observation_entity_id") == before["target"]["entity_id"]}
    if len(own_ids) != 1 or len(peer_ids) != 1 or own_ids & peer_ids:
        return result(False, "native actor mappings are absent or ambiguous")
    actual_motion = [row for row in mechanics if row.get("phase") == "physics_step" and row.get("actor_id") in peer_ids and
                     before["t_game_ms"]-MOTION_WINDOW_MS <= row.get("t_game", -1) <= before["t_game_ms"] and
                     row.get("movealgo_tick") is True and
                     math.hypot(row.get("moved_dx", 0), row.get("moved_dz", 0)) >= 1]
    if not actual_motion:
        return result(False, "numeric peer motion lacks actual mapped native movement")
    physics = [row for row in mechanics if row.get("phase") == "physics_step" and row.get("actor_id") in own_ids and
               start["t_game_ms"] <= row.get("t_game", -1) <= end["t_game_ms"] and row.get("movealgo_tick") is True]
    physics.sort(key=lambda row: (row["t_game"], row.get("seq", 0)))
    if (not physics or physics[0]["t_game"] > start["t_game_ms"]+34 or physics[-1]["t_game"] < end["t_game_ms"]-34 or
            any(right["t_game"]-left["t_game"] > 34 for left, right in zip(physics, physics[1:])) or
            any(row.get("grounded") is not True or row.get("collision_failed_count") != 0 or
                row.get("collision_prefall") is not False for row in physics)):
        return result(False, "actual native physics is missing, gapped, ungrounded or colliding")
    active = [row for row in mechanics if start["t_game_ms"] <= row.get("t_game", -1) <= settled_row["t_game_ms"]]
    losses = [row for row in active if row.get("phase") == "damage" and row.get("actor_id") in own_ids and
              row.get("hp_before", 0) > row.get("hp_after", 0)]
    if losses:
        return result(False, "actual own HP damage occurred")
    diagnostics = [row for row in active if row.get("phase") == "visibility_diagnostic" and row.get("actor_id") in own_ids]
    diagnostics.sort(key=lambda row: (row["t_game"], row.get("seq", 0)))
    if (not diagnostics or diagnostics[0]["t_game"] > start["t_game_ms"]+67 or
            diagnostics[-1]["t_game"] < settled_row["t_game_ms"]-67 or
            any(b["t_game"]-a["t_game"] > 67 for a, b in zip(diagnostics, diagnostics[1:]))):
        return result(False, "native-phase visibility proof is missing or gapped")
    for row in diagnostics:
        rays = [ray for ray in (row.get("center_ray"), row.get("head_ray")) if isinstance(ray, dict)]
        if (any(row.get(key) != before.get(key) for key in IDENTITY) or row.get("target_entity_id") != before["target"]["entity_id"] or
                row.get("actual_can_see") is not True or row.get("controller_visible") is not True or
                row.get("query_matches_diagnostic") is not True or row.get("release_pending") is not False or
                row.get("owner") != "scripted" or row.get("provider") != PROVIDER or
                not any(ray.get("range_pass") is True and ray.get("ray_hit") is False and ray.get("query_pass") is True for ray in rays)):
            return result(False, "an actual native phase lost visibility, geometry or ownership")
    if not any(row.get("source_phase") == "controller_segment_completed" and row["t_game"] == end["t_game_ms"] and
               row.get("execution_id") == decision.get("execution_id") and row.get("command_id") == decision.get("command_id") for row in diagnostics):
        return result(False, "actual completed endpoint lacks its mapped native visibility proof")
    positions = before["self"].get("position_world_cm"), settled_row["self"].get("position_world_cm")
    if not all(vector(value) for value in positions):
        return result(False, "physical positions unavailable")
    displacement = math.hypot(positions[1][0]-positions[0][0], positions[1][2]-positions[0][2])
    effect: dict[str, Any]
    if action == 2:
        yaw0, yaw1 = before["self"].get("yaw_rad"), settled_row["self"].get("yaw_rad")
        if not number(yaw0) or not number(yaw1):
            return result(False, "physical turn yaw unavailable")
        rotation = abs((yaw1-yaw0+math.pi) % (2*math.pi)-math.pi)
        passed = rotation > math.radians(2) and abs(settled_row["target"].get("yaw_error_rad", math.inf)) < abs(before["target"]["yaw_error_rad"])
        effect = {"rotation_rad": rotation, "focus_5": settled_row["target"].get("focus_5")}
    elif action == 11:
        elapsed = end["t_game_ms"]-start["t_game_ms"]
        passed = (elapsed >= 300 and displacement < 2 and start.get("reason") == "wait_started" and
                  end.get("reason") == "wait_elapsed" and start.get("effective_primitive") == 11 and
                  all(not incoming(row) for row in relevant))
        effect = {"elapsed_game_ms": elapsed, "stationary_displacement_cm": displacement}
    else:
        attempts = [row for row in active if row.get("phase") == "hit_attempt" and row.get("actor_id") in peer_ids and row.get("target_id") in own_ids]
        deliveries = [row for row in attempts if row.get("result") == "applied"]
        defenses = [row for row in active if row.get("phase") == "defence_result" and row.get("damage_kind") == "melee" and
                    row.get("jumped_back") is True and row.get("actor_id") in own_ids and row.get("target_id") in peer_ids]
        used, pairs = set(), []
        for hit in sorted(deliveries, key=lambda row: row.get("seq", -1)):
            matches = [row for row in defenses if type(hit.get("seq")) is int and type(row.get("seq")) is int and
                       row["seq"] > hit["seq"] and row["seq"] not in used and row["t_game"] == hit["t_game"] and
                       row["actor_id"] == hit["target_id"] and row["target_id"] == hit["actor_id"]]
            if matches:
                defense = min(matches, key=lambda row: row["seq"])
                used.add(defense["seq"])
                pairs.append({"hit_seq": hit["seq"], "defence_seq": defense["seq"]})
        misses = [row for row in attempts if row.get("result") in ("out_of_range", "out_of_focus")]
        gain = settled_row["target"].get("fight_distance_cm", -math.inf)-before["target"]["fight_distance_cm"]
        passed = bool(attempts) and len(pairs)+len(misses) == len(attempts) and displacement >= 50 and gain >= 50
        effect = {"displacement_cm": displacement, "separation_gain_cm": gain, "defence_pairs": pairs, "explicit_misses": len(misses)}
    return result(passed, "actual safe moving-peer effect" if passed else "declared effect was not physically verified",
                  **effect, numeric_rows=len(relevant), periodic_rows=len(periodic), physics_rows=len(physics), native_visibility_rows=len(diagnostics),
                  motion_anchor_raw_capture_seq=moving[-1]["raw_capture_seq"], peer_motion_rows=len(actual_motion),
                  settled_raw_capture_seq=settled["raw_capture_seq"], settled_t_game_ms=settled_row["t_game_ms"],
                  maximum_periodic_gap_ms=max((b["t_game_ms"]-a["t_game_ms"] for a, b in zip(periodic, periodic[1:])), default=0),
                  visibility_failures=0, own_hp_loss=0)
