#!/usr/bin/env python3
"""Bounded memory-v3 training demonstrations; run inside a caller-owned gq batch.

Only train splits are recorded. Every completed attempt remains on disk; failures
are retained with training_eligible=false. Resuming never retries a failed or
partial episode. This command does not train, submit jobs, or write reports.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
import math
from pathlib import Path
import random
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


campaign = module("nav_teacher_v3_campaign")
v3 = campaign.v3
jumps = module("nav_jump_record")
demos = module("nav_demos")
SEED = 19102026
MEMORY_VERSION = 3
FAMILIES = ("point", "object", "approach", "follow", "jump")
FOLLOW_BELOW_GATE = "follow_below_gate"
FOLLOW_BELOW_GATE_MIN_FRACTION = .7
MOVING_SPLITS = dict(harbour="train", market="train", port_streets="train", upper_streets="train",
                     onar="validation", lobart="test")
INPUTS = ("training-pairs.jsonl", "ppo-validation.jsonl", "holdout-cv.jsonl", "places.json",
          "accepted-links.txt", "object-candidates.jsonl", "object-probes.jsonl", "moving-scenarios.jsonl",
          "jump-routes.jsonl", "jump-links.txt", "save.sav")


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    v3.write_json(temporary, value)
    temporary.replace(path)


class CollectionState:
    """Keep sample hashes and status outside immutable recording directories."""
    def __init__(self, path, settings, previous=None):
        self.path, self.lock = Path(path), threading.Lock()
        self.data = dict(previous if previous is not None else
                         v3.read_json(self.path) if self.path.is_file() else {})
        self.data["settings"] = settings
        integrity = self.data.setdefault("integrity", dict(schema_version=1, episodes={}))
        if (integrity.get("schema_version") != 1 or not isinstance(integrity.get("episodes"), dict)):
            raise ValueError("Unsupported recording integrity cache")
        summary = self.path.with_name("collection-metrics.json")
        self.saved = {str(Path(value["episode"])): value for value in
                      (v3.read_json(summary).get("episodes", []) if summary.is_file() else [])}

    def key(self, directory):
        return str(Path(directory).relative_to(self.path.parent))

    def saved_metrics(self, directory):
        with self.lock:
            return self.saved.get(str(Path(directory)))

    def verify_samples(self, directory, metadata, *, allow_unpinned=False):
        digest = campaign.binary_digest(Path(directory) / "samples.bin.gz")
        with self.lock:
            pin = self.data["integrity"]["episodes"].get(self.key(directory), {})
            saved = self.saved.get(str(Path(directory)), {})
            summaries = (metadata, metadata.get("collection_metrics", {}), saved, pin)
            recorded = [value["samples_sha256"] for value in summaries if value.get("samples_sha256")]
        if not recorded and not allow_unpinned:
            raise ValueError(f"Completed recording lacks its recorded samples hash: {directory}")
        if any(value != digest for value in recorded):
            raise ValueError(f"Recorded samples hash differs: {directory}")
        return digest

    def pin_samples(self, directory, digest):
        with self.lock:
            episodes = self.data["integrity"]["episodes"]
            prior = episodes.get(self.key(directory), {})
            if prior.get("samples_sha256", digest) != digest:
                raise ValueError(f"Recorded samples hash differs: {directory}")
            episodes[self.key(directory)] = dict(samples_sha256=digest)
            atomic_json(self.path, self.data)

    def remember_metrics(self, directory, measured):
        with self.lock:
            self.saved[str(Path(directory))] = measured

    def update(self, **values):
        with self.lock:
            self.data.update(values)
            atomic_json(self.path, self.data)


def recorded_parameters(metadata):
    fields = (*v3.SCENARIO_IDENTITY_FIELDS, "index", "site_id", "transition_id", "type",
              "dataset_family", "follow_below_gate", "minimum_follow_fraction", "command_seed",
              "limit_ms", "teacher_version", "navigation_memory_version", "demo_version")
    return {key: metadata[key] for key in fields if key in metadata}


def balanced(values, key, seed=SEED):
    groups = defaultdict(list)
    for value in sorted(values, key=lambda value: value["id"]):
        groups[value[key]].append(value)
    rng = random.Random(seed)
    for group in groups.values():
        rng.shuffle(group)
    result = []
    while any(groups.values()):
        for name in sorted(groups):
            if groups[name]:
                result.append(groups[name].pop())
    return result


def select_points(training, excluded, count=400):
    regions = sorted({value["region"] for value in training})
    if len(regions) != 6:
        raise ValueError(f"Road collection requires the original six regions: {regions}")
    pool = {}
    for pair in training:
        value = dict(goal_kind="point", split="train", region=pair["region"], start=pair["start"],
                     goal=pair["goal"], seed=SEED, id=v3.scenario_id("point", pair["start"], pair["goal"]),
                     source_pair_index=pair.get("index"))
        if v3.isolated(value, excluded):
            prior = pool.setdefault(value["id"], value)
            if prior["region"] != value["region"]:
                raise ValueError("One training pair belongs to multiple regions")
    selected = balanced(pool.values(), "region")[:count]
    counts = Counter(value["region"] for value in selected)
    if len(selected) != count or set(counts) != set(regions) or max(counts.values()) - min(counts.values()) > 1:
        raise ValueError(f"Not enough isolated, balanced road pairs: {dict(counts)}")
    return selected


def select_objects(pool, probes, excluded):
    v3.validate_splits(pool)
    by_id = {}
    for probe in probes:
        identifier = probe.get("label", probe.get("id"))
        if identifier in by_id and by_id[identifier] != probe:
            raise ValueError(f"Conflicting object probes: {identifier}")
        by_id[identifier] = probe
    selected = []
    for original in pool:
        if original.get("goal_kind") != "object" or original.get("split") != "train":
            continue
        probe = by_id.get(original["id"])
        if not v3.object_qualified(probe) or v3.object_visibility(probe) == "not_visible":
            continue
        if (probe.get("start"), probe.get("goal")) != (original["start"], original["goal"]):
            raise ValueError(f"Object probe route differs: {original['id']}")
        if v3.isolated(original, excluded):
            value = dict(original, qualification=probe, visibility=v3.object_visibility(probe))
            selected.append(value)
    if len({value["id"] for value in selected}) != len(selected):
        raise ValueError("Duplicate object candidate IDs")
    if len(selected) < 100:
        raise ValueError(f"Only {len(selected)} qualified train objects; need at least 100")
    return balanced(selected, "place_id")


def select_moving(places, training, excluded, frozen, per_region=100):
    old = [value for value in frozen if value.get("goal_kind") in ("follow", "approach")]
    v3.validate_splits(old)
    for value in old:
        if MOVING_SPLITS.get(value["region"]) != value["split"]:
            raise ValueError(f"Frozen moving split changed: {value['id']}")
    generated = v3.build_candidates(places, training, excluded, SEED, point_count=0, object_places=0,
                                    moving_per_region=per_region)
    pool = {value["id"]: value for value in old}
    for value in generated:
        if value["goal_kind"] not in ("follow", "approach"):
            continue
        value["split"] = MOVING_SPLITS[value["region"]]
        if value["id"] in pool:
            # Older IDs describe the route, excluding count-dependent seed and
            # target choice. A larger pool can generate that same route with
            # another seed/creature; keep the exact frozen trial instead.
            core = ("id", "goal_kind", "split", "region", "start", "goal", "route")
            if any(value.get(key) != pool[value["id"]].get(key) for key in core):
                raise ValueError(f"Generated route changed frozen moving membership: {value['id']}")
        else:
            pool[value["id"]] = value
    result = list(pool.values())
    v3.validate_splits(result)
    for value in result:
        if not v3.isolated(value, excluded):
            raise ValueError(f"Moving route overlaps an excluded pair: {value['id']}")
    # Keep complete old split membership as selection evidence, never as recordings.
    return sorted(result, key=lambda value: (value["split"], value["goal_kind"], value["id"]))


def follow_enabled(task, requested):
    if requested == "no":
        return False
    result = v3.read_json(Path(task) / "validation/attempt-2/metrics.json")
    group = result.get("by_kind", {}).get("follow", {}).get("v3", {})
    current_runner = campaign.binary_digest(Path(task) / "build/opengothic/gothic-body-runner")
    passed = (result.get("complete") is True and result.get("collection", {}).get("follow") is True
            and result.get("settings", {}).get("runner_sha256") == current_runner
            and group.get("episodes") == 10
            and group.get("contact_episodes") == 10 and group.get("observed_episodes") == 10
            and group.get("full_horizon_episodes") == 10 and group.get("nonterminal_episodes") == 10
            and (group.get("follow_fraction") or 0) >= .8)
    if requested == "yes" and not passed:
        raise ValueError("Follow recordings require the completed ten-case >=80% validation gate")
    return passed


def family(value):
    return value.get("dataset_family", value["goal_kind"])


def version(value):
    return 2 if family(value) == "jump" else 3


def directory_for(task, value):
    if value.get("split") != "train":
        raise ValueError("Validation and test recordings are forbidden")
    identifier = value["id"]
    if Path(identifier).name != identifier or identifier in ("", ".", ".."):
        raise ValueError("Unsafe recording scenario ID")
    return Path(task) / "recordings" / family(value) / "train" / identifier


def freeze_selection(path, values):
    """Verify frozen selections without even rewriting an identical old file."""
    path = Path(path)
    if path.exists():
        if list(v3.rows(path)) != values:
            raise ValueError(f"Refusing to replace scenario selection: {path}")
    else:
        v3.write_selection(path, values)


def prepare_selections(task, follow_below_gate=False):
    source = Path(task) / "input"
    excluded = v3.exclusion_pairs(source / "holdout-cv.jsonl", source / "ppo-validation.jsonl")
    training = list(v3.rows(source / "training-pairs.jsonl"))
    points = select_points(training, excluded)
    objects = select_objects(list(v3.rows(source / "object-candidates.jsonl")),
                             list(v3.rows(source / "object-probes.jsonl")), excluded)
    moving = select_moving(v3.read_json(source / "places.json")["places"], training, excluded,
                           list(v3.rows(source / "moving-scenarios.jsonl")))
    routes = jumps.load_routes(source / "jump-routes.jsonl", source / "jump-links.txt")
    if len(routes) != 267 or sum(route["split"] == "train" for route in routes) != 168:
        raise ValueError("Jump source must preserve the original 267 routes, including 168 train routes")
    jump_values = [dict(route, id=f"jump-{route['index']:04d}", goal_kind="point", dataset_family="jump", seed=19)
                   for route in jumps.collection_order(routes) if route["split"] == "train"]
    selections = dict(point=points, object=objects,
        approach=balanced([value for value in moving if value["goal_kind"] == "approach" and value["split"] == "train"], "region"),
        follow=balanced([value for value in moving if value["goal_kind"] == "follow" and value["split"] == "train"], "region"),
        jump=jump_values)
    for name, values in selections.items():
        if any(value["split"] != "train" for value in values):
            raise ValueError("Held-out scenario entered a recording selection")
        freeze_selection(Path(task) / "recording-input" / f"{name}.jsonl", values)
    freeze_selection(Path(task) / "recording-input/moving-all-splits.jsonl", moving)
    if follow_below_gate:
        # The additive family uses the frozen training selection verbatim. It
        # never changes the old family or expands any held-out membership.
        source = list(v3.rows(Path(task) / "recording-input/follow.jsonl"))
        selections[FOLLOW_BELOW_GATE] = [dict(value, dataset_family=FOLLOW_BELOW_GATE,
            follow_below_gate=True, minimum_follow_fraction=FOLLOW_BELOW_GATE_MIN_FRACTION)
            for value in source]
        freeze_selection(Path(task) / "recording-input" / f"{FOLLOW_BELOW_GATE}.jsonl",
                         selections[FOLLOW_BELOW_GATE])
    return selections


class StorageBudget:
    """One aggregate 15-GiB ceiling, including every family's failed attempts."""
    def __init__(self, root, max_bytes=v3.MAX_BYTES):
        self.root, self.max_bytes = Path(root), max_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        if not 0 < max_bytes <= v3.MAX_BYTES:
            raise ValueError("Recording budget must be within 15 GiB")
        self.reserved, self.lock = 0, threading.Lock()

    def used(self):
        return campaign.tree_bytes(self.root)

    @contextmanager
    def reserve(self, limit_ms, jump=False):
        amount = math.ceil(limit_ms / 100) * v3.ESTIMATED_DECISION_BYTES
        if jump:
            amount = max(amount, jumps.RESERVE_BYTES)
        with self.lock:
            used, free = self.used(), shutil.disk_usage(self.root).free
            if used + self.reserved + amount > self.max_bytes or free < self.reserved + amount + 256 * 1024**2:
                raise ValueError(f"Disk budget: used={used}, reserved={self.reserved}, next={amount}, limit={self.max_bytes}, free={free}")
            self.reserved += amount
        try:
            yield
        finally:
            with self.lock:
                self.reserved -= amount
                if self.used() > self.max_bytes:
                    raise ValueError("Aggregate recording budget exceeded 15 GiB")


