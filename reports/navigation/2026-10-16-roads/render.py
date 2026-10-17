#!/usr/bin/env python3
"""Verify the saved runner paths and render their familiar road subsets."""
import argparse
import heapq
import json
import math
from pathlib import Path
from statistics import mean, median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    graph = json.loads((args.raw / "roads-graph.json").read_text())
    nodes, edges = graph["nodes"], graph["edges"]
    snapshots = rows(args.raw / "roads-snapshots.jsonl")
    by_tag = {s["tag"]: s for s in snapshots}
    checked_edges = 0
    for snapshot in snapshots:
        known = set(snapshot["known_edge_ids"])
        assert len(known) == snapshot["known_edges"]
        assert len(set(snapshot["known_node_ids"])) == snapshot["known_nodes"]
        if snapshot["stranger"]:
            assert all(edges[e][3] for e in known), "stranger learned a minor edge"
        if "last_path" not in snapshot:
            continue
        route = snapshot["last_path"]
        assert route["unknown_edge_count"] == 0
        assert set(route["edges"]) <= known, "path uses an unknown edge"
        assert len(route["nodes"]) == len(route["edges"]) + 1
        for a, b, edge_id in zip(route["nodes"], route["nodes"][1:], route["edges"]):
            assert set(edges[edge_id][:2]) == {a, b}
        assert math.isclose(sum(edges[e][2] for e in route["edges"]) / 100,
                            route["length_m"], abs_tol=1e-5)
        checked_edges += len(route["edges"])
        # Independent reachability and straight-distance frontier check.
        adjacent = {}
        for e in known:
            a, b, length, _ = edges[e]
            adjacent.setdefault(a, []).append((b, length))
            adjacent.setdefault(b, []).append((a, length))
        start = route["nodes"][0]
        distances, queue = {start: 0}, [(0, start)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node]:
                continue
            for other, length in adjacent.get(node, []):
                candidate = distance + length
                if candidate < distances.get(other, math.inf):
                    distances[other] = candidate
                    heapq.heappush(queue, (candidate, other))
        assert math.isclose(distances[route["nodes"][-1]] / 100, route["length_m"], abs_tol=1e-5)

    paths = rows(args.raw / "roads-paths.jsonl")
    local, stranger = by_tag["local"], by_tag["stranger"]
    assert paths[0]["goal_cm"] == paths[1]["goal_cm"]
    assert paths[0]["start_cm"] == paths[1]["start_cm"]
    target = paths[0]["goal_cm"]
    for snapshot in (local, stranger):
        route = snapshot["last_path"]
        known = set(snapshot["known_edge_ids"])
        reached, pending = {route["nodes"][0]}, [route["nodes"][0]]
        adjacent = {}
        for e in known:
            a, b = edges[e][:2]
            adjacent.setdefault(a, []).append(b)
            adjacent.setdefault(b, []).append(a)
        while pending:
            for other in adjacent.get(pending.pop(), []):
                if other not in reached:
                    reached.add(other)
                    pending.append(other)
        closest = min(math.dist(nodes[n][1:], target) / 100 for n in reached)
        assert math.isclose(closest, route["remaining_m"], abs_tol=1e-5)
    before, after = by_tag["lobart-before"], by_tag["lobart-after"]
    assert after["last_path"]["remaining_m"] < before["last_path"]["remaining_m"]
    assert set(before["known_edge_ids"]) <= set(after["known_edge_ids"])

    timing = rows(args.raw / "roads-timing.jsonl")
    assert len({r["instance"] for r in timing}) == 50
    def distribution(field):
        values = sorted(r[field] for r in timing)
        return dict(samples=len(values), median_us=median(values),
                    p95_us=values[math.ceil(.95 * len(values)) - 1], mean_us=mean(values))
    summary = dict(nodes=len(nodes), edges=len(edges), major_edges=sum(e[3] for e in edges),
                   checked_path_edges=checked_edges, unknown_edges=0,
                   knowledge=[{k: s[k] for k in ("tag", "actor", "known_nodes", "known_edges",
                                                 "node_fraction", "edge_fraction")} for s in snapshots],
                   paths=paths, build=distribution("build_us"), query=distribution("path_us"))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    def segment(edge_id):
        a, b = edges[edge_id][:2]
        return [(nodes[n][1] / 100, nodes[n][3] / 100) for n in (a, b)]
    colors = ("#008fa8", "#dc7926")
    for filename, city in (("world.png", False), ("city.png", True)):
        fig, axes = plt.subplots(1, 2, figsize=(15, 8), layout="constrained")
        for axis, snapshot, color, title in zip(axes, (local, stranger), colors,
                                                ("Local: Lares", "Stranger at city gate")):
            axis.scatter([n[1] / 100 for n in nodes], [n[3] / 100 for n in nodes],
                         color="#b9bec5", s=1.5, alpha=.45, zorder=1)
            axis.add_collection(LineCollection([segment(e) for e in range(len(edges))],
                                               colors="#b9bec5", linewidths=.55, zorder=1))
            axis.add_collection(LineCollection([segment(e) for e in snapshot["known_edge_ids"]],
                                               colors=color, linewidths=1.6, alpha=.65, zorder=2))
            full = paths[0]["full_network"]
            axis.add_collection(LineCollection([segment(e) for e in full["edges"]],
                                               colors="#272b31", linewidths=1.5, linestyles="dotted", zorder=3))
            route = snapshot["last_path"]
            axis.add_collection(LineCollection([segment(e) for e in route["edges"]],
                                               colors=color, linewidths=3.7, zorder=4))
            endpoint = nodes[route["nodes"][-1]]
            axis.plot([endpoint[1] / 100, target[0] / 100],
                      [endpoint[3] / 100, target[2] / 100], "--", color="#b63b50", lw=2, zorder=5)
            origin = paths[0]["start_cm"]
            axis.scatter([origin[0] / 100], [origin[2] / 100], marker="o", color="#252b34", s=40, zorder=6)
            axis.scatter([target[0] / 100], [target[2] / 100], marker="*", color="#b63b50", s=130, zorder=6)
            axis.set_title(f"{title}\n{snapshot['known_nodes']} nodes / {snapshot['known_edges']} edges; "
                           f"route {route['length_m']:.2f} m + eyes {route['remaining_m']:.2f} m")
            axis.set_aspect("equal")
            axis.set_xlabel("world X, m")
            axis.set_ylabel("world Z, m")
            axis.grid(alpha=.15)
            if city:
                axis.set_xlim(-35, 115)
                axis.set_ylim(-85, 50)
            else:
                axis.autoscale()
        fig.suptitle("Grey: complete WayMatrix; colour: familiar roads and route; "
                     "black dots: full-network route; red dash: continue by sight", fontsize=11)
        fig.savefig(args.output / filename, dpi=150)
        plt.close(fig)
    print(json.dumps({k: summary[k] for k in ("nodes", "edges", "major_edges", "checked_path_edges",
                                            "unknown_edges", "build", "query")}, indent=2))


if __name__ == "__main__":
    main()
