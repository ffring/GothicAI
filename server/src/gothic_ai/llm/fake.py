"""Deterministic backend for tests and offline runs: replies with canned text, streamed in small chunks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from .base import Done, LLMEvent, Message, TextDelta, Tool, ToolCall

DEFAULT_REPLY = "Ты вернулся. Хорошо. Времени у нас мало, так что слушай внимательно. Драконы уже здесь."


class FakeBackend:
    name = "fake"

    def __init__(
        self,
        reply: str = DEFAULT_REPLY,
        *,
        chunk: int = 7,
        delay: float = 0.02,
        tool_calls: list[ToolCall] | None = None,
    ) -> None:
        self.reply = reply
        self.chunk = chunk
        self.delay = delay
        self.tool_calls = tool_calls or []
        self.calls: list[tuple[str, list[Message], list[Tool]]] = []

    async def aclose(self) -> None:
        pass

    async def stream(
        self, *, instructions: str, messages: list[Message], tools: list[Tool] | None = None
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append((instructions, list(messages), list(tools or [])))
        for i in range(0, len(self.reply), self.chunk):
            await asyncio.sleep(self.delay)
            yield TextDelta(self.reply[i : i + self.chunk])
        offered = {t.name for t in tools or []}
        for call in self.tool_calls:
            if call.name in offered:
                yield call
        yield Done(self.reply)
