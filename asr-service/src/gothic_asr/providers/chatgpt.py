"""Transcription with a ChatGPT plan: a Realtime API transcription session authorized by Sign in with
ChatGPT (the token store `gothic-ai login chatgpt` writes, shared with the AI server).

One session is kept open and reused; it is reopened when it drops.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path

import numpy as np
from chatgpt_auth import AuthError, ChatGPTAuth, TokenStore
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import InvalidStatus, WebSocketException

from ..audio import RATE, float_to_pcm16, resample
from .base import AsrError, Transcript

log = logging.getLogger(__name__)

REALTIME_RATE = 24000
CHUNK_BYTES = REALTIME_RATE  # 0.5 s of 16-bit audio per append


class ChatGPTRealtime:
    name = "chatgpt"
    streaming = False
    languages = None

    def __init__(
        self,
        model: str = "gpt-realtime-whisper",
        url: str = "wss://api.openai.com/v1/realtime?intent=transcription",
        store: str | None = None,
        timeout: str | float = 15.0,
    ) -> None:
        self.model = model
        self.url = url
        self.timeout = float(timeout)
        self._auth = ChatGPTAuth(TokenStore(Path(store).expanduser()) if store else None)
        self._ws: ClientConnection | None = None
        self._lang: str | None = None
        self._lock = asyncio.Lock()

    async def warmup(self) -> None:
        async with self._lock:
            await self._session("ru")

    async def transcribe(self, audio: np.ndarray, lang: str) -> Transcript:
        async with self._lock:
            for attempt in range(2):
                try:
                    ws = await self._session(lang)
                    return Transcript(await self._transcribe(ws, audio), self.name)
                except (WebSocketException, OSError) as err:
                    await self._drop()
                    if attempt:
                        raise AsrError(f"realtime session: {type(err).__name__}: {err}") from err
                    log.info("realtime session dropped (%s), reconnecting", err)
        raise AssertionError("unreachable")

    async def _session(self, lang: str) -> ClientConnection:
        if self._ws is not None and self._lang == lang:
            return self._ws
        if self._ws is None:
            try:
                token = await self._auth()
            except AuthError as err:
                raise AsrError(str(err)) from err
            try:
                self._ws = await connect(
                    self.url,
                    additional_headers={"Authorization": f"Bearer {token}"},
                    max_size=2**22,
                    open_timeout=self.timeout,
                )
            except InvalidStatus as err:
                raise AsrError(f"realtime session refused: HTTP {err.response.status_code}") from err
            await self._expect(self._ws, "session.created")
        await self._ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": REALTIME_RATE},
                                "transcription": {"model": self.model, "language": lang},
                                "turn_detection": None,
                            }
                        },
                    },
                }
            )
        )
        await self._expect(self._ws, "session.updated")
        self._lang = lang
        log.info("realtime transcription session ready (%s, %s)", self.model, lang)
        return self._ws

    async def _transcribe(self, ws: ClientConnection, audio: np.ndarray) -> str:
        pcm = float_to_pcm16(resample(audio, RATE, REALTIME_RATE))
        await ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
        for i in range(0, len(pcm), CHUNK_BYTES):
            chunk = base64.b64encode(pcm[i : i + CHUNK_BYTES]).decode()
            await ws.send(json.dumps({"type": "input_audio_buffer.append", "audio": chunk}))
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        item = (await self._expect(ws, "input_audio_buffer.committed")).get("item_id")
        while True:
            ev = await self._event(ws)
            if ev.get("item_id") != item:
                continue
            if ev["type"] == "conversation.item.input_audio_transcription.completed":
                return str(ev.get("transcript", "")).strip()
            if ev["type"] == "conversation.item.input_audio_transcription.failed":
                raise AsrError(f"transcription failed: {_message(ev)}")

    async def _event(self, ws: ClientConnection) -> dict:
        try:
            ev = json.loads(await asyncio.wait_for(ws.recv(), self.timeout))
        except TimeoutError:
            await self._drop()
            raise AsrError("no answer from the realtime session") from None
        if ev.get("type") == "error":
            raise AsrError(f"realtime error: {_message(ev)}")
        return ev

    async def _expect(self, ws: ClientConnection, kind: str) -> dict:
        while True:
            ev = await self._event(ws)
            if ev.get("type") == kind:
                return ev

    async def _drop(self) -> None:
        ws, self._ws, self._lang = self._ws, None, None
        if ws is not None:
            await ws.close()

    async def aclose(self) -> None:
        await self._drop()


def _message(ev: dict) -> str:
    err = ev.get("error") or {}
    return str(err.get("message") or err.get("code") or ev.get("type"))
