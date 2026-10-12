"""LLM adapter: one streaming interface, swappable backends (plan subscription, API key, local, fake)."""

from __future__ import annotations

import os

from .base import (
    Done,
    Item,
    LLMBackend,
    LLMError,
    LLMEvent,
    Message,
    Reasoning,
    Role,
    TextDelta,
    Tool,
    ToolCall,
    ToolChoice,
    ToolResult,
    Usage,
    with_deadlines,
)
from .fake import FakeBackend, FakeStep
from .openai_compat import OpenAICompatBackend
from .openai_responses import OpenAIResponsesBackend
from .siwc import ChatGPTAuth

BACKENDS = ("chatgpt", "openai", "local", "fake")
DEFAULT_MODELS = {"chatgpt": "gpt-6.1-sol", "openai": "gpt-6.1-sol", "local": "qwen3:8b", "fake": "fake"}

__all__ = [
    "BACKENDS",
    "Done",
    "FakeBackend",
    "FakeStep",
    "Item",
    "LLMBackend",
    "LLMError",
    "LLMEvent",
    "Message",
    "Reasoning",
    "Role",
    "TextDelta",
    "Tool",
    "ToolCall",
    "ToolChoice",
    "ToolResult",
    "Usage",
    "make_backend",
    "with_deadlines",
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
            # a slow model (to see the npc's first sounds): GOTHIC_AI_FAKE_FIRST seconds before it writes,
            # GOTHIC_AI_FAKE_DELAY seconds a chunk of the canned reply; GOTHIC_AI_FAKE_REPLIES: replies
            # in turn instead, separated by " | " (a scripted scene: answers that fit its phrases)
            replies = [
                r.strip() for r in os.environ.get("GOTHIC_AI_FAKE_REPLIES", "").split(" | ") if r.strip()
            ]
            return FakeBackend(
                delay=float(os.environ.get("GOTHIC_AI_FAKE_DELAY", "0.02")),
                first=float(os.environ.get("GOTHIC_AI_FAKE_FIRST", "0")),
                script=[FakeStep(r) for r in replies] or None,
            )
    raise LLMError(f"unknown LLM backend: {kind} (expected one of {', '.join(BACKENDS)})")
