#!/usr/bin/env python3
"""Compare the first declared rendered 20-body runs; forced game dt is never a frame-time measurement."""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("nonfinite JSON number: " + value)


def case(path, mode, coverage=False):
    with (path / ("ragdoll-coverage.csv" if coverage else "ragdoll-performance.csv")).open() as file:
        reader = csv.DictReader(file)
        required = {"mode" if coverage else "seg", "frame", "actors", "corpses", "physical", "sleeping", "parts", "centers_in_view",
                    "physical_before_impulse", "sleeping_before_impulse", "visible_meshes" if coverage else "visible_mesh_proxies"}
        if not coverage:
            required |= {"wall_ms", "step_ms", "world_bodies", "rays"}
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or set(reader.fieldnames) != required:
            raise ValueError(mode + ": missing, duplicate or unexpected performance columns")
        rows = list(reader)
    counters = required - {"seg", "mode", "wall_ms", "step_ms"}
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError(mode + ": malformed CSV row")
        for field in counters:
            if not row[field].isascii() or not row[field].isdigit():
                raise ValueError(mode + ": invalid integer counter: " + field)
    if len(rows) != 1020 or [int(row["frame"]) for row in rows] != list(range(1020)):
        raise ValueError(mode + ": expected exactly 120 warmup and 900 measured frames")
    if {row["mode" if coverage else "seg"] for row in rows} != {mode if coverage else "twenty_" + mode}:
        raise ValueError(mode + ": segment identity differs")
    samples = rows if coverage else rows[120:]
    expected = 0 if mode == "disabled" else 20
    expected_parts = 0 if mode == "disabled" else 480
    if any(int(row["parts"]) != expected_parts for row in rows):
        raise ValueError(mode + ": every warmup/measured frame must contain exactly twenty full 24-part humans (or zero parts when disabled)")
    for row in rows:
        for key, value in row.items():
            if key in ("seg", "mode"):
                continue
            number = float(value)
            if not math.isfinite(number) or number < 0:
                raise ValueError(mode + ": nonfinite/negative sample")
        if any(int(row[field]) != 20 for field in ("actors", "corpses", "centers_in_view", "visible_meshes" if coverage else "visible_mesh_proxies")):
            raise ValueError(mode + ": not all twenty corpse centers are inside the camera frustum")
        if int(row["physical"]) != expected or int(row["sleeping"]) != (20 if mode == "sleeping" else 0):
            raise ValueError(mode + ": actual active/sleeping body coverage differs")
        if int(row["physical_before_impulse"]) != expected or int(row["sleeping_before_impulse"]) != (20 if mode == "sleeping" else 0):
            raise ValueError(mode + ": post-world body coverage before controlled wake differs")
        if (int(row["parts"]) > 0) != (expected > 0) or int(row["parts"]) % 20 != 0:
            raise ValueError(mode + ": native parts differ from the declared case")
        if not coverage and int(row["world_bodies"]) < int(row["parts"]):
            raise ValueError(mode + ": total native bodies cannot be less than corpse parts")
    if len({row["parts"] for row in samples}) != 1:
        raise ValueError(mode + ": physical parts changed during the measured interval")
    def quantiles(field):
        values = sorted(float(row[field]) for row in samples)
        if values[0] <= 0:
            raise ValueError(mode + ": positive clock samples are required")
        result = {"p50_ms": values[449], "p95_ms": values[854], "mean_ms": sum(values) / len(values)}
        if field == "wall_ms":
            result["mean_fps_from_monotonic_cycles"] = 1000 / result["mean_ms"]
            result["frames_within_60fps_budget"] = sum(value <= 1000/60 for value in values)
        return result
    if "done" not in (path / "bench.log").read_text().splitlines():
        raise ValueError(mode + ": scenario did not finish")
    events = [json.loads(line, object_pairs_hook=unique_object, parse_constant=reject_constant)
              for line in (path / "events.jsonl").read_text().splitlines()]
    if any(event.get("kind") == "ragdoll_error" for event in events):
        raise ValueError(mode + ": scenario reported a control error")
    down = [event for event in events if event.get("kind") == "ragdoll_down_bulk"]
    if len(down) != 1 or down[0].get("actors") != 20 or down[0].get("down") != 20:
        raise ValueError(mode + ": actual twenty-actor down/schema fixture is missing")
    bodies = down[0].get("bodies")
    if not isinstance(bodies, list) or len(bodies) != 20:
        raise ValueError(mode + ": per-actor full-body schema coverage is absent")
    ids = []
    topology = None
    for body in bodies:
        if type(body["actor_id"]) is not int or body["actor_id"] < 0 or body["symbol"] != "BDT_1013_BANDIT_L" or body["skeleton"].upper() != "HUMANS.MDS":
            raise ValueError(mode + ": actor identity/skeleton differs from the declared human fixture")
        ids.append(body["actor_id"])
        bones = body["bone_indices"]
        count = 0 if mode == "disabled" else 24
        if type(body["part_count"]) is not int or body["part_count"] != count or not isinstance(bones, list) or len(bones) != count or any(type(bone) is not int or bone < 0 for bone in bones) or len(set(bones)) != count:
            raise ValueError(mode + ": actor contains a truncated or malformed native body")
        if topology is None:
            topology = bones
        if bones != topology:
            raise ValueError(mode + ": identical human actors have different native bone topology")
    if len(set(ids)) != 20:
        raise ValueError(mode + ": per-actor full-body fixture contains duplicate actors")
    policy = [event for event in events if event.get("kind") == "ragdoll_benchmark_sleep_policy"]
    if mode == "active":
        if len(policy) != 1 or policy[0].get("actors") != 20 or policy[0].get("allow_sleeping") is not False:
            raise ValueError(mode + ": conservative no-sleep ACTIVE workload metadata is absent")
    elif policy:
        raise ValueError(mode + ": benchmark sleep override must apply only to ACTIVE")
    sites = [event for event in events if event.get("kind") == "ragdoll_arena_site"]
    cameras = [event for event in events if event.get("kind") == "ragdoll_arena_camera"]
    verified = [event for event in events if event.get("kind") == "ragdoll_arena_verified"]
    if len(sites) != 1 or sites[0].get("places") != 20 or len(cameras) != 1 or len(verified) != 1 or verified[0].get("mode") != mode or verified[0].get("visible_meshes") != 20:
        raise ValueError(mode + ": verified arena geometry/model camera proof is absent")
    if coverage:
        complete = [event for event in events if event.get("kind") == "ragdoll_coverage_complete"]
        if len(complete) != 1 or complete[0].get("pass") is not True or complete[0].get("frames") != 1020:
            raise ValueError(mode + ": untimed coverage did not finish successfully")
        models = [event for event in events if event.get("kind") == "ragdoll_coverage_model"]
        if len(models) != 20400:
            raise ValueError(mode + ": per-model posed-envelope coverage is incomplete")
        for frame in range(1020):
            group = models[frame*20:(frame+1)*20]
            if {event.get("actor_id") for event in group} != set(ids) or any(event.get("coverage_frame") != frame for event in group):
                raise ValueError(mode + ": per-model actor/frame coverage differs")
            for model in group:
                if model.get("visible") is not True or model.get("projected_with_margin") is not True or model.get("body_drawable") is not True or model.get("cheap_proxy_visible") is not True or model.get("proxy_contains_envelope") is not True or not math.isfinite(model["model_bound_radius_cm"]) or model["model_bound_radius_cm"] <= 0 or model.get("bone_count") != 34:
                    raise ValueError(mode + ": full loaded model visibility proof differs")
                for key in ("world_min_cm", "world_max_cm", "ndc_min", "ndc_max", "actor_origin_cm"):
                    if not isinstance(model[key], list) or len(model[key]) != 3 or not all(math.isfinite(v) for v in model[key]):
                        raise ValueError(mode + ": invalid per-model bounds")
                if any(model["world_max_cm"][i] <= model["world_min_cm"][i] for i in range(3)) or any(abs(model[key][i]) > .900001 for key in ("ndc_min", "ndc_max") for i in (0, 1)) or model["ndc_min"][2] < 0 or model["ndc_max"][2] > 1:
                    raise ValueError(mode + ": actual envelope projection exceeds declared margin")
                if not math.isfinite(model["proxy_radius_cm"]) or model["proxy_radius_cm"] <= 0 or any(math.dist([model["world_max_cm" if corner & (1 << i) else "world_min_cm"][i] for i in range(3)], model["actor_origin_cm"]) > model["proxy_radius_cm"] + .01 for corner in range(8)):
                    raise ValueError(mode + ": lightweight proxy does not contain full untimed envelope")
        return {"frames": 1020, "physical_parts": expected_parts, "timings_collected": False,
                "arena": sites[0], "camera": cameras[0], "actors": bodies, "model_frames": len(models)}
    return {"frames": 900, "warmup_frames": 120, "physics_step": quantiles("step_ms"),
            "full_cycle": quantiles("wall_ms"), "physical_parts": int(samples[0]["parts"]),
            "sha256": hashlib.sha256((path / "ragdoll-performance.csv").read_bytes()).hexdigest(),
            "arena": sites[0], "camera": cameras[0]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for mode in ("disabled", "active", "sleeping"):
        parser.add_argument("--" + mode, type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--coverage", action="store_true", help="Validate all1020 untimed frames; require no clock columns")
    args = parser.parse_args()
    result = {"schema_version": 1, "pass": False,
              "scope": ("Untimed all1020 model/schema/visibility/awake coverage; no timing quantiles" if args.coverage else
                        "actual monotonic wall interval between post-world bench ticks, including game/render loop; native CollisionWorld tick separately"),
              "frame_budget_rule": "primary: active/sleeping full-cycle p50 and p95 <= disabled (no permitted slowdown); supplemental engineering budget: native physics p95 <= 2 ms",
              "cases": {}}
    try:
        result["cases"] = {mode: case(getattr(args, mode), mode, args.coverage) for mode in ("disabled", "active", "sleeping")}
        # Timestamps differ across setup variants; compare only actual site/camera coordinates.
        for mode in ("active", "sleeping"):
            for key in ("waypoint", "center", "ground_min_cm", "ground_max_cm"):
                if result["cases"][mode]["arena"][key] != result["cases"]["disabled"]["arena"][key]:
                    raise ValueError("matched case arena coordinates differ")
            for key in ("origin", "target", "bounds_min", "bounds_max", "fov_y", "aspect"):
                if result["cases"][mode]["camera"][key] != result["cases"]["disabled"]["camera"][key]:
                    raise ValueError("matched case camera geometry differs")
        if args.coverage:
            result["scope"] = "Untimed all1020 model/schema/visibility/awake coverage; no timing quantiles"
            result["pass"] = True
            args.output.write_text(json.dumps(result, indent=2) + "\n")
            print("PASS")
            return 0
        if result["cases"]["active"]["physical_parts"] != result["cases"]["sleeping"]["physical_parts"]:
            raise ValueError("active/sleeping native part counts differ for the identical twenty actors")
        baseline = result["cases"]["disabled"]["full_cycle"]
        for mode in ("active", "sleeping"):
            item = result["cases"][mode]
            item["frame_ratios"] = {key: item["full_cycle"][key] / baseline[key] for key in ("p50_ms", "p95_ms")}
            item["physics_budget_pass"] = item["physics_step"]["p95_ms"] <= 2
            item["pass"] = all(ratio <= 1 for ratio in item["frame_ratios"].values())
        result["pass"] = all(result["cases"][mode]["pass"] for mode in ("active", "sleeping"))
    except (ValueError, TypeError, KeyError, OSError, OverflowError) as error:
        result["error"] = str(error)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("PASS" if result["pass"] else "FAIL")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
