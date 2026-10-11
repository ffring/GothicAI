import asyncio
import json
import time

import pytest
from websockets.asyncio.client import connect

from gothic_decider.engines import FakeEngine, make_engine
from gothic_decider.protocol import DecideRequest, Lane, ProtocolError, parse_decide
from gothic_decider.scheduler import Scheduler, SchedulerConfig
from gothic_decider.service import DeciderService

REACT = {
    "react": {
        "type": "choice",
        "instructions": "What do you do?",
        "criteria": {"continue": "keep working", "greet": "greet him", "flee": "run"},
    }
}


def req(
    rid: str,
    lane: Lane = Lane.BACKGROUND,
    priority: float = 0,
    deadline_ms: int = 5000,
    state: str = "You are a smith. The hero walks past.",
) -> DecideRequest:
    return DecideRequest(rid, lane, priority, deadline_ms, state, REACT)


class Collector:
    def __init__(self) -> None:
        self.msgs: dict[str, dict] = {}
        self.order: list[str] = []
        self.event = asyncio.Event()

    def __call__(self, key):
        def reply(msg: dict) -> None:
            assert key not in self.msgs, f"second outcome for {key}"
            self.msgs[key] = msg
            self.order.append(key)
            self.event.set()

        return reply

    async def wait(self, *keys: str) -> None:
        async with asyncio.timeout(3):
            while not all(k in self.msgs for k in keys):
                self.event.clear()
                await self.event.wait()


async def started(sched: Scheduler) -> asyncio.Task:
    task = asyncio.create_task(sched.run())
    await asyncio.sleep(0)
    return task


def test_parse_decide() -> None:
    r = parse_decide(
        {
            "type": "decide",
            "id": "a",
            "lane": "urgent",
            "priority": 2,
            "deadline_ms": 300,
            "state": "s",
            "questions": REACT,
        }
    )
    assert (r.lane, r.priority, r.deadline_ms) == (Lane.URGENT, 2.0, 300)
    assert parse_decide({"id": "b", "state": {"npc": "x"}, "questions": REACT}).lane is Lane.BACKGROUND

    def one(q: dict) -> dict:
        return {"id": "a", "state": "s", "questions": {"q": q}}

    bad = [
        {"state": "s", "questions": REACT},
        {"id": "a", "lane": "now", "state": "s", "questions": REACT},
        {"id": "a", "state": " ", "questions": REACT},
        {"id": "a", "state": "s", "questions": {}},
        {"id": "a", "state": "s", "deadline_ms": 0, "questions": REACT},
        one({"type": "choice", "instructions": "x", "criteria": {"a": "1"}}),
        one({"type": "score", "instructions": "x", "criteria": ["a"]}),
        one({"type": "noul", "instructions": "x", "criteria": {"yes": "y"}}),
        one({"type": "rank", "instructions": "x"}),
    ]
    for msg in bad:
        with pytest.raises(ProtocolError):
            parse_decide(msg)


async def test_answers_once_each() -> None:
    sched = Scheduler(FakeEngine())
    got = Collector()
    task = await started(sched)
    for i in range(10):
        sched.submit(f"k{i}", req(f"r{i}"), got(f"k{i}"))
    await got.wait(*(f"k{i}" for i in range(10)))
    task.cancel()
    assert {m["type"] for m in got.msgs.values()} == {"answers"}
    assert got.msgs["k3"]["answers"]["react"]["choice"] == "continue"
    assert sched.snapshot()["lanes"]["background"]["answered"] == 10


async def test_urgent_overtakes_background() -> None:
    engine = FakeEngine(overhead_ms=60)
    sched = Scheduler(engine, SchedulerConfig(bg_target_ms=1, bg_share=1.0))  # one background job per pass
    got = Collector()
    task = await started(sched)
    for i in range(4):
        sched.submit(f"b{i}", req(f"b{i}"), got(f"b{i}"))
    await asyncio.sleep(0.02)  # the first background pass is running now
    sched.submit("u", req("u", Lane.URGENT), got("u"))
    await got.wait("u", "b3")
    task.cancel()
    assert engine.batches[:3] == [["b0"], ["u"], ["b1"]]


async def test_priority_then_deadline_within_lane() -> None:
    engine = FakeEngine(overhead_ms=30)
    sched = Scheduler(engine, SchedulerConfig(bg_target_ms=1, bg_share=1.0))
    got = Collector()
    task = await started(sched)
    sched.submit("first", req("first"), got("first"))
    await asyncio.sleep(0.01)
    sched.submit("low", req("low", priority=0, deadline_ms=900), got("low"))
    sched.submit("high", req("high", priority=5), got("high"))
    sched.submit("soon", req("soon", priority=0, deadline_ms=500), got("soon"))
    await got.wait("low", "high", "soon")
    task.cancel()
    assert [b[0] for b in engine.batches] == ["first", "high", "soon", "low"]


async def test_expired_before_start() -> None:
    sched = Scheduler(FakeEngine(overhead_ms=100), SchedulerConfig(bg_share=1.0))
    got = Collector()
    task = await started(sched)
    sched.submit("slow", req("slow"), got("slow"))
    await asyncio.sleep(0.01)
    sched.submit("late", req("late", Lane.URGENT, deadline_ms=30), got("late"))
    await got.wait("slow", "late")
    task.cancel()
    assert got.msgs["late"]["type"] == "expired"
    assert got.msgs["slow"]["type"] == "answers"
    assert sched.snapshot()["lanes"]["urgent"]["expired"] == 1