def runner_args(options, index, jump=False):
    task = options.task
    return SimpleNamespace(output=task / "recording-workers" / str(index) / ("jumps" if jump else "v3"),
        runner=task / "build/opengothic/gothic-body-runner", assets=Path.home() / "gothic-ai/game",
        save=task / "input/save.sav", cache=task / "recording-cache" / ("jumps" if jump else "v3"),
        links=task / "input" / ("jump-links.txt" if jump else "accepted-links.txt"),
        roads=task / "input/places.json", npc="BAU_954_MALETH", ini=None, seed=SEED,
        physics="cv", watchdog_seconds=900, limit_ms=options.limit_ms,
        contract=task / "build/opengothic/gothic-navmemoryteacher-contract")


def commands(args, value):
    directory_for(Path("/unused"), value)
    start = (jumps.scenario_commands(args, value)[-2] if family(value) == "jump"
             else v3.start_command(value, 3, args.npc, args.limit_ms))
    return [f"roads load {args.roads}", f"roads on {args.npc}", "navdemos record on",
            "navdemos perception-mode gaze", "navdemos oracle off", start, "navdemos wait"]


def validate_native(metadata, value, identity):
    if (metadata.get("teacher_version") != version(value) or metadata.get("demo_version") != 2
            or metadata.get("navigation_memory_version") != MEMORY_VERSION
            or metadata.get("perception_mode") != "gaze" or metadata.get("physics_mode") != "cv"
            or metadata.get("oracle") is not False or metadata.get("record") is not True
            or metadata.get("demonstration_recorded") is not True
            or metadata.get("drive", False) or metadata.get("replay", False)
            or metadata.get("limit_ms") != identity["limit_ms"]
            or metadata.get("start") != value["start"] or metadata.get("goal_kind") != value["goal_kind"]):
        raise ValueError(f"Recording identity, memory or mode differs: {value['id']}")
    if metadata.get("observation_source") != ("detour_teacher_v2" if family(value) == "jump" else "learner_memory"):
        raise ValueError("Wrong teacher observation source")
    if value["goal_kind"] not in ("follow", "approach") and metadata.get("goal") != value["goal"]:
        raise ValueError("Raw recording goal differs")
    # Point/object native metadata uses zero for the moving-target seed. The
    # global scenario seed is witnessed by the command file, not this field.
    if value["goal_kind"] in ("follow", "approach") and metadata.get("seed") != value["seed"]:
        raise ValueError(f"Recording seed differs: {value['id']}")
    if value["goal_kind"] == "approach" and (metadata.get("target_immortal") is not True
            or metadata.get("target_nonhostile") is not True or metadata.get("target_final_dead") is not False
            or metadata.get("target_final_down") is not False):
        raise ValueError("Approach fixture lacks immortal, nonhostile, surviving target evidence")
    if "id" in metadata and (metadata.get("recording_identity") != identity
            or any(metadata.get(key) != value.get(key) for key in v3.SCENARIO_IDENTITY_FIELDS)
            or (family(value) == "jump" and any(metadata.get(key) != value.get(key) for key in
                ("index", "site_id", "transition_id", "type", "dataset_family")))
            or (family(value) == FOLLOW_BELOW_GATE and (any(metadata.get(key) != value.get(key) for key in
                ("dataset_family", "follow_below_gate", "minimum_follow_fraction"))
                or metadata.get("teacher_goal_success") != metadata.get("success")))):
        raise ValueError(f"Completed recording belongs to another frozen scenario: {value['id']}")


