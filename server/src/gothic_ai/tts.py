"""Client of the TTS service (tts-service/): an npc line in, its audio as a WAV file out."""

from __future__ import annotations

import asyncio
import io
import itertools
import json
import logging
import time
import wave
from dataclasses import dataclass

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

log = logging.getLogger(__name__)

DEFAULT_TTS_URL = "ws://127.0.0.1:47820"


class TtsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Speech:
    wav: bytes
    audio_s: float
    provider: str
    latency_s: float


class TtsClient:
    """One connection, one line at a time, reopened when it drops."""

    def __init__(self, url: str = DEFAULT_TTS_URL, timeout: float = 10.0) -> None:
        self.url = url
        self.timeout = timeout  # per message: the first chunk, and every next one
        self._ws: ClientConnection | None = None
        self._lock = asyncio.Lock()
        self._ids = itertools.count(1)

    async def speak(self, text: str, voice_id: str, description: str) -> Speech:
        async with self._lock:
            started = time.monotonic()
            try:
                if self._ws is None:
                    self._ws = await connect(self.url, open_timeout=3, max_size=2**22)
                ws = self._ws
                req = str(next(self._ids))
                await ws.send(
                    json.dumps(
                        {
                            "type": "speak",
                            "id": req,
                            "text": text,
                            "voice": {"id": voice_id, "description": description},
                        },
                        ensure_ascii=False,
                    )
                )
                rate, provider, pcm = 0, "", bytearray()
                while True:
                    msg = await asyncio.wait_for(ws.recv(), self.timeout)
                    if isinstance(msg, bytes):
                        pcm += msg
                        continue
                    ev = json.loads(msg)
                    if ev.get("id") != req:
                        continue
                    match ev.get("type"):
                        case "start":
                            rate, provider = int(ev["sample_rate"]), str(ev.get("provider", ""))
                        case "done":
                            return Speech(
                                _wav(bytes(pcm), rate),
                                len(pcm) / 2 / rate,
                                provider,
                                time.monotonic() - started,
                            )
                        case "error":
                            raise TtsError(str(ev.get("detail")))
            except (OSError, WebSocketException, TimeoutError) as err:
                await self._drop()
                raise TtsError(f"TTS service {self.url}: {type(err).__name__}: {err}") from err
            except BaseException:
                await self._drop()  # cancelled mid-line: its remaining audio must not reach the next one
                raise

    async def _drop(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            await ws.close()

    async def aclose(self) -> None:
        await self._drop()


def _wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()
