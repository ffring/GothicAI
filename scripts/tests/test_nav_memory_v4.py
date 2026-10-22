"""Contracts for frozen, eval-only memory comparisons and resident selection."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("nav_memory_v4", Path(__file__).resolve().parents[1] / "nav_memory_v4.py")
measurement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measurement)


def test_resident_selection_uses_start_routines_and_ignores_goal():
    actors = [dict(instance="LOCAL_A", routines=[dict(node=0)]),
              dict(instance="LOCAL_B", routines=[dict(node=1)])]
    nodes = [["A", 0, 0, 0], ["B", 10000, 0, 0]]
    case = dict(start="cm:100,0,0", goal="cm:10000,0,0")
    first = measurement.local_actor(case, actors, nodes, {})
    assert first["npc"] == "LOCAL_A"
    assert first["nearest_routine_to_start_cm"] == 100
    assert measurement.local_actor(dict(case, goal="secret-goal"), actors, nodes, {}) == first
    assert measurement.local_actor(dict(case, start="START"), actors, nodes, {"START": [100, 0, 0]}) == first


def test_same_actor_and_teacher_are_used_without_recording_or_oracle(tmp_path):
    case = dict(goal_kind="point", start="START", goal="GOAL")
    for mode in measurement.MODES:
        commands = measurement.command_sequence(tmp_path, case, "REGIONAL_LOCAL", mode)
        assert "navdemos record off" in commands and "navdemos oracle off" in commands
        assert commands[-2:] == ["navdemos start-v3 REGIONAL_LOCAL START GOAL 240000", "navdemos wait"]
        assert (f"roads {'stranger' if mode == 'familiar-stranger' else 'on'} REGIONAL_LOCAL") in commands
        assert any("roads familiar-load" in command for command in commands) == mode.startswith("familiar-")


def test_environment_selects_baseline_without_familiarity():
    measurement.mode_environment("familiar-local")
    assert measurement.os.environ["OPENGOTHIC_NAV_FAMILIAR_MAP"] == "1"
    measurement.mode_environment("baseline")
    assert measurement.os.environ["OPENGOTHIC_NAV_JOURNEY_MEMORY"] == "0"
    assert measurement.os.environ["OPENGOTHIC_NAV_FAMILIAR_MAP"] == "0"


def test_training_recordings_and_wrong_representation_are_rejected(tmp_path):
    case = dict(start="START", goal="GOAL", goal_kind="point")
    metadata = dict(case, teacher_version=3, demo_version=2, navigation_memory_version=4,
        journey_memory=True, limit_ms=240000, perception_mode="gaze", oracle=False,
        observation_source="learner_memory", record=False, demonstration_recorded=False)
    measurement.validate_raw(tmp_path, metadata, case, "journey")
    for changes in (dict(record=True), dict(demonstration_recorded=True), dict(oracle=True),
                    dict(navigation_memory_version=3), dict(journey_memory=False)):
        with pytest.raises(ValueError, match="identity or no-recording"):
            measurement.validate_raw(tmp_path, dict(metadata, **changes), case, "journey")
    (tmp_path / "samples.bin.gz").write_bytes(b"forbidden")
    with pytest.raises(ValueError, match="identity or no-recording"):
        measurement.validate_raw(tmp_path, metadata, case, "journey")
