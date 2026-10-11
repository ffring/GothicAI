"""Provider-agnostic streaming interface. The orchestrator only talks to `LLMBackend`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["user", "assistant", "developer"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class Tool:
    """A function the model may call. `parameters` is a JSON schema object (strict: all fields required)."""

    name: str
    description: str
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    )


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = ""


@dataclass(frozen=True)
class Done:
    text: str


LLMEvent = TextDelta | ToolCall | Done


class LLMError(RuntimeError):
    """A backend failed. `retryable` is False for errors like quota exhaustion or bad credentials."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMBackend(Protocol):
    name: str

    async def aclose(self) -> None:
        """Release connections (call once at shutdown)."""
        ...

    def stream(
        self, *, instructions: str, messages: list[Message], tools: list[Tool] | None = None
    ) -> AsyncIterator[LLMEvent]:
        """Yield `TextDelta`s and `ToolCall`s as they arrive, then exactly one `Done` with the full text."""
        ...