def eligibility(metadata, moving=None):
    if metadata.get("dataset_family") == "jump":
        return metadata.get("transition_success") is True
    if metadata.get("goal_kind") == "follow":
        moving = moving or {}
        if metadata.get("dataset_family") == FOLLOW_BELOW_GATE:
            return (metadata.get("follow_below_gate") is True
                    and metadata.get("minimum_follow_fraction") == FOLLOW_BELOW_GATE_MIN_FRACTION
                    and metadata.get("failure") not in ("actor_down", "target_down", "collection_limit")
                    and all(metadata.get(key) is False for key in
                        ("final_dead", "final_down", "target_final_dead", "target_final_down"))
                    and metadata.get("elapsed_ms", 0) >= metadata["limit_ms"]
                    and moving.get("first_contact_ms") is not None and moving.get("follow_observed_ms", 0) > 0
                    and (moving.get("follow_fraction") or 0) >= FOLLOW_BELOW_GATE_MIN_FRACTION)
        return (metadata.get("success") is True and not metadata.get("final_dead", False)
                and not metadata.get("final_down", False) and metadata.get("elapsed_ms", 0) >= metadata["limit_ms"]
                and moving.get("first_contact_ms") is not None and moving.get("follow_observed_ms", 0) > 0
                and (moving.get("follow_fraction") or 0) >= .8)
    return metadata.get("success") is True


