#!/usr/bin/env python3
"""Stage 9.1: lockstep simulation throughput of the headless runner.

Runs native-vs-native arena duels and measures what an RL collector pays per
simulated tick and per reset. Melee variants enable only the physical benchmark;
--checkpoint also measures the existing frozen-policy collector with greedy
decisions. Measurement only: no quality credit.
"""
from __future__ import annotations

import argparse
import configparser
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from body_observe_suite import ObserverRunner  # noqa: E402


# Training mode of the RL collector: culls, frozen world, isolated arena randomness.
FROZEN_ENV = {"OPENGOTHIC_FOOTIK_WATCHED_ONLY": "1", "OPENGOTHIC_ANIM_SKIP_FAR": "1",
              "OPENGOTHIC_FREEZE_WORLD": "1", "OPENGOTHIC_BODY_SCRIPT_RNG": "isolated"}
VISUAL_EXTRAS = ("OPENGOTHIC_FOOTIK", "OPENGOTHIC_LEAN", "OPENGOTHIC_AIM", "OPENGOTHIC_AIMWALK", "OPENGOTHIC_ANIBLEND",
                 "OPENGOTHIC_BOWDRAW", "OPENGOTHIC_BLINK", "OPENGOTHIC_FLINCH", "OPENGOTHIC_GAZE", "OPENGOTHIC_LIPSYNC",
                 "OPENGOTHIC_GESTURES")


def run_policy(args: argparse.Namespace, output: Path) -> dict:
    """Measure the existing single-runner collector without changing its policy."""
    command = [sys.executable, str(Path(__file__).with_name("body_rl_collect.py"))]
    for name in ("runner", "assets", "save", "reference", "scenario", "checkpoint"):
        command.extend(("--"+name, str(getattr(args, name))))
    command.extend(("--output", str(output/"policy"), "--workers", "1", "--envs-per-worker", "1",
                    "--episodes", str(args.episodes), "--seed-start", str(args.seed), "--temperature", "0",
                    "--sampling-seed", "1", "--timeout-ms", str(args.timeout_ms)))
    if args.melee_shadow is not None:
        command.append("--keep-journals")
    with (output/"policy-collector.log").open("w") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    summary = json.loads((output/"policy/summary.json").read_text())
    episodes = [json.loads(line) for path in sorted((output/"policy").glob("worker-*/episodes.jsonl"))
                for line in path.read_text().splitlines()]
    if args.melee_shadow is not None:
        for row in episodes:
            row["damage"] = damage_rows(output/"policy"/f"worker-{row['worker']:02d}"/row["name"])
    ticks = sum(row["ticks"] for row in episodes)
    rollout = sum(row["rollout_s"] for row in episodes)
    if ticks <= 0 or rollout <= 0 or summary["decisions"] <= 0:
        raise RuntimeError("policy benchmark did not advance ticks and decisions")
    return {"provider": "frozen_checkpoint", "temperature": 0, "workers": 1, "envs_per_worker": 1,
            "checkpoint_sha256": episodes[0]["checkpoint_sha256"], "episodes": episodes,
            "decisions": summary["decisions"], "decisions_per_s": summary["decisions_per_s"],
            "rollout_decisions_per_s": summary["decisions"]/rollout,
            "ms_per_tick": rollout*1000/ticks, "ticks": ticks, "rollout_s": rollout,
            "wall_s_including_startup_resets": summary["wall_s"], "fallbacks": summary["fallbacks"]}


def damage_rows(output: Path) -> list[dict]:
    """Keep ordered native damage results, excluding journal bookkeeping."""
    keys = ("t_game", "actor", "target", "damage_kind", "hp_before", "hp_after", "net", "has_hit", "down", "dead")
    with (output/"events.jsonl").open() as stream:
        return [{key: row[key] for key in keys} for line in stream
                if (row := json.loads(line)).get("kind") == "body_combat" and row.get("phase") == "damage"]


def shadow_comparison(results: list[dict]) -> dict | None:
    variants = {row["name"]: row for row in results}
    if not {"shadow-off", "shadow-on"} <= variants.keys():
        return None
    off, on = variants["shadow-off"], variants["shadow-on"]
    comparisons = {}
    groups = [("native", off["episodes"], on["episodes"],
               ("seed", "side", "steps", "game_ms", "outcome", "reason", "actors", "damage"))]
    if "policy" in off and "policy" in on:
        groups.append(("policy", off["policy"]["episodes"], on["policy"]["episodes"],
                       ("name", "task", "seed", "side", "distance_cm", "geometry", "opponent", "learner_seat",
                        "arena_reason", "reason", "outcome", "elapsed_game_ms", "primary_hp", "primary_hp_max",
                        "opponent_hp", "opponent_hp_max", "ticks", "checkpoint_sha256", "damage")))
    for name, baseline, candidate, keys in groups:
        differences = [{"episode": index, "seed": a["seed"], "fields": [key for key in keys if a[key] != b[key]]}
                       for index, (a, b) in enumerate(zip(baseline, candidate)) if any(a[key] != b[key] for key in keys)]
        comparisons[name] = {"off_episodes": len(baseline), "on_episodes": len(candidate),
                             "matched": min(len(baseline), len(candidate))-len(differences),
                             "differences": differences, "equal": len(baseline) == len(candidate) and not differences}
    return comparisons


