"""OpenAI-compatible Chat Completions backend: Ollama, mlx_lm.server, LM Studio, llama.cpp, OpenRouter."""

from __future__ import annotations

from collections.abc import AsyncIterator

import openai
from openai import AsyncOpenAI

from .base import Done, LLMError, LLMEvent, Message, TextDelta


class OpenAICompatBackend:
    def __init__(self, model: str, base_url: str, api_key: str = "local", *, name: str = "compat") -> None:
        self.name = name
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0)

    async def stream(self, *, instructions: str, messages: list[Message]) -> AsyncIterator[LLMEvent]:
        chat = [{"role": "system", "content": instructions}]
        chat += [{"role": m.role, "content": m.content} for m in messages]
        text = ""
        try:
            stream = await self._client.chat.completions.create(model=self.model, messages=chat, stream=True)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    text += delta
                    yield TextDelta(delta)
        except openai.APIStatusError as err:
            raise LLMError(f"{self.name}: HTTP {err.status_code}: {err.message}") from err
        except openai.APIConnectionError as err:
            raise LLMError(f"{self.name}: cannot reach {self._client.base_url}", retryable=True) from err
        yield Done(text)