def measured_episode(directory, metadata, checked):
    result = {key: metadata.get(key) for key in ("id", "goal_kind", "split", "region", "place_id", "success",
        "failure", "elapsed_ms", "decisions", "teacher_version", "navigation_memory_version", "transition_success")}
    result.update(family=metadata.get("dataset_family", metadata["goal_kind"]), episode=str(directory),
        key_counts=checked["key_counts"],
        payload_bytes=sum(path.stat().st_size for path in Path(directory).rglob("*")
                          if path.is_file() and path.name != "episode.json"), validated=True)
    if metadata["goal_kind"] in ("follow", "approach"):
        import numpy as np
        trace = np.loadtxt(Path(directory) / "target-trajectory.txt", ndmin=2)
        if trace.shape != (metadata["decisions"] + 1, 6):
            raise ValueError(f"Moving target trace is incomplete: {directory}")
        result.update(v3.follow_metrics(trace[:, 0], trace[:, 4], metadata["elapsed_ms"]))
    result["training_eligible"] = eligibility(metadata, result)
    if result["family"] == FOLLOW_BELOW_GATE:
        result.update(follow_below_gate=True, minimum_follow_fraction=FOLLOW_BELOW_GATE_MIN_FRACTION,
                      teacher_goal_success=metadata.get("teacher_goal_success", metadata["success"]))
    return result


def replay_contract(binary, directory, decisions, *, write=False):
    """Replay each time, but keep already checked recording metadata immutable."""
    process = subprocess.run([str(binary), str(Path(directory) / "samples.bin.gz")],
                             capture_output=True, text=True, check=False)
    if process.returncode:
        raise ValueError(f"Teacher contract failed: {directory}: {process.stderr[-2000:]}")
    import json
    result = json.loads(process.stdout)
    if result.get("records") != decisions or result.get("mismatches") != 0 or result.get("previous_mismatches") != 0:
        raise ValueError(f"Teacher contract mismatch: {directory}: {result}")
    path = Path(directory) / "contract.json"
    if path.exists():
        saved = v3.read_json(path)
        if (saved.get("records") != decisions or saved.get("mismatches") != 0
                or saved.get("previous_mismatches") != 0):
            raise ValueError(f"Saved contract changed: {directory}")
    elif write:
        atomic_json(path, result)
    return result


