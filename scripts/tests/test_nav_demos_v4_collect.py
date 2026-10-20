"""Asset-free selection, storage, eligibility and resume checks for demos-v4."""
from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("nav_demos_v4_collect", Path(__file__).resolve().parents[1] / "nav_demos_v4_collect.py")
collect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect)
v3 = collect.v3


def point(index=0, region="market", split="train"):
    return dict(id=f"point-{index}", goal_kind="point", split=split, region=region,
                start=f"S{index}", goal=f"G{index}", seed=collect.SEED)


def moving(index=0, region="market", split="train", kind="follow"):
    return dict(point(index, region, split), id=f"{kind}-{index}", goal_kind=kind,
                route=[f"G{index}", f"S{index}", f"T{index}"], target="TARGET")


def object_value(index=0, place="place", split="train"):
    return dict(point(index, split=split), id=f"object-{index}", goal_kind="object", place_id=place)


def probe(value, **changes):
    result = dict(label=value["id"], start=value["start"], goal=value["goal"],
                  visible=True, visible_centre=True, visible_edge=True, start_supported=True,
                  detour_complete=True, direct_cm=1000, detour_length_cm=2000)
    result.update(changes)
    return result


def test_selects_exactly_400_unique_training_pairs_evenly_and_is_deterministic():
    training = [dict(region=f"r{region}", start=f"S{region}-{index}", goal=f"G{region}-{index}", index=region*100+index)
                for region in range(6) for index in range(100)]
    excluded = {(value["goal"], value["start"]) for value in training[::11]}
    result = collect.select_points(training, excluded)
    assert len(result) == len({value["id"] for value in result}) == 400
    assert sorted(Counter(value["region"] for value in result).values()) == [66, 66, 67, 67, 67, 67]
    assert all(value["split"] == "train" and v3.isolated(value, excluded) for value in result)
    assert collect.select_points(list(reversed(training)), excluded) == result


def test_insufficient_balanced_region_cannot_hide_behind_large_other_regions():
    training = [dict(region=f"r{region}", start=f"S{region}-{index}", goal=f"G{region}-{index}")
                for region in range(6) for index in range(20 if region == 0 else 100)]
    with pytest.raises(ValueError, match="balanced"):
        collect.select_points(training, set())


def test_objects_require_train_split_matching_visible_qualified_probe_and_balance_places():
    values = [object_value(index, f"place-{index % 5}") for index in range(110)]
    probes = [probe(value) for value in values]
    bad = object_value(1000, "bad")
    hidden = object_value(1001, "hidden")
    heldout = object_value(1002, "heldout", "test")
    result = collect.select_objects(values + [bad, hidden, heldout],
        probes + [probe(bad, start_supported=False), probe(hidden, visible=False, visible_centre=False, visible_edge=False), probe(heldout)], set())
    assert len(result) == 110 and {value["id"] for value in result} == {value["id"] for value in values}
    assert len({value["place_id"] for value in result[:5]}) == 5
    changed = deepcopy(probes)
    changed[0]["start"] = "OTHER"
    with pytest.raises(ValueError, match="route differs"):
        collect.select_objects(values, changed, set())
    with pytest.raises(ValueError, match="at least 100"):
        collect.select_objects(values[:99], probes, set())


def test_object_place_split_overlap_rejected_before_recording():
    with pytest.raises(ValueError, match="Split overlap"):
        collect.select_objects([object_value(0), object_value(1, split="validation")], [], set())


def test_moving_generation_preserves_all_frozen_membership_and_uses_original_region_splits(monkeypatch):
    old = [moving(0), moving(1, "onar", "validation"), moving(2, "lobart", "test")]
    generated = [moving(3, "onar", "train"), moving(4, "harbour", "test")]
    monkeypatch.setattr(v3, "build_candidates", lambda *args, **kwargs: generated)
    before = deepcopy(old)
    result = collect.select_moving([], [], set(), old)
    assert old == before and all(value in result for value in old)
    assert next(value for value in result if value["id"] == "follow-3")["split"] == "validation"
    assert next(value for value in result if value["id"] == "follow-4")["split"] == "train"
    old[0]["split"] = "validation"
    with pytest.raises(ValueError, match="Frozen moving split"):
        collect.select_moving([], [], set(), old)


