#!/usr/bin/env python3
"""Collect navigation populations and freeze a teacher-qualified final test set.

Runner commands belong inside one caller-owned gq batch request. Paths passed to
body-runner are absolute. This module does not start an optimizer or choose a
checkpoint from the final test set. Generation, teacher qualification and drive
measurement are separate commands so a measured test set cannot be reselected.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import copy
import csv
import gzip
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import statistics
import sys
import threading
import time

REPO = Path(__file__).resolve().parent.parent
REGIONS = ("market", "harbour", "onar", "lobart", "port_streets", "upper_streets")
HEADS = ("forward", "strafe", "turn", "walk", "jump", "climb")
CLASSES = (3, 3, 3, 2, 2, 2)
REWARDS = ("progress_cm", "arrived", "blocked", "fall_damage", "death", "jump", "climb", "time_ms")


def module(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"Invalid JSON at {path}:{number}") from exc


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_pairs(path, pairs):
    """Freeze a selection: reruns may reuse it but cannot silently replace it."""
    path = Path(path)
    text = "".join(json.dumps(pair, ensure_ascii=False, sort_keys=True) + "\n" for pair in pairs)
    if path.exists() and path.read_text(encoding="utf-8") != text:
        raise ValueError(f"Refusing to replace a different pair selection: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_pairs(path, *, deduplicate=False):
    """Keep final selections strict; raw bench candidates may repeat point names."""
    pairs = []
    seen = set()
    for index, pair in enumerate(rows(path)):
        if not isinstance(pair, dict) or not all(
                isinstance(pair.get(key), str) and pair[key] and not any(c.isspace() for c in pair[key])
                for key in ("region", "start", "goal")):
            raise ValueError(f"Invalid named pair {index}: {pair}")
        key = pair["start"], pair["goal"]
        if key[0] == key[1]:
            raise ValueError(f"Duplicate or identical pair at {index}: {key}")
        if key in seen:
            if deduplicate:
                continue
            raise ValueError(f"Duplicate or identical pair at {index}: {key}")
        seen.add(key)
        pairs.append(pair)
    if not pairs:
        raise ValueError(f"No pairs in {path}")
    return pairs


def pair_keys(value):
    """Read historical selections, cache indices and metrics without split assumptions."""
    if isinstance(value, dict):
        start, goal = value.get("start"), value.get("goal")
        if isinstance(start, str) and isinstance(goal, str):
            yield start, goal
        pair = value.get("pair")
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(p, str) for p in pair):
            yield tuple(pair)
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from pair_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from pair_keys(child)


def historical_pairs(paths):
    """Exclude both directions, including all historical attempts, not only successes."""
    result = set()
    for path in paths:
        path = Path(path)
        if not path.exists():
            raise ValueError(f"Missing exclusion source: {path}")
        sources = sorted(p for p in path.rglob("*") if p.suffix in (".json", ".jsonl", ".csv")) if path.is_dir() else [path]
        for source in sources:
            if source.suffix == ".csv":
                with source.open(encoding="utf-8") as stream:
                    values = list(csv.DictReader(stream))
            elif source.suffix == ".jsonl":
                values = list(rows(source))
            elif source.suffix == ".json":
                values = read_json(source)
            else:
                raise ValueError(f"Unsupported exclusion source: {source}")
            for start, goal in pair_keys(values):
                result.update(((start, goal), (goal, start)))
    return result


def select_pairs(candidates, excluded, per_region, seed):
    if per_region < 1:
        raise ValueError("per-region must be positive")
    groups = defaultdict(dict)
    for pair in candidates:
        key = pair["start"], pair["goal"]
        if pair["region"] not in REGIONS:
            raise ValueError(f"Unknown region {pair['region']}")
        if key not in excluded and key[::-1] not in excluded:
            groups[pair["region"]].setdefault(key, pair)
    generator = random.Random(seed)
    selected = []
    used = set(excluded)
    for region in REGIONS:
        candidates = sorted(groups[region].values(), key=lambda pair: (pair["start"], pair["goal"]))
        generator.shuffle(candidates)
        chosen = []
        for pair in candidates:
            key = pair["start"], pair["goal"]
            if key in used or key[::-1] in used:
                continue
            chosen.append(dict(pair))
            used.update((key, key[::-1]))
            if len(chosen) == per_region:
                break
        if len(chosen) != per_region:
            raise ValueError(f"Need {per_region} unused pairs in {region}, found {len(chosen)}")
        selected.extend(chosen)
    for index, pair in enumerate(selected):
        pair.update(index=index, selection_seed=seed)
    return selected


def runner_pairs(path, pairs):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{p['start']} {p['goal']}\n" for p in pairs), encoding="utf-8")
    return path


def iter_samples(path):
    """Yield one selected action and its unweighted reward components per decision."""
    path = Path(path)
    if path.is_dir():
        path = path / "samples.jsonl"
    for index, sample in enumerate(rows(path)):
        if not isinstance(sample, dict):
            raise ValueError(f"Sample {index} must be an object")
        yield sample


def categorical_log_probs(logits, actions):
    """Stable NumPy reference for independent policy heads; returns per-head log p."""
    import numpy as np
    actions = np.asarray(actions)
    if actions.shape != (len(HEADS),) or not np.issubdtype(actions.dtype, np.integer):
        raise ValueError("action_indices must contain six integers")
    if isinstance(logits, dict):
        heads = [np.asarray(logits[name], dtype=np.float64) for name in HEADS]
    else:
        flat = np.asarray(logits, dtype=np.float64)
        if flat.shape != (sum(CLASSES),):
            raise ValueError("logits must have 15 values or six named heads")
        heads = np.split(flat, np.cumsum(CLASSES)[:-1])
    result = []
    for name, classes, values, action in zip(HEADS, CLASSES, heads, actions):
        if values.shape != (classes,) or not np.isfinite(values).all() or not 0 <= action < classes:
            raise ValueError(f"Invalid {name} logits/action")
        shifted = values - values.max()
        result.append(float(shifted[action] - np.log(np.exp(shifted).sum())))
    return np.asarray(result)


def check_episode(path, tolerance=1e-4, progress_tolerance=.05):
    """Validate C++ sampling math, transition accounting and NAVDEMO alignment."""
    import numpy as np
    path = Path(path)
    episode = read_json(path / "episode.json")
    sums = Counter()
    maximum_error = 0.0
    count = 0
    last_time = None
    first_path = last_path = None
    stamps = []
    selections = []
    last_end = None
    invalid_paths = 0
    ended = False
    for sample in iter_samples(path):
        expected = categorical_log_probs(sample["logits"], sample["action_indices"])
        selections.append(sample["action_indices"])
        actual = np.asarray(sample["log_probs"], dtype=np.float64)
        total = float(sample["log_prob"])
        if actual.shape != expected.shape or not np.isfinite(actual).all() or not math.isfinite(total):
            raise ValueError(f"Invalid log-prob in {path} sample {count}")
        maximum_error = max(maximum_error, float(np.max(np.abs(expected - actual))), abs(float(expected.sum()) - total))
        if maximum_error > tolerance:
            raise ValueError(f"Log-prob error {maximum_error:g} > {tolerance:g} in {path}")
        if ended:
            raise ValueError(f"Sample follows terminal transition in {path}")
        stamp = sample["time_ms"]
        if not isinstance(stamp, int) or stamp < 0 or (last_time is not None and stamp <= last_time):
            raise ValueError(f"Nonincreasing sample time in {path}")
        end = sample["next_time_ms"]
        if not isinstance(end, int) or not 0 < end - stamp <= 100 or (last_end is not None and stamp != last_end):
            raise ValueError(f"Invalid transition interval in {path}")
        stamps.append(stamp)
        last_time, last_end = stamp, end
        if not isinstance(sample["terminated"], bool) or not isinstance(sample["truncated"], bool) or (sample["terminated"] and sample["truncated"]):
            raise ValueError(f"Invalid terminal flags in {path}")
        ended = sample["terminated"] or sample["truncated"]
        reward = sample["rewards"]
        if set(reward) != set(REWARDS) or any(not math.isfinite(float(v)) for v in reward.values()):
            raise ValueError(f"Invalid reward components in {path}")
        if reward["time_ms"] != end - stamp or any(reward[key] < 0 for key in REWARDS if key != "progress_cm"):
            raise ValueError(f"Invalid unsigned reward component in {path}")
        before, after = float(sample["path_before_cm"]), float(sample["path_after_cm"])
        if not math.isfinite(before) or not math.isfinite(after) or before < 0 or after < 0:
            raise ValueError(f"Invalid reward path lengths in {path}")
        valid = sample.get("path_valid", True)
        invalid_paths += not valid
        if valid and abs((before - after) - reward["progress_cm"]) > .1:
            raise ValueError(f"Progress is not the Detour path difference in {path}")
        if first_path is None:
            first_path = before
        elif valid and abs(last_path - before) > .1:
            raise ValueError(f"Discontinuous reward path lengths in {path}")
        last_path = after
        sums.update(reward)
        count += 1
    if count != episode["decisions"]:
        raise ValueError(f"Sample/decision count differs in {path}: {count}/{episode['decisions']}")
    if count and (not ended or last_end - stamps[0] != episode["elapsed_ms"]):
        raise ValueError(f"Final transition does not close episode time in {path}")
    progress_error = abs(sums["progress_cm"] - first_path) / first_path if first_path else None
    if episode.get("success") and (progress_error is None or progress_error > progress_tolerance):
        raise ValueError(f"Successful progress relative error {progress_error} > {progress_tolerance} in {path}")
    actions = np.loadtxt(path / "actions.txt", ndmin=2)
    if actions.shape != (count + 1, 9) or not np.isfinite(actions).all():
        raise ValueError(f"Invalid applied-action rows in {path}")
    selected_keys = np.asarray(selections, dtype=np.float64).reshape(count, 6)
    selected_keys[:, :3] -= 1
    if not np.array_equal(selected_keys, actions[:-1, 1:7]):
        raise ValueError(f"Selected action differs from applied keys in {path}")
    if count and (not np.array_equal(actions[:-1, 0], np.asarray(stamps) - stamps[0])
                  or actions[-1, 0] != episode["elapsed_ms"]):
        raise ValueError(f"Applied-action timestamps differ in {path}")
    demos = module("nav_demos")
    records = 0
    for data in demos.iter_chunks(path):
        if data["time_ms"].tolist() != stamps[records:records + len(data)]:
            raise ValueError(f"NAVDEMO/sample timestamps differ in {path}")
        records += len(data)
    if demos.episode_version(path) != 2 or records != count:
        raise ValueError(f"NAVDEMO v2/sample count differs in {path}")
    return dict(episode=str(path), decisions=count, max_log_prob_error=maximum_error,
                reward_sums=dict(sums), start_path_cm=first_path, final_path_cm=last_path, invalid_reward_paths=invalid_paths,
                successful_progress_relative_error=progress_error if episode.get("success") else None)


def check(args):
    paths = [path.parent for path in sorted(args.source.rglob("samples.jsonl"))] if not (args.source / "samples.jsonl").is_file() else [args.source]
    if not paths:
        raise ValueError(f"No population samples in {args.source}")
    results = [check_episode(path, args.logprob_tolerance) for path in paths]
    result = dict(episodes=len(results), decisions=sum(ep["decisions"] for ep in results),
                  max_log_prob_error=max(ep["max_log_prob_error"] for ep in results), results=results)
    write_json(args.output / "check.json", result)
    print(json.dumps(result, ensure_ascii=False))


def checked_paths(args, policy=False):
    if sys.platform != "linux":
        raise ValueError("Run heavy navigation work on Ryzen/Linux under gq batch")
    for name in ("runner", "assets", "save", "cache", "links", "roads", *(('weights',) if policy else ())):
        path = getattr(args, name)
        if path is None or (name != "cache" and not path.exists()):
            raise ValueError(f"--{name} must exist")
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    os.environ["OPENGOTHIC_BODY_TRACE"] = "0"


def new_runner(args, name):
    collector = module("collect_nav_demos")
    worker = copy(args)
    worker.output = args.output / "runner-work" / name
    worker.output.mkdir(parents=True, exist_ok=True)
    scenario = collector.scenario_file(args, f"prepare-{name}", [f"navmesh build {args.cache} {args.links}"])
    runner = collector.runner_type()(worker, scenario)
    try:
        runner.scenario(scenario, worker.output / "prepare", args.seed, timeout=720)
        collector.check_log(worker.output / "prepare")
    except BaseException:
        runner.close()
        raise
    return runner


def scenario(args, runner, name, commands, directory):
    collector = module("collect_nav_demos")
    path = collector.scenario_file(args, name, commands)
    runner.scenario(path, directory, args.seed, timeout=args.watchdog_seconds)
    collector.check_log(directory)


def generate(args):
    checked_paths(args)
    excluded = historical_pairs(args.exclude)
    if not excluded:
        raise ValueError("Generation requires nonempty --exclude historical pairs")
    runner = new_runner(args, "generate")
    directory = args.output / "candidate-world"
    try:
        scenario(args, runner, "generate", [f"navmesh pairs {args.seed} {args.candidates_per_region}"], directory)
    finally:
        runner.close()
    candidates = load_pairs(directory / "pairs.jsonl", deduplicate=True)
    candidate_file = runner_pairs(args.output / "candidate-pairs.txt", candidates)
    reach_directory = args.output / "reachability"
    runner = new_runner(args, "reachability")
    try:
        scenario(args, runner, "reachability", [f"navdemos reachable {candidate_file}"], reach_directory)
    finally:
        runner.close()
    reachable = list(rows(reach_directory / "reachable.jsonl"))
    if len(reachable) != len(candidates):
        raise ValueError("Reachability result count differs from candidates")
    checked = []
    for index, (pair, reach) in enumerate(zip(candidates, reachable)):
        if (reach.get("pair_index") != index or (reach.get("start"), reach.get("goal")) != (pair["start"], pair["goal"])):
            raise ValueError(f"Reachability pair mismatch at {index}")
        if reach.get("complete") is True:
            checked.append(dict(pair, detour_complete=True, detour_length_cm=reach["length_cm"]))
    selected = select_pairs(checked, excluded, args.per_region, args.seed)
    destination = args.output / "pairs.jsonl"
    write_pairs(destination, selected)
    print(json.dumps(dict(pairs=str(destination), n=len(selected), seed=args.seed,
                          regions=dict(Counter(p["region"] for p in selected)),
                          historical_directed_pairs=len(excluded), source=str(directory / "pairs.jsonl"))))


def qualify_region(args, candidates, region, publish):
    """Visit one region in source order, independently of other runners' clocks."""
    runner = new_runner(args, f"teacher-{region}")
    chosen = []
    try:
        for index, pair in enumerate(candidates):
            if pair["region"] != region:
                continue
            directory = args.output / "teacher" / f"{index:05d}"
            if (directory / "episode.json").is_file():
                if not args.resume:
                    raise ValueError(f"Existing teacher attempt: {directory}; use --resume")
            else:
                scenario(args, runner, f"teacher-{index:05d}", [f"roads load {args.roads}", f"roads on {args.npc}",
                         f"navdemos start-v2 {args.npc} {pair['start']} {pair['goal']} {args.limit_ms}", "navdemos wait"], directory)
            if not all((directory / name).is_file() for name in ("episode.json", "samples.bin.gz", "trajectory.txt", "actions.txt")):
                raise ValueError(f"Incomplete teacher attempt: {directory}")
            episode = read_json(directory / "episode.json")
            if (episode.get("demo_version") != 2 or episode.get("teacher_version") != 2
                    or (episode.get("start"), episode.get("goal")) != (pair["start"], pair["goal"])):
                raise ValueError(f"Teacher attempt is not the requested v2 pair: {directory}")
            attempt = dict(candidate=index, region=region, start=pair["start"], goal=pair["goal"],
                           success=bool(episode["success"]), elapsed_ms=episode["elapsed_ms"], episode=str(directory))
            publish(attempt)
            if episode["success"]:
                chosen.append(dict(pair, index=index, teacher_episode=str(directory), teacher_success=True,
                                   teacher_elapsed_ms=episode["elapsed_ms"], teacher_version=2, demo_version=2,
                                   npc=args.npc, seed=args.seed))
            print(json.dumps(attempt), flush=True)
            if len(chosen) == args.per_region:
                break
    finally:
        runner.close()
    if len(chosen) != args.per_region:
        raise ValueError(f"Insufficient teacher-qualified candidates in {region}: {len(chosen)}; need {args.per_region}")
    return chosen


