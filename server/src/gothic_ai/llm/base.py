"""Provider-agnostic streaming interface. The orchestrator only talks to `LLMBackend`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class Done:
    text: str


LLMEvent = TextDelta | Done


class LLMError(RuntimeError):
    """A backend failed. `retryable` is False for errors like quota exhaustion or bad credentials."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMBackend(Protocol):
    name: str

    def stream(self, *, instructions: str, messages: list[Message]) -> AsyncIterator[LLMEvent]:
        """Yield `TextDelta`s as text arrives, then exactly one `Done` with the full text."""
        ...