def test_moving_route_id_collision_preserves_exact_frozen_seed_and_target(monkeypatch):
    old = [moving()]
    before = deepcopy(old)
    changed = dict(old[0], target="OTHER", seed=old[0]["seed"] + 1)
    monkeypatch.setattr(v3, "build_candidates", lambda *args, **kwargs: [changed])
    assert collect.select_moving([], [], set(), old) == before
    assert old == before


@pytest.mark.parametrize("changes", [dict(route=["G0", "S0", "OTHER"]), dict(start="OTHER"),
    dict(goal="OTHER"), dict(goal_kind="approach"), dict(region="harbour")])
def test_moving_generation_cannot_change_frozen_core_route_or_membership(monkeypatch, changes):
    old = [moving()]
    changed = dict(old[0], **changes)
    monkeypatch.setattr(v3, "build_candidates", lambda *args, **kwargs: [changed])
    with pytest.raises(ValueError, match="frozen moving membership"):
        collect.select_moving([], [], set(), old)


def gate(task, **changes):
    runner = task / "build/opengothic/gothic-body-runner"
    runner.parent.mkdir(parents=True, exist_ok=True)
    if not runner.exists():
        runner.write_bytes(b"current-runner")
    value = dict(complete=True, collection=dict(follow=True),
        settings=dict(runner_sha256=collect.campaign.binary_digest(runner)), by_kind=dict(follow=dict(v3=dict(
        episodes=10, contact_episodes=10, observed_episodes=10, full_horizon_episodes=10,
        nonterminal_episodes=10, follow_fraction=.8))))
    value["by_kind"]["follow"]["v3"].update(changes)
    collect.atomic_json(task / "validation/attempt-2/metrics.json", value)
    return value


def test_follow_auto_uses_complete_ten_episode_second_validation_and_can_explicitly_exclude(tmp_path):
    assert collect.follow_enabled(tmp_path, "no") is False
    gate(tmp_path)
    assert collect.follow_enabled(tmp_path, "auto") is True
    assert collect.follow_enabled(tmp_path, "yes") is True
    gate(tmp_path, episodes=3)
    assert collect.follow_enabled(tmp_path, "auto") is False
    with pytest.raises(ValueError, match="validation gate"):
        collect.follow_enabled(tmp_path, "yes")
    value = gate(tmp_path)
    value["collection"]["follow"] = False
    collect.atomic_json(tmp_path / "validation/attempt-2/metrics.json", value)
    assert collect.follow_enabled(tmp_path, "auto") is False


@pytest.mark.parametrize("field", ["full_horizon_episodes", "nonterminal_episodes"])
def test_follow_gate_requires_every_case_to_run_full_horizon_and_remain_alive(tmp_path, field):
    gate(tmp_path, **{field: 9})
    assert collect.follow_enabled(tmp_path, "auto") is False
    with pytest.raises(ValueError, match="validation gate"):
        collect.follow_enabled(tmp_path, "yes")


def test_follow_gate_rejects_stale_validation_for_another_runner(tmp_path):
    gate(tmp_path)
    (tmp_path / "build/opengothic/gothic-body-runner").write_bytes(b"changed-runner")
    assert collect.follow_enabled(tmp_path, "auto") is False
    with pytest.raises(ValueError, match="validation gate"):
        collect.follow_enabled(tmp_path, "yes")


def test_collection_identity_freezes_second_validation_result_hash(tmp_path, monkeypatch):
    result = gate(tmp_path)
    monkeypatch.setattr(collect.campaign, "binary_digest", lambda path: Path(path).read_bytes().hex()
                        if Path(path).is_file() else "other-input")
    options = SimpleNamespace(task=tmp_path, limit_ms=240000, max_bytes=collect.v3.MAX_BYTES)
    identity = collect.settings(options, True)
    result["collection"]["follow"] = False
    collect.atomic_json(tmp_path / "validation/attempt-2/metrics.json", result)
    assert collect.settings(options, True)["validation_attempt_2_sha256"] != identity["validation_attempt_2_sha256"]


