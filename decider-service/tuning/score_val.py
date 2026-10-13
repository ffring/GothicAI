"""Score checkpoints on the held-out Gothic cards (data/train/val_requests.jsonl, labels: both
gpt-6.1-sol passes agreed). Runs on the GPU host, in the decider service's venv:

    ~/gothic-ai/decider/.venv/bin/python score_val.py --data data \\
        parent=StrandsAgents/strands-decider-2B-qwen3.5-v1-2610 g1=ckpt/g1

Per checkpoint:
- react accuracy on the planner's menu (prod) and on npc-specific menus (rich), and for prod react what
  the planner would do with its thresholds (an action below its threshold means the npc keeps its
  routine, i.e. `continue`);
- attitude and escalate: exactly / within one level, also per row variant (prod, game, rich); for escalate
  also AUROC of "level 3 or more" by the expected level, overall and on the game-format rows;
- with `--eval DIR` (evals/npc-decisions: requests.jsonl, escalate_gold.yaml): the escalate question
  asked of the 76 eval scenes, against its hand-labelled levels.
"""

from __future__ import annotations

import argparse
import json
import os

import torch
from strands_decider.infer import load_engine
from strands_decider.schema import SystemOneRequest

# server/src/gothic_ai/brain/planner.py: option_threshold, otherwise 0.5
THRESHOLDS = {"look_at": 0.25, "greet": 0.3, "approach_talk": 0.6}


def acted(probs: dict[str, float]) -> str:
    best = max(probs, key=probs.get)
    if best != "continue" and probs[best] < THRESHOLDS.get(best, 0.5):
        return "continue"
    return best


def expected_level(probs: dict[str, float]) -> float:
    return sum(int(k) * p for k, p in probs.items())


def auroc(pos: list[float], neg: list[float]) -> float:
    """Probability that a positive scores above a negative (ties count half)."""
    if not pos or not neg:
        return float("nan")
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


def score(engine, reqs: list[dict], gold: dict) -> dict:
    n: dict[str, int] = {}
    ok: dict[str, int] = {}
    esc: dict[str, tuple[list[float], list[float]]] = {}  # "" (all) or a variant -> (≥3, below)

    def add(key: str, hit: bool) -> None:
        n[key] = n.get(key, 0) + 1
        ok[key] = ok.get(key, 0) + int(hit)

    for r in reqs:
        resp = engine.evaluate(SystemOneRequest(state=r["state"], questions=r["questions"]))
        g = gold[r["id"]]
        for name, a in resp.answers.items():
            probs = {k: float(v) for k, v in a.probabilities.items()}
            best = max(probs, key=probs.get)
            if name == "react":
                add(f"{r['variant']} react", best == g[name])
                if r["variant"] == "prod":
                    add("prod react, planner", acted(probs) == g[name])
                    if g[name] == "continue":
                        add("prod react, gold continue", best == g[name])
            else:
                for key in (name, f"{r['variant']} {name}"):
                    add(key, int(best) == g[name])
                    add(f"{key} ±1", abs(int(best) - g[name]) <= 1)
                if name == "escalate":
                    for key in ("", r["variant"]):
                        pos, neg = esc.setdefault(key, ([], []))
                        (pos if g[name] >= 3 else neg).append(expected_level(probs))
                    # what the planner does with it: the large model is called from the expected level 3 on
                    add(
                        f"{r['variant']} escalate: call or not",
                        (expected_level(probs) >= 3) == (g[name] >= 3),
                    )
    out = {k: f"{ok[k] / n[k]:.0%} ({ok[k]}/{n[k]})" for k in sorted(n)}
    for key, (pos, neg) in sorted(esc.items()):
        if pos and neg:
            out[f"{key + ' ' if key else ''}escalate AUROC (≥3)"] = (
                f"{auroc(pos, neg):.2f} ({len(pos)}/{len(neg)})"
            )
    return out


def load_gold(path: str) -> dict[str, list[int]]:
    """evals/npc-decisions/escalate_gold.yaml: `id: [gold, acceptable...]` per line."""
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if line:
            key, levels = line.split(":", 1)
            out[key.strip()] = json.loads(levels)
    return out


def score_eval(engine, eval_dir: str, question: dict) -> dict:
    """The escalate question asked of the 76 eval scenes, against the hand-labelled escalate_gold.yaml."""
    gold = load_gold(os.path.join(eval_dir, "escalate_gold.yaml"))
    pos, neg, ok, near = [], [], 0, 0
    for line in open(os.path.join(eval_dir, "requests.jsonl")):
        r = json.loads(line)
        if r["cond"] != "en":
            continue
        resp = engine.evaluate(SystemOneRequest(state=r["state"], questions={"escalate": question}))
        probs = {k: float(v) for k, v in resp.answers["escalate"].probabilities.items()}
        best = int(max(probs, key=probs.get))
        g = gold[r["id"]]
        ok += best in g
        near += abs(best - g[0]) <= 1
        (pos if g[0] >= 3 else neg).append(expected_level(probs))
    n = len(pos) + len(neg)
    return {
        "eval escalate acceptable": f"{ok / n:.0%}",
        "eval escalate ±1": f"{near / n:.0%}",
        "eval escalate AUROC (≥3)": f"{auroc(pos, neg):.2f} ({len(pos)}/{len(neg)})",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--eval", help="evals/npc-decisions directory: escalate vs escalate_gold.yaml")
    ap.add_argument("ckpts", nargs="+", help="name=checkpoint")
    args = ap.parse_args()
    reqs = [json.loads(line) for line in open(os.path.join(args.data, "train", "val_requests.jsonl"))]
    gold = json.load(open(os.path.join(args.data, "train", "val_gold.json")))
    question = next(r["questions"]["escalate"] for r in reqs if "escalate" in r["questions"])
    for spec in args.ckpts:
        name, ckpt = spec.split("=", 1)
        engine = load_engine(ckpt)
        out = score(engine, reqs, gold)
        if args.eval:
            out |= score_eval(engine, args.eval, question)
        del engine
        torch.cuda.empty_cache()
        print(name, json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
