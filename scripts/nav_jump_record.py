#!/usr/bin/env python3
"""Record bounded batches of qualified CV teacher-v2 jump routes on Ryzen.

Run inside an admitted gq batch job (<=8 threads, <=6GiB). No training is run.
Every Runner.scenario resets the saved world. All attempts, including failures,
remain in output/episodes/NNNN; --resume skips completed attempts, never retries
failed routes. A persisted pilot stop cannot be resumed automatically.

    python3 -I scripts/nav_jump_record.py --runner /absolute/gothic-body-runner \
        --assets /absolute/game --save /absolute/save.sav --cache /absolute/own-cache \
        --links /absolute/accepted-links-cv.txt --roads /absolute/roads.json \
        --routes /absolute/routes.jsonl --output /absolute/nav-demos-jumps --count 30

Counts and stop reasons are printed as JSON. The source routes and links are
read-only. Collection evidence is appended to each existing episode.json; raw
recordings never enter git. Use nav_demos.py check on the final output separately.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import copy
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import time


REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = Path.home() / "gothic-ai/nav-demos-jumps"
TYPES = ("jump", "climb", "drop")
LIMIT_MS = 240000
# Reserve above 2401 * 173838 uncompressed NAVDEMO bytes, with room for gzip
# overhead, trajectory/actions, teacher paths and runner diagnostics. The usual
# 17KB/decision compression estimate is too small for a storage safety margin.
RESERVE_BYTES = 512 * 1024**2
MAX_BYTES = 10 * 1024**3
MIN_FREE_BYTES = 20 * 1024**3
REQUIRED = ("episode.json", "samples.bin.gz", "actions.txt", "trajectory.txt", "teacher-paths.jsonl")


def module(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def json_rows(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_metadata(path, value):
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def tree_bytes(path):
    """Count actual present file lengths, not a decision-count estimate."""
    return sum(file.stat().st_size for file in Path(path).rglob("*") if file.is_file())


def check_storage(output, disk_usage=shutil.disk_usage):
    output = Path(output)
    existing = tree_bytes(output)
    if existing + RESERVE_BYTES > MAX_BYTES:
        raise RuntimeError(f"STOP storage: {existing} existing + {RESERVE_BYTES} reserved > {MAX_BYTES} bytes")
    free = disk_usage(output).free
    if free < MIN_FREE_BYTES:
        raise RuntimeError(f"STOP disk: {free} free < {MIN_FREE_BYTES} bytes (20GiB)")
    return dict(existing_bytes=existing, reserved_bytes=RESERVE_BYTES, free_bytes=free)


def load_routes(path, links_path):
    routes = json_rows(path)
    if not routes:
        raise ValueError("No qualified routes")
    probe_module = module("nav_jumps")
    links = {link["id"]: link for link in probe_module.links(links_path)}
    route_module = module("nav_jump_routes")
    owners = {}
    for index, route in enumerate(routes):
        if route.get("index") != index:
            raise ValueError("Route indices must be consecutive starting at zero")
        if route.get("type") not in TYPES or route.get("split") not in ("train", "validation", "test"):
            raise ValueError(f"Invalid route type/split: {index}")
        for key in ("label", "start", "goal", "region"):
            if not isinstance(route.get(key), str) or not route[key] or any(c.isspace() for c in route[key]):
                raise ValueError(f"Invalid scenario token {key}: {index}")
        site = route.get("site_id", route.get("cluster"))
        if not isinstance(site, str) or not site:
            raise ValueError(f"Missing spatial site for route {index}")
        route["site_id"] = site
        identifier = route.get("transition_id")
        link = links.get(identifier)
        if link is None or probe_module.TYPES[link["type"]] != route["type"]:
            raise ValueError(f"Route transition is not in supplied accepted CV links: {index}")
        if route_module.qualifying_plan(route, route.get("teacher_plan", {})) is not None:
            raise ValueError(f"Route was not qualified against teacher v2: {index}")
        for key, expected in (("transition_start_m", link["start"]), ("transition_end_m", link["end"])):
            value = route.get(key, expected)
            if len(value) != 3 or not all(math.isfinite(v) for v in value) or math.dist(value, expected) > .001:
                raise ValueError(f"Route endpoint differs from accepted link: {index}")
            route[key] = list(expected)
        for key in (("site", site), ("transition", identifier)):
            if key in owners and owners[key] != route["split"]:
                raise ValueError(f"Spatial site/transition crosses splits: {key}")
            owners[key] = route["split"]
    return routes


def collection_order(routes):
    """Interleave the first ten attempts per type before the remaining routes."""
    grouped = {kind: [route for route in routes if route["type"] == kind] for kind in TYPES}
    pilot = [grouped[kind][i] for i in range(10) for kind in TYPES if i < len(grouped[kind])]
    chosen = {route["index"] for route in pilot}
    return pilot + [route for route in routes if route["index"] not in chosen]


def counts(episodes):
    result = {}
    for kind in TYPES:
        group = [episode for episode in episodes if episode["type"] == kind]
        successes = sum(episode.get("transition_success") is True for episode in group)
        result[kind] = dict(attempts=len(group), successes=successes,
                            reach_rate=successes/len(group) if group else None,
                            target_70pct=successes/len(group) >= .70 if group else False,
                            failures=dict(Counter(episode.get("transition_failure", "unspecified")
                                                  for episode in group if not episode.get("transition_success"))))
    return result


def pilot_stop(episodes):
    for kind, metric in counts(episodes).items():
        if metric["attempts"] >= 10 and metric["reach_rate"] < .40:
            return f"STOP teacher {kind}: {metric['successes']}/{metric['attempts']} <40%; inspect approach/action/landing in retained recordings"
    return None


def _compact_samples(directory):
    demos = module("nav_demos")
    times, positions, flags, jump, climb = [], [], [], [], []
    for records in demos.iter_chunks(directory, 32):
        times.extend(records["time_ms"].tolist())
        positions.extend(records["position_cm"].tolist())
        flags.extend(records["body"]["flags"].tolist())
        jump.extend(records["keys"]["jump"].tolist())
        climb.extend(records["keys"]["climb"].tolist())
    if not times:
        raise ValueError(f"Empty recording: {directory}")
    times = [value - times[0] for value in times]
    return times, positions, flags, jump, climb


def transition_evidence(route, trace, samples):
    """Require the scheduled id in the real teacher plan, a native action and landing.

    A successful goal alone, matching geometric proximity, or a button without
    airborne/climbing dynamics cannot count. Position/flags are outcome evidence;
    they do not enter the learner observability rule.
    """
    identifier, kind = route["transition_id"], route["type"]
    planned = [row for row in trace if any(link.get("id") == identifier and link.get("type") == kind
                                         for link in row.get("transitions", []))]
    active = [row for row in planned if row.get("active_transition_id") == identifier and
              row.get("active_transition_type") == kind and row.get("on_transition") is True]
    pressed = active if kind == "drop" else [row for row in active if row.get(kind) is True or row.get(kind) == 1]
    evidence = dict(planned=bool(planned), on_transition=bool(active), action=bool(pressed),
                    native_dynamics=False, landing=False, event_ms=None, landing_ms=None)
    if not planned:
        return evidence, "scheduled_transition_not_in_actual_path"
    if not active:
        return evidence, "approach_never_on_transition"
    if not pressed:
        return evidence, "teacher_never_pressed_" + kind
    times, positions, flags, jump, climb = samples
    start = [value*100 for value in route["transition_start_m"]]
    end = [value*100 for value in route["transition_end_m"]]
    dx, dz = end[0] - start[0], end[2] - start[2]
    horizontal_square = dx*dx + dz*dz
    for press in pressed:
        began = int(press["elapsed_ms"])
        witnessed = False
        for i, elapsed in enumerate(times):
            if elapsed < began or elapsed > began + 10000:
                continue
            airborne = flags[i][0] or flags[i][1]
            physical = ((airborne and positions[i][1] < start[1] - 20) if kind == "drop" else
                        bool(flags[i][3]) if kind == "climb" else bool(airborne))
            if physical and not witnessed:
                witnessed = True
                evidence["native_dynamics"] = True
                evidence["event_ms"] = elapsed
            along = ((positions[i][0] - start[0])*dx + (positions[i][2] - start[2])*dz) / horizontal_square if horizontal_square > 1 else 1.
            grounded = not (flags[i][0] or flags[i][1] or flags[i][3] or flags[i][4])
            if witnessed and grounded and along >= .80 and math.dist(positions[i], end) <= 150:
                evidence["landing"] = True
                evidence["landing_ms"] = elapsed
                return evidence, None
    return evidence, "landing_not_witnessed" if evidence["native_dynamics"] else "native_action_not_witnessed"


def assess_episode(directory, route):
    missing = [name for name in REQUIRED if not (directory / name).is_file()]
    if missing:
        raise RuntimeError(f"Incomplete attempt {directory}: missing {', '.join(missing)}; retained, no retry")
    episode = json.loads((directory / "episode.json").read_text(encoding="utf-8"))
    if (episode.get("physics_mode") != "cv" or episode.get("frames", 0) <= 0 or
            episode.get("cv_frames", 0) <= 0 or episode.get("demo_version") != 2 or episode.get("teacher_version") != 2):
        # Native airborne/climbing states temporarily disable actual CV inside
        # MoveAlgo even with the episode's CV forcing active.
        raise RuntimeError(f"Attempt lacks teacher v2/CV evidence: {directory}")
    samples = _compact_samples(directory)
    if len(samples[0]) != episode.get("decisions"):
        raise RuntimeError(f"Record count differs from episode metadata: {directory}")
    evidence, failure = transition_evidence(route, json_rows(directory / "teacher-paths.jsonl"), samples)
    if not failure and episode.get("success") is not True:
        failure = "goal_not_reached:" + episode.get("failure", "unspecified")
    initial_hp, final_hp = episode.get("initial_state", {}).get("hp"), episode.get("final_hp")
    if not failure and (initial_hp is None or final_hp is None):
        failure = "health_evidence_missing"
    if not failure and (final_hp < initial_hp or episode.get("final_down") or episode.get("final_dead")):
        failure = "health_lost_or_actor_down"
    episode.update({key: route[key] for key in ("index", "label", "start", "goal", "split", "site_id", "transition_id", "type", "region")})
    episode.update(route_index=route["index"], episode=str(directory), transition_evidence=evidence,
                   transition_success=failure is None, qualifying_success=failure is None, transition_failure=failure or "",
                   planned_transition_ids=route.get("planned_transition_ids", [route["transition_id"]]),
                   requested_physics="cv", collection_limit_ms=LIMIT_MS,
                   action_decisions=dict(jump=sum(bool(value) for value in samples[3]), climb=sum(bool(value) for value in samples[4])))
    return episode


def existing_episodes(output, routes):
    episodes = []
    root = output / "episodes"
    if not root.exists():
        return episodes
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not directory.name.isdecimal():
            raise RuntimeError(f"Unexpected artifact in episodes directory: {directory}")
        index = int(directory.name)
        if index >= len(routes):
            raise RuntimeError(f"Existing episode is outside selected routes: {directory}")
        missing = [name for name in REQUIRED if not (directory / name).is_file()]
        if missing:
            raise RuntimeError(f"Incomplete previous attempt {directory}; retained for diagnosis, no automatic retry")
        episode = json.loads((directory / "episode.json").read_text(encoding="utf-8"))
        for key in ("route_index", "label", "start", "goal", "split", "site_id", "transition_id", "type", "region"):
            expected = index if key == "route_index" else routes[index][key]
            if episode.get(key) != expected:
                raise RuntimeError(f"Changed/unchecked previous attempt {directory}: {key}")
        if episode.get("collection_stop"):
            raise RuntimeError(episode["collection_stop"])
        if type(episode.get("transition_success")) is not bool:
            raise RuntimeError(f"Previous attempt lacks passage evidence: {directory}")
        episodes.append(episode)
    stop = pilot_stop(episodes)
    if stop:
        raise RuntimeError(stop)
    return episodes


def scenario_commands(args, route=None):
    if route is None:
        return [f"navmesh build {args.cache} {args.links}"]
    return [f"roads load {args.roads}", f"roads on {args.npc}",
            f"navdemos start-v2 {args.npc} {route['start']} {route['goal']} {LIMIT_MS}", "navdemos wait"]


def collect(args):
    if sys.platform != "linux":
        raise RuntimeError("Run collection on Ryzen inside gq batch")
    routes = load_routes(args.routes, args.links)
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    previous = existing_episodes(args.output, routes)
    if previous and not args.resume:
        raise RuntimeError("Previous attempts exist; --resume preserves and counts them without retry")
    done = {episode["route_index"] for episode in previous}
    selected = [route for route in collection_order(routes) if route["index"] not in done][:args.count]
    if not selected:
        print(json.dumps(dict(complete=len(previous) == len(routes), by_type=counts(previous))), flush=True)
        return
    check_storage(args.output)
    collector = module("collect_nav_demos")
    worker = copy(args)
    worker.output = args.output / "runner-work"
    worker.output.mkdir(parents=True, exist_ok=True)
    prepare = collector.scenario_file(args, "jumps-record-prepare", scenario_commands(args))
    runner = collector.runner_type()(worker, prepare)
    try:
        runner.scenario(prepare, worker.output / "prepare", args.seed, timeout=720)
        collector.check_log(worker.output / "prepare")
        for route in selected:
            check_storage(args.output)
            directory = args.output / "episodes" / f"{route['index']:04d}"
            if directory.exists():
                raise RuntimeError(f"Refusing to replace attempt {directory}")
            task = collector.scenario_file(args, f"jumps-record-{route['index']:04d}", scenario_commands(args, route))
            began = time.monotonic()
            # Runner.scenario issues reset before every episode, restoring the save.
            runner.scenario(task, directory, args.seed, timeout=args.watchdog_seconds)
            collector.check_log(directory)
            episode = assess_episode(directory, route)
            episode["collector_wall_s"] = time.monotonic() - began
            previous.append(episode)
            stop = pilot_stop(previous)
            if stop:
                episode["collection_stop"] = stop
            write_metadata(directory / "episode.json", episode)
            print(json.dumps(dict(route=route["index"], type=route["type"],
                                  transition_success=episode["transition_success"], failure=episode["transition_failure"],
                                  by_type=counts(previous), stop=stop), ensure_ascii=False), flush=True)
            if stop:
                raise RuntimeError(stop)
    finally:
        runner.close()
    print(json.dumps(dict(complete=len(previous) == len(routes), attempts=len(previous),
                          routes=len(routes), by_type=counts(previous)), ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "cache", "links", "roads", "routes"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ini", type=Path)
    parser.add_argument("--npc", default="BAU_954_MALETH")
    parser.add_argument("--seed", type=int, default=19)
    parser.add_argument("--count", type=int, default=30, help="bounded new attempts in this gq request")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--watchdog-seconds", type=int, default=900)
    args = parser.parse_args()
    args.physics = "cv"
    if args.count < 1 or args.count > 300 or args.watchdog_seconds <= 0:
        parser.error("--count must be 1..300 and watchdog positive")
    for name, value in vars(args).items():
        if isinstance(value, Path) and not value.is_absolute():
            parser.error(f"--{name} must be absolute")
    for name in ("runner", "assets", "save", "links", "roads", "routes"):
        if not getattr(args, name).exists():
            parser.error(f"--{name} does not exist")
    forbidden = (Path.home() / "gothic-ai/nav-ppo").resolve()
    for value in (args.output, args.cache):
        if value.resolve() == forbidden or forbidden in value.resolve().parents:
            parser.error("Use private output/cache outside nav-ppo")
    # A cache cannot alias raw output or mutate a source tree containing inputs.
    if args.cache.resolve() == args.output.resolve():
        parser.error("cache and output must differ")
    if any(char.isspace() for char in args.npc):
        parser.error("--npc must be a single scenario token")
    collect(args)


if __name__ == "__main__":
    main()