def run_variant(args: argparse.Namespace, name: str, *, serial: bool, trace: bool, watch: bool,
                visibility: bool = True, off=(), env=None, extra=(), melee_shadow=None) -> dict:
    output = args.output/name
    output.mkdir(parents=True)
    variant = argparse.Namespace(**vars(args))
    variant.output = output
    variant.native_serial_workers = serial
    variant.compact_replies = True
    variant.runner_extra_args = list(extra)
    variant.melee_shadow = melee_shadow
    if melee_shadow is not None:
        # Both collectors copy this private INI; the archived reference stays unchanged.
        variant.reference = output/"reference"
        settings = variant.reference/"run/Gothic.ini"
        settings.parent.mkdir(parents=True)
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read(args.reference/"run/Gothic.ini")
        if not config.has_section("GAME"):
            config.add_section("GAME")
        config.set("GAME", "melee_blade_shadow", str(melee_shadow))
        with settings.open("w") as stream:
            config.write(stream)
    os.environ.pop("OPENGOTHIC_BODY_TRACE", None)
    if trace:
        os.environ["OPENGOTHIC_BODY_TRACE"] = "1"
    for key in VISUAL_EXTRAS:
        os.environ.pop(key, None)
    for key in off:
        os.environ[key] = "0"
    for key in FROZEN_ENV:
        os.environ.pop(key, None)
    os.environ.pop("OPENGOTHIC_MELEE_BENCH", None)
    os.environ.update(env or {})
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    runner = ObserverRunner(variant)
    episodes = []
    try:
        runner.request({"cmd": "status"})
        for index in range(args.episodes):
            folder = output/f"episode-{index}"
            folder.mkdir()
            begin = time.monotonic()
            reset = runner.request({"cmd": "reset", "scenario": str(args.scenario), "output_dir": str(folder),
                                    "mode": "lockstep", "arena": {"scenario": "native_duel", "seed": args.seed+index,
                                                                  "side": index % 2, "distance_cm": 400,
                                                                  "timeout_ms": args.timeout_ms}})
            if not watch:
                runner.request({"cmd": "body_watch", "enabled": False}, allow_error=True)
            elif visibility:
                runner.request({"cmd": "body_visibility_watch", "enabled": True}, allow_error=True)
            reset_s = time.monotonic()-begin
            native_reset_ms = reset.get("reset_wall_ms")
            warmup_ms = reset.get("simulated_ms")
            start = runner.request({"cmd": "arena_start", "provider": "native"})
            game0 = (start.get("arena") or reset.get("arena") or {}).get("game_ms", 0)
            steps, begin = 0, time.monotonic()
            arena = start.get("arena") or {}
            while not (arena.get("terminated") or arena.get("truncated")):
                response = runner.request({"cmd": "arena_step", "substeps": args.substeps})
                steps += response.get("advanced_substeps", 0)
                arena = response.get("arena") or arena
                if response.get("advanced_substeps", 0) == 0:
                    break
            wall = time.monotonic()-begin
            game = arena.get("game_ms", game0)-game0
            episodes.append({"reset_s": reset_s, "native_reset_ms": native_reset_ms, "warmup_game_ms": warmup_ms, "steps": steps, "wall_s": wall, "game_ms": game,
                             "ms_per_tick": wall*1000/max(1, steps), "speed": game/1000/max(wall, 1e-9),
                             "outcome": arena.get("outcome")})
            if melee_shadow is not None:
                episodes[-1].update(seed=args.seed+index, side=index % 2, reason=arena["reason"],
                                    actors=[{key: actor[key] for key in ("hp", "hp_max", "received_damage", "outgoing_damage", "blocked_hits")}
                                            for actor in arena["actors"]], damage=damage_rows(folder))
            print(f"{name} ep{index}: reset {reset_s:.2f}s (runner {native_reset_ms} ms, warmup {warmup_ms} game ms) steps {steps} {wall*1000/max(1,steps):.2f} ms/tick "
                  f"speed {game/1000/max(wall,1e-9):.2f}x {arena.get('outcome')}", flush=True)
        sample = runner.sample()
        runner.request({"cmd": "close"})
    finally:
        runner.terminate()
    cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    runner_cpu_s = (cpu_after.ru_utime+cpu_after.ru_stime)-(cpu_before.ru_utime+cpu_before.ru_stime)
    ticks = sum(e["steps"] for e in episodes)
    wall = sum(e["wall_s"] for e in episodes)
    result = {"name": name, "serial_workers": serial, "body_trace": trace, "visibility_watch": watch and visibility, "disabled": list(off),
            "environment": dict(env or {}), "runner_peak_rss": sample.get("VmHWM"),
            "runner_cpu_s_including_resets": runner_cpu_s,
            "episodes": episodes, "ms_per_tick": wall*1000/max(1, ticks),
            "reset_s_mean": sum(e["reset_s"] for e in episodes)/len(episodes),
            "game_speed": sum(e["game_ms"] for e in episodes)/1000/max(wall, 1e-9)}
    if args.checkpoint is not None:
        result["policy"] = run_policy(variant, output)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "scenario", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--episodes", type=int, default=3)
    parser.add_argument("--seed", type=int, default=501)
    parser.add_argument("--timeout-ms", type=int, default=20000)
    parser.add_argument("--substeps", type=int, default=30)
    parser.add_argument("--variants", default="gate,lean,pool-lean,pool-gate")
    parser.add_argument("--checkpoint", type=Path,
                        help="also measure the existing deterministic P30 collector, one duel runner at a time")
    args = parser.parse_args()
    for name in ("runner", "assets", "save", "reference", "scenario", "output", "checkpoint"):
        value = getattr(args, name)
        if value is not None:
            setattr(args, name, value.resolve())
    args.output.mkdir(parents=True, exist_ok=False)
    table = {"gate": dict(serial=True, trace=True, watch=True), "lean": dict(serial=True, trace=False, watch=False),
             "pool-lean": dict(serial=False, trace=False, watch=False), "pool-gate": dict(serial=False, trace=True, watch=True),
             "lean-nofootik": dict(serial=True, trace=False, watch=False, off=("OPENGOTHIC_FOOTIK",)),
             "lean-novisual": dict(serial=True, trace=False, watch=False, off=VISUAL_EXTRAS),
             "pool-novisual": dict(serial=False, trace=False, watch=False, off=VISUAL_EXTRAS),
             "lean-footik-watched": dict(serial=True, trace=False, watch=False, env={"OPENGOTHIC_FOOTIK_WATCHED_ONLY": "1"}),
             "gate-footik-watched": dict(serial=True, trace=True, watch=True, env={"OPENGOTHIC_FOOTIK_WATCHED_ONLY": "1"}),
             "lean-cull": dict(serial=True, trace=False, watch=False,
                               env={"OPENGOTHIC_FOOTIK_WATCHED_ONLY": "1", "OPENGOTHIC_ANIM_SKIP_FAR": "1"}),
             "lean-cull-fork": dict(serial=True, trace=False, watch=False, extra=("--fork-reset",),
                                    env={"OPENGOTHIC_FOOTIK_WATCHED_ONLY": "1", "OPENGOTHIC_ANIM_SKIP_FAR": "1"}),
             "frozen-fork": dict(serial=True, trace=False, watch=False, extra=("--fork-reset",), env=FROZEN_ENV),
             "frozen-prefix": dict(serial=True, trace=False, watch=False, extra=("--fork-reset", "--fork-prefix"),
                                   env=FROZEN_ENV),
             "melee-blade": dict(serial=True, trace=False, watch=True, visibility=False, extra=("--fork-reset", "--fork-prefix"),
                                 env={**FROZEN_ENV, "OPENGOTHIC_MELEE_BENCH": "blade"}),
             "melee-blade-motors": dict(serial=True, trace=False, watch=True, visibility=False, extra=("--fork-reset", "--fork-prefix"),
                                        env={**FROZEN_ENV, "OPENGOTHIC_MELEE_BENCH": "blade-motors"}),
             "shadow-off": dict(serial=True, trace=True, watch=True, visibility=False, extra=("--fork-reset", "--fork-prefix"),
                                env=FROZEN_ENV, melee_shadow=0),
             "shadow-on": dict(serial=True, trace=True, watch=True, visibility=False, extra=("--fork-reset", "--fork-prefix"),
                               env=FROZEN_ENV, melee_shadow=1)}
    results = [run_variant(args, name, **table[name]) for name in args.variants.split(",")]
    comparison = shadow_comparison(results)
    (args.output/"bench.json").write_text(json.dumps({"schema_version": 1, "kind": "body_sim_bench", "quality_credit": False,
                                                      "results": results, "shadow_comparison": comparison}, indent=2)+"\n")
    for result in results:
        ticks = sum(e["steps"] for e in result["episodes"])
        print(f"== {result['name']}: {result['ms_per_tick']:.2f} ms/tick, speed {result['game_speed']:.2f}x, "
              f"reset {result['reset_s_mean']:.2f}s, runner CPU {result['runner_cpu_s_including_resets']*1000/max(1,ticks):.1f} ms/tick "
              f"(incl. resets), peak RSS {result['runner_peak_rss']}", flush=True)
        if "policy" in result:
            policy = result["policy"]
            print(f"   policy: {policy['decisions_per_s']:.2f} decisions/s incl. resets, "
                  f"{policy['rollout_decisions_per_s']:.2f} rollout decisions/s, "
                  f"{policy['ms_per_tick']:.2f} ms/tick, fallbacks {policy['fallbacks']}", flush=True)
    if comparison is not None:
        for name, row in comparison.items():
            print(f"shadow {name}: {row['matched']}/{row['off_episodes']} identical outcomes and damage", flush=True)
        return int(any(not row["equal"] for row in comparison.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
