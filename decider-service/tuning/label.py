"""Labels for data/rows.jsonl from gpt-6.1-sol, blind: the prompt is the eval baseline's
(evals/npc-decisions/run_gpt.py), with no hint of what the card's author had in mind.

Two passes: `A` as written, `B` with every menu reversed and the rows grouped differently. make_train.py
keeps an answer only where both passes agree, which drops ambiguous rows and position bias.

    cd decider-service/tuning
    uv run --project ../../server python label.py A [--batch 8] [--jobs 4]
    uv run --project ../../server python label.py B

Writes data/labels_<pass>.jsonl, continuing where a previous run stopped. With `--only <question>`, asks
just that question of rows labelled before (it did not exist yet, or its wording changed), into
data/labels_<pass>_<question>.jsonl, which takes precedence over the answers in other files:

    uv run --project ../../server python label.py A --only escalate --batch 12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from pathlib import Path

from gothic_ai.llm import Done, LLMError, Message, make_backend

HERE = Path(__file__).parent
DATA = HERE / "data"
sys.path.insert(0, str(HERE.parent.parent / "evals" / "npc-decisions"))
from run_gpt import INSTRUCTIONS, render  # noqa: E402


def reverse_menus(row: dict) -> dict:
    qs = {}
    for name, q in row["questions"].items():
        if q["type"] == "choice":
            q = {**q, "criteria": dict(reversed(list(q["criteria"].items())))}
        qs[name] = q
    return {**row, "questions": qs}


def answered(pass_: str) -> dict[str, set[str]]:
    """Row id -> the questions some label file of this pass already answers."""
    out: dict[str, set[str]] = {}
    for path in DATA.glob(f"labels_{pass_}*.jsonl"):
        for line in path.open():
            d = json.loads(line)
            out.setdefault(d["id"], set()).update(d["answers"])
    return out


def parse(row: dict, got: dict) -> dict | None:
    """The answers of one row, or None if any is missing or invalid."""
    out = {}
    for name, q in row["questions"].items():
        v = got.get(name)
        if q["type"] == "choice":
            if v not in q["criteria"]:
                return None
        elif q["type"] == "score":
            try:
                v = int(v)
            except (TypeError, ValueError):
                return None
            if not 0 <= v < len(q["criteria"]):
                return None
        out[name] = v
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pass_", choices=["A", "B"])
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--only", help="label just this question, for rows labelled before it existed")
    args = ap.parse_args()

    rows = [json.loads(line) for line in (DATA / "rows.jsonl").open()]
    if args.only:
        # its own file overrides older answers to the question (make_train.py merges files in name order)
        out_path = DATA / f"labels_{args.pass_}_{args.only}.jsonl"
        known = answered(args.pass_)
        done = {json.loads(line)["id"] for line in out_path.open()} if out_path.exists() else set()
        todo = [
            {**r, "questions": {args.only: r["questions"][args.only]}}
            for r in rows
            if args.only in r["questions"] and r["id"] in known and r["id"] not in done
        ]
    else:
        out_path = DATA / f"labels_{args.pass_}.jsonl"
        done = {json.loads(line)["id"] for line in out_path.open()} if out_path.exists() else set()
        todo = [r for r in rows if r["id"] not in done]
    if args.pass_ == "B":
        todo = [reverse_menus(r) for r in todo]
        random.Random(7).shuffle(todo)
    batches = [todo[i : i + args.batch] for i in range(0, len(todo), args.batch)]
    print(f"{len(rows)} rows, {len(done)} labelled, {len(batches)} calls to go", flush=True)

    backend = make_backend("chatgpt", reasoning_effort=args.effort)
    sem = asyncio.Semaphore(args.jobs)
    lock = asyncio.Lock()
    stats = {"labelled": 0, "missing": 0}

    async def one(i: int, chunk: list[dict]) -> None:
        prompt = "\n\n".join(render(r) for r in chunk)
        got: dict = {}
        async with sem:
            for attempt in range(3):
                try:
                    text = ""
                    messages = [Message("user", prompt)]
                    async for ev in backend.stream(instructions=INSTRUCTIONS, messages=messages):
                        if isinstance(ev, Done):
                            text = ev.text
                    m = re.search(r"\{.*\}", text, re.DOTALL)
                    got = {a["id"]: a for a in json.loads(m.group(0))["answers"]} if m else {}
                    break
                except (LLMError, json.JSONDecodeError, KeyError, TypeError) as e:
                    if isinstance(e, LLMError) and not e.retryable:
                        raise
                    print(f"call {i}: {e!r}, retry {attempt + 1}", flush=True)
                    await asyncio.sleep(5 * (attempt + 1))
        async with lock:
            with out_path.open("a") as out:
                for r in chunk:
                    ans = parse(r, got.get(r["id"], {}))
                    if ans is None:
                        stats["missing"] += 1
                        continue
                    out.write(json.dumps({"id": r["id"], "answers": ans}, ensure_ascii=False) + "\n")
                    stats["labelled"] += 1
            if i % 10 == 0:
                print(f"call {i}/{len(batches)}: {stats}", flush=True)

    try:
        await asyncio.gather(*(one(i, b) for i, b in enumerate(batches)))
    finally:
        await backend.aclose()
    print(json.dumps(stats))


if __name__ == "__main__":
    asyncio.run(main())
