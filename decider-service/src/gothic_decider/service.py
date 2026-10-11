"""WebSocket API of the service. It knows nothing about the game: a state and typed questions in,
probabilities out, scheduled by lane, priority and deadline.

    service -> client  {"type":"hello","service":"gothic-decider","model":"...",
                        "lanes":["urgent","background"]}
    client  -> service {"type":"decide","id":"npc42#7","lane":"urgent"|"background","priority":3.5,
                        "deadline_ms":300,"state":"...","questions":{...}}
    service -> client  {"type":"answers","id":"npc42#7","answers":{...},"queue_ms":12.0,"infer_ms":35.1}
                     | {"type":"expired","id":"...","queue_ms":...}   (deadline passed before it started)
                     | {"type":"cancelled","id":"..."}
                     | {"type":"error","id":"...","detail":"..."}
    client  -> service {"type":"cancel","id":"npc42#7"}
    client  -> service {"type":"stats"}  ->  {"type":"stats", ...scheduler snapshot...}

Every `decide` gets exactly one of answers / expired / cancelled / error. Ids are per connection;
closing the connection cancels its requests.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .protocol import Lane, ProtocolError, parse_decide
from .scheduler import Scheduler

log = logging.getLogger(__name__)


class DeciderService:
    def __init__(self, scheduler: Scheduler, host: str = "127.0.0.1", port: int = 47832) -> None:
        self.scheduler = scheduler
        self.host = host
        self.port = port
        self._conn_ids = itertools.count(1)

    async def serve_forever(self, started: asyncio.Event | None = None) -> None:
        worker = asyncio.create_task(self.scheduler.run())
        try:
            async with serve(self._handler, self.host, self.port, max_size=2**22) as server:
                self.port = server.sockets[0].getsockname()[1]
                log.info("listening on ws://%s:%d", self.host, self.port)
                if started is not None:
                    started.set()
                await server.serve_forever()
        finally:
            worker.cancel()

    async def _handler(self, conn: ServerConnection) -> None:
        cid = next(self._conn_ids)
        log.info("client %d connected from %s", cid, conn.remote_address)
        outbox: asyncio.Queue[dict] = asyncio.Queue()
        writer = asyncio.create_task(_write(conn, outbox))
        mine: set[tuple[int, str]] = set()

        def reply_for(key: tuple[int, str]):
            def reply(msg: dict) -> None:
                mine.discard(key)
                outbox.put_nowait(msg)

            return reply

        outbox.put_nowait(
            {
                "type": "hello",
                "service": "gothic-decider",
                "model": self.scheduler.engine.name,
                "lanes": [lane.value for lane in Lane],
            }
        )
        try:
            async for raw in conn:
                try:
                    msg = json.loads(raw)
                    kind = msg["type"]
                except (ValueError, KeyError, TypeError):
                    outbox.put_nowait({"type": "error", "detail": "malformed message"})
                    continue
                if kind == "decide":
                    try:
                        req = parse_decide(msg)
                    except ProtocolError as err:
                        outbox.put_nowait({"type": "error", "id": msg.get("id"), "detail": str(err)})
                        continue
                    key = (cid, req.id)
                    if key in mine:
                        outbox.put_nowait({"type": "error", "id": req.id, "detail": "duplicate id"})
                        continue
                    mine.add(key)
                    self.scheduler.submit(key, req, reply_for(key))
                elif kind == "cancel":
                    self.scheduler.cancel((cid, str(msg.get("id"))))
                elif kind == "stats":
                    outbox.put_nowait({"type": "stats", **self.scheduler.snapshot()})
                else:
                    outbox.put_nowait({"type": "error", "detail": f"unknown message type {kind!r}"})
        except ConnectionClosed:
            pass
        finally:
            for key in list(mine):
                self.scheduler.cancel(key)
            writer.cancel()
        log.info("client %d disconnected", cid)


async def _write(conn: ServerConnection, outbox: asyncio.Queue[dict]) -> None:
    try:
        while True:
            await conn.send(json.dumps(await outbox.get(), ensure_ascii=False))
    except ConnectionClosed:
        pass
