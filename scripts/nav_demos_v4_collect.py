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


def prepare_selections(task):
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
        v3.write_selection(Path(task) / "recording-input" / f"{name}.jsonl", values)
    v3.write_selection(Path(task) / "recording-input/moving-all-splits.jsonl", moving)
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
    if value["goal_kind"] in ("follow", "approach") and metadata.get("seed") != value["seed"]:
        raise ValueError("Moving seed differs")
    if value["goal_kind"] == "approach" and (metadata.get("target_immortal") is not True
            or metadata.get("target_nonhostile") is not True or metadata.get("target_final_dead") is not False
            or metadata.get("target_final_down") is not False):
        raise ValueError("Approach fixture lacks immortal, nonhostile, surviving target evidence")
    if "id" in metadata and (metadata.get("recording_identity") != identity
            or any(metadata.get(key) != value.get(key) for key in v3.SCENARIO_IDENTITY_FIELDS)
            or (family(value) == "jump" and any(metadata.get(key) != value.get(key) for key in
                ("index", "site_id", "transition_id", "type", "dataset_family")))):
        raise ValueError(f"Completed recording belongs to another frozen scenario: {value['id']}")


def eligibility(metadata, moving=None):
    if metadata.get("dataset_family") == "jump":
        return metadata.get("transition_success") is True
    if metadata.get("goal_kind") == "follow":
        moving = moving or {}
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
    return result


def replay_contract(binary, directory, decisions):
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
        if v3.read_json(path) != result:
            raise ValueError(f"Saved contract changed: {directory}")
    else:
        atomic_json(path, result)
    return result


