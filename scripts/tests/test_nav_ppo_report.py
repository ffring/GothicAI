"""Final navigation comparisons require the complete regional test coverage."""
import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("nav_ppo_report", Path(__file__).resolve().parents[1] / "nav_ppo_report.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def measured_pairs():
    return {(f"{region}-S{index}", f"{region}-G{index}"): dict(region=region)
            for region in report.REGIONS for index in range(10)}


def test_final_comparison_accepts_sixty_pairs_with_ten_per_region():
    baseline = measured_pairs()
    final = {key: dict(episode) for key, episode in baseline.items()}
    assert report.matched_episodes(baseline, final) == sorted(baseline, key=lambda key: (baseline[key]["region"], *key))


def test_final_comparison_rejects_identically_incomplete_fifty_nine_pairs():
    baseline = measured_pairs()
    baseline.pop(next(iter(baseline)))
    final = {key: dict(episode) for key, episode in baseline.items()}
    with pytest.raises(ValueError, match="exactly 60 pairs"):
        report.matched_episodes(baseline, final)


def test_final_comparison_rejects_sixty_pairs_with_wrong_regional_distribution():
    baseline = measured_pairs()
    baseline[("harbour-S0", "harbour-G0")]["region"] = "market"
    final = {key: dict(episode) for key, episode in baseline.items()}
    assert len(baseline) == 60
    with pytest.raises(ValueError, match="ten in each"):
        report.matched_episodes(baseline, final)