def qualify(args):
    checked_paths(args)
    candidates = load_pairs(args.pairs)
    if any(pair["region"] not in REGIONS for pair in candidates):
        raise ValueError("Teacher qualification requires the six known regions")
    # No learner or weights are consulted before the complete selection is frozen.
    destination = args.output / "holdout.jsonl"
    if destination.exists():
        raise ValueError(f"Final test set already frozen: {destination}")
    attempts = {}
    lock = threading.Lock()
    def publish(attempt):
        with lock:
            attempts[attempt["candidate"]] = attempt
            write_json(args.output / "teacher-attempts.json", [attempts[index] for index in sorted(attempts)])
    workers = min(args.parallel, len(REGIONS))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(qualify_region, args, candidates, region, publish) for region in REGIONS]
        groups = [future.result() for future in futures]
    chosen = sorted((pair for group in groups for pair in group), key=lambda p: (REGIONS.index(p["region"]), p["index"]))
    for index, pair in enumerate(chosen):
        pair.update(index=index, split="validation", split_index=index, final_test=True)
    write_pairs(destination, chosen)
    print(json.dumps(dict(pairs=str(destination), n=len(chosen),
                          regions=dict(Counter(pair["region"] for pair in chosen)), attempts=len(attempts), workers=workers)))


def drive(args):
    pairs = load_pairs(args.pairs)
    if not all(pair.get("final_test") and pair.get("teacher_success") and pair.get("teacher_version") == 2 for pair in pairs):
        raise ValueError("drive requires a frozen teacher-qualified final test set")
    selected = list(range(len(pairs))) if args.indices is None else [int(value) for value in args.indices.split(",")]
    if len(selected) != len(set(selected)) or any(index < 0 or index >= len(pairs) for index in selected):
        raise ValueError("--indices must contain distinct valid pair indices")
    if not selected:
        raise ValueError("No final-test drive indices selected")
    workers = min(args.parallel, len(selected))
    def collect(worker_index):
        worker = copy(args)
        # Existing nav_drive.collect owns all mutable workdirs and scenario files
        # under its output. Each worker gets a disjoint subtree and original IDs.
        if args.parallel > 1:
            worker.output = args.output / "drive-workers" / f"w{worker_index}"
        worker.command = "collect"
        worker.teacher_beta = 0
        worker.dagger_collection = False
        worker.split = "validation"
        worker.list_indices = False
        worker.selection_seed = args.seed
        worker.mixing_seed = args.seed
        worker.indices = ",".join(map(str, selected[worker_index::workers]))
        worker.start_index = 0
        worker.count = None
        module("nav_drive").collect(worker)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(collect, index) for index in range(workers)]
        for future in futures:
            future.result()


