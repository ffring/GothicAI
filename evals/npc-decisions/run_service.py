"""Run requests.jsonl through a running decider service and write predictions like run_decider.py.

    cd evals/npc-decisions
    uv run --project ../../decider-service python run_service.py ws://<gpu-host>:47832 --name service-v1-2610

All requests go at once into the background lane, so this also exercises the service's batching.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from websockets.asyncio.client import connect

HERE = Path(__file__).parent


def normalize(questions: dict, raw: dict) -> dict:
    out = {}
    for name, q in questions.items():
        a = raw[name]
        if q["type"] == "choice":
            out[name] = {"probs": a["probabilities"]}
        elif q["type"] == "noul":
            out[name] = {"p_true": a["noul"]}
        else:
            out[name] = {"probs": [a["probabilities"][str(i)] for i in range(len(q["criteria"]))]}
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--name", required=True)
    args = ap.parse_args()

    reqs = [json.loads(x) for x in (HERE / "requests.jsonl").open()]
    results: dict[str, dict] = {}
    async with connect(args.url, max_size=2**22) as ws:
        print(json.loads(await ws.recv()))
        t0 = time.perf_counter()
        for i, r in enumerate(reqs):
            await ws.send(json.dumps({"type": "decide", "id": str(i), "lane": "background",
                                      "deadline_ms": 60_000, "state": r["state"],
                                      "questions": r["questions"]}, ensure_ascii=False))
        while len(results) < len(reqs):
            msg = json.loads(await ws.recv())
            results[msg["id"]] = msg
        wall = time.perf_counter() - t0
        await ws.send(json.dumps({"type": "stats"}))
        stats = json.loads(await ws.recv())

    bad = [m for m in results.values() if m["type"] != "answers"]
    assert not bad, bad[:3]
    out = HERE / "preds" / f"{args.name}.jsonl"
    with out.open("w") as f:
        for i, r in enumerate(reqs):
            m = results[str(i)]
            f.write(json.dumps({"id": r["id"], "cond": r["cond"], "ms": m["infer_ms"],
                                "answers": normalize(r["questions"], m["answers"])}, ensure_ascii=False) + "\n")
    print(f"{len(reqs)} requests in {wall:.1f} s ({len(reqs) / wall:.1f}/s); "
          f"passes {stats['passes']}, batch p50 {stats['batch_p50']}, infer p50 {stats['infer_ms_p50']} ms")


if __name__ == "__main__":
    asyncio.run(main())
