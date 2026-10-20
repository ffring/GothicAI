#!/usr/bin/env python3
"""Frozen v3 controls at physically qualified cliffs, water and Xardas ledges.

Run only inside the caller's admitted Ryzen gq batch. Historical CV links,
qualified routes and jump recordings are read-only. Fixture preparation is
bounded and precedes every actual attempt; episodes run ordinary start-v3 with
passive physical-tick monitoring and never create NAVDEMO recordings.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace


REPO = Path(__file__).resolve().parent.parent
KINDS = ("cliff", "water", "ledge")
BRIDGE_LINK = 1230208767
SHORES = (("water-bigfarm-lake", (51441, 1861, -3979)),
          ("water-lake", (49232, 1619, -7571)),
          ("water-harbour", (41, -125, 780)))
CV_STEP_CM = 60


def module(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def point(value):
    if not value.startswith("cm:"):
        raise ValueError("Historical hazard route must use explicit cm coordinates")
    result = [float(field) for field in value[3:].split(",")]
    if len(result) != 3 or not all(math.isfinite(field) for field in result):
        raise ValueError("Invalid historical hazard route coordinate")
    return result


def token(value):
    return "cm:" + ",".join(f"{field:.9g}" for field in value)


def fixture_row(label, kind, start, goal, hazard, identifier):
    return " ".join([label, kind, *(f"{v:.9g}" for p in (start, goal, hazard) for v in p), str(identifier)])


def height_candidates(routes, links, records):
    """Use recorded sites before looking at new-episode outcomes."""
    accepted = {value["id"]: value for value in module("nav_jumps").links(links)}
    historical = rows(routes)
    result, xardas_links = [], set()
    for route in historical:
        identifier = route["transition_id"]
        if identifier not in accepted:
            raise ValueError(f"Historical route link absent from accepted CV links: {identifier}")
        link = accepted[identifier]
        for key, expected in (("transition_start_m", link["start"]), ("transition_end_m", link["end"])):
            if math.dist(route[key], expected) > .001:
                raise ValueError(f"Historical route differs from accepted link: {identifier}")
        if identifier != BRIDGE_LINK and not (route["region"] == "xardas" and
                abs(link["end"][1] - link["start"][1]) >= .60 and identifier not in xardas_links):
            continue
        recorded = Path(records) / "episodes" / f"{route['index']:04d}" / "episode.json"
        if not recorded.is_file():
            raise ValueError(f"Missing historical hazard recording: {recorded}")
        metadata = json.loads(recorded.read_text())
        if metadata.get("transition_id") != identifier:
            raise ValueError(f"Historical hazard recording belongs to another link: {recorded}")
        if identifier == BRIDGE_LINK:
            start, goal = point(route["start"]), point(route["goal"])
            hazard = [v * 100 for v in link["end"]]
            dx, dz = goal[0] - start[0], goal[2] - start[2]
            length = math.hypot(dx, dz)
            for back in (0, 100, 200):
                shifted = [start[0] - back * dx / length, start[1], start[2] - back * dz / length]
                result.append(fixture_row(f"cliff-bridge-{route['index']}-back{back}", "cliff", shifted, goal, hazard, identifier))
        else:
            xardas_links.add(identifier)
            high, low = sorted((link["start"], link["end"]), key=lambda p: p[1], reverse=True)
            start, hazard = ([v * 100 for v in p] for p in (high, low))
            dx, dz = hazard[0] - start[0], hazard[2] - start[2]
            length = math.hypot(dx, dz)
            goal = [hazard[0] + 100 * dx / length, hazard[1], hazard[2] + 100 * dz / length]
            result.append(fixture_row(f"ledge-xardas-link{identifier}", "ledge", start, goal, hazard, identifier))
    if len(xardas_links) < 3 or not any(row.startswith("cliff-") for row in result):
        raise ValueError("Need historical bridge drop and at least three distinct Xardas height links")
    return result


def select_fixtures(fixtures, per_kind=3):
    selected = []
    for kind in KINDS:
        group = [row for row in fixtures if row["kind"] == kind and row.get("qualified") is True]
        if len(group) < per_kind:
            reasons = Counter(row["reason"] for row in fixtures if row["kind"] == kind)
            raise ValueError(f"Safety preparation {kind}: qualified {len(group)}/{per_kind}, reasons={dict(reasons)}")
        selected.extend(group[:per_kind])
    if len({row["label"] for row in selected}) != len(selected):
        raise ValueError("Safety fixture labels must be unique")
    return selected


def assess(fixture, metadata, frames):
    if len(frames) < 2 or any(b["elapsed_ms"] <= a["elapsed_ms"] for a, b in zip(frames, frames[1:])):
        raise ValueError("Missing/nonmonotonic physical safety monitor")
    required = ("cv_active", "supported", "in_air", "falling", "native_jump", "native_climb", "swimming")
    for frame in frames:
        if any(type(frame.get(key)) is not bool for key in required):
            raise ValueError("Incomplete passive safety monitor fields")
        if len(frame["position_cm"]) != 3 or not all(math.isfinite(v) for v in frame["position_cm"]):
            raise ValueError("Nonfinite physical safety monitor position")
        depth = frame.get("water_depth_cm")
        if not isinstance(depth, (float, int)) or not math.isfinite(depth) or depth < 0:
            raise ValueError("Missing/nonfinite passive feet water depth")
    cv_frames = sum(frame["cv_active"] for frame in frames)
    supported_cv = sum(frame["cv_active"] and frame["supported"] for frame in frames)
    if supported_cv == 0:
        raise ValueError(f"Safety control lacks supported CV evidence: {cv_frames}/{len(frames)} physical ticks")
    max_descent = 0.
    peak = frames[0]["position_cm"][1]
    for frame in frames:
        height = frame["position_cm"][1]
        if frame["supported"] and not (frame["in_air"] or frame["falling"]):
            max_descent = max(max_descent, peak - height)
            peak = height
        else:
            peak = max(peak, height)
            max_descent = max(max_descent, peak - height)
    swim = sum(frame["swimming"] for frame in frames)
    wet = sum(frame["water_depth_cm"] > 10 for frame in frames)
    hazard = fixture["hazard_cm"]
    closest = min(math.hypot(frame["position_cm"][0] - hazard[0], frame["position_cm"][2] - hazard[2]) for frame in frames)
    start = frames[0]["position_cm"]
    movement = max(math.hypot(frame["position_cm"][0] - start[0], frame["position_cm"][2] - start[2]) for frame in frames)
    # A goal may be deliberately unreachable. Stopping before the witnessed
    # boundary is safe; success means no unsafe descent or swimming, not arrival.
    fell = max_descent > CV_STEP_CM
    safe = not fell and swim == 0 and wet == 0 and not metadata.get("final_dead") and not metadata.get("final_down")
    return dict(label=fixture["label"], kind=fixture["kind"], link_id=fixture["link_id"],
                safe=safe, unsafe_descent=fell, max_unsupported_descent_cm=max_descent,
                swimming_frames=swim, entered_water_frames=wet,
                max_water_depth_cm=max(frame["water_depth_cm"] for frame in frames),
                physical_frames=len(frames), cv_frames=cv_frames, supported_cv_frames=supported_cv,
                closest_hazard_cm=closest, max_displacement_cm=movement,
                initial_hazard_distance_cm=fixture["start_to_hazard_cm"],
                goal_success=metadata["success"], elapsed_ms=metadata["elapsed_ms"], failure=metadata.get("failure"))


def run(options):
    if sys.platform != "linux":
        raise ValueError("Safety episodes require Ryzen/Linux inside caller-owned gq batch")
    v3 = module("nav_teacher_v3")
    output = options.task / "safety"
    output.mkdir(parents=True, exist_ok=True)
    args = SimpleNamespace(output=output, runner=options.runner, assets=options.assets, save=options.save,
        cache=options.cache, links=options.links, roads=options.roads, npc=options.npc, ini=None,
        seed=19102026, physics="cv", watchdog_seconds=900, limit_ms=options.limit_ms)
    v3.checked_paths(args)
    height_file = output / "height-fixtures.txt"
    height_file.write_text("\n".join(height_candidates(options.routes, options.links, options.records)) + "\n")
    shores_file = output / "shore-seeds.txt"
    shores_file.write_text("".join(label + " " + " ".join(map(str, center)) + "\n" for label, center in SHORES))
    source_identity = dict(runner_sha256=digest(options.runner), routes_sha256=digest(options.routes),
                           links_sha256=digest(options.links), mode=options.mode, limit_ms=options.limit_ms)
    runner = v3.new_runner(args)
    try:
        for name, command, source in (("height", "perception-hazards", height_file), ("shore", "perception-shores", shores_file)):
            v3.runner_scenario(args, runner, f"safety-{name}-prepare", [f"navdemos {command} {source}"], output / name, args.seed)
        fixtures = select_fixtures(rows(output / "height/perception-hazards.jsonl") + rows(output / "shore/perception-hazards.jsonl"))
        results = []
        for fixture in fixtures:
            directory = output / "episodes" / fixture["label"]
            identity = dict(source_identity, fixture_sha256=hashlib.sha256(
                json.dumps(fixture, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
            resumed = (directory / "episode.json").is_file()
            if not resumed:
                commands = ["navdemos record off", "navdemos oracle off", f"navdemos perception-mode {options.mode}",
                    "navdemos perception-monitor on",
                    f"navdemos start-v3 {args.npc} {token(fixture['projected_start_cm'])} {token(fixture['goal_cm'])} {args.limit_ms}",
                    "navdemos wait"]
                v3.runner_scenario(args, runner, fixture["label"], commands, directory, args.seed)
            if (directory / "samples.bin.gz").exists():
                raise ValueError("Safety evaluation unexpectedly recorded demonstration samples")
            metadata = json.loads((directory / "episode.json").read_text())
            if (metadata.get("start") != token(fixture["projected_start_cm"]) or
                    metadata.get("goal") != token(fixture["goal_cm"]) or metadata.get("teacher_version") != 3 or
                    metadata.get("oracle") is not False or metadata.get("navigation_memory_version") != 2 or
                    metadata.get("perception_mode") != options.mode or
                    (resumed and metadata.get("perception_safety_identity") != identity)):
                raise ValueError(f"Safety episode identity/memory version mismatch: {directory}")
            if not resumed:
                metadata["perception_safety_identity"] = identity
                write_json(directory / "episode.json", metadata)
            measured = assess(fixture, metadata, rows(directory / "perception-safety-frames.jsonl"))
            results.append(measured)
            print(json.dumps(measured), flush=True)
        result = dict(episodes=results, by_kind={kind: dict(attempts=sum(r["kind"] == kind for r in results),
            safe=sum(r["kind"] == kind and r["safe"] for r in results),
            swimming_episodes=sum(r["kind"] == kind and r["swimming_frames"] > 0 for r in results),
            entered_water_episodes=sum(r["kind"] == kind and r["entered_water_frames"] > 0 for r in results),
            fall_episodes=sum(r["kind"] == kind and r["unsafe_descent"] for r in results)) for kind in KINDS},
            recordings_created=False, complete=True)
        write_json(output / "metrics.json", result)
        if not all(row["safe"] for row in results):
            raise RuntimeError(f"Safety control failed: {result['by_kind']}")
        return result
    finally:
        runner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("task", "runner", "assets", "save", "cache", "links", "roads", "routes", "records"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--npc", default="BAU_954_MALETH")
    parser.add_argument("--mode", choices=("gaze", "rows"), default="gaze")
    parser.add_argument("--limit-ms", type=int, default=30000)
    options = parser.parse_args()
    if any(not getattr(options, name).is_absolute() for name in ("task", "runner", "assets", "save", "cache", "links", "roads", "routes", "records")):
        parser.error("All input/output paths must be absolute")
    if not 1000 <= options.limit_ms <= 60000:
        parser.error("Safety episode limit must be 1000..60000 ms")
    run(options)


if __name__ == "__main__":
    main()
