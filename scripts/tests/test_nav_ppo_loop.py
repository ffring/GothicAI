"""Training stop rules and split isolation, without a model or game process."""
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("nav_ppo_loop", Path(__file__).resolve().parents[1] / "nav_ppo_loop.py")
loop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(loop)


def metrics(arrived, turn=.2):
    return dict(all=dict(n=60, arrived=arrived, arrival_fraction=arrived / 60,
                         action_fraction=dict(turn_only=turn), blocked_ms=100,
                         median_arrival_s=30))


def state(arrived=33, decisions=0):
    return dict(baseline_arrival_fraction=arrived / 60, best_arrival_fraction=arrived / 60,
                best_score=list(loop.validation_score(metrics(arrived))), best_iteration=0,
                best_policy="round2.navbc", best_checkpoint=None, decisions=decisions,
                bad_validations=0, stop_reason=None)


def test_stop_at_30m_requires_five_percentage_points_and_boundary_is_inclusive():
    updated, _ = loop.record_validation(state(decisions=30_000_000), metrics(35), 100, "new.navbc", "new.pt")
    assert updated["stop_reason"] == "no_5pp_validation_improvement_at_30m_decisions"
    updated, _ = loop.record_validation(state(decisions=30_000_000), metrics(36), 100, "new.navbc", "new.pt")
    assert updated["stop_reason"] is None
    # The rule concerns best validation, even when the latest score has noise.
    updated["decisions"] += 300_000
    updated, _ = loop.record_validation(updated, metrics(34), 105, "worse.navbc", "worse.pt")
    assert updated["stop_reason"] is None


def test_collapse_requires_two_consecutive_drops_strictly_over_15pp():
    current = state(arrived=48)
    current, _ = loop.record_validation(current, metrics(39), 5, "a", "a.pt")
    assert current["bad_validations"] == 0  # exactly 15 points
    current, _ = loop.record_validation(current, metrics(38), 10, "b", "b.pt")
    assert current["bad_validations"] == 1
    current, _ = loop.record_validation(current, metrics(48), 15, "c", "c.pt")
    assert current["bad_validations"] == 0
    current, _ = loop.record_validation(current, metrics(38), 20, "d", "d.pt")
    current, _ = loop.record_validation(current, metrics(37), 25, "e", "e.pt")
    assert current["stop_reason"] == "validation_collapse_two_consecutive_drops_gt_15pp"
    assert current["best_policy"] == "round2.navbc"


def test_arrival_selects_checkpoint_before_tie_breakers():
    current, improved = loop.record_validation(state(), metrics(34, turn=.4), 5, "a", "a.pt")
    assert improved and current["best_policy"] == "a"
    current, improved = loop.record_validation(current, metrics(34, turn=.1), 10, "b", "b.pt")
    assert improved and current["best_policy"] == "b"
    current, improved = loop.record_validation(current, metrics(33, turn=0), 15, "c", "c.pt")
    assert not improved and current["best_policy"] == "b"


def test_success_stop_needs_two_strong_validations_after_curriculum():
    current = state(decisions=4_999_999)
    current, _ = loop.record_validation(current, metrics(50, turn=.12), 5, "a", "a.pt")
    assert current["target_validations"] == 0
    current["decisions"] = 5_000_000
    current, _ = loop.record_validation(current, metrics(50, turn=.12), 10, "b", "b.pt")
    assert current["target_validations"] == 1 and current["stop_reason"] is None
    current, _ = loop.record_validation(current, metrics(51, turn=.13), 15, "c", "c.pt")
    assert current["target_validations"] == 0
    current, _ = loop.record_validation(current, metrics(50, turn=.1), 20, "d", "d.pt")
    current, _ = loop.record_validation(current, metrics(51, turn=.1), 25, "e", "e.pt")
    assert current["stop_reason"] == "validation_target_sustained"


def test_conservative_collection_bound_includes_resets_and_stays_below_ceiling():
    bound = loop.collection_decision_bound(40_000, 8, 100)
    assert bound == 1_291_200
    assert bound > 8 * 100 * 400
    assert 148_000_000 + bound <= 150_000_000
    assert 149_000_000 + bound > 150_000_000


