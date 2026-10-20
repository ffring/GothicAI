"""Safety controls require physical CV evidence, independent of arrival."""
import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("nav_perception_safety", Path(__file__).resolve().parents[1] / "nav_perception_safety.py")
safety = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(safety)


def frame(time, height=100, **updates):
    value = dict(elapsed_ms=time, position_cm=[0, height, 0], cv_active=True, supported=True,
                 in_air=False, falling=False, native_jump=False, native_climb=False, swimming=False,
                 water_depth_cm=0)
    value.update(updates)
    return value


def assess(frames, **metadata):
    fixture = dict(label="cliff-bridge", kind="cliff", link_id=123, hazard_cm=[0, 0, 200], start_to_hazard_cm=200)
    result = dict(success=False, elapsed_ms=1000, failure="timeout")
    result.update(metadata)
    return safety.assess(fixture, result, frames)


def test_stop_before_hazard_is_safe_without_arrival():
    result = assess([frame(0), frame(1000)])
    assert result["safe"] and not result["goal_success"]


def test_successful_arrival_does_not_excuse_falling():
    result = assess([frame(0), frame(100, 80, supported=False, falling=True, cv_active=False), frame(200, 10)], success=True)
    assert not result["safe"]
    assert result["max_unsupported_descent_cm"] == 90


def test_gradual_grounded_downhill_does_not_accumulate_as_cliff():
    result = assess([frame(i * 100, 1000 - i * 20) for i in range(40)])
    assert result["safe"]
    assert result["max_unsupported_descent_cm"] == 20


def test_entering_knee_water_fails_before_swimming():
    result = assess([frame(0), frame(100, water_depth_cm=30)])
    assert not result["safe"]
    assert result["entered_water_frames"] == 1
    assert result["swimming_frames"] == 0


@pytest.mark.parametrize("frames,match", [([frame(0)], "Missing"),
    ([frame(100), frame(100)], "nonmonotonic"),
    ([frame(0, cv_active=False), frame(100, cv_active=False)], "supported CV"),
    ([frame(0), frame(100, water_depth_cm=float("nan"))], "water depth")])
def test_incomplete_evidence_cannot_pass(frames, match):
    with pytest.raises(ValueError, match=match):
        assess(frames)


def test_each_hazard_kind_needs_three_qualified_fixtures():
    fixtures = [dict(label=f"{kind}-{i}", kind=kind, qualified=True, reason="ok")
                for kind in safety.KINDS for i in range(3)]
    assert len(safety.select_fixtures(fixtures)) == 9
    fixtures[-1]["qualified"] = False
    fixtures[-1]["reason"] = "no_real_height_drop"
    with pytest.raises(ValueError, match="ledge: qualified 2/3"):
        safety.select_fixtures(fixtures)
