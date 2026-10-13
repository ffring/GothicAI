"""Connection to the game: one OpenGothic client (ai_bridge) at a time."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
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
    EndCard,
    GameMessage,
    GraphPatchCmd,
    Hello,
    Loading,
    NpcActCmd,
    NpcActResult,
    NpcActType,
    NpcView,
    NpcViewCmd,
    PingCmd,
    Pong,
    RaiseDeadCmd,
    Result,
    RoutineCmd,
    RoutineInfo,
    Session,
    Snapshot,
    SnapshotCmd,
    StoryEffectCmd,
    StoryEvalCmd,
    StoryNearbyCmd,
    StoryPeopleCmd,
    StorySpawnCmd,
    StoryStateCmd,
    StoryTagCmd,
    StoryWatchCmd,
    WorldLoaded,
    _Command,
    parse_game_message,
)

log = logging.getLogger(__name__)

# requests answered with `result`
_Request = (
    AttachCmd
    | DetachCmd
    | DebugCmd
    | GraphPatchCmd
    | StorySpawnCmd
    | StoryEffectCmd
    | RaiseDeadCmd
    | StoryNearbyCmd
    | StoryStateCmd
    | StoryEvalCmd
    | StoryTagCmd
    | StoryPeopleCmd
)

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
        self._routines: dict[str, asyncio.Future[RoutineInfo]] = {}
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
            case RoutineInfo():
                _resolve(self._routines.pop(msg.req, None), msg)
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
            *self._routines.values(),
            *(f for waiters in self._snapshot_waiters.values() for f in waiters),
        ]
        for fut in pending:
            if not fut.done():
                fut.set_exception(err)
        self._requests.clear()
        self._actions.clear()
        self._pongs.clear()
        self._routines.clear()
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

    async def _request(self, cmd: _Request, timeout: float) -> Result:
        assert cmd.req is not None
        fut: asyncio.Future[Result] = asyncio.get_running_loop().create_future()
        self._requests[cmd.req] = fut
        try:
            await self.send(cmd)
            res = await asyncio.wait_for(fut, timeout)
        finally:
            self._requests.pop(cmd.req, None)
        if not res.ok:
            raise GameError(res.error or "request failed")
        return res

    async def attach(self, npc: str, timeout: float = 5.0) -> None:
        await self._request(AttachCmd(req=self._next_id("r"), npc=npc), timeout)

    async def detach(self, npc: str, timeout: float = 5.0) -> None:
        await self._request(DetachCmd(req=self._next_id("r"), npc=npc), timeout)

    async def routine(self, npc: str, timeout: float = 5.0) -> RoutineInfo:
        """The npc's daily routine, its routine variants and current script state (v6)."""
        cmd = RoutineCmd(req=self._next_id("q"), npc=npc)
        fut: asyncio.Future[RoutineInfo] = asyncio.get_running_loop().create_future()
        self._routines[cmd.req] = fut
        try:
            await self.send(cmd)
            info = await asyncio.wait_for(fut, timeout)
        finally:
            self._routines.pop(cmd.req, None)
        if info.error:
            raise GameError(info.error)
        return info

    async def debug(self, cmd: DebugCommand, slot: int = 0, timeout: float = 10.0, **args: Any) -> None:
        """A test helper (game in -devmode): saving/loading `save_slot_<slot>.sav` (returns once it has
        started), `spawn` (instance=, near=, distance=), `player_weapon` (state="melee"|"none"),
        `teleport` (near=, distance=), `kill` (instance=, near=), `npc_goto` (npc=, waypoint= or to= and
        distance=, face=: the npc stays free)."""
        await self._request(DebugCmd(req=self._next_id("r"), cmd=cmd, slot=slot, **args), timeout)

    async def patch_graph(
        self, ops: list[dict[str, Any]], replace: bool = False, timeout: float = 5.0
    ) -> None:
        """Change the story graph in the game (see GraphPatchCmd); GameError lists the rejected operations."""
        await self._request(GraphPatchCmd(req=self._next_id("g"), replace=replace, ops=ops), timeout)

    async def story_spawn(
        self,
        instance: str,
        tag: str,
        count: int = 1,
        near: str | None = None,
        distance: int = 600,
        waypoint: str | None = None,
    ) -> int:
        """Creatures under a tag for the director (near an npc, or at a waypoint); how many came."""
        cmd = StorySpawnCmd(
            req=self._next_id("s"),
            instance=instance.upper(),
            tag=tag,
            count=count,
            near=near,
            distance=distance,
            waypoint=waypoint,
        )
        return int((await self._request(cmd, 5.0)).value or 0)

    async def story_watch(self, npcs: list[str], owner: str = "") -> None:
        """Npc's whose distance to every tag's living creatures the game reports (story_world); each owner
        keeps its own list."""
        await self.send(StoryWatchCmd(npcs=[n.upper() for n in npcs], owner=owner))

    async def story_effect(
        self, npc: str, effects: list[dict[str, Any]], timeout: float = 5.0, *, check: bool = False
    ) -> None:
        """Effects on an npc through the scripts (see StoryEffectCmd); `check`: all or nothing, the gives
        only if each giver has what it gives."""
        cmd = StoryEffectCmd(req=self._next_id("s"), npc=npc.upper(), effects=effects, check=check or None)
        await self._request(cmd, timeout)

    async def raise_dead(
        self, npc: str, card: EndCard, spell: str | None = None, timeout: float = 5.0
    ) -> None:
        """The necromancer's answer: `npc` kills the hero, raises him as a skeleton, the game ends on
        `card`."""
        cmd = RaiseDeadCmd(req=self._next_id("s"), npc=npc.upper(), spell=spell, card=card)
        await self._request(cmd, timeout)

    async def waypoints(self, timeout: float = 10.0) -> list[dict[str, Any]]:
        """Every waypoint and freepoint of the world: name, position, freepoint or not (game in -devmode)."""
        res = await self._request(DebugCmd(req=self._next_id("r"), cmd="waypoints"), timeout)
        return list(res.value or [])

    async def npc_view(self, npcs: list[str], timeout: float = 3.0) -> list[NpcView]:
        """What these npc's perceive now (see NpcViewCmd)."""
        res = await self._request(NpcViewCmd(req=self._next_id("v"), npcs=[n.upper() for n in npcs]), timeout)
        return [NpcView.model_validate(v) for v in res.value or []]

    async def story_nearby(self, npc: str, radius: int = 2000) -> list[dict[str, Any]]:
        """People within `radius` cm of an npc."""
        res = await self._request(StoryNearbyCmd(req=self._next_id("s"), npc=npc.upper(), radius=radius), 5.0)
        return list(res.value or [])

    async def story_state(
        self,
        vars: Iterable[str] = (),
        npcs: Iterable[str] = (),
        knows: Iterable[str] = (),
        symbols: Iterable[str] = (),
        items: Iterable[str] = (),
        near: str | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """What the director's rules read (see StoryStateCmd); with `near`, how far each npc is from it."""
        cmd = StoryStateCmd(
            req=self._next_id("s"),
            vars=list(vars),
            npcs=[n.upper() for n in npcs],
            near=near.upper() if near else None,
            knows=list(knows),
            symbols=list(symbols),
            items=list(items),
        )
        return dict((await self._request(cmd, timeout)).value or {})

    async def story_people(self, timeout: float = 10.0) -> list[dict[str, Any]]:
        """Every person of the world and its day (see StoryPeopleCmd)."""
        return list((await self._request(StoryPeopleCmd(req=self._next_id("s")), timeout)).value or [])

    async def story_eval(self, npc: str, exprs: list[Any], timeout: float = 5.0) -> list[Any]:
        """Story graph expressions on the live VM, self = `npc` (see StoryEvalCmd)."""
        cmd = StoryEvalCmd(req=self._next_id("s"), npc=npc.upper(), exprs=exprs)
        return list((await self._request(cmd, timeout)).value or [])

    async def story_tag(self, tag: str, npcs: list[str], timeout: float = 5.0) -> int:
        """Npc's of the world watched under a tag (see StoryTagCmd); how many were found."""
        cmd = StoryTagCmd(req=self._next_id("s"), tag=tag, npcs=[n.upper() for n in npcs])
        return int((await self._request(cmd, timeout)).value or 0)

    async def offered_dialogs(self, npc: str, timeout: float = 5.0) -> list[str]:
        """The dialogs the npc offers the player now (game in -devmode)."""
        res = await self._request(DebugCmd(req=self._next_id("r"), cmd="dialogs", npc=npc.upper()), timeout)
        return list(res.value or [])

    async def choose_dialog(self, npc: str, node: str, timeout: float = 5.0) -> None:
        """Play a dialog the npc offers as if the player chose it (game in -devmode)."""
        await self._request(
            DebugCmd(req=self._next_id("r"), cmd="choose", npc=npc.upper(), node=node.upper()), timeout
        )

    async def deal_window(self, timeout: float = 5.0) -> dict[str, Any] | None:
        """What the deal window shows (game in -devmode): {deal, version, npc, npc_gives, hero_gives,
        missing, sent}, or None."""
        res = await self._request(DebugCmd(req=self._next_id("r"), cmd="deal"), timeout)
        return dict(res.value) if res.value else None

    async def press_deal(self, timeout: float = 5.0) -> str:
        """Y as the player presses it on the deal window (game in -devmode): "sent", or why not ("none",
        "waiting", "missing", "changed", "blocked")."""
        res = await self._request(DebugCmd(req=self._next_id("r"), cmd="deal_accept"), timeout)
        return str(res.value or "")

    async def decline_deal(self, timeout: float = 5.0) -> str:
        """N as the player presses it on the deal window (game in -devmode): "declined", "none", "blocked"."""
        res = await self._request(DebugCmd(req=self._next_id("r"), cmd="deal_decline"), timeout)
        return str(res.value or "")

    async def check_conditions(self, timeout: float = 30.0) -> dict[str, Any]:
        """Script conditions of every npc in the world against their lifted form (game in -devmode)."""
        return dict((await self._request(DebugCmd(req=self._next_id("r"), cmd="check"), timeout)).value or {})

    async def evaluate(self, expr: list[Any], npc: str | None = None, timeout: float = 5.0) -> Any:
        """A story graph expression computed in the game, self = `npc` (game in -devmode)."""
        cmd = DebugCmd(req=self._next_id("r"), cmd="eval", npc=npc.upper() if npc else None, expr=expr)
        return (await self._request(cmd, timeout)).value

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