def legacy_identity():
    return dict(schema_version=1, navigation_memory_version=3, demo_version=2, perception_mode="gaze", physics="cv",
        validation_attempt_2_sha256="validation-2-hash", input_sha256={name: f"hash-{name}" for name in collect.INPUTS},
        runner_sha256="runner-hash", contract_sha256="contract-hash", limit_ms=240000,
        include_follow=False, seed=collect.SEED, max_bytes=15_000_000_000)


def test_follow_override_is_additive_opt_in_and_preserves_original_recording_provenance():
    base = legacy_identity()
    before = deepcopy(base)
    assert collect.collection_settings(base, base) == base
    upgraded = collect.collection_settings(base, base, follow_below_gate=True, source_follow_sha256="source-follow-hash")
    assert base == before
    assert upgraded["schema_version"] == 2
    assert upgraded["base_recording_identity"] == before
    policy = upgraded["family_policies"]["follow_below_gate"]
    assert policy["follow_below_gate"] is True and policy["minimum_follow_fraction"] == .7
    assert policy["source_selection_sha256"] == "source-follow-hash"
    assert policy["source_validation_attempt_2_sha256"] == before["validation_attempt_2_sha256"]
    for value in (point(), object_value(), moving(kind="approach"), dict(point(), dataset_family="jump")):
        assert collect.recording_identity(upgraded, value) == before
    tagged = dict(moving(), dataset_family="follow_below_gate", follow_below_gate=True, minimum_follow_fraction=.7)
    new_identity = collect.recording_identity(upgraded, tagged)
    assert new_identity != before and new_identity["schema_version"] == 2
    assert new_identity["dataset_family"] == "follow_below_gate"
    assert new_identity["family_policy"] == policy
    assert new_identity["minimum_follow_fraction"] == .7 and new_identity["follow_below_gate"] is True
    assert collect.collection_settings(base, upgraded, follow_below_gate=True,
        source_follow_sha256="source-follow-hash") == upgraded
    with pytest.raises(ValueError):
        collect.collection_settings(base, upgraded)
    with pytest.raises(ValueError):
        collect.collection_settings(base, upgraded, follow_below_gate=True, source_follow_sha256="changed-follow-hash")


@pytest.mark.parametrize("field,changed", [
    ("runner_sha256", "other-runner"), ("contract_sha256", "other-contract"),
    ("validation_attempt_2_sha256", "other-validation"), ("input_sha256", {"training-pairs.jsonl": "changed"}),
    ("seed", 20), ("limit_ms", 239900), ("max_bytes", 15_000_000_001),
    ("navigation_memory_version", 2), ("demo_version", 1), ("perception_mode", "grid"),
    ("physics", "native"), ("include_follow", True),
])
def test_follow_override_cannot_migrate_changed_core_or_mutate_existing_identity(tmp_path, field, changed):
    previous = legacy_identity()
    state = tmp_path / "collection-state.json"
    collect.atomic_json(state, dict(settings=previous, status="bounded_phase_complete"))
    before = state.read_bytes(), state.stat().st_mtime_ns, deepcopy(previous)
    requested = deepcopy(previous)
    requested[field] = changed
    with pytest.raises(ValueError):
        collect.collection_settings(requested, previous, follow_below_gate=True, source_follow_sha256="source-follow-hash")
    assert (state.read_bytes(), state.stat().st_mtime_ns, previous) == before


def test_follow_override_rejects_policy_drift_on_subsequent_resume():
    base = legacy_identity()
    upgraded = collect.collection_settings(base, base, follow_below_gate=True, source_follow_sha256="source-follow-hash")
    tampered = deepcopy(upgraded)
    tampered["family_policies"]["follow_below_gate"]["minimum_follow_fraction"] = .69
    with pytest.raises(ValueError):
        collect.collection_settings(base, tampered, follow_below_gate=True, source_follow_sha256="source-follow-hash")


