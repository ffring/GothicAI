"""A deterministic engine for tests and local runs without a GPU.

It always picks the first option, and it sleeps in proportion to the batch cost, like the real model,
whose time is linear in tokens.
"""

from __future__ import annotations

import json
import threading
import time

from ..protocol import DecideRequest
from .base import EngineError


class FakeEngine:
    name = "fake"

    def __init__(self, ms_per_token: float = 0.0, overhead_ms: float = 0.0, fail: bool = False) -> None:
        self.ms_per_token = ms_per_token
        self.overhead_ms = overhead_ms
        self.fail = fail
        self.batches: list[list[str]] = []  # request ids of every batch run, for tests
        self._lock = threading.Lock()

    def cost(self, req: DecideRequest) -> int:
        state = req.state if isinstance(req.state, str) else json.dumps(req.state)
        return sum(len(state) // 4 + len(json.dumps(q)) // 4 for q in req.questions.values())

    def run(self, reqs: list[DecideRequest]) -> list[dict[str, dict]]:
        with self._lock:
            self.batches.append([r.id for r in reqs])
        time.sleep((self.overhead_ms + self.ms_per_token * sum(map(self.cost, reqs))) / 1000)
        if self.fail:
            raise EngineError("fake engine failure")
        return [{name: _answer(q) for name, q in r.questions.items()} for r in reqs]

    def warmup(self) -> None:
        pass


def _answer(q: dict) -> dict:
    match q["type"]:
        case "choice":
            keys = list(q["criteria"])
            probs = {k: float(i == 0) for i, k in enumerate(keys)}
            return {"type": "choice", "choice": keys[0], "probabilities": probs, "confidence": 1.0}
        case "noul":
            return {"type": "noul", "noul": 1.0}
        case _:
            n = len(q["criteria"])
            return {
                "type": "score",
                "score": 0.0,
                "probabilities": {str(i): float(i == 0) for i in range(n)},
                "confidence": 1.0,
            }
