#!/usr/bin/env python3
"""Stage 9: what a policy does after it takes a hit, from collector runs with --keep-journals.

Usage: body_rl_after_hit.py NAME=DIR [NAME=DIR ...]. For the primary actor of every episode:
its attacks (front/left/right) and the share cut by the opponent's hit (hit_reaction), the first
decision after each such hit, and whether the next attack after it is cut as well. A sidestep
(strafe) is the move that leaves a pocket next to an obstacle; see
reports/npc-body/2026-10-16-wall-duel. Measurement only.
"""
from __future__ import annotations

from collections import Counter
import glob
import json
from pathlib import Path
import sys

NAMES = ("continue", "approach", "turn", "strafe_left", "strafe_right", "jump_back",
         "attack_front", "attack_left", "attack_right", "block", "wait_200", "wait_300")
GROUPS = {"strafe_left": "strafe", "strafe_right": "strafe", "wait_200": "wait", "wait_300": "wait",
          "attack_front": "attack", "attack_left": "attack", "attack_right": "attack"}


def episode(folder: Path, totals: Counter, after: Counter) -> None:
    rows = [json.loads(line) for line in (folder/"body-control-events.jsonl").read_text().splitlines() if line]
    rows = [row for row in rows if row.get("entity_id") == 1 and row.get("phase") in ("accepted", "completed", "cancelled")]
    accepted = {row["command_id"]: row for row in rows if row["phase"] == "accepted"}
    ends = {row["command_id"]: row for row in rows if row["phase"] in ("completed", "cancelled")}
    cut = lambda command: command in ends and ends[command]["phase"] == "cancelled" and ends[command].get("reason") == "hit_reaction"
    order = sorted(accepted.values(), key=lambda row: (row["t_game_ms"], row["command_id"]))
    for row in order:
        if NAMES[row["action_id"]].startswith("attack"):
            totals["attacks"] += 1
            totals["attacks_cut"] += cut(row["command_id"])
    for row in order:
        if not cut(row["command_id"]):
            continue
        hit_ms = ends[row["command_id"]]["t_game_ms"]
        later = [item for item in order if item["t_game_ms"] > hit_ms or
                 (item["t_game_ms"] == hit_ms and item["command_id"] > row["command_id"])]
        if not later:
            continue
        totals["hits"] += 1
        name = NAMES[later[0]["action_id"]]
        after[GROUPS.get(name, name)] += 1
        attack = next((item for item in later if NAMES[item["action_id"]].startswith("attack")), None)
        if attack is not None and attack["command_id"] in ends:
            totals["next_attacks"] += 1
            totals["next_attacks_cut"] += cut(attack["command_id"])


def main() -> int:
    for argument in sys.argv[1:]:
        name, _, root = argument.partition("=")
        totals, after = Counter(), Counter()
        for folder in sorted(glob.glob(root+"/worker-*/ep-*")):
            if not folder.endswith(".npz"):
                episode(Path(folder), totals, after)
        hits = max(1, totals["hits"])
        share = lambda key: f"{100*after[key]/hits:.0f}%"
        print(f"{name:14s} attacks {totals['attacks']:5d}, cut {100*totals['attacks_cut']/max(1, totals['attacks']):4.1f}% | "
              f"after {totals['hits']:3d} hits: strafe {share('strafe')}, block {share('block')}, attack {share('attack')}, "
              f"wait {share('wait')}, approach {share('approach')}, jump back {share('jump_back')}, turn {share('turn')} | "
              f"next attack cut {totals['next_attacks_cut']}/{totals['next_attacks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
