"""Test doubles shared by several test modules."""

from __future__ import annotations

import asyncio

from gothic_ai.brain import Outcome


class FakeDecider:
    def __init__(
        self,
        choice: str = "greet",
        confidence: float = 0.9,
        kind: str = "answers",
        per_question: dict[str, str] | None = None,
    ) -> None:
        self.choice, self.confidence, self.kind = choice, confidence, kind
        self.per_question = per_question or {}  # question name -> its choice (else `choice`)
        self.calls: list[dict] = []
        self.cancelled: list[str] = []
        self.gate: asyncio.Event | None = None

    async def decide(self, rid, lane, priority, deadline_ms, state, questions) -> Outcome:
        self.calls.append(
            {
                "rid": rid,
                "lane": lane,
                "priority": priority,
                "deadline_ms": deadline_ms,
                "state": state,
                "questions": questions,
            }
        )
        if self.gate is not None:
            await self.gate.wait()
        if self.kind != "answers":
            return Outcome(self.kind)
        answers = {}
        for name, q in questions.items():
            if q["type"] == "choice":
                want = self.per_question.get(name, self.choice)
                pick = want if want in q["criteria"] else next(iter(q["criteria"]))
                answers[name] = {
                    "type": "choice",
                    "choice": pick,
                    "probabilities": {pick: self.confidence},
                    "confidence": self.confidence,
                }
            elif q["type"] == "score":
                answers[name] = {
                    "type": "score",
                    "score": 3.0,
                    "probabilities": {"3": 1.0},
                    "confidence": 0.8,
                }
        return Outcome("answers", answers, 1.0, 30.0)

    async def cancel(self, rid: str) -> None:
        self.cancelled.append(rid)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t
