#!/usr/bin/env python3
"""Stage 9.2: held-out duels in realtime, paired with the same duels played in lockstep.

config   writes a copy of the frozen stage 8 config whose realtime and screening phases are the first
         N held-out duels of the lockstep evaluation (scripts/body_rl_collect.py --seed-start 90000),
         GRU only. body_policy_diagnostics.py --phase realtime|screening plays them through the gate's
         driver in realtime or lockstep.
compare  pairs realtime outcomes of two checkpoints with each other, with the gate driver in lockstep
         and with the RL collector in lockstep.
Measurement only: no stage 8 credit.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

DISTANCES = (250, 300, 350, 400)


def duel(index, seed_start):
    # Same matrix as body_rl_collect.py for --tasks native_duel.
    seed, side, distance = seed_start+index, index % 2, DISTANCES[(index//2) % len(DISTANCES)]
    return {"name": f"rtduel-{seed}-{side}-{distance}", "task": "native_duel", "seed": seed, "side": side,
            "distance_cm": distance, "timeout_ms": 60000}


def write_config(args):
    config = json.loads(args.base.read_text())
    cases = [duel(index, args.seed_start) for index in range(args.duels)]
    config["realtime"] = {**config["realtime"], "cases": cases, "policies": ["gru"], "service_probes": False}
    config["screening"] = {**config["screening"], "cases": cases, "policies": ["gru"]}
    if args.lockstep_start_substeps is not None:
        config["runtime"]["lockstep_start_substeps"] = args.lockstep_start_substeps
    args.output.write_text(json.dumps(config, indent=2)+"\n")


def realtime_rows(folder):
    rows = {}
    for record in json.loads((folder/"suite.json").read_text())["cases"]:
        final, case = record["final"], record["case"]
        actors = {actor["entity_id"]: actor for actor in final["actors"]}
        stats = record.get("model_stats") or {}
        rows[(case["seed"], case["side"], case["distance_cm"])] = {
            "win": final["outcome"] == "death_or_down" and final.get("reason") == "opponent_down",
            "no_fallback": record.get("no_fallback_credit") is True, "outcome": final["outcome"], "reason": final.get("reason"),
            "hp": actors[final["primary_entity_id"]]["hp"], "opponent_hp": actors[final["opponent_entity_id"]]["hp"],
            "elapsed_s": final["elapsed_game_ms"]/1000, "control": record.get("neural_control_fraction"),
            "fallbacks": stats.get("runtime_fallbacks", 0)+len(record.get("fallback_events") or []),
            "decisions": stats.get("accepted_model_commands")}
    return rows


def lockstep_rows(folder):
    rows = {}
    for path in sorted(folder.glob("worker-*/episodes.jsonl")):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            rows[(row["seed"], row["side"], row["distance_cm"])] = {"win": row["reason"] == "opponent_down", "hp": row["primary_hp"],
                                                                    "opponent_hp": row["opponent_hp"],
                                                                    "elapsed_s": row["elapsed_game_ms"]/1000}
    return rows


def mcnemar(only_a, only_b):
    total = only_a+only_b
    if not total:
        return 1.0
    tail = sum(math.comb(total, k) for k in range(0, min(only_a, only_b)+1))/2**total
    return min(1.0, 2*tail)


def pair(a, b, keys):
    mean = lambda values: sum(values)/len(values) if values else None
    only_a = sum(a[key]["win"] and not b[key]["win"] for key in keys)
    only_b = sum(b[key]["win"] and not a[key]["win"] for key in keys)
    hp = [b[key]["hp"]-a[key]["hp"] for key in keys if a[key].get("hp") is not None and b[key].get("hp") is not None]
    both = [key for key in keys if a[key]["win"] and b[key]["win"]]
    return {"cases": len(keys), "wins_a": sum(a[key]["win"] for key in keys), "wins_b": sum(b[key]["win"] for key in keys),
            "only_a": only_a, "only_b": only_b, "mcnemar_p": mcnemar(only_a, only_b),
            "hp_a": mean([a[key]["hp"] for key in keys]), "hp_b": mean([b[key]["hp"] for key in keys]),
            "hp_delta_mean": mean(hp), "both_win_elapsed_s_a": mean([a[key]["elapsed_s"] for key in both]),
            "both_win_elapsed_s_b": mean([b[key]["elapsed_s"] for key in both])}


def compare(args):
    bc, candidate = realtime_rows(args.bc_realtime), realtime_rows(args.candidate_realtime)
    keys = sorted(set(bc) & set(candidate))
    value = {"schema_version": 1, "kind": "body_stage9_realtime_duels", "quality_credit": False,
             "realtime_bc_vs_candidate": pair(bc, candidate, keys),
             "health": {name: {"fallbacks": sum(rows[key]["fallbacks"] for key in keys),
                               "no_fallback_credit": sum(rows[key]["no_fallback"] for key in keys),
                               "control_min": min(rows[key]["control"] or 0 for key in keys),
                               "outcomes": {outcome: sum(rows[key]["outcome"] == outcome for key in keys)
                                            for outcome in {rows[key]["outcome"] for key in keys}}}
                        for name, rows in (("bc", bc), ("candidate", candidate))}}
    if args.bc_lockstep and args.candidate_lockstep:
        for name, realtime, lockstep in (("bc", bc, lockstep_rows(args.bc_lockstep)), ("candidate", candidate, lockstep_rows(args.candidate_lockstep))):
            value["lockstep_vs_realtime_"+name] = pair(lockstep, realtime, [key for key in keys if key in lockstep])
    if args.bc_gate_lockstep and args.candidate_gate_lockstep:
        gate_bc, gate_candidate = realtime_rows(args.bc_gate_lockstep), realtime_rows(args.candidate_gate_lockstep)
        value["gate_lockstep_bc_vs_candidate"] = pair(gate_bc, gate_candidate, sorted(set(gate_bc) & set(gate_candidate)))
        for name, realtime, lockstep in (("bc", bc, gate_bc), ("candidate", candidate, gate_candidate)):
            value["gate_lockstep_vs_realtime_"+name] = pair(lockstep, realtime, [key for key in keys if key in lockstep])
    args.output.write_text(json.dumps(value, indent=2)+"\n")
    print(json.dumps(value, indent=1))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config")
    config.add_argument("--base", type=Path, required=True)
    config.add_argument("--output", type=Path, required=True)
    config.add_argument("--duels", type=int, default=64)
    config.add_argument("--seed-start", type=int, default=90000)
    config.add_argument("--lockstep-start-substeps", type=int, help="1 matches the realtime driver's first poll")
    pairs = commands.add_parser("compare")
    pairs.add_argument("--bc-realtime", type=Path, required=True)
    pairs.add_argument("--candidate-realtime", type=Path, required=True)
    pairs.add_argument("--bc-lockstep", type=Path)
    pairs.add_argument("--candidate-lockstep", type=Path)
    pairs.add_argument("--bc-gate-lockstep", type=Path)
    pairs.add_argument("--candidate-gate-lockstep", type=Path)
    pairs.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    (write_config if args.command == "config" else compare)(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
