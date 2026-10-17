#!/usr/bin/env python3
"""Stage 9.3: one line per PPO iteration of a body-rl-ppo-loop.sh work directory.

Training columns come from the iteration's collection (temperature 1, mixed opponents and arenas)
and update.json; eval columns (greedy, fixed held-out seeds) appear every PPO_EVAL_EVERY
iterations: native open / native wall wins and mean health, and wins against the pool head
(seats random). Usage: body_rl_ppo_report.py WORK [--json OUT].
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def episodes(directory: Path) -> list[dict]:
    return [json.loads(line) for path in sorted(directory.glob("worker-*/episodes.jsonl")) for line in path.read_text().splitlines()]


def wins(rows) -> int:
    return sum(row["reason"] == "opponent_down" for row in rows)


def iteration(folder: Path) -> dict:
    out = {"iteration": int(folder.name.split("-")[1])}
    rows = episodes(folder/"collect")
    if rows:
        native = [row for row in rows if row.get("opponent", "native") == "native"]
        pool = [row for row in rows if row.get("opponent", "native") != "native"]
        out.update(train_native=f"{wins(native)}/{len(native)}", train_pool=f"{wins(pool)}/{len(pool)}",
                   train_score=sum(row["score"] for row in rows)/len(rows))
    update = folder/"update/update.json"
    if update.exists():
        record = json.loads(update.read_text())
        last = record["history"][-1]
        policy = [row for row in record["history"] if row["policy_updated"]]
        out.update(ev=record["explained_variance_before"], value_loss=last["value_loss"],
                   approx_kl=policy[-1]["approx_kl"] if policy else 0.0,
                   clip=policy[-1]["clip_fraction"] if policy else 0.0,
                   entropy=last["entropy"], kl_ref=last["kl_ref"], logp_diff=record["ratio_check"]["max_abs_logp_diff"],
                   update_s=record["wall_s"])
    for name in ("open", "wall", "pool"):
        rows = episodes(folder/"eval"/name)
        if rows:
            out[f"eval_{name}"] = f"{wins(rows)}/{len(rows)}"
            out[f"eval_{name}_hp"] = sum(row["primary_hp"] for row in rows)/len(rows)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("work", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    rows = [iteration(folder) for folder in sorted(args.work.glob("iter-*")) if folder.is_dir()]
    print(f"{'it':>3} {'native':>9} {'pool':>9} {'score':>6} {'ev':>6} {'kl':>7} {'clip':>5} {'ent':>5} {'klBC':>5} "
          f"{'s':>4} | {'open':>8} {'hp':>5} {'wall':>8} {'hp':>5} {'pool':>8}")
    for row in rows:
        def get(key, form):
            return format(row[key], form) if key in row else ""
        print(f"{row['iteration']:>3} {row.get('train_native', ''):>9} {row.get('train_pool', ''):>9} {get('train_score', '6.3f'):>6} "
              f"{get('ev', '6.2f'):>6} {get('approx_kl', '7.4f'):>7} {get('clip', '5.3f'):>5} {get('entropy', '5.3f'):>5} "
              f"{get('kl_ref', '5.3f'):>5} {get('update_s', '4.0f'):>4} | {row.get('eval_open', ''):>8} {get('eval_open_hp', '5.1f'):>5} "
              f"{row.get('eval_wall', ''):>8} {get('eval_wall_hp', '5.1f'):>5} {row.get('eval_pool', ''):>8}")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2)+"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
