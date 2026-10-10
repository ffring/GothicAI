"""Deterministic backend for tests and offline runs: replies with canned text, streamed in small chunks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .base import Done, LLMEvent, Message, TextDelta

DEFAULT_REPLY = "Ты вернулся. Хорошо. Времени у нас мало, так что слушай внимательно. Драконы уже здесь."


class FakeBackend:
    name = "fake"

    def __init__(self, reply: str = DEFAULT_REPLY, *, chunk: int = 7, delay: float = 0.02) -> None:
        self.reply = reply
        self.chunk = chunk
        self.delay = delay
        self.calls: list[tuple[str, list[Message]]] = []

    async def stream(self, *, instructions: str, messages: list[Message]) -> AsyncIterator[LLMEvent]:
        self.calls.append((instructions, list(messages)))
        for i in range(0, len(self.reply), self.chunk):
            await asyncio.sleep(self.delay)
            yield TextDelta(self.reply[i : i + self.chunk])
        yield Done(self.reply)
