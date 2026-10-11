"""`gothic-decider`: the decision service and a load generator for it."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

from .scheduler import Scheduler, SchedulerConfig
from .service import DeciderService

log = logging.getLogger("gothic_decider")

DEFAULT_PORT = int(os.environ.get("GOTHIC_DECIDER_PORT", "47832"))


async def serve(args: argparse.Namespace) -> int:
    from .engines import make_engine

    if args.engine == "strands":
        kwargs = {
            "checkpoint": args.checkpoint,
            "merge_lora": not args.no_merge,
            "token_budget": args.token_budget,
            "mem_cap_gb": args.mem_cap_gb,
        }
    else:
        kwargs = {"ms_per_token": args.fake_ms_per_token}
    engine = await asyncio.to_thread(make_engine, args.engine, **kwargs)
    if not args.no_warmup:
        await asyncio.to_thread(engine.warmup)
    cfg = SchedulerConfig(
        urgent_target_ms=args.urgent_target_ms, bg_target_ms=args.bg_target_ms, bg_share=args.bg_share
    )
    await DeciderService(Scheduler(engine, cfg), args.host, args.port).serve_forever()
    return 0


async def bench(args: argparse.Namespace) -> int:
    """Send requests at a fixed rate (Poisson arrivals) and report latency per lane."""
    from websockets.asyncio.client import connect

    pool = _load_requests(args.requests, args.questions)
    rnd = random.Random(1)
    sent: dict[str, tuple[float, str]] = {}
    outcomes: dict[str, list] = {"urgent": [], "background": []}
    counts: dict[str, int] = {}

    async with connect(args.url, max_size=2**22) as ws:
        hello = json.loads(await ws.recv())
        print(f"connected: {hello.get('model')}", flush=True)

        async def receive() -> None:
            async for raw in ws:
                msg = json.loads(raw)
                if msg["type"] == "stats":
                    print(json.dumps(msg, indent=1), flush=True)
                    continue
                rid = msg.get("id")
                if rid not in sent:
                    continue
                t0, lane = sent.pop(rid)
                counts[msg["type"]] = counts.get(msg["type"], 0) + 1
                if msg["type"] == "answers":
                    outcomes[lane].append((time.perf_counter() - t0) * 1000)

        reader = asyncio.create_task(receive())
        stop = time.perf_counter() + args.seconds
        n = 0
        while time.perf_counter() < stop:
            await asyncio.sleep(rnd.expovariate(args.rate))
            lane = "urgent" if rnd.random() < args.urgent_share else "background"
            item = pool[rnd.randrange(len(pool))]
            rid = f"b{n}"
            n += 1
            sent[rid] = (time.perf_counter(), lane)
            await ws.send(
                json.dumps(
                    {
                        "type": "decide",
                        "id": rid,
                        "lane": lane,
                        "priority": rnd.random(),
                        "deadline_ms": args.urgent_deadline_ms if lane == "urgent" else args.deadline_ms,
                        "state": item["state"],
                        "questions": item["questions"],
                    },
                    ensure_ascii=False,
                )
            )
        deadline = time.perf_counter() + 10
        while sent and time.perf_counter() < deadline:  # noqa: ASYNC110 - polling a dict filled by reader
            await asyncio.sleep(0.05)
        await ws.send(json.dumps({"type": "stats"}))
        await asyncio.sleep(0.3)
        reader.cancel()

    print(
        f"sent {n} in {args.seconds:.0f} s ({n / args.seconds:.1f}/s); "
        f"outcomes {counts}; no reply {len(sent)}"
    )
    for lane, lat in outcomes.items():
        if lat:
            lat.sort()
            print(
                f"  {lane:10s} n={len(lat):4d}  p50 {lat[len(lat) // 2]:7.1f} ms  "
                f"p95 {lat[int(len(lat) * 0.95)]:7.1f} ms  max {lat[-1]:7.1f} ms"
            )
    return 0


def _load_requests(path: str | None, questions: str | None) -> list[dict]:
    if path:
        items = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    else:
        items = [
            {
                "state": "You are a guard at the city gate. The hero walks past and nods at you.",
                "questions": {
                    "react": {
                        "type": "choice",
                        "instructions": "What do you do right now?",
                        "criteria": {
                            "continue": "keep standing guard",
                            "greet": "nod back",
                            "question": "stop him",
                        },
                    }
                },
            }
        ]
    if questions:
        keep = set(questions.split(","))
        items = [{**it, "questions": {k: v for k, v in it["questions"].items() if k in keep}} for it in items]
        items = [it for it in items if it["questions"]]
    return items


def main() -> None:
    p = argparse.ArgumentParser(prog="gothic-decider")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the decision service")
    s.add_argument("--host", default=os.environ.get("GOTHIC_DECIDER_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--engine", choices=("strands", "fake"), default="strands")
    s.add_argument(
        "--checkpoint",
        default=os.environ.get(
            "GOTHIC_DECIDER_CHECKPOINT", "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610"
        ),
    )
    s.add_argument("--no-merge", action="store_true", help="keep the LoRA adapter separate")
    s.add_argument("--no-warmup", action="store_true")
    s.add_argument("--token-budget", type=int, default=8192, help="padded tokens per forward pass")
    s.add_argument("--mem-cap-gb", type=float, default=9.0, help="GPU memory cap of this process")
    s.add_argument("--urgent-target-ms", type=float, default=300)
    s.add_argument("--bg-target-ms", type=float, default=150, help="initial time cap of a background pass")
    s.add_argument("--bg-share", type=float, default=0.6, help="max share of GPU time for background")
    s.add_argument("--fake-ms-per-token", type=float, default=0.0)

    b = sub.add_parser("bench", help="send requests at a fixed rate and report latency")
    b.add_argument("--url", default=f"ws://127.0.0.1:{DEFAULT_PORT}")
    b.add_argument("--requests", help="JSONL with state + questions per line (default: one sample)")
    b.add_argument("--questions", help="keep only these question names, comma-separated")
    b.add_argument("--rate", type=float, default=10, help="requests per second")
    b.add_argument("--seconds", type=float, default=30)
    b.add_argument("--urgent-share", type=float, default=0.1)
    b.add_argument("--deadline-ms", type=int, default=5000)
    b.add_argument("--urgent-deadline-ms", type=int, default=1000)

    args = p.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        sys.exit(asyncio.run(serve(args) if args.cmd == "serve" else bench(args)))
    except KeyboardInterrupt:
        sys.exit(130)
