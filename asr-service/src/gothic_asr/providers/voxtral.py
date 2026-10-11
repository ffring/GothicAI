"""Voxtral Mini 4B Realtime, self-hosted behind vLLM's realtime WebSocket API (on the GPU server,
never on this machine).

vLLM protocol: `session.update{model}` once per connection; per utterance `input_audio_buffer.commit`
starts a generation, `input_audio_buffer.append` (base64 PCM16, 16 kHz) feeds audio while the model
transcribes it with a fixed delay (480 ms by default), `input_audio_buffer.commit{final:true}` ends
the audio; the server answers with `transcription.delta` events and one `transcription.done`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

import numpy as np
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from ..audio import float_to_pcm16
from .base import AsrError, PartialCallback, Transcript

log = logging.getLogger(__name__)

DEFAULT_URL = "ws://127.0.0.1:47830/v1/realtime"  # GOTHIC_VOXTRAL_URL: the GPU server running it
DEFAULT_MODEL = "mistralai/Voxtral-Mini-4B-Realtime-2602"
LANGUAGES = frozenset("en fr es de ru zh ja it pt nl ar hi ko".split())
BATCH_CHUNK = 16000  # samples per append when a whole recording is sent at once


class VoxtralRealtime:
    name = "voxtral"
    streaming = True
    languages = LANGUAGES

    def __init__(
        self, url: str = "", model: str = DEFAULT_MODEL, timeout: str | float = 10.0
    ) -> None:
        self.url = url or os.environ.get("GOTHIC_VOXTRAL_URL", DEFAULT_URL)
        self.model = model
        self.timeout = float(timeout)
        self._ws: ClientConnection | None = None
        self._lock = asyncio.Lock()  # one utterance per connection at a time

    async def warmup(self) -> None:
        async with self._lock:
            await self._connection()

    async def open_stream(self, lang: str, on_partial: PartialCallback | None = None) -> _Stream:
        await self._lock.acquire()
        try:
            ws = await self._connection()
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))  # start generating
        except (WebSocketException, OSError) as err:
            await self._drop()
            self._lock.release()
            raise AsrError(f"voxtral {self.url}: {type(err).__name__}: {err}") from err
        except BaseException:
            self._lock.release()
            raise
        return _Stream(self, ws, on_partial)

    async def transcribe(self, audio: np.ndarray, lang: str) -> Transcript:
        stream = await self.open_stream(lang)
        try:
            for i in range(0, len(audio), BATCH_CHUNK):
                await stream.feed(audio[i : i + BATCH_CHUNK])
        except BaseException:
            await stream.cancel()
            raise
        return await stream.finish()

    async def _connection(self) -> ClientConnection:
        if self._ws is not None:
            return self._ws
        try:
            ws = await connect(self.url, open_timeout=self.timeout, max_size=2**22)
            created = json.loads(await asyncio.wait_for(ws.recv(), self.timeout))
            if created.get("type") != "session.created":
                raise AsrError(f"voxtral: unexpected first event {created.get('type')!r}")
            await ws.send(json.dumps({"type": "session.update", "model": self.model}))
        except (WebSocketException, OSError, TimeoutError) as err:
            raise AsrError(f"voxtral {self.url}: {type(err).__name__}: {err}") from err
        self._ws = ws
        log.info("voxtral session ready (%s)", self.url)
        return ws

    async def _drop(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            await ws.close()

    async def aclose(self) -> None:
        await self._drop()


class _Stream:
    def __init__(
        self, owner: VoxtralRealtime, ws: ClientConnection, on_partial: PartialCallback | None
    ) -> None:
        self._owner = owner
        self._ws = ws
        self._on_partial = on_partial
        self._text = ""
        self._done: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._reader = asyncio.create_task(self._read())
        self._closed = False

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                ev = json.loads(raw)
                match ev.get("type"):
                    case "transcription.delta":
                        self._text += ev.get("delta", "")
                        if self._on_partial is not None and self._text.strip():
                            self._on_partial(self._text.strip())
                    case "transcription.done":
                        _resolve(self._done, str(ev.get("text", self._text)))
                        return
                    case "error":
                        _fail(self._done, AsrError(f"voxtral: {ev.get('error')} ({ev.get('code')})"))
                        return
        except (WebSocketException, OSError) as err:
            _fail(self._done, AsrError(f"voxtral connection lost: {err}"))
            return
        _fail(self._done, AsrError("voxtral connection closed"))

    async def feed(self, audio: np.ndarray) -> None:
        if self._done.done():
            self._done.result()  # raises the reader's error
        try:
            payload = base64.b64encode(float_to_pcm16(audio)).decode()
            await self._ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": payload}))
        except (WebSocketException, OSError) as err:
            raise AsrError(f"voxtral connection lost: {err}") from err

    async def finish(self) -> Transcript:
        try:
            await self._ws.send(json.dumps({"type": "input_audio_buffer.commit", "final": True}))
            text = await asyncio.wait_for(asyncio.shield(self._done), self._owner.timeout)
        except (WebSocketException, OSError, TimeoutError) as err:
            await self._close(broken=True)
            raise AsrError(f"voxtral: no transcription ({type(err).__name__})") from err
        except AsrError:
            await self._close(broken=True)
            raise
        await self._close(broken=False)
        return Transcript(text.strip(), self._owner.name)

    async def cancel(self) -> None:
        # dropping the connection aborts the generation on the server; the next stream reconnects
        await self._close(broken=True)

    async def _close(self, *, broken: bool) -> None:
        if self._closed:
            return
        self._closed = True
        if broken:
            self._reader.cancel()
            await self._owner._drop()
        await asyncio.gather(self._reader, return_exceptions=True)
        self._owner._lock.release()


def _resolve(fut: asyncio.Future[str], value: str) -> None:
    if not fut.done():
        fut.set_result(value)


def _fail(fut: asyncio.Future[str], err: Exception) -> None:
    if not fut.done():
        fut.set_exception(err)
        fut.exception()  # mark retrieved: the stream may be cancelled without reading it
