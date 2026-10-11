"""Audio helpers: providers produce 16-bit little-endian mono PCM; WAV only at the edges."""

from __future__ import annotations

import io
import wave


def to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def seconds(pcm: bytes, rate: int) -> float:
    return len(pcm) / 2 / rate
