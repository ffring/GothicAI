#!/usr/bin/env python3
"""Bounded, resumable teacher-v3 campaign; execute inside the caller's gq batch.

Selection uses v2 and geometry only. No policy training, queue submission, report
writing or deletion of existing dataset episodes occurs here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from copy import copy
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import threading
import time
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location("nav_teacher_v3", Path(__file__).with_name("nav_teacher_v3.py"))
v3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v3)


_PREPARE_LOCK = threading.Lock()


def setup_fraction_cache(task):
    """Memoize old v2 fractions without changing existing episode metadata."""
    path = Path(task) / "beyond-fractions.json"
    values = v3.read_json(path) if path.is_file() else {}
    original = getattr(v3.beyond_path_fraction, "uncached", v3.beyond_path_fraction)
    lock = threading.Lock()

    def cached(directory, demos=None):
        sample = Path(directory).resolve() / "samples.bin.gz"
        stamp = sample.stat()
        key = f"{sample}|{stamp.st_size}|{stamp.st_mtime_ns}"
        with lock:
            if key not in values:
                values[key] = original(directory, demos)
                v3.write_json(path, values)
            return values[key]

    cached.uncached = original
    v3.beyond_path_fraction = cached


def emit(event, **values):
    print(json.dumps(dict(event=event, **values), ensure_ascii=False), flush=True)


def gate(selected, regions, min_points=30, min_objects=30, min_places=30, per_region=5, object_regions=()):
    counts = v3.qualification_gate(selected, minimum_points=min_points, minimum_objects=min_objects,
                                   minimum_object_places=min_places, required_point_regions=regions,
                                   minimum_points_per_region=per_region)
    objects = Counter(p["region"] for p in selected if p["goal_kind"] == "object")
    missing = sorted(set(object_regions) - objects.keys())
    if missing:
        raise ValueError(f"Qualification gate: missing object regions {missing}")
    counts["object_regions"] = dict(sorted(objects.items()))
    return counts


def point_stop(comparison):
    rate = comparison.get("conditional_success_fraction")
    return rate is not None and rate < .5


class PendingSchedule:
    """One candidate per take, balanced across unmet quotas; finish drains all.

    Inflight episodes balance dispatch, but never count as successful outcomes.
    This deliberately retains every qualified inflight result after a quota fills.
    """
    def __init__(self, pool, qualified, attempted_ids, parallel=3, min_points=30, min_objects=30,
                 min_places=30, per_region=5, regions=(), object_regions=()):
        self.pool = list(pool)
        self.qualified = list(qualified)
        self.attempted = set(attempted_ids)
        self.parallel = parallel
        self.min_points, self.min_objects, self.min_places = min_points, min_objects, min_places
        self.per_region, self.regions, self.object_regions = per_region, tuple(regions), tuple(object_regions)
        self.inflight = set()
        self.running = {}

    def take(self):
        if len(self.inflight) >= self.parallel:
            return None
        points = [p for p in self.qualified if p["goal_kind"] == "point"]
        objects = [p for p in self.qualified if p["goal_kind"] == "object"]
        pc = Counter(p["region"] for p in points)
        oc = Counter(p["region"] for p in objects)
        places = {p["place_id"] for p in objects}
        need_points = len(points) < self.min_points or any(pc[r] < self.per_region for r in self.regions)
        need_objects = (len(objects) < self.min_objects or len(places) < self.min_places
                        or any(not oc[r] for r in self.object_regions))
        projected_points = pc + Counter(p["region"] for p in self.running.values() if p["goal_kind"] == "point")
        projected_places = places | {p["place_id"] for p in self.running.values() if p["goal_kind"] == "object"}
        projected_objects = oc + Counter(p["region"] for p in self.running.values() if p["goal_kind"] == "object")
        candidates = []
        for index, value in enumerate(self.pool):
            if value["id"] in self.attempted or value["id"] in self.inflight:
                continue
            kind = value["goal_kind"]
            if kind == "point" and need_points:
                score = (0 if pc[value["region"]] < self.per_region else 2,
                         projected_points[value["region"]], index)
            elif kind == "object" and need_objects:
                score = (0 if not oc[value["region"]] and value["region"] in self.object_regions else 1,
                         int(value["place_id"] in projected_places), projected_objects[value["region"]], index)
            else:
                continue
            candidates.append((score, value))
        if not candidates:
            return None
        value = min(candidates, key=lambda pair: pair[0])[1]
        self.inflight.add(value["id"])
        self.running[value["id"]] = value
        return value

    def finish(self, value, eligible):
        self.inflight.discard(value["id"])
        self.running.pop(value["id"], None)
        self.attempted.add(value["id"])
        if eligible and not any(p["id"] == value["id"] for p in self.qualified):
            self.qualified.append(value)


def tree_bytes(root):
    root = Path(root)
    total = 0
    if root.exists():
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                continue
    return total


class StorageBudget:
    """Aggregate dataset + this campaign's new attempts, with concurrent reserves."""
    def __init__(self, task, data, max_bytes=v3.MAX_BYTES):
        self.task, self.data = Path(task).resolve(), Path(data).resolve()
        if not 0 < max_bytes <= v3.MAX_BYTES:
            raise ValueError("Disk budget must be positive and at most 15 GiB")
        self.max_bytes, self.reserved = max_bytes, 0
        self.lock = threading.Lock()
        self.data.mkdir(parents=True, exist_ok=True)

    def used(self):
        attempts = self.task / "v2-attempts"
        # Avoid double accounting if a custom data path contains the task.
        return tree_bytes(self.data) + (0 if attempts.is_relative_to(self.data) else tree_bytes(attempts))

    @contextmanager
    def reserve(self, limit_ms):
        if limit_ms <= 0:
            raise ValueError("Positive decision duration required")
        amount = math.ceil(limit_ms / 100) * v3.ESTIMATED_DECISION_BYTES
        with self.lock:
            used = self.used()
            free = shutil.disk_usage(self.data).free
            if used + self.reserved + amount > self.max_bytes or free < self.reserved + amount + 256 * 1024**2:
                raise ValueError(f"Disk budget: used={used}, reserved={self.reserved}, next={amount}, limit={self.max_bytes}, free={free}")
            self.reserved += amount
        try:
            yield
        finally:
            with self.lock:
                self.reserved -= amount
                used = self.used()
                if used > self.max_bytes:
                    raise ValueError(f"Recording disk budget exceeded: {used} > {self.max_bytes}")