def test_pilot_rejects_head_collapse_even_when_total_kl_is_small():
    base = {head: .5 for head in loop.HEADS}
    low = dict(coefficient=.03, stats=dict(kl_reference=.001, entropy_by_head=dict(base, forward=.1)))
    good = dict(coefficient=.1, stats=dict(kl_reference=.02, entropy_by_head=base))
    high = dict(coefficient=.3, stats=dict(kl_reference=.005, entropy_by_head=base))
    assert loop.select_pilot([low, high, good], base) is good
    with pytest.raises(ValueError, match="no candidate"):
        loop.select_pilot([low], base)
    values = [loop.kl_coefficient(.1, step, 150_000_000) for step in (0, 30_000_000, 150_000_000)]
    assert values[0] == pytest.approx(.1)
    assert values[-1] == pytest.approx(.003)
    assert values[0] > values[1] > values[2]


def test_training_validation_reverse_overlap_is_rejected():
    training = [dict(region=region, start=f"{region}-a{i}", goal=f"{region}-b{i}",
                     detour_complete=True, detour_length_cm=5000)
                for region in sorted(loop.REGIONS) for i in range(200)]
    validation = [dict(region=region, start=f"{region}-val-a{i}", goal=f"{region}-val-b{i}",
                       final_test=True, teacher_success=True, teacher_version=2)
                  for region in sorted(loop.REGIONS) for i in range(10)]
    assert len(loop.validate_sets(training, validation)) == 1200
    validation[0].update(start=training[0]["goal"], goal=training[0]["start"])
    with pytest.raises(ValueError, match="overlaps"):
        loop.validate_sets(training, validation)


def test_shuffling_keeps_every_region_in_each_initial_block_and_changes_each_iteration():
    source = [dict(region=region, start=f"{region}-{i}") for region in sorted(loop.REGIONS) for i in range(200)]
    one = loop.balanced_routes(source, 1)
    assert one == loop.balanced_routes(source, 1)
    assert one != loop.balanced_routes(source, 2)
    assert len(one) == len(source)
    for offset in range(0, len(one), 6):
        assert {pair["region"] for pair in one[offset:offset + 6]} == loop.REGIONS


def test_raw_cleanup_keeps_episode_outcomes_and_checkpoint_is_not_in_collect(tmp_path):
    collect = tmp_path / "collect"
    episode = collect / "population/n100-r8-w0/episodes/00000"
    episode.mkdir(parents=True)
    for name in ("episode.json", "samples.jsonl", "samples.bin.gz", "bootstrap.navdemo", "actions.txt", "trajectory.txt"):
        (episode / name).write_text("{}")
    (collect / "population/n100-r8-w0/episodes.jsonl").write_text("{}\n")
    checkpoint = tmp_path / "update/last.pt"
    checkpoint.parent.mkdir()
    checkpoint.write_text("saved")
    loop.remove_raw(collect)
    assert sorted(path.name for path in collect.rglob("*") if path.is_file()) == ["episode.json", "episodes.jsonl"]
    assert checkpoint.read_text() == "saved"


def test_atomic_state_is_valid_json_without_nonfinite_values(tmp_path):
    path = tmp_path / "state.json"
    loop.write_json(path, dict(iteration=2, decisions=123))
    assert json.loads(path.read_text()) == dict(iteration=2, decisions=123)
    with pytest.raises(ValueError):
        loop.write_json(path, dict(decisions=float("nan")))
    assert json.loads(path.read_text()) == dict(iteration=2, decisions=123)


def test_validation_retains_curves_outcomes_actions_and_trajectories(tmp_path):
    for name in ("metrics.json", "episode.json", "actions.txt", "trajectory.txt", "samples.bin.gz", "samples.jsonl", "bootstrap.bin.gz"):
        (tmp_path / name).write_text("saved")
    loop.trim_validation(tmp_path)
    assert {path.name for path in tmp_path.iterdir()} == {"metrics.json", "episode.json", "actions.txt", "trajectory.txt"}
