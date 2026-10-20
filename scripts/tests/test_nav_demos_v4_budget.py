"""Pure phase forecasts: retained recordings, equal roads, fixed other goals."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("nav_demos_v4_budget", Path(__file__).resolve().parents[1] / "nav_demos_v4_budget.py")
budget = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(budget)


def corpus(point_count=126, decisions=1200):
    selections = {name: [] for name in budget.TARGETS}
    selections["point"] = [dict(id=f"p-{index}", region=f"r{index % 6}") for index in range(400)]
    episodes = [dict(value, family="point", decisions=decisions, payload_bytes=decisions * 16000,
                     training_eligible=True) for value in selections["point"][:point_count]]
    for name, count, good, average in (("object", 120, 98, 675), ("approach", 81, 61, 1500),
                                     ("jump", 108, 90, 191), ("follow_below_gate", 48, 29, 2352)):
        candidates = 168 if name == "jump" else 410
        selections[name] = [dict(id=f"{name}-{index}") for index in range(candidates)]
        episodes.extend(dict(family=name, id=f"{name}-{index}", decisions=average,
            payload_bytes=average * 16000, training_eligible=index < good) for index in range(count))
    return dict(episodes=episodes), selections


def test_measured_cost_reduces_only_points_with_equal_regions_and_keeps_old_pairs():
    summary, selections = corpus()
    before = deepcopy((summary, selections))
    result = budget.plan(summary, selections, 6_556_700_000)
    assert result["can_collect"] and result["reason"] == "point_reduced_for_budget"
    assert 126 <= result["point_limit"] < 400 and result["point_limit"] % 6 == 0
    assert len({row["quota"] for row in result["point_regions"].values()}) == 1
    assert {row["id"] for row in summary["episodes"] if row["family"] == "point"} <= set(result["point_ids"])
    assert len(result["point_ids"]) == result["point_limit"]
    assert result["forecast_bytes"] <= 15_000_000_000 < result["unreduced_forecast_bytes"]
    assert result["targets"] == dict(budget.TARGETS, point=result["point_limit"])
    assert (summary, selections) == before


def test_no_reduction_when_original_400_fit_and_minimum_decision_cost_applies():
    summary, selections = corpus(decisions=200)
    result = budget.plan(summary, selections, 3_000_000_000)
    assert result["point_limit"] == 400 and result["reason"] == "within_budget"
    assert result["by_family"]["point"]["forecast_bytes_per_episode"] == 200 * 17000
    assert result["point_ids"] == [value["id"] for value in selections["point"]]


def test_previous_reduced_cap_never_grows_when_cost_becomes_lower():
    summary, selections = corpus(decisions=200)
    result = budget.plan(summary, selections, 3_000_000_000, previous_point_limit=300)
    assert result["point_limit"] == 300 and result["previous_point_limit"] == 300
    assert result["targets"]["follow_below_gate"] == 60


def test_reduction_to_already_recorded_balanced_points_marks_corpus_complete():
    summary, selections = corpus()
    for value in summary["episodes"]:
        if value["family"] in ("object", "follow_below_gate"):
            value["training_eligible"] = True
    for name, begin, end in (("jump", 108, 168), ("follow_below_gate", 48, 60)):
        summary["episodes"].extend(dict(family=name, id=f"{name}-{index}",
            decisions=200, payload_bytes=3_000_000, training_eligible=True)
            for index in range(begin, end))
    result = budget.plan(summary, selections, 14_700_000_000, previous_point_limit=132)
    assert result["point_limit"] == 126 and result["point_reduced"]
    assert all(value["complete"] for value in result["by_family"].values())
    assert result["reason"] == "complete" and not result["can_collect"]
    assert result["forecast_bytes"] == 14_700_000_000


def test_larger_cost_can_reduce_previous_cap_further():
    summary, selections = corpus(decisions=2200)
    result = budget.plan(summary, selections, 6_556_700_000, previous_point_limit=378)
    assert result["can_collect"] and result["point_limit"] < 378
    assert result["point_limit"] >= 126 and result["point_limit"] % 6 == 0


def test_nonpoint_forecast_that_cannot_fit_stops_without_reducing_other_goals():
    summary, selections = corpus()
    result = budget.plan(summary, selections, 14_500_000_000)
    assert not result["can_collect"] and result["reason"] == "budget_blocked_nonpoint"
    assert result["targets"] == budget.TARGETS


def test_recorded_region_imbalance_sets_minimum_quota_and_retains_nonprefix_ids():
    summary, selections = corpus(point_count=0, decisions=1200)
    old = [value for value in selections["point"] if value["region"] == "r0"][-50:]
    summary["episodes"].extend(dict(value, family="point", decisions=1200,
        payload_bytes=19_000_000, training_eligible=True) for value in old)
    result = budget.plan(summary, selections, 6_556_700_000)
    assert set(value["id"] for value in old) <= set(result["point_ids"])
    assert result["point_regions"]["r0"]["recorded"] == 50
    assert result["point_limit"] >= 300


def test_cannot_cut_below_already_recorded_region_quota():
    summary, selections = corpus(point_count=0)
    old = [value for value in selections["point"] if value["region"] == "r0"]
    summary["episodes"].extend(dict(value, family="point", decisions=2400,
        payload_bytes=38_000_000, training_eligible=True) for value in old)
    result = budget.plan(summary, selections, 6_556_700_000)
    assert not result["can_collect"] and result["reason"] == "budget_blocked_preserve_recorded_points"
    assert result["point_limit"] == 400


def test_exhausted_success_candidate_pool_stops():
    summary, selections = corpus()
    selections["object"] = selections["object"][:120]
    result = budget.plan(summary, selections, 3_000_000_000)
    assert not result["can_collect"] and result["reason"] == "candidate_pool_exhausted"
    assert result["exhausted_families"] == ["object"]


def test_empty_success_history_is_conservative_and_complete_families_cost_zero():
    summary, selections = corpus()
    for value in summary["episodes"]:
        if value["family"] == "follow_below_gate":
            value["training_eligible"] = False
    result = budget.plan(summary, selections, 3_000_000_000)
    assert result["by_family"]["follow_below_gate"]["eligible_rate"] == 1 / 49
    assert result["by_family"]["approach"]["estimated_remaining_attempts"] == 0
    assert not result["can_collect"]


def test_physical_free_space_also_limits_future_recording_budget():
    summary, selections = corpus()
    result = budget.plan(summary, selections, 6_556_700_000, free_bytes=100_000_000)
    assert not result["can_collect"]
    assert result["effective_limit_bytes"] == 6_556_700_000


@pytest.mark.parametrize("limit", [0, 399, 401])
def test_invalid_previous_cap_is_rejected(limit):
    summary, selections = corpus()
    with pytest.raises(ValueError, match="previous point limit"):
        budget.plan(summary, selections, 3_000_000_000, previous_point_limit=limit)
