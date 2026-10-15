#!/usr/bin/env python3
"""Stage 9.1: a --fork-reset episode must start from the same arena state as a full reset.

Two runners play the same sequence of native arena episodes, one reloading the save on every
reset and one forking a template that loaded it once. For each episode the reset snapshot,
the terminal snapshot and every numeric observation row must match after removing only
identity and wall-clock fields. Several episodes in a row also catch state leaking between
episodes. Measurement only: no quality credit.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from body_observe_suite import ObserverRunner  # noqa: E402

# Identity and wall-clock fields differ by construction; everything else must be equal.
VOLATILE = ("epoch", "generation", "episode_id", "wall", "monotonic", "_ns", "runner_pid", "clock_domain",
            "output_dir", "reset_wall_ms", "elapsed_wall_ms", "max_realtime_lag_ms")
EPISODES = (("native_duel", 701, 0), ("native_duel", 702, 1), ("distance", 703, 0), ("attack", 704, 1),
            ("block", 705, 0), ("native_duel", 706, 1))


def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in sorted(value.items())
                if not any(token in key for token in VOLATILE)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def play(args: argparse.Namespace, name: str, fork: bool) -> list[dict]:
    output = args.output/name
    output.mkdir(parents=True)
    variant = argparse.Namespace(**vars(args))
    variant.output = output
    variant.native_serial_workers = True
    variant.compact_replies = False
    variant.runner_extra_args = ["--fork-reset"] if fork else []
    runner = ObserverRunner(variant)
    episodes = []
    try:
        for index, (task, seed, side) in enumerate(EPISODES):
            folder = output/f"episode-{index}"
            folder.mkdir()
            begin = time.monotonic()
            reset = runner.request({"cmd": "reset", "scenario": str(args.scenario), "output_dir": str(folder),
                                    "mode": "lockstep", "arena": {"scenario": task, "seed": seed, "side": side,
                                                                  "distance_cm": 300, "timeout_ms": args.timeout_ms}})
            reset_s = time.monotonic()-begin
            arena = runner.request({"cmd": "arena_start", "provider": "native"}).get("arena") or {}
            while not (arena.get("terminated") or arena.get("truncated")):
                response = runner.request({"cmd": "arena_step", "substeps": 30})
                arena = response.get("arena") or arena
                if response.get("advanced_substeps", 0) == 0:
                    break
            episodes.append({"task": task, "seed": seed, "side": side, "reset_s": reset_s,
                             "reset_arena": reset.get("arena"), "final_arena": arena, "folder": str(folder)})
        runner.request({"cmd": "close"})
    finally:
        runner.terminate()
    for episode in episodes:
        rows = [json.loads(line) for line in Path(episode["folder"], "observations.jsonl").read_text().splitlines() if line]
        episode["observation_rows"] = rows
    return episodes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "scenario", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=20000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    full, fork = play(args, "full-reset", False), play(args, "fork-reset", True)
    report, issues = [], []
    for index, (a, b) in enumerate(zip(full, fork)):
        checks = {"reset_snapshot": canonical(a["reset_arena"]) == canonical(b["reset_arena"]),
                  "final_snapshot": canonical(a["final_arena"]) == canonical(b["final_arena"]),
                  "observation_rows": len(a["observation_rows"]) == len(b["observation_rows"]) and
                                      all(canonical(x) == canonical(y) for x, y in zip(a["observation_rows"], b["observation_rows"]))}
        for check, ok in checks.items():
            if not ok:
                issues.append(f"episode {index} {a['task']} seed {a['seed']}: {check} differs")
        report.append({"episode": index, "task": a["task"], "seed": a["seed"], "side": a["side"], **checks,
                       "rows": len(a["observation_rows"]), "full_reset_s": a["reset_s"], "fork_reset_s": b["reset_s"],
                       "outcome": (a["final_arena"] or {}).get("outcome")})
        print(f"episode {index} {a['task']:11s} seed {a['seed']}: reset {checks['reset_snapshot']} final {checks['final_snapshot']} "
              f"rows {checks['observation_rows']} ({len(a['observation_rows'])}) reset full {a['reset_s']:.2f}s fork {b['reset_s']:.3f}s", flush=True)
    value = {"schema_version": 1, "kind": "body_fork_reset_equivalence", "quality_credit": False,
             "passed": not issues, "issues": issues, "volatile_fields": list(VOLATILE), "episodes": report}
    (args.output/"check.json").write_text(json.dumps(value, indent=2)+"\n")
    print(json.dumps({"passed": value["passed"], "issues": issues}), flush=True)
    return 0 if value["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
