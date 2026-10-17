"""Reaction receipt invariants use saved-shape fixtures, never engine/model calls."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_block_reaction import ReactionFixtureError, preparation_proof, ready_anchor


def scene():
    arena = {"session_epoch": 1, "world_epoch": 2, "primary_entity_id": 10, "opponent_entity_id": 20,
             "game_ms": 1067, "actors": [{"entity_id": entity, "hp": 300, "received_damage": 0,
                                          "outgoing_damage": 0, "blocked_hits": 0, "command_id": 0} for entity in (10, 20)]}
    row = {"session_epoch": 1, "world_epoch": 2, "entity_id": 10, "control_generation": 3,
           "t_game_ms": 1067, "periodic": True, "event_only": False, "phase": "post_simulation_post_animation",
           "native_decision_eligible": True, "self": {"hp": 300, "hp_max": 300, "attack_anim": False, "defence_active": False},
           "target": {"entity_id": 20, "hp": 300, "hp_max": 300, **{key: True for key in
                      ("present", "visible", "current_valid", "in_w_range", "melee_line_clear", "attack_anim", "prehit")}}}
    status = {"control_generation": 3, "purity_state": {"rng_state": "unchanged", "queue": [0]*6}}
    events = [{"entity_id": 20, "phase": phase, "action_id": 6, "command_id": 1, "execution_id": 7,
               "t_game_ms": t} for phase, t in (("accepted", 1033), ("started", 1067))]
    mechanics = [{"phase": "arena_actor_mapping", "observation_entity_id": 10, "actor_id": 30}]
    initial = {**arena, "game_ms": 1000}
    return initial, arena, status, deepcopy(status), events, mechanics, row


def test_real_periodic_incoming_attack_has_no_prep_model_or_quality_credit():
    values = scene()
    proof = preparation_proof(*values)
    assert proof["prepared_game_ms"] == 1067 and proof["preparation_game_ms"] == 67
    assert proof["preparation_quality_credit"] is False and proof["primary_commands_before_activation"] == 0
    assert proof["anchor"] == values[-1] and proof["attacker_started"]["execution_id"] == 7


@pytest.mark.parametrize("change", ["event_only", "no_attack", "stale", "ineligible", "not_visible"])
def test_an_event_or_unobserved_threat_cannot_be_the_first_model_scene(change):
    _, arena, _, _, _, _, row = scene()
    if change == "event_only":
        row.update(periodic=False, event_only=True)
    elif change == "stale":
        row["t_game_ms"] -= 33
    elif change == "ineligible":
        row["native_decision_eligible"] = False
    else:
        row["target"]["attack_anim" if change == "no_attack" else "visible"] = False
    assert not ready_anchor(row, arena)


@pytest.mark.parametrize("change", ["rng", "primary_command", "primary_native_choice", "already_hit", "unstarted_attack"])
def test_held_preparation_rejects_early_actions_rng_hits_and_unproved_attacks(change):
    initial, arena, before, after, events, mechanics, row = scene()
    if change == "rng":
        after["purity_state"]["rng_state"] = "advanced"
    elif change == "primary_command":
        events.append({"entity_id": 10, "phase": "accepted", "action_id": 0, "command_id": 1})
    elif change == "primary_native_choice":
        mechanics.append({"actor_id": 30, "phase": "native_choice", "t_game": 1033})
    elif change == "already_hit":
        mechanics.append({"actor_id": 40, "phase": "hit_attempt", "t_game": 1033})
    else:
        events[:] = [event for event in events if event["phase"] != "started"]
    with pytest.raises(ReactionFixtureError):
        preparation_proof(initial, arena, before, after, events, mechanics, row)