def resume_metadata(directory, value, version, require_samples=True):
    directory = Path(directory)
    path = directory / "episode.json"
    if not path.is_file():
        return None
    metadata = v3.read_json(path)
    if not v3.same_scenario(metadata, value, version):
        # A process may stop after C++ closed all episode files but before the
        # collector attached its dataset identity. Recover only that exact raw
        # terminal record in its own ID-named directory. Enriched records retain
        # strict identity checks, including the original seed.
        raw_identity = not any(key in metadata for key in ("id", "split", "region", "place_id", "route", "target"))
        matching_raw = (raw_identity and directory.name == value["id"] and v3.complete(directory)
                        and metadata.get("teacher_version") == version and metadata.get("demo_version") == 2
                        and all(metadata.get(key) == value.get(key) for key in ("start", "goal", "goal_kind"))
                        and not metadata.get("drive") and metadata.get("dagger") is None
                        and (value["goal_kind"] in ("point", "object") or metadata.get("seed") == value.get("seed")))
        if not matching_raw:
            raise ValueError(f"Existing recording belongs to another scenario: {directory}")
        metadata.update({key: value[key] for key in v3.SCENARIO_IDENTITY_FIELDS if key in value})
        if not v3.same_scenario(metadata, value, version):
            raise ValueError(f"Recovered recording belongs to another scenario: {directory}")
        metadata["campaign_identity_recovered"] = True
        v3.write_json(path, metadata)
    if require_samples:
        return metadata if v3.complete(directory) else None
    # Rejected new v2 samples are removed only AFTER this marker and the point
    # fraction are durable. Such attempts remain terminal on resume.
    if metadata.get("campaign_attempt_complete"):
        if value["goal_kind"] == "point" and "beyond_path_fraction" not in metadata:
            raise ValueError(f"Completed point attempt lacks cached fraction: {directory}")
        return metadata
    return metadata if v3.complete(directory) else None


