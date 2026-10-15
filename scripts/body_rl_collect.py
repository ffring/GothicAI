#!/usr/bin/env python3
"""Stage 9.2: parallel lockstep rollouts of a frozen policy with sampled actions.

Each worker owns one --fork-reset runner with the training culls and one single-thread
CPU policy runtime. Every periodic row of the policy actor updates memory exactly as in
evaluation; at each decision the action is sampled from the runtime's own logits over
the effective mask (temperature 0 is the evaluation argmax). Per episode the collector
keeps a compact score summary and the model-view rows (features, mask, reset, sampled
label) a self-imitation round trains on. Measurement and data only: no quality credit.

Decisions follow the stage 8 realtime driver: the first one after one tick (--start-substeps 1)
and native command TTL 100 ms. With them a greedy rollout reproduces the realtime driver's
episodes (scripts/body_schedule_equivalence.py); the 9.2 rounds used 2 and 1000 and fitted
a lockstep-only schedule. Between decisions the runner advances on its own (arena_advance) to
the tick where the per-tick loop would decide next; --per-tick keeps one round trip per tick.
The world outside the arena is frozen and the actors draw their own script randomness: such a
rollout equals the realtime driver in the live world with OPENGOTHIC_BODY_SCRIPT_RNG=isolated.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import json
import math
import multiprocessing
import os
from pathlib import Path
import random
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Training culls: foot IK only for arena actors, no animation far away, the rest of the world frozen,
# and the arena actors' script randomness isolated from it (which makes the frozen world exact).
TRAINING_ENV = {"OPENGOTHIC_FOOTIK_WATCHED_ONLY": "1", "OPENGOTHIC_ANIM_SKIP_FAR": "1",
                "OPENGOTHIC_FREEZE_WORLD": "1", "OPENGOTHIC_BODY_SCRIPT_RNG": "isolated", "DISPLAY": "",
                "WAYLAND_DISPLAY": "", "GOTHIC_AI": "0", "VK_DRIVER_FILES": "/nonexistent/no-vulkan.json",
                "VK_ICD_FILENAMES": "/nonexistent/no-vulkan.json", "OPENGOTHIC_TEXTURES": "",
                "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
HEARTBEAT_EVERY_S = 0.25
# Longest arena_advance; keeps the bounded native queues far from overflow between drains.
ADVANCE_MAX_SUBSTEPS = 30
# Short arena tasks keep their stage-8 distance and timeout; duels vary the distance.
TASK_SETUP = {"distance": (600, 10000), "attack": (150, 5000), "block": (150, 5000)}


def success(summary: dict) -> bool:
    """A duel is won by downing the opponent; a short task only by its own success
    (killing the opponent in the block task is not a blocked hit)."""
    if summary.get("task", "native_duel") == "native_duel":
        return summary["reason"] == "opponent_down"
    return summary["outcome"] == "task_success"


def score(summary: dict, timeout_ms: int) -> float:
    """Win/loss, then health margin, then a faster win. Timeouts score on margin only."""
    if summary.get("task", "native_duel") != "native_duel":
        return 1.0 if success(summary) else 0.0
    outcome = {"opponent_down": 1.0, "primary_down": -1.0}.get(summary["reason"], 0.0)
    margin = summary["primary_hp"]/summary["primary_hp_max"]-summary["opponent_hp"]/summary["opponent_hp_max"]
    speed = -0.2*summary["elapsed_game_ms"]/timeout_ms if outcome > 0 else 0.0
    return outcome+0.5*margin+speed


def sample_action(scores: list[float], mask: list[bool], temperature: float, rng: random.Random) -> int:
    allowed = [index for index in range(1, 12) if mask[index]]
    if temperature <= 0:
        return max(allowed, key=lambda index: scores[index])
    top = max(scores[index] for index in allowed)
    weights = [math.exp((scores[index]-top)/temperature) for index in allowed]
    return rng.choices(allowed, weights=weights)[0]


def run_worker(worker: int, options: dict, tasks: list[list]) -> dict:
    os.environ.update(TRAINING_ENV)
    import numpy
    from body_arena_env import LEASE_KEYS, ArenaInfrastructureError
    from body_policy_suite import PolicyArenaRunner, PolicyEpisodeEnv
    from gothic_body.runtime import PolicyRuntime, actor_identity

    class LeanRunner(PolicyArenaRunner):
        def record(self, direction, value):
            pass

        def episode_log(self, output):
            pass

    class LeanEnv(PolicyEpisodeEnv):
        """Heartbeats on wall time; per-tick native events are not read."""
        last_heartbeat = 0.0

        def heartbeat(self):
            now = time.monotonic()
            if now-self.last_heartbeat < HEARTBEAT_EVERY_S:
                return
            self.last_heartbeat = now
            for entity, lease in list(self.leases.items()):
                response = self.runner.call({"cmd": "body_heartbeat", **lease, "provider": self.owner_provider})
                if response.get("accepted") is not True and response.get("reason") != "release_pending":
                    self.leases.pop(entity)
                    self.fallbacks.append({"entity_id": entity, "reason": response.get("reason"), "kind": "ownership_loss"})

        def step(self, substeps=1):
            self.heartbeat()
            response = self.runner.call({"cmd": "arena_step", "substeps": substeps})
            self._arena(response)
            self.pull()
            return response.get("advanced_substeps", 0)

        def advance(self, watches, max_substeps):
            """One round trip to the next tick where a watched actor could decide, or terminal."""
            self.heartbeat()
            response = self.runner.call({"cmd": "arena_advance", "max_substeps": max_substeps, "until": watches})
            self._arena(response)
            if response.get("observation_dropped_since_drain"):
                raise ArenaInfrastructureError("arena_advance lost queued observations")
            self.pulled.extend(self.accept_rows(response.get("observations")))
            self.events.extend(response.get("events", []))
            return response.get("advanced_substeps", 0)

    output = Path(options["output"])/f"worker-{worker:02d}"
    output.mkdir(parents=True)
    runner_args = argparse.Namespace(output=output, runner=Path(options["runner"]), assets=Path(options["assets"]),
                                     save=Path(options["save"]), reference=Path(options["reference"]),
                                     scenario=Path(options["scenario"]), compact_replies=True,
                                     native_serial_workers=True, runner_extra_args=["--fork-reset"])
    runner = LeanRunner(runner_args)
    policy = PolicyRuntime(options["checkpoint"], device="cpu", max_actors=4, inference_cpu_threads=1)
    rng = random.Random(options["sampling_seed"]*1000+worker)
    summaries = []
    try:
        for task, seed, side, distance in tasks:
            name = f"ep-{task}-{seed}-{side}-{distance}"
            episode_dir = output/name
            env = LeanEnv(runner, Path(options["scenario"]), owner_provider="stage9-rl")
            env.command_ttl_ms = options.get("command_ttl_ms", 1000)
            started = time.monotonic()
            timeout_ms = TASK_SETUP[task][1] if task in TASK_SETUP else options["timeout_ms"]
            initial = env.reset(output=episode_dir, task=task, seed=seed, side=side, distance_cm=distance,
                                timeout_ms=timeout_ms, provider="scripted")
            adversary_sent = task != "block"
            reset_s = time.monotonic()-started
            primary, opponent = initial["primary_entity_id"], initial["opponent_entity_id"]
            policy.begin_episode(initial["session_epoch"], initial["world_epoch"], initial_game_ms=initial["game_ms"])
            stream, rows, stats = 0, [], Counter()

            def feed(command_enabled):
                nonlocal stream
                pulled, env.pulled = env.pulled, []
                for row in pulled:
                    if row.get("entity_id") != primary or row.get("periodic") is not True:
                        continue
                    lease = env.leases.get(primary)
                    if lease is None or any(row.get(key) != lease[key] for key in LEASE_KEYS):
                        continue
                    stream += 1
                    # A per-tick client processes each periodic row on its own tick: now == row time.
                    response = policy.process({"stream_seq": stream, "observation": row, "source": "periodic_observer",
                                               "now_game_ms": row["t_game_ms"], "checkpoint_sha256": policy.checkpoint_sha256,
                                               "command_enabled": command_enabled,
                                               "memory_only": not command_enabled or row.get("can_decide") is not True})
                    if response.get("model_processed"):
                        features = policy.packer(row, policy.normalizer, sequence_dt_game_ms=response["sequence_dt_game_ms"])
                        rows.append({"features": features, "mask": response["effective_action_mask"],
                                     "reset": bool(response.get("hidden_reset")), "label": -1, "seq": row["observation_seq"]})
                    elif response.get("status") == "fallback" and command_enabled:
                        stats["fallbacks"] += 1
                        env.release_to_native(primary, response.get("reason"))

            def watches():
                # Exactly the per-tick checks below: the primary on a periodic row, and the
                # block task's held opponent while its single attack is still unsent.
                out = []
                if primary in env.leases:
                    out.append({**{key: env.leases[primary][key] for key in LEASE_KEYS}, "periodic": True})
                if not adversary_sent and opponent in env.leases:
                    out.append({**{key: env.leases[opponent][key] for key in LEASE_KEYS}, "periodic": False, "actions": [6, 2]})
                return out

            rollout_started = time.monotonic()
            feed(False)
            env.start(options.get("start_substeps", 2))
            ticks = 0
            while ticks < options["max_substeps"]:
                terminal = bool(env.arena.get("terminated") or env.arena.get("truncated"))
                feed(not terminal)
                if terminal:
                    break
                if not adversary_sent:
                    # The stage-7 block task: the held opponent throws one real front attack.
                    if env.eligible(opponent, 6):
                        env.act(opponent, 6)
                        adversary_sent = True
                    elif env.eligible(opponent, 2):
                        env.act(opponent, 2)
                row = env.latest.get(primary)
                undecided = False
                if row is not None and row.get("periodic") is True and env.eligible(primary):
                    response = policy.arbitrate(row, stream_seq=stream)
                    if response.get("status") == "decision" and response.get("action_id") is not None:
                        scores = policy.states[actor_identity(row)].scores
                        allowed = [bool(a and b) for a, b in zip(response["effective_action_mask"], row["action_mask"])]
                        action = sample_action(scores, allowed, options["temperature"], rng)
                        env.act(primary, action)
                        stats["decisions"] += 1
                        stats[f"action_{action}"] += 1
                        if rows and rows[-1]["seq"] == row["observation_seq"]:
                            rows[-1]["label"] = action
                    elif response.get("status") == "fallback":
                        stats["fallbacks"] += 1
                        env.release_to_native(primary, response.get("reason"))
                    else:
                        # The runtime declined an eligible row: re-check it tick by tick, as before.
                        undecided = True
                if options.get("advance") and not undecided:
                    advanced = env.advance(watches(), min(ADVANCE_MAX_SUBSTEPS, options["max_substeps"]-ticks))
                    stats["advance_calls"] += 1
                else:
                    advanced = env.step(1)
                ticks += advanced
                if advanced == 0 and not (env.arena.get("terminated") or env.arena.get("truncated")):
                    raise ArenaInfrastructureError("rollout made no simulation progress")
            final = env.status()
            policy.end_episode(initial["session_epoch"], initial["world_epoch"])
            actors = {actor["entity_id"]: actor for actor in final["actors"]}
            start = {actor["entity_id"]: actor for actor in initial["actors"]}
            summary = {"worker": worker, "name": name, "task": task, "seed": seed, "side": side, "distance_cm": distance,
                       "reason": final.get("reason"), "outcome": final.get("outcome"),
                       "elapsed_game_ms": final.get("elapsed_game_ms", 0),
                       "primary_hp": actors[primary]["hp"], "primary_hp_max": start[primary].get("hp_max", start[primary]["hp"]),
                       "opponent_hp": actors[opponent]["hp"], "opponent_hp_max": start[opponent].get("hp_max", start[opponent]["hp"]),
                       "rows": len(rows), "ticks": ticks, "reset_s": reset_s, "rollout_s": time.monotonic()-rollout_started,
                       "temperature": options["temperature"], "checkpoint_sha256": policy.checkpoint_sha256, **stats}
            summary["success"] = success(summary)
            summary["score"] = score(summary, timeout_ms)
            if rows:
                numpy.savez_compressed(output/f"{name}.npz",
                                       features=numpy.asarray([row["features"] for row in rows], dtype=numpy.float32),
                                       masks=numpy.asarray([row["mask"] for row in rows], dtype=bool),
                                       resets=numpy.asarray([row["reset"] for row in rows], dtype=bool),
                                       labels=numpy.asarray([row["label"] for row in rows], dtype=numpy.int16))
            summaries.append(summary)
            with (output/"episodes.jsonl").open("a") as stream_file:
                stream_file.write(json.dumps(summary)+"\n")
            if not options["keep_journals"]:
                shutil.rmtree(episode_dir, ignore_errors=True)
        runner.call({"cmd": "close"})
    finally:
        runner.terminate()
    return {"worker": worker, "episodes": len(summaries)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runner", "assets", "save", "reference", "scenario", "checkpoint", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--episodes", type=int, default=96)
    parser.add_argument("--seed-start", type=int, default=20000)
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument("--sampling-seed", type=int, default=1)
    parser.add_argument("--timeout-ms", type=int, default=60000)
    parser.add_argument("--max-substeps", type=int, default=2200)
    parser.add_argument("--keep-journals", action="store_true")
    parser.add_argument("--tasks", default="native_duel")
    parser.add_argument("--start-substeps", type=int, default=1,
                        help="ticks before the first decision; 1 matches the realtime driver (9.2 rounds used 2)")
    parser.add_argument("--per-tick", dest="advance", action="store_false",
                        help="poll every tick instead of the runner's arena_advance to the next decision")
    parser.add_argument("--command-ttl-ms", type=int, default=100,
                        help="native command TTL; 100 is the stage 8 realtime runtime (9.2 rounds used 1000)")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    distances = (250, 300, 350, 400)
    kinds = args.tasks.split(",")
    tasks = []
    for index in range(args.episodes):
        task = kinds[index % len(kinds)]
        distance = TASK_SETUP[task][0] if task in TASK_SETUP else distances[(index//2) % len(distances)]
        tasks.append([task, args.seed_start+index, (index//len(kinds)) % 2, distance])
    options = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}
    (args.output/"collect.json").write_text(json.dumps({"schema_version": 1, "kind": "body_rl_collect",
                                                        "quality_credit": False, "options": options,
                                                        "training_env": TRAINING_ENV}, indent=2)+"\n")
    started = time.monotonic()
    shards = [tasks[index::args.workers] for index in range(args.workers)]
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = [pool.submit(run_worker, index, options, shard) for index, shard in enumerate(shards) if shard]
        for future in futures:
            future.result()
    wall = time.monotonic()-started
    summaries = [json.loads(line) for path in sorted(args.output.glob("worker-*/episodes.jsonl")) for line in path.read_text().splitlines()]
    wins = sum(row["reason"] == "opponent_down" for row in summaries)
    decisions = sum(row.get("decisions", 0) for row in summaries)
    by_task = {}
    for row in summaries:
        entry = by_task.setdefault(row.get("task", "native_duel"), [0, 0])
        entry[0] += int(row.get("success", row["reason"] == "opponent_down"))
        entry[1] += 1
    report = {"episodes": len(summaries), "wall_s": wall, "wins": wins,
              "success_by_task": {task: f"{done}/{total}" for task, (done, total) in sorted(by_task.items())}, "losses": sum(row["reason"] == "primary_down" for row in summaries),
              "timeouts": sum(row["reason"] not in ("opponent_down", "primary_down") for row in summaries),
              "mean_score": sum(row["score"] for row in summaries)/max(1, len(summaries)),
              "mean_primary_hp": sum(row["primary_hp"] for row in summaries)/max(1, len(summaries)),
              "mean_elapsed_s": sum(row["elapsed_game_ms"] for row in summaries)/1000/max(1, len(summaries)),
              "decisions": decisions, "decisions_per_s": decisions/wall, "episodes_per_min": len(summaries)/wall*60,
              "fallbacks": sum(row.get("fallbacks", 0) for row in summaries)}
    (args.output/"summary.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