async def test_cancel_queued_and_running() -> None:
    engine = FakeEngine(overhead_ms=80)
    sched = Scheduler(engine, SchedulerConfig(bg_target_ms=1, bg_share=1.0))
    got = Collector()
    task = await started(sched)
    sched.submit("run", req("run"), got("run"))
    sched.submit("wait", req("wait"), got("wait"))
    await asyncio.sleep(0.02)
    assert sched.cancel("wait") and sched.cancel("run")
    assert not sched.cancel("run")  # already cancelled
    assert got.msgs["wait"]["type"] == "cancelled"  # queued: answered right away
    await got.wait("run")
    task.cancel()
    assert got.msgs["run"]["type"] == "cancelled"  # running: answered when its pass ends
    assert engine.batches == [["run"]]


async def test_engine_error_reaches_every_job() -> None:
    sched = Scheduler(FakeEngine(fail=True))
    got = Collector()
    task = await started(sched)
    sched.submit("a", req("a"), got("a"))
    sched.submit("b", req("b"), got("b"))
    await got.wait("a", "b")
    task.cancel()
    assert {m["type"] for m in got.msgs.values()} == {"error"}


def test_background_pass_is_capped_by_time() -> None:
    engine = FakeEngine()
    sched = Scheduler(engine, SchedulerConfig(bg_target_ms=10, ms_per_token=0.1))  # 100 tokens per pass
    one = engine.cost(req("x"))
    assert 20 < one < 100
    for i in range(10):
        sched.submit(f"k{i}", req(f"k{i}"), lambda m: None)
    batch = sched._take(Lane.BACKGROUND)
    assert len(batch) == 100 // one
    big = req("big", state="word " * 400)
    sched._queues[Lane.BACKGROUND].clear()
    sched.submit("big", big, lambda m: None)
    assert [j.req.id for j in sched._take(Lane.BACKGROUND)] == ["big"]  # oversized still goes, alone


def test_background_cap_adapts_to_urgent_latency() -> None:
    sched = Scheduler(FakeEngine(), SchedulerConfig(bg_target_ms=150, urgent_target_ms=300))
    sched._adapt(500)
    assert sched.bg_target_ms == pytest.approx(135)
    for _ in range(100):
        sched._adapt(400)
    assert sched.bg_target_ms == sched.cfg.bg_min_ms
    sched._adapt(100)
    assert sched.bg_target_ms == sched.cfg.bg_min_ms + 1


async def test_background_budget() -> None:
    engine = FakeEngine(overhead_ms=20)
    sched = Scheduler(engine, SchedulerConfig(bg_target_ms=1, bg_share=0.3, window_s=0.5))
    task = await started(sched)
    for i in range(200):
        sched.submit(f"k{i}", req(f"k{i}"), lambda m: None)
    await asyncio.sleep(1.5)
    share = sched._busy_share(time.monotonic(), Lane.BACKGROUND)
    task.cancel()
    assert 0.15 < share <= 0.4
    # urgent work ignores the budget
    assert sched._pick_lane(time.monotonic()) in (None, Lane.BACKGROUND)


async def test_service_round_trip() -> None:
    engine = make_engine("fake", overhead_ms=50)
    service = DeciderService(Scheduler(engine, SchedulerConfig(bg_target_ms=1)), port=0)
    up = asyncio.Event()
    server = asyncio.create_task(service.serve_forever(up))
    await up.wait()
    url = f"ws://127.0.0.1:{service.port}"
    async with connect(url) as ws:
        hello = json.loads(await ws.recv())
        assert hello["service"] == "gothic-decider" and hello["model"] == "fake"
        await ws.send("not json")
        assert json.loads(await ws.recv())["detail"] == "malformed message"
        await ws.send(json.dumps({"type": "decide", "id": "x", "state": "s", "questions": {}}))
        bad = json.loads(await ws.recv())
        assert (bad["type"], bad["id"]) == ("error", "x")
        for rid in ("a", "b"):
            await ws.send(
                json.dumps(
                    {
                        "type": "decide",
                        "id": rid,
                        "lane": "urgent",
                        "deadline_ms": 2000,
                        "state": "You are a guard.",
                        "questions": REACT,
                    }
                )
            )
        await ws.send(json.dumps({"type": "cancel", "id": "b"}))
        msgs = {}
        while len(msgs) < 2:
            m = json.loads(await ws.recv())
            msgs[m["id"]] = m
        assert msgs["a"]["type"] == "answers" and msgs["a"]["answers"]["react"]["choice"] == "continue"
        assert msgs["b"]["type"] == "cancelled"
        await ws.send(json.dumps({"type": "stats"}))
        stats = json.loads(await ws.recv())
        assert stats["lanes"]["urgent"]["answered"] == 1
        # requests of a closed connection are cancelled
        await ws.send(json.dumps({"type": "decide", "id": "c", "state": "s", "questions": REACT}))
        await ws.send(json.dumps({"type": "decide", "id": "d", "state": "s", "questions": REACT}))
    await asyncio.sleep(0.2)
    lanes = service.scheduler.snapshot()["lanes"]["background"]
    assert lanes["cancelled"] >= 1 and lanes["queued"] == 0
    server.cancel()
    async with asyncio.timeout(3):
        await asyncio.gather(server, return_exceptions=True)