def worker_args(options, version, index):
    task = options.task
    return SimpleNamespace(output=task / "workers" / f"v{version}-{index}",
                           runner=task / "build/opengothic/gothic-body-runner", assets=task.parent / "game",
                           save=task / "input/save.sav", cache=task / "cache", links=task / "input/accepted-links.txt",
                           roads=task / "input/places.json", npc="BAU_954_MALETH", ini=None, seed=19102026,
                           physics="cv", watchdog_seconds=900, limit_ms=options.limit_ms,
                           contract=task / "build/opengothic/gothic-navmemoryteacher-contract")


class Worker:
    def __init__(self, args, version, budget):
        self.args, self.version, self.budget = args, version, budget
        self.runner = None

    def ready(self):
        if self.runner is None:
            # Cache construction publishes several files. Serializing initial
            # prepare prevents cold parallel engines from replacing each other's
            # half-built cache; subsequent scenarios still run concurrently.
            with _PREPARE_LOCK:
                if self.runner is None:
                    v3.checked_paths(self.args)
                    self.runner = v3.new_runner(self.args)
                    emit("worker_ready", teacher=self.version, worker=str(self.args.output))
        return self.runner

    def close(self):
        if self.runner is not None:
            self.runner.close()
            self.runner = None

    def scenario(self, name, commands, directory, seed=19102026):
        v3.runner_scenario(self.args, self.ready(), name, commands, directory, seed)

    def trial(self, value, directory):
        resumed = resume_metadata(directory, value, self.version)
        demos = v3.module("nav_demos")
        if resumed is None:
            with self.budget.reserve(self.args.limit_ms):
                started = time.monotonic()
                emit("trial_started", teacher=self.version, id=value["id"], kind=value["goal_kind"], region=value["region"])
                self.scenario(f"v{self.version}-{value['id']}",
                              [f"roads load {self.args.roads}", f"roads on {self.args.npc}",
                               v3.start_command(value, self.version, self.args.npc, self.args.limit_ms), "navdemos wait"],
                              directory, value["seed"])
                if not v3.complete(directory):
                    raise ValueError(f"Incomplete teacher recording: {directory}")
                metadata = v3.read_json(directory / "episode.json")
                if metadata.get("teacher_version") != self.version or metadata.get("demo_version") != 2:
                    raise ValueError(f"Wrong teacher/NAVDEMO version: {directory}")
                metadata.update(value, limit_ms=self.args.limit_ms, collector_wall_s=time.monotonic() - started)
                v3.write_json(directory / "episode.json", metadata)
                demos.summarize(directory, validate=True)
        else:
            metadata = resumed
            demos.summarize(directory, validate=True)
        if self.version == 3:
            contract_path = directory / "contract.json"
            contract = v3.read_json(contract_path) if contract_path.is_file() else {}
            if (contract.get("records") != metadata["decisions"] or contract.get("mismatches") != 0
                    or contract.get("previous_mismatches") != 0):
                v3.replay_contract(self.args.contract, directory)
        emit("trial_complete", teacher=self.version, id=value["id"], kind=value["goal_kind"],
             success=metadata["success"], decisions=metadata["decisions"], resumed=resumed is not None)
        return metadata


def phase_start(name):
    emit("phase_started", phase=name)
    subprocess.run(["df", "-h", str(Path.home())], check=True)


def run_parallel(values, workers, callback, deadline):
    """At most one submitted trial per persistent engine, with clean draining."""
    pending = iter(values)
    with ThreadPoolExecutor(max_workers=len(workers)) as executor:
        running = {}
        exhausted = False
        while running or not exhausted:
            idle = [worker for worker in workers if worker not in running.values()]
            for worker in idle:
                try:
                    value = next(pending)
                except StopIteration:
                    exhausted = True
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Campaign wall budget reached; resume the saved campaign")
                running[executor.submit(callback, worker, value)] = worker
            if running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    running.pop(future)
                    future.result()


def nearest_regions(pool, training, positions):
    endpoints = sorted({(pair[field], pair["region"]) for pair in training for field in ("start", "goal")
                        if pair[field] in positions})
    if not endpoints:
        raise ValueError("No training endpoint coordinates for object region assignment")
    for value in pool:
        if value["goal_kind"] != "object":
            continue
        target = value["target_position"]
        name, region = min(endpoints, key=lambda pair: (math.hypot(positions[pair[0]][0]-target[0], positions[pair[0]][2]-target[2]), pair))
        value["region"] = region