def population_worker(args, worker_index, barrier=None):
    worker = copy(args)
    worker.seed = (args.seed + worker_index) & 0xffffffff
    name = f"n{args.population}-r{args.parallel}-w{worker_index}"
    runner = None
    try:
        runner = new_runner(worker, name)
        pairs = load_pairs(args.pairs)
        # Rotate the route order between workers, preserving all pair provenance.
        offset = worker_index % len(pairs)
        selected = pairs[offset:] + pairs[:offset]
        pair_file = runner_pairs(args.output / "runner-pairs" / f"{name}.txt", selected)
        directory = args.output / "population" / name
        if (directory / "population.json").exists():
            raise ValueError(f"Population output already exists: {directory}; choose a fresh --output")
        if barrier is not None:
            barrier.wait(timeout=720 + args.watchdog_seconds)
        command = (f"navdemos population {args.npc} {pair_file} {args.limit_ms} {args.weights} "
                   f"{args.population} {worker.seed} {args.action_mode} {args.episodes} {int(args.record)} {args.hero} {args.run_ms}")
        started = time.monotonic()
        scenario(worker, runner, name, [f"roads load {args.roads}", f"roads on {args.npc}", command, "navdemos wait"], directory)
        wall = time.monotonic() - started
        result = read_json(directory / "population.json")
        if args.record:
            for episode_path in sorted((directory / "episodes").rglob("episode.json")):
                episode = read_json(episode_path)
                pair_index = episode["pair_index"]
                if not isinstance(pair_index, int) or not 0 <= pair_index < len(selected):
                    raise ValueError(f"Invalid population pair index in {episode_path}")
                pair = selected[pair_index]
                episode.update(region=pair["region"], index=pair.get("index", pair_index),
                               split=pair.get("split", "population"), split_index=pair.get("split_index", pair_index),
                               weights=str(args.weights), limit_ms=args.limit_ms, drive=True)
                write_json(episode_path, episode)
        decisions = result.get("decisions", result.get("total_decisions"))
        if not isinstance(decisions, int) or decisions <= 0:
            raise ValueError(f"Population has no decisions: {directory}")
        result.update(worker=worker_index, population=args.population, parallel=args.parallel,
                      collector_wall_s=wall, directory=str(directory), weights=str(args.weights),
                      action_mode=args.action_mode, recording=args.record, hero=args.hero,
                      decisions=decisions)
        write_json(directory / "measurement.json", result)
        return result
    finally:
        if barrier is not None:
            barrier.abort()
        if runner is not None:
            runner.close()


