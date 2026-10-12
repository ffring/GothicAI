"""Rows where the two gpt-6.1-sol passes disagree, for a third, blind judge (Claude) to break the tie.

    uv run --project ../../server python disputes.py export [--batch 40]  # data/disputes/batch_NN.txt
    uv run --project ../../server python disputes.py collect  # answers_NN.json -> data/labels_C.jsonl

A batch is the prompt with the disputed questions only.

The judge sees the same instructions and state as the labeller (evals/npc-decisions/run_gpt.py), the
questions in their original order, and neither pass's answer. make_train.py --judge C keeps a disputed
answer only where the judge sides with one of the passes, and only for training: validation stays
what both passes agreed on, so models trained with and without the judge are scored alike.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = DATA / "disputes"
sys.path.insert(0, str(HERE.parent.parent / "evals" / "npc-decisions"))
from label import parse  # noqa: E402
from make_train import load  # noqa: E402
from run_gpt import INSTRUCTIONS, render  # noqa: E402

ANSWER = (
    "Write your answers as JSON to {path}: "
    '{{"answers": [{{"id": "<situation id>", "<question>": <answer>, ...}}, ...]}}, one entry per situation, '
    "every question answered."
)


def disputed() -> list[dict]:
    rows = [json.loads(line) for line in (DATA / "rows.jsonl").open()]
    a, b = load("A"), load("B")
    out = []
    for row in rows:
        if row["id"] not in a or row["id"] not in b:
            continue
        names = [n for n in row["questions"] if n in a[row["id"]] and n in b[row["id"]]]
        names = [n for n in names if a[row["id"]][n] != b[row["id"]][n]]
        if names:
            out.append({**row, "questions": {n: row["questions"][n] for n in names}})
    return out


def export(batch: int) -> None:
    """New batches for the disputes not exported before (more labels may have come since)."""
    OUT.mkdir(parents=True, exist_ok=True)
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    rows = [r for r in disputed() if r["id"] not in index]
    first = len(list(OUT.glob("batch_*.txt")))
    for i in range(0, len(rows), batch):
        n = first + i // batch
        chunk = rows[i : i + batch]
        text = "\n\n".join(
            [INSTRUCTIONS, ANSWER.format(path=OUT / f"answers_{n:02d}.json"), *(render(r) for r in chunk)]
        )
        (OUT / f"batch_{n:02d}.txt").write_text(text + "\n")
    index |= {r["id"]: r["questions"] for r in rows}
    index_path.write_text(json.dumps(index, ensure_ascii=False))
    questions = sum(len(r["questions"]) for r in rows)
    last = first + (len(rows) - 1) // batch
    print(f"{len(rows)} new rows, {questions} disputed questions, batches {first}..{last}")


def collect() -> None:
    index = json.loads((OUT / "index.json").read_text())
    got: dict = {}
    for path in sorted(OUT.glob("answers_*.json")):
        for ans in json.loads(path.read_text())["answers"]:
            got[ans["id"]] = ans
    kept = missing = 0
    with (DATA / "labels_C.jsonl").open("w") as out:
        for rid, questions in index.items():
            ans = parse({"questions": questions}, got.get(rid, {}))
            if ans is None:
                missing += 1
                continue
            out.write(json.dumps({"id": rid, "answers": ans}, ensure_ascii=False) + "\n")
            kept += 1
    print(f"{kept} rows judged, {missing} missing or invalid")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["export", "collect"])
    ap.add_argument("--batch", type=int, default=40)
    args = ap.parse_args()
    export(args.batch) if args.what == "export" else collect()


if __name__ == "__main__":
    main()