class Worker(campaign.Worker):
    def __init__(self, options, index, budget, identity, state=None):
        self.options, self.index, self.identity = options, index, identity
        self.state = state or CollectionState(options.task / "recordings/collection-state.json", identity)
        self.jump = False
        super().__init__(runner_args(options, index), 3, budget)

    def resume(self, value, directory):
        """Completed attempts own their parameters and finalized measurements."""
        if not v3.complete(directory):
            raise ValueError(f"Incomplete recording retained: {directory}")
        metadata = v3.read_json(directory / "episode.json")
        parameters = recorded_parameters(metadata)
        saved = metadata.get("collection_metrics") or self.state.saved_metrics(directory)
        digest = self.state.verify_samples(directory, metadata, allow_unpinned=saved is None)
        checked = demos.summarize(directory, validate=True)
        if saved is not None:
            measured = dict(saved)
        else:
            # A native attempt completed just before collection was interrupted.
            # Fill only missing collection labels; keep its recorded seed and
            # parameters. Finalization is saved outside the recording directory.
            for key, item in value.items():
                metadata.setdefault(key, item)
            metadata.setdefault("command_seed", value["seed"])
            if family(value) == "jump":
                metadata.update(jumps.assess_episode(directory, metadata))
            else:
                replay_contract(self.args.contract, directory, metadata["decisions"])
            measured = measured_episode(directory, metadata, checked)
        measured.update(seed=metadata.get("seed"), scenario=parameters,
                        samples_sha256=digest)
        self.state.pin_samples(directory, digest)
        self.state.remember_metrics(directory, measured)
        campaign.emit("recording_complete", family=family(value), id=value["id"], resumed=True,
            success=measured["success"], training_eligible=measured["training_eligible"], decisions=measured["decisions"])
        return measured

    def trial(self, value):
        directory = directory_for(self.options.task, value)
        if (directory / "episode.json").is_file():
            return self.resume(value, directory)
        identity = recording_identity(self.identity, value)
        jump = family(value) == "jump"
        if jump != self.jump:
            self.close()
            self.jump, self.args = jump, runner_args(self.options, self.index, jump)
        scenario_name = (f"demos-v4-{FOLLOW_BELOW_GATE}-{value['id']}" if family(value) == FOLLOW_BELOW_GATE
                         else f"demos-v4-{value['id']}")
        expected = [f"seed {value['seed']}", "cheat god", "set time 10 0", "navdemos physics cv",
                    *commands(self.args, value), "label done"]
        started = time.monotonic()
        if directory.exists():
            raise ValueError(f"Partial recording retained; refusing to overwrite: {directory}")
        if getattr(self.options, "verify_existing_only", False):
            raise ValueError(f"Verification cannot start a new recording: {directory}")
        with self.budget.reserve(self.args.limit_ms, jump=jump):
            campaign.emit("recording_started", family=family(value), id=value["id"])
            self.scenario(scenario_name, commands(self.args, value), directory, value["seed"])
        if not v3.complete(directory):
            raise ValueError(f"Incomplete recording retained: {directory}")
        witnesses = sorted((self.options.task / "recording-workers").glob(f"*/*/scenarios/{scenario_name}.bench"))
        if not witnesses or any(path.read_text(encoding="utf-8").splitlines() != expected for path in witnesses):
            raise ValueError(f"Missing or changed seeded command witness: {value['id']}")
        metadata = v3.read_json(directory / "episode.json")
        validate_native(metadata, value, identity)
        metadata["runner_goal"] = metadata.get("goal")
        actual_seed = metadata.get("seed")
        metadata.update(value, recording_identity=identity, collector_wall_s=time.monotonic() - started,
                        command_seed=value["seed"])
        metadata["seed"] = actual_seed
        if family(value) == FOLLOW_BELOW_GATE:
            metadata["teacher_goal_success"] = metadata["success"]
        digest = self.state.verify_samples(directory, metadata, allow_unpinned=True)
        if jump:
            metadata.update(jumps.assess_episode(directory, value))
        checked = demos.summarize(directory, validate=True)
        if checked.get("navigation_memory_versions") != [3] or checked.get("format_versions") != [2]:
            raise ValueError("Only memory-v3 NAVDEMO-v2 recordings are allowed")
        if not jump:
            replay_contract(self.args.contract, directory, metadata["decisions"], write=True)
        measured = measured_episode(directory, metadata, checked)
        measured.update(seed=metadata.get("seed"), scenario=recorded_parameters(metadata), samples_sha256=digest)
        metadata.update(training_eligible=measured["training_eligible"], collection_metrics=measured,
                        samples_sha256=digest)
        atomic_json(directory / "episode.json", metadata)
        self.state.pin_samples(directory, digest)
        self.state.remember_metrics(directory, measured)
        campaign.emit("recording_complete", family=family(value), id=value["id"], resumed=False,
            success=measured["success"], training_eligible=measured["training_eligible"], decisions=measured["decisions"])
        return measured


