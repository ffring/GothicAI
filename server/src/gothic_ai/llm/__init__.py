"""LLM adapter: one streaming interface, swappable backends (plan subscription, API key, local, fake)."""

from __future__ import annotations

import os

from .base import Done, LLMBackend, LLMError, LLMEvent, Message, Role, TextDelta, Tool, ToolCall
from .fake import FakeBackend
from .openai_compat import OpenAICompatBackend
from .openai_responses import OpenAIResponsesBackend
from .siwc import ChatGPTAuth

BACKENDS = ("chatgpt", "openai", "local", "fake")
DEFAULT_MODELS = {"chatgpt": "gpt-6.1-sol", "openai": "gpt-6.1-sol", "local": "qwen3:8b", "fake": "fake"}

__all__ = [
    "BACKENDS",
    "Done",
    "LLMBackend",
    "LLMError",
    "LLMEvent",
    "Message",
    "Role",
    "TextDelta",
    "Tool",
    "ToolCall",
    "make_backend",
]


def make_backend(
    kind: str,
    model: str | None = None,
    *,
    base_url: str | None = None,
    reasoning_effort: str | None = "low",
) -> LLMBackend:
    model = model or DEFAULT_MODELS[kind]
    match kind:
        case "chatgpt":
            return OpenAIResponsesBackend(
                model, ChatGPTAuth(), reasoning_effort=reasoning_effort, name="chatgpt"
            )
        case "openai":
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise LLMError("OPENAI_API_KEY is not set")

            async def api_key() -> str:
                return key

            return OpenAIResponsesBackend(
                model,
                api_key,
                reasoning_effort=reasoning_effort,
                base_url=base_url or "https://api.openai.com/v1",
            )
        case "local":
            return OpenAICompatBackend(model, base_url or "http://127.0.0.1:11434/v1", name="local")
        case "fake":
            return FakeBackend()
    raise LLMError(f"unknown LLM backend: {kind} (expected one of {', '.join(BACKENDS)})")
