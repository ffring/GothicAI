#!/usr/bin/env python3
"""Observed reactive block teacher; labels require a real prevented melee hit.

The command selector reads only the actual periodic body observation. Native
mechanics are used afterwards to certify an effect, never to choose an action.
"""
from __future__ import annotations

from typing import Any

TEACHER_ID = "observed-reactive-defence-v1"
PROVIDER = "stage8-scripted-defence-teacher"
RULE = "block_visible_in_range_windup"
IDENTITY = ("entity_id", "session_epoch", "world_epoch", "control_generation", "observation_seq", "t_game_ms")


def result(passed: bool, reason: str, **proof: Any) -> dict[str, Any]:
    return {"passed": bool(passed), "reason": reason, "proof": proof}


def verify_precondition(cert: dict[str, Any], before: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    if (cert.get("teacher_id") != TEACHER_ID or cert.get("source_provider") != PROVIDER or
            cert.get("action_id") != 9 or cert.get("rule_id") != RULE or
            any(cert.get(key) != before.get(key) for key in IDENTITY)):
        return result(False, "reactive block certificate or exact observation identity differs")
    context = cert.get("context", {})
    if (context.get("objective") != "observed_reactive_block" or history or
            context.get("completed_command_ids") != [] or
            context.get("prepared_game_ms") != before.get("t_game_ms") or
            context.get("prepared_observation_seq") != before.get("observation_seq")):
        return result(False, "block is not the first command at the genuine prepared boundary")
    own, target, mask = before.get("self", {}), before.get("target", {}), before.get("action_mask")
    passed = (before.get("periodic") is True and before.get("event_only") is False and
              before.get("phase") == "post_simulation_post_animation" and before.get("can_decide") is True and
              before.get("action_mask_valid") is True and isinstance(mask, list) and len(mask) == 12 and mask[9] is True and
              own.get("hp") == own.get("hp_max") == 300 and own.get("down") is False and
              own.get("attack_anim") is False and own.get("defence_active") is False and own.get("in_air") is False and
              target.get("hp") == target.get("hp_max") == 300 and target.get("down") is False and
              all(target.get(key) is True for key in ("present", "visible", "current_valid", "in_w_range",
                                                      "melee_line_clear", "focus_30", "attack_anim", "prehit")))
    return result(passed, "actual visible legal incoming windup" if passed else "observed block guard failed",
                  attack_anim=target.get("attack_anim"), prehit=target.get("prehit"), in_w_range=target.get("in_w_range"))


def select_certificate(row: dict[str, Any], *, command_id: int, wall_monotonic: float) -> dict[str, Any] | None:
    cert = {"schema_version": 1, "teacher_id": TEACHER_ID, "source_provider": PROVIDER, "action_id": 9,
            "rule_id": RULE, "command_id": command_id, "certificate_seq": 1, "wall_monotonic": wall_monotonic,
            "context": {"objective": "observed_reactive_block", "completed_command_ids": [],
                        "prepared_game_ms": row.get("t_game_ms"), "prepared_observation_seq": row.get("observation_seq")},
            **{key: row.get(key) for key in IDENTITY}}
    return cert if verify_precondition(cert, row, [])["passed"] else None


def verify_outcome(action: int, before: dict[str, Any], after: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    if action != 9:
        return result(False, "reactive block teacher cannot approve another primitive")
    life = evidence.get("executions", [])
    starts, ends = [row for row in life if row.get("phase") == "started"], [row for row in life if row.get("phase") == "completed"]
    if (len(starts) != 1 or len(ends) != 1 or any(row.get("phase") in ("cancelled", "rejected") for row in life) or
            any(before.get(key) != after.get(key) for key in IDENTITY[:4]) or
            ends[0].get("t_game_ms", -1) < starts[0].get("t_game_ms", 0) or
            any(row.get("effective_primitive", 9) != 9 for row in starts+ends)):
        return result(False, "block lacks an unchanged-generation genuine completed execution")
    mechanics = evidence.get("mechanics", [])
    primary, opponent = before.get("entity_id"), before.get("target", {}).get("entity_id")
    mapped = {entity: {row.get("actor_id") for row in mechanics if row.get("phase") in
                      ("arena_actor_mapping", "observation_actor_mapping") and row.get("observation_entity_id") == entity}
              for entity in (primary, opponent)}
    if primary == opponent or any(len(ids) != 1 or None in ids for ids in mapped.values()):
        return result(False, "defence lacks unique actual actor mappings")
    own_ids, foe_ids = mapped[primary], mapped[opponent]
    active = [row for row in mechanics if type(row.get("t_game")) is int and
              starts[0]["t_game_ms"] <= row["t_game"] <= ends[0]["t_game_ms"]]
    # hit_attempt records upstream delivery without a damage_kind. The paired
    # receiver defence_result below supplies the actual melee kind.
    hits = [row for row in active if row.get("phase") == "hit_attempt" and
            row.get("actor_id") in foe_ids and row.get("target_id") in own_ids and row.get("result") == "applied"]
    defenses = [row for row in active if row.get("phase") == "defence_result" and row.get("damage_kind") == "melee" and
                row.get("actor_id") in own_ids and row.get("target_id") in foe_ids and row.get("blocked") is True]
    pairs, used = [], set()
    for hit in sorted(hits, key=lambda row: row.get("seq", -1)):
        matches = [row for row in defenses if type(hit.get("seq")) is int and type(row.get("seq")) is int and
                   row["seq"] > hit["seq"] and row["seq"] not in used and row["t_game"] == hit["t_game"] and
                   row["actor_id"] == hit["target_id"] and row["target_id"] == hit["actor_id"]]
        if matches:
            defense = min(matches, key=lambda row: row["seq"])
            used.add(defense["seq"])
            pairs.append({"hit_seq": hit["seq"], "defence_seq": defense["seq"], "t_game_ms": hit["t_game"]})
    damage = [row for row in active if row.get("phase") == "damage" and row.get("actor_id") in own_ids and
              type(row.get("hp_before")) in (int, float) and type(row.get("hp_after")) in (int, float) and
              row["hp_after"] < row["hp_before"]]
    healthy = (before.get("self", {}).get("hp") == after.get("self", {}).get("hp") == 300 and
               before.get("target", {}).get("hp") == after.get("target", {}).get("hp") == 300)
    passed = healthy and not damage and bool(hits) and len(pairs) == len(hits)
    return result(passed, "actual completed block prevented incoming melee hit" if passed else "block effect lacks complete avoided-hit proof",
                  blocked_hit_pairs=pairs, delivered_hits=len(hits), own_damage_events=len(damage), primary_hp_unchanged=healthy)