def population(args):
    checked_paths(args, policy=True)
    load_pairs(args.pairs)
    if args.parallel > 1:
        # Build/read immutable cache once before concurrent readers enter it.
        warm = new_runner(args, "cache-warmup")
        warm.close()
    barrier = threading.Barrier(args.parallel) if args.parallel > 1 else None
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = [pool.submit(population_worker, args, index, barrier) for index in range(args.parallel)]
        results = [future.result() for future in futures]
    all_wall = time.monotonic() - started
    wall = max(result["collector_wall_s"] for result in results)
    decisions = sum(result["decisions"] for result in results)
    result = dict(population=args.population, parallel=args.parallel, decisions=decisions,
                  collection_wall_s=wall, total_wall_s=all_wall, decisions_per_second=decisions / wall,
                  timing_scope="concurrent_full_population_scenarios_excluding_initial_world_and_navmesh_preparation",
                  recording=args.record, action_mode=args.action_mode, workers=results)
    if all("measured_decisions" in worker and "collect_seconds" in worker for worker in results):
        warmed_decisions = sum(worker["measured_decisions"] for worker in results)
        warmed_wall = max(worker["collect_seconds"] for worker in results)
        result.update(warmed_decisions=warmed_decisions, warmed_collection_wall_s=warmed_wall,
                      warmed_decisions_per_second=warmed_decisions / warmed_wall,
                      warmed_timing_scope="C++ active population intervals excluding initial N sensor/policy decisions and startup")
    write_json(args.output / f"throughput-n{args.population}-r{args.parallel}.json", result)
    print(json.dumps(result, ensure_ascii=False))