def test_follow_override_copies_only_frozen_train_selection_without_rewriting_existing_files(tmp_path, monkeypatch):
    source = tmp_path / "input"
    source.mkdir()
    for name in ("training-pairs.jsonl", "object-candidates.jsonl", "object-probes.jsonl",
                 "moving-scenarios.jsonl", "holdout-cv.jsonl", "ppo-validation.jsonl"):
        (source / name).write_text("")
    collect.atomic_json(source / "places.json", dict(places=[]))
    moving_values = [moving(), moving(1, "onar", "validation"), moving(2, "lobart", "test"),
                     moving(3, kind="approach")]
    monkeypatch.setattr(collect, "select_points", lambda *args: [point()])
    monkeypatch.setattr(collect, "select_objects", lambda *args: [object_value()])
    monkeypatch.setattr(collect, "select_moving", lambda *args: moving_values)
    routes = [dict(point(index, split="train" if index < 168 else "validation"), index=index)
              for index in range(267)]
    monkeypatch.setattr(collect.jumps, "load_routes", lambda *args: routes)
    monkeypatch.setattr(collect.jumps, "collection_order", lambda values: values)
    original = collect.prepare_selections(tmp_path)
    frozen = {path: (path.read_bytes(), path.stat().st_mtime_ns)
              for path in (tmp_path / "recording-input").iterdir()}
    upgraded = collect.prepare_selections(tmp_path, follow_below_gate=True)
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == saved for path, saved in frozen.items())
    assert {key: upgraded[key] for key in original} == original
    assert upgraded["follow_below_gate"] == [dict(original["follow"][0], dataset_family="follow_below_gate",
        follow_below_gate=True, minimum_follow_fraction=.7)]
    value = upgraded["follow_below_gate"][0]
    assert value["goal_kind"] == "follow" and value["split"] == "train"
    assert collect.directory_for(tmp_path, value) == tmp_path / "recordings/follow_below_gate/train/follow-0"
    assert collect.directory_for(tmp_path, value) != collect.directory_for(tmp_path, original["follow"][0])
    again = {path: (path.read_bytes(), path.stat().st_mtime_ns)
             for path in (tmp_path / "recording-input").iterdir()}
    assert collect.prepare_selections(tmp_path, follow_below_gate=True) == upgraded
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == saved for path, saved in again.items())


@pytest.mark.parametrize("split", ["validation", "test"])
def test_no_heldout_commands_or_recording_paths(tmp_path, split):
    value = point(split=split)
    options = SimpleNamespace(task=tmp_path, limit_ms=240000)
    with pytest.raises(ValueError, match="forbidden"):
        collect.directory_for(tmp_path, value)
    with pytest.raises(ValueError, match="forbidden"):
        collect.commands(collect.runner_args(options, 0), value)
    tagged = dict(moving(split=split), dataset_family="follow_below_gate", follow_below_gate=True, minimum_follow_fraction=.7)
    with pytest.raises(ValueError, match="forbidden"):
        collect.directory_for(tmp_path, tagged)
    with pytest.raises(ValueError, match="forbidden"):
        collect.commands(collect.runner_args(options, 0), tagged)


def test_commands_enable_recording_memory_input_and_correct_teacher(tmp_path):
    args = collect.runner_args(SimpleNamespace(task=tmp_path, limit_ms=240000), 0)
    commands = collect.commands(args, point())
    assert commands[2:5] == ["navdemos record on", "navdemos perception-mode gaze", "navdemos oracle off"]
    assert "start-v3" in commands[-2]
    jump = dict(point(), dataset_family="jump", index=50)
    assert "start-v2" in collect.commands(args, jump)[-2]
    assert collect.version(jump) == 2 and collect.version(point()) == 3


def native(value):
    return dict(start=value["start"], goal=value["goal"], goal_kind=value["goal_kind"], seed=value["seed"],
                teacher_version=collect.version(value), demo_version=2, navigation_memory_version=3,
                perception_mode="gaze", physics_mode="cv", oracle=False, record=True, demonstration_recorded=True,
                observation_source="detour_teacher_v2" if collect.version(value) == 2 else "learner_memory",
                success=True, elapsed_ms=240000, limit_ms=240000, decisions=2, failure="")


@pytest.mark.parametrize("field,bad", [("navigation_memory_version", 2), ("teacher_version", 2),
    ("demo_version", 1), ("oracle", True), ("record", False), ("demonstration_recorded", False),
    ("perception_mode", "grid"), ("physics_mode", "native"), ("limit_ms", 100), ("start", "OTHER"),
    ("observation_source", "detour_raster"), ("drive", True)])
