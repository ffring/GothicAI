#!/usr/bin/env python3
"""Stage-0 M1 Pro motor/blade matrix, normal 30 s cadence without video."""
from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
from pathlib import Path
import statistics
import subprocess
import time


ROOT = Path(__file__).resolve().parent.parent


def percentile(values: list[float]) -> float:
    return sorted(values)[math.ceil(len(values)*.95)-1]


def summarize(output: Path) -> dict:
    with (output/"appcadence.csv").open() as stream:
        frames = list(csv.DictReader(stream))
    seconds = sum(int(row["interval_ns"]) for row in frames)/1e9
    if seconds < 30 or not frames:
        raise RuntimeError(f"incomplete 30 s cadence: {output}")
    with (output/"melee-physics.csv").open() as stream:
        samples = [{key: float(value) for key, value in row.items()} for row in csv.DictReader(stream)]
    active = [row for row in samples if row["updates"] > 0]
    if not active or any(row["finite"] != 1 for row in samples):
        raise RuntimeError(f"missing physics steps or nonfinite rig: {output}")
    result = {"frames": len(frames), "seconds": seconds, "fps": len(frames)/seconds,
              "samples": len(samples), "active_samples": len(active),
              "physics_mean_ms": statistics.mean(row["physics_ms"] for row in samples),
              "physics_p95_ms": percentile([row["physics_ms"] for row in samples]),
              "active_physics_mean_ms": statistics.mean(row["physics_ms"] for row in active),
              "active_physics_p95_ms": percentile([row["physics_ms"] for row in active]),
              "solve_mean_ms": statistics.mean(row["solve_ms"] for row in active),
              "solve_p95_ms": percentile([row["solve_ms"] for row in active]),
              "cast_mean_ms": statistics.mean(row["cast_ms"] for row in active),
              "cast_p95_ms": percentile([row["cast_ms"] for row in active])}
    with (output/"melee-physics.csv.steps.csv").open() as stream:
        steps = list(csv.DictReader(stream))
    result["fixed_steps"] = len(steps)
    result["step_mean_ms"] = statistics.mean(float(row["physics_ms"]) for row in steps)
    result["step_p95_ms"] = percentile([float(row["physics_ms"]) for row in steps])
    result["step_cast_mean_ms"] = statistics.mean(float(row["cast_ms"]) for row in steps)
    result["step_cast_p95_ms"] = percentile([float(row["cast_ms"]) for row in steps])
    for key in ("updates", "casts", "body_hits", "world_hits", "floor_samples", "floor_misses"):
        result[key] = int(sum(row[key] for row in samples))
    for key in ("dynamic_parts", "blades", "max_linear_cm_s", "max_angular_rad_s", "pose_rms_cm",
                "pose_max_cm", "max_foot_drop_cm", "max_foot_penetration_cm"):
        result[key] = max(row[key] for row in samples)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--case", action="append", help="one matrix row: count:mode:pelvis:substeps")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    binary = ROOT/"OpenGothic/build/opengothic/Gothic2Notr"
    binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    cases = args.case or [f"{count}:{mode}:{pelvis}:{steps}"
                         for count in (1, 2, 4, 10) for mode in ("full", "chain")
                         for pelvis in ("kinematic", "dynamic") for steps in (1, 2)]
    if args.case is None:
        cases = [f"{count}:base:kinematic:{steps}" for count in (0, 1, 2, 4, 10) for steps in (1, 2)] + cases
        cases += [f"10:blade:kinematic:{steps}" for steps in (1, 2)]
    template = (ROOT/"scripts/bench/melee-physics.bench").read_text()
    for case in cases:
        # Match executable names, since pgrep also finds build command lines.
        while True:
            processes = subprocess.check_output(["ps", "-axo", "pid=,comm="], text=True)
            games = [row for row in processes.splitlines() if row.split()[-1].endswith("/Gothic2Notr")]
            if not games:
                break
            print("Waiting for the other Mac game: "+"; ".join(games), flush=True)
            time.sleep(5)
        count, mode, pelvis, steps = case.split(":")
        take = output/case.replace(":", "-")
        take.mkdir()
        scenario = take/"melee-physics.bench"
        scenario.write_text(template.replace("setup 10 full kinematic 2", f"setup {count} {mode} {pelvis} {steps}"))
        env = {**os.environ, "BENCH_HOST": "local", "NO_BUILD": "1", "WORLD": "NEWWORLD.ZEN",
               "OPENGOTHIC_MELEE_BENCH": "fixture", "GOTHIC_AI": "0", "TIMEOUT": "180"}
        print(f"Running {case}", flush=True)
        with (take/"launcher.log").open("w") as stream:
            subprocess.run([str(ROOT/"scripts/bench.sh"), str(scenario), str(take)],
                           cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, check=True)
        result = {"case": case, "binary_sha256": binary_hash, **summarize(take)}
        if mode == "blade" and (result["blades"] != int(count) or result["casts"] == 0):
            raise RuntimeError(f"missing blade bodies or casts: {case}")
        with (output/"summary.csv").open("a") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(result))
            if stream.tell() == 0:
                writer.writeheader()
            writer.writerow(result)
        print(f"{case}: step p95 {result['step_p95_ms']:.3f} ms, mean {result['step_mean_ms']:.3f} ms, "
              f"{result['fps']:.2f} FPS, pose max {result['pose_max_cm']:.2f} cm", flush=True)


if __name__ == "__main__":
    main()
