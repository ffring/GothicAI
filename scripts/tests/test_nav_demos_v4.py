"""Asset-free checks for moving validation; never launch a game or write NAVDEMO."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


SPEC = importlib.util.spec_from_file_location("nav_demos_v4", Path(__file__).resolve().parents[1] / "nav_demos_v4.py")
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)
v3 = validation.v3


def scenario(kind="follow", split="validation", index=0):
    return dict(id=f"{kind}-{split}-{index}", goal_kind=kind, split=split, region=f"region-{split}-{index}",
        start=f"S{index}", goal=f"G{index}", target="TARGET", route=[f"G{index}", f"T{index}", f"R{index}"], seed=42 + index)


def args_for(task):
    return validation.runner_args(SimpleNamespace(task=task, limit_ms=240000), 0)


def write_witness(task, args, value, teacher, worker="0"):
    path = validation.output_for(task) / "workers" / worker / "scenarios" / f"{validation.scenario_name(value, teacher)}.bench"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([f"seed {value['seed']}", "cheat god", "set time 10 0", "navdemos physics cv",
        *validation.command_sequence(args, value, teacher), "label done", ""]))
    return path


def write_episode(directory, value, teacher="v3"):
    directory.mkdir(parents=True, exist_ok=True)
    metadata = dict(start=value["start"], goal="RUNTIME_WAYPOINT", goal_kind=value["goal_kind"], seed=value["seed"],
        teacher_version=3 if teacher == "v3" else 2, demo_version=2, navigation_memory_version=3,
        perception_mode="gaze", physics_mode="cv", success=True, limit_ms=240000, elapsed_ms=150, decisions=2,
        record=False, demonstration_recorded=False, oracle=False,
        observation_source="learner_memory" if teacher == "v3" else "detour_teacher_v2",
        failure="", blocked_spans=0, blocked_ms=0, max_blocked_ms=0, final_dead=False, final_down=False)
    if value["goal_kind"] == "approach":
        metadata.update(target_immortal=True, target_nonhostile=True, target_final_dead=False, target_final_down=False)
    v3.write_json(directory / "episode.json", metadata)
    actions = np.zeros((3, 9))
    actions[:, 0] = [0, 100, 150]
    actions[:, 4] = 1
    actions[:-1, 1] = 1
    np.savetxt(directory / "actions.txt", actions)
    trajectory = np.zeros((3, 5))
    trajectory[:, 0] = actions[:, 0]
    np.savetxt(directory / "trajectory.txt", trajectory)
    target = np.zeros((3, 6))
    target[:, 0] = actions[:, 0]
    target[:, 4] = [900, 400, 300]
    np.savetxt(directory / "target-trajectory.txt", target)
    (directory / "teacher-paths.jsonl").write_text("{}\n")
    return metadata


def test_validation_selection_excludes_train_test_and_static_cases(tmp_path):
    values = [scenario(kind, split) for split in ("train", "validation", "test") for kind in validation.KINDS]
    values.append(dict(id="point", goal_kind="point", split="validation"))
    source = tmp_path / "input/moving-scenarios.jsonl"
    v3.write_selection(source, values)
    before = source.read_bytes()
    selected_source, selected = validation.validation_cases(tmp_path)
    assert selected_source == source
    assert selected == [value for value in values if value.get("goal_kind") in validation.KINDS and value["split"] == "validation"]
    assert source.read_bytes() == before
    assert len(selected) == 2
    values[0]["region"] = values[2]["region"]
    source.unlink()
    v3.write_selection(source, values)
    with pytest.raises(ValueError, match="Split overlap"):
        validation.validation_cases(tmp_path)


@pytest.mark.parametrize("teacher,version", [("v3", 3), ("v2", 2)])
def test_validation_commands_explicitly_disable_recording_and_oracle(tmp_path, teacher, version):
    value = scenario()
    commands = validation.command_sequence(args_for(tmp_path), value, teacher)
    assert commands[2:5] == ["navdemos record off", "navdemos perception-mode gaze", "navdemos oracle off"]
    assert commands[-2] == f"navdemos moving-v3 {version} BAU_954_MALETH S0 TARGET G0,T0,R0 follow 240000 42"
    for split in ("train", "test"):
        with pytest.raises(ValueError, match="Only v3/v2 moving validation"):
            validation.command_sequence(args_for(tmp_path), scenario(split=split), teacher)
    assert args_for(tmp_path).assets == Path.home() / "gothic-ai/game"


def measured(kind, teacher, index=0, success=True, observed=100, in_band=100, contacted=True):
    return dict(scenario(kind, index=index), mode=teacher, success=success, failure="" if success else "path_timeout",
        first_contact_ms=100 if contacted else None, follow_observed_ms=observed,
        follow_in_band_ms=in_band, follow_fraction=in_band / observed if observed else None)


def threshold_episodes():
    episodes = []
    for teacher in validation.TEACHERS:
        episodes += [measured("follow", teacher), measured("follow", teacher, 1, observed=900, in_band=700)]
        episodes += [measured("approach", teacher, index, success=index < 4) for index in range(5)]
    return episodes


def test_follow_uses_total_time_after_contact_and_both_gates_include_80pct_boundary():
    result = validation.summarize_metrics(threshold_episodes())
    follow = result["by_kind"]["follow"]["v3"]
    assert follow["follow_fraction"] == .8  # unweighted per-case mean would be 8/9.
    assert follow["follow_observed_ms"] == 1000 and follow["follow_in_band_ms"] == 800
    assert result["by_kind"]["approach"]["v3"]["success_fraction"] == .8
    assert result["passed"] is True


def test_follow_retains_both_too_close_and_lost_contact_time_in_denominator():
    result = v3.follow_metrics([0, 100, 200, 300, 350], [900, 400, 100, 700, 400], 350)
    assert result == dict(first_contact_ms=100, follow_observed_ms=250, follow_in_band_ms=100, follow_fraction=.4)


def test_v2_success_rates_are_comparison_only_but_approach_target_deaths_are_never_ignored():
    episodes = threshold_episodes()
    for value in episodes:
        if value["mode"] == "v2":
            value["success"] = False
    assert validation.summarize_metrics(episodes)["passed"] is True
    episodes[-1]["target_final_dead"] = True
    result = validation.summarize_metrics(episodes)
    assert result["passed"] is False
    assert result["gates"]["approach_v2_no_target_death_or_down"] is False


@pytest.mark.parametrize("failure", ["lost_contact", "terminal_contact", "below_follow", "below_approach"])
def test_validation_gates_fail_for_missing_contact_or_below_threshold(failure):
    episodes = threshold_episodes()
    if failure == "lost_contact":
        episodes[0].update(first_contact_ms=None, follow_observed_ms=0, follow_in_band_ms=0)
    elif failure == "terminal_contact":
        episodes[0].update(follow_observed_ms=0, follow_in_band_ms=0)
    elif failure == "below_follow":
        episodes[0]["follow_in_band_ms"] -= 1
    else:
        next(value for value in episodes if value["goal_kind"] == "approach")["success"] = False
    assert validation.summarize_metrics(episodes)["passed"] is False


@pytest.mark.parametrize("teacher", validation.TEACHERS)
def test_raw_resume_uses_seeded_witness_then_preserves_complete_trial(tmp_path, monkeypatch, teacher):
    value, args, identity = scenario(), args_for(tmp_path), dict(limit_ms=240000, navigation_memory_version=3)
    directory = validation.directory_for(tmp_path, value, teacher)
    write_episode(directory, value, teacher)
    write_witness(tmp_path, args, value, teacher, worker="2")
    worker = validation.Worker(args, identity)
    monkeypatch.setattr(worker, "scenario", lambda *args: pytest.fail("Completed validation must not run again"))
    result = worker.trial(tmp_path, value, teacher)
    assert result["follow_fraction"] == 1 and result["first_contact_ms"] == 100
    enriched = v3.read_json(directory / "episode.json")
    assert enriched["goal"] == value["goal"] and enriched["runner_goal"] == "RUNTIME_WAYPOINT"
    assert enriched["seed"] == 42 and enriched["moving_validation_identity"] == identity
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir()}
    assert worker.trial(tmp_path, value, teacher) == result
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir()}
    (directory / "samples.bin.gz").write_bytes(b"forbidden; must be preserved")
    with pytest.raises(ValueError, match="forbidden NAVDEMO"):
        worker.trial(tmp_path, value, teacher)
    assert (directory / "samples.bin.gz").read_bytes() == b"forbidden; must be preserved"


@pytest.mark.parametrize("field,bad", [("navigation_memory_version", 2), ("seed", 43), ("teacher_version", 2),
    ("record", True), ("oracle", True), ("limit_ms", 2000), ("physics_mode", "native"), ("perception_mode", "rows")])
def test_resume_rejects_changed_native_identity(tmp_path, field, bad):
    value = scenario()
    directory = validation.directory_for(tmp_path, value, "v3")
    metadata = write_episode(directory, value)
    metadata[field] = bad
    with pytest.raises(ValueError, match="identity, memory or recording mode"):
        validation.validate_metadata(directory, metadata, value, "v3", dict(limit_ms=240000))


@pytest.mark.parametrize("field,bad", [("target_immortal", False), ("target_nonhostile", False),
    ("target_final_dead", None), ("target_final_down", None)])
def test_approach_resume_requires_safe_fixture_and_explicit_death_evidence(tmp_path, field, bad):
    value = scenario("approach")
    directory = validation.directory_for(tmp_path, value, "v3")
    metadata = write_episode(directory, value)
    metadata[field] = bad
    with pytest.raises(ValueError, match="immortal/nonhostile or lacks death evidence"):
        validation.validate_metadata(directory, metadata, value, "v3", dict(limit_ms=240000))


def test_resume_rejects_changed_route_identity_and_every_conflicting_witness(tmp_path, monkeypatch):
    value, args, identity = scenario(), args_for(tmp_path), dict(limit_ms=240000)
    directory = validation.directory_for(tmp_path, value, "v3")
    write_episode(directory, value)
    write_witness(tmp_path, args, value, "v3")
    worker = validation.Worker(args, identity)
    monkeypatch.setattr(worker, "scenario", lambda *args: pytest.fail("Must resume"))
    worker.trial(tmp_path, value, "v3")
    altered = deepcopy(value)
    altered["route"][1] = "OTHER"
    metadata = v3.read_json(directory / "episode.json")
    with pytest.raises(ValueError, match="different frozen trial"):
        validation.validate_metadata(directory, metadata, altered, "v3", identity)
    witness = write_witness(tmp_path, args, value, "v3", worker="1")
    witness.write_text(witness.read_text().replace("seed 42", "seed 43"))
    with pytest.raises(ValueError, match="different saved moving validation command"):
        worker.trial(tmp_path, value, "v3")


def test_target_trace_must_align_with_actions_and_death_is_reported(tmp_path):
    value = scenario()
    metadata = write_episode(tmp_path, value)
    metadata.update(failure="target_down", success=False)
    v3.write_json(tmp_path / "episode.json", metadata)
    assert validation.text_metrics(tmp_path)["target_down"] is True
    trace = np.loadtxt(tmp_path / "target-trajectory.txt")
    trace[1, 0] = 101
    np.savetxt(tmp_path / "target-trajectory.txt", trace)
    with pytest.raises(ValueError, match="does not align"):
        validation.text_metrics(tmp_path)


def test_run_refuses_an_existing_output_with_changed_settings(tmp_path, monkeypatch):
    values = [scenario(kind) for kind in validation.KINDS]
    v3.write_selection(tmp_path / "input/moving-scenarios.jsonl", values)
    monkeypatch.setattr(validation, "settings", lambda *args: dict(navigation_memory_version=3))
    v3.write_json(validation.output_for(tmp_path) / "state.json", dict(settings=dict(navigation_memory_version=2)))
    with pytest.raises(ValueError, match="another binary, input, seed, memory or time limit"):
        validation.run(SimpleNamespace(task=tmp_path, parallel=3, limit_ms=240000))
