"""One realtime melee lease driven by the remote P30 JSONL policy (no model dependencies)."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import math
import time
from collections import Counter, deque
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from websockets.exceptions import ConnectionClosed

from .game import GameDisconnected, GameError, GameLink
from .protocol import (
    Attached,
    BodyControlEvents,
    BodyLease,
    BodyObservation,
    GameMessage,
    Hello,
    Loading,
    NpcEvent,
    NpcRoster,
    RosterEntry,
)

log = logging.getLogger(__name__)
SERVICE_TIMEOUT = 0.100
BRIDGE_TIMEOUT = 0.250
ACQUIRE_INTERVAL = 0.150
ACQUIRE_WINDOW = 5.0
MAX_FIGHT_SECONDS = 300.0
HEARTBEAT_INTERVAL = 0.250
HEARTBEAT_TIMEOUT = 0.500
LEASE_TIMEOUT_MS = 2000
EVENTS_INTERVAL = 0.150
FINISH_TIMEOUT = 2.0
MAX_JSON_BYTES = 256 * 1024
BRIDGE_ERRORS = (GameError, ConnectionClosed, OSError, TimeoutError)
TARGET_IS_HERO = [
    "&&",
    ["call", "NPC_GETTARGET", ["inst", "SELF"]],
    ["call", "NPC_ISPLAYER", ["inst", "OTHER"]],
]


class PolicyError(RuntimeError):
    """A policy response cannot authorize an action."""


class PolicyClient:
    """Async JSONL RPC. Late replies cannot become the next request's response."""

    def __init__(self, url: str) -> None:
        address = urlsplit(url)
        if address.scheme != "tcp" or not address.hostname or not address.port or address.path:
            raise ValueError("body URL must be tcp://host:port")
        self.host, self.port = address.hostname, address.port
        self.writer: asyncio.StreamWriter | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self.ids = itertools.count(1)

    async def connect(self) -> None:
        async with asyncio.timeout(SERVICE_TIMEOUT):
            reader, self.writer = await asyncio.open_connection(self.host, self.port, limit=MAX_JSON_BYTES)
        self.reader_task = asyncio.create_task(self._read(reader))

    async def _read(self, reader: asyncio.StreamReader) -> None:
        try:
            while line := await reader.readline():
                if len(line) > MAX_JSON_BYTES or not line.endswith(b"\n"):
                    raise PolicyError("invalid policy response framing")
                response = json.loads(line)
                if not isinstance(response, dict) or type(response.get("id")) is not int:
                    raise PolicyError("invalid policy response identity")
                future = self.pending.get(response["id"])
                if future is not None and not future.done():
                    future.set_result(response)
            raise PolicyError("policy disconnected")
        except (OSError, ValueError, PolicyError) as error:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)

    async def call(self, command: str, **fields: Any) -> dict[str, Any]:
        request = {"id": next(self.ids), "cmd": command, **fields}
        encoded = (json.dumps(request, separators=(",", ":"), allow_nan=False) + "\n").encode()
        if len(encoded) > MAX_JSON_BYTES:
            raise PolicyError("policy request exceeds bounded JSONL size")
        if self.writer is None or self.writer.is_closing():
            raise PolicyError("policy is not connected")
        future = asyncio.get_running_loop().create_future()
        self.pending[request["id"]] = future
        try:
            async with asyncio.timeout(SERVICE_TIMEOUT):
                self.writer.write(encoded)
                await self.writer.drain()
                response = await future
            if response.get("ok") is not True:
                raise PolicyError(f"policy rejected {command}: {response.get('reason')}")
            return response
        finally:
            self.pending.pop(request["id"], None)

    async def close(self) -> None:
        try:
            if self.writer is not None:
                self.writer.close()
                with contextlib.suppress(OSError):
                    await self.writer.wait_closed()
        finally:
            if self.reader_task is not None:
                self.reader_task.cancel()
                await asyncio.gather(self.reader_task, return_exceptions=True)


def _percentile(values: list[float], fraction: float) -> float | None:
    return sorted(values)[math.ceil(len(values) * fraction) - 1] if values else None