def prepare_pool(options, worker, deadline):
    task = options.task
    source = task / "expanded-candidates.jsonl"
    points_file = task / "discovery/points/points-v3.jsonl"
    if not points_file.is_file():
        worker.scenario("export-points-v3", ["navdemos points-v3"], points_file.parent)
    positions = v3.load_start_positions(points_file)
    training = list(v3.rows(task / "input/training-pairs.jsonl"))
    regions = tuple(sorted({p["region"] for p in training}))
    prep = Counter()
    if source.is_file():
        pool = list(v3.rows(source))
    else:
        excluded = v3.exclusion_pairs(task / "input/holdout-cv.jsonl", task / "input/ppo-validation.jsonl")
        pool = v3.build_candidates(v3.read_json(task / "input/places.json")["places"], training, excluded, 19102026,
                                   starts_per_object=192, moving_per_region=3, point_count=2400,
                                   object_places=999, start_positions=positions)
        nearest_regions(pool, training, positions)
        # Expanded object sampling consumes RNG calls. Preserve the previously
        # frozen 18+18 moving scenarios byte-for-byte in identity and routes.
        moving_source = options.data / "scenarios.jsonl"
        if not moving_source.is_file():
            raise ValueError(f"Missing frozen moving selection: {moving_source}")
        moving = [p for p in v3.rows(moving_source) if p["goal_kind"] in ("follow", "approach")]
        moving_counts = Counter(p["goal_kind"] for p in moving)
        if moving_counts != {"follow": 18, "approach": 18}:
            raise ValueError(f"Expected frozen follow18 + approach18, got {dict(moving_counts)}")
        pool = [p for p in pool if p["goal_kind"] in ("point", "object")] + moving
        if any(not v3.isolated(value, excluded) for value in pool):
            raise ValueError("Excluded route in expanded selection")
        prepared = []
        for value in pool:
            if value["start"] not in positions:
                prep["unknown_start"] += 1
                continue
            if value["goal_kind"] == "object":
                if not value["goal"].startswith("cm:") and value["goal"] not in positions:
                    prep["unknown_object_goal"] += 1
                    continue
                if value.get("target_type") in ("house", "cave"):
                    inside, outside = value.get("entrance_in"), value.get("entrance_out")
                    if not inside or not outside or inside not in positions or outside not in positions:
                        prep["missing_entrance_alias"] += 1
                        continue
                    if math.hypot(positions[inside][0]-positions[outside][0], positions[inside][2]-positions[outside][2]) < .001:
                        prep["zero_entrance_normal"] += 1
                        continue
            prepared.append(value)
        pool = prepared
        v3.write_selection(source, pool)
        v3.write_json(task / "preparation-expanded.json", dict(rejections=dict(prep), points_exported=len(positions)))
    moving_source = options.data / "scenarios.jsonl"
    if moving_source.is_file():
        frozen_moving = [p for p in v3.rows(moving_source) if p["goal_kind"] in ("follow", "approach")]
        if [p for p in pool if p["goal_kind"] in ("follow", "approach")] != frozen_moving:
            raise ValueError("Expanded candidate file differs from the frozen moving selection")
    probes_file = task / "expanded-object-probes.jsonl"
    probes = {p.get("label", p.get("id")): p for p in v3.rows(probes_file)} if probes_file.is_file() else {}
    objects = [p for p in pool if p["goal_kind"] == "object" and p["id"] not in probes]
    # Restart only incomplete batches, never append repeated rows to the same
    # runner output. Completed batches are recovered before launching new work.
    for index in range(0, len(objects), 512):
        values = objects[index:index+512]
        label = v3.scenario_id("probe", *(p["id"] for p in values))
        directory = task / "discovery" / label
        output = directory / "object-candidates.jsonl"
        if output.is_file():
            found = list(v3.rows(output))
            ids = {p.get("label", p.get("id")) for p in found}
            if ids == {p["id"] for p in values}:
                probes.update((p.get("label", p.get("id")), p) for p in found)
                continue
        if time.monotonic() >= deadline:
            raise TimeoutError("Campaign wall budget reached during geometry discovery")
        query = task / "probe-queries" / f"{label}.txt"
        query.parent.mkdir(parents=True, exist_ok=True)
        query.write_text("".join(v3.probe_row(p)+"\n" for p in values), encoding="utf-8")
        # Remove only a partial NEW discovery output, never any recorded episode.
        if output.exists():
            output.unlink()
        worker.scenario(label, [f"navdemos probes-v3 {worker.args.npc} {query}"], directory)
        found = list(v3.rows(output))
        if {p.get("label", p.get("id")) for p in found} != {p["id"] for p in values}:
            raise ValueError(f"Probe batch incomplete: {label}")
        probes.update((p.get("label", p.get("id")), p) for p in found)
        with probes_file.open("w", encoding="utf-8") as stream:
            for key in sorted(probes):
                stream.write(json.dumps(probes[key], ensure_ascii=False)+"\n")
        emit("probes_complete", probes=len(probes), object_candidates=sum(p["goal_kind"] == "object" for p in pool))
    return pool, probes, regions


