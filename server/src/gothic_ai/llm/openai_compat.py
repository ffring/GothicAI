"""OpenAI-compatible Chat Completions backend: Ollama, mlx_lm.server, LM Studio, llama.cpp, OpenRouter."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import openai
from openai import AsyncOpenAI

from .base import Done, LLMError, LLMEvent, Message, TextDelta, Tool, ToolCall

log = logging.getLogger(__name__)


class OpenAICompatBackend:
    def __init__(self, model: str, base_url: str, api_key: str = "local", *, name: str = "compat") -> None:
        self.name = name
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    async def aclose(self) -> None:
        await self._client.close()

    async def stream(
        self, *, instructions: str, messages: list[Message], tools: list[Tool] | None = None
    ) -> AsyncIterator[LLMEvent]:
        chat = [{"role": "system", "content": instructions}]
        # chat completions has no developer role everywhere: situation notes go in as system messages
        chat += [
            {"role": "system" if m.role == "developer" else m.role, "content": m.content} for m in messages
        ]
        kwargs = {}
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in tools
            ]
        text = ""
        calls: dict[int, dict[str, str]] = {}
        try:
            stream = await self._client.chat.completions.create(
                model=self.model, messages=chat, stream=True, **kwargs
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    text += delta.content
                    yield TextDelta(delta.content)
                for tc in delta.tool_calls or []:
                    acc = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    acc["id"] = tc.id or acc["id"]
                    if tc.function is not None:
                        acc["name"] += tc.function.name or ""
                        acc["arguments"] += tc.function.arguments or ""
        except openai.APIStatusError as err:
            raise LLMError(f"{self.name}: HTTP {err.status_code}: {err.message}") from err
        except openai.APIConnectionError as err:
            raise LLMError(f"{self.name}: cannot reach {self._client.base_url}", retryable=True) from err
        for acc in calls.values():
            try:
                args = json.loads(acc["arguments"] or "{}")
            except json.JSONDecodeError:
                log.warning("tool call %s with invalid arguments: %r", acc["name"], acc["arguments"])
                continue
            yield ToolCall(
                name=acc["name"], arguments=args if isinstance(args, dict) else {}, call_id=acc["id"]
            )
        yield Done(text)
