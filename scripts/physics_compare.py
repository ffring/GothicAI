#!/usr/bin/env python3
"""Compare replayed public DynamicWorld queries from physics dump (stage 1 oracle)."""

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import re


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values, fraction):
    if not values:
        return None
    values.sort()
    return values[int(fraction * (len(values) - 1))]


def timings(path):
    pattern = re.compile(r"physics timing (\w+) count (\d+) batch (\d+) ns/query p50 ([\d.]+) p95 ([\d.]+)")
    result = {}
    for match in pattern.finditer((path / "bench.log").read_text()):
        kind, count, batch, p50, p95 = match.groups()
        result[kind] = {"count": int(count), "batch": int(batch), "p50_ns_per_query": float(p50), "p95_ns_per_query": float(p95)}
    return result


def strict_result(raw):
    def reject(value):
        raise ValueError("nonfinite JSON constant: " + value)
    row = json.loads(raw, parse_constant=reject)
    if type(row["hit"]) is not bool or type(row["id"]) is not int:
        raise ValueError("hit must be boolean and id must be integer")
    for field in ("point", "normal"):
        if len(row[field]) != 3 or not all(type(v) in (int, float) and math.isfinite(v) for v in row[field]):
            raise ValueError("invalid vector: " + field)
    if type(row["fraction"]) not in (int, float) or not math.isfinite(row["fraction"]):
        raise ValueError("invalid fraction")
    if type(row["material"]) is not int or not isinstance(row["sector"], str):
        raise ValueError("invalid material/sector")
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bullet", type=Path)
    parser.add_argument("jolt", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--point-tolerance-cm", type=float, default=0.1)
    parser.add_argument("--normal-tolerance", type=float, default=0.001)
    parser.add_argument("--kind", choices=("ray", "sphere"), help="early diagnostic of one completed query kind")
    parser.add_argument("--expected-count", type=int, default=100000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    identical_inputs = sha(args.bullet / "physics-queries.tsv") == sha(args.jolt / "physics-queries.tsv")
    if not identical_inputs:
        raise SystemExit("query inputs differ: backend outputs are not comparable")
    groups = {}
    differences = []
    queries = [list(map(float, line.split())) for line in (args.bullet / "physics-queries.tsv").read_text().splitlines()]
    if len(queries) != args.expected_count or any(len(q) != 7 or not all(math.isfinite(v) for v in q) or q[6] <= 0 for q in queries):
        raise SystemExit("query input must contain the expected number of seven finite values, with positive radius")
    manifests = [json.loads((path.parent / "manifest.json").read_text()) for path in (args.bullet, args.jolt)]
    if [m.get("backend") for m in manifests] != ["bullet", "jolt"]:
        raise SystemExit("parent manifests must identify bullet then jolt")
    for field in ("save_sha256", "assets", "mode"):
        if not manifests[0].get(field) or manifests[0][field] != manifests[1].get(field):
            raise SystemExit("parent manifest provenance differs or is missing: " + field)
    if any(not m.get("runner_sha256") for m in manifests):
        raise SystemExit("runner provenance missing")
    if not args.kind and any(not m.get("scenarios", {}).get("queries", {}).get("pass") for m in manifests):
        raise SystemExit("full oracle requires completed successful query scenarios")
    consumed = 0
    with (args.bullet / "physics-results.jsonl").open() as a, (args.jolt / "physics-results.jsonl").open() as b:
        for raw_a, raw_b in itertools.zip_longest(a, b):
            if raw_a is None or raw_b is None:
                raise SystemExit("result row counts differ")
            x, y = strict_result(raw_a), strict_result(raw_b)
            expected = ("ray" if consumed < len(queries) else "sphere", consumed % len(queries))
            consumed += 1
            if (x["kind"], x["id"]) != expected or consumed > 2 * len(queries):
                raise SystemExit("missing, duplicate, out-of-order or excess query result")
            if (x["kind"], x["id"]) != (y["kind"], y["id"]):
                raise SystemExit("query ordering differs")
            if args.kind and x["kind"] != args.kind:
                continue
            q = queries[x["id"]]
            direction = "vertical" if q[0] == q[3] and q[2] == q[5] else "oblique"
            keys = [x["kind"], x["kind"] + "/" + direction]
            if x["kind"] == "sphere":
                keys.append("sphere/radius=" + format(q[6], "g") + "cm")
            bad = []
            point = normal = None
            if x["hit"] != y["hit"]:
                bad.append("hit")
            if x["hit"] and y["hit"]:
                point = math.dist(x["point"], y["point"])
                normal = math.dist(x["normal"], y["normal"])
                if not math.isfinite(point) or point > args.point_tolerance_cm:
                    bad.append("point")
                if not math.isfinite(normal) or normal > args.normal_tolerance:
                    bad.append("normal")
                    # Explicit user choice for stage 1: native Jolt normals are retained for
                    # 1–10 mm spheres; Bullet's GJK/EPA noise remains visible in raw counts.
                    bad.append("normal_exempt_small_sphere" if x["kind"] == "sphere" and 0.1 <= q[6] <= 1 else "normal_nonexempt")
                if x["material"] != y["material"]:
                    bad.append("material")
                if x["sector"] != y["sector"]:
                    bad.append("sector")
            for key in keys:
                group = groups.setdefault(key, {"queries": 0, "bullet_hits": 0, "jolt_hits": 0, "both_hits": 0, "mismatches": {}, "point_distances_cm": [], "normal_distances": []})
                group["queries"] += 1
                group["bullet_hits"] += int(x["hit"])
                group["jolt_hits"] += int(y["hit"])
                if point is not None:
                    group["both_hits"] += 1
                    group["point_distances_cm"].append(point)
                    group["normal_distances"].append(normal)
                for field in bad:
                    group["mismatches"][field] = group["mismatches"].get(field, 0) + 1
            if bad and len(differences) < 1000:
                differences.append({"kind": x["kind"], "id": x["id"], "direction": direction, "query": q, "fields": bad, "point_distance_cm": point, "normal_distance": normal, "bullet": x, "jolt": y})
            if args.kind and x["id"] == len(queries) - 1:
                break
    if not args.kind and consumed != 2 * len(queries):
        raise SystemExit("full oracle requires exactly two complete result sets")
    for group in groups.values():
        for field in ("point_distances_cm", "normal_distances"):
            values = group.pop(field)
            group[field] = {"p50": percentile(values, 0.5), "p95": percentile(values, 0.95), "p999": percentile(values, 0.999), "max": max(values) if values else None}
        mismatches = group["mismatches"]
        group["point_match_fraction"] = 1 - (mismatches.get("point", 0) + mismatches.get("hit", 0)) / group["queries"]
        group["pass"] = group["point_match_fraction"] >= 0.999 and not any(mismatches.get(k, 0) for k in ("hit", "material", "sector", "normal_nonexempt"))
    timing = {"bullet": timings(args.bullet), "jolt": timings(args.jolt)}
    selected = [args.kind] if args.kind else ["ray", "sphere"]
    if any(groups.get(k, {}).get("queries") != len(queries) for k in selected):
        raise SystemExit("selected query kind is incomplete")
    selected_pass = all(groups[k]["pass"] for k in selected)
    diagnostic = args.kind is not None or args.expected_count != 100000 or any(m.get("scope") == "diagnostic_subset" for m in manifests)
    provenance = [{key: m[key] for key in ("backend", "runner_sha256", "save_sha256", "assets", "mode")} for m in manifests]
    result = {"schema_version": 1, "scope": "diagnostic_partial" if diagnostic else "full_oracle", "selected_kind": args.kind, "provenance": provenance, "query_input_sha256": sha(args.bullet / "physics-queries.tsv"), "point_tolerance_cm": args.point_tolerance_cm, "normal_tolerance": args.normal_tolerance, "groups": groups, "timings": timing, "query_parity_pass": None if diagnostic else selected_pass, "selected_kind_pass": selected_pass, "timing_scope": "warm batches of 256 queries; excludes file IO; not physics simulation/frame timing", "saved_difference_examples": len(differences)}
    ray = groups.get("ray")
    result["document_ray_gate_pass"] = None if diagnostic or ray is None else ray["point_match_fraction"] >= 0.999 and not any(ray["mismatches"].get(k, 0) for k in ("material", "sector"))
    result["small_sphere_normal_policy"] = "User accepted native Jolt normals for 0.1<=radius<=1cm (1–10mm); raw normal differences remain reported. Points, hits, material and sector receive no exception."
    (args.output / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    with (args.output / "differences.jsonl").open("w") as file:
        for row in differences:
            file.write(json.dumps(row) + "\n")
    title = "Physics query comparison (partial diagnostic)" if diagnostic else "Physics query comparison"
    gate = "Partial diagnostic: no full document gate is evaluated." if diagnostic else f"Document ray gate: **{result['document_ray_gate_pass']}** (at least 99.9% within {args.point_tolerance_cm}cm, all materials/sectors). Full ray+sphere parity: **{result['query_parity_pass']}**."
    lines = ["# " + title, "", f"Inputs: `{result['query_input_sha256']}`", "", gate, "", result["small_sphere_normal_policy"], "", "| Query | Count | Hit mismatch | Point mismatch | Raw normal mismatch | Exempt normal | Nonexempt normal | Material mismatch | Sector mismatch | Point match |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key, group in groups.items():
        m = group["mismatches"]
        values = " | ".join(str(m.get(field, 0)) for field in ("hit", "point", "normal", "normal_exempt_small_sphere", "normal_nonexempt", "material", "sector"))
        lines.append(f"| {key} | {group['queries']} | {values} | {group['point_match_fraction']:.6%} |")
    lines.extend(["", "Timing measures warmed query batches, excluding output writes. It does not measure simulation/frame cost.", "", "| Query | Bullet p50 ns | Jolt p50 ns | Bullet p95 ns | Jolt p95 ns |", "|---|---:|---:|---:|---:|"])
    for key in selected:
        x, y = timing["bullet"][key], timing["jolt"][key]
        lines.append(f"| {key} | {x['p50_ns_per_query']:.1f} | {y['p50_ns_per_query']:.1f} | {x['p95_ns_per_query']:.1f} | {y['p95_ns_per_query']:.1f} |")
    (args.output / "comparison.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"query_parity_pass": result["query_parity_pass"], "groups": {key: value["mismatches"] for key, value in groups.items()}}, indent=2))
    return 0 if selected_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
