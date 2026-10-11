"""Baseline: the same questions to gpt-6.1-sol through the server's LLM adapter (ChatGPT plan).

    cd evals/npc-decisions && uv run --project ../../server python run_gpt.py [--cond en] [--batch 4]

A few situations go into one request to spend less plan quota. The model answers with labels,
not probabilities, so its answers are written as one-hot distributions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from gothic_ai.llm import Done, Message, make_backend

HERE = Path(__file__).parent

INSTRUCTIONS = """\
You decide how non-player characters in the game Gothic 2 react to what happens around them.
For every situation you get the NPC's state and typed questions:
- choice: answer with exactly one option key;
- noul: answer true or false;
- score: answer with the level index (0 is the first level).
Answer as the NPC would, in character. Reply with JSON only:
{"answers": [{"id": "<situation id>", "<question>": <answer>, ...}, ...]}"""


def render(req: dict) -> str:
    lines = [f"### {req['id']}", f"STATE: {req['state']}"]
    for name, q in req["questions"].items():
        if q["type"] == "choice":
            opts = "; ".join(f"{k} = {v}" for k, v in q["criteria"].items())
            lines.append(f'{name} (choice): {q["instructions"]} Options: {opts}')
        elif q["type"] == "noul":
            c = q["criteria"]
            lines.append(f'{name} (noul): {q["instructions"]} true: {c["true"]} false: {c["false"]}')
        else:
            levels = "; ".join(f"{i} = {c}" for i, c in enumerate(q["criteria"]))
            lines.append(f'{name} (score): {q["instructions"]} Levels: {levels}')
    return "\n".join(lines)


def to_answers(req: dict, got: dict) -> dict:
    out = {}
    for name, q in req["questions"].items():
        v = got.get(name)
        if q["type"] == "choice":
            out[name] = {"probs": {k: float(k == v) for k in q["criteria"]}}
        elif q["type"] == "noul":
            out[name] = {"p_true": float(v is True or str(v).lower() == "true")}
        else:
            idx = int(v) if v is not None else -1
            out[name] = {"probs": [float(i == idx) for i in range(len(q["criteria"]))]}
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cond", default="en")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--effort", default="low")
    args = ap.parse_args()

    reqs = [r for r in map(json.loads, (HERE / "requests.jsonl").open()) if r["cond"] == args.cond]
    backend = make_backend("chatgpt", reasoning_effort=args.effort)
    out_dir = HERE / "preds"
    out_dir.mkdir(exist_ok=True)
    name = f"gpt-6.1-sol-{args.effort}"
    times = []
    try:
        with (out_dir / f"{name}.jsonl").open("w") as out:
            for i in range(0, len(reqs), args.batch):
                chunk = reqs[i : i + args.batch]
                prompt = "\n\n".join(render(r) for r in chunk)
                t = time.perf_counter()
                text = ""
                async for ev in backend.stream(
                    instructions=INSTRUCTIONS, messages=[Message("user", prompt)]
                ):
                    if isinstance(ev, Done):
                        text = ev.text
                ms = (time.perf_counter() - t) * 1000
                times.append(ms)
                m = re.search(r"\{.*\}", text, re.DOTALL)
                got = {a["id"]: a for a in json.loads(m.group(0))["answers"]} if m else {}
                for r in chunk:
                    out.write(json.dumps({"id": r["id"], "cond": r["cond"], "ms": round(ms / len(chunk), 1),
                                          "answers": to_answers(r, got.get(r["id"], {}))},
                                         ensure_ascii=False) + "\n")
                print(f"{i + len(chunk)}/{len(reqs)}  {ms:.0f} ms  missing: "
                      f"{[r['id'] for r in chunk if r['id'] not in got]}", flush=True)
    finally:
        await backend.aclose()
    times.sort()
    meta = {"model": name, "requests": len(reqs), "batch": args.batch,
            "call_ms_p50": round(times[len(times) // 2], 1)}
    (out_dir / f"{name}.meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta))


if __name__ == "__main__":
    asyncio.run(main())