class Worker(campaign.Worker):
    def __init__(self, options, index, budget, identity):
        self.options, self.index, self.identity = options, index, identity
        self.jump = False
        super().__init__(runner_args(options, index), 3, budget)

    def trial(self, value):
        jump = family(value) == "jump"
        if jump != self.jump:
            self.close()
            self.jump, self.args = jump, runner_args(self.options, self.index, jump)
        directory = directory_for(self.options.task, value)
        scenario_name = f"demos-v4-{value['id']}"
        expected = [f"seed {value['seed']}", "cheat god", "set time 10 0", "navdemos physics cv",
                    *commands(self.args, value), "label done"]
        resumed = (directory / "episode.json").is_file()
        started = time.monotonic()
        if not resumed:
            if directory.exists() and any(directory.iterdir()):
                raise ValueError(f"Partial recording retained; refusing to overwrite: {directory}")
            with self.budget.reserve(self.args.limit_ms, jump=jump):
                campaign.emit("recording_started", family=family(value), id=value["id"])
                self.scenario(scenario_name, commands(self.args, value), directory, value["seed"])
        if not v3.complete(directory):
            raise ValueError(f"Incomplete recording retained: {directory}")
        witnesses = sorted((self.options.task / "recording-workers").glob(f"*/*/scenarios/{scenario_name}.bench"))
        if not witnesses or any(path.read_text(encoding="utf-8").splitlines() != expected for path in witnesses):
            raise ValueError(f"Missing or changed seeded command witness: {value['id']}")
        metadata = v3.read_json(directory / "episode.json")
        validate_native(metadata, value, self.identity)
        if "id" not in metadata:
            if jump:
                metadata = jumps.assess_episode(directory, value)
            metadata["runner_goal"] = metadata.get("goal")
            metadata.update(value, recording_identity=self.identity, collector_wall_s=time.monotonic() - started)
        checked = demos.summarize(directory, validate=True)
        if checked.get("navigation_memory_versions") != [3] or checked.get("format_versions") != [2]:
            raise ValueError("Only memory-v3 NAVDEMO-v2 recordings are allowed")
        if not jump:
            contract = replay_contract(self.args.contract, directory, metadata["decisions"])
        measured = measured_episode(directory, metadata, checked)
        if "training_eligible" in metadata:
            if metadata["training_eligible"] != measured["training_eligible"] or metadata.get("collection_metrics") != measured:
                raise ValueError("Previously finalized recording metrics changed")
        else:
            metadata.update(training_eligible=measured["training_eligible"], collection_metrics=measured)
            atomic_json(directory / "episode.json", metadata)
        campaign.emit("recording_complete", family=family(value), id=value["id"], resumed=resumed,
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


def summarize(episodes, selections, include_follow, used):
    groups = {}
    for name in FAMILIES:
        values = [value for value in episodes if value["family"] == name]
        keys = sum((Counter(value["key_counts"]) for value in values), Counter())
        decisions = sum(value["decisions"] for value in values)
        eligible = sum(value["training_eligible"] for value in values)
        needed = (len(selections[name]) if name in ("point", "jump") else 100 if name == "object" else 60)
        complete = len(values) == needed if name in ("point", "jump") else eligible >= needed
        if name == "follow" and not include_follow:
            complete = True
        groups[name] = dict(episodes=len(values),
            successes=eligible if name == "jump" else sum(bool(value["success"]) for value in values),
            teacher_goal_successes=sum(bool(value["success"]) for value in values),
            training_eligible=eligible, decisions=decisions, backward=keys["back"], strafe=keys["strafe"],
            backward_fraction=keys["back"] / decisions if decisions else None,
            strafe_fraction=keys["strafe"] / decisions if decisions else None, jump=keys["jump"], climb=keys["climb"],
            dataset_bytes=sum(campaign.tree_bytes(value["episode"]) for value in values), complete=complete,
            available_candidates=len(selections[name]), excluded=name == "follow" and not include_follow,
            candidate_pool_exhausted=len(values) == len(selections[name]) and not complete)
    return dict(complete=all(value["complete"] for value in groups.values()), by_family=groups,
                navigation_memory_version=3, demo_version=2, dataset_bytes=used, episodes=episodes,
                student_training_run=False)


def run(options):
    if sys.platform != "linux":
        raise ValueError("Run collection on Ryzen/Linux inside the caller's gq batch")
    include_follow = follow_enabled(options.task, options.include_follow)
    identity = settings(options, include_follow)
    selections = prepare_selections(options.task)
    output = options.task / "recordings"
    output.mkdir(parents=True, exist_ok=True)
    state = output / "collection-state.json"
    if state.exists():
        if v3.read_json(state).get("settings") != identity:
            raise ValueError("Recording state belongs to another binary, input, selection or follow gate")
    elif any(output.iterdir()):
        raise ValueError("Existing recordings lack frozen collection identity")
    allowed = {directory_for(options.task, value) for values in selections.values() for value in values}
    if any(path.parent not in allowed for path in output.rglob("samples.bin*")):
        raise ValueError("Recording exists outside frozen train selections")
    atomic_json(state, dict(settings=identity, status="running"))
    budget = StorageBudget(output, options.max_bytes)
    workers = [Worker(options, index, budget, identity) for index in range(options.parallel)]
    episodes, done = [], set()
    deadline = time.monotonic() + options.wall_minutes * 60
    new = 0
    try:
        # Verify prior attempts first; partial output is never automatically rerun.
        for name in FAMILIES:
            expected = {directory_for(options.task, value) for value in selections[name]}
            for directory in (output / name / "train").glob("*"):
                if directory not in expected:
                    raise ValueError(f"Recording outside frozen train selection: {directory}")
            for value in selections[name]:
                directory = directory_for(options.task, value)
                if directory.exists():
                    if name == "follow" and not include_follow:
                        raise ValueError("Follow recordings exist despite a closed follow gate")
                    episodes.append(workers[0].trial(value))
                    done.add(value["id"])
        with ThreadPoolExecutor(max_workers=options.parallel) as executor:
            while True:
                progressed = False
                for name in FAMILIES:
                    result = summarize(episodes, selections, include_follow, budget.used())
                    if result["by_family"][name]["complete"]:
                        continue
                    pending = [value for value in selections[name] if value["id"] not in done]
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
                        done.update(value["id"] for value in batch)
                        new += len(batch)
                        progressed = True
                        result = summarize(episodes, selections, include_follow, budget.used())
                        atomic_json(output / "collection-metrics.json", dict(result, settings=identity))
                        campaign.emit("collection_progress", family=name, new_episodes=new,
                                      by_family=result["by_family"], bytes=budget.used())
                        if result["by_family"][name]["complete"]:
                            break
                if not progressed or summarize(episodes, selections, include_follow, budget.used())["complete"]:
                    break
    finally:
        for worker in workers:
            worker.close()
    if settings(options, include_follow) != identity:
        raise ValueError("Frozen recording inputs or binaries changed during the phase")
    result = summarize(episodes, selections, include_follow, budget.used())
    atomic_json(output / "collection-metrics.json", dict(result, settings=identity))
    atomic_json(state, dict(settings=identity, status="complete" if result["complete"] else "bounded_phase_complete"))
    campaign.emit("collection_phase_complete", complete=result["complete"], new_episodes=new,
                  bytes=result["dataset_bytes"], by_family=result["by_family"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--include-follow", choices=("auto", "yes", "no"), default="auto")
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
    options.task = options.task.resolve()
    try:
        run(options)
    except Exception as exc:
        atomic_json(options.task / "recordings/collection-error.json", dict(error=type(exc).__name__, detail=str(exc)))
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
