"""Rows + both label passes -> training files for finetune.py (strands-decider Examples).

An answer is kept only where passes A and B agree. Every tenth card goes to validation, whole: both
its rows, so no scene is in train and val at once.

    cd decider-service/tuning
    python3 make_train.py

Writes data/train/:
  gothic_train.jsonl, gothic_val.jsonl   Examples (one per kept question)
  val_requests.jsonl, val_gold.json      the validation rows as decider requests, for score_val.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = DATA / "train"


def load(pass_: str) -> dict[str, dict]:
    """Row id -> answers, merged over all label files of the pass (labels_A*.jsonl)."""
    out: dict[str, dict] = {}
    for path in sorted(DATA.glob(f"labels_{pass_}*.jsonl")):
        for d in map(json.loads, path.open()):
            out.setdefault(d["id"], {}).update(d["answers"])
    return out


def example(row: dict, name: str, answer) -> dict:
    q = row["questions"][name]
    if q["type"] == "choice":
        options = [[k, v] for k, v in q["criteria"].items()]
        label = list(q["criteria"]).index(answer)
    else:  # score
        options = [[str(i), c] for i, c in enumerate(q["criteria"])]
        label = int(answer)
    task = f"gothic:{row['variant']}_{name}" if name == "react" else f"gothic:{name}"
    return {
        "kind": q["type"],
        "state": row["state"],
        "instructions": q["instructions"],
        "options": options,
        "label": label,
        "task": task,
    }


def main() -> None:
    rows = [json.loads(line) for line in (DATA / "rows.jsonl").open()]
    a, b = load("A"), load("B")
    OUT.mkdir(parents=True, exist_ok=True)
    stats: Counter = Counter()
    picked: Counter = Counter()
    train, val, val_reqs, val_gold = [], [], [], {}
    for row in rows:
        if row["id"] not in a or row["id"] not in b:
            stats["unlabelled"] += 1
            continue
        is_val = row["card"] % 10 == 0
        kept = {}
        for name in row["questions"]:
            key = f"{row['variant']}_{name}"
            if name not in a[row["id"]] or name not in b[row["id"]]:
                stats[f"{key} unlabelled"] += 1
                continue
            stats[f"{key} labelled"] += 1
            va, vb = a[row["id"]][name], b[row["id"]][name]
            if va != vb:
                stats[f"{key} disagree"] += 1
                if row["questions"][name]["type"] == "score" and abs(va - vb) == 1:
                    stats[f"{key} disagree by one level"] += 1
                continue
            kept[name] = va
            if name == "react":
                picked[(row["variant"], kept[name] if kept[name] in ("continue",) else "other")] += 1
            elif name == "escalate":
                picked[("escalate", va)] += 1
            (val if is_val else train).append(example(row, name, kept[name]))
        if is_val and kept:
            val_reqs.append(
                {
                    "id": row["id"],
                    "variant": row["variant"],
                    "state": row["state"],
                    "questions": {n: row["questions"][n] for n in kept},
                }
            )
            val_gold[row["id"]] = kept
    for name, items in (("gothic_train", train), ("gothic_val", val)):
        with (OUT / f"{name}.jsonl").open("w") as out:
            for ex in items:
                out.write(json.dumps(ex, ensure_ascii=False) + "\n")
    with (OUT / "val_requests.jsonl").open("w") as out:
        for r in val_reqs:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    (OUT / "val_gold.json").write_text(json.dumps(val_gold, ensure_ascii=False, indent=0))
    print(f"train {len(train)}, val {len(val)} examples; {len(val_reqs)} val requests")
    for k in sorted(stats):
        print(f"  {k}: {stats[k]}")
    print("  answers:", dict(sorted(picked.items(), key=str)))


if __name__ == "__main__":
    main()