class BodyPilot:
    """Single-owner state machine with a separate wall-clock lease keepalive."""

    def __init__(
        self,
        link: GameLink,
        url: str,
        *,
        companion: Callable[[str], bool] = lambda npc: False,
        unavailable: Callable[[str], bool] = lambda npc: False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.link = link
        self.url = url
        self.companion, self.unavailable, self.clock = companion, unavailable, clock
        self.people: dict[str, RosterEntry] = {}
        # Repeated attack perceptions belong to the same combat, even after fallback.
        self._claimed_fights: set[str] = set()
        # Periodic roster updates cannot restart an exhausted acquisition window.
        self._roster_attempted: set[str] = set()
        self.npc: str | None = None
        self.lease: BodyLease | None = None
        self.client: PolicyClient | None = None
        self._rows: deque[tuple[dict[str, Any], float]] = deque()
        self._wake = asyncio.Event()
        self._done = asyncio.Event()
        self._closing = False
        self._running = False
        self._reason: str | None = None
        self._watched = False
        self._watch_ready = False
        self._attempt_at = 0.0
        self._last_acquire = float("-inf")
        self._fight_at = 0.0
        self._initial_game_ms: int | None = None
        self._now_game_ms = 0
        self._last_seq = 0
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._last_events_poll = 0.0
        self._acquiring = False
        self._pending_acquire: BodyObservation | None = None
        self._symbol: int | None = None
        self._entity: int | None = None
        self._hero: int | None = None
        self._refusals: Counter[str] = Counter()
        self._latencies: list[float] = []
        self._decisions = 0
        self._fallbacks = 0
        link.listeners.append(self.on_message)
        link.raw_body_listeners.append(self.on_body_packet)

    def _allowed(self, npc: str) -> bool:
        entry = self.people.get(npc)
        return bool(
            entry is not None
            and entry.guild <= 16
            and entry.distance <= 2000
            and entry.activity not in {"dialog", "attached", "down"}
            and (entry.activity != "body" or self.lease is not None or self._acquiring)
            and not self.companion(npc)
            and not self.unavailable(npc)
            and not self.link.party.get(npc, (0, 0))[1]
        )

    def on_message(self, message: GameMessage) -> None:
        if isinstance(message, NpcRoster):
            entries = {entry.id.upper(): entry for entry in message.npcs}
            ended = {
                npc
                for npc, entry in entries.items()
                if entry.activity == "down"
                or (entry.activity == "routine" and entry.state.upper() not in {"ZS_ATTACK", "ZS_MM_ATTACK"})
            }
            self._claimed_fights.difference_update(ended)
            self._roster_attempted.difference_update(ended)
            self.people = entries if message.full else {**self.people, **entries}
            if self.npc is not None and not self._allowed(self.npc):
                self._reason = "npc_unavailable"
            # A lone hostile NPC can attack a passive hero without any combat
            # perception reaching the server. A drawn weapon alone is not a fight.
            for npc, entry in sorted(entries.items(), key=lambda item: item[1].distance):
                if entry.activity != "fight" or entry.state.upper() != "ZS_ATTACK":
                    continue
                if npc == self.npc:
                    self._roster_attempted.add(npc)
                elif npc not in self._roster_attempted and self._start(npc):
                    break
        elif isinstance(message, (Loading, Hello)):
            self.people.clear()
            self._claimed_fights.clear()
            self._roster_attempted.clear()
            self._reason = "loading" if isinstance(message, Loading) else "session_changed"
        elif isinstance(message, Attached) and message.npc.upper() == self.npc:
            self._reason = "attached"
        elif isinstance(message, BodyControlEvents) and self.lease is not None:
            for event in message.events:
                if (
                    (event.entity_id, event.session_epoch, event.world_epoch, event.control_generation)
                    == self._identity(self.lease)
                    and event.phase == "ownership"
                    and event.reason == "native_restored"
                ):
                    self._reason = "native_restored"
        elif isinstance(message, NpcEvent) and self.npc is None and not self._closing:
            npc = None
            if message.other.upper() in {"PLAYER", "HERO"} and message.kind in {
                "attack",
                "threatened",
                "weapon_drawn",
                "npc_attack",
            }:
                npc = message.npc.upper()
            elif message.kind == "hero_attacked":
                npc = message.other.upper()
            if npc is not None:
                self._start(npc)
        self._wake.set()

    def _start(self, npc: str) -> bool:
        if (
            self.npc is not None
            or self._closing
            or npc in self._claimed_fights
            or not self._allowed(npc)
            or self.link.body_leases
        ):
            return False
        self.npc = npc
        self._reason = None
        self._attempt_at = self.clock()
        entry = self.people[npc]
        if entry.activity == "fight" and entry.state.upper() == "ZS_ATTACK":
            self._roster_attempted.add(npc)
        return True

    @staticmethod
    def _identity(lease: BodyLease) -> tuple[int, int, int, int]:
        return lease.entity_id, lease.session_epoch, lease.world_epoch, lease.control_generation

    def on_body_packet(self, packet: dict[str, Any]) -> None:
        rows = packet["observations"]
        if rows:
            self._now_game_ms = max(row["t_game_ms"] for row in rows)
        if self.npc is None or not self._watch_ready:
            return
        if self.lease is not None and (packet["session_epoch"], packet["world_epoch"]) != (
            self.lease.session_epoch,
            self.lease.world_epoch,
        ):
            self._reason = "world_changed"
        for row in rows:
            if len(self._rows) >= 128:
                self._reason = "observation_overflow"
                break
            self._rows.append((row, self.clock()))
        self._wake.set()

    async def run(self) -> None:
        self._running = True
        try:
            while not self._closing:
                self._wake.clear()
                if self.link.closed.is_set():
                    self._reason = "disconnected"
                    break
                if self.npc is not None:
                    await self._tick()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.025)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            self._reason = self._reason or "shutdown"
            raise
        except Exception:
            self._reason = "pilot_error"
            log.exception("neural-body pilot failed")
        finally:
            try:
                await self._finish(self._reason or "shutdown")
            finally:
                self._unregister()
                self._done.set()

    async def aclose(self) -> None:
        self._closing = True
        self._wake.set()
        if self._running:
            await self._done.wait()
        else:
            try:
                await self._finish("shutdown")
            finally:
                self._unregister()
                self._done.set()

    def _unregister(self) -> None:
        if self.on_message in self.link.listeners:
            self.link.listeners.remove(self.on_message)
        if self.on_body_packet in self.link.raw_body_listeners:
            self.link.raw_body_listeners.remove(self.on_body_packet)

    async def _heartbeats(self, lease: BodyLease) -> None:
        """Renew from send time, independently of policy calls and ownership polling."""
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        while self.lease == lease and not self._closing and self._reason is None:
            await asyncio.sleep(max(0.0, deadline - loop.time()))
            if self.lease != lease or self._closing or self._reason is not None:
                return
            if self.link.closed.is_set() or self.link.body_leases.get(lease.entity_id) != lease:
                self._reason = "lease_lost"
                self._wake.set()
                return
            sent_at = loop.time()
            try:
                await self.link.body_heartbeat(lease, timeout=HEARTBEAT_TIMEOUT)
            except Exception:
                # Never let a detached keepalive task fail silently and leave the
                # main loop accepting policy decisions for an unrenewed lease.
                self._reason = self._reason or "lease_lost"
                self._wake.set()
                return
            deadline = sent_at + HEARTBEAT_INTERVAL

    async def _tick(self) -> None:
        if self._reason:
            await self._finish(self._reason)
            return
        assert self.npc is not None
        now = self.clock()
        if not self._allowed(self.npc):
            await self._finish("npc_unavailable")
            return
        if self.lease is None and now - self._attempt_at >= ACQUIRE_WINDOW:
            await self._finish("acquire_exhausted")
            return
        if not self._watched:
            try:
                symbol = await self.link.story_eval(self.npc, [["sym", self.npc]], timeout=BRIDGE_TIMEOUT)
                if len(symbol) != 1 or type(symbol[0]) is not int or symbol[0] < 0:
                    raise GameError("NPC script symbol unavailable")
                self._symbol = symbol[0]
                # A timed-out request may already have enabled observations on
                # the bridge. Track it before awaiting the reply so cleanup
                # also unsubscribes when that acknowledgement never arrives.
                self._watched = True
                await self.link.body_watch(self._watch_npcs(), timeout=BRIDGE_TIMEOUT)
            except BRIDGE_ERRORS:
                await self._finish("watch_failed")
                return
            self._watch_ready = True
        if self.lease is not None:
            if self.link.body_leases.get(self.lease.entity_id) != self.lease:
                await self._finish("lease_lost")
                return
            if now - self._fight_at >= MAX_FIGHT_SECONDS or (
                self._initial_game_ms is not None and self._now_game_ms - self._initial_game_ms >= 300_000
            ):
                await self._finish("fight_timeout")
                return
            loop = asyncio.get_running_loop()
            if loop.time() - self._last_events_poll >= EVENTS_INTERVAL:
                try:
                    self._last_events_poll = loop.time()
                    await self.link.body_events(timeout=BRIDGE_TIMEOUT)
                except BRIDGE_ERRORS:
                    await self._finish("lease_lost")
                    return
                if self._reason or self.link.body_leases.get(self.lease.entity_id) != self.lease:
                    await self._finish(self._reason or "lease_lost")
                    return
        while self._rows and self.npc is not None:
            row, received_at = self._rows.popleft()
            await self._row(row, received_at)
            if self._reason:
                await self._finish(self._reason)
                break
            if (
                self.lease is not None
                and asyncio.get_running_loop().time() - self._last_events_poll >= EVENTS_INTERVAL
            ):
                # Keep ownership polling responsive under a continuous row stream.
                self._wake.set()
                break

    def _watch_npcs(self) -> list[str]:
        assert self.npc is not None
        return [self.npc]

    def _is_hero(self, row: dict[str, Any]) -> bool:
        return self._hero is None or row["target"]["entity_id"] == self._hero

    def _terminal(self, row: dict[str, Any]) -> str | None:
        own, target = row["self"], row["target"]
        if own["dead"] or own["unconscious"] or own["down"] or own["hp"] <= 0:
            return "npc_down"
        if not target["present"] or not target["visible"] or not target["current_valid"]:
            return "target_lost"
        if not self._is_hero(row):
            return "target_changed"
        if target["down"] or target["hp"] is None or target["hp"] <= 0:
            return "hero_down"
        if own["weapon_state"] != 2:
            return "weapon_changed"
        return None

    async def _row(self, raw: dict[str, Any], received_at: float) -> None:
        if raw["script_symbol"] != self._symbol or not raw["periodic"]:
            return
        if self._entity is not None and raw["entity_id"] != self._entity:
            return
        if (raw["session_epoch"], raw["world_epoch"]) != (
            self.link.body_session_epoch,
            self.link.body_generation,
        ):
            return
        if raw["observation_seq"] <= self._last_seq:
            return
        if self.lease is not None and (
            raw["entity_id"],
            raw["session_epoch"],
            raw["world_epoch"],
            raw["control_generation"],
        ) != self._identity(self.lease):
            return
        self._last_seq = raw["observation_seq"]
        if self.lease is not None:
            terminal = self._terminal(raw)
            if terminal:
                self._reason = terminal
                return
        elif (
            self._terminal(raw)
            or self.clock() - received_at > 0.100
            or self._now_game_ms - raw["t_game_ms"] > 100
        ):
            return
        observation = self.link.body_latest.get(raw["entity_id"])
        if observation is None or observation.observation_seq != raw["observation_seq"]:
            # The exact typed row is only needed at the bridge API boundary.
            observation = BodyObservation.model_validate(raw)
        if self.lease is None:
            if self.clock() - self._last_acquire < ACQUIRE_INTERVAL:
                return
            self._last_acquire = self.clock()
            try:
                # NPC_GETTARGET sets OTHER to the actual current target; story_eval
                # restores SELF/OTHER afterward. Acquire independently checks that
                # the observation's numeric target still matches this live target.
                hero = await self.link.story_eval(self.npc, [TARGET_IS_HERO], timeout=BRIDGE_TIMEOUT)
                if hero != [1]:
                    self._refusals["target_not_hero"] += 1
                    return
                if self._reason or self._closing or not self._allowed(self.npc):
                    return
                if self.clock() - received_at > 0.100 or self.clock() - self._attempt_at >= ACQUIRE_WINDOW:
                    return
                self._acquiring = True
                self._pending_acquire = observation
                try:
                    self.lease = await self.link.body_acquire(
                        self.npc,
                        observation,
                        "neural-body",
                        mode="realtime",
                        timeout_ms=LEASE_TIMEOUT_MS,
                        timeout=BRIDGE_TIMEOUT,
                    )
                except (TimeoutError, OSError, ConnectionClosed):
                    # The bridge may own the body even though no receipt arrived.
                    self._reason = self._reason or "acquire_uncertain"
                    raise
                except GameError:
                    self._pending_acquire = None
                    raise
                else:
                    self._pending_acquire = None
                finally:
                    self._acquiring = False
            except (GameDisconnected, ConnectionClosed):
                self._reason = "disconnected"
            except BRIDGE_ERRORS as error:
                self._refusals[str(error)] += 1
            else:
                self._claimed_fights.add(self.npc)
                self._entity = self.lease.entity_id
                self._hero = self.lease.target_entity_id
                self._fight_at = self.clock()
                self._last_events_poll = float("-inf")
                self._heartbeat_task = asyncio.create_task(self._heartbeats(self.lease))
                log.info(
                    "neural-body start npc=%s lease=%s acquire_refusals=%s",
                    self.npc,
                    self._identity(self.lease),
                    dict(self._refusals),
                )
            return
        if raw["observation_seq"] <= self.lease.acquired_observation_seq:
            return
        try:
            if self.client is None:
                self.client = PolicyClient(self.url)
                await self.client.connect()
                self._initial_game_ms = raw["t_game_ms"]
                await self.client.call(
                    "reset",
                    session_epoch=self.lease.session_epoch,
                    world_epoch=self.lease.world_epoch,
                    initial_game_ms=self._initial_game_ms,
                )
            started = time.monotonic()
            try:
                result = await self.client.call("step", observation=raw, now_game_ms=self._now_game_ms)
            finally:
                self._latencies.append((time.monotonic() - started) * 1000)
            if result.get("status") == "fallback":
                self._fallbacks += 1
                self._reason = f"policy_fallback:{result.get('reason')}"
                return
            if self._closing or self._reason or not self._allowed(self.npc):
                self._reason = self._reason or "npc_unavailable"
                return
            if self.link.body_leases.get(self.lease.entity_id) != self.lease:
                self._reason = "lease_lost"
                return
            if result.get("status") == "decision" and raw["can_decide"] and raw["action_mask_valid"]:
                action = result.get("action_id")
                if type(action) is not int or not 0 <= action <= 11 or not raw["action_mask"][action]:
                    raise PolicyError("invalid policy action")
                latest = self.link.body_latest.get(self.lease.entity_id)
                if latest is None or latest.observation_seq != observation.observation_seq:
                    return
                await self.link.body_action(
                    self.lease, observation, action, ttl_ms=100, timeout=BRIDGE_TIMEOUT
                )
                self._decisions += 1
        except TimeoutError:
            self._reason = "policy_timeout"
        except (OSError, ValueError, PolicyError):
            self._reason = "policy_error"
        except BRIDGE_ERRORS:
            self._reason = "body_action_rejected"

    async def _recover_acquire(self) -> bool:
        """Resolve an uncertain acquire before releasing or changing the watch."""
        pending = self._pending_acquire
        assert pending is not None and self.npc is not None
        scope = pending.session_epoch, pending.world_epoch
        while not self.link.closed.is_set():
            if (self.link.body_session_epoch, self.link.body_generation) != scope:
                self._pending_acquire = None
                return True
            try:
                status = await self.link.body_control_status(timeout=BRIDGE_TIMEOUT)
            except BRIDGE_ERRORS as error:
                if "unknown_entity" in str(error):
                    self._pending_acquire = None
                    return True
            else:
                if self.link.closed.is_set() or (
                    self.link.body_session_epoch, self.link.body_generation
                ) != scope:
                    self._pending_acquire = None
                    return True
                if (status.session_epoch, status.world_epoch) == scope:
                    actor = next((row for row in status.actors if row.entity_id == pending.entity_id), None)
                    if actor is None or actor.owner == "native":
                        self._pending_acquire = None
                        return True
                    if actor.provider == "neural-body" and actor.mode == "realtime":
                        self.lease = BodyLease(
                            npc=self.npc, provider=actor.provider, mode=actor.mode,
                            session_epoch=actor.session_epoch, world_epoch=actor.world_epoch,
                            entity_id=actor.entity_id, control_generation=actor.control_generation,
                            goal_version=actor.goal_version, target_entity_id=actor.target_entity_id,
                            acquired_observation_seq=pending.observation_seq,
                        )
                        self.link.body_leases[self.lease.entity_id] = self.lease
                        self._claimed_fights.add(self.npc)
                        self._pending_acquire = None
                        return True
            await asyncio.sleep(0.025)
        self._pending_acquire = None
        return True

    async def _finish_status(self, lease: BodyLease) -> bool:
        try:
            status = await self.link.body_control_status(timeout=BRIDGE_TIMEOUT)
        except GameError as error:
            if "unknown_entity" not in str(error):
                raise
        else:
            if (status.session_epoch, status.world_epoch) != (lease.session_epoch, lease.world_epoch):
                return False
            if any(actor.entity_id == lease.entity_id for actor in status.actors):
                return self.link.body_leases.get(lease.entity_id) != lease
        # Removed actors cannot send native_restored or appear in a status row.
        if self.link.body_leases.get(lease.entity_id) == lease:
            self.link.body_leases.pop(lease.entity_id)
        return True

    async def _finish(self, reason: str) -> None:
        npc, client = self.npc, self.client
        if npc is None and self.lease is None and client is None:
            return
        self._watch_ready = False
        unconfirmed = "acquire_status" if self._pending_acquire is not None else "body_release"
        lease = self.lease
        heartbeat, self._heartbeat_task = self._heartbeat_task, None
        try:
            # One wall-clock budget covers every route into ownership cleanup.
            async with asyncio.timeout(FINISH_TIMEOUT):
                if heartbeat is not None:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                if self._pending_acquire is not None:
                    await self._recover_acquire()
                lease = self.lease
                if lease is not None:
                    try:
                        await self.link.body_release(lease, timeout=BRIDGE_TIMEOUT)
                    except BRIDGE_ERRORS as error:
                        unconfirmed = f"body_release:{error}"
                while lease is not None and self.link.body_leases.get(lease.entity_id) == lease:
                    if self.link.closed.is_set():
                        break
                    try:
                        await self.link.body_events(timeout=BRIDGE_TIMEOUT)
                    except BRIDGE_ERRORS:
                        pass
                    if self.link.body_leases.get(lease.entity_id) != lease:
                        break
                    try:
                        if await self._finish_status(lease):
                            break
                        unconfirmed = "native_restored"
                    except BRIDGE_ERRORS as error:
                        unconfirmed = f"body_control_status:{error}"
                    await asyncio.sleep(0.025)
        except TimeoutError:
            reason = "finish_deadline"
            # Make a final authoritative check before discarding local ownership.
            lease = self.lease
            if lease is not None:
                with contextlib.suppress(*BRIDGE_ERRORS):
                    async with asyncio.timeout(BRIDGE_TIMEOUT):
                        await self._finish_status(lease)
        finally:
            if reason == "finish_deadline" and lease is not None:
                if self.link.body_leases.get(lease.entity_id) == lease:
                    self.link.body_leases.pop(lease.entity_id)
            self._pending_acquire = None
            if client is not None:
                try:
                    if lease is not None:
                        with contextlib.suppress(OSError, ValueError, PolicyError, TimeoutError):
                            await client.call(
                                "end_episode",
                                session_epoch=lease.session_epoch,
                                world_epoch=lease.world_epoch,
                            )
                    with contextlib.suppress(OSError, ValueError, PolicyError, TimeoutError):
                        await client.call("close")
                finally:
                    with contextlib.suppress(OSError, TimeoutError):
                        async with asyncio.timeout(SERVICE_TIMEOUT):
                            await client.close()
            if self._watched and not self.link.body_leases:
                with contextlib.suppress(*BRIDGE_ERRORS):
                    async with asyncio.timeout(BRIDGE_TIMEOUT):
                        await self.link.body_watch([], timeout=BRIDGE_TIMEOUT)
        log.info(
            "neural-body end npc=%s reason=%s decisions=%d fallback=%d service_p50_ms=%s "
            "service_p95_ms=%s acquire_refusals=%s unconfirmed=%s",
            npc,
            reason,
            self._decisions,
            self._fallbacks,
            _percentile(self._latencies, 0.50),
            _percentile(self._latencies, 0.95),
            dict(self._refusals),
            unconfirmed if reason == "finish_deadline" else None,
        )
        self.npc = self.lease = self.client = None
        self._watched = self._watch_ready = False
        self._reason = None
        self._rows.clear()
        self._last_seq = self._now_game_ms = 0
        self._last_acquire = float("-inf")
        self._initial_game_ms = None
        self._symbol = self._entity = self._hero = None
        self._refusals.clear()
        self._latencies.clear()
        self._decisions = self._fallbacks = 0
