"""Connection to the game: one OpenGothic client (ai_bridge) at a time."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any

from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .protocol import (
    PROTOCOL_VERSION,
    ActionCmd,
    ActionResult,
    ActionType,
    AttachCmd,
    DebugCmd,
    DebugCommand,
    DetachCmd,
    Detached,
    GameMessage,
    Hello,
    Loading,
    NpcActCmd,
    NpcActResult,
    NpcActType,
    PingCmd,
    Pong,
    Result,
    Session,
    Snapshot,
    SnapshotCmd,
    WorldLoaded,
    _Command,
    parse_game_message,
)

log = logging.getLogger(__name__)

Listener = Callable[[GameMessage], None]


class GameError(RuntimeError):
    """The game rejected a request."""


class GameDisconnected(GameError):
    """The game connection closed while a request was pending."""


class GameLink:
    """A connected game. Requests are matched to replies by `req`/`id`."""

    def __init__(self, conn: ServerConnection) -> None:
        self._conn = conn
        self._ids = itertools.count(1)
        self._requests: dict[str, asyncio.Future[Result]] = {}
        self._actions: dict[str, asyncio.Future[ActionResult]] = {}
        self._pongs: dict[str, asyncio.Future[Pong]] = {}
        self._snapshot_waiters: dict[str, list[asyncio.Future[Snapshot]]] = {}
        self._world_ready = asyncio.Event()
        self.hello: Hello | None = None
        self.world: str | None = None
        self.session: Session | None = None
        self.snapshots: dict[str, Snapshot] = {}
        self.listeners: list[Listener] = []
        self.closed = asyncio.Event()

    # --- receiving --------------------------------------------------------------

    async def run(self) -> None:
        try:
            async for raw in self._conn:
                try:
                    msg = parse_game_message(raw)
                except ValidationError as err:
                    log.warning("unparsed message from game: %s (%s)", raw, err.errors()[0]["msg"])
                    continue
                self._dispatch(msg)
        except ConnectionClosed:
            pass
        finally:
            self.closed.set()
            self._fail_pending(GameDisconnected("game disconnected"))

    def _dispatch(self, msg: GameMessage) -> None:
        match msg:
            case Hello():
                self.hello = msg
                if msg.protocol != PROTOCOL_VERSION:
                    log.warning("protocol mismatch: game %s, server %s", msg.protocol, PROTOCOL_VERSION)
            case WorldLoaded():
                self.world = msg.world
                self._world_ready.set()
            case Session():
                self.session = msg
            case Loading():
                self.world = None
                self._world_ready.clear()
                self.snapshots.clear()
            case Result():
                _resolve(self._requests.pop(msg.req, None), msg)
            case ActionResult():
                _resolve(self._actions.pop(msg.id, None), msg)
            case Pong():
                _resolve(self._pongs.pop(msg.req, None), msg)
            case Snapshot():
                self.snapshots[msg.npc.id] = msg
                for fut in self._snapshot_waiters.pop(msg.npc.id, []):
                    _resolve(fut, msg)
            case Detached():
                self.snapshots.pop(msg.npc, None)
            case NpcActResult(status="rejected"):
                log.debug("npc_act %s for %s rejected: %s", msg.id, msg.npc, msg.detail)
        for listener in self.listeners:
            listener(msg)

    def _fail_pending(self, err: Exception) -> None:
        pending: list[asyncio.Future[Any]] = [
            *self._requests.values(),
            *self._actions.values(),
            *self._pongs.values(),
            *(f for waiters in self._snapshot_waiters.values() for f in waiters),
        ]
        for fut in pending:
            if not fut.done():
                fut.set_exception(err)
        self._requests.clear()
        self._actions.clear()
        self._pongs.clear()
        self._snapshot_waiters.clear()

    # --- requests ---------------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}"

    async def send(self, cmd: _Command) -> None:
        if self.closed.is_set():
            raise GameDisconnected("game disconnected")
        await self._conn.send(cmd.to_json())

    async def wait_world(self, timeout: float | None = None) -> str:
        await asyncio.wait_for(self._world_ready.wait(), timeout)
        assert self.world is not None
        return self.world

    async def _request(self, cmd: AttachCmd | DetachCmd | DebugCmd, timeout: float) -> None:
        fut: asyncio.Future[Result] = asyncio.get_running_loop().create_future()
        self._requests[cmd.req] = fut
        try:
            await self.send(cmd)
            res = await asyncio.wait_for(fut, timeout)
        finally:
            self._requests.pop(cmd.req, None)
        if not res.ok:
            raise GameError(res.error or "request failed")

    async def attach(self, npc: str, timeout: float = 5.0) -> None:
        await self._request(AttachCmd(req=self._next_id("r"), npc=npc), timeout)

    async def detach(self, npc: str, timeout: float = 5.0) -> None:
        await self._request(DetachCmd(req=self._next_id("r"), npc=npc), timeout)

    async def debug(self, cmd: DebugCommand, slot: int = 0, timeout: float = 10.0, **args: Any) -> None:
        """A test helper (game in -devmode): saving/loading `save_slot_<slot>.sav` (returns once it has
        started), `spawn` (instance=, near=, distance=), `player_weapon` (state="melee"|"none")."""
        await self._request(DebugCmd(req=self._next_id("r"), cmd=cmd, slot=slot, **args), timeout)

    async def act(
        self, npc: str, action: ActionType, /, *, timeout: float = 90.0, **args: Any
    ) -> ActionResult:
        """Run one action and wait for its result. The game enforces its own per-action deadline.

        `args` go to the game as-is, e.g. act(npc, "turn_to", npc="PLAYER").
        """
        cmd = ActionCmd(id=self._next_id("a"), npc=npc, action=action, args=args)
        fut: asyncio.Future[ActionResult] = asyncio.get_running_loop().create_future()
        self._actions[cmd.id] = fut
        try:
            await self.send(cmd)
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._actions.pop(cmd.id, None)

    async def npc_act(self, npc: str, action: NpcActType, /, **args: Any) -> str:
        """Queue a soft action for an npc that follows its routine; returns the id. The game answers
        `npc_act_result` at once and `npc_idle` when the npc is done."""
        cmd = NpcActCmd(id=self._next_id("n"), npc=npc, action=action, args=args)
        await self.send(cmd)
        return cmd.id

    async def snapshot(self, npc: str, *, fresh: bool = False, timeout: float = 5.0) -> Snapshot:
        npc = npc.upper()
        if not fresh and npc in self.snapshots:
            return self.snapshots[npc]
        fut: asyncio.Future[Snapshot] = asyncio.get_running_loop().create_future()
        self._snapshot_waiters.setdefault(npc, []).append(fut)
        await self.send(SnapshotCmd(npc=npc))
        return await asyncio.wait_for(fut, timeout)

    async def ping(self, timeout: float = 5.0) -> float:
        loop = asyncio.get_running_loop()
        cmd = PingCmd(req=self._next_id("p"))
        fut: asyncio.Future[Pong] = loop.create_future()
        self._pongs[cmd.req] = fut
        started = loop.time()
        try:
            await self.send(cmd)
            await asyncio.wait_for(fut, timeout)
        finally:
            self._pongs.pop(cmd.req, None)
        return loop.time() - started


def _resolve[T](fut: asyncio.Future[T] | None, value: T) -> None:
    if fut is not None and not fut.done():
        fut.set_result(value)


OnLink = Callable[[GameLink], Awaitable[None]]


class GameServer:
    """WebSocket server the game connects to. A new connection replaces the previous one.

    `GET /health` on the same port answers {"ok":true,"service":"gothic-ai","game":<connected>}:
    the launcher waits for it before starting the game."""

    def __init__(self, host: str = "127.0.0.1", port: int = 47817, on_link: OnLink | None = None) -> None:
        self.host = host
        self.port = port
        self.on_link = on_link
        self.link: GameLink | None = None
        self.started = asyncio.Event()
        self._link_changed = asyncio.Condition()

    async def wait_link(self) -> GameLink:
        async with self._link_changed:
            await self._link_changed.wait_for(lambda: self.link is not None and not self.link.closed.is_set())
            assert self.link is not None
            return self.link

    async def _handler(self, conn: ServerConnection) -> None:
        link = GameLink(conn)
        log.info("game connected from %s", conn.remote_address)
        async with self._link_changed:
            self.link = link
            self._link_changed.notify_all()

        session = asyncio.create_task(self.on_link(link)) if self.on_link else None
        if session is not None:
            session.add_done_callback(_log_session_error)
        try:
            await link.run()
        finally:
            log.info("game disconnected")
            if session is not None and not session.done():
                session.cancel()
                await asyncio.gather(session, return_exceptions=True)
            async with self._link_changed:
                if self.link is link:
                    self.link = None
                self._link_changed.notify_all()

    def _health(self, conn: ServerConnection, request: Request) -> Response | None:
        if request.path != "/health":
            return None
        game = self.link is not None and not self.link.closed.is_set()
        resp = conn.respond(HTTPStatus.OK, json.dumps({"ok": True, "service": "gothic-ai", "game": game}))
        del resp.headers["Content-Type"]
        resp.headers["Content-Type"] = "application/json"
        return resp

    async def serve_forever(self) -> None:
        async with serve(self._handler, self.host, self.port, process_request=self._health) as server:
            self.port = server.sockets[0].getsockname()[1]  # actual port when 0 was requested
            self.started.set()
            log.info("waiting for the game on ws://%s:%d", self.host, self.port)
            await server.serve_forever()


def _log_session_error(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("game session failed", exc_info=task.exception())
