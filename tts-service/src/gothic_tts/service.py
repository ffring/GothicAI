"""WebSocket API of the service. It knows nothing about the game: text and a voice in, audio out.

    client  -> service {"type":"speak","id":"7","text":"...",
                        "voice":{"id":"xardas","description":"...","preset":null}}
    service -> client  {"type":"start","id":"7","sample_rate":48000,"provider":"voxcpm"}
    service -> client  binary frames: 16-bit little-endian mono PCM, as it is generated
                       (at the loudness of the game's own dialogue, see loudness.py)
    service -> client  {"type":"done","id":"7","audio_s":2.1,"first_chunk_s":0.06,"latency_s":1.1}
    service -> client  {"type":"error","id":"7","detail":"..."}

Requests on one connection are handled one after another.
"""

from __future__ import annotations

import json
import logging

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .providers import TtsError, TtsProvider, Voice
from .synthesizer import Synthesizer

log = logging.getLogger(__name__)

MAX_TEXT = 1000


class TtsService:
    def __init__(self, synthesizer: Synthesizer, host: str = "127.0.0.1", port: int = 47820) -> None:
        self.synthesizer = synthesizer
        self.host = host
        self.port = port

    async def serve_forever(self, started=None) -> None:
        async with serve(self._handler, self.host, self.port, max_size=2**20) as server:
            self.port = server.sockets[0].getsockname()[1]
            log.info("listening on ws://%s:%d", self.host, self.port)
            if started is not None:
                started.set()
            await server.serve_forever()

    async def _handler(self, conn: ServerConnection) -> None:
        log.info("client connected from %s", conn.remote_address)
        try:
            async for raw in conn:
                if isinstance(raw, bytes):
                    continue
                try:
                    msg = json.loads(raw)
                    kind = msg["type"]
                except (ValueError, KeyError, TypeError):
                    await _send(conn, {"type": "error", "detail": "malformed message"})
                    continue
                if kind != "speak":
                    await _send(
                        conn, {"type": "error", "id": msg.get("id"), "detail": f"unknown type {kind!r}"}
                    )
                    continue
                await self._speak(conn, msg)
        except ConnectionClosed:
            pass
        log.info("client disconnected")

    async def _speak(self, conn: ServerConnection, msg: dict) -> None:
        req = msg.get("id")
        text = str(msg.get("text", "")).strip()[:MAX_TEXT]
        v = msg.get("voice") or {}
        voice = Voice(str(v.get("id", "default")), str(v.get("description") or ""), v.get("preset"))
        if not text:
            await _send(conn, {"type": "error", "id": req, "detail": "empty text"})
            return
        current: list[TtsProvider] = []

        async def chunk(pcm: bytes, p: TtsProvider) -> None:
            if not current:
                current.append(p)
                await _send(conn, {"type": "start", "id": req, "sample_rate": p.rate, "provider": p.name})
            await conn.send(pcm)

        try:
            s = await self.synthesizer.speak(text, voice, chunk)
        except TtsError as err:
            await _send(conn, {"type": "error", "id": req, "detail": str(err)})
            return
        await _send(
            conn,
            {
                "type": "done",
                "id": req,
                "audio_s": round(s.audio_s, 3),
                "first_chunk_s": round(s.first_chunk_s, 3),
                "latency_s": round(s.latency_s, 3),
            },
        )


async def _send(conn: ServerConnection, msg: dict) -> None:
    await conn.send(json.dumps(msg, ensure_ascii=False))
