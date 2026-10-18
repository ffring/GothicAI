#!/usr/bin/env python3
"""Summarize carrot demonstrations and draw four top-down examples on Ryzen.

Run inside an admitted gq job. Metadata includes failed and incomplete episodes;
only the four plotted examples stream sensor records, in chunks of 32. Historical
--baseline data is read only through metadata and file names, never rewritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import statistics

import numpy as np


REGIONS = ("market", "harbour", "onar", "lobart", "port_streets", "upper_streets")
BASELINE_COUNTS = dict(zip(REGIONS, ((27, 34), (28, 34), (29, 34), (30, 34), (29, 34), (13, 21))))
REQUIRED = ("episode.json", "trajectory.txt", "actions.txt", "samples.bin.gz")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def episode_root(source):
    return source / "episodes" if (source / "episodes").is_dir() else source


def collect_metadata(source, expected=None, version=None):
    """Keep every expected slot; missing/incomplete output is an explicit failure."""
    root = episode_root(source)
    directories = {int(path.name): path for path in root.iterdir()
                   if path.is_dir() and path.name.isdecimal()}
    ids = sorted(set(directories) | set(range(expected or 0)))
    episodes = []
    for index in ids:
        directory = directories.get(index, root / f"{index:03d}")
        problems, metadata = [], {}
        try:
            metadata = read_json(directory / "episode.json")
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be an object")
        except (OSError, ValueError) as exc:
            problems.append(f"metadata: {exc}")
            metadata = {}
        missing = [name for name in REQUIRED if not (directory / name).is_file()]
        if version == 2 and not (directory / "known-paths.jsonl").is_file():
            missing.append("known-paths.jsonl")
        if missing:
            problems.append("missing: " + ", ".join(missing))
        pair = metadata.get("pair", index)
        if type(pair) is not int or pair < 0:
            problems.append("invalid pair ID")
            pair = index
        if pair != index:
            problems.append(f"pair ID {pair} differs from directory ID {index}")
        if type(metadata.get("success")) is not bool:
            problems.append("missing success bit")
        for key in ("region", "start", "goal"):
            if not isinstance(metadata.get(key), str) or not metadata[key]:
                problems.append(f"missing {key}")
        elapsed = metadata.get("elapsed_ms")
        if type(elapsed) not in (int, float) or not np.isfinite(elapsed) or elapsed <= 0:
            problems.append("invalid elapsed_ms")
            elapsed = 240000
        if version is not None and metadata.get("demo_version") != version:
            problems.append(f"demo_version must be {version}")
        decisions = metadata.get("decisions", 0)
        if type(decisions) is not int or decisions < 0:
            problems.append("invalid decisions")
            decisions = 0
        episodes.append(dict(pair=pair, directory=str(directory),
                             region=metadata.get("region", "unknown"),
                             start=metadata.get("start"), goal=metadata.get("goal"),
                             success=metadata.get("success") is True and not problems,
                             metadata_success=metadata.get("success"),
                             elapsed_s=elapsed / 1000, decisions=decisions,
                             failure=metadata.get("failure", ""), problems=problems,
                             target_position=metadata.get("target_position")))
    pair_ids = [ep["pair"] for ep in episodes]
    if len(set(pair_ids)) != len(pair_ids):
        raise ValueError(f"duplicate pair IDs in {source}")
    return episodes


def group_metrics(episodes):
    successful = [ep for ep in episodes if ep["success"]]
    median = lambda values: statistics.median(values) if values else None
    return dict(episodes=len(episodes), successes=len(successful),
                incomplete=sum(bool(ep["problems"]) for ep in episodes),
                decisions=sum(ep["decisions"] for ep in episodes),
                median_elapsed_s=median([ep["elapsed_s"] for ep in episodes]),
                median_success_elapsed_s=median([ep["elapsed_s"] for ep in successful]),
                failures=dict(Counter("incomplete" if ep["problems"] else ep["failure"] or "unspecified"
                                      for ep in episodes if not ep["success"])))


def metrics(episodes):
    regions = sorted({ep["region"] for ep in episodes}, key=lambda name: (
        REGIONS.index(name) if name in REGIONS else len(REGIONS), name))
    return dict(all=group_metrics(episodes), by_region={
        region: group_metrics([ep for ep in episodes if ep["region"] == region]) for region in regions})


def known_paths(directory):
    paths = []
    with (directory / "known-paths.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            value = json.loads(line)
            points = np.asarray(value["points_cm"], dtype=np.float64)
            if not points.size:
                points = np.empty((0, 3))
            if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
                raise ValueError(f"invalid known path in {directory}")
            paths.append((value["elapsed_ms"], points))
    return paths


def trajectory(directory):
    rows = np.loadtxt(directory / "trajectory.txt", ndmin=2)
    if rows.shape[1] != 5 or not len(rows) or not np.isfinite(rows).all():
        raise ValueError(f"invalid trajectory in {directory}")
    return rows[:, [1, 3]] / 100


def body_to_world(direction, yaw_deg):
    """Inverse of navdemos local(): body x=right,z=forward; yaw0 is world +X."""
    yaw = np.deg2rad(np.asarray(yaw_deg, dtype=np.float64))
    c, s = np.cos(yaw), np.sin(yaw)
    right, up, forward = np.moveaxis(np.asarray(direction, dtype=np.float64), -1, 0)
    return np.stack((forward * c - right * s, up, forward * s + right * c), axis=-1)


def load_demos():
    path = Path(__file__).resolve().with_name("nav_demos.py")
    specification = importlib.util.spec_from_file_location("nav_demos_report", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def example_candidates(episodes):
    candidates, errors = [], []
    for ep in sorted(episodes, key=lambda item: (not item["success"], item["pair"])):
        if ep["problems"]:
            continue
        directory = Path(ep["directory"])
        try:
            paths = known_paths(directory)
            if not paths or max(len(points) for _, points in paths) < 3:
                continue
            route = trajectory(directory)
            if np.max(np.linalg.norm(route - route[0], axis=1)) < 5:
                continue
        except (OSError, ValueError, KeyError) as exc:
            errors.append(dict(pair=ep["pair"], error=str(exc)))
            continue
        candidates.append(ep)
    selected, used_regions = [], set()
    for ep in candidates:
        if ep["region"] not in used_regions:
            selected.append(ep)
            used_regions.add(ep["region"])
        if len(selected) == 4:
            break
    for ep in candidates:
        if len(selected) == 4:
            break
        if ep not in selected:
            selected.append(ep)
    return selected, errors


def plot_examples(episodes, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    selected, errors = example_candidates(episodes)
    demos = load_demos()
    figure, axes = plt.subplots(2, 2, figsize=(12, 10), layout="constrained")
    for ax, ep in zip(axes.flat, selected):
        directory = Path(ep["directory"])
        route, paths = trajectory(directory), known_paths(directory)
        for index, (_, points) in enumerate(paths):
            if not len(points):
                continue
            ax.plot(points[:, 0] / 100, points[:, 2] / 100, "--", color="#219653",
                    linewidth=1.5, alpha=max(.25, 1 / len(paths)),
                    label="Familiar path" if index == 0 else None)
        carrots = []
        offset = 0
        for records in demos.iter_chunks(directory, chunk_records=32):
            if "carrot_direction" not in records["roads"].dtype.names:
                raise ValueError(f"plot requires NAVDEMO v2: {directory}")
            # One carrot per second; selection uses absolute decision index so
            # chunk boundaries do not change the plot.
            mask = (np.arange(len(records)) + offset) % 10 == 0
            roads = records["roads"][mask]
            delta = roads["carrot_direction"] * roads["carrot_distance_cm"][:, None]
            points = records["position_cm"][mask] + body_to_world(delta, records["body_yaw_deg"][mask])
            carrots.extend(points[:, [0, 2]] / 100)
            offset += len(records)
        if carrots:
            points = np.asarray(carrots)
            ax.scatter(points[:, 0], points[:, 1], s=10, color="#ed8c16", alpha=.65, label="Carrot (1 s)")
        ax.plot(route[:, 0], route[:, 1], color="#2468b4", linewidth=1.5, label="Teacher trajectory")
        ax.scatter(*route[0], marker="o", color="#111111", s=35, label="Start", zorder=5)
        target = ep["target_position"]
        if target is not None:
            ax.scatter(target[0] / 100, target[2] / 100, marker="*", color="#c23728", s=100, label="Goal", zorder=5)
        result = "arrived" if ep["success"] else "failed"
        ax.set_title(f"#{ep['pair']:03d} {ep['region']} · {result}, {ep['elapsed_s']:.1f} s\n"
                     f"{ep['start']} → {ep['goal']}", fontsize=9)
        ax.set(xlabel="World X (m)", ylabel="World Z (m)", aspect="equal")
        for limits, set_limits in ((ax.get_xlim(), ax.set_xlim), (ax.get_ylim(), ax.set_ylim)):
            center = (limits[0] + limits[1]) / 2
            half_span = max(limits[1] - limits[0], 12) / 2
            set_limits(center - half_span, center + half_span)
        ax.grid(alpha=.2)
        ax.legend(fontsize=7, loc="best")
    for ax in list(axes.flat)[len(selected):]:
        ax.text(.5, .5, "No complete example with a familiar path", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
    figure.savefig(output / "trajectories.png", dpi=160)
    plt.close(figure)
    return [ep["pair"] for ep in selected], errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--expected-episodes", type=int, default=204)
    args = parser.parse_args()
    if not args.source.is_absolute() or not args.output.is_absolute() or (args.baseline and not args.baseline.is_absolute()):
        parser.error("all paths must be absolute")
    if args.expected_episodes < 1:
        parser.error("--expected-episodes must be positive")
    if args.baseline and args.output.resolve().is_relative_to(args.baseline.resolve()):
        parser.error("--output cannot be inside the read-only baseline")
    source = collect_metadata(args.source, expected=args.expected_episodes, version=2)
    result = dict(source=str(args.source), expected_episodes=args.expected_episodes, **metrics(source))
    files = [path for path in args.source.rglob("*") if path.is_file()]
    result["storage"] = dict(files=len(files), bytes=sum(path.stat().st_size for path in files),
                             samples_bytes=sum(path.stat().st_size for path in files if path.name == "samples.bin.gz"))
    result["historical_v1"] = dict(all=dict(successes=156, episodes=191), by_region={
        name: dict(successes=values[0], episodes=values[1]) for name, values in BASELINE_COUNTS.items()})
    if args.baseline:
        baseline = collect_metadata(args.baseline)
        current_by_id = {ep["pair"]: ep for ep in source}
        baseline_by_id = {ep["pair"]: ep for ep in baseline}
        common_ids = sorted(current_by_id.keys() & baseline_by_id.keys())
        mismatches = [index for index in common_ids if any(
            current_by_id[index][key] is not None and current_by_id[index][key] != baseline_by_id[index][key]
            for key in ("start", "goal"))]
        if mismatches:
            raise ValueError(f"start/goal mismatch for common pair IDs: {mismatches}")
        result["comparison"] = dict(pair_ids=common_ids,
                                    current=metrics([current_by_id[index] for index in common_ids]),
                                    baseline=metrics([baseline_by_id[index] for index in common_ids]))
    result["episodes"] = source
    args.output.mkdir(parents=True, exist_ok=True)
    result["example_pair_ids"], result["plot_errors"] = plot_examples(source, args.output)
    (args.output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(all=result["all"], storage=result["storage"],
                          example_pair_ids=result["example_pair_ids"]), ensure_ascii=False))
    return int(result["all"]["incomplete"] > 0 or len(source) != args.expected_episodes or
               len(result["example_pair_ids"]) != 4)


if __name__ == "__main__":
    raise SystemExit(main())
