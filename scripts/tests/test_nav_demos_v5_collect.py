"""Asset-free memory-v4 collection, safety and shared-storage contracts."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location("nav_demos_v5_collect", Path(__file__).resolve().parents[1] / "nav_demos_v4_collect.py")
collect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect)


def scenario(index=0, **changes):
    return dict(dict(id=f"point-{index}", goal_kind="point", split="train", region="market",
        start="START", goal="GOAL", seed=collect.SEED), **changes)


def memberships():
    nodes = [["START", 0, 0, 0], ["FAR", 1000, 0, 0]]
    snapshots = [dict(kind="roads_snapshot", actor="OUTSIDER", stranger=False,
        known_node_ids=[1], routines=[dict(node=0)], spawn_cm=[0, 0, 0]),
        dict(kind="roads_snapshot", actor="RESIDENT", stranger=False,
        known_node_ids=[0, 1], routines=[dict(node=1)], spawn_cm=[1000, 0, 0])]
    return nodes, {"START": [0, 0, 0]}, snapshots


def test_residency_uses_native_membership_before_distance_and_preserves_a_stable_mix():
    values = [collect.resident_scenario(scenario(index), memberships()) for index in range(40)]
    assert {value["npc"] for value in values} == {"RESIDENT"}
    assert {value["local"] for value in values} == {False, True}
    assert all(value["knownroads_selection"]["nearest_start_node"] == 0 for value in values)
    assert [collect.resident_scenario(scenario(index), memberships()) for index in range(40)] == values
    assert scenario() == {key: values[0][key] for key in scenario()}


def test_start_unknown_to_every_native_actor_remains_a_stranger():
    nodes, positions, snapshots = memberships()
    snapshots[1]["known_node_ids"] = [1]
    result = collect.resident_scenario(scenario(), (nodes, positions, snapshots))
    assert result["npc"] == "OUTSIDER" and result["local"] is False


def test_moving_target_cannot_also_be_the_walking_actor():
    result = collect.resident_scenario(scenario(goal_kind="follow", target="RESIDENT"), memberships())
    assert result["npc"] == "OUTSIDER" and result["local"] is False


def test_jump_teacher2_uses_explicit_stranger_without_familiar_memory_attachment():
    for index in range(20):
        result = collect.resident_scenario(scenario(index, dataset_family="jump"), memberships())
        assert result["local"] is False and result["residency"] == "stranger"
        assert result["knownroads_selection"]["stranger_reason"] == "teacher v2 does not attach familiar memory"


@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize("start,goal", [("START", "GOAL"), ("cm:0,0,0", "GOAL"),
    ("START", "cm:1000,0,0"), ("cm:0,0,0", "cm:1000,0,0")])
def test_v5_commands_install_familiar_memory_keep_gaze_cv_and_select_native_residency(tmp_path, local, start, goal):
    options = SimpleNamespace(task=tmp_path, limit_ms=240000, corpus_version=5)
    args = collect.runner_args(options, 0)
    value = dict(collect.resident_scenario(scenario(start=start, goal=goal), memberships()), local=local)
    commands = collect.commands(args, value)
    assert f"roads familiar-load {tmp_path}/input/familiar.bin" in commands
    assert f"roads {'on' if local else 'stranger'} RESIDENT" in commands
    assert not any("cm:" in command for command in commands if command.startswith("roads "))
    assert "navdemos perception-monitor on" in commands
    assert "navdemos record on" in commands and "navdemos oracle off" in commands
    assert "navdemos perception-mode gaze" in commands
    assert commands[-2] == f"navdemos start-v3 RESIDENT {start} {goal} 240000"
    jump = dict(value, dataset_family="jump", index=0)
    assert "start-v2 RESIDENT" in collect.commands(args, jump)[-2]


def native(value):
    return dict(start=value["start"], goal=value["goal"], goal_kind="point", seed=0,
        teacher_version=3, demo_version=3, navigation_memory_version=4, journey_memory=True,
        familiar_map=value["local"], perception_mode="gaze", perception_monitor=True, physics_mode="cv",
        oracle=False, record=True, demonstration_recorded=True, observation_source="learner_memory",
        success=True, elapsed_ms=200, limit_ms=240000, decisions=2, failure="")


def test_v5_native_identity_requires_memory4_and_recording3_but_allows_point_seed_sentinel():
    value = collect.resident_scenario(scenario(), memberships())
    identity = dict(navigation_memory_version=4, demo_version=3, limit_ms=240000)
    collect.validate_native(native(value), value, identity)
    for changes in (dict(navigation_memory_version=3), dict(demo_version=2),
                    dict(journey_memory=False), dict(familiar_map=not value["local"]),
                    dict(perception_monitor=False)):
        with pytest.raises(ValueError):
            collect.validate_native(dict(native(value), **changes), value, identity)
    completed = dict(native(value), **{key: item for key, item in value.items() if key != "seed"},
                     command_seed=value["seed"], recording_identity=identity,
                     teacher_variant="v3b", teacher_familiar_hint=False)
    collect.validate_native(completed, value, identity)


def test_stranger_keeps_major_roads_but_cannot_have_familiar_terrain(tmp_path):
    value = dict(npc="STRANGER", local=False)
    known = dict(kind="roads_snapshot", tag="recording", actor="STRANGER", stranger=True,
                 known_node_ids=[2, 9, 10], known_edge_ids=[14, 15])
    familiar = dict(kind="familiar_stats", actor="STRANGER", stranger=True,
                    known_tiles=0, known_2m_tiles_xz=0, eligible_2m_tiles=0, familiar_source_poses=0)
    (tmp_path / "roads-snapshots.jsonl").write_text(json.dumps(known) + "\n")
    events = tmp_path / "roads-events.jsonl"
    events.write_text(json.dumps(familiar) + "\n")
    assert collect.native_residency(tmp_path, value) == dict(knownroads=known, familiar=familiar)
    events.write_text(json.dumps(dict(familiar, known_tiles=1)) + "\n")
    with pytest.raises(ValueError, match="Stranger unexpectedly knows terrain"):
        collect.native_residency(tmp_path, value)


def monitor(directory, depths=(0, 20, 99, 0)):
    frames = [dict(elapsed_ms=index * 100, cv_active=True, swimming=False,
        water_depth_cm=depth, deep_water_threshold_cm=100) for index, depth in enumerate(depths)]
    (directory / "perception-safety-frames.jsonl").write_text("".join(json.dumps(frame) + "\n" for frame in frames))
    return dict(elapsed_ms=(len(frames) - 1) * 100)


def test_water_eligibility_uses_passive_depth_and_swim_evidence_not_goal_success_alone(tmp_path):
    metadata = monitor(tmp_path)
    measured = collect.water_metrics(tmp_path, metadata)
    assert measured["entered_water_frames"] == 2 and measured["water_entries"] == 1
    assert measured["deep_water_frames"] == 1 and measured["deep_water_entries"] == 1
    water = dict(dataset_family="water", goal_kind="point", success=True)
    assert not collect.eligibility(water, measured)
    dry = collect.water_metrics(tmp_path, monitor(tmp_path, (0, 10, 0)))
    assert collect.eligibility(water, dry)
    assert not collect.eligibility(water, dict(dry, swimming_frames=1))
    assert not collect.eligibility(dict(water, success=False), dry)


def test_incomplete_physical_water_trace_is_rejected(tmp_path):
    metadata = monitor(tmp_path)
    with pytest.raises(ValueError, match="incomplete physical"):
        collect.water_metrics(tmp_path, dict(metadata, elapsed_ms=1000))


def test_archived_sample_hash_pins_and_metrics_survive_moving_without_symlinks(tmp_path):
    task, archive = tmp_path / "active", tmp_path / "shared"
    value = scenario()
    local = collect.directory_for(task, value)
    local.mkdir(parents=True)
    (local / "samples.bin.gz").write_bytes(b"unchanged sample payload")
    digest = collect.campaign.binary_digest(local / "samples.bin.gz")
    state_path = task / "recordings/collection-state.json"
    collect.atomic_json(task / "recordings/collection-metrics.json", dict(
        episodes=[dict(episode=str(local), samples_sha256=digest)]))
    state = collect.CollectionState(state_path, {}, archive_root=archive)
    state.pin_samples(local, digest)
    archived = collect.directory_for(archive, value)
    archived.parent.mkdir(parents=True)
    local.rename(archived)
    options = SimpleNamespace(task=task, archive_root=archive)
    assert collect.resolve_directory(options, value) == archived
    assert state.key(archived) == "point/train/point-0"
    assert state.saved_metrics(archived)["samples_sha256"] == digest
    assert state.verify_samples(archived, {}) == digest
    (archived / "samples.bin.gz").write_bytes(b"changed samples")
    with pytest.raises(ValueError, match="hash differs"):
        state.verify_samples(archived, {})


def test_duplicate_active_and_archived_attempts_are_not_silently_selected(tmp_path):
    options = SimpleNamespace(task=tmp_path / "active", archive_root=tmp_path / "shared")
    for task in (options.task, options.archive_root):
        collect.directory_for(task, scenario()).mkdir(parents=True)
    with pytest.raises(ValueError, match="both active and archived"):
        collect.resolve_directory(options, scenario())


def test_v5_budget_counts_shared_failures_and_retains_20gib_locally(tmp_path, monkeypatch):
    archive, local = tmp_path / "shared", tmp_path / "active"
    archive.mkdir()
    (archive / "failed-attempt").write_bytes(b"x" * 20)
    budget = collect.StorageBudget(local, max_bytes=100, archive_root=archive,
                                   min_free_bytes=collect.V5_MIN_FREE_BYTES)
    monkeypatch.setattr(collect.shutil, "disk_usage", lambda root: SimpleNamespace(free=collect.V5_MIN_FREE_BYTES + 100))
    assert budget.used() == 20
    budget.require_room(80)
    with pytest.raises(collect.StorageLimit, match="used=20"):
        budget.require_room(81)
    monkeypatch.setattr(collect.shutil, "disk_usage", lambda root: SimpleNamespace(free=collect.V5_MIN_FREE_BYTES))
    with pytest.raises(collect.StorageLimit):
        budget.require_room(1)