def reference_map(task, data):
    result = v3.references(data / "v2")
    for path in sorted((task / "v2-attempts").rglob("episode.json")):
        metadata = v3.read_json(path)
        if metadata.get("teacher_version") == 2 and metadata.get("campaign_attempt_complete"):
            result.setdefault(metadata["id"], path.parent)
    return result


def qualification(pool, probes, refs):
    return v3.qualified_candidates(pool, probes, refs)


def legacy_probe_stats(task, data):
    probes = {}
    for root in (task, data):
        for path in sorted(Path(root).rglob("object-candidates.jsonl")):
            if path.parent.name != "discovery":
                continue
            for value in v3.rows(path):
                probes[value.get("label", value.get("id"))] = value
    return dict(probes=len(probes), visible_centre=sum(p.get("visible_centre", p.get("visible")) is True for p in probes.values()),
                visible=sum(p.get("visible") is True for p in probes.values()),
                geometry_qualified=sum(v3.object_qualified(p) for p in probes.values()))


def save_qualification(task, counters, attempted, preparation=None, original=None, regions=(), object_regions=()):
    value = dict(counters, attempted=len(attempted), required_point_regions=list(regions),
                 geometry_object_regions=list(object_regions),
                 object_regions_without_geometry=sorted(set(regions) - set(object_regions)))
    value["object_rejection_breakdown"] = {
        key: counters.get("rejections", {}).get("object", {}).get(reason, 0)
        for key, reason in (("not_visible", "not_visible"), ("detour_short", "detour_too_short"),
                            ("unsupported", "unsupported_start"), ("incomplete", "no_detour"), ("v2_failed", "v2_failed"))}
    if preparation:
        value["preparation"] = preparation
    if original is not None:
        value["original_probe_baseline"] = original
    v3.write_json(task / "qualification-expanded.json", value)


