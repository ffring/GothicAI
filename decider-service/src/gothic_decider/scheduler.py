"""Two-lane scheduler in front of one engine (one GPU, one forward pass at a time).

- `urgent` is served first, a few requests per pass, so it waits at most for the pass already running.
- `background` is packed into passes capped by time, not by count: the cap in tokens is the target
  time times the measured speed. The target adapts (AIMD): it shrinks by 10% whenever an urgent
  request misses its latency target and grows slowly while they do not. A GPU pass cannot be
  interrupted, so this cap is what bounds the urgent lane's worst case.
- `background` gets at most `bg_share` of GPU time over a sliding window; the rest is headroom for
  urgent bursts and for other models on the same GPU.
- Within a lane: higher priority first, then the earlier deadline, then arrival order.
- Every request ends with exactly one outcome: answers, expired (deadline passed before it was
  started), cancelled, or error.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque
from collections.abc import Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .engines import Engine
from .protocol import DecideRequest, Lane

log = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    urgent_max_batch: int = 4
    urgent_target_ms: float = 300  # urgent latency (queue + inference) the background cap adapts to
    bg_target_ms: float = 150  # starting time cap of one background pass
    bg_min_ms: float = 20
    bg_max_ms: float = 400
    bg_share: float = 0.6  # max share of GPU time for background work
    window_s: float = 2.0  # window of the GPU-time budget
    ms_per_token: float = 0.087  # initial speed guess (3090, Strands 2B); then measured
    stats_window: int = 500


@dataclass
class Job:
    key: Hashable
    req: DecideRequest
    cost: int
    arrived: float
    deadline: float
    seq: int
    reply: Callable[[dict], None]
    cancelled: bool = False
    started: float | None = None

    def order(self) -> tuple:
        return (-self.req.priority, self.deadline, self.seq)


@dataclass
class _LaneStats:
    queue_ms: deque = field(default_factory=lambda: deque(maxlen=500))
    total_ms: deque = field(default_factory=lambda: deque(maxlen=500))
    answered: int = 0
    expired: int = 0
    cancelled: int = 0
    failed: int = 0


class Scheduler:
    def __init__(self, engine: Engine, config: SchedulerConfig | None = None) -> None:
        self.engine = engine
        self.cfg = config or SchedulerConfig()
        self.bg_target_ms = self.cfg.bg_target_ms
        self.ms_per_token = self.cfg.ms_per_token
        self._queues: dict[Lane, list[Job]] = {Lane.URGENT: [], Lane.BACKGROUND: []}
        self._jobs: dict[Hashable, Job] = {}  # pending and running
        self._seq = itertools.count()
        self._wake = asyncio.Event()
        self._busy: deque[tuple[float, float, Lane]] = deque()  # (start, end, lane) of recent passes
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="decider-gpu")
        self.stats = {lane: _LaneStats() for lane in Lane}
        self.passes = 0
        self.infer_ms: deque[float] = deque(maxlen=self.cfg.stats_window)
        self.batch_sizes: deque[int] = deque(maxlen=self.cfg.stats_window)

    # --- API ------------------------------------------------------------------------------

    def submit(self, key: Hashable, req: DecideRequest, reply: Callable[[dict], None]) -> None:
        """Queue a request. `reply` is called exactly once with the outcome message."""
        if key in self._jobs:
            reply({"type": "error", "id": req.id, "detail": "duplicate id"})
            return
        now = time.monotonic()
        try:
            cost = self.engine.cost(req)
        except Exception as err:  # bad question for the model (e.g. unrenderable criteria)
            reply({"type": "error", "id": req.id, "detail": f"cannot render request: {err}"})
            return
        job = Job(key, req, cost, now, now + req.deadline_ms / 1000, next(self._seq), reply)
        self._jobs[key] = job
        self._queues[req.lane].append(job)
        self._wake.set()

    def cancel(self, key: Hashable) -> bool:
        job = self._jobs.get(key)
        if job is None or job.cancelled:
            return False
        job.cancelled = True
        if job.started is None:  # still queued: answer now; a running job answers when its pass ends
            self._queues[job.req.lane].remove(job)
            self._finish(job, {"type": "cancelled", "id": job.req.id})
            self.stats[job.req.lane].cancelled += 1
        return True

    def queued(self, lane: Lane) -> int:
        return len(self._queues[lane])

    def snapshot(self) -> dict:
        def pct(values, q):
            v = sorted(values)
            return round(v[min(len(v) - 1, int(len(v) * q))], 1) if v else None

        lanes = {}
        for lane, s in self.stats.items():
            lanes[lane.value] = {
                "queued": len(self._queues[lane]),
                "answered": s.answered,
                "expired": s.expired,
                "cancelled": s.cancelled,
                "failed": s.failed,
                "queue_ms_p50": pct(s.queue_ms, 0.5),
                "queue_ms_p95": pct(s.queue_ms, 0.95),
                "total_ms_p50": pct(s.total_ms, 0.5),
                "total_ms_p95": pct(s.total_ms, 0.95),
            }
        return {
            "lanes": lanes,
            "passes": self.passes,
            "infer_ms_p50": pct(self.infer_ms, 0.5),
            "batch_p50": pct(self.batch_sizes, 0.5),
            "bg_target_ms": round(self.bg_target_ms, 1),
            "tokens_per_s": round(1000 / self.ms_per_token),
            "gpu_busy": round(self._busy_share(time.monotonic(), None), 3),
            "bg_busy": round(self._busy_share(time.monotonic(), Lane.BACKGROUND), 3),
        }

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                now = time.monotonic()
                self._expire(now)
                lane = self._pick_lane(now)
                if lane is None:
                    await self._sleep_until_work(now)
                    continue
                batch = self._take(lane)
                for job in batch:
                    job.started = now
                t0 = time.monotonic()
                try:
                    answers = await loop.run_in_executor(self._pool, self.engine.run, [j.req for j in batch])
                    error = None
                except Exception as err:
                    log.exception("engine failed on a batch of %d", len(batch))
                    answers, error = None, str(err)
                t1 = time.monotonic()
                self._account(lane, batch, t0, t1)
                for i, job in enumerate(batch):
                    self._complete(job, answers[i] if answers else None, error, t0, t1)
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)

    # --- internals ------------------------------------------------------------------------

    def _expire(self, now: float) -> None:
        for lane, queue in self._queues.items():
            keep = []
            for job in queue:
                if job.deadline <= now:
                    self._finish(
                        job,
                        {
                            "type": "expired",
                            "id": job.req.id,
                            "queue_ms": round((now - job.arrived) * 1000, 1),
                        },
                    )
                    self.stats[lane].expired += 1
                else:
                    keep.append(job)
            queue[:] = keep

    def _pick_lane(self, now: float) -> Lane | None:
        if self._queues[Lane.URGENT]:
            return Lane.URGENT
        if self._queues[Lane.BACKGROUND] and self._busy_share(now, Lane.BACKGROUND) < self.cfg.bg_share:
            return Lane.BACKGROUND
        return None

    async def _sleep_until_work(self, now: float) -> None:
        """Wait for a new request, the earliest deadline, or the budget to free up."""
        timeout = None
        pending = [j for q in self._queues.values() for j in q]
        if pending:
            timeout = max(0.0, min(j.deadline for j in pending) - now)
            if self._queues[Lane.BACKGROUND]:
                timeout = min(timeout, 0.01)  # budget-throttled: look again shortly
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout)
        except TimeoutError:
            pass

    def _take(self, lane: Lane) -> list[Job]:
        queue = self._queues[lane]
        queue.sort(key=Job.order)
        if lane is Lane.URGENT:
            batch = queue[: self.cfg.urgent_max_batch]
        else:
            cap = self.bg_target_ms / self.ms_per_token
            batch, spent = [], 0
            for job in queue:
                if batch and spent + job.cost > cap:
                    break
                batch.append(job)
                spent += job.cost
        del queue[: len(batch)]
        return batch

    def _account(self, lane: Lane, batch: list[Job], t0: float, t1: float) -> None:
        ms = (t1 - t0) * 1000
        self.passes += 1
        self.infer_ms.append(ms)
        self.batch_sizes.append(len(batch))
        self._busy.append((t0, t1, lane))
        tokens = sum(j.cost for j in batch)
        if tokens >= 200:  # tiny passes are dominated by fixed overhead; they would skew the speed
            self.ms_per_token = 0.8 * self.ms_per_token + 0.2 * ms / tokens

    def _busy_share(self, now: float, lane: Lane | None) -> float:
        start = now - self.cfg.window_s
        while self._busy and self._busy[0][1] < start:
            self._busy.popleft()
        busy = sum(min(e, now) - max(s, start) for s, e, ln in self._busy if lane is None or ln is lane)
        return busy / self.cfg.window_s

    def _complete(self, job: Job, answers: dict | None, error: str | None, t0: float, t1: float) -> None:
        lane = job.req.lane
        stats = self.stats[lane]
        if job.cancelled:
            self._finish(job, {"type": "cancelled", "id": job.req.id})
            stats.cancelled += 1
            return
        if error is not None:
            self._finish(job, {"type": "error", "id": job.req.id, "detail": error})
            stats.failed += 1
            return
        queue_ms = (t0 - job.arrived) * 1000
        total_ms = (t1 - job.arrived) * 1000
        stats.answered += 1
        stats.queue_ms.append(queue_ms)
        stats.total_ms.append(total_ms)
        if lane is Lane.URGENT:
            self._adapt(total_ms)
        self._finish(
            job,
            {
                "type": "answers",
                "id": job.req.id,
                "answers": answers,
                "queue_ms": round(queue_ms, 1),
                "infer_ms": round((t1 - t0) * 1000, 1),
            },
        )

    def _adapt(self, urgent_ms: float) -> None:
        c = self.cfg
        if urgent_ms > c.urgent_target_ms:
            self.bg_target_ms = max(c.bg_min_ms, self.bg_target_ms * 0.9)
        else:
            self.bg_target_ms = min(c.bg_max_ms, self.bg_target_ms + 1)

    def _finish(self, job: Job, msg: dict) -> None:
        self._jobs.pop(job.key, None)
        try:
            job.reply(msg)
        except Exception:
            log.exception("reply callback failed")
