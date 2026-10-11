"""Provider interface: text and a voice in, 16-bit mono PCM out (streamed in chunks)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

ChunkCallback = Callable[[bytes], Awaitable[None]]


@dataclass(frozen=True)
class Voice:
    """Who speaks. Providers use what they understand: VoxCPM designs a voice from `description`
    (and keeps it per `id`), preset-voice APIs take `preset`."""

    id: str
    description: str = ""
    preset: str | None = None


class TtsError(RuntimeError):
    """A provider failed (unreachable, quota, bad credentials, bad text)."""


class TtsProvider(Protocol):
    name: str
    rate: int  # sample rate of the PCM it produces

    async def warmup(self) -> None: ...

    async def synthesize(self, text: str, voice: Voice, on_chunk: ChunkCallback) -> float:
        """Speak `text`, passing PCM chunks as they are generated. Returns the audio length in seconds."""
        ...

    async def aclose(self) -> None: ...
