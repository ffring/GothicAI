"""Client of the gothic-decider service (decider-service/): one connection, replies matched by id.

When the service is down, `decide` answers `error` at once and the npc simply keeps its routine;
reconnecting is retried at most every `retry_s`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

log = logging.getLogger(__name__)

OutcomeKind = Literal["answers", "expired", "cancelled", "error"]
Lane = Literal["urgent", "background"]


@dataclass(frozen=True)
class Outcome:
    kind: OutcomeKind
    answers: dict[str, dict] | None = None
    queue_ms: float | None = None
    infer_ms: float | None = None
    detail: str | None = None


class Decider(Protocol):
    async def decide(
        self, rid: str, lane: Lane, priority: float, deadline_ms: int, state: str, questions: dict[str, dict]
    ) -> Outcome: ...

    async def cancel(self, rid: str) -> None: ...


class DeciderClient:
    def __init__(self, url: str, retry_s: float = 2.0, connect_timeout: float = 3.0) -> None:
        self.url = url
        self.retry_s = retry_s
        self.connect_timeout = connect_timeout
        self.model: str | None = None
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[Outcome]] = {}
        self._lock = asyncio.Lock()
        self._next_try = 0.0

    async def decide(
        self, rid: str, lane: Lane, priority: float, deadline_ms: int, state: str, questions: dict[str, dict]
    ) -> Outcome:
        ws = await self._connection()
        if ws is None:
            return Outcome("error", detail="decider unreachable")
        fut: asyncio.Future[Outcome] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        msg = {
            "type": "decide",
            "id": rid,
            "lane": lane,
            "priority": priority,
            "deadline_ms": deadline_ms,
            "state": state,
            "questions": questions,
        }
        try:
            await ws.send(json.dumps(msg, ensure_ascii=False))
            # the service always answers; the margin covers a service that hangs
            return await asyncio.wait_for(fut, deadline_ms / 1000 + 10)
        except (OSError, WebSocketException) as err:
            return Outcome("error", detail=f"decider: {err}")
        except TimeoutError:
            return Outcome("error", detail="decider did not answer")
        finally:
            self._pending.pop(rid, None)

    async def cancel(self, rid: str) -> None:
        if self._ws is not None and rid in self._pending:
            try:
                await self._ws.send(json.dumps({"type": "cancel", "id": rid}))
            except (OSError, WebSocketException):
                pass

    async def aclose(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            await ws.close()
        if self._reader is not None:
            self._reader.cancel()

    async def _connection(self) -> ClientConnection | None:
        if self._ws is not None:
            return self._ws
        async with self._lock:
            if self._ws is not None:
                return self._ws
            if time.monotonic() < self._next_try:
                return None
            try:
                ws = await connect(self.url, open_timeout=self.connect_timeout, max_size=2**22)
                hello = json.loads(await asyncio.wait_for(ws.recv(), self.connect_timeout))
            except (OSError, WebSocketException, TimeoutError, ValueError) as err:
                self._next_try = time.monotonic() + self.retry_s
                log.warning("decider %s unreachable: %s", self.url, err)
                return None
            self.model = hello.get("model")
            log.info("decider connected: %s (%s)", self.url, self.model)
            self._ws = ws
            self._reader = asyncio.create_task(self._read(ws))
            return ws

    async def _read(self, ws: ClientConnection) -> None:
        try:
            async for raw in ws:
                msg: dict[str, Any] = json.loads(raw)
                fut = self._pending.get(str(msg.get("id")))
                if fut is None or fut.done():
                    continue
                kind = msg.get("type")
                if kind in ("answers", "expired", "cancelled", "error"):
                    fut.set_result(
                        Outcome(
                            kind,
                            msg.get("answers"),
                            msg.get("queue_ms"),
                            msg.get("infer_ms"),
                            msg.get("detail"),
                        )
                    )
        except WebSocketException:
            pass
        finally:
            if self._ws is ws:
                self._ws = None
            log.warning("decider connection closed")
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result(Outcome("error", detail="decider disconnected"))
