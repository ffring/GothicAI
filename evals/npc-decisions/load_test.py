"""Load test: can one Strands Decider on the 3090 serve many NPCs at once?

    .venv-strands/bin/python load_test.py [--tag base]

The stock engine answers one request (one NPC state) per forward pass. Here requests from many
NPCs are packed into one forward pass: every (state + question) becomes a row of the batch.

Steps:
  1. check: batched answers match engine.evaluate() on the 76 situations;
  2. sweep: time of one batch of B requests, short states (~150 tokens) and long (~550 tokens);
  3. live: N NPCs, each sends a request at random intervals (mean T s); a dynamic batcher takes
     everything that is waiting and runs it as one batch; end-to-end latency per request;
  4. burst: N requests arrive at the same moment.
Output: load/<tag>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path

import torch
from strands_decider.infer import _option_token_index, _to_answer, load_engine
from strands_decider.prompting import render_question, render_state
from strands_decider.schema import SystemOneRequest

HERE = Path(__file__).parent
CKPT = "StrandsAgents/strands-decider-2B-hobson-v19"
MEM_CAP_GB = 9

# Extra context a production state would carry: memories and a plan from the big model.
MEMORY = [
    "Yesterday the hero bought two healing potions from a trader at the market.",
    "Two days ago a militia patrol arrested a thief near the harbour.",
    "The hero was seen talking to Lord Andre in the barracks.",
    "Last night wolves came close to the farms outside the city gate.",
    "The hero helped a farmer drive off bandits on the road to the tavern.",
    "Rumour says the paladins will not let anyone into the upper quarter without permission.",
    "You argued with your neighbour about a debt of twenty gold coins.",
    "The hero asked you about the way to the monastery three days ago.",
    "A merchant ship has not arrived in the harbour for weeks.",
    "The hero wears better armor than last week and seems to have earned some gold.",
    "Orcs were seen in the Valley of Mines; people are afraid.",
    "Your plan for today: work until evening, then go to the tavern and listen to the news.",
]


def long_state(state: str, seed: int) -> str:
    rnd = random.Random(seed)
    facts = rnd.sample(MEMORY, 10)
    return state + " What you remember: " + " ".join(facts)


class BatchEngine:
    """Many requests -> one forward pass, using the engine's own tokenisation and readout."""

    def __init__(self, engine, token_budget: int = 8192) -> None:
        self.e = engine
        self.token_budget = token_budget

    def _forward(self, rows, opt_rows, n_slots, kinds) -> list[list[float]]:
        e = self.e
        ids, mask = e._pad(rows)
        width = max(len(r) for r in opt_rows)
        opt_idx = torch.tensor([r + [-1] * (width - len(r)) for r in opt_rows], device=e.device)
        out = e.model(
            input_ids=ids,
            attention_mask=mask,
            n_slots=torch.tensor(n_slots, device=e.device),
            temperature=e._temperatures(kinds),
            opt_idx=opt_idx,
        )
        return out["log_probs"].exp().float().cpu().tolist()

    @torch.inference_mode()
    def evaluate_many(self, reqs: list[SystemOneRequest]) -> list[dict]:
        e = self.e
        rows, opt_rows, n_slots, kinds, owners = [], [], [], [], []
        rendered_all = []
        for ri, req in enumerate(reqs):
            names = list(req.questions)
            rendered = [render_question(req.questions[n]) for n in names]
            s, q = e._fit(render_state(req.state), [rq.text for rq in rendered])
            for name, rq, qi, offs in zip(names, rendered, q, e._last_offsets, strict=True):
                rows.append(s + qi)
                opt_rows.append([len(s) + i for i in _option_token_index(offs, rq.option_spans, 0)])
                n_slots.append(rq.n_slots)
                kinds.append(rq.kind)
                owners.append((ri, name))
                rendered_all.append(rq)
        # similar lengths together (less padding), and at most TOKEN_BUDGET padded tokens per forward
        order = sorted(range(len(rows)), key=lambda i: len(rows[i]))
        probs_by_row: dict[int, list[float]] = {}
        chunk: list[int] = []
        for i in order + [None]:
            if i is not None and (len(chunk) + 1) * len(rows[i]) <= self.token_budget:
                chunk.append(i)
                continue
            if chunk:
                probs = self._forward([rows[j] for j in chunk], [opt_rows[j] for j in chunk],
                                      [n_slots[j] for j in chunk], [kinds[j] for j in chunk])
                for j, p in zip(chunk, probs, strict=True):
                    probs_by_row[j] = p
            chunk = [i] if i is not None else []
        answers: list[dict] = [{} for _ in reqs]
        for i, ((ri, name), rq) in enumerate(zip(owners, rendered_all, strict=True)):
            row = probs_by_row[i][: rq.n_slots]
            answers[ri][name] = _to_answer(rq, row, ordinal_smoothing=e.model.config.ordinal_smoothing).model_dump()
        return answers


def load_requests(long: bool) -> list[SystemOneRequest]:
    raw = [json.loads(x) for x in (HERE / "requests.jsonl").open()]
    raw = [r for r in raw if r["cond"] == "en"]
    out = []
    for i, r in enumerate(raw):
        state = long_state(r["state"], i) if long else r["state"]
        out.append(SystemOneRequest(state=state, questions=r["questions"]))
    return out


def tokens(engine, req: SystemOneRequest) -> int:
    return len(engine.tok(render_state(req.state))["input_ids"])


def sync() -> None:
    torch.cuda.synchronize()