def summarize(args):
    paths = sorted(args.source.rglob("episode.json"))
    episodes = []
    for path in paths:
        episode = read_json(path)
        if not (path.parent / "actions.txt").is_file() or not (path.parent / "trajectory.txt").is_file():
            continue
        if not episode.get("drive") or episode.get("decisions", 0) == 0:
            continue
        episodes.append(module("nav_drive").episode_metrics(path.parent, module("nav_demos")))
    if not episodes:
        raise ValueError(f"No recorded policy episodes in {args.source}")
    drive_module = module("nav_drive")
    result = dict(all=drive_module.group_metrics(episodes),
                  by_region={region: drive_module.group_metrics([ep for ep in episodes if ep["region"] == region])
                             for region in sorted({ep["region"] for ep in episodes})}, episodes=episodes)
    write_json(args.output / "metrics.json", result)
    print(json.dumps(result, ensure_ascii=False))


def history(args):
    excluded = historical_pairs(args.exclude)
    if not excluded:
        raise ValueError("No historical pairs found")
    pairs = [dict(start=start, goal=goal) for start, goal in sorted(excluded) if start < goal]
    destination = args.output / "historical-pairs.jsonl"
    write_pairs(destination, pairs)
    print(json.dumps(dict(pairs=str(destination), undirected_pairs=len(pairs))))


