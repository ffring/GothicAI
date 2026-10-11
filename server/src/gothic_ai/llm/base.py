"""Provider-agnostic streaming interface. The orchestrator only talks to `LLMBackend`.

A request is a list of items: role messages, plus, within one turn, what the model returned on the
previous step (`Done.items`: its reasoning, text and tool calls) followed by the results of those
calls (`ToolResult`). That lets the model continue after a tool without repeating itself.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["user", "assistant", "developer"]
ToolChoice = Literal["auto", "none"]


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
    raw: str = ""  # the arguments exactly as the model wrote them; sent back as is


@dataclass(frozen=True)
class ToolResult:
    """What came of a tool call, for the next step of the same turn."""

    call_id: str
    output: str


@dataclass(frozen=True)
class Reasoning:
    """The model's reasoning on a step, opaque and provider-specific; sent back unchanged."""

    data: dict[str, Any]


Item = Message | ToolCall | ToolResult | Reasoning


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_tokens: int = 0  # part of input_tokens served from the provider's prompt cache
    output_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class Done:
    text: str
    items: tuple[Item, ...] = ()  # the response as request items, in order: reasoning, text, calls
    usage: Usage | None = None


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
        self,
        *,
        instructions: str,
        messages: Sequence[Item],
        tools: list[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """Yield `TextDelta`s and `ToolCall`s as they arrive, then exactly one `Done` with the full text.
        `tool_choice="none"` keeps the tools in the request (and the prompt cache) but forbids calls."""
        ...