def test_wrong_native_identity_never_gets_relabelled_as_memory_v3(field, bad):
    value = point()
    metadata = native(value)
    metadata[field] = bad
    with pytest.raises(ValueError):
        collect.validate_native(metadata, value, dict(limit_ms=240000))


def test_follow_eligibility_needs_contact_full_horizon_and_eighty_percent():
    value = moving()
    metadata = native(value)
    measured = dict(first_contact_ms=100, follow_observed_ms=239900, follow_fraction=.8)
    assert collect.eligibility(metadata, measured)
    for changed in (dict(first_contact_ms=None), dict(follow_observed_ms=0), dict(follow_fraction=.799)):
        assert not collect.eligibility(metadata, dict(measured, **changed))
    assert not collect.eligibility(dict(metadata, elapsed_ms=239900), measured)
    assert not collect.eligibility(dict(metadata, success=False), measured)
    assert not collect.eligibility(dict(metadata, dataset_family="jump", transition_success=False), measured)
    assert collect.eligibility(dict(metadata, dataset_family="jump", success=False, transition_success=True), measured)


def below_gate_metadata(**changes):
    result = dict(native(moving()), dataset_family="follow_below_gate", follow_below_gate=True,
        minimum_follow_fraction=.7, success=False, teacher_goal_success=False, final_dead=False, final_down=False,
        target_final_dead=False, target_final_down=False)
    result.update(changes)
    return result


@pytest.mark.parametrize("fraction,expected", [(.7, True), (.699, False), (.75, True)])
def test_follow_below_gate_uses_explicit_seventy_percent_without_changing_native_success(fraction, expected):
    metadata = below_gate_metadata()
    measured = dict(first_contact_ms=100, follow_observed_ms=239900, follow_fraction=fraction)
    assert collect.eligibility(metadata, measured) is expected
    assert metadata["success"] is False
    assert not collect.eligibility(dict(metadata, dataset_family="follow", follow_below_gate=False), measured)


@pytest.mark.parametrize("changes,measured_changes", [
    (dict(elapsed_ms=239900), {}),
    ({}, dict(first_contact_ms=None)),
    ({}, dict(follow_observed_ms=0)),
    (dict(final_dead=True), {}),
    (dict(final_down=True), {}),
    (dict(target_final_dead=True), {}),
    (dict(target_final_down=True), {}),
    (dict(final_dead=None), {}),
    (dict(target_final_down=None), {}),
    (dict(follow_below_gate=False), {}),
    (dict(minimum_follow_fraction=.69), {}),
])
def test_follow_below_gate_rejects_missing_contact_horizon_survival_or_policy(changes, measured_changes):
    measured = dict(first_contact_ms=100, follow_observed_ms=239900, follow_fraction=.75)
    assert not collect.eligibility(below_gate_metadata(**changes), dict(measured, **measured_changes))


def test_follow_below_gate_occupancy_uses_physical_time_after_first_contact(tmp_path):
    # Four of five observed trace rows are in band, but only 69.9% of the
    # elapsed post-contact time is: decision counts cannot supply the label.
    metadata = below_gate_metadata(id="follow-0", split="train", decisions=5)
    (tmp_path / "target-trajectory.txt").write_text("\n".join([
        "0 0 0 0 600 0", "100000 0 0 0 300 0", "100100 0 0 0 300 0",
        "100200 0 0 0 300 0", "197860 0 0 0 600 0", "240000 0 0 0 300 0", ""]))
    measured = collect.measured_episode(tmp_path, metadata, dict(key_counts={}))
    assert measured["first_contact_ms"] == 100000
    assert measured["follow_observed_ms"] == 140000
    assert measured["follow_fraction"] == pytest.approx(.699)
    assert measured["training_eligible"] is False
    assert measured["success"] is False