def comparable_episodes(root):
    result = {}
    for path in sorted(root.rglob("episode.json")):
        episode = read_json(path)
        if not episode.get("drive") or episode.get("decisions", 0) == 0:
            continue
        key = episode["start"], episode["goal"]
        if key in result:
            raise ValueError(f"Repeated comparison pair {key} in {root}")
        result[key] = dict(episode, directory=str(path.parent))
    if not result:
        raise ValueError(f"No policy episodes in {root}")
    return result


def comparison_metrics(reference, repeated):
    if reference.keys() != repeated.keys():
        raise ValueError("Comparison needs identical route sets")
    comparisons = []
    for key, episode in reference.items():
        other = repeated[key]
        elapsed = episode["elapsed_ms"]
        relative = abs(other["elapsed_ms"] - elapsed) / elapsed if elapsed else float("inf")
        comparisons.append(dict(start=key[0], goal=key[1],
                                reference_success=episode["success"], repeated_success=other["success"],
                                reference_elapsed_ms=elapsed, repeated_elapsed_ms=other["elapsed_ms"],
                                elapsed_relative_error=relative,
                                passed=episode["success"] == other["success"] and relative <= .05))
    result = dict(passed=all(pair["passed"] for pair in comparisons), n=len(comparisons),
                  tolerance=.05, reference_arrived=sum(p["reference_success"] for p in comparisons),
                  repeated_arrived=sum(p["repeated_success"] for p in comparisons),
                  median_reference_ms=statistics.median(p["reference_elapsed_ms"] for p in comparisons),
                  median_repeated_ms=statistics.median(p["repeated_elapsed_ms"] for p in comparisons),
                  pairs=comparisons)
    return result


def compare(args):
    result = comparison_metrics(comparable_episodes(args.reference), comparable_episodes(args.source))
    write_json(args.output / "comparison.json", result)
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise ValueError("Population comparison exceeds success/time tolerance; see comparison.json")


