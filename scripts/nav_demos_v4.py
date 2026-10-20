#!/usr/bin/env python3
"""Validate the moving-target fixture before collecting memory-v3 demos.

Run inside the caller's gq batch on Ryzen. Only the validation split is
evaluated; NAVDEMO recording and the oracle are explicitly disabled. This first
phase writes text-only trials and metrics under TASK/validation/attempt-1.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


oracle = module("nav_teacher_v3_oracle")
campaign, v3 = oracle.campaign, oracle.v3
TEACHERS = ("v3", "v2")
KINDS = ("follow", "approach")
MEMORY_VERSION = 3
PERCEPTION_MODE = "gaze"


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    v3.write_json(temporary, value)
    temporary.replace(path)


def output_for(task):
    return Path(task) / "validation/attempt-1"


def validation_cases(task):
    source = Path(task) / "input/moving-scenarios.jsonl"
    values = list(v3.rows(source))
    moving = [value for value in values if value.get("goal_kind") in KINDS]
    if any(value.get("split") not in v3.SPLITS for value in moving):
        raise ValueError("Moving scenario has an unknown split")
    v3.validate_splits(moving)
    selected = [value for value in moving if value["split"] == "validation"]
    if not all(any(value["goal_kind"] == kind for value in selected) for kind in KINDS):
        raise ValueError("Validation needs both follow and approach scenarios")
    if len({value["id"] for value in selected}) != len(selected):
        raise ValueError("Duplicate moving validation scenario ID")
    for value in selected:
        identifier = value["id"]
        if not isinstance(identifier, str) or Path(identifier).name != identifier or identifier in (".", ".."):
            raise ValueError(f"Unsafe scenario ID: {identifier!r}")
        for field in ("id", "start", "goal", "target", "region"):
            v3.token(value[field])
        route = value.get("route")
        if not isinstance(route, list) or len(route) < 2:
            raise ValueError(f"Missing moving route: {identifier}")
        for waypoint in route:
            v3.token(waypoint)
        if (not isinstance(value.get("seed"), int) or isinstance(value["seed"], bool)
                or not 0 <= value["seed"] <= 0xffffffff):
            raise ValueError(f"Moving scenario needs a uint32 seed: {identifier}")
    return source, selected


def runner_args(options, index):
    task = options.task
    return SimpleNamespace(output=output_for(task) / "workers" / str(index),
        runner=task / "build/opengothic/gothic-body-runner", assets=Path.home() / "gothic-ai/game",
        save=task / "input/save.sav", cache=task / "cache", links=task / "input/accepted-links.txt",
        roads=task / "input/places.json", npc="BAU_954_MALETH", ini=None, seed=20102026,
        physics="cv", watchdog_seconds=900, limit_ms=options.limit_ms)


def settings(options, source, selected):
    args = runner_args(options, 0)
    return dict(schema_version=1, navigation_memory_version=MEMORY_VERSION, perception_mode=PERCEPTION_MODE,
        runner_sha256=campaign.binary_digest(args.runner), source=str(source),
        cohort_sha256=campaign.binary_digest(source),
        input_sha256={name: campaign.binary_digest(getattr(args, name)) for name in ("save", "links", "roads")},
        assets=str(args.assets), physics="cv", limit_ms=options.limit_ms, teachers=list(TEACHERS),
        expected_by_kind=dict(Counter(value["goal_kind"] for value in selected)),
        split="validation", record=False, oracle=False, demonstration_recorded=False)


def directory_for(task, value, teacher):
    if teacher not in TEACHERS or value["goal_kind"] not in KINDS or value["split"] != "validation":
        raise ValueError("Only v3/v2 moving validation trials are allowed")
    return output_for(task) / teacher / value["goal_kind"] / value["id"]


def command_sequence(args, value, teacher):
    # directory_for checks the split even when only preparing a command.
    directory_for(Path("/unused"), value, teacher)
    return [f"roads load {args.roads}", f"roads on {args.npc}", "navdemos record off",
        f"navdemos perception-mode {PERCEPTION_MODE}", "navdemos oracle off",
        v3.start_command(value, 3 if teacher == "v3" else 2, args.npc, args.limit_ms), "navdemos wait"]


def scenario_name(value, teacher):
    return f"demos-v4-moving-validation-{teacher}-{value['id']}"


def reject_samples(directory):
    if any(Path(directory).rglob("samples.bin*")):
        raise ValueError(f"Moving validation contains forbidden NAVDEMO recording: {directory}")


def validate_witness(task, args, value, teacher):
    expected = [f"seed {value['seed']}", "cheat god", "set time 10 0", "navdemos physics cv",
                *command_sequence(args, value, teacher), "label done"]
    witnesses = sorted((output_for(task) / "workers").glob(f"*/scenarios/{scenario_name(value, teacher)}.bench"))
    if not witnesses or any(path.read_text(encoding="utf-8").splitlines() != expected for path in witnesses):
        raise ValueError(f"Missing or different saved moving validation command: {value['id']}/{teacher}")


def validate_metadata(directory, metadata, value, teacher, identity):
    version = 3 if teacher == "v3" else 2
    source = "learner_memory" if teacher == "v3" else "detour_teacher_v2"
    reject_samples(directory)
    # A moving goal's current waypoint/position is runtime state. The frozen
    # route and target are witnessed by the exact saved scenario command.
    if (metadata.get("teacher_version") != version or metadata.get("demo_version") != 2
            or any(metadata.get(key) != value.get(key) for key in ("start", "goal_kind", "seed"))
            or metadata.get("navigation_memory_version") != MEMORY_VERSION
            or metadata.get("limit_ms") != identity["limit_ms"]
            or metadata.get("perception_mode") != PERCEPTION_MODE or metadata.get("physics_mode") != "cv"
            or metadata.get("oracle") is not False or metadata.get("record") is not False
            or metadata.get("demonstration_recorded") is not False
            or metadata.get("observation_source") != source
            or metadata.get("drive", False) or metadata.get("replay", False)):
        raise ValueError(f"Moving validation identity, memory or recording mode differs: {directory}")
    if value["goal_kind"] == "approach":
        if (metadata.get("target_immortal") is not True or metadata.get("target_nonhostile") is not True
                or any(not isinstance(metadata.get(key), bool) for key in ("target_final_dead", "target_final_down"))):
            raise ValueError(f"Approach target fixture is not immortal/nonhostile or lacks death evidence: {directory}")
    if "id" in metadata:
        if (not v3.same_scenario(metadata, value, version)
                or metadata.get("moving_validation_identity") != identity
                or metadata.get("experiment_mode") != teacher):
            raise ValueError(f"Completed moving validation belongs to a different frozen trial: {directory}")
    else:
        for key in v3.SCENARIO_IDENTITY_FIELDS:
            if key != "goal" and key in metadata and metadata[key] != value.get(key):
                raise ValueError(f"Raw moving validation scenario {key} differs: {directory}")


def text_metrics(directory):
    measured = oracle.text_metrics(directory)
    metadata = v3.read_json(Path(directory) / "episode.json")
    actions = np.loadtxt(Path(directory) / "actions.txt", ndmin=2)
    trace = np.loadtxt(Path(directory) / "target-trajectory.txt", ndmin=2)
    if trace.shape != (len(actions), 6) or not np.array_equal(trace[:, 0], actions[:, 0]):
        raise ValueError(f"Moving target trace does not align with physical actions: {directory}")
    measured.update(navigation_memory_version=metadata["navigation_memory_version"],
        target_down=metadata.get("failure") == "target_down", actor_down=metadata.get("failure") == "actor_down")
    for key in ("target_final_dead", "target_final_down", "target_immortal", "target_nonhostile",
                "target_attitude", "target_temp_attitude", "target_external_control"):
        if key in metadata:
            measured[key] = metadata[key]
    return measured


class Worker(campaign.Worker):
    def __init__(self, args, identity):
        super().__init__(args, 3, None)
        self.identity = identity

    def trial(self, task, value, teacher):
        directory = directory_for(task, value, teacher)
        episode = directory / "episode.json"
        resumed = episode.is_file()
        started = time.monotonic()
        reject_samples(directory)
        if not resumed:
            if directory.exists() and any(directory.iterdir()):
                raise ValueError(f"Incomplete prior validation; refusing to overwrite: {directory}")
            campaign.emit("moving_validation_trial_started", teacher=teacher, id=value["id"], kind=value["goal_kind"])
            self.scenario(scenario_name(value, teacher), command_sequence(self.args, value, teacher), directory, value["seed"])
        if not oracle.complete_text(directory, value["goal_kind"]):
            raise ValueError(f"Incomplete moving validation text files: {directory}")
        metadata = v3.read_json(episode)
        validate_metadata(directory, metadata, value, teacher, self.identity)
        validate_witness(task, self.args, value, teacher)
        if "id" not in metadata:
            metadata["runner_goal"] = metadata.get("goal")
            metadata.update({key: value[key] for key in v3.SCENARIO_IDENTITY_FIELDS if key in value})
            metadata.update(experiment_mode=teacher, moving_validation_identity=self.identity,
                            collector_wall_s=time.monotonic() - started)
            atomic_json(episode, metadata)
        measured = dict(text_metrics(directory), mode=teacher)
        campaign.emit("moving_validation_trial_complete", teacher=teacher, id=value["id"], kind=value["goal_kind"],
            success=metadata["success"], failure=metadata.get("failure"), resumed=resumed)
        return measured


def summarize_metrics(episodes):
    groups = {}
    for kind in KINDS:
        groups[kind] = {}
        for teacher in TEACHERS:
            values = [value for value in episodes if value["goal_kind"] == kind and value["mode"] == teacher]
            if not values:
                raise ValueError(f"Missing {teacher}/{kind} validation metrics")
            group = dict(episodes=len(values), successes=sum(bool(value["success"]) for value in values),
                failures=dict(Counter(value.get("failure") or "unspecified" for value in values if not value["success"])),
                target_down=sum(bool(value.get("target_down")) for value in values),
                target_final_dead=sum(bool(value.get("target_final_dead")) for value in values),
                target_final_down=sum(bool(value.get("target_final_down")) for value in values),
                actor_down=sum(bool(value.get("actor_down")) for value in values),
                final_dead=sum(bool(value.get("final_dead")) for value in values),
                final_down=sum(bool(value.get("final_down")) for value in values))
            group["success_fraction"] = group["successes"] / len(values)
            if kind == "follow":
                observed = sum(value["follow_observed_ms"] for value in values)
                in_band = sum(value["follow_in_band_ms"] for value in values)
                group.update(contact_episodes=sum(value["first_contact_ms"] is not None for value in values),
                    observed_episodes=sum(value["follow_observed_ms"] > 0 for value in values),
                    follow_observed_ms=observed, follow_in_band_ms=in_band,
                    follow_fraction=in_band / observed if observed > 0 else None)
            groups[kind][teacher] = group
    follow, approach = groups["follow"]["v3"], groups["approach"]["v3"]
    gates = dict(follow_all_contacted=follow["contact_episodes"] == follow["episodes"],
        follow_all_observed=follow["observed_episodes"] == follow["episodes"],
        follow_at_least_80pct=follow["follow_fraction"] is not None and follow["follow_fraction"] >= .8,
        approach_at_least_80pct=approach["success_fraction"] >= .8)
    for teacher in TEACHERS:
        target = groups["approach"][teacher]
        gates[f"approach_{teacher}_no_target_death_or_down"] = not any(target[key] for key in
            ("target_down", "target_final_dead", "target_final_down"))
    return dict(by_kind=groups, gates=gates, passed=all(gates.values()), episode_metrics=episodes,
        scenarios=len(episodes) // len(TEACHERS), episodes=len(episodes), complete=True,
        demonstration_recorded=False, navigation_memory_version=MEMORY_VERSION,
        follow_metric="sum in-band ms / sum post-contact ms; first <=500cm, band 200..500cm; every case must contact")


def run(options):
    source, selected = validation_cases(options.task)
    identity = settings(options, source, selected)
    output = output_for(options.task)
    output.mkdir(parents=True, exist_ok=True)
    state = output / "state.json"
    if state.exists():
        if v3.read_json(state).get("settings") != identity:
            raise ValueError("Moving validation belongs to another binary, input, seed, memory or time limit")
    elif any(output.iterdir()):
        raise ValueError("Existing moving validation has no identity state")
    reject_samples(output)
    atomic_json(state, dict(settings=identity, status="running"))
    workers = [Worker(runner_args(options, index), identity) for index in range(options.parallel)]
    try:
        for teacher in TEACHERS:
            for kind in KINDS:
                values = [value for value in selected if value["goal_kind"] == kind]
                campaign.run_parallel(values, workers,
                    lambda worker, value: worker.trial(options.task, value, teacher), float("inf"))
                atomic_json(state, dict(settings=identity, status=f"completed_{teacher}_{kind}"))
    finally:
        for worker in workers:
            worker.close()
    if settings(options, source, selected) != identity:
        raise ValueError("Moving validation binary or frozen inputs changed during collection")
    episodes = []
    for teacher in TEACHERS:
        for value in selected:
            directory = directory_for(options.task, value, teacher)
            validate_metadata(directory, v3.read_json(directory / "episode.json"), value, teacher, identity)
            validate_witness(options.task, runner_args(options, 0), value, teacher)
            episodes.append(dict(text_metrics(directory), mode=teacher))
    result = dict(summarize_metrics(episodes), settings=identity)
    atomic_json(output / "metrics.json", result)
    atomic_json(state, dict(settings=identity, status="passed" if result["passed"] else "failed"))
    campaign.emit("moving_validation_complete", passed=result["passed"], gates=result["gates"], by_kind=result["by_kind"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-moving", help="text-only moving-target validation, v3 against v2")
    validate.add_argument("--task", type=Path, required=True)
    validate.add_argument("--parallel", type=int, choices=(1, 2, 3), default=3)
    validate.add_argument("--limit-ms", type=int, default=240000)
    options = parser.parse_args()
    if not options.task.is_absolute() or options.limit_ms <= 0:
        parser.error("An absolute task path and a positive limit are required")
    options.task = options.task.resolve()
    try:
        result = run(options)
    except Exception as exc:
        atomic_json(output_for(options.task) / "error.json", dict(error=type(exc).__name__, detail=str(exc)))
        raise
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
