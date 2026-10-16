#!/usr/bin/env python3
"""Stage 9: does a lockstep run make the same decisions as the realtime driver?

Pairs episodes of a realtime run (body_policy_diagnostics.py --phase realtime) with a lockstep
run of the same cases: the gate driver (--phase screening) or the RL collector with
--keep-journals. Episodes are paired by the native reset snapshot (task, seed, side, distance).
Only files the runner writes are compared: control events of the primary actor (accepted,
started, completed, cancelled commands), every numeric observation row and the terminal arena
snapshot, without identity, wall-clock and mode fields. For each pair the first divergence is
reported with the realtime timing faults before it, which lockstep cannot reproduce: a poll
that saw more than one tick, and a command the engine accepted a tick or more after its
observation. Measurement only: no quality credit.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from body_fork_reset_check import VOLATILE  # noqa: E402

IGNORED = VOLATILE+("mode", "last_heartbeat")
COMMAND_PHASES = ("accepted", "started", "completed", "cancelled")
# Per-process counters (a fork restarts them each episode), the owner label and the command
# TTL are protocol bookkeeping; the decision is time, observation, action and its lifecycle.
COMMAND_BOOKKEEPING = ("event_seq", "execution_id", "provider", "ttl_ms")


def canonical(value):
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in sorted(value.items()) if not any(token in key for token in IGNORED)}
    if isinstance(value, list):
        return [canonical(item) for item in value]
    return value


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


def episode(folder):
    arena = rows(folder/"arena-events.jsonl")
    initial = next(row["arena"] for row in arena if row.get("kind") == "reset_ready")
    final = next((row["arena"] for row in arena if row.get("kind") == "terminal"), None)
    primary = initial["primary_entity_id"]
    commands = [{key: value for key, value in canonical(row).items() if key not in COMMAND_BOOKKEEPING}
                for row in rows(folder/"body-control-events.jsonl")
                if row.get("entity_id") == primary and row.get("phase") in COMMAND_PHASES]
    observations = [canonical(row) for row in rows(folder/"observations.jsonl")]
    return {"key": (initial["scenario"], initial["seed"], initial["side"], initial["distance_cm"]), "folder": str(folder),
            "start_game_ms": final["start_game_ms"] if final else None, "commands": commands,
            "observations": observations, "final": canonical(final)}


def skipped_ticks(folder):
    """Realtime observation polls that advanced more than one native tick after arena_start."""
    pending, started, last, skips = {}, None, None, []
    for row in rows(folder/"ipc.jsonl"):
        value = row["value"]
        if row["direction"] == "request":
            pending[value.get("id")] = value.get("cmd")
            continue
        command, step = pending.get(value.get("id")), value.get("sim_step")
        if command == "arena_start":
            started = step
        elif command == "body_observations" and step is not None and started is not None:
            base = last if last is not None else started
            if step-base > 1:
                skips.append({"sim_step": step, "ticks": step-base, "game_ms": (value.get("arena") or {}).get("game_ms")})
            last = step
    return skips


def late_commands(folder):
    """Realtime commands accepted after the engine had already advanced past their observation."""
    observed = {row["observation_seq"]: row["t_game_ms"] for row in rows(folder/"observations.jsonl")}
    return [{"game_ms": row["t_game_ms"], "observation_game_ms": observed[row["observation_seq"]],
             "action_id": row["action_id"]}
            for row in rows(folder/"body-control-events.jsonl")
            if row.get("phase") == "accepted" and row.get("observation_seq") in observed and
            row["t_game_ms"] > observed[row["observation_seq"]]]


def rejected_commands(folder):
    """Realtime commands the engine refused (e.g. stale_observation: a newer row arrived first); lockstep accepts them."""
    pending, rejected = {}, []
    for row in rows(folder/"ipc.jsonl"):
        value = row["value"]
        if row["direction"] == "request":
            pending[value.get("id")] = value
            continue
        request = pending.get(value.get("id")) or {}
        if request.get("cmd") == "body_action" and value.get("accepted") is False:
            rejected.append({"game_ms": value.get("game_ms"), "action_id": request.get("action_id"), "reason": value.get("reason")})
    return rejected


def first_difference(a, b):
    for index, (left, right) in enumerate(zip(a, b)):
        if left != right:
            keys = sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key)) if isinstance(left, dict) else []
            return {"index": index, "keys": keys[:8], "game_ms": left.get("t_game_ms") if isinstance(left, dict) else None}
    if len(a) != len(b):
        return {"index": min(len(a), len(b)), "keys": ["length"], "game_ms": None}
    return None


def folders(root):
    # Episode folders sit directly in a diagnostics run or under worker-NN in a collector run;
    # symlinked folders (a subset picked for a partial check) are followed.
    return sorted(Path(base) for base, _, files in os.walk(root, followlinks=True) if "arena-events.jsonl" in files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--realtime", type=Path, required=True)
    parser.add_argument("--lockstep", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    realtime = {item["key"]: item for item in map(episode, folders(args.realtime))}
    lockstep = {item["key"]: item for item in map(episode, folders(args.lockstep))}
    keys = sorted(set(realtime) & set(lockstep))
    pairs, totals = [], {"pairs": len(keys), "identical": 0, "identical_commands": 0, "same_outcome": 0,
                         "diverged_after_realtime_fault": 0, "diverged_without_fault": 0,
                         "realtime_skipped_polls": 0, "realtime_late_commands": 0, "realtime_rejected_commands": 0,
                         "realtime_accepted_commands": 0}
    for key in keys:
        a, b = realtime[key], lockstep[key]
        command_diff = first_difference(a["commands"], b["commands"])
        observation_diff = first_difference(a["observations"], b["observations"])
        identical = command_diff is None and observation_diff is None and a["final"] == b["final"]
        skips, late = skipped_ticks(Path(a["folder"])), late_commands(Path(a["folder"]))
        rejected = rejected_commands(Path(a["folder"]))
        diverged_ms = min([diff["game_ms"] for diff in (command_diff, observation_diff) if diff and diff["game_ms"] is not None],
                          default=None)
        explained = (not identical and diverged_ms is not None and
                     any(fault["game_ms"] is not None and fault["game_ms"] <= diverged_ms for fault in skips+late+rejected))
        totals["identical"] += identical
        totals["identical_commands"] += command_diff is None
        totals["same_outcome"] += (a["final"] or {}).get("reason") == (b["final"] or {}).get("reason")
        totals["realtime_skipped_polls"] += len(skips)
        totals["realtime_late_commands"] += len(late)
        totals["realtime_rejected_commands"] += len(rejected)
        totals["realtime_accepted_commands"] += sum(row.get("phase") == "accepted" for row in a["commands"])
        if not identical:
            totals["diverged_after_realtime_fault" if explained else "diverged_without_fault"] += 1
        pairs.append({"key": list(key), "identical": identical, "first_command_difference": command_diff,
                      "first_observation_difference": observation_diff, "final_equal": a["final"] == b["final"],
                      "realtime_reason": (a["final"] or {}).get("reason"), "lockstep_reason": (b["final"] or {}).get("reason"),
                      "commands": [len(a["commands"]), len(b["commands"])],
                      "observations": [len(a["observations"]), len(b["observations"])],
                      "realtime_skipped_ticks": skips, "realtime_late_commands": late, "realtime_rejected_commands": rejected,
                      "divergence_after_realtime_fault": explained})
    value = {"schema_version": 1, "kind": "body_schedule_equivalence", "quality_credit": False,
             "realtime": str(args.realtime), "lockstep": str(args.lockstep), "ignored_fields": list(IGNORED),
             "command_bookkeeping_fields": list(COMMAND_BOOKKEEPING),
             "totals": totals, "pairs": pairs}
    args.output.write_text(json.dumps(value, indent=2)+"\n")
    print(json.dumps(totals), flush=True)
    for pair in pairs:
        if not pair["identical"]:
            print("diverged", pair["key"], "commands", pair["first_command_difference"], "observations",
                  pair["first_observation_difference"], "skips", pair["realtime_skipped_ticks"][:2],
                  "late", pair["realtime_late_commands"][:2], "rejected", pair["realtime_rejected_commands"][:2], flush=True)
    return 0 if totals["identical"] == totals["pairs"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