def equivalence_commands(args, pair, pair_file, mode):
    """Preserve the old drive; explicitly place only the far comparison's hero."""
    commands = [f"roads load {args.roads}", f"roads on {args.npc}"]
    if mode == "reference":
        commands.append(f"navdemos drive {args.npc} {pair['start']} {pair['goal']} {args.limit_ms} {args.weights} 0 {args.seed}")
    elif mode in ("near", "far"):
        if mode == "far":
            commands.append(f"goto {args.far_waypoint}")
        commands.append(f"navdemos population {args.npc} {pair_file} {args.limit_ms} {args.weights} 1 {args.seed} argmax 1 1 {mode} 0")
    else:
        raise ValueError(f"Unknown equivalence mode: {mode}")
    return [*commands, "navdemos wait"]


def record_equivalence(args, runner, index, pair, pair_file, mode):
    worker = copy(args)
    worker.seed = pair.get("seed", args.seed)
    directory = args.output / mode / f"{index:05d}"
    episode_path = directory / "episode.json" if mode == "reference" else directory / "episodes" / "0" / "episode.json"
    if episode_path.is_file():
        if not args.resume:
            raise ValueError(f"Existing equivalence output: {episode_path}; use --resume")
    else:
        scenario(worker, runner, f"equivalence-{mode}-{index:05d}",
                 equivalence_commands(worker, pair, pair_file, mode), directory)
    episode = read_json(episode_path)
    if (episode.get("start"), episode.get("goal")) != (pair["start"], pair["goal"]) or episode.get("demo_version") != 2:
        raise ValueError(f"Equivalence recorded a different route/schema: {episode_path}")
    episode.update(region=pair["region"], index=index, split="equivalence", split_index=index,
                   drive=True, weights=str(args.weights), seed=worker.seed, limit_ms=args.limit_ms)
    write_json(episode_path, episode)
    print(json.dumps(dict(pair=index, mode=mode, success=episode["success"],
                          elapsed_ms=episode["elapsed_ms"], episode=str(episode_path.parent))), flush=True)


def distance_comparison_metrics(near, far):
    """Far simulation must retain reachability and avoid slowing common successes."""
    symmetric = comparison_metrics(near, far)
    lost = [dict(start=start, goal=goal) for (start, goal), episode in near.items()
            if episode["success"] and not far[start, goal]["success"]]
    common = []
    for key, episode in near.items():
        other = far[key]
        if not (episode["success"] and other["success"]):
            continue
        common.append(dict(start=key[0], goal=key[1], near_elapsed_ms=episode["elapsed_ms"],
                           far_elapsed_ms=other["elapsed_ms"],
                           passed=other["elapsed_ms"] <= episode["elapsed_ms"] * 1.05))
    reachability_passed = symmetric["repeated_arrived"] >= symmetric["reference_arrived"]
    return dict(passed=reachability_passed and all(pair["passed"] for pair in common),
                criterion="far_arrivals_at_least_near_and_no_more_than_5pct_slower_on_each_common_success",
                time_tolerance=.05, reachability_passed=reachability_passed,
                near_arrived=symmetric["reference_arrived"], far_arrived=symmetric["repeated_arrived"],
                lost_success_routes=lost, common_successes=common, symmetric_diagnostic=symmetric)


