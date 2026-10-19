"""Asset-free checks for the bounded three-runner teacher-v3 campaign."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location(
    "nav_teacher_v3_campaign", Path(__file__).resolve().parents[1] / "nav_teacher_v3_campaign.py")
campaign = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = campaign
SPEC.loader.exec_module(campaign)
v3 = campaign.v3

REGIONS = tuple(f"region-{index}" for index in range(6))
DECISION_BYTES = 20 * 1024


def point(region, index):
    return dict(id=f"point-{region}-{index}", goal_kind="point", split="train", region=region,
                start=f"START-{region}-{index}", goal=f"GOAL-{region}-{index}", seed=42)


def object_candidate(place, region, index=0):
    return dict(id=f"object-{place}-{index}", goal_kind="object", split="train", region=region,
                place_id=place, start=f"START-{place}-{index}", goal=f"GOAL-{place}", seed=42)


def accepted_selection():
    points = [point(region, index) for region in REGIONS for index in range(5)]
    objects = [object_candidate(f"place-{index}", REGIONS[index % 6]) for index in range(30)]
    return points + objects


def test_gate_requires_all_thirty_points_objects_places_and_six_balanced_regions():
    selected = accepted_selection()
    counts = campaign.gate(selected, REGIONS, object_regions=REGIONS)
    assert counts["points"] == counts["objects"] == counts["object_places"] == 30
    assert counts["point_regions"] == {region: 5 for region in REGIONS}

    for kind in ("point", "object"):
        deficient = list(selected)
        deficient.pop(next(index for index, value in enumerate(deficient) if value["goal_kind"] == kind))
        with pytest.raises(ValueError):
            campaign.gate(deficient, REGIONS, object_regions=REGIONS)


def test_gate_does_not_hide_a_minority_region_behind_the_total_count():
    selected = accepted_selection()
    selected.remove(point(REGIONS[-1], 4))
    selected.append(point(REGIONS[0], 5))
    assert sum(value["goal_kind"] == "point" for value in selected) == 30
    with pytest.raises(ValueError):
        campaign.gate(selected, REGIONS)


def test_gate_counts_distinct_places_and_requires_every_object_region():
    selected = accepted_selection()
    objects = [value for value in selected if value["goal_kind"] == "object"]
    objects[-1]["place_id"] = objects[-2]["place_id"]
    with pytest.raises(ValueError):
        campaign.gate(selected, REGIONS)

    selected = accepted_selection()
    for value in selected:
        if value["goal_kind"] == "object" and value["region"] == REGIONS[-1]:
            value["region"] = REGIONS[0]
    assert len({value["place_id"] for value in selected if value["goal_kind"] == "object"}) == 30
    with pytest.raises(ValueError):
        campaign.gate(selected, REGIONS, object_regions=REGIONS)


@pytest.mark.parametrize(("fraction", "expected"), [(None, False), (0, True), (.499, True), (.5, False), (.8, False), (1, False)])
def test_point_stop_uses_the_strict_half_success_boundary(fraction, expected):
    assert campaign.point_stop(dict(conditional_success_fraction=fraction)) is expected


def schedule(pool, qualified=(), attempted=(), **options):
    defaults = dict(parallel=3, min_points=30, min_objects=0, min_places=0,
                    per_region=5, regions=REGIONS, object_regions=())
    defaults.update(options)
    return campaign.PendingSchedule(pool, list(qualified), set(attempted), **defaults)


def test_pending_points_balance_regions_and_drain_at_most_two_extra_trials():
    # Deliberately group the pool by region: taking its first entries would
    # starve five regions and waste recordings after one region fills up.
    pool = [point(region, index) for region in REGIONS for index in range(10)]
    pending = schedule(pool)
    active = [pending.take() for _ in range(3)]
    assert all(value is not None for value in active)
    assert len({value["region"] for value in active}) == 3
    assert pending.inflight == {value["id"] for value in active}
    assert pending.take() is None
    attempted, accepted = set(), []
    while active:
        value = active.pop(0)
        assert value["id"] not in attempted
        attempted.add(value["id"])
        pending.finish(value, True)
        accepted.append(value)
        assert value["id"] not in pending.inflight
        replacement = pending.take()
        if replacement is not None:
            active.append(replacement)
        assert len(active) <= 3
        assert len(attempted) <= 32
    assert 30 <= len(accepted) <= 32
    assert all(Counter(value["region"] for value in accepted)[region] >= 5 for region in REGIONS)
    assert pending.take() is None
    assert not pending.inflight


def test_pending_stops_dispatch_after_quotas_fill_and_retains_inflight_results():
    qualified = [point(region, index) for region in REGIONS for index in range(5)]
    missing = qualified.pop()
    extra = point(REGIONS[-1], 8)
    pending = schedule([missing, extra], qualified)
    candidate = pending.take()
    assert candidate["id"] == missing["id"]
    inflight = pending.take()
    assert inflight["id"] == extra["id"]
    pending.finish(candidate, True)
    assert len(qualified) == 29  # scheduling must not mutate the caller's frozen selection.
    assert pending.take() is None
    assert pending.inflight == {extra["id"]}
    pending.finish(inflight, True)
    assert len(pending.qualified) == 31
    assert not pending.inflight


def test_pending_fills_region_deficit_even_when_total_points_are_already_thirty():
    qualified = [point(region, index) for region in REGIONS for index in range(5)]
    missing = qualified.pop()
    qualified.append(point(REGIONS[0], 5))
    pending = schedule([point(REGIONS[0], 6), missing], qualified)
    assert pending.take()["id"] == missing["id"]


def test_pending_objects_prefer_unrepresented_place_and_required_region():
    known = object_candidate("known", REGIONS[0])
    duplicate = object_candidate("known", REGIONS[0], 1)
    fresh = object_candidate("fresh", REGIONS[1])
    pending = schedule([duplicate, fresh], [known], min_points=0, min_objects=2, min_places=2,
                       parallel=1, regions=(), object_regions=REGIONS[:2])
    assert pending.take()["id"] == fresh["id"]
    assert pending.take() is None
    pending.finish(fresh, True)
    assert pending.take() is None


def test_pending_failed_qualification_reopens_the_deficit_and_skips_attempted_ids():
    skipped, failed, replacement = [point(REGIONS[0], index) for index in range(3)]
    pending = schedule([skipped, failed, replacement], attempted=[skipped["id"]],
                       parallel=1, min_points=1, per_region=1, regions=REGIONS[:1])
    first = pending.take()
    assert first["id"] == failed["id"]
    assert pending.take() is None
    pending.finish(first, False)
    assert not pending.inflight
    second = pending.take()
    assert second["id"] == replacement["id"]
    pending.finish(second, True)
    assert pending.take() is None


def test_pending_exhaustion_finishes_without_retrying_the_same_failed_candidate():
    value = point(REGIONS[0], 0)
    pending = schedule([value], min_points=1, per_region=1, regions=REGIONS[:1])
    assert pending.take()["id"] == value["id"]
    pending.finish(value, False)
    assert pending.take() is None
    assert not pending.inflight


def storage(tmp_path, monkeypatch, max_bytes):
    task, data = tmp_path / "task", tmp_path / "data"
    data.mkdir(parents=True)
    (task / "v2-attempts" / "failed").mkdir(parents=True)
    (data / "samples.bin.gz").write_bytes(b"12345")
    (task / "v2-attempts" / "failed" / "episode.json").write_bytes(b"1234567")
    # Builds, logs and unrelated task files are outside the recording budget.
    (task / "build.log").write_bytes(b"x" * 1000)
    monkeypatch.setattr(campaign.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3))
    return campaign.StorageBudget(task, data, max_bytes=max_bytes)


def test_storage_budget_aggregates_data_and_attempts_and_releases_reservations(tmp_path, monkeypatch):
    budget = storage(tmp_path, monkeypatch, 12 + DECISION_BYTES)
    for _ in range(2):
        with budget.reserve(100):
            with pytest.raises(ValueError, match="[Bb]udget"):
                with budget.reserve(100):
                    pytest.fail("two pending episodes exceeded the combined recording budget")
    with pytest.raises(RuntimeError, match="runner failed"):
        with budget.reserve(100):
            raise RuntimeError("runner failed")
    with budget.reserve(100):
        pass
    assert (tmp_path / "data" / "samples.bin.gz").read_bytes() == b"12345"
    assert (tmp_path / "task" / "v2-attempts" / "failed" / "episode.json").read_bytes() == b"1234567"
    assert (tmp_path / "task" / "build.log").stat().st_size == 1000


def test_storage_budget_rounds_up_partial_decisions_and_checks_free_space(tmp_path, monkeypatch):
    budget = storage(tmp_path, monkeypatch, 12 + 2 * DECISION_BYTES - 1)
    with pytest.raises(ValueError, match="[Bb]udget"):
        with budget.reserve(150):
            pytest.fail("a partial final decision was omitted from the reservation")
    monkeypatch.setattr(campaign.shutil, "disk_usage", lambda _: SimpleNamespace(free=DECISION_BYTES - 1))
    with pytest.raises(ValueError):
        with budget.reserve(100):
            pytest.fail("insufficient filesystem space was accepted")


def test_storage_reservations_are_atomic_between_parallel_workers(tmp_path, monkeypatch):
    budget = storage(tmp_path, monkeypatch, 12 + DECISION_BYTES)
    start = threading.Barrier(3)
    rejected, release = threading.Event(), threading.Event()

    def worker():
        start.wait(timeout=3)
        try:
            with budget.reserve(100):
                assert release.wait(timeout=3)
                return "accepted"
        except ValueError:
            rejected.set()
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        try:
            start.wait(timeout=3)
            assert rejected.wait(timeout=3), "concurrent workers both reserved the last available episode"
        finally:
            release.set()
        assert sorted(future.result(timeout=3) for future in futures) == ["accepted", "rejected"]
    with budget.reserve(100):
        pass


def write_metadata(directory, value, version=2, **extra):
    directory.mkdir(parents=True, exist_ok=True)
    metadata = dict(value, teacher_version=version, demo_version=2, **extra)
    (directory / "episode.json").write_text(json.dumps(metadata), encoding="utf-8")
    return metadata


def test_resume_requires_complete_samples_and_preserves_scenario_identity(tmp_path):
    directory = tmp_path / "episode"
    value = point(REGIONS[0], 0)
    assert campaign.resume_metadata(directory, value, 2) is None
    metadata = write_metadata(directory, value, success=True)
    assert campaign.resume_metadata(directory, value, 2) is None
    for name in ("samples.bin.gz", "actions.txt", "trajectory.txt"):
        (directory / name).write_bytes(b"recorded")
    assert campaign.resume_metadata(directory, value, 2) == metadata
    enriched = dict(value, qualification=dict(visible=True), beyond_path_fraction=.75,
                    teacher_v2_episode="/qualification/reference", teacher_v2_success=True)
    assert campaign.resume_metadata(directory, enriched, 2) == metadata
    with pytest.raises(ValueError):
        campaign.resume_metadata(directory, dict(value, seed=43), 2)
    with pytest.raises(ValueError):
        campaign.resume_metadata(directory, value, 3)


def test_resume_uses_cached_failed_attempt_after_samples_were_removed(tmp_path):
    directory = tmp_path / "failed-attempt"
    value = point(REGIONS[0], 0)
    metadata = write_metadata(directory, value, success=False, campaign_attempt_complete=True, beyond_path_fraction=0.0)
    assert campaign.resume_metadata(directory, value, 2, require_samples=False) == metadata
    assert campaign.resume_metadata(directory, value, 2, require_samples=True) is None
    metadata.pop("campaign_attempt_complete")
    (directory / "episode.json").write_text(json.dumps(metadata), encoding="utf-8")
    assert campaign.resume_metadata(directory, value, 2, require_samples=False) is None
    metadata["campaign_attempt_complete"] = True
    metadata.pop("beyond_path_fraction")
    (directory / "episode.json").write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="fraction"):
        campaign.resume_metadata(directory, value, 2, require_samples=False)


def metric(value, version):
    result = dict(id=value["id"], goal_kind=value["goal_kind"], teacher_version=version,
                  success=True, decisions=1000, elapsed_ms=100000, travelled_cm=1000,
                  length_over_detour=1, backward=1, strafe=1, contract_records=1000 if version == 3 else 0)
    if value["goal_kind"] == "follow":
        result.update(follow_observed_ms=1000, follow_in_band_ms=800, first_contact_ms=0)
    if value["goal_kind"] == "approach":
        result["approach_success"] = True
    return result


def complete_episode(directory, value, version):
    write_metadata(directory, value, version=version, success=True, decisions=1000)
    for name in ("samples.bin.gz", "actions.txt", "trajectory.txt"):
        (directory / name).write_bytes(b"recorded")
    if version == 3:
        (directory / "contract.json").write_text(json.dumps(dict(records=1000, mismatches=0, previous_mismatches=0)))


def measured_selection(tmp_path):
    selected = [point(REGIONS[0], 0), object_candidate("cave", REGIONS[1])]
    for kind in ("follow", "approach"):
        selected.append(dict(id=f"moving-{kind}", goal_kind=kind, region=REGIONS[2], split="test",
                             start="START", goal="GOAL", route=["GOAL", "TURN", "RETURN"], target="HERO", seed=42))
    data, metrics = tmp_path / "data", {}
    for value in selected:
        if value["goal_kind"] in ("point", "object"):
            reference = tmp_path / "references" / value["id"]
            value["teacher_v2_episode"] = str(reference)
            complete_episode(reference, value, 2)
            metrics[reference] = metric(value, 2)
        directory = v3.episode_directory(data, value, 3)
        complete_episode(directory, value, 3)
        metrics[directory] = metric(value, 3)
    return selected, data, metrics


def test_evaluation_uses_only_the_frozen_selection_and_its_exact_v2_references(tmp_path):
    selected, data, metrics = measured_selection(tmp_path)
    old = point("unrelated", 99)
    complete_episode(v3.episode_directory(data, old, 2), old, 2)
    complete_episode(v3.episode_directory(data, old, 3), old, 3)
    called = []

    def measured(directory):
        directory = Path(directory)
        called.append(directory)
        assert directory in metrics, "old dataset episodes leaked into the selected comparison"
        return metrics[directory]

    result = campaign.evaluate_selection(selected, data, metrics_fn=measured)
    assert set(called) == set(metrics)
    assert len(called) == len(result["episodes"]) == 6
    assert result["selected"] == 4
    assert result["expected_by_kind"] == {kind: 1 for kind in v3.KINDS}
    assert result["all_passed"] is True
    assert result["stop_required"] is False
    assert result["by_kind"]["follow"]["v3"]["follow_fraction"] == .8
    assert all(result["comparisons"][kind]["conditional_success_fraction"] == 1 for kind in ("point", "object"))


def test_evaluation_does_not_count_a_recording_before_contract_replay_exists(tmp_path):
    selected, data, metrics = measured_selection(tmp_path)
    value = next(value for value in selected if value["goal_kind"] == "point")
    directory = v3.episode_directory(data, value, 3)
    (directory / "contract.json").unlink()
    called = []

    def measured(path):
        called.append(Path(path))
        return metrics[Path(path)]

    result = campaign.evaluate_selection(selected, data, metrics_fn=measured)
    assert directory not in called
    assert result["checks"]["all_selected_recorded"] is False
    assert result["checks"]["contract_point"] is False
    assert result["all_passed"] is False
    assert result["stop_required"] is False


@pytest.mark.parametrize(("kind", "changes", "failed_check"), [
    ("point", dict(success=False), "comparison_point"),
    ("object", dict(success=False), "comparison_object"),
    ("follow", dict(first_contact_ms=None), "follow"),
    ("follow", dict(follow_in_band_ms=799), "follow"),
    ("approach", dict(approach_success=False), "approach"),
    ("approach", dict(contract_records=999), "contract_approach"),
])
def test_evaluation_enforces_success_contact_band_and_each_kind_contract(tmp_path, kind, changes, failed_check):
    selected, data, metrics = measured_selection(tmp_path)
    value = next(value for value in selected if value["goal_kind"] == kind)
    metrics[v3.episode_directory(data, value, 3)].update(changes)
    result = campaign.evaluate_selection(selected, data, metrics_fn=lambda directory: metrics[Path(directory)])
    assert result["checks"][failed_check] is False
    assert result["all_passed"] is False
    assert result["stop_required"] is (kind == "point")


def test_evaluation_rejects_existing_v3_episode_with_another_seed(tmp_path):
    selected, data, metrics = measured_selection(tmp_path)
    value = selected[0]
    directory = v3.episode_directory(data, value, 3)
    write_metadata(directory, dict(value, seed=43), version=3)
    with pytest.raises(ValueError, match="another scenario"):
        campaign.evaluate_selection(selected, data, metrics_fn=lambda path: metrics[Path(path)])


def test_resume_recovers_own_complete_cpp_record_before_collector_enrichment(tmp_path):
    value = point(REGIONS[0], 0)
    directory = tmp_path / "v2-attempts" / "point" / "train" / value["id"]
    directory.mkdir(parents=True)
    raw = dict(teacher_version=2, demo_version=2, start=value["start"], goal=value["goal"],
               goal_kind="point", seed=0, success=True, decisions=17)
    (directory / "episode.json").write_text(json.dumps(raw), encoding="utf-8")
    for name in ("samples.bin.gz", "actions.txt", "trajectory.txt"):
        (directory / name).write_bytes(b"original recorded data")
    result = campaign.resume_metadata(directory, value, 2)
    assert result["id"] == value["id"] and result["seed"] == value["seed"]
    assert result["campaign_identity_recovered"] is True and result["decisions"] == 17
    assert (directory / "samples.bin.gz").read_bytes() == b"original recorded data"
    assert campaign.resume_metadata(directory, value, 2) == result
    with pytest.raises(ValueError, match="another scenario"):
        campaign.resume_metadata(directory, dict(value, seed=43), 2)
    # Neither an unrelated directory nor an incompatible raw route may be adopted.
    (directory / "episode.json").write_text(json.dumps(dict(raw, start="OTHER")), encoding="utf-8")
    with pytest.raises(ValueError, match="another scenario"):
        campaign.resume_metadata(directory, value, 2)


def test_worker_prepares_shared_cold_cache_serially_but_keeps_distinct_engines(tmp_path, monkeypatch):
    active = 0
    maximum = 0
    state_lock = threading.Lock()
    enter = threading.Event()
    release = threading.Event()
    created = []

    def prepare(args):
        nonlocal active, maximum
        with state_lock:
            active += 1
            maximum = max(maximum, active)
        enter.set()
        assert release.wait(timeout=3)
        engine = object()
        created.append(engine)
        with state_lock:
            active -= 1
        return engine

    monkeypatch.setattr(v3, "checked_paths", lambda args: None)
    monkeypatch.setattr(v3, "new_runner", prepare)
    workers = [campaign.Worker(SimpleNamespace(output=tmp_path / str(index)), 3, None) for index in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(worker.ready) for worker in workers]
        assert enter.wait(timeout=3)
        release.set()
        results = [future.result(timeout=3) for future in futures]
    assert maximum == 1
    assert len(set(map(id, results))) == len(created) == 3
    assert [worker.ready() for worker in workers] == results
    assert len(created) == 3
