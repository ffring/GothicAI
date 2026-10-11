"""Load test for a whole island of NPCs with tiered update rates.

    .venv-strands-fast/bin/python island_test.py

1. capacity with fewer questions per request: react only / react + attitude / all (3-4);
2. tiered live load, each tier with its own rate and question set; near NPCs go first in the queue.
Output: load/island.json
"""

from __future__ import annotations

import asyncio
import json
import random
import statistics
import time

import torch
from load_test import CKPT, HERE, MEM_CAP_GB, BatchEngine, load_requests, sync
from strands_decider.infer import load_engine
from strands_decider.schema import SystemOneRequest

LIVE_S = 60
MAX_BATCH = 32  # bounds how long a near NPC can wait behind a batch already running


def only(reqs: list[SystemOneRequest], names: tuple[str, ...]) -> list[SystemOneRequest]:
    return [SystemOneRequest(state=r.state, questions={n: r.questions[n] for n in names}) for r in reqs]


def capacity(batch: BatchEngine, pool, sizes=(1, 8, 32, 64, 128), reps=3) -> list[dict]:
    res = []
    for b in sizes:
        reqs = (pool * (b // len(pool) + 1))[:b]
        times = []
        for _ in range(reps):
            sync()
            t = time.perf_counter()
            batch.evaluate_many(reqs)
            sync()
            times.append((time.perf_counter() - t) * 1000)
        ms = statistics.median(times)
        res.append({"batch": b, "ms": round(ms, 1), "req_per_s": round(b / ms * 1000, 1)})
    return res


async def tiered(batch: BatchEngine, tiers: list[dict]) -> dict:
    loop = asyncio.get_running_loop()
    pending: list[tuple] = []  # (priority, t0, request, future)
    wake = asyncio.Event()
    lat: dict[str, list[float]] = {t["name"]: [] for t in tiers}
    sizes: list[int] = []
    stop = time.perf_counter() + LIVE_S

    async def npc(tier: dict, prio: int, i: int) -> None:
        rnd = random.Random(f"{tier['name']}{i}")
        await asyncio.sleep(rnd.uniform(0, tier["every_s"]))
        while time.perf_counter() < stop:
            fut = loop.create_future()
            pending.append((prio, time.perf_counter(), tier["pool"][rnd.randrange(len(tier["pool"]))], fut, tier["name"]))
            wake.set()
            await fut
            await asyncio.sleep(rnd.expovariate(1 / tier["every_s"]))

    async def worker() -> None:
        while True:
            if not pending:
                wake.clear()
                await wake.wait()
            pending.sort(key=lambda x: (x[0], x[1]))
            items = pending[:MAX_BATCH]
            del pending[:MAX_BATCH]
            sizes.append(len(items))
            await loop.run_in_executor(None, batch.evaluate_many, [it[2] for it in items])
            now = time.perf_counter()
            for _, t0, _, fut, name in items:
                lat[name].append((now - t0) * 1000)
                fut.set_result(None)

    w = asyncio.create_task(worker())
    await asyncio.gather(*(npc(t, p, i) for p, t in enumerate(tiers) for i in range(t["npc"])))
    w.cancel()
    out = {"req_per_s": round(sum(map(len, lat.values())) / LIVE_S, 1),
           "mean_batch": round(statistics.mean(sizes), 1), "tiers": []}
    for t in tiers:
        v = sorted(lat[t["name"]])
        out["tiers"].append({
            "tier": t["name"], "npc": t["npc"], "every_s": t["every_s"], "questions": t["questions"],
            "requests": len(v), "p50_ms": round(v[len(v) // 2], 1),
            "p95_ms": round(v[int(len(v) * 0.95)], 1), "max_ms": round(v[-1], 1),
        })
    return out


def main() -> None:
    torch.cuda.set_per_process_memory_fraction(MEM_CAP_GB * 2**30 / torch.cuda.get_device_properties(0).total_memory)
    engine = load_engine(CKPT)
    batch = BatchEngine(engine)
    short, long_ = load_requests(False), load_requests(True)
    sets = {
        "react": ("react",),
        "react+attitude": ("react", "attitude"),
    }
    pools = {(k, s): only(p, q) for k, q in sets.items() for s, p in (("short", short), ("long", long_))}
    pools[("all", "short")], pools[("all", "long")] = short, long_
    batch.evaluate_many(short[:8])  # warmup

    report = {"capacity": []}
    for (qs, state), pool in pools.items():
        r = {"questions": qs, "state": state, "sweep": capacity(batch, pool)}
        report["capacity"].append(r)
        print("capacity", r, flush=True)

    def tier(name, n, every, qs, state):
        return {"name": name, "npc": n, "every_s": every, "questions": qs, "pool": pools[(qs, state)]}

    scenarios = {
        # what the user proposed: near every 0.5 s, far every 5-20 s, full question set
        "proposed": [tier("near", 6, 0.5, "all", "long"), tier("mid", 20, 5, "all", "long"),
                     tier("far", 125, 20, "all", "long")],
        # event-driven near, one question for routine checks, coarse far decisions
        "island": [tier("near", 8, 2, "react+attitude", "long"), tier("mid", 30, 10, "react", "long"),
                   tier("far", 250, 60, "react", "short")],
        # the same island, with near NPCs polled every 0.5 s
        "island_fast_near": [tier("near", 8, 0.5, "react+attitude", "long"), tier("mid", 30, 5, "react", "long"),
                             tier("far", 250, 20, "react", "short")],
    }
    report["tiered"] = {}
    for name, tiers in scenarios.items():
        r = asyncio.run(tiered(batch, tiers))
        report["tiered"][name] = r
        print("tiered", name, json.dumps(r), flush=True)

    (HERE / "load").mkdir(exist_ok=True)
    (HERE / "load" / "island.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
