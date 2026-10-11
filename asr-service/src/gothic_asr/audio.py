"""Audio helpers. On the wire audio is 16-bit little-endian mono PCM; providers get float32 at 16 kHz."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np

RATE = 16000


def pcm16_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0


def float_to_pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def resample(x: np.ndarray, src: int, dst: int = RATE) -> np.ndarray:
    """Good enough for speech: box low-pass when downsampling, then linear interpolation."""
    if src == dst or len(x) == 0:
        return x.astype(np.float32, copy=False)
    if src > dst:
        k = max(1, round(src / dst))
        if k > 1:
            x = np.convolve(x, np.ones(k, dtype=np.float32) / k, mode="same")
    n = round(len(x) * dst / src)
    t = np.linspace(0, len(x) - 1, n, dtype=np.float64)
    return np.interp(t, np.arange(len(x)), x).astype(np.float32)


def rms_dbfs(x: np.ndarray) -> float:
    if len(x) == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return 20.0 * np.log10(max(rms, 1e-6))


def to_wav(x: np.ndarray, rate: int = RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(float_to_pcm16(x))
    return buf.getvalue()


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Mono float32 samples and the sample rate of a 16-bit PCM wav file."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: only 16-bit PCM wav is supported")
        rate, channels = w.getframerate(), w.getnchannels()
        x = pcm16_to_float(w.readframes(w.getnframes()))
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x, rate
