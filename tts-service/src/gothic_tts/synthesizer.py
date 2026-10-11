"""Primary provider with an optional fallback and latency logging.

The fallback takes over only before any audio was produced: switching mid-line would change the
voice halfway through a sentence.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .providers import TtsError, TtsProvider, Voice

log = logging.getLogger(__name__)

# (pcm chunk, the provider producing it: its name and sample rate)
SpeechCallback = Callable[[bytes, TtsProvider], Awaitable[None]]


@dataclass(frozen=True)
class Speech:
    provider: str
    rate: int
    audio_s: float
    first_chunk_s: float
    latency_s: float  # until the last chunk


class Synthesizer:
    def __init__(self, primary: TtsProvider, fallback: TtsProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    @property
    def providers(self) -> list[TtsProvider]:
        return [p for p in (self.primary, self.fallback) if p is not None]

    async def warmup(self) -> None:
        """Gets the primary ready; the fallback only if the primary is not (a warm fallback may cost)."""
        for p in self.providers:
            started = time.monotonic()
            try:
                await p.warmup()
                log.info("%s ready in %.1fs", p.name, time.monotonic() - started)
                return
            except TtsError as err:
                log.warning("%s warmup failed: %s", p.name, err)

    async def speak(self, text: str, voice: Voice, on_chunk: SpeechCallback) -> Speech:
        """Raises TtsError when every provider failed (or one failed after it started speaking)."""
        started = time.monotonic()
        errors: list[str] = []
        for p in self.providers:
            first: float | None = None

            async def chunk(pcm: bytes, p: TtsProvider = p) -> None:
                nonlocal first
                if first is None:
                    first = time.monotonic() - started
                await on_chunk(pcm, p)

            try:
                audio_s = await p.synthesize(text, voice, chunk)
            except TtsError as err:
                log.warning("%s failed: %s", p.name, err)
                if first is not None:
                    raise  # the line is half spoken in this voice
                errors.append(f"{p.name}: {err}")
                continue
            took = time.monotonic() - started
            log.info(
                "%s: %.2fs of audio in %.2fs (first chunk %.2fs) [%s]: %s",
                p.name,
                audio_s,
                took,
                first or 0.0,
                voice.id,
                text[:60],
            )
            return Speech(p.name, p.rate, audio_s, first or 0.0, took)
        raise TtsError("; ".join(errors) or "no providers")

    async def aclose(self) -> None:
        for p in self.providers:
            await p.aclose()