def settings(options, include_follow):
    task = options.task
    validation = task / "validation/attempt-2/metrics.json"
    return dict(schema_version=1, navigation_memory_version=3, demo_version=2, perception_mode="gaze", physics="cv",
        validation_attempt_2_sha256=campaign.binary_digest(validation) if validation.is_file() else None,
        input_sha256={name: campaign.binary_digest(task / "input" / name) for name in INPUTS},
        runner_sha256=campaign.binary_digest(task / "build/opengothic/gothic-body-runner"),
        contract_sha256=campaign.binary_digest(task / "build/opengothic/gothic-navmemoryteacher-contract"),
        limit_ms=options.limit_ms, include_follow=include_follow, seed=SEED, max_bytes=options.max_bytes)


def collection_settings(base_identity, prior_settings=None, *, follow_below_gate=False, source_follow_sha256=None):
    """Authorize an additive family while preserving the original recording identity.

    All old core inputs must match before the caller creates selections, changes
    state, or launches a runner. Subsequent resumes require exactly the same
    explicit opt-in and family policy.
    """
    prior_base = prior_settings
    if prior_settings is not None and prior_settings.get("schema_version") == 2:
        if not follow_below_gate:
            raise ValueError("Existing follow_below_gate recordings require the explicit --follow-below-gate opt-in")
        prior_base = prior_settings.get("base_recording_identity")
    if prior_settings is not None and prior_base != base_identity:
        raise ValueError("Recording state belongs to another binary, input, selection or follow gate")
    if not follow_below_gate:
        return base_identity
    if base_identity.get("include_follow") is not False:
        raise ValueError("The additive follow_below_gate family cannot enable the ordinary follow family")
    if not source_follow_sha256:
        raise ValueError("The additive follow family requires its frozen source selection hash")
    policy = dict(dataset_family=FOLLOW_BELOW_GATE, follow_below_gate=True,
        minimum_follow_fraction=FOLLOW_BELOW_GATE_MIN_FRACTION,
        metric="time_weighted_2m_5m_after_first_contact", require_full_horizon=True,
        require_surviving_actor_and_target=True, source_family="follow",
        source_selection_sha256=source_follow_sha256,
        source_validation_attempt_2_sha256=base_identity["validation_attempt_2_sha256"])
    result = dict(schema_version=2, base_recording_identity=base_identity,
                  family_policies={FOLLOW_BELOW_GATE: policy})
    if prior_settings is not None and prior_settings.get("schema_version") == 2 and prior_settings != result:
        raise ValueError("Frozen follow_below_gate authorization policy or source selection changed")
    return result


def recording_identity(identity, value):
    """Old families retain their exact schema-v1 identity after state migration."""
    if identity.get("schema_version") != 2:
        if family(value) == FOLLOW_BELOW_GATE:
            raise ValueError("follow_below_gate recordings require an additive family identity")
        return identity
    base = identity["base_recording_identity"]
    if family(value) != FOLLOW_BELOW_GATE:
        return base
    policy = identity["family_policies"][FOLLOW_BELOW_GATE]
    if (value.get("goal_kind") != "follow" or value.get("follow_below_gate") is not True
            or value.get("minimum_follow_fraction") != policy["minimum_follow_fraction"]):
        raise ValueError("Scenario differs from the frozen follow_below_gate policy")
    return dict(base, schema_version=2, dataset_family=FOLLOW_BELOW_GATE, follow_below_gate=True,
                minimum_follow_fraction=policy["minimum_follow_fraction"], family_policy=policy)


def summarize(episodes, selections, include_follow, used, follow_below_gate=False):
    groups = {}
    for name in (*FAMILIES, *((FOLLOW_BELOW_GATE,) if follow_below_gate else ())):
        values = [value for value in episodes if value["family"] == name]
        keys = sum((Counter(value["key_counts"]) for value in values), Counter())
        decisions = sum(value["decisions"] for value in values)
        eligible = sum(value["training_eligible"] for value in values)
        needed = (len(selections[name]) if name in ("point", "jump") else 100 if name == "object" else 60)
        complete = len(values) == needed if name in ("point", "jump") else eligible >= needed
        if name == "follow" and not include_follow:
            complete = True
        groups[name] = dict(episodes=len(values),
            successes=eligible if name in ("jump", FOLLOW_BELOW_GATE) else sum(bool(value["success"]) for value in values),
            teacher_goal_successes=sum(bool(value["success"]) for value in values),
            training_eligible=eligible, decisions=decisions, backward=keys["back"], strafe=keys["strafe"],
            backward_fraction=keys["back"] / decisions if decisions else None,
            strafe_fraction=keys["strafe"] / decisions if decisions else None, jump=keys["jump"], climb=keys["climb"],
            dataset_bytes=sum(campaign.tree_bytes(value["episode"]) for value in values), complete=complete,
            available_candidates=len(selections[name]), excluded=name == "follow" and not include_follow,
            candidate_pool_exhausted=len(values) == len(selections[name]) and not complete)
        if name == FOLLOW_BELOW_GATE:
            groups[name].update(follow_below_gate=True,
                                minimum_follow_fraction=FOLLOW_BELOW_GATE_MIN_FRACTION)
    return dict(complete=all(value["complete"] for value in groups.values()), by_family=groups,
                navigation_memory_version=3, demo_version=2, dataset_bytes=used, episodes=episodes,
                student_training_run=False)


