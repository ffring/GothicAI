"""Score checkpoints on the `voice` question (make_voice.py): the held-out phrases
(data/voice/gothic_val.jsonl) and evals/talk-reactions (data/voice/eval_requests.jsonl), with the server's
thresholds; and how much the gesture asked with it moved against the first checkpoint. Runs on the GPU
host, in the decider service's venv:

    ~/gothic-ai/decider/.venv/bin/python score_voice.py --data data g7=ckpt/g7 g8=ckpt/g8

Per checkpoint: right / by kind, answered over the thresholds and right among them, and a strong sound out
of place (surprise, displeasure or doubt over its threshold where it does not fit). The eval's phrases
may have several right kinds (`good`, the first is the best).
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

import torch
from strands_decider.infer import load_engine
from strands_decider.schema import SystemOneRequest

STRONG = ("surprise", "displeasure", "doubt")


def ask(engine, state: str, questions: dict) -> dict[str, dict[str, float]]:
    resp = engine.evaluate(SystemOneRequest(state=state, questions=questions))
    return {q: {k: float(v) for k, v in a.probabilities.items()} for q, a in resp.answers.items()}


def confidence(probs: dict[str, float]) -> float:
    """The normalised top probability the service answers with (System One API, for choice)."""
    n = len(probs)
    return max(0.0, (n * max(probs.values()) - 1) / (n - 1))


def report(rows: list[tuple[list[str], dict[str, float]]], thresholds: dict[str, float]) -> dict:
    """rows: (right kinds, the best first; voice probabilities)."""
    n = len(rows)
    right = best = answered = answered_right = jarring = 0
    by_kind: dict[str, Counter] = {}
    for good, probs in rows:
        choice = max(probs, key=probs.get)
        right += choice in good
        best += choice == good[0]
        by_kind.setdefault(good[0], Counter())[choice] += 1
        if confidence(probs) >= thresholds[choice]:
            answered += 1
            answered_right += choice in good
            jarring += choice in STRONG and choice not in good
    out = {
        "right": f"{right / n:.0%} ({right}/{n})",
        "best": f"{best}/{n}",
        "over thresholds": f"{answered}/{n}, right {answered_right}/{answered}",
        "strong out of place": f"{jarring}/{n}",
    }
    for kind, c in sorted(by_kind.items()):
        misses = ", ".join(f"{k} {v}" for k, v in c.most_common() if k != kind)
        out[kind] = f"{c[kind]}/{sum(c.values())} {misses}"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--voice", default="voice", help="make_voice.py's directory under --data")
    ap.add_argument("ckpts", nargs="+", help="name=checkpoint")
    args = ap.parse_args()
    d = os.path.join(args.data, args.voice)
    meta = json.load(open(os.path.join(d, "questions.json"), encoding="utf-8"))
    questions, thresholds = meta["questions"], meta["thresholds"]
    val = [json.loads(line) for line in open(os.path.join(d, "gothic_val.jsonl"), encoding="utf-8")]
    evals = [json.loads(line) for line in open(os.path.join(d, "eval_requests.jsonl"), encoding="utf-8")]
    first_gestures: list[str] | None = None
    for spec in args.ckpts:
        name, ckpt = spec.split("=", 1)
        engine = load_engine(ckpt)
        val_rows = []
        for r in val:
            probs = ask(engine, r["state"], {"voice": questions["voice"]})["voice"]
            val_rows.append(([r["options"][r["label"]][0]], probs))
        eval_rows, gestures, picks = [], [], []
        for r in evals:
            answers = ask(engine, r["state"], questions)
            eval_rows.append((r["good"], answers["voice"]))
            gestures.append(max(answers["gesture"], key=answers["gesture"].get))
            v = answers["voice"]
            choice = max(v, key=v.get)
            conf = round(confidence(v), 3)
            picks.append(
                {"i": r["i"], "say": r["say"], "choice": choice, "confidence": conf, "gesture": gestures[-1]}
            )
        del engine
        torch.cuda.empty_cache()
        out = {"val": report(val_rows, thresholds), "eval": report(eval_rows, thresholds)}
        if first_gestures is None:
            first_gestures = gestures
        else:
            same = sum(a == b for a, b in zip(first_gestures, gestures, strict=True))
            out["gesture same as the first"] = f"{same}/{len(gestures)}"
        out["gestures"] = dict(Counter(gestures).most_common())
        print(name, json.dumps(out, ensure_ascii=False, indent=1), flush=True)
        with open(os.path.join(d, f"eval_picks_{name}.jsonl"), "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(p, ensure_ascii=False) + "\n" for p in picks)


if __name__ == "__main__":
    main()
