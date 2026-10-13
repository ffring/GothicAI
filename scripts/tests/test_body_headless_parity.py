"""Semantic parity failures which cannot be hidden by equal histograms or censoring."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import body_headless_parity as parity

SPEC = importlib.util.spec_from_file_location("baseline_test_fixture", Path(__file__).with_name("test_body_baseline_report.py"))
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)


def enriched_fixture(physics=False):
    melee, rows = fixtures.trace_fixture()
    config = json.loads(parity.DEFAULT_PARITY_CONFIG.read_text())
    for row in rows:
        phase = row["phase"]
        if phase == "actor_setup":
            row.update(x=0.0 if row["actor_id"] == 1 else 300.0, y=0.0, z=0.0,
                       weapon_damage=40, weapon_length=90, fight_tactic=4)
        if phase == "native_choice":
            row.update(fai_instruction=1, queue=[9, 0, 0, 0, 0, 0])
        if phase == "native_consume":
            row["next_primitive"] = 0
        if phase in ("phase_snapshot", "native_attempt"):
            layer = {"name": "T_1HPARADE_0", "start": 0, "clock": row["t_game"],
                     "speed": 1.0, "combination": 0, "body_state": 0, "combo_length": 0,
                     "fps": 25.0, "duration_ms": 600.0, "def_window_ms": [0, 480],
                     "def_par_ms": [], "hit_end_ms": [], "optimal_frames": []}
            if phase == "native_attempt":
                row.update(hook="set_anim", requested_anim=10, layers_after=[layer])
            else:
                row.update(animation="T_1HPARADE_0", hp=300, body_state=0, weapon_state=1,
                           down=False, dead=False, primitive=9, choice_id=1,
                           x=0.0 if row["actor_id"] == 1 else 300.0, y=0.0, z=0.0, yaw=0.0,
                           layers=[layer])
        if phase == "damage":
            row.update(down=True, dead=True)
        if phase == "defence_result":
            row["animation"] = "T_1HPARADE_0"
    if physics:
        for time in (0, 33, 66, 99, 132):
            for actor, target in ((1, 2), (2, 1)):
                fields = dict.fromkeys(config["physics_step"]["required_fields"], 0)
                fields.update(dt_ms=33, sample_phase="npc_post_movement", grounded=True, movealgo_tick=True, skip_reason="",
                              collision_scope="movealgo_trymove", collision_attempts=1,
                              collision_land=False, collision_npc=False, collision_vob=False,
                              collision_prefall=False, x=0.0 if actor == 1 else 300.0)
                rows.insert(-1, {"kind": "body_combat", "schema_version": 1, "seq": 0, "step": time//33,
                                 "t_game": time, "actor_id": actor, "target_id": target,
                                 "actor": "SAME_SYMBOL", "target": "SAME_SYMBOL", "phase": "physics_step", **fields})
        rows.sort(key=lambda row: row["t_game"])
        # Keep terminal after all samples at the terminal timestamp.
        terminal = next(row for row in rows if row["phase"] == "episode_end")
        rows.remove(terminal)
        rows.append(terminal)
    for seq, row in enumerate(rows, 1):
        row["seq"] = seq
    rendered = {key: "fixture-hash" for key in config["provenance_equal"]}
    rendered.update(assets=[{"path": "Data/fixture.vdf", "sha256": "abc", "size": 1}],
                    headless=False, sim_fps=30, game_version="Gothic II")
    headless = {**deepcopy(rendered), **config["headless_manifest"]}
    return melee, config, rows, rendered, headless


def evaluate(rows, candidate, melee, config, rendered, headless, profile="archived_reference"):
    return parity.compare(rows, candidate, rendered, headless, melee, config, profile)


def test_lifetime_and_absolute_clock_remapping_preserves_semantics():
    melee, config, rows, rendered, headless = enriched_fixture()
    candidate = deepcopy(rows)
    for row in candidate:
        row["actor_id"] += 100
        row["target_id"] += 100
        row["t_game"] += 1000
        row["step"] += 30
        for field in ("layers", "layers_after"):
            for layer in row.get(field, []):
                layer["start"] += 1000
                layer["clock"] += 1000
    report = evaluate(rows, candidate, melee, config, rendered, headless)
    assert report["status"] == "matched"
    assert not report["full_stage2_evidence"]  # archival trace cannot prove collision internals
    assert "def_par_window" in report["episodes"][0]["unexercised_guards"]


def test_equal_histograms_do_not_hide_reordered_acceptance():
    melee, config, rows, rendered, headless = enriched_fixture()
    candidate = deepcopy(rows)
    attempts = [row for row in candidate if row["phase"] == "native_attempt" and row["actor_id"] == 1]
    attempts[0]["accepted"], attempts[1]["accepted"] = attempts[1]["accepted"], attempts[0]["accepted"]
    report = evaluate(rows, candidate, melee, config, rendered, headless)
    assert report["status"] == "diverged"
    assert any(diff["area"] == "native_attempt" for diff in report["episodes"][0]["first_differences"])


def test_queue_order_is_exact_even_when_first_primitive_unchanged():
    melee, config, rows, rendered, headless = enriched_fixture()
    candidate = deepcopy(rows)
    next(row for row in candidate if row["phase"] == "native_choice")["queue"][1] = 4
    report = evaluate(rows, candidate, melee, config, rendered, headless)
    assert report["status"] == "diverged"
    assert any("queue" in diff["detail"] for diff in report["episodes"][0]["first_differences"])


@pytest.mark.parametrize("mutation", ("damage", "animation", "guard", "terminal"))
def test_mechanics_changes_are_not_outcome_distribution_matches(mutation):
    melee, config, rows, rendered, headless = enriched_fixture()
    candidate = deepcopy(rows)
    if mutation == "damage":
        next(row for row in candidate if row["phase"] == "damage")["net"] -= 1
    elif mutation == "animation":
        next(row for row in candidate if row["phase"] == "native_attempt")["layers_after"][0]["def_window_ms"][1] = 479
    elif mutation == "guard":
        next(row for row in candidate if row["phase"] == "phase_snapshot" and row["t_game"] == 33)["defence"] = False
    else:
        candidate[-1]["actor_hp"] = 299
    report = evaluate(rows, candidate, melee, config, rendered, headless)
    assert report["status"] == "diverged"


def test_missing_terminal_is_incomplete_and_never_full_evidence():
    melee, config, rows, rendered, headless = enriched_fixture()
    report = evaluate(rows, rows[:-1], melee, config, rendered, headless)
    assert report["status"] == "incomplete"
    assert not report["full_stage2_evidence"]
    assert any("infrastructure interruption" in issue for issue in report["issues"])


def test_censored_right_edge_is_not_treated_as_completed_guard_window():
    melee, config, rows, rendered, headless = enriched_fixture()
    candidate = deepcopy(rows)
    for row in candidate:
        if row["phase"] == "phase_snapshot" and row["actor_id"] == 2 and row["t_game"] == 132:
            row["prehit"] = False
    report = evaluate(rows, candidate, melee, config, rendered, headless)
    assert report["status"] == "diverged"
    assert any(diff["area"] == "guard_censoring" for diff in report["episodes"][0]["first_differences"])


def test_absent_root_motion_cannot_close_full_parity():
    melee, config, rows, rendered, headless = enriched_fixture()
    report = evaluate(rows, deepcopy(rows), melee, config, rendered, headless, "full")
    assert report["status"] == "incomplete"
    assert not report["full_stage2_evidence"]
    assert any("root-motion/collision" in gap for gap in report["episodes"][0]["evidence_gaps"])


def test_full_physics_trace_matches_but_collision_difference_fails():
    melee, config, rows, rendered, headless = enriched_fixture(physics=True)
    candidate = deepcopy(rows)
    report = evaluate(rows, candidate, melee, config, rendered, headless, "full")
    assert report["status"] == "matched" and report["full_stage2_evidence"]
    next(row for row in candidate if row["phase"] == "physics_step")["collision_failed_count"] = 1
    report = evaluate(rows, candidate, melee, config, rendered, headless, "full")
    assert report["status"] == "diverged"
    assert any(diff["area"] == "physics" for diff in report["episodes"][0]["first_differences"])


def test_native_skipped_movement_requires_explicit_zero_collision_evidence():
    melee, config, rows, rendered, headless = enriched_fixture(physics=True)
    row = next(row for row in rows if row["phase"] == "physics_step")
    row.update(movealgo_tick=False, skip_reason="npc_tick_without_movealgo", collision_scope="none",
               collision_attempts=0, collision_failed_count=0,
               root_dx=0, root_dy=0, root_dz=0, requested_dx=0, requested_dy=0, requested_dz=0)
    candidate = deepcopy(rows)
    report = evaluate(rows, candidate, melee, config, rendered, headless, "full")
    assert report["status"] == "matched" and report["full_stage2_evidence"]
    next(row for row in candidate if row["phase"] == "physics_step")["collision_attempts"] = 1
    report = evaluate(rows, candidate, melee, config, rendered, headless, "full")
    assert report["status"] == "incomplete"
    assert any("skipped movement" in gap for gap in report["episodes"][0]["evidence_gaps"])


@pytest.mark.parametrize("offset,status", ((1.0, "matched"), (2.01, "diverged")))
def test_spatial_tolerance_is_predeclared_not_relative_to_fight_length(offset, status):
    melee, config, rows, rendered, headless = enriched_fixture()
    candidate = deepcopy(rows)
    for row in candidate:
        if row["phase"] == "phase_snapshot":
            row["x"] += offset
    assert evaluate(rows, candidate, melee, config, rendered, headless)["status"] == status


def test_sparse_physics_and_missing_device_evidence_are_incomplete():
    melee, config, rows, rendered, headless = enriched_fixture(physics=True)
    candidate = [row for row in deepcopy(rows) if not (row["phase"] == "physics_step" and row["actor_id"] == 1 and row["t_game"] == 66)]
    headless.pop("graphics_device_created")
    report = evaluate(rows, candidate, melee, config, rendered, headless, "full")
    assert report["status"] == "incomplete"
    assert any("missing measured headless evidence" in issue for issue in report["issues"])
    assert any("missing/duplicate physics steps" in gap for gap in report["episodes"][0]["evidence_gaps"])


def test_settings_or_graphics_creation_invalidates_claimed_headless_match():
    melee, config, rows, rendered, headless = enriched_fixture()
    headless.update(settings_sha256="changed", graphics_device_created=True)
    report = evaluate(rows, deepcopy(rows), melee, config, rendered, headless)
    assert report["status"] == "diverged"
    assert any("settings_sha256" in issue for issue in report["provenance_differences"])
    assert any("graphics_device_created" in issue for issue in report["provenance_differences"])


def test_malformed_animation_layers_report_missing_evidence_without_crashing():
    melee, config, rows, rendered, headless = enriched_fixture()
    candidate = deepcopy(rows)
    next(row for row in candidate if row["phase"] == "phase_snapshot")["layers"] = {"name": "invalid container"}
    report = evaluate(rows, candidate, melee, config, rendered, headless)
    assert report["status"] == "incomplete"
    assert any("invalid layers" in issue for issue in report["issues"])


def test_target_detachment_cannot_be_hidden_by_per_actor_signature():
    melee, config, rows, rendered, headless = enriched_fixture()
    candidate = deepcopy(rows)
    next(row for row in candidate if row["phase"] == "native_attempt")["target_id"] = 0
    report = evaluate(rows, candidate, melee, config, rendered, headless)
    assert report["status"] == "diverged"
    assert any(diff["area"] == "global_event_order" for diff in report["episodes"][0]["first_differences"])


def test_root_displacement_tolerance_uses_3d_norm():
    melee, config, rows, rendered, headless = enriched_fixture(physics=True)
    candidate = deepcopy(rows)
    row = next(row for row in candidate if row["phase"] == "physics_step")
    row.update(root_dx=1.2, root_dy=1.2, root_dz=1.2)
    report = evaluate(rows, candidate, melee, config, rendered, headless, "full")
    assert report["status"] == "diverged"
    assert any("3D delta" in diff["detail"] for diff in report["episodes"][0]["first_differences"])


def test_headless_schedule_match_is_distinct_from_rendered_parity():
    melee, config, rows, rendered, headless = enriched_fixture(physics=True)
    report = evaluate(rows, deepcopy(rows), melee, config, headless, headless, "full")
    assert report["status"] == "matched"
    assert report["comparison_kind"] == "headless_schedule_invariance"
    assert not report["full_stage2_evidence"]
