#!/usr/bin/env python3
"""Stage 9.2: a self-imitation checkpoint under the stage 8 gate procedure, paired with the BC gate.

Reads the closed phases (shadow, realtime, realtime-reaction, fresh screening, four-actor load) of the
preregistered BC gate run and of a candidate run with the same readers and thresholds the gate uses.
The candidate run is not preregistered: its declaration is the gate's with the candidate's own GRU
checkpoint, run and engine binary substituted. Cases are paired by name (same task, seed, side).
Measurement only: no stage 8 credit.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def declaration(root, gate, bc):
    declared = bc.read_json(gate/"final-evaluation.json")
    if root == gate:
        return declared
    declared = copy.deepcopy(declared)
    declared["new_run"] = str(root)
    declared["candidate"]["run"] = str(root)
    declared["candidate"]["checkpoint_sha256"]["gru"] = bc.digest(root/"models/gru/best.pt")
    reaction = bc.read_json(root/"realtime-reaction/manifest.json")
    declared["updated_engine"] = {"binary_sha256": bc.read_json(root/"environment.json")["binary_sha256"],
                                  "source_sha256": reaction["source_sha256"]}
    return declared


def outcome(record):
    final = record["final"]
    actors = {actor["entity_id"]: actor for actor in final["actors"]}
    primary, opponent = actors[final["primary_entity_id"]], actors[final["opponent_entity_id"]]
    return {"outcome": final["outcome"], "reason": final.get("reason"), "hp": primary["hp"], "opponent_hp": opponent["hp"],
            "elapsed_game_ms": final["elapsed_game_ms"]}


def latency_summary(episodes, key="latency"):
    rows = [row for row in episodes if row["policy"] == "gru"]
    out = {"episodes": len(rows),
           "neural_control_min": min(((row.get("runtime") or {}).get("neural_control_fraction") or 0) for row in rows) if rows else None}
    for name in ("model_step_ms", "collector_to_command_send_ms", "observation_to_arbitration_ms"):
        values = [(row.get(key) or {}).get(name) for row in rows]
        values = [value["p95"] for value in values if value]
        out[name+"_p95_max"] = max(values) if values else None
    return out


def evaluate(root, gate, readers):
    final, bc, combat, reaction = readers
    declared = declaration(root, gate, bc)
    config = bc.read_json(root/"code/evals/npc-body/body-stage8-evaluation-v2.json")
    original = bc.read_json(Path(declared["reference_run"])/"code/evals/npc-body/body-stage8-v1.json")
    reference = Path(declared["reference_run"])
    issues, result = [], {"run": str(root), "gru_sha256": declared["candidate"]["checkpoint_sha256"]["gru"],
                          "mlp_sha256": declared["candidate"]["checkpoint_sha256"]["mlp"],
                          "normalizer_sha256": bc.digest(root/"dataset/normalization.json")}
    combat.phase_evidence(root, "shadow", original, reference)
    ordinary = combat.phase_evidence(root, "realtime", original, reference)
    nonblock = final.ordinary_realtime_cases(ordinary)
    real = final.repaired_phase(root, "realtime-reaction", config, declared, bc, combat, reaction)
    screen = final.repaired_phase(root, "screening", config, declared, bc, combat, reaction)
    load_evidence = combat.four_actor_evidence(root, original)
    issues += final.quality(nonblock, config) + final.quality(real["episodes"], config) + final.quality(load_evidence["cases"], config)
    for episode in real["episodes"]:
        if episode["policy"] != "gru":
            continue
        runtime = episode.get("runtime") or {}
        if not (episode["credited_success"] and runtime.get("runtime_fallbacks") == 0 and runtime.get("neural_control_fraction", 0) >= .95):
            issues.append(episode["name"]+": strict actual block/no-fallback/control gate failed")
    known, failed = combat.combat_comparisons(nonblock+real["episodes"], {**config, "candidate_policy": "gru"})
    issues += failed
    screening, failed = combat.combat_comparisons(screen["episodes"], {**original, "quality": config["quality"]})
    issues += failed + final.quality(screen["episodes"], config)
    for record in bc.read_json(root/"load/suite.json")["cases"]:
        if record["policy"] == "gru" and (record.get("fallback_events") or record.get("model_stats", {}).get("runtime_fallbacks", 0)):
            issues.append(record["name"]+": runtime fallback during neural load")
    reactions = []
    for episode in real["episodes"]:
        for row in (episode.get("defense_reactions") or {}).get("linked_reactions", []):
            if episode["policy"] == "gru" and row["action_id"] == 9:
                reactions.append({"episode": episode["name"], "block_after_windup_game_ms": row["actual_started_game_ms"]-row["observed_windup_game_ms"],
                                  "window_game_ms": row["incoming_attempt_game_ms"]-row["observed_windup_game_ms"], "hp_preserved": row["hp_preserved"]})
    credited = {row["name"]: row["credited_success"] for row in nonblock+real["episodes"]+screen["episodes"]}
    cases = {}
    for phase in ("realtime", "realtime-reaction", "screening"):
        for record in bc.read_json(root/phase/"suite.json")["cases"]:
            cases[(phase, record["case"]["name"], record["policy"])] = {
                "task": record["case"]["task"], "seed": record["case"].get("seed"), "side": record["case"].get("side"),
                "mode": record.get("mode"), "credited_success": credited.get(record["name"]), **outcome(record),
                "decisions": (record.get("model_stats") or {}).get("accepted_model_commands"),
                "actions": {key: value for key, value in (record.get("model_stats") or {}).items() if key.startswith("action_")}}
    result.update(passed=not issues, issues=issues, known_comparisons=known["comparisons"], screening_comparisons=screening["comparisons"],
                  ordinary_realtime=latency_summary(nonblock), realtime_reaction=latency_summary(real["episodes"]),
                  four_actor_load=latency_summary(load_evidence["cases"]), block_reactions=reactions)
    return result, cases


def paired(base, candidate, phase, task, policy="gru"):
    keys = sorted(key for key in base if key[0] == phase and key[2] == policy and base[key]["task"] == task)
    rows = [(base[key], candidate[key]) for key in keys if key in candidate]
    if not rows:
        return None
    mean = lambda values: sum(values)/len(values)
    wins = lambda row: row["credited_success"] is True
    return {"cases": len(rows), "bc_successes": sum(wins(a) for a, _ in rows), "candidate_successes": sum(wins(b) for _, b in rows),
            "only_bc": sum(wins(a) and not wins(b) for a, b in rows), "only_candidate": sum(wins(b) and not wins(a) for a, b in rows),
            "bc_hp_mean": mean([a["hp"] for a, _ in rows]), "candidate_hp_mean": mean([b["hp"] for _, b in rows]),
            "bc_opponent_hp_mean": mean([a["opponent_hp"] for a, _ in rows]),
            "candidate_opponent_hp_mean": mean([b["opponent_hp"] for _, b in rows]),
            "bc_elapsed_s_mean": mean([a["elapsed_game_ms"] for a, _ in rows])/1000,
            "candidate_elapsed_s_mean": mean([b["elapsed_game_ms"] for _, b in rows])/1000,
            "rows": [{"case": key[1], "bc": a, "candidate": b} for key, (a, b) in zip(keys, rows)]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, required=True, help="preregistered BC gate run")
    parser.add_argument("--candidate", type=Path, required=True, help="candidate run with the same phases")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wire-max-ulps", type=int, default=2,
                        help="RapidJSON roundtrip tolerance of the native/wire observation check (the gate's: 2); "
                             "a wider one is recorded in the output as a reader deviation")
    args = parser.parse_args()
    scripts = args.candidate.resolve()/"code/scripts"
    sys.path.insert(0, str(scripts))
    import body_bc_report as bc
    import body_combat_report as combat
    if args.wire_max_ulps != 2:
        frozen = bc.native_wire_relation
        bc.native_wire_relation = lambda native, wire, max_ulps=2: frozen(native, wire, max_ulps=args.wire_max_ulps)
    readers = (load("stage9_final_reader", scripts/"body_stage8_final_report.py"), bc, combat,
               load("stage9_reaction_reader", scripts/"body_block_reaction_report.py"))
    gate, candidate = args.gate.resolve(), args.candidate.resolve()
    base, base_cases = evaluate(gate, gate, readers)
    other, other_cases = evaluate(candidate, gate, readers)
    comparison = {phase: {task: paired(base_cases, other_cases, phase, task) for task in ("distance", "attack", "block", "native_duel")}
                  for phase in ("realtime", "realtime-reaction", "screening")}
    mlp_control = {task: paired(base_cases, other_cases, "screening", task, "mlp") for task in ("native_duel",)}
    value = {"schema_version": 1, "kind": "body_stage9_gate_procedure_comparison", "stage8_quality_credit": False,
             "candidate_preregistered": False, "bc": base, "candidate": other, "paired_gru": comparison,
             "paired_mlp_same_weights": mlp_control,
             "reader_deviations": [] if args.wire_max_ulps == 2 else
             [{"check": "native_wire_relation", "gate_max_ulps": 2, "used_max_ulps": args.wire_max_ulps}]}
    args.output.write_text(json.dumps(value, indent=2)+"\n")
    for name, run in (("BC", base), ("candidate", other)):
        print(name, run["gru_sha256"][:12], "passed" if run["passed"] else "FAILED", run["issues"][:5])
        for phase in ("ordinary_realtime", "realtime_reaction", "four_actor_load"):
            print(" ", phase, json.dumps(run[phase]))
        print("  block", json.dumps(run["block_reactions"]))
    for phase, tasks in comparison.items():
        for task, row in tasks.items():
            if row:
                print(f"{phase:17s} {task:11s} n={row['cases']:2d} success BC {row['bc_successes']} cand {row['candidate_successes']} "
                      f"(only BC {row['only_bc']}, only cand {row['only_candidate']}) hp {row['bc_hp_mean']:.0f}/{row['candidate_hp_mean']:.0f} "
                      f"opp hp {row['bc_opponent_hp_mean']:.0f}/{row['candidate_opponent_hp_mean']:.0f} "
                      f"t {row['bc_elapsed_s_mean']:.1f}/{row['candidate_elapsed_s_mean']:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
