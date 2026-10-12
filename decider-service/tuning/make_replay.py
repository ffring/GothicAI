# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "pyarrow"]
# ///
"""Replay rows: general typed decisions the parent model was trained on, so fine-tuning on Gothic
scenes does not erase what it can do elsewhere. finetune.py pulls the model toward the parent's own
answers on them (KL), alongside their labels.

Source: the validation split of tasksource/tasksource-jev-typed-decisions (rows marked for commercial
use only), downloaded to data/replay/validation.parquet:

    curl -L -o data/replay/validation.parquet https://huggingface.co/datasets/tasksource/\\
tasksource-jev-typed-decisions/resolve/71f31c69db4325a92f8639093362578b696ba62f/data/validation-00000-of-00001.parquet
    uv run make_replay.py [--rows 1500] [--per-source 12]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
REPLAY = HERE / "data" / "replay"
NOUL_FALSE = "the statement does not hold for this state"
NOUL_TRUE = "the statement holds for this state"


def to_example(r) -> dict | None:
    target = list(r.target)
    if r.kind == "noul":
        if len(target) != 1 or target[0] not in (0.0, 1.0):
            return None
        options, label = [["false", NOUL_FALSE], ["true", NOUL_TRUE]], int(target[0])
    else:
        opts = list(r.options)
        if not 2 <= len(opts) <= 10 or len(target) != len(opts) or max(target) != 1.0 or sum(target) != 1.0:
            return None
        if len(set(opts)) != len(opts):
            return None
        label = target.index(1.0)
        if r.kind == "choice":
            options = [[" ".join(o.split()), ""] for o in opts]
            if any(len(o[0]) > 300 or not o[0] for o in options):
                return None
        else:
            options = [[str(i), " ".join(str(o).split())] for i, o in enumerate(opts)]
    return {
        "kind": r.kind,
        "state": r.state,
        "instructions": r.question,
        "options": options,
        "label": label,
        "task": f"replay:{r.source}",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1500)
    ap.add_argument("--per-source", type=int, default=12)
    ap.add_argument("--heldout", type=int, default=500)
    args = ap.parse_args()

    df = pd.read_parquet(REPLAY / "validation.parquet")
    df = df[(df.license_use == "commercial") & df.state.str.len().between(50, 1500)]
    rows = [ex for r in df.itertuples() if (ex := to_example(r)) is not None]
    rnd = random.Random(0)
    rnd.shuffle(rows)
    per_source: dict[str, int] = {}
    picked = []
    for ex in rows:
        if per_source.get(ex["task"], 0) < args.per_source:
            per_source[ex["task"]] = per_source.get(ex["task"], 0) + 1
            picked.append(ex)
        if len(picked) == args.rows:
            break
    with (REPLAY / "replay.jsonl").open("w") as out:
        for ex in picked:
            out.write(json.dumps(ex, ensure_ascii=False) + "\n")
    # rows of sources never trained on here: what fine-tuning may have cost elsewhere
    used = set(per_source)
    heldout = [ex for ex in rows if ex["task"] not in used][: args.heldout]
    if len(heldout) < args.heldout:
        rest = [ex for ex in rows if ex not in picked]
        heldout += rest[: args.heldout - len(heldout)]
    with (REPLAY / "replay_heldout.jsonl").open("w") as out:
        for ex in heldout:
            out.write(json.dumps(ex, ensure_ascii=False) + "\n")
    kinds = pd.Series([ex["kind"] for ex in picked]).value_counts().to_dict()
    print(f"{len(picked)} replay rows from {len(per_source)} sources: {kinds}; {len(heldout)} held out")


if __name__ == "__main__":
    main()