def baseline(options, pool, probes, regions, workers, deadline):
    refs = reference_map(options.task, options.data)
    qualified, counters = qualification(pool, probes, refs)
    # Moving scenarios never need a privileged v2 baseline for qualification.
    qualified = [p for p in qualified if p["goal_kind"] in ("point", "object")]
    attempted = {p["id"] for p in pool if p["id"] in refs or (p["start"], p["goal"]) in refs}
    geometry_pool = [p for p in pool if p["goal_kind"] == "point"
                     or (p["goal_kind"] == "object" and v3.object_qualified(probes.get(p["id"])))]
    object_regions = tuple(sorted({p["region"] for p in geometry_pool if p["goal_kind"] == "object"}))
    schedule = PendingSchedule(geometry_pool, qualified, attempted, parallel=options.parallel,
                               min_points=options.min_points, min_objects=options.min_objects,
                               min_places=options.min_object_places, per_region=options.per_region,
                               regions=regions, object_regions=object_regions)
    preparation_file = options.task / "preparation-expanded.json"
    preparation = v3.read_json(preparation_file) if preparation_file.is_file() else None
    original = legacy_probe_stats(options.task, options.data)
    save_qualification(options.task, counters, attempted, preparation, original, regions, object_regions)

    def attempt(worker, value):
        directory = options.task / "v2-attempts" / value["goal_kind"] / value["split"] / value["id"]
        metadata = resume_metadata(directory, value, 2, require_samples=False)
        if metadata is None:
            metadata = worker.trial(value, directory)
        if value["goal_kind"] == "point" and "beyond_path_fraction" not in metadata:
            metadata["beyond_path_fraction"] = v3.beyond_path_fraction(directory)
        metadata["campaign_attempt_complete"] = True
        v3.write_json(directory / "episode.json", metadata)
        eligible = metadata.get("success") is True and (value["goal_kind"] != "point" or metadata["beyond_path_fraction"] > .3)
        if eligible:
            destination = v3.episode_directory(options.data, value, 2)
            if destination.exists():
                raise ValueError(f"Refusing to replace baseline episode: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with worker.budget.lock:
                shutil.move(str(directory), str(destination))
            directory = destination
        else:
            # Only this campaign's rejected v2 samples are disposable. Every
            # metadata/action/trajectory file and every v3 sample is preserved.
            sample = directory / "samples.bin.gz"
            with worker.budget.lock:
                if sample.is_file():
                    sample.unlink()
        return value, directory, eligible

    with ThreadPoolExecutor(max_workers=options.parallel) as executor:
        running = {}
        while True:
            for worker in [w for w in workers if w not in running.values()]:
                value = schedule.take()
                if value is None:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Campaign wall budget reached during baseline qualification")
                running[executor.submit(attempt, worker, value)] = worker
            if not running:
                break
            done, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in done:
                running.pop(future)
                value, directory, eligible = future.result()
                refs[value["id"]] = directory
                if eligible:
                    chosen, _ = qualification([value], probes, refs)
                    value = chosen[0]
                schedule.finish(value, eligible)
                # Re-evaluate the original pool with references only, never v3
                # results, so all exclusions and rejection counts remain honest.
                _, counters = qualification(pool, probes, refs)
                save_qualification(options.task, counters, schedule.attempted, preparation, original, regions, object_regions)
                emit("qualification_progress", qualified=counters["qualified"], point_regions=counters["point_regions"],
                     object_places=len(counters["object_places"]), attempted=len(schedule.attempted), inflight=len(running))
    selected, counters = qualification(pool, probes, refs)
    save_qualification(options.task, counters, schedule.attempted, preparation, original, regions, object_regions)
    gate(selected, regions, options.min_points, options.min_objects, options.min_object_places, options.per_region, object_regions)
    v3.write_selection(options.task / "expanded-scenarios.jsonl", selected)
    emit("selection_frozen", scenarios=len(selected), qualified=counters["qualified"], object_places=len(counters["object_places"]))
    return selected


def evaluate_selection(selected, data, metrics_fn=None):
    metrics_fn = metrics_fn or v3.episode_metrics
    episodes = []
    for value in selected:
        if value["goal_kind"] in ("point", "object"):
            reference = Path(value["teacher_v2_episode"])
            episodes.append(metrics_fn(reference))
        directory = v3.episode_directory(data, value, 3)
        if v3.complete(directory) and (directory / "contract.json").is_file():
            if not v3.same_scenario(v3.read_json(directory / "episode.json"), value, 3):
                raise ValueError(f"Existing v3 recording belongs to another scenario: {directory}")
            episodes.append(metrics_fn(directory))
    groups, comparisons = {}, {}
    for kind in v3.KINDS:
        values = [e for e in episodes if e["goal_kind"] == kind]
        groups[kind] = {f"v{version}": v3.group_metrics([e for e in values if e["teacher_version"] == version]) for version in (2, 3)}
        comparisons[kind] = v3.comparison_metrics(values)
    checks = {f"comparison_{kind}": (comparisons[kind]["conditional_success_fraction"] or 0) >= .8 for kind in ("point", "object")}
    follow = groups["follow"]["v3"]
    checks["follow"] = ((follow["follow_fraction"] or 0) >= .8 and follow["follow_first_contacts"] == follow["episodes"] and follow["episodes"] > 0)
    checks["approach"] = (groups["approach"]["v3"]["approach_success_fraction"] or 0) >= .8
    checks.update({f"contract_{kind}": groups[kind]["v3"]["contract_records"] >= 1000 for kind in v3.KINDS})
    expected = Counter(p["goal_kind"] for p in selected)
    checks["all_selected_recorded"] = all(groups[k]["v3"]["episodes"] == expected[k] for k in v3.KINDS)
    return dict(by_kind=groups, comparisons=comparisons, checks=checks, all_passed=all(checks.values()),
                stop_required=point_stop(comparisons["point"]), episodes=episodes,
                selected=len(selected), expected_by_kind=dict(expected))


def campaign(options):
    deadline = time.monotonic() + options.budget_seconds
    budget = StorageBudget(options.task, options.data)
    selection = options.task / "expanded-scenarios.jsonl"
    setup_fraction_cache(options.task)
    if options.stage == "evaluate" and not selection.is_file():
        raise ValueError("--stage evaluate requires the frozen expanded-scenarios.jsonl")
    if options.stage == "analyze":
        selected = list(v3.rows(selection))
        result = evaluate_selection(selected, options.data)
        v3.write_json(options.task / "campaign-metrics.json", result)
        emit("evaluation_complete", **{k: v for k, v in result.items() if k != "episodes"})
        return result
    workers = [Worker(worker_args(options, 2, i), 2, budget) for i in range(options.parallel)]
    try:
        phase_start("qualification")
        if selection.is_file():
            selected = list(v3.rows(selection))
            regions = sorted({p["region"] for p in v3.rows(options.task / "input/training-pairs.jsonl")})
            stats_path = options.task / "qualification-expanded.json"
            required_objects = v3.read_json(stats_path).get("geometry_object_regions", []) if stats_path.is_file() else []
            gate(selected, regions, options.min_points, options.min_objects, options.min_object_places, options.per_region, required_objects)
            v3.validate_splits(selected)
        else:
            pool, probes, regions = prepare_pool(options, workers[0], deadline)
            emit("ready", candidates=len(pool), geometry_objects=sum(v3.object_qualified(p) for p in probes.values()), regions=regions)
            selected = baseline(options, pool, probes, regions, workers, deadline)
    finally:
        for worker in workers:
            worker.close()
    workers = [Worker(worker_args(options, 3, i), 3, budget) for i in range(options.parallel)]
    try:
        for kind in v3.KINDS:
            phase_start(f"v3-{kind}")
            values = [p for p in selected if p["goal_kind"] == kind]
            emit("evaluate_started", kind=kind, scenarios=len(values), parallel=options.parallel)
            run_parallel(values, workers, lambda worker, value: worker.trial(value, v3.episode_directory(options.data, value, 3)), deadline)
            result = evaluate_selection(selected, options.data)
            v3.write_json(options.task / "campaign-metrics.json", result)
            if kind == "point" and result["stop_required"]:
                emit("stop_required", **result["comparisons"]["point"])
                return result
    finally:
        for worker in workers:
            worker.close()
    emit("evaluation_complete", **{k: v for k, v in result.items() if k != "episodes"})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--min-points", type=int, default=30)
    parser.add_argument("--min-objects", type=int, default=30)
    parser.add_argument("--min-object-places", type=int, default=30)
    parser.add_argument("--per-region", type=int, default=5)
    parser.add_argument("--stage", choices=("campaign", "evaluate", "analyze"), default="campaign")
    parser.add_argument("--limit-ms", type=int, default=240000)
    parser.add_argument("--budget-seconds", type=float, default=6900)
    options = parser.parse_args()
    if not options.task.is_absolute() or not options.data.is_absolute():
        parser.error("--task and --data must be absolute")
    if not 1 <= options.parallel <= 3 or min(options.min_points, options.min_objects, options.min_object_places, options.per_region, options.limit_ms, options.budget_seconds) <= 0:
        parser.error("Positive bounds and --parallel between 1 and 3 are required")
    options.task, options.data = options.task.resolve(), options.data.resolve()
    try:
        result = campaign(options)
    except Exception as exc:
        v3.write_json(options.task / "campaign-error.json", dict(error=type(exc).__name__, detail=str(exc)))
        emit("campaign_failed", error=type(exc).__name__, detail=str(exc))
        raise
    return 1 if result["stop_required"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