def equivalence(args):
    checked_paths(args, policy=True)
    pairs = load_pairs(args.pairs)
    if args.count > len(pairs):
        raise ValueError(f"Need {args.count} routes, found {len(pairs)}")
    chosen = pairs[:args.count]
    # Route order is fixed by the input file before any policy result is seen.
    write_pairs(args.output / "selected-pairs.jsonl", chosen)
    pair_files = [runner_pairs(args.output / "runner-pairs" / f"equivalence-{index:05d}.txt", [pair])
                  for index, pair in enumerate(chosen)]
    modes = ("reference", "near", "far")
    if args.parallel == 1:
        runner = new_runner(args, "equivalence")
        try:
            for index, pair in enumerate(chosen):
                for mode in modes:
                    record_equivalence(args, runner, index, pair, pair_files[index], mode)
        finally:
            runner.close()
    else:
        def record_mode(mode):
            runner = new_runner(args, f"equivalence-{mode}")
            try:
                for index, pair in enumerate(chosen):
                    record_equivalence(args, runner, index, pair, pair_files[index], mode)
            finally:
                runner.close()
        with ThreadPoolExecutor(max_workers=min(args.parallel, len(modes))) as pool:
            futures = [pool.submit(record_mode, mode) for mode in modes]
            for future in futures:
                future.result()
    reference = comparable_episodes(args.output / "reference")
    near = comparable_episodes(args.output / "near")
    far = comparable_episodes(args.output / "far")
    distances = [dict(start=start, goal=goal, min_hero_distance_cm=episode["min_hero_distance_cm"],
                      max_hero_distance_cm=episode["max_hero_distance_cm"],
                      far_enough=episode["min_hero_distance_cm"] >= args.min_far_cm)
                 for (start, goal), episode in far.items()]
    parity = comparison_metrics(reference, near)
    distance_comparison = distance_comparison_metrics(near, far)
    result = dict(passed=parity["passed"] and distance_comparison["passed"] and all(p["far_enough"] for p in distances),
                  n=len(chosen), selection="first_count_routes_in_supplied_order_before_policy_results",
                  world_reset="same_save_and_seed_before_every_reference_near_far_episode",
                  far_waypoint=args.far_waypoint, min_required_hero_distance_cm=args.min_far_cm,
                  drive_vs_population=parity, near_vs_far=distance_comparison, far_distances=distances)
    write_json(args.output / "equivalence.json", result)
    print(json.dumps(result, ensure_ascii=False))
    if not result["passed"]:
        raise ValueError("Navigation equivalence failed success/time or hero-distance check; see equivalence.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "qualify", "drive", "population", "check", "summarize", "history", "compare", "equivalence"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pairs", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--exclude", type=Path, action="append", default=[])
    parser.add_argument("--seed", type=int, default=18102026)
    parser.add_argument("--per-region", type=int, default=10)
    parser.add_argument("--count", type=int, default=10, help="First routes for independent equivalence episodes")
    parser.add_argument("--far-waypoint", default="NW_FARM1_ENTRANCE_01")
    parser.add_argument("--min-far-cm", type=float, default=6000)
    parser.add_argument("--candidates-per-region", type=int, default=200)
    parser.add_argument("--npc", default="BAU_954_MALETH")
    parser.add_argument("--population", type=int, default=20)
    parser.add_argument("--parallel", type=int, choices=(1, 4, 8), default=1)
    parser.add_argument("--episodes", type=int, help="Global episode cap; default N, or 1000000 with --run-ms")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--action-mode", choices=("sample", "argmax"), default="sample")
    parser.add_argument("--hero", choices=("near", "far"), default="far")
    parser.add_argument("--limit-ms", type=int, default=240000)
    parser.add_argument("--run-ms", type=int, default=0, help="Population game-time span; 0 runs all episodes")
    parser.add_argument("--watchdog-seconds", type=float, default=3600)
    parser.add_argument("--logprob-tolerance", type=float, default=1e-4)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--indices")
    parser.add_argument("--roads", type=Path, default=REPO / "server/src/gothic_ai/gamedata/places.json")
    parser.add_argument("--links", type=Path, default=REPO / "reports/navigation/2026-10-16-teacher/raw/accepted-links.txt")
    for name in ("runner", "assets", "save", "cache", "ini", "weights"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.expanduser().resolve())
    args.exclude = [path.expanduser().resolve() for path in args.exclude]
    if args.episodes is None:
        args.episodes = 1000000 if args.run_ms else args.population
    if not 0 <= args.seed <= 0xffffffff or args.per_region < 1 or args.count < 1 or args.min_far_cm < 0 or args.population < 1 or args.episodes < 1 or args.limit_ms < 1 or args.run_ms < 0:
        parser.error("seed must be uint32; per-region, population, episodes and limit-ms must be positive")
    if args.command in ("qualify", "drive", "population", "equivalence") and args.pairs is None:
        parser.error("--pairs is required")
    if args.command in ("check", "summarize", "compare") and args.source is None:
        parser.error("--source is required")
    if args.command == "compare" and args.reference is None:
        parser.error("--reference is required")
    if args.command == "population" and (args.episodes < args.population or (args.hero == "near" and args.population != 1)):
        parser.error("population requires episodes >= N; near hero mode requires N=1")
    if args.command == "population" and args.parallel > 1 and args.record:
        parser.error("parallel population measurements require recording disabled")
    try:
        {"generate": generate, "qualify": qualify, "drive": drive, "population": population,
         "check": check, "summarize": summarize, "history": history, "compare": compare, "equivalence": equivalence}[args.command](args)
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        parser.exit(1, f"nav-rl: {exc}\n")


if __name__ == "__main__":
    main()