def run(options):
    deadline = time.monotonic() + options.wall_minutes * 60
    if sys.platform != "linux":
        raise ValueError("Run collection on Ryzen/Linux inside the caller's gq batch")
    follow_below_gate = getattr(options, "follow_below_gate", False)
    verify_only = getattr(options, "verify_existing_only", False)
    expect_existing = getattr(options, "expect_existing", None)
    if follow_below_gate and options.include_follow == "yes":
        raise ValueError("--follow-below-gate is a separate family; it cannot enable ordinary follow")
    include_follow = False if follow_below_gate else follow_enabled(options.task, options.include_follow)
    base_identity = settings(options, include_follow)
    output = options.task / "recordings"
    state_path = output / "collection-state.json"
    prior_state = None
    prior_settings = None
    if state_path.exists():
        prior_state = v3.read_json(state_path)
        prior_settings = prior_state.get("settings")
        if not isinstance(prior_settings, dict):
            raise ValueError("Existing recording state lacks its frozen collection identity")
        # Check every old core identity field before making any new file. The
        # policy check below additionally freezes the new family's source hash.
        prior_base = prior_settings.get("base_recording_identity") if prior_settings.get("schema_version") == 2 else prior_settings
        if prior_base != base_identity:
            raise ValueError("Recording state belongs to another binary, input, selection or follow gate")
        if prior_settings.get("schema_version") == 2 and not follow_below_gate:
            raise ValueError("Existing follow_below_gate recordings require the explicit --follow-below-gate opt-in")
        for name in (*FAMILIES, "moving-all-splits"):
            if not (options.task / "recording-input" / f"{name}.jsonl").is_file():
                raise ValueError(f"Existing collection lacks its frozen selection: {name}")
    elif output.exists() and any(output.iterdir()):
        raise ValueError("Existing recordings lack frozen collection identity")
    if verify_only and prior_settings is None:
        raise ValueError("Verification requires an existing frozen collection")
    if follow_below_gate:
        if any(path.is_file() for path in (output / "follow").rglob("*")):
            raise ValueError("The additive override cannot migrate existing ordinary follow recordings")
        if (prior_settings is None or prior_settings.get("schema_version") != 2) and any(
                path.is_file() for path in (output / FOLLOW_BELOW_GATE).rglob("*")):
            raise ValueError("Existing follow_below_gate recordings lack their frozen authorization policy")
    if prior_settings is None:
        # Fresh collections have no old selections to preserve.
        prepare_selections(options.task)
    source_follow_sha256 = (campaign.binary_digest(options.task / "recording-input/follow.jsonl")
                            if follow_below_gate else None)
    identity = collection_settings(base_identity, prior_settings, follow_below_gate=follow_below_gate,
                                   source_follow_sha256=source_follow_sha256)
    families = (*FAMILIES, *((FOLLOW_BELOW_GATE,) if follow_below_gate else ()))
    if prior_settings is None:
        selections = prepare_selections(options.task, follow_below_gate)
    else:
        # Completed IDs refer to these frozen candidate lists. Do not regenerate
        # old seeds, qualification or route parameters while resuming.
        selections = {name: list(v3.rows(options.task / "recording-input" / f"{name}.jsonl"))
                      for name in families}
        if any(value.get("split") != "train" for values in selections.values() for value in values):
            raise ValueError("Held-out scenario entered a recording selection")
    allowed = {directory_for(options.task, value) for values in selections.values() for value in values}
    if any(path.parent not in allowed for path in output.rglob("samples.bin*")):
        raise ValueError("Recording exists outside frozen train selections")
    state = CollectionState(state_path, identity, prior_state)
    state.update(status="verifying_existing" if verify_only else "running")
    budget = StorageBudget(output, options.max_bytes)
    workers = [Worker(options, index, budget, identity, state) for index in range(options.parallel)]
    episodes, done = [], set()
    new = 0
    verified = 0
    def metrics():
        return dict(summarize(episodes, selections, include_follow, budget.used(), follow_below_gate),
                    resume_verified_episodes=verified)

    try:
        # Verify prior attempts first; partial output is never automatically rerun.
        existing = []
        for name in families:
            expected = {directory_for(options.task, value) for value in selections[name]}
            for directory in (output / name / "train").glob("*"):
                if directory not in expected:
                    raise ValueError(f"Recording outside frozen train selection: {directory}")
            for value in selections[name]:
                directory = directory_for(options.task, value)
                if directory.exists():
                    if name == "follow" and not include_follow:
                        raise ValueError("Follow recordings exist despite a closed follow gate")
                    existing.append(value)
        if expect_existing is not None and len(existing) != expect_existing:
            raise ValueError(f"Expected {expect_existing} existing episodes, found {len(existing)}")
        campaign.emit("collection_resume_started", episodes=len(existing), parallel=options.parallel)
        with ThreadPoolExecutor(max_workers=options.parallel) as executor:
            for offset in range(0, len(existing), options.parallel):
                if time.monotonic() >= deadline:
                    state.update(status="resume_verification_incomplete")
                    campaign.emit("collection_resume_bounded", verified_episodes=len(episodes),
                                  total_episodes=len(existing), new_episodes=0)
                    return dict(complete=False, resume_verification_complete=False,
                                verified_episodes=len(episodes), total_episodes=len(existing), new_episodes=0)
                batch = existing[offset:offset + options.parallel]
                futures = [executor.submit(worker.trial, value) for worker, value in zip(workers, batch)]
                measured = [future.result() for future in futures]
                episodes.extend(measured)
                verified += len(measured)
                done.update((family(value), value["id"]) for value in batch)
                campaign.emit("collection_resume_progress", verified_episodes=len(episodes), total_episodes=len(existing))
            campaign.emit("collection_resume_complete", episodes=len(existing))
            while not verify_only:
                progressed = False
                for name in families:
                    result = metrics()
                    if result["by_family"][name]["complete"]:
                        continue
                    pending = [value for value in selections[name] if (name, value["id"]) not in done]
                    # Round-robin families prevent long road collections starving other sets.
                    for offset in range(0, min(len(pending), options.round_size), options.parallel):
                        if time.monotonic() + 900 >= deadline or new >= options.max_new:
                            pending = []
                            break
                        batch = pending[offset:min(offset + options.parallel, options.max_new - new + offset)]
                        if not batch:
                            break
                        futures = [executor.submit(worker.trial, value) for worker, value in zip(workers, batch)]
                        # Always drain and preserve all concurrently started attempts.
                        measured = [future.result() for future in futures]
                        episodes.extend(measured)
                        done.update((family(value), value["id"]) for value in batch)
                        new += len(batch)
                        progressed = True
                        result = metrics()
                        atomic_json(output / "collection-metrics.json", dict(result, settings=identity))
                        campaign.emit("collection_progress", family=name, new_episodes=new,
                                      by_family=result["by_family"], bytes=budget.used())
                        if result["by_family"][name]["complete"]:
                            break
                if not progressed or metrics()["complete"]:
                    break
    finally:
        for worker in workers:
            worker.close()
    if (settings(options, include_follow) != base_identity
            or (follow_below_gate and campaign.binary_digest(options.task / "recording-input/follow.jsonl") != source_follow_sha256)):
        raise ValueError("Frozen recording inputs or binaries changed during the phase")
    result = metrics()
    result["resume_verification_complete"] = True
    atomic_json(output / "collection-metrics.json", dict(result, settings=identity))
    state.update(status="existing_verified" if verify_only else
                 "complete" if result["complete"] else "bounded_phase_complete")
    campaign.emit("collection_phase_complete", complete=result["complete"], new_episodes=new,
                  bytes=result["dataset_bytes"], by_family=result["by_family"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--include-follow", choices=("auto", "yes", "no"), default="auto")
    parser.add_argument("--follow-below-gate", action="store_true",
                        help="Explicitly add separate follow_below_gate training data with the authorized 70%% per-episode rule")
    parser.add_argument("--verify-existing-only", action="store_true",
                        help="Validate all existing recordings and refresh the global summary without starting scenarios")
    parser.add_argument("--expect-existing", type=int,
                        help="Require this exact number of existing episodes before verification")
    parser.add_argument("--wall-minutes", type=float, default=100)
    parser.add_argument("--max-new", type=int, default=300)
    parser.add_argument("--round-size", type=int, default=12)
    parser.add_argument("--parallel", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--limit-ms", type=int, default=240000)
    parser.add_argument("--max-bytes", type=int, default=v3.MAX_BYTES)
    options = parser.parse_args()
    if not options.task.is_absolute() or options.limit_ms != jumps.LIMIT_MS:
        parser.error("An absolute task and the unchanged 240000-ms collection horizon are required")
    if not 15 < options.wall_minutes <= 110 or min(options.max_new, options.round_size) <= 0:
        parser.error("Wall budget must be in (15,110] minutes; positive episode bounds are required")
    if options.expect_existing is not None and options.expect_existing < 0:
        parser.error("Expected existing episode count cannot be negative")
    options.task = options.task.resolve()
    try:
        run(options)
    except Exception as exc:
        atomic_json(options.task / "recordings/collection-error.json", dict(error=type(exc).__name__, detail=str(exc)))
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