def check(engine, batch: BatchEngine, reqs) -> dict:
    single = [{k: v.model_dump() for k, v in engine.evaluate(r).answers.items()} for r in reqs]
    many = batch.evaluate_many(reqs)
    same, max_diff = 0, 0.0
    for a, b in zip(single, many, strict=True):
        ra, rb = a["react"], b["react"]
        same += ra["choice"] == rb["choice"]
        max_diff = max(max_diff, *(abs(ra["probabilities"][k] - rb["probabilities"][k]) for k in ra["probabilities"]))
    return {"react_same": f"{same}/{len(reqs)}", "max_prob_diff": round(max_diff, 4)}


def sweep(engine, batch: BatchEngine, reqs, sizes, reps=5) -> list[dict]:
    res = []
    for b in sizes:
        pool = (reqs * (b // len(reqs) + 1))[:b]
        times = []
        for _ in range(reps):
            sync()
            t = time.perf_counter()
            batch.evaluate_many(pool)
            sync()
            times.append((time.perf_counter() - t) * 1000)
        ms = statistics.median(times)
        res.append({"batch": b, "ms": round(ms, 1), "req_per_s": round(b / ms * 1000, 1),
                    "peak_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2)})
        print(res[-1], flush=True)
    # the stock path, one request at a time with its shared-prefix cache
    sync()
    t = time.perf_counter()
    for r in reqs[:20]:
        engine.evaluate(r)
    sync()
    seq_ms = (time.perf_counter() - t) * 1000 / 20
    res.append({"batch": "sequential", "ms": round(seq_ms, 1), "req_per_s": round(1000 / seq_ms, 1)})
    print(res[-1], flush=True)
    return res


async def live(batch: BatchEngine, reqs, n_npc: int, mean_s: float, duration_s: float,
               max_batch: int = 128) -> dict:
    """N NPCs with exponential gaps between their requests; a dynamic batcher serves them."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    lat: list[float] = []
    sizes: list[int] = []
    stop = time.perf_counter() + duration_s

    async def npc(i: int) -> None:
        rnd = random.Random(i)
        await asyncio.sleep(rnd.uniform(0, mean_s))
        while time.perf_counter() < stop:
            fut = loop.create_future()
            await queue.put((time.perf_counter(), reqs[rnd.randrange(len(reqs))], fut))
            await fut
            await asyncio.sleep(rnd.expovariate(1 / mean_s))

    async def worker() -> None:
        while True:
            first = await queue.get()
            items = [first]
            while not queue.empty() and len(items) < max_batch:
                items.append(queue.get_nowait())
            sizes.append(len(items))
            await loop.run_in_executor(None, batch.evaluate_many, [it[1] for it in items])
            now = time.perf_counter()
            for t0, _, fut in items:
                lat.append((now - t0) * 1000)
                fut.set_result(None)

    w = asyncio.create_task(worker())
    await asyncio.gather(*(npc(i) for i in range(n_npc)))
    w.cancel()
    lat.sort()
    return {
        "npc": n_npc, "mean_interval_s": mean_s, "requests": len(lat),
        "req_per_s": round(len(lat) / duration_s, 1),
        "p50_ms": round(lat[len(lat) // 2], 1),
        "p95_ms": round(lat[int(len(lat) * 0.95)], 1),
        "max_ms": round(lat[-1], 1),
        "mean_batch": round(statistics.mean(sizes), 1),
    }


def burst(batch: BatchEngine, reqs, n: int) -> dict:
    pool = (reqs * (n // len(reqs) + 1))[:n]
    sync()
    t = time.perf_counter()
    batch.evaluate_many(pool)
    sync()
    return {"npc": n, "all_answered_ms": round((time.perf_counter() - t) * 1000, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="base")
    ap.add_argument("--live-seconds", type=float, default=30)
    ap.add_argument("--token-budget", type=int, default=8192, help="padded tokens per forward pass")
    args = ap.parse_args()

    # Voxtral and VoxCPM share this GPU: cap our share so an oversized batch fails here, not there
    torch.cuda.set_per_process_memory_fraction(MEM_CAP_GB * 2**30 / torch.cuda.get_device_properties(0).total_memory)
    engine = load_engine(CKPT)
    batch = BatchEngine(engine, args.token_budget)
    short, long_ = load_requests(False), load_requests(True)
    report = {
        "tag": args.tag,
        "token_budget": args.token_budget,
        "gpu": torch.cuda.get_device_name(0),
        "state_tokens": {
            "short_mean": round(statistics.mean(tokens(engine, r) for r in short)),
            "long_mean": round(statistics.mean(tokens(engine, r) for r in long_)),
        },
    }
    for r in short[:3]:  # warmup
        engine.evaluate(r)
    batch.evaluate_many(short[:8])
    print(report, flush=True)

    report["check"] = check(engine, batch, short)
    print("check", report["check"], flush=True)

    sizes = [1, 2, 4, 8, 16, 32, 50, 64, 100]
    report["sweep_short"] = sweep(engine, batch, short, sizes)
    report["sweep_long"] = sweep(engine, batch, long_, sizes)

    report["live"] = []
    for state_kind, pool in (("short", short), ("long", long_)):
        for n_npc, mean_s in ((10, 5), (25, 5), (50, 5), (100, 5), (50, 2), (100, 2)):
            r = asyncio.run(live(batch, pool, n_npc, mean_s, args.live_seconds))
            r["state"] = state_kind
            report["live"].append(r)
            print("live", r, flush=True)

    report["burst"] = [dict(burst(batch, pool, n), state=k)
                       for k, pool in (("short", short), ("long", long_)) for n in (10, 25, 50, 100)]
    print("burst", report["burst"], flush=True)

    out = HERE / "load"
    out.mkdir(exist_ok=True)
    (out / f"{args.tag}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