def test_aggregate_storage_reserves_across_families_and_releases_after_failure(tmp_path, monkeypatch):
    budget = collect.StorageBudget(tmp_path, max_bytes=2 * 20480)
    monkeypatch.setattr(collect.shutil, "disk_usage", lambda root: SimpleNamespace(free=10**12))
    retained = tmp_path / "point/train/failed/samples.bin.gz"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"x" * 10)
    new_family = tmp_path / "follow_below_gate/train/follow-0/samples.bin.gz"
    new_family.parent.mkdir(parents=True)
    with budget.reserve(100):
        new_family.write_bytes(b"x" * 10)
        with pytest.raises(ValueError, match="Disk budget"):
            with budget.reserve(100):
                pytest.fail("Concurrent family exceeded aggregate budget")
        assert budget.reserved == 20480
    assert budget.reserved == 0
    with pytest.raises(RuntimeError):
        with budget.reserve(100):
            raise RuntimeError("retained partial attempt")
    assert budget.reserved == 0 and retained.read_bytes() == new_family.read_bytes() == b"x" * 10


def test_raw_completed_record_recovers_by_exact_witness_then_resumes_immutably(tmp_path, monkeypatch):
    options = SimpleNamespace(task=tmp_path, limit_ms=240000)
    identity = legacy_identity()
    value = point()
    directory = collect.directory_for(tmp_path, value)
    directory.mkdir(parents=True)
    for name in ("samples.bin.gz", "actions.txt", "trajectory.txt"):
        (directory / name).write_bytes(b"retained")
    (directory / "contract.json").write_text('{"records":2,"mismatches":0,"previous_mismatches":0}\n')
    collect.atomic_json(directory / "episode.json", native(value))
    worker = collect.Worker(options, 0, None, identity)
    args = worker.args
    witness = args.output / "scenarios" / "demos-v4-point-0.bench"
    witness.parent.mkdir(parents=True)
    witness.write_text("\n".join([f"seed {value['seed']}", "cheat god", "set time 10 0", "navdemos physics cv",
        *collect.commands(args, value), "label done", ""]))
    monkeypatch.setattr(worker, "scenario", lambda *args: pytest.fail("Completed recording must not run again"))
    monkeypatch.setattr(collect.demos, "summarize", lambda *args, **kwargs: dict(navigation_memory_versions=[3],
        format_versions=[2], key_counts=dict(back=1, strafe=1, jump=0, climb=0)))
    monkeypatch.setattr(collect, "replay_contract", lambda *args: dict(records=2, mismatches=0, previous_mismatches=0))
    measured = worker.trial(value)
    assert measured["training_eligible"] is True
    metadata = v3.read_json(directory / "episode.json")
    assert metadata["id"] == value["id"] and metadata["recording_identity"] == identity
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir()}
    assert worker.trial(value) == measured
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir()}
    upgraded = collect.collection_settings(identity, identity, follow_below_gate=True, source_follow_sha256="source-follow-hash")
    worker.identity = upgraded
    assert worker.trial(value) == measured
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir()}
    assert v3.read_json(directory / "episode.json")["recording_identity"] == identity
    metadata["seed"] += 1
    collect.atomic_json(directory / "episode.json", metadata)
    with pytest.raises(ValueError, match="another frozen scenario"):
        worker.trial(value)


def test_partial_attempt_is_retained_and_never_retried(tmp_path, monkeypatch):
    value = point()
    directory = collect.directory_for(tmp_path, value)
    directory.mkdir(parents=True)
    partial = directory / "samples.bin.gz"
    partial.write_bytes(b"unfinished")
    worker = collect.Worker(SimpleNamespace(task=tmp_path, limit_ms=240000), 0, None, dict(limit_ms=240000))
    monkeypatch.setattr(worker, "scenario", lambda *args: pytest.fail("Partial recording must not run again"))
    with pytest.raises(ValueError, match="Partial recording retained"):
        worker.trial(value)
    assert partial.read_bytes() == b"unfinished"


def test_contract_replay_does_not_rewrite_existing_contract(tmp_path, monkeypatch):
    result = dict(records=42, mismatches=0, previous_mismatches=0)
    monkeypatch.setattr(collect.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(result)))
    path = tmp_path / "contract.json"
    assert collect.replay_contract(Path("/contract"), tmp_path, 42) == result
    before = path.read_bytes(), path.stat().st_mtime_ns
    assert collect.replay_contract(Path("/contract"), tmp_path, 42) == result
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    with pytest.raises(ValueError, match="mismatch"):
        collect.replay_contract(Path("/contract"), tmp_path, 43)
