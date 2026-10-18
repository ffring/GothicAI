"""Exercise the live pilot through the bridge adapter and a torch-free TCP policy."""

import asyncio
import json
import socket
from collections import Counter, deque
from contextlib import asynccontextmanager
from dataclasses import FrozenInstanceError
from itertools import pairwise

import pytest
from websockets.exceptions import ConnectionClosedError

from gothic_ai import bodypilot
from gothic_ai.bodypilot import BodyPilot
from gothic_ai.game import GameError, GameLink
from gothic_ai.protocol import (
    Attached,
    Hello,
    Loading,
    NpcEvent,
    NpcRoster,
    Result,
    RosterEntry,
)
from tests.test_body_control import controlled, event, events, receipt, visibility_diagnostics
from tests.test_body_observations import message, observation, packet


async def eventually(predicate, timeout=1.0):
    async with asyncio.timeout(timeout):
        while not predicate():  # noqa: ASYNC110 - observe a fake bridge or TCP peer without coupling it.
            await asyncio.sleep(0.001)


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Bridge:
    """Fake native requests while retaining GameLink's lease and message checks."""

    def __init__(self):
        self.sent = []
        self.sent_times = []
        self.link = GameLink(self)
        self.acquire_rejections = deque()
        self.acquire_pending_results = deque()
        self.acquire_pending_delay = 0.020
        self.pending_acquire = None
        self._acquire_request_id = 0
        self._pending_event_seq = 0
        self.action_rejections = deque()
        self.release_rejections = deque()
        self.status_error = None
        self.acquire_roster_delay = 0
        self.target_is_hero = True
        self.target_npc = None
        self.release_pending_polls = 0
        self._released_pending = False
        self.request_rejections = {}
        self.response_delays = {}
        self._response_tasks = set()
        self._roster = {}
        self._watched_npc = None
        self._lease_timeout = 0.0
        self._lease_watchdog = None
        self.lease_expirations = 0
        self.provider_owned = False
        self._control_scope = None
        self._control_npc = None
        self._control_target = 2
        self.control_events = deque()

    def commands(self, kind):
        return [command for command in self.sent if command["type"] == kind]

    def command_times(self, kind):
        return [when for command, when in self.sent_times if command["type"] == kind]

    def roster(self, *entries):
        self._roster = {entry.id.upper(): entry for entry in entries}
        self.link._dispatch(NpcRoster(type="npc_roster", t=1, npcs=entries))

    def _activity(self, npc, activity):
        if npc in self._roster:
            self._roster[npc] = self._roster[npc].model_copy(update={"activity": activity})
            # Validate the same activity value that the native bridge puts on the wire.
            self.link._dispatch(NpcRoster.model_validate({
                "type": "npc_roster", "t": 1,
                "npcs": [entry.model_dump(mode="json") for entry in self._roster.values()],
            }))

    def _background(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._response_tasks.add(task)
        task.add_done_callback(self._response_tasks.discard)

    async def _acquired_roster(self, npc):
        # Native ownership is independent of whether the acquire reply arrives.
        await asyncio.sleep(self.acquire_roster_delay)
        if self.provider_owned:
            self._activity(npc, "body")

    def _renew_lease(self):
        if self._lease_watchdog is not None:
            self._lease_watchdog.cancel()
        self._lease_watchdog = asyncio.get_running_loop().call_later(
            self._lease_timeout, self._expire_lease,
        )

    def _grant(self, command):
        self.provider_owned = True
        self._control_scope = {name: command[name] for name in (
            "entity_id", "session_epoch", "world_epoch",
        )}
        self._control_npc = self._watched_npc
        self._control_target = command["target_entity_id"]
        self._acquire_request_id = command.get("acquire_request_id", 0)
        self._lease_timeout = command["timeout_ms"] / 1000
        self._renew_lease()
        self._background(self._acquired_roster(self._watched_npc))

    async def _resolve_pending(self, command, outcome):
        await asyncio.sleep(self.acquire_pending_delay)
        if self.pending_acquire is not command:
            return
        self.pending_acquire = None
        self._pending_event_seq += 1
        if outcome == "acquired":
            self._grant(command)
            self.control_events.append(event(
                self._pending_event_seq, **self._control_scope, provider=command["provider"],
                acquire_request_id=command["acquire_request_id"], observation_seq=3,
            ))
        else:
            self.control_events.append(event(
                self._pending_event_seq, entity_id=command["entity_id"], provider=command["provider"],
                acquire_request_id=command["acquire_request_id"], reason="acquire_cancelled",
                acquire_cancel_reason=outcome, owner="native", control_generation=0, goal_version=0,
            ))

    def _expire_lease(self):
        self.lease_expirations += 1
        if self.provider_owned:
            self.provider_owned = False
            self.link._dispatch(events(event(
                1, **self._control_scope, reason="native_restored", owner="native",
            )))
            self._activity(self._control_npc, "fight")

    def restore_native(self, *, release_reason=""):
        """Restore the native actor, leaving ownership events for the next destructive poll."""
        lease = next(iter(self.link.body_leases.values()))
        self.provider_owned = False
        if self._lease_watchdog is not None:
            self._lease_watchdog.cancel()
        self.control_events.append(event(
            1, entity_id=lease.entity_id, reason="native_restored", owner="native",
            target_entity_id=lease.target_entity_id, release_reason=release_reason,
        ))
        self._activity(lease.npc, "fight")

    async def close(self):
        if self._lease_watchdog is not None:
            self._lease_watchdog.cancel()
        for task in list(self._response_tasks):
            task.cancel()
        await asyncio.gather(*self._response_tasks, return_exceptions=True)

    async def send(self, raw):
        command = json.loads(raw)
        self.sent.append(command)
        self.sent_times.append((command, asyncio.get_running_loop().time()))
        kind = command["type"]
        if rejections := self.request_rejections.get(kind):
            self.link._dispatch(Result(
                type="result", req=command["req"], ok=False, error=rejections.popleft(),
            ))
            return
        if kind == "body_watch":
            assert not self.link.body_leases, "watch must never change with an active lease"
            if self.provider_owned:
                self.link._dispatch(Result(
                    type="result", req=command["req"], ok=False,
                    error="cannot change watch while a lease is active",
                ))
                return
            self._watched_npc = command["npcs"][0] if command["npcs"] else None
            value = {"generation": 1, "session_epoch": 1, "world_epoch": 1}
        elif kind == "story_eval":
            expressions = command["exprs"]
            value = []
            for expr in expressions:
                if expr[0] == "sym":
                    value.append(50)
                elif expr == bodypilot.TARGET_IS_HERO:
                    value.append(int(self.target_is_hero))
                else:
                    npc = expr[2][2][2][1]
                    assert expr == [
                        "&&", ["call", "NPC_GETTARGET", ["inst", "SELF"]],
                        ["==", ["call", "HLP_GETINSTANCEID", ["inst", "OTHER"]],
                         ["call", "HLP_GETINSTANCEID", ["inst", npc]]],
                    ]
                    value.append(int(not self.target_is_hero and self.target_npc == npc))
        elif kind == "body_events":
            if self.control_events:
                value = events(*self.control_events).model_dump(mode="json")
                self.control_events.clear()
            elif self._released_pending and self.release_pending_polls <= 0:
                lease = next(iter(self.link.body_leases.values()))
                value = events(event(
                    1, entity_id=lease.entity_id, reason="native_restored", owner="native",
                )).model_dump(mode="json")
                self._released_pending = False
                self.provider_owned = False
                self._activity(lease.npc, "fight")
            else:
                value = events().model_dump(mode="json")
                if self._released_pending:
                    self.release_pending_polls -= 1
        elif kind == "body_control_status":
            if self.status_error is not None:
                self.link._dispatch(Result(
                    type="result", req=command["req"], ok=False, error=self.status_error,
                ))
                return
            actors = []
            if self._control_scope is not None:
                row = {
                    **self._control_scope,
                    "control_generation": 7 if self.provider_owned else 0,
                    "acquire_request_id": (
                        self.pending_acquire["acquire_request_id"] if self.pending_acquire
                        else self._acquire_request_id if self.provider_owned else 0
                    ),
                    "acquire_pending": self.pending_acquire is not None,
                    "goal_version": 1 if self.provider_owned else 0,
                    "target_entity_id": self._control_target, "command_id": 0, "execution_id": 0,
                    "owner": "scripted" if self.provider_owned else "native",
                    "release_pending": self._released_pending,
                    "provider": "neural-body" if self.provider_owned else "",
                    "mode": "realtime" if self.provider_owned else "native",
                    "t_game_ms": 200, "observation_seq": 4, "observed_game_ms": 200,
                    "watchdog_timeout_ms": int(self._lease_timeout * 1000) if self.provider_owned else 0,
                    "control_allowed": False, "can_decide": False, "action_mask": [False] * 12,
                }
                actors.append(row)
            value = {
                "type": "body_control_status", "session_epoch": 1, "world_epoch": 1,
                "paused": False, "infrastructure_failure": False, "infrastructure_reason": "",
                "visibility_diagnostics": visibility_diagnostics(),
                "actors": actors,
            }
        else:
            value = receipt(
                entity_id=command.get("entity_id", 1),
                session_epoch=command.get("session_epoch", 1),
                world_epoch=command.get("world_epoch", 1),
                control_generation=command.get("control_generation", 7),
                goal_version=command.get("goal_version", 1),
                target_entity_id=command.get("target_entity_id", 2),
                command_id=command.get("command_id", 0),
                acquire_request_id=command.get("acquire_request_id", 0),
                observation_seq=self.link.body_latest[command.get("entity_id", 1)].observation_seq
                if command.get("entity_id", 1) in self.link.body_latest else 1,
            )
            if kind == "body_acquire" and self.acquire_rejections:
                value.update(accepted=False, reason=self.acquire_rejections.popleft())
            if kind == "body_action" and self.action_rejections:
                value.update(accepted=False, reason=self.action_rejections.popleft())
            if kind == "body_acquire" and value["accepted"]:
                if self.acquire_pending_results:
                    assert command["await_boundary_ms"] > 0
                    self.pending_acquire = command
                    self._control_scope = {name: command[name] for name in (
                        "entity_id", "session_epoch", "world_epoch",
                    )}
                    value.update(reason="acquire_pending", owner="native", control_generation=0)
                    self._background(self._resolve_pending(command, self.acquire_pending_results.popleft()))
                else:
                    self._grant(command)
            elif kind == "body_heartbeat":
                if self.provider_owned:
                    self._renew_lease()
                else:
                    value.update(accepted=False, reason="not_owned", owner="native", control_generation=0)
            if kind == "body_release":
                if self._lease_watchdog is not None:
                    self._lease_watchdog.cancel()
                if command.get("acquire_request_id") and self.pending_acquire is not None:
                    self.pending_acquire = None
                    value.update(owner="native", control_generation=0, reason="acquire_cancelled",
                                 acquire_cancel_reason="released")
                elif self.release_rejections:
                    value.update(accepted=False, reason=self.release_rejections.popleft())
                elif not self.provider_owned:
                    value.update(accepted=False, reason="not_owned", owner="native", control_generation=0)
                elif self.release_pending_polls:
                    self._released_pending = True
                    value.update(owner="scripted", reason="release_pending", release_pending=True)
                else:
                    self.provider_owned = False
                    value.update(owner="native", reason="native_restored")
                    self._activity(self._control_npc, "fight")
        result = Result(type="result", req=command["req"], ok=True, value=value)
        delay = self.response_delays.get(kind, 0)
        if delay:
            async def reply():
                await asyncio.sleep(delay)
                self.link._dispatch(result)

            # Replies can arrive later while the socket remains writable.
            self._background(reply())
        else:
            self.link._dispatch(result)

    def emit(self, *rows, generation=1):
        raw = packet(*rows, generation=generation)
        self.link._dispatch(message(raw), raw_body_packet=raw)


class Policy:
    """The real JSONL wire protocol, without importing the model package."""

    def __init__(self):
        self.requests = []
        self.writers = set()
        self.tasks = set()
        self.step_mode = "normal"
        self.reset_mode = "normal"
        self.reset_waiter = None
        self.step_waiter = None
        self.step_delay = 0
        self.step_delays = deque()
        self.step_response_actions = deque()
        self.on_step = None

    def commands(self, kind):
        return [request for request in self.requests if request["cmd"] == kind]

    async def handle(self, reader, writer):
        self.writers.add(writer)
        self.tasks.add(asyncio.current_task())
        try:
            while line := await reader.readline():
                request = json.loads(line)
                self.requests.append(request)
                command = request["cmd"]
                result = {"id": request["id"], "schema_version": 1, "ok": True}
                if command == "reset":
                    if self.reset_waiter is not None:
                        await self.reset_waiter.wait()
                    if self.reset_mode == "error":
                        result.update(ok=False, reason="reset_error", error="fake reset failure")
                    elif self.reset_mode == "timeout":
                        continue
                    else:
                        result.update(reason="episode_reset", hidden_reset=True)
                elif command == "step":
                    if self.on_step is not None:
                        self.on_step(request)
                    delay = self.step_delays.popleft() if self.step_delays else self.step_delay
                    if delay:
                        await asyncio.sleep(delay)
                    if self.step_waiter is not None:
                        await self.step_waiter.wait()
                    if self.step_mode == "disconnect":
                        return
                    if self.step_mode == "timeout":
                        continue
                    if self.step_mode == "error":
                        result.update(ok=False, reason="inference_error", error="fake policy failure")
                    elif self.step_mode == "fallback":
                        result.update(status="fallback", action_id=None, reason="fake_fallback")
                    else:
                        row = request["observation"]
                        eligible = row["can_decide"] and row["action_mask_valid"]
                        action = self.step_response_actions.popleft() if self.step_response_actions else 1
                        result.update(
                            status="decision" if eligible else "locked",
                            action_id=action if eligible else None,
                            reason="accepted" if eligible else "memory_only",
                        )
                elif command == "end_episode":
                    result.update(accepted=True, reason="episode_ended")
                elif command == "close":
                    result.update(reason="closed")
                else:
                    raise AssertionError(f"unexpected policy command: {command}")
                writer.write(json.dumps(result).encode() + b"\n")
                await writer.drain()
                if command == "close":
                    return
        finally:
            self.writers.discard(writer)
            self.tasks.discard(asyncio.current_task())
            writer.close()
            await writer.wait_closed()

    async def close(self):
        for writer in list(self.writers):
            writer.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def fighter(seq=2, *, owned=False, entity=1, target_entity=2, **changes):
    row = controlled(seq) if owned else observation(seq, entity=entity)
    if owned:
        row["action_mask"] = [False, True] + [False] * 10
    row["entity_id"] = entity
    row["self"]["weapon_state"] = 2  # WeaponState::W1H; 3 is two-handed.
    row["target"].update(
        entity_id=target_entity, present=True, visible=True, current_valid=True,
        position_world_cm=[100.0, 0.0, 450.0],
        relative_position_cm=[0.0, 0.0, 150.0],
        hp=300, hp_max=300, hp_fraction=1.0, down=False,
    )
    row["goal"]["target_id"] = target_entity
    row.update(changes)
    return row


def unseen_fighter(seq, *, present=True, target_entity=2, **changes):
    row = fighter(seq, owned=True, can_decide=False, action_mask_valid=False, **changes)
    row["action_mask"] = [False] * 12
    target = row["target"]
    target.update(entity_id=target_entity, present=present, visible=False, current_valid=False)
    allowed = {
        "entity_id", "present", "visible", "current_valid", "last_known_position_world_cm",
        "last_known_velocity_cm_s", "last_seen_game_ms", "last_known_age_ms",
    }
    for key in target:
        if key not in allowed:
            target[key] = None
    return row


def player_row(seq=1):
    row = observation(seq, entity=2)
    row["script_symbol"] = 99
    row["goal"]["kind"] = "native_idle"
    row["goal"]["target_id"] = 0
    row["target"]["entity_id"] = 0
    row["target"]["present"] = False
    return row


def companion_entry():
    return RosterEntry(
        id="CAVALORN", name="Кавалорн", guild=1, distance=150.0,
        in_view=True, activity="fight", state="ZS_ATTACK",
    )


class Rig:
    def __init__(self, bridge, policy, pilot, clock):
        self.bridge, self.policy, self.pilot, self.clock = bridge, policy, pilot, clock
        self.link = bridge.link

    def roster(self, *, activity=None, state=None, guild=1, include=True, extra=()):
        if activity is None:
            activity = "body" if self.link.body_owned("NPC") else "fight"
        npcs = [RosterEntry(
            id="NPC", name="Fighter", guild=guild, distance=200.0,
            in_view=True, activity=activity,
            state=state if state is not None else (
                "ZS_ATTACK" if activity in {"body", "fight"} else "ZS_SIT_BENCH"
            ),
        )] if include else []
        self.bridge.roster(*npcs, *extra)

    def combat(self, npc="NPC", kind="attack", other="PLAYER"):
        self.link._dispatch(NpcEvent(type="npc_event", npc=npc, kind=kind, other=other, distance=200.0))

    async def watch(self, **roster):
        self.roster(**roster)
        self.combat()
        await eventually(lambda: self.bridge.commands("body_watch"))

    async def acquire(self, *, entity=1, target_entity=2, extra=()):
        await self.watch(extra=extra)
        self.bridge.emit(player_row(), fighter(entity=entity, target_entity=target_entity))
        await eventually(lambda: self.link.body_leases)

    async def active(self, *, entity=1, target_entity=2, extra=()):
        await self.acquire(entity=entity, target_entity=target_entity, extra=extra)
        self.bridge.emit(fighter(3, owned=True, entity=entity, target_entity=target_entity))
        await eventually(lambda: self.policy.commands("step"))
        await eventually(lambda: self.bridge.commands("body_action"))

    async def ended(self, *, policy=True):
        await eventually(lambda: self.bridge.commands("body_release"))
        await eventually(lambda: any(not cmd["npcs"] for cmd in self.bridge.commands("body_watch")))
        if policy:
            await eventually(lambda: self.policy.commands("end_episode"))
        assert not self.link.body_leases

    async def retarget(self, seq, *, target_entity, target_npc=None):
        releases = len(self.bridge.commands("body_release"))
        episodes = len(self.policy.commands("end_episode"))
        self.bridge.target_is_hero = target_npc is None
        self.bridge.target_npc = target_npc
        self.bridge.emit(fighter(seq, owned=True, target_entity=target_entity))
        await eventually(lambda: len(self.bridge.commands("body_release")) == releases + 1)
        await eventually(lambda: len(self.policy.commands("end_episode")) == episodes + 1)
        await eventually(lambda: self.pilot.npc is None)
        assert not self.link.body_leases
        assert self.pilot.last_summary.reason == "target_changed"

    async def reacquire(self, seq, *, target_entity, count):
        await eventually(lambda: len(self.bridge.commands("body_watch")) == 2 * count - 1)
        self.bridge.emit(fighter(seq, target_entity=target_entity))
        await eventually(lambda: len(self.bridge.commands("body_acquire")) == count)
        await eventually(lambda: len(self.policy.commands("reset")) == count)
        assert self.pilot.lease.target_entity_id == target_entity

    async def lose_heartbeat_before_native_event(self, *, target_entity, evidence="matching", down=False):
        original_heartbeat = self.link.body_heartbeat
        heartbeat_errors = []

        async def record_heartbeat(*args, **kwargs):
            try:
                return await original_heartbeat(*args, **kwargs)
            except GameError as error:
                heartbeat_errors.append(str(error))
                raise

        self.link.body_heartbeat = record_heartbeat
        # Keep the ordinary event poll out of this handoff. The next heartbeat
        # discovers native ownership; cleanup must then consume its ownership event.
        self.pilot._last_events_poll = float("inf")
        first_command = len(self.bridge.sent)
        self.bridge.restore_native(release_reason="target_changed_or_lost")
        if evidence == "missing":
            self.bridge.control_events.clear()
        elif evidence == "foreign":
            self.bridge.control_events[0]["control_generation"] += 1
        row = fighter(4, target_entity=target_entity, periodic=False, event_only=True)
        row["target"]["down"] = down
        self.bridge.emit(row)
        await eventually(lambda: self.pilot.last_summary is not None)
        assert heartbeat_errors == ["body command rejected: not_owned"]
        commands = [command["type"] for command in self.bridge.sent[first_command:]]
        assert (
            commands.index("body_heartbeat") < commands.index("body_release") < commands.index("body_events")
        )
        return self.pilot.last_summary


@asynccontextmanager
async def running(*, companion=False, unavailable=False):
    bridge, policy, clock = Bridge(), Policy(), Clock()
    server = await asyncio.start_server(policy.handle, host="localhost", port=0, family=socket.AF_INET)
    port = server.sockets[0].getsockname()[1]
    pilot = BodyPilot(
        bridge.link, f"tcp://localhost:{port}",
        companion=companion if callable(companion) else lambda _: companion,
        unavailable=lambda _: unavailable, clock=clock,
    )
    task = asyncio.create_task(pilot.run())
    try:
        yield Rig(bridge, policy, pilot, clock)
    finally:
        await pilot.aclose()
        await asyncio.wait_for(task, 1.0)
        server.close()
        await server.wait_closed()
        await policy.close()
        await bridge.close()


async def test_acquire_immediately_resets_before_first_owned_row_and_exact_wire_actions():
    async with running() as rig:
        await rig.acquire()
        acquire = rig.bridge.commands("body_acquire")[0]
        assert acquire["mode"] == "realtime" and acquire["provider"] == "neural-body"
        assert acquire["target_entity_id"] == 2
        await eventually(lambda: rig.policy.commands("reset"))
        assert not rig.policy.commands("step")
        own = fighter(3, owned=True)
        own["snapshot_wall_monotonic_ns"] = 123456789
        own["clock_domain"] = "cxx-steady-clock"
        own["self"]["position_world_cm"] = [101.25, 0.0, 302.5]
        rig.bridge.emit(own)
        await eventually(lambda: rig.bridge.commands("body_action"))
        assert [request["cmd"] for request in rig.policy.requests[:2]] == ["reset", "step"]
        reset, step = rig.policy.requests[:2]
        assert (reset["session_epoch"], reset["world_epoch"], reset["initial_game_ms"]) == (1, 1, 100)
        assert step["observation"] == own
        assert step["now_game_ms"] == 150
        command = rig.bridge.commands("body_action")[0]
        assert {key: command[key] for key in (
            "goal_version", "target_entity_id", "observation_seq", "issued_game_ms", "ttl_ms", "action_id",
        )} == {
            "goal_version": own["goal"]["version"], "target_entity_id": own["target"]["entity_id"],
            "observation_seq": own["observation_seq"], "issued_game_ms": own["t_game_ms"],
            "ttl_ms": 100, "action_id": 1,
        }


async def test_pre_acquire_snapshot_delivered_after_acquire_keeps_lease_and_waits_for_owned_row():
    async with running() as rig:
        await rig.acquire()
        await eventually(lambda: rig.policy.commands("reset"))
        lease = rig.link.body_leases[1]
        # The bridge replies to acquire before flushing observations from the previous tick.
        passive = fighter(3)
        assert passive["control_generation"] == 0
        assert passive["observation_seq"] > lease.acquired_observation_seq
        rig.bridge.emit(passive)
        await eventually(lambda: not rig.pilot._rows)
        assert rig.link.body_leases == {1: lease}
        assert rig.pilot.lease == lease
        assert not rig.policy.commands("step")
        assert not rig.bridge.commands("body_release")

        own = fighter(4, owned=True)
        rig.bridge.emit(own)
        await eventually(lambda: rig.bridge.commands("body_action"))
        assert [request["cmd"] for request in rig.policy.requests] == ["reset", "step"]
        assert rig.policy.commands("reset")[0]["initial_game_ms"] == fighter()["t_game_ms"]
        assert rig.policy.commands("step")[0]["observation"] == own
        assert rig.bridge.commands("body_action")[0]["observation_seq"] == 4
        assert rig.link.body_leases == {1: lease}
        assert not rig.bridge.commands("body_release")


@pytest.mark.parametrize("shutdown", [False, True])
async def test_lost_acquire_reply_reconciles_native_lease_before_clearing_watch(shutdown):
    async with running() as rig:
        rig.bridge.response_delays["body_acquire"] = 0.350
        rig.bridge.acquire_roster_delay = 0.500
        await rig.watch()
        await eventually(lambda: rig.pilot._watch_ready)
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.bridge.provider_owned)
        # The native actor has a lease before its result or body roster arrives.
        await asyncio.sleep(0.050)
        rig.bridge.emit(fighter(3, owned=True))
        assert not rig.link.body_leases
        if shutdown:
            await asyncio.wait_for(rig.pilot.aclose(), 1.0)
        await eventually(lambda: rig.bridge.commands("body_release"))
        await eventually(lambda: any(not cmd["npcs"] for cmd in rig.bridge.commands("body_watch")))
        await eventually(lambda: rig.pilot.npc is None)
        # Drain the delayed reply and roster; neither may revive the discarded lease.
        await eventually(lambda: not rig.bridge._response_tasks)
        assert not rig.bridge.provider_owned
        assert not rig.link.body_leases
        assert rig.bridge._watched_npc is None
        assert rig.bridge.lease_expirations == 0
        assert len(rig.bridge.commands("body_acquire")) == 1
        assert not rig.bridge.commands("body_heartbeat")
        assert not rig.policy.requests
        release = rig.bridge.commands("body_release")[0]
        assert {key: release[key] for key in (
            "session_epoch", "world_epoch", "entity_id", "control_generation",
        )} == {
            "session_epoch": 1, "world_epoch": 1, "entity_id": 1, "control_generation": 0,
        }
        assert release["acquire_request_id"] == rig.bridge.commands("body_acquire")[0]["acquire_request_id"]
        release_index = rig.bridge.sent.index(release)
        unwatch_index = next(i for i, cmd in enumerate(rig.bridge.sent)
                             if cmd["type"] == "body_watch" and not cmd["npcs"])
        assert release_index < unwatch_index
        assert rig.bridge.commands("body_control_status")

        if not shutdown:
            # Cleanup leaves the same pilot available for a later fight.
            rig.bridge.response_delays.clear()
            rig.bridge.acquire_roster_delay = 0
            rig.roster(activity="routine", state="ZS_SIT_BENCH")
            await rig.watch()
            await eventually(lambda: rig.pilot._watch_ready)
            rig.bridge.emit(player_row(4), fighter(5))
            await eventually(lambda: rig.link.body_leases)
            rig.bridge.emit(fighter(6, owned=True))
            await eventually(lambda: rig.bridge.commands("body_action"))
            assert len(rig.bridge.commands("body_acquire")) == 2
            assert len(rig.policy.commands("reset")) == 1


@pytest.mark.parametrize("player_near", [False, True])
async def test_native_attack_roster_starts_episode_without_hero_combat_events(player_near):
    async with running() as rig:
        rig.roster(activity="fight", state="ZS_ATTACK")
        if player_near:
            rig.combat(kind="player_near")
        await eventually(lambda: rig.bridge.commands("body_watch"))
        assert rig.bridge.commands("body_watch")[0]["npcs"] == ["NPC"]
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.link.body_leases)
        assert len(rig.bridge.commands("body_acquire")) == 1
        await eventually(lambda: rig.policy.commands("reset"))
        assert not rig.policy.commands("step")
        own = fighter(3, owned=True)
        rig.bridge.emit(own)
        await eventually(lambda: rig.bridge.commands("body_action"))
        assert [request["cmd"] for request in rig.policy.requests[:2]] == ["reset", "step"]
        assert rig.policy.commands("step")[0]["observation"] == own


async def test_fight_activity_without_native_attack_state_does_not_start_watch():
    async with running() as rig:
        rig.roster(activity="fight", state="ZS_WAIT")
        rig.combat(kind="player_near")
        rig.bridge.emit(player_row(), fighter())
        await asyncio.sleep(0.04)
        assert not rig.bridge.commands("body_watch")
        assert not rig.bridge.commands("body_acquire")
        assert not rig.policy.requests


async def test_native_attack_roster_rejects_live_target_outside_hero_and_companions():
    async with running(companion=lambda npc: npc == "CAVALORN") as rig:
        rig.bridge.target_is_hero = False
        rig.bridge.target_npc = "OTHER"
        rig.roster(activity="fight", state="ZS_ATTACK", extra=[companion_entry()])
        await eventually(lambda: rig.bridge.commands("body_watch"))
        own = fighter()
        own["target"]["entity_id"] = 8
        own["goal"]["target_id"] = 8
        rig.bridge.emit(player_row(), own)
        await eventually(lambda: len(rig.bridge.commands("story_eval")) == 2)
        await eventually(lambda: rig.pilot._refusals["target_not_allowed"] == 1)
        assert rig.pilot._refusals == {"target_not_allowed": 1}
        assert not rig.bridge.commands("body_acquire")
        assert not rig.policy.requests


@pytest.mark.parametrize("membership", ["orders", "party"])
async def test_live_companion_target_is_acquired_and_named_in_summary(membership):
    async with running(companion=lambda npc: membership == "orders" and npc == "CAVALORN") as rig:
        if membership == "party":
            rig.link.party["CAVALORN"] = (0, 1)
        rig.bridge.target_is_hero = False
        rig.bridge.target_npc = "CAVALORN"
        await rig.active(target_entity=3, extra=[companion_entry()])
        acquire = rig.bridge.commands("body_acquire")[0]
        assert acquire["target_entity_id"] == 3
        assert rig.pilot.lease.target_entity_id == 3
        assert len(rig.policy.commands("reset")) == 1
        assert rig.policy.commands("step")[0]["observation"]["target"]["entity_id"] == 3
        assert rig.bridge.commands("body_action")[0]["target_entity_id"] == 3

        row = fighter(4, owned=True, target_entity=3)
        row["self"]["down"] = True
        rig.bridge.emit(row)
        await rig.ended()
        summary = rig.pilot.last_summary
        assert summary.reason == "npc_down"
        assert summary.target_kind == "companion" and summary.target_name == "Кавалорн"
        assert summary.release_confirmed and summary.watch_removed


async def test_companion_membership_is_rechecked_after_live_target_query():
    companions = {"CAVALORN"}
    async with running(companion=companions.__contains__) as rig:
        rig.bridge.target_is_hero = False
        rig.bridge.target_npc = "CAVALORN"
        await rig.watch(extra=[companion_entry()])
        await eventually(lambda: rig.pilot._watch_ready)
        rig.bridge.response_delays["story_eval"] = 0.020
        rig.bridge.emit(player_row(), fighter(target_entity=3))
        await eventually(lambda: len(rig.bridge.commands("story_eval")) == 2)
        companions.clear()
        await eventually(lambda: rig.pilot._refusals["target_not_allowed"] == 1)
        assert not rig.bridge.commands("body_acquire")
        assert not rig.policy.requests


async def test_runtime_entity_id_is_bound_from_the_watched_npc_symbol():
    async with running() as rig:
        await rig.active(entity=9)
        assert rig.bridge.commands("body_acquire")[0]["entity_id"] == 9
        assert rig.bridge.commands("body_action")[0]["entity_id"] == 9
        assert rig.policy.commands("step")[0]["observation"]["entity_id"] == 9
        assert rig.bridge.commands("body_watch")[0]["npcs"] == ["NPC"]


async def test_native_body_roster_keeps_the_owned_npc_under_its_current_lease():
    async with running() as rig:
        await rig.active()
        await eventually(lambda: rig.pilot.people["NPC"].activity == "body")
        assert rig.pilot.people["NPC"].state == "ZS_ATTACK"
        rig.combat()
        rig.bridge.emit(fighter(4, owned=True))
        await eventually(lambda: len(rig.bridge.commands("body_action")) == 2)
        assert len(rig.bridge.commands("body_acquire")) == 1
        assert rig.link.body_owned("NPC")
        assert not rig.bridge.commands("body_release")


async def test_memory_only_periodic_rows_advance_policy_without_sending_commands():
    async with running() as rig:
        await rig.active()
        count = len(rig.bridge.commands("body_action"))
        own = fighter(4, owned=True, can_decide=False, action_mask_valid=False)
        rig.bridge.emit(own)
        await eventually(lambda: len(rig.policy.commands("step")) == 2)
        await asyncio.sleep(0.01)
        assert rig.policy.commands("step")[-1]["observation"] == own
        assert len(rig.bridge.commands("body_action")) == count


async def test_reset_precedes_first_periodic_row_with_the_acquired_identity():
    async with running() as rig:
        await rig.acquire()
        await eventually(lambda: rig.policy.commands("reset"))
        rig.bridge.emit(fighter(3, owned=True, periodic=False, event_only=True))
        await asyncio.sleep(0.01)
        assert not rig.policy.commands("step")
        own = fighter(4, owned=True, can_decide=False)
        rig.bridge.emit(own)
        await eventually(lambda: rig.policy.commands("step"))
        assert rig.policy.commands("reset")[0]["initial_game_ms"] == fighter()["t_game_ms"]
        assert rig.policy.commands("step")[0]["observation"] == own
        assert not rig.bridge.commands("body_action")


async def test_lease_heartbeats_run_while_acquire_waits_for_policy_reset(monkeypatch):
    monkeypatch.setattr(bodypilot, "HEARTBEAT_INTERVAL", 0.010)
    monkeypatch.setattr(bodypilot, "SERVICE_TIMEOUT", 0.500)
    async with running() as rig:
        rig.policy.reset_waiter = asyncio.Event()
        await rig.acquire()
        await eventually(lambda: rig.policy.commands("reset"))
        rig.bridge.emit(fighter(3, owned=True))
        await eventually(lambda: len(rig.bridge.commands("body_heartbeat")) >= 3)
        assert not rig.policy.commands("step")
        assert rig.link.body_owned("NPC") and rig.bridge.lease_expirations == 0
        rig.policy.reset_waiter.set()
        await eventually(lambda: rig.bridge.commands("body_action"))
        assert [request["cmd"] for request in rig.policy.requests] == ["reset", "step"]


async def test_foreign_old_event_only_rows_do_not_reach_policy_and_packet_time_is_current():
    async with running() as rig:
        await rig.active()
        old = fighter(3, owned=True)
        rig.bridge.emit(old)  # GameLink rejects the stale packet before listeners.
        foreign = fighter(4, owned=True, entity=4)
        event_only = fighter(5, owned=True, periodic=False, event_only=True)
        rig.bridge.emit(foreign, event_only)
        own = fighter(6, owned=True)
        latest = player_row(7)
        rig.bridge.emit(own, latest)
        await eventually(lambda: len(rig.policy.commands("step")) == 2)
        assert [step["observation"]["observation_seq"] for step in rig.policy.commands("step")] == [3, 6]
        assert rig.policy.commands("step")[-1]["now_game_ms"] == latest["t_game_ms"]


async def test_every_owned_periodic_row_in_a_packet_reaches_policy_as_received():
    async with running() as rig:
        await rig.active()
        first = fighter(4, owned=True, can_decide=False)
        second = fighter(5, owned=True, can_decide=False)
        rig.bridge.emit(first, second)
        await eventually(lambda: len(rig.policy.commands("step")) == 3)
        steps = rig.policy.commands("step")[-2:]
        assert [step["observation"] for step in steps] == [first, second]
        assert all(step["now_game_ms"] == second["t_game_ms"] for step in steps)
        assert len(rig.bridge.commands("body_action")) == 1


async def test_policy_backlog_does_not_starve_lease_heartbeats():
    async with running() as rig:
        await rig.active()
        processed = []
        rig.policy.step_delay = 0.075

        def step_received(request):
            processed.append((
                request["observation"]["observation_seq"],
                asyncio.get_running_loop().time(),
            ))

        rig.policy.on_step = step_received
        sequences = list(range(4, 13))
        rig.bridge.emit(*(fighter(seq, owned=True, can_decide=False) for seq in sequences))
        await eventually(lambda: len(processed) == len(sequences), timeout=2.0)
        assert [seq for seq, _ in processed] == sequences
        heartbeats_during_steps = [when for when in rig.bridge.command_times("body_heartbeat")
                                   if processed[0][1] <= when <= processed[-1][1]]
        assert len(heartbeats_during_steps) >= 2
        assert rig.bridge.lease_expirations == 0
        assert rig.link.body_owned("NPC")
        assert not rig.bridge.commands("body_release")


async def test_delayed_heartbeat_and_event_replies_do_not_accumulate_into_lease_expiry():
    async with running() as rig:
        rig.bridge.response_delays.update(body_heartbeat=0.200, body_events=0.240)
        await rig.active()
        assert rig.bridge.commands("body_acquire")[0]["timeout_ms"] == 2000
        await eventually(lambda: len(rig.bridge.commands("body_heartbeat")) >= 10, timeout=3.5)
        sent = rig.bridge.command_times("body_heartbeat")
        assert sent[-1] - sent[0] >= 2.0
        assert max(later - earlier for earlier, later in pairwise(sent)) < 0.400
        assert len(rig.bridge.commands("body_events")) >= 3
        assert rig.bridge.lease_expirations == 0
        assert rig.link.body_owned("NPC")
        assert not rig.bridge.commands("body_release")
        assert not rig.policy.commands("end_episode")


async def test_old_world_packets_are_excluded_without_rewinding_active_lease():
    async with running() as rig:
        await rig.active()
        old = fighter(4, owned=True, generation=0, world_epoch=0)
        rig.bridge.emit(old, generation=0)
        await asyncio.sleep(0.01)
        assert len(rig.policy.commands("step")) == 1
        assert rig.link.body_leases and not rig.bridge.commands("body_release")


async def test_acquire_rejection_retries_at_150_ms_and_then_starts_an_episode(caplog):
    caplog.set_level("INFO")
    async with running() as rig:
        rig.bridge.acquire_rejections.extend(["unsafe_boundary", "ai_queue_not_empty"])
        await rig.watch()
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: len(rig.bridge.commands("body_acquire")) == 1)
        rig.clock.advance(0.149)
        rig.bridge.emit(fighter(3))
        await asyncio.sleep(0.01)
        assert len(rig.bridge.commands("body_acquire")) == 1
        rig.clock.advance(0.002)
        rig.bridge.emit(fighter(4))
        await eventually(lambda: len(rig.bridge.commands("body_acquire")) == 2)
        rig.clock.advance(0.151)
        rig.bridge.emit(fighter(5))
        await eventually(lambda: rig.link.body_leases)
        assert len(rig.bridge.commands("body_acquire")) == 3
        await eventually(lambda: rig.policy.commands("reset"))
        assert not rig.policy.commands("step")
        rig.bridge.emit(fighter(6, owned=True))
        await eventually(lambda: rig.policy.commands("step"))
        await rig.pilot.aclose()
        assert "unsafe_boundary" in caplog.text and "ai_queue_not_empty" in caplog.text
        assert dict(rig.pilot.last_summary.acquire_refusals) == {
            "unsafe_boundary": 1,
            "ai_queue_not_empty": 1,
        }


async def test_pending_boundary_wait_sends_no_policy_or_actions_until_actual_grant():
    async with running() as rig:
        rig.bridge.acquire_pending_results.append("acquired")
        rig.bridge.acquire_pending_delay = 0.075
        await rig.watch()
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.bridge.pending_acquire is not None)
        command = rig.bridge.commands("body_acquire")[0]
        assert command["await_boundary_ms"] == 1500 and command["acquire_request_id"] > 0
        assert not rig.link.body_leases and not rig.policy.requests
        assert not rig.bridge.commands("body_action")
        rig.clock.advance(0.075)
        await eventually(lambda: rig.policy.commands("reset"))
        assert len(rig.bridge.commands("body_acquire")) == 1
        assert rig.pilot.lease.acquired_observation_seq == 3
        assert not rig.policy.commands("step")
        rig.bridge.emit(fighter(4, owned=True))
        await eventually(lambda: rig.bridge.commands("body_action"))
        await rig.pilot.aclose()
        summary = rig.pilot.last_summary
        assert summary.acquire_requests == summary.acquire_completed == 1
        assert summary.acquire_wait_p50_ms == pytest.approx(75)
        assert summary.acquire_wait_max_ms == pytest.approx(75)
        assert summary.release_confirmed and summary.watch_removed


async def test_expired_boundary_request_retries_without_ending_or_resetting_episode():
    async with running() as rig:
        rig.bridge.acquire_pending_results.extend(["expired", "acquired"])
        await rig.watch()
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.pilot._refusals)
        assert dict(rig.pilot._refusals) == {"body acquire cancelled: expired": 1}
        assert not rig.policy.requests and rig.pilot.npc == "NPC"
        rig.clock.advance(0.151)
        rig.bridge.emit(fighter(4))
        await eventually(lambda: rig.policy.commands("reset"))
        assert len(rig.bridge.commands("body_acquire")) == 2
        assert len(rig.policy.commands("reset")) == 1
        await rig.pilot.aclose()
        summary = rig.pilot.last_summary
        assert summary.acquire_requests == 2 and summary.acquire_completed == 1


async def test_boundary_wait_is_clipped_to_remaining_five_second_acquire_window():
    async with running() as rig:
        rig.bridge.acquire_pending_results.append("expired")
        await rig.watch()
        rig.clock.advance(4.5)
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.pilot._refusals)
        assert rig.bridge.commands("body_acquire")[0]["await_boundary_ms"] == 500
        rig.clock.advance(0.501)
        rig.bridge.emit(fighter(4))
        await eventually(lambda: rig.pilot.last_summary is not None)
        assert rig.pilot.last_summary.reason == "acquire_exhausted"
        assert rig.pilot.last_summary.acquire_completed == 0
        assert not rig.policy.requests


async def test_shutdown_cancels_boundary_request_before_removing_watch():
    async with running() as rig:
        rig.bridge.acquire_pending_results.append("acquired")
        rig.bridge.acquire_pending_delay = 1.5
        await rig.watch()
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.bridge.pending_acquire is not None)
        await rig.pilot.aclose()
        assert rig.bridge.pending_acquire is None and not rig.bridge.provider_owned
        cancel = rig.bridge.commands("body_release")[0]
        assert cancel["control_generation"] == 0
        assert cancel["acquire_request_id"] == rig.bridge.commands("body_acquire")[0]["acquire_request_id"]
        assert rig.bridge.commands("body_watch")[-1]["npcs"] == []
        assert not rig.policy.requests and not rig.link.body_leases


async def test_acquire_window_expires_after_five_seconds_and_waits_for_new_combat_event(caplog):
    caplog.set_level("INFO")
    async with running() as rig:
        rig.bridge.acquire_rejections.extend(["unsafe_boundary"] * 4)
        await rig.watch()
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.bridge.commands("body_acquire"))
        rig.clock.advance(4.99)
        rig.bridge.emit(fighter(3))
        await eventually(lambda: len(rig.bridge.commands("body_acquire")) == 2)
        rig.clock.advance(0.02)
        rig.bridge.emit(fighter(4))
        await eventually(lambda: any(not cmd["npcs"] for cmd in rig.bridge.commands("body_watch")))
        rig.clock.advance(1.0)
        rig.bridge.emit(fighter(5))
        await asyncio.sleep(0.01)
        assert len(rig.bridge.commands("body_acquire")) == 2
        assert not rig.policy.requests and not rig.bridge.commands("body_release")
        assert "unsafe_boundary" in caplog.text
        rig.combat()
        await eventually(lambda: len(rig.bridge.commands("body_watch")) == 3)


async def test_repeated_native_attack_roster_does_not_restart_exhausted_acquisition():
    async with running() as rig:
        rig.bridge.acquire_rejections.append("unsafe_boundary")
        rig.roster(activity="fight", state="ZS_ATTACK")
        await eventually(lambda: rig.bridge.commands("body_watch"))
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.bridge.commands("body_acquire"))
        rig.clock.advance(5.01)
        rig.roster()
        await eventually(lambda: any(not cmd["npcs"] for cmd in rig.bridge.commands("body_watch")))
        await eventually(lambda: rig.pilot.npc is None)
        for seq in range(3, 6):
            rig.roster()
            rig.bridge.emit(fighter(seq))
            await asyncio.sleep(0.01)
        assert len(rig.bridge.commands("body_watch")) == 2
        assert len(rig.bridge.commands("body_acquire")) == 1
        assert not rig.policy.requests


async def test_native_attack_roster_starts_after_a_precombat_watch_expires():
    async with running() as rig:
        rig.roster(activity="fight", state="ZS_WAIT")
        rig.combat(kind="weapon_drawn")
        await eventually(lambda: rig.bridge.commands("body_watch"))
        rig.clock.advance(5.01)
        rig.roster(activity="fight", state="ZS_WAIT")
        await eventually(lambda: any(not cmd["npcs"] for cmd in rig.bridge.commands("body_watch")))
        await eventually(lambda: rig.pilot.npc is None)
        assert not rig.bridge.commands("body_acquire")
        rig.roster(activity="fight", state="ZS_ATTACK")
        await eventually(lambda: len(rig.bridge.commands("body_watch")) == 3)
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.link.body_leases)
        assert len(rig.bridge.commands("body_acquire")) == 1


async def test_native_attack_during_a_precombat_watch_is_not_retried_after_exhaustion():
    async with running() as rig:
        rig.roster(activity="fight", state="ZS_WAIT")
        rig.combat(kind="weapon_drawn")
        await eventually(lambda: rig.bridge.commands("body_watch"))
        rig.clock.advance(2.0)
        rig.roster(activity="fight", state="ZS_ATTACK")
        rig.clock.advance(3.01)
        rig.roster(activity="fight", state="ZS_ATTACK")
        await eventually(lambda: any(not cmd["npcs"] for cmd in rig.bridge.commands("body_watch")))
        await eventually(lambda: rig.pilot.npc is None)
        for seq in range(2, 5):
            rig.roster(activity="fight", state="ZS_ATTACK")
            rig.bridge.emit(fighter(seq))
            await asyncio.sleep(0.01)
        assert len(rig.bridge.commands("body_watch")) == 2
        assert not rig.bridge.commands("body_acquire")
        assert not rig.policy.requests


@pytest.mark.parametrize("change", [
    {"dead": True}, {"unconscious": True}, {"down": True}, {"hp": 0},
])
async def test_npc_down_or_dead_releases_body_and_ends_episode(change):
    async with running() as rig:
        await rig.active()
        row = fighter(4, owned=True)
        row["self"].update(change)
        rig.bridge.emit(row)
        await rig.ended()


async def test_completed_fight_summary_survives_counter_reset_and_the_next_fight():
    async with running() as rig:
        assert rig.pilot.last_summary is None
        await rig.active()
        lease = rig.pilot.lease
        for seq in (4, 5):
            rig.bridge.emit(fighter(seq, owned=True))
            await eventually(lambda seq=seq: len(rig.bridge.commands("body_action")) == seq - 2)
        rig.bridge.emit(fighter(6, owned=True, can_decide=False))
        await eventually(lambda: len(rig.pilot._latencies) == 4)
        latencies = sorted(rig.pilot._latencies)
        rig.clock.advance(0.75)
        row = fighter(7, owned=True)
        row["self"]["down"] = True
        rig.bridge.emit(row)
        await eventually(lambda: rig.pilot.last_summary is not None)

        summary = rig.pilot.last_summary
        assert summary.npc == "NPC" and summary.lease == lease
        assert summary.reason == "npc_down"
        assert summary.decisions == 3 and summary.fallbacks == 0
        assert summary.service_p50_ms == latencies[1]
        assert summary.service_p95_ms == latencies[-1]
        assert summary.acquire_refusals == summary.action_rejections == ()
        assert summary.transient_rejections_consecutive == 0
        assert summary.fight_seconds == pytest.approx(0.75)
        assert summary.release_confirmed
        assert 0 <= summary.release_seconds < bodypilot.FINISH_TIMEOUT
        assert summary.watch_removed
        assert rig.pilot.npc is rig.pilot.lease is rig.pilot.client is None
        assert not rig.link.body_leases and rig.bridge._watched_npc is None
        assert not rig.pilot._latencies and rig.pilot._decisions == rig.pilot._fallbacks == 0
        with pytest.raises(FrozenInstanceError):
            summary.decisions = 100

        rig.roster(activity="routine", state="ZS_SIT_BENCH")
        await rig.watch()
        assert rig.pilot.last_summary is summary
        assert summary.decisions == 3


async def test_latency_breakdown_ends_at_action_send_before_bridge_ack(monkeypatch, caplog):
    caplog.set_level("INFO", logger="gothic_ai.bodypilot")
    async with running() as rig:
        await rig.acquire()
        await eventually(lambda: rig.policy.commands("reset"))
        action = rig.link.body_action

        async def delayed_ack(*args, **kwargs):
            result = await action(*args, **kwargs)
            rig.clock.advance(0.050)
            return result

        monkeypatch.setattr(rig.link, "body_action", delayed_ack)
        rig.policy.on_step = lambda _: rig.clock.advance(0.010)
        rig.bridge.emit(fighter(3, owned=True))
        rig.clock.advance(0.005)  # Packet waiting in the pilot queue before step.
        await eventually(lambda: rig.pilot._decisions == 1)
        row = fighter(4, owned=True)
        row["self"]["down"] = True
        rig.bridge.emit(row)
        await eventually(lambda: rig.pilot.last_summary is not None)
        summary = rig.pilot.last_summary
        for percentile in ("p50", "p95"):
            assert getattr(summary, f"service_{percentile}_ms") == pytest.approx(10)
            assert getattr(summary, f"packet_to_request_{percentile}_ms") == pytest.approx(5)
            assert getattr(summary, f"packet_to_response_{percentile}_ms") == pytest.approx(15)
            assert getattr(summary, f"response_to_action_{percentile}_ms") == pytest.approx(0)
            assert getattr(summary, f"packet_to_action_{percentile}_ms") == pytest.approx(15)
        assert summary.fight_seconds == pytest.approx(0.065)
        assert "packet_to_action_p95_ms=" in caplog.text
        assert not rig.pilot._packet_to_request and not rig.pilot._packet_to_response
        assert not rig.pilot._response_to_action and not rig.pilot._packet_to_action


@pytest.mark.parametrize("rejections", [
    ["stale_observation"] * 9,
    ["masked"] * 9,
    ["masked", "stale_observation", "expired", "expired_wall", "target_invalid",
     "command_busy", "locked", "masked", "stale_observation"],
])
async def test_transient_action_rejections_wait_for_a_fresh_row_and_success_resets_streak(rejections):
    async with running() as rig:
        await rig.active()
        rig.bridge.action_rejections.extend(rejections)
        for count, seq in enumerate(range(4, 13), 1):
            rig.bridge.emit(fighter(seq, owned=True))
            await eventually(lambda count=count: rig.pilot._transient_rejections_consecutive == count)
            assert rig.link.body_owned("NPC")
            assert not rig.bridge.commands("body_release")
        rig.bridge.emit(fighter(13, owned=True, can_decide=False))
        await eventually(lambda: len(rig.policy.commands("step")) == 11)
        assert rig.pilot._transient_rejections_consecutive == 9  # Memory-only is not an accepted action.
        rig.bridge.emit(fighter(14, owned=True))
        await eventually(lambda: rig.pilot._decisions == 2)
        assert rig.pilot._transient_rejections_consecutive == 0
        # More than ten total refusals are allowed when accepted actions reset the streak.
        rig.bridge.action_rejections.append("masked")
        rig.bridge.emit(fighter(15, owned=True))
        await eventually(lambda: rig.pilot._transient_rejections_consecutive == 1)
        assert rig.link.body_owned("NPC") and not rig.bridge.commands("body_release")
        rig.bridge.emit(fighter(16, owned=True))
        await eventually(lambda: rig.pilot._decisions == 3)
        assert rig.pilot._transient_rejections_consecutive == 0
        rig.bridge.action_rejections.append("stale_observation")
        rig.bridge.emit(fighter(17, owned=True))
        await eventually(lambda: rig.pilot._transient_rejections_consecutive == 1)
        assert rig.link.body_owned("NPC") and not rig.bridge.commands("body_release")
        row = fighter(18, owned=True)
        row["self"]["down"] = True
        rig.bridge.emit(row)
        await eventually(lambda: rig.pilot.last_summary is not None)
        summary = rig.pilot.last_summary
        assert summary.reason == "npc_down" and summary.decisions == 3
        assert dict(summary.action_rejections) == Counter([*rejections, "masked", "stale_observation"])
        assert summary.transient_rejections_consecutive == 1
        assert not rig.pilot._action_rejections and rig.pilot._transient_rejections_consecutive == 0


@pytest.mark.parametrize("rejections", [
    ["stale_observation"] * 10,
    ["masked"] * 10,
    ["masked", "stale_observation"] * 5,
])
async def test_ten_consecutive_transient_action_rejections_restore_native_and_are_reported(
    caplog, rejections,
):
    caplog.set_level("INFO", logger="gothic_ai.bodypilot")
    async with running() as rig:
        await rig.active()
        rig.bridge.action_rejections.extend(rejections)
        for count, seq in enumerate(range(4, 13), 1):
            rig.bridge.emit(fighter(seq, owned=True))
            await eventually(lambda count=count: rig.pilot._transient_rejections_consecutive == count)
            assert not rig.bridge.commands("body_release")
        rig.bridge.emit(fighter(13, owned=True))
        await rig.ended()
        await eventually(lambda: rig.pilot.last_summary is not None)
        summary = rig.pilot.last_summary
        assert summary.reason == "body_action_rejected" and summary.decisions == 1
        assert dict(summary.action_rejections) == Counter(rejections)
        assert summary.transient_rejections_consecutive == 10
        assert summary.release_confirmed and summary.watch_removed
        assert f"action_rejections={dict(sorted(Counter(rejections).items()))}" in caplog.text
        assert "transient_rejections_consecutive=10" in caplog.text


@pytest.mark.parametrize("reason", [
    "not_owned", "stale_generation", "owner_mismatch", "invalid_command", "weapon_mode", "paused_or_failed",
])
async def test_nontransient_action_rejection_still_ends_immediately(reason):
    async with running() as rig:
        await rig.active()
        rig.bridge.action_rejections.append(reason)
        rig.bridge.emit(fighter(4, owned=True))
        await rig.ended()
        await eventually(lambda: rig.pilot.last_summary is not None)
        summary = rig.pilot.last_summary
        assert summary.reason == "body_action_rejected" and summary.decisions == 1
        assert dict(summary.action_rejections) == {reason: 1}
        assert summary.transient_rejections_consecutive == 0


@pytest.mark.parametrize("change,reason", [
    ({"down": True}, "hero_down"), ({"hp": 0}, "hero_down"),
    ({"entity_id": 8}, "target_changed"),
])
async def test_hero_down_dead_or_target_changed_ends_episode(change, reason):
    async with running() as rig:
        await rig.active()
        row = fighter(4, owned=True)
        row["target"].update(change)
        rig.bridge.emit(row)
        await rig.ended()
        assert rig.pilot.last_summary.reason == reason
        assert rig.pilot.last_summary.target_kind == "hero"
        assert rig.pilot.last_summary.target_name is None


@pytest.mark.parametrize("change", [{"down": True}, {"hp": 0}])
async def test_companion_down_or_dead_ends_with_target_down(change):
    async with running(companion=lambda npc: npc == "CAVALORN") as rig:
        rig.bridge.target_is_hero = False
        rig.bridge.target_npc = "CAVALORN"
        await rig.active(target_entity=3, extra=[companion_entry()])
        row = fighter(4, owned=True, target_entity=3)
        row["target"].update(change)
        rig.bridge.emit(row)
        await rig.ended()
        summary = rig.pilot.last_summary
        assert summary.reason == "target_down"
        assert summary.target_kind == "companion" and summary.target_name == "Кавалорн"
        assert summary.release_confirmed and summary.watch_removed


async def test_companion_to_hero_target_change_reacquires_and_resets_the_policy():
    async with running(companion=lambda npc: npc == "CAVALORN") as rig:
        rig.bridge.target_is_hero = False
        rig.bridge.target_npc = "CAVALORN"
        await rig.active(target_entity=3, extra=[companion_entry()])
        old_lease = rig.pilot.lease
        rig.clock.advance(0.5)
        await rig.retarget(4, target_entity=2)
        summary = rig.pilot.last_summary
        assert summary.lease == old_lease
        assert summary.target_kind == "companion" and summary.target_name == "Кавалорн"
        assert summary.release_confirmed and summary.watch_removed

        rig.clock.now = 12.0
        await rig.reacquire(5, target_entity=2, count=2)
        assert rig.pilot.last_summary is summary
        assert [request["cmd"] for request in rig.policy.requests] == [
            "reset", "step", "end_episode", "close", "reset",
        ]
        assert [request["initial_game_ms"] for request in rig.policy.commands("reset")] == [100, 250]
        assert [command["target_entity_id"] for command in rig.bridge.commands("body_acquire")] == [3, 2]
        rig.bridge.emit(fighter(6, owned=True))
        await eventually(lambda: len(rig.bridge.commands("body_action")) == 2)
        assert rig.bridge.commands("body_action")[-1]["target_entity_id"] == 2


@pytest.mark.parametrize("release_reason", ["native_script_takeover", "target_changed_or_lost"])
@pytest.mark.parametrize("old_target,new_target", [(3, 2), (2, 3)])
async def test_polled_native_target_change_reacquires_without_an_owned_target_row(
    release_reason, old_target, new_target,
):
    async with running(companion=lambda npc: npc == "CAVALORN") as rig:
        rig.bridge.target_is_hero = old_target == 2
        rig.bridge.target_npc = "CAVALORN" if old_target == 3 else None
        await rig.active(target_entity=old_target, extra=[companion_entry()])
        rig.clock.advance(0.25)
        # Native setTarget restores ownership before applying the deferred new target.
        # The event poll therefore reaches the pilot before any owned row with that target.
        rig.bridge.target_is_hero = new_target == 2
        rig.bridge.target_npc = "CAVALORN" if new_target == 3 else None
        rig.bridge.restore_native(release_reason=release_reason)
        await eventually(lambda: rig.pilot.last_summary is not None)
        await eventually(lambda: rig.pilot.npc is None)
        summary = rig.pilot.last_summary
        assert summary.reason == "target_changed"
        assert summary.native_release_reason == release_reason
        assert summary.target_kind == ("hero" if old_target == 2 else "companion")
        assert summary.release_confirmed and summary.watch_removed
        assert len(rig.policy.commands("step")) == 1

        rig.clock.now = 12.0
        await rig.reacquire(4, target_entity=new_target, count=2)
        assert [command["target_entity_id"] for command in rig.bridge.commands("body_acquire")] == [
            old_target, new_target,
        ]
        assert len(rig.policy.commands("reset")) == 2


@pytest.mark.parametrize("release_reason", ["native_script_takeover", "target_changed_or_lost"])
@pytest.mark.parametrize("new_target", ["CAVALORN", "OTHER"])
async def test_polled_native_release_keeps_same_or_unrelated_target_combat_claimed(
    release_reason, new_target,
):
    async with running(companion=lambda npc: npc == "CAVALORN") as rig:
        rig.bridge.target_is_hero = False
        rig.bridge.target_npc = "CAVALORN"
        await rig.active(target_entity=3, extra=[companion_entry()])
        rig.clock.advance(0.25)
        rig.bridge.target_npc = new_target
        rig.bridge.restore_native(release_reason=release_reason)
        await eventually(lambda: rig.pilot.last_summary is not None)
        await eventually(lambda: rig.pilot.npc is None)
        summary = rig.pilot.last_summary
        assert summary.reason == "native_restored"
        assert summary.native_release_reason == release_reason
        assert summary.release_confirmed and summary.watch_removed
        assert "NPC" in rig.pilot._claimed_fights

        rig.clock.advance(3)
        rig.bridge.target_is_hero = True
        rig.bridge.target_npc = None
        rig.roster(extra=[companion_entry()])
        rig.combat()
        rig.bridge.emit(fighter(4))
        await asyncio.sleep(0.060)
        assert len(rig.bridge.commands("body_watch")) == 2
        assert len(rig.bridge.commands("body_acquire")) == 1
        assert len(rig.policy.commands("reset")) == 1
        assert not rig.link.body_leases


@pytest.mark.parametrize("companion", [False, True])
async def test_polled_native_release_keeps_down_reason_from_passive_target_observation(companion):
    async with running(companion=lambda npc: companion and npc == "CAVALORN") as rig:
        rig.bridge.target_is_hero = not companion
        rig.bridge.target_npc = "CAVALORN" if companion else None
        target_entity = 3 if companion else 2
        await rig.active(target_entity=target_entity, extra=[companion_entry()] if companion else [])
        rig.bridge.restore_native(release_reason="target_changed_or_lost")
        row = fighter(4, target_entity=target_entity, periodic=False, event_only=True)
        row["target"]["down"] = True
        rig.bridge.emit(row)
        await eventually(lambda: rig.pilot.last_summary is not None)
        summary = rig.pilot.last_summary
        assert summary.reason == ("target_down" if companion else "hero_down")
        assert summary.release_confirmed and summary.watch_removed
        assert len(rig.policy.commands("reset")) == 1


@pytest.mark.parametrize("companion", [False, True])
@pytest.mark.parametrize("evidence", ["matching", "missing", "foreign", "alive"])
async def test_heartbeat_native_handoff_requires_matching_evidence_for_down_reason(companion, evidence):
    async with running(companion=lambda npc: companion and npc == "CAVALORN") as rig:
        rig.bridge.target_is_hero = not companion
        rig.bridge.target_npc = "CAVALORN" if companion else None
        target_entity = 3 if companion else 2
        await rig.active(target_entity=target_entity, extra=[companion_entry()] if companion else [])
        original_call = rig.pilot.client.call

        async def close_after_target_cleared(command, **fields):
            if command == "end_episode":
                # Native combat clears the fallen target while policy cleanup awaits.
                # That later snapshot must not erase the scoped down evidence.
                rig.bridge.emit(unseen_fighter(
                    5, present=False, target_entity=0, control_generation=0,
                    periodic=False, event_only=True,
                ))
            return await original_call(command, **fields)

        rig.pilot.client.call = close_after_target_cleared
        summary = await rig.lose_heartbeat_before_native_event(
            target_entity=target_entity, evidence=evidence, down=evidence != "alive",
        )
        expected = (
            ("target_down" if companion else "hero_down") if evidence == "matching" else "lease_lost"
        )
        assert summary.reason == expected
        assert summary.release_confirmed and summary.watch_removed
        assert summary.native_release_reason == (
            "target_changed_or_lost" if evidence in {"matching", "alive"} else None
        )
        assert len(rig.policy.commands("reset")) == 1


async def test_heartbeat_native_target_change_reacquires_and_resets_after_cleanup_event():
    async with running(companion=lambda npc: npc == "CAVALORN") as rig:
        rig.bridge.target_is_hero = False
        rig.bridge.target_npc = "CAVALORN"
        await rig.active(target_entity=3, extra=[companion_entry()])
        rig.bridge.target_is_hero = True
        rig.bridge.target_npc = None
        summary = await rig.lose_heartbeat_before_native_event(target_entity=3)
        assert summary.reason == "target_changed"
        assert summary.native_release_reason == "target_changed_or_lost"
        assert summary.target_kind == "companion"
        assert summary.release_confirmed and summary.watch_removed
        rig.clock.now = 12.0
        await rig.reacquire(5, target_entity=2, count=2)
        assert [command["target_entity_id"] for command in rig.bridge.commands("body_acquire")] == [3, 2]
        assert len(rig.policy.commands("reset")) == 2


async def test_target_change_reacquisition_waits_two_seconds_for_each_npc():
    async with running(companion=lambda npc: npc == "CAVALORN") as rig:
        rig.bridge.target_is_hero = False
        rig.bridge.target_npc = "CAVALORN"
        await rig.active(target_entity=3, extra=[companion_entry()])
        rig.clock.advance(0.25)
        await rig.retarget(4, target_entity=2)
        rig.clock.now = 11.999
        await asyncio.sleep(0.060)
        assert len(rig.bridge.commands("body_watch")) == 2
        assert len(rig.bridge.commands("body_acquire")) == 1

        rig.clock.now = 12.0
        await rig.reacquire(5, target_entity=2, count=2)
        rig.clock.advance(0.25)
        await rig.retarget(6, target_entity=3, target_npc="CAVALORN")
        rig.clock.now = 13.999
        await asyncio.sleep(0.060)
        assert len(rig.bridge.commands("body_watch")) == 4
        assert len(rig.bridge.commands("body_acquire")) == 2
        assert len(rig.policy.commands("reset")) == 2

        rig.clock.now = 14.0
        await rig.reacquire(7, target_entity=3, count=3)
        assert [command["target_entity_id"] for command in rig.bridge.commands("body_acquire")] == [3, 2, 3]


async def test_change_to_unrelated_npc_blocks_recapture_for_the_same_native_fight():
    async with running(companion=lambda npc: npc == "CAVALORN") as rig:
        rig.bridge.target_is_hero = False
        rig.bridge.target_npc = "CAVALORN"
        await rig.active(target_entity=3, extra=[companion_entry()])
        rig.clock.advance(0.25)
        await rig.retarget(4, target_entity=8, target_npc="OTHER")
        assert "NPC" in rig.pilot._claimed_fights
        rig.clock.advance(3)
        # Returning to an eligible target does not undo the end of this native fight.
        rig.bridge.target_is_hero = True
        rig.bridge.target_npc = None
        for seq in (5, 6):
            rig.roster(extra=[companion_entry()])
            rig.combat()
            rig.bridge.emit(fighter(seq))
            await asyncio.sleep(0.030)
        assert len(rig.bridge.commands("body_watch")) == 2
        assert len(rig.bridge.commands("body_acquire")) == 1
        assert len(rig.policy.commands("reset")) == 1
        assert not rig.link.body_leases


async def test_temporary_target_invisibility_recovers_without_ending_the_fight():
    async with running() as rig:
        await rig.active()
        for seq, game_ms in ((4, 1000), (5, 1250), (6, 1700)):
            rig.clock.advance(0.6)
            rig.bridge.emit(unseen_fighter(seq, t_game_ms=game_ms))
            await eventually(lambda seq=seq: len(rig.policy.commands("step")) == seq - 2)
            assert rig.pilot._reason is None
            assert not rig.bridge.commands("body_release")
        rig.bridge.emit(fighter(7, owned=True, t_game_ms=1750))
        await eventually(lambda: len(rig.bridge.commands("body_action")) == 2)
        assert rig.pilot._reason is None and rig.pilot._target_absent_game_ms is None
        assert len(rig.pilot._policy_decisions) == 2  # Locked unseen rows are not policy decisions.
        row = fighter(8, owned=True, t_game_ms=1800)
        row["self"]["down"] = True
        rig.bridge.emit(row)
        await rig.ended()
        assert rig.pilot.last_summary.reason == "npc_down"


@pytest.mark.parametrize("present,target_entity", [(False, 0), (True, 2)])
async def test_target_absence_or_invalidity_ends_after_one_game_second(present, target_entity):
    async with running() as rig:
        await rig.active()
        for seq, game_ms in ((4, 1000), (5, 1999)):
            rig.bridge.emit(unseen_fighter(
                seq, present=present, target_entity=target_entity, t_game_ms=game_ms,
            ))
            await eventually(lambda seq=seq: len(rig.policy.commands("step")) == seq - 2)
            assert rig.pilot._reason is None and not rig.bridge.commands("body_release")
        rig.bridge.emit(unseen_fighter(6, present=present, target_entity=target_entity, t_game_ms=2000))
        await rig.ended()
        assert rig.pilot.last_summary.reason == "target_lost"
        assert rig.pilot.last_summary.target_invisible_game_ms == 1000


async def test_target_validity_recovery_restarts_the_absence_grace():
    async with running() as rig:
        await rig.active()
        for seq, game_ms, visible in ((4, 1000, False), (5, 1700, True),
                                      (6, 1900, False), (7, 2899, False)):
            row = (fighter(seq, owned=True, t_game_ms=game_ms) if visible
                   else unseen_fighter(seq, t_game_ms=game_ms))
            rig.bridge.emit(row)
            await eventually(lambda seq=seq: len(rig.policy.commands("step")) == seq - 2)
            assert rig.pilot._reason is None and not rig.bridge.commands("body_release")
        rig.bridge.emit(unseen_fighter(8, t_game_ms=2900))
        await rig.ended()
        assert rig.pilot.last_summary.reason == "target_lost"


@pytest.mark.parametrize("change,reason", [("target", "target_changed"), ("weapon", "weapon_changed")])
async def test_target_change_and_weapon_change_remain_immediate_while_unseen(change, reason):
    async with running() as rig:
        await rig.active()
        row = unseen_fighter(4, target_entity=8 if change == "target" else 2)
        if change == "weapon":
            row["self"]["weapon_state"] = 0
        rig.bridge.emit(row)
        await rig.ended()
        assert rig.pilot.last_summary.reason == reason


@pytest.mark.parametrize("scope", [
    {"entity_id": 8}, {"session_epoch": 2}, {"world_epoch": 2}, {"control_generation": 8},
])
async def test_native_visibility_restoration_respects_lease_identity(scope):
    async with running() as rig:
        await rig.active()
        rig.pilot.on_message(events(event(
            1, reason="native_restored", release_reason="target_visibility_lost", owner="native", **scope,
        ), **{key: value for key, value in scope.items() if key in {"session_epoch", "world_epoch"}}))
        assert rig.pilot._reason is None
        assert rig.pilot._native_release_reason is None
        rig.link._dispatch(events(event(
            2, reason="native_restored", release_reason="target_visibility_lost", owner="native",
        )))
        await rig.ended()
        assert rig.pilot.last_summary.reason == "target_lost"
        assert rig.pilot.last_summary.native_release_reason == "target_visibility_lost"


async def test_visibility_restoration_diagnostics_keep_last_ten_actual_policy_decisions(caplog):
    caplog.set_level("INFO", logger="gothic_ai.bodypilot")
    async with running() as rig:
        await rig.active()
        rig.bridge.emit(unseen_fighter(4, t_game_ms=1000))
        await eventually(lambda: len(rig.policy.commands("step")) == 2)
        rig.clock.advance(0.8)
        rig.bridge.emit(fighter(5, owned=True, t_game_ms=1800))
        await eventually(lambda: len(rig.bridge.commands("body_action")) == 2)
        for seq in range(6, 18):
            if seq == 8:
                rig.bridge.action_rejections.append("masked")
            rig.bridge.emit(fighter(seq, owned=True, t_game_ms=1800 + seq))
            await eventually(lambda seq=seq: len(rig.bridge.commands("body_action")) == seq - 3)
        rig.bridge.emit(unseen_fighter(18, t_game_ms=2000))
        await eventually(lambda: len(rig.policy.commands("step")) == 16)
        rig.clock.advance(0.24)
        rig.bridge.emit(unseen_fighter(19, t_game_ms=2200))
        await eventually(lambda: len(rig.policy.commands("step")) == 17)
        rig.clock.advance(0.35)
        rig.link._dispatch(events(event(
            1, reason="native_restored", release_reason="target_visibility_lost", owner="native",
            t_game_ms=2400,
        )))
        await rig.ended()
        summary = rig.pilot.last_summary
        assert summary.reason == "target_lost" and summary.release_confirmed
        assert summary.target_invisible_seconds == pytest.approx(0.8)
        assert summary.target_invisible_game_ms == 800
        assert summary.target_invisible_last_seconds == pytest.approx(0.59)
        assert summary.target_invisible_last_game_ms == 400
        decisions = summary.last_policy_decisions
        assert tuple(row.observation_seq for row in decisions) == tuple(range(8, 18))
        assert all(row.action_id == 1 and row.sent for row in decisions)
        assert not decisions[0].accepted and decisions[0].reason == "masked"
        assert all(row.accepted and row.reason == "accepted" for row in decisions[1:])
        assert not rig.pilot._policy_decisions
        with pytest.raises(FrozenInstanceError):
            decisions[0].accepted = True
        assert "invisible_max_game_ms=800" in caplog.text
        assert "invisible_last_game_ms=400" in caplog.text
        assert "native_release_reason=target_visibility_lost release_confirmed=True" in caplog.text


@pytest.mark.parametrize("takeover", ["native_restored", "lease_lost", "attached", "dialog", "unavailable"])
async def test_native_ownership_or_dialogue_takeover_ends_episode(takeover):
    async with running() as rig:
        await rig.active()
        if takeover == "native_restored":
            rig.link._dispatch(events(event(1, reason="native_restored", owner="native")))
        elif takeover == "lease_lost":
            rig.bridge.emit(fighter(4, owned=True, control_generation=8))
        elif takeover == "attached":
            rig.link._dispatch(Attached(type="attached", npc="NPC", name="Fighter"))
        elif takeover == "dialog":
            rig.roster(activity="dialog")
        else:
            rig.pilot.unavailable = lambda _: True
            rig.bridge.emit(fighter(4, owned=True))
        await rig.ended()


async def test_pending_release_keeps_watch_until_native_restoration():
    async with running() as rig:
        await rig.active()
        rig.bridge.release_pending_polls = 2
        rig.link._dispatch(Attached(type="attached", npc="NPC", name="Fighter"))
        await rig.ended()
        commands = rig.bridge.sent
        released_at = next(i for i, command in enumerate(commands) if command["type"] == "body_release")
        unwatched_at = next(i for i, command in enumerate(commands)
                            if command["type"] == "body_watch" and not command["npcs"])
        assert sum(command["type"] == "body_events" for command in commands[released_at:unwatched_at]) == 3


@pytest.mark.parametrize("lost_during_release", [False, True])
async def test_lost_native_restoration_event_reconciles_lease_and_allows_the_next_fight(lost_during_release):
    async with running() as rig:
        await rig.active()
        lease = rig.link.body_leases[1]
        rig.bridge.response_delays["body_events"] = 0.350
        if lost_during_release:
            rig.bridge.release_pending_polls = 1
            rig.link._dispatch(Attached(type="attached", npc="NPC", name="Fighter"))
            await eventually(lambda: not rig.bridge.provider_owned)
        else:
            rig.bridge.restore_native()
            # Polling destructively drains the event before its reply times out.
            await eventually(lambda: not rig.bridge.control_events)
        rig.bridge.emit(fighter(4))
        assert rig.link.body_leases == {1: lease}

        await rig.ended()
        await eventually(lambda: rig.pilot.npc is None)
        assert rig.bridge.commands("body_control_status")
        assert rig.bridge._watched_npc is None
        assert len(rig.policy.commands("end_episode")) == 1
        assert len(rig.policy.commands("close")) == 1
        assert len(rig.policy.commands("step")) == 1

        # A later combat can still acquire and run policy decisions.
        rig.bridge.response_delays.clear()
        rig.roster(activity="routine", state="ZS_SIT_BENCH")
        await rig.watch()
        await eventually(lambda: rig.pilot._watch_ready)
        rig.bridge.emit(player_row(5), fighter(6))
        await eventually(lambda: len(rig.bridge.commands("body_acquire")) == 2)
        await eventually(lambda: rig.link.body_leases)
        rig.bridge.emit(fighter(7, owned=True))
        await eventually(lambda: len(rig.bridge.commands("body_action")) == 2)
        assert len(rig.policy.commands("reset")) == 2


@pytest.mark.parametrize("status_response", ["missing_actor", "unknown_entity"])
async def test_removed_actor_during_release_clears_watch_and_allows_a_different_fight(
    status_response, monkeypatch,
):
    monkeypatch.setattr(bodypilot, "FINISH_TIMEOUT", 0.8)
    async with running() as rig:
        await rig.active()
        lease = rig.link.body_leases[1]
        # Removal destroys the native actor without a restoration event. The
        # adapter still has the lease, while neither release nor status can find it.
        rig.bridge.provider_owned = False
        rig.bridge._control_scope = None
        rig.bridge._lease_watchdog.cancel()
        rig.bridge.release_rejections.append("unknown_entity")
        if status_response == "unknown_entity":
            rig.bridge.status_error = "unknown_entity"
        assert rig.link.body_leases == {1: lease}
        rig.link._dispatch(Attached(type="attached", npc="NPC", name="Fighter"))
        await eventually(lambda: rig.pilot.npc is None, timeout=1.2)
        assert rig.bridge.commands("body_release")
        assert rig.bridge.commands("body_control_status")
        assert not rig.link.body_leases
        assert rig.bridge._watched_npc is None
        assert rig.bridge.commands("body_watch")[-1]["npcs"] == []
        assert len(rig.policy.commands("end_episode")) == 1
        assert len(rig.policy.commands("close")) == 1
        assert not rig.pilot._done.is_set()

        rig.bridge.status_error = None
        rig.bridge.roster(RosterEntry(
            id="OTHER", name="Other Fighter", guild=1, distance=250.0,
            in_view=True, activity="fight", state="ZS_ATTACK",
        ))
        rig.combat("OTHER")
        await eventually(lambda: rig.pilot._watch_ready)
        assert rig.bridge.commands("body_watch")[-1]["npcs"] == ["OTHER"]
        rig.bridge.emit(player_row(5), fighter(6, entity=4))
        await eventually(lambda: len(rig.bridge.commands("body_acquire")) == 2)
        await eventually(lambda: rig.link.body_leases)
        rig.bridge.emit(fighter(7, owned=True, entity=4))
        await eventually(lambda: len(rig.bridge.commands("body_action")) == 2)
        assert rig.link.body_leases[4].npc == "OTHER"
        assert len(rig.policy.commands("reset")) == 2


async def test_unconfirmed_release_hits_finish_deadline_and_closes_episode(monkeypatch, caplog):
    monkeypatch.setattr(bodypilot, "FINISH_TIMEOUT", 0.8)
    caplog.set_level("INFO", logger="gothic_ai.bodypilot")
    async with running() as rig:
        await rig.active()
        lease = rig.link.body_leases[1]
        # Ownership has returned to native, but all evidence is unavailable:
        # release says not_owned, the event queue is empty, and status never replies.
        rig.bridge.provider_owned = False
        rig.bridge._lease_watchdog.cancel()
        rig.bridge.response_delays["body_control_status"] = 2.0
        rig.link._dispatch(Attached(type="attached", npc="NPC", name="Fighter"))
        await eventually(lambda: rig.bridge.commands("body_control_status"))
        assert rig.link.body_leases == {1: lease}
        await eventually(lambda: rig.pilot.npc is None, timeout=1.2)
        assert not rig.link.body_leases
        assert rig.bridge._watched_npc is None
        assert rig.bridge.commands("body_watch")[-1]["npcs"] == []
        assert len(rig.policy.commands("end_episode")) == 1
        assert len(rig.policy.commands("close")) == 1
        assert not rig.pilot._done.is_set()
        assert "reason=finish_deadline" in caplog.text
        assert "unconfirmed=" in caplog.text
        summary = rig.pilot.last_summary
        assert summary.lease == lease and summary.reason == "finish_deadline"
        assert not summary.release_confirmed and summary.release_seconds is None
        assert summary.watch_removed


async def test_uncertain_acquire_recovery_is_bounded_by_finish_deadline(monkeypatch, caplog):
    monkeypatch.setattr(bodypilot, "FINISH_TIMEOUT", 0.8)
    caplog.set_level("INFO", logger="gothic_ai.bodypilot")
    async with running() as rig:
        rig.bridge.response_delays.update(body_acquire=2.0, body_control_status=2.0)
        await rig.watch()
        await eventually(lambda: rig.pilot._watch_ready)
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.bridge.commands("body_control_status"))
        # The native watchdog has restored the actor, but the adapter never
        # received its acquire receipt or an ownership snapshot.
        rig.bridge.provider_owned = False
        rig.bridge._lease_watchdog.cancel()
        await eventually(lambda: rig.pilot.npc is None, timeout=1.2)
        assert rig.pilot._pending_acquire is None
        assert not rig.link.body_leases
        assert rig.bridge._watched_npc is None
        assert rig.bridge.commands("body_watch")[-1]["npcs"] == []
        assert not rig.policy.requests
        assert not rig.pilot._done.is_set()
        assert "reason=finish_deadline" in caplog.text
        assert "unconfirmed=" in caplog.text


@pytest.mark.parametrize("confirmation", ["event", "status"])
async def test_orphaned_boundary_request_unsubscribes_and_allows_the_next_fight(monkeypatch, confirmation):
    monkeypatch.setattr(bodypilot, "BRIDGE_TIMEOUT", 0.050)
    monkeypatch.setattr(bodypilot, "FINISH_TIMEOUT", 0.150)
    async with running() as rig:
        # The native request expires, but its ACK and cancellation evidence are
        # unavailable until after the pilot's bounded cleanup has finished.
        rig.bridge.acquire_pending_results.append("expired")
        rig.bridge.acquire_pending_delay = 0
        rig.bridge.response_delays["body_acquire"] = 2.0
        rig.bridge.release_rejections.extend(["cancel_busy"] * 100)
        rig.bridge.status_error = "status_busy"
        await rig.watch()
        await eventually(lambda: rig.pilot._watch_ready)
        rig.bridge.emit(player_row(), fighter())
        await eventually(lambda: rig.pilot.last_summary is not None)
        assert rig.pilot.last_summary.reason == "finish_deadline"
        assert rig.pilot.last_summary.watch_removed
        assert rig.bridge.commands("body_watch")[-1]["npcs"] == []
        assert rig.bridge._watched_npc is None
        assert rig.link.body_acquire_pending(1)
        assert not rig.bridge.pending_acquire and not rig.bridge.provider_owned
        assert not rig.policy.requests

        rig.bridge.status_error = None
        rig.bridge.response_delays.clear()
        rig.bridge.release_rejections.clear()
        if confirmation == "event":
            await rig.link.body_events()
        else:
            status = await rig.link.body_control_status()
            assert status.actors[0].owner == "native" and status.actors[0].acquire_request_id == 0
        assert not rig.link.body_acquire_pending(1)

        rig.roster(activity="routine", state="ZS_SIT_BENCH")
        await rig.watch()
        await eventually(lambda: rig.pilot._watch_ready)
        rig.bridge.emit(player_row(5), fighter(6))
        await eventually(lambda: rig.link.body_leases)
        rig.bridge.emit(fighter(7, owned=True))
        await eventually(lambda: rig.bridge.commands("body_action"))
        assert len(rig.bridge.commands("body_acquire")) == 2
        assert len(rig.policy.commands("reset")) == 1


@pytest.mark.parametrize("failed_command", [
    "body_release", "body_events", "body_control_status", "body_watch",
])
async def test_websocket_closing_during_cleanup_still_closes_policy_and_pilot(failed_command, monkeypatch):
    async with running() as rig:
        await rig.active()
        client = rig.pilot.client
        assert client is not None
        if failed_command in {"body_events", "body_control_status"}:
            rig.bridge.release_pending_polls = 2
        original_send = rig.bridge.send
        failed_sends = []

        async def disconnect_during_send(raw):
            command = json.loads(raw)
            if command["type"] == failed_command:
                failed_sends.append(command)
                # WebSocket send can fail before GameLink.run observes the close.
                asyncio.get_running_loop().call_soon(rig.link.closed.set)
                raise ConnectionClosedError(None, None)
            await original_send(raw)

        monkeypatch.setattr(rig.bridge, "send", disconnect_during_send)
        rig.link._dispatch(Attached(type="attached", npc="NPC", name="Fighter"))
        await eventually(lambda: failed_sends)
        await eventually(lambda: rig.pilot._done.is_set())
        await asyncio.wait_for(rig.pilot.aclose(), 0.1)
        assert rig.link.closed.is_set()
        assert len(rig.policy.commands("end_episode")) == 1
        assert len(rig.policy.commands("close")) == 1
        assert client.writer is not None and client.writer.is_closing()
        assert client.reader_task is not None and client.reader_task.done()
        assert rig.pilot.on_message not in rig.link.listeners
        assert rig.pilot.on_body_packet not in rig.link.raw_body_listeners


@pytest.mark.parametrize("transition", ["loading", "hello", "disconnect"])
async def test_world_or_connection_reset_releases_and_closes_episode(transition, monkeypatch):
    async with running() as rig:
        await rig.active()
        if transition == "loading":
            rig.link._dispatch(Loading(type="loading"))
        elif transition == "hello":
            rig.link._dispatch(Hello(type="hello", protocol=41, client="fake", codepage="utf-8"))
        else:
            attempted = []
            release, watch = rig.link.body_release, rig.link.body_watch

            async def record_release(*args, **kwargs):
                attempted.append("release")
                return await release(*args, **kwargs)

            async def record_watch(*args, **kwargs):
                attempted.append("watch")
                return await watch(*args, **kwargs)

            monkeypatch.setattr(rig.link, "body_release", record_release)
            monkeypatch.setattr(rig.link, "body_watch", record_watch)
            # GameLink.run tears down the cached native state before declaring
            # the WebSocket closed; no command can travel over it afterward.
            rig.link._clear_body_observations()
            rig.link.closed.set()
            await eventually(lambda: attempted == ["release", "watch"])
            assert rig.policy.commands("end_episode") and rig.policy.commands("close")
            assert not rig.link.body_leases and not rig.link.body_latest
            assert not rig.bridge.commands("body_release")
            return
        await rig.ended()


@pytest.mark.parametrize("rpc,stage", [("story_eval", "symbol_eval"), ("body_watch", "body_watch")])
@pytest.mark.parametrize("failure", ["timeout", "rejected"])
async def test_watch_setup_failure_identifies_rpc_and_failure_type(rpc, stage, failure, caplog):
    caplog.set_level("WARNING", logger="gothic_ai.bodypilot")
    async with running() as rig:
        if failure == "timeout":
            rig.bridge.response_delays[rpc] = 0.300
        else:
            rig.bridge.request_rejections[rpc] = deque([f"fake {rpc} rejected"])
        rig.roster(activity="fight", state="ZS_ATTACK")
        await eventually(lambda: rig.bridge.commands(rpc))
        # The first reply remains delayed, while an unsubscribe may acknowledge promptly.
        rig.bridge.response_delays.clear()
        await eventually(lambda: rig.pilot.last_summary is not None)
        await eventually(lambda: not rig.bridge._response_tasks)
        summary = rig.pilot.last_summary
        suffix = "timeout" if failure == "timeout" else "failed"
        assert summary.reason == f"{stage}_{suffix}"
        assert summary.lease is None and summary.acquire_requests == 0
        assert not summary.release_confirmed
        assert summary.watch_removed == (rpc == "body_watch")
        assert not rig.bridge.commands("body_acquire") and not rig.policy.requests
        watches = [command["npcs"] for command in rig.bridge.commands("body_watch")]
        assert watches == ([["NPC"], []] if rpc == "body_watch" else [])
        assert rig.bridge._watched_npc is None
        assert not rig.pilot._watched and not rig.pilot._watch_ready

        records = [record for record in caplog.records if "watch setup failed stage=" in record.message]
        assert len(records) == 1
        diagnostic = records[0].message
        assert f"stage={stage} rpc={rpc} npc=NPC" in diagnostic
        assert f"error_type={'TimeoutError' if failure == 'timeout' else 'GameError'}" in diagnostic
        assert "error=" in diagnostic and "elapsed_ms=" in diagnostic
        assert records[0].args[-1] >= 0
        if failure == "rejected":
            assert f"fake {rpc} rejected" in diagnostic


async def test_watch_timeout_clears_applied_subscription_and_ignores_late_replies():
    async with running() as rig:
        rig.bridge.response_delays["body_watch"] = 0.300
        rig.roster(activity="fight", state="ZS_ATTACK")
        await eventually(lambda: rig.bridge._watched_npc == "NPC")
        # The bridge applies the watch immediately, before its delayed acknowledgement.
        rig.bridge.emit(fighter())
        await eventually(lambda: any(not cmd["npcs"] for cmd in rig.bridge.commands("body_watch")))
        await asyncio.wait_for(rig.pilot.aclose(), 1.0)
        # Both acknowledgements arrive after their request deadlines. Neither
        # may revive a watch or allow the queued observation to acquire a body.
        await eventually(lambda: not rig.bridge._response_tasks)
        assert [cmd["npcs"] for cmd in rig.bridge.commands("body_watch")] == [["NPC"], []]
        assert rig.bridge._watched_npc is None
        assert rig.pilot.npc is None
        assert not rig.pilot._watched and not rig.pilot._watch_ready
        assert not rig.bridge.commands("body_acquire")
        assert not rig.bridge.commands("body_action")
        assert not rig.policy.requests
        summary = rig.pilot.last_summary
        assert summary.reason == "body_watch_timeout" and summary.lease is None
        assert summary.fight_seconds is None
        assert not summary.release_confirmed and summary.release_seconds is None
        assert not summary.watch_removed


@pytest.mark.parametrize("late_count", [1, 3])
async def test_late_step_replies_do_not_act_or_steal_the_next_request_and_timely_reply_resets(late_count):
    async with running() as rig:
        await rig.active()
        rig.policy.step_delays.extend([0.120] * late_count)
        # If an old reply were matched to a new row, this invalid action would
        # terminate the pilot. Only the timely reply authorizes the legal action.
        rig.policy.step_response_actions.extend([11] * late_count + [1])
        for count, seq in enumerate(range(4, 4 + late_count), 1):
            rig.bridge.emit(fighter(seq, owned=True))
            await eventually(lambda count=count: rig.pilot._policy_timeouts == count)
            assert rig.pilot._policy_timeouts_consecutive == count
            assert rig.link.body_owned("NPC")
            assert len(rig.bridge.commands("body_action")) == 1
            assert not rig.bridge.commands("body_release")
        fresh_seq = 4 + late_count
        rig.bridge.emit(fighter(fresh_seq, owned=True))
        await eventually(lambda: rig.pilot._decisions == 2)
        assert rig.pilot._policy_timeouts_consecutive == 0
        assert rig.pilot._policy_watchdog_task is None
        assert not rig.pilot.client.pending
        assert [cmd["observation_seq"] for cmd in rig.bridge.commands("body_action")] == [3, fresh_seq]

        # A later isolated timeout starts a new streak rather than exhausting the
        # earlier one. A memory-only timely reply also resets that streak.
        rig.policy.step_delays.append(0.120)
        rig.policy.step_response_actions.extend([11, 1])
        rig.bridge.emit(fighter(fresh_seq + 1, owned=True))
        await eventually(lambda: rig.pilot._policy_timeouts == late_count + 1)
        assert rig.pilot._policy_timeouts_consecutive == 1
        rig.bridge.emit(fighter(fresh_seq + 2, owned=True, can_decide=False))
        await eventually(lambda: rig.pilot._policy_timeouts_consecutive == 0)
        assert len(rig.bridge.commands("body_action")) == 2
        row = fighter(fresh_seq + 3, owned=True)
        row["self"]["down"] = True
        rig.bridge.emit(row)
        await eventually(lambda: rig.pilot.last_summary is not None)
        summary = rig.pilot.last_summary
        assert summary.reason == "npc_down" and summary.decisions == 2
        assert summary.policy_timeouts == late_count + 1 and summary.policy_timeouts_consecutive == 0
        assert summary.policy_timeout_wait_p95_ms is not None
        assert summary.policy_response_silence_ms is None
        assert summary.release_confirmed and summary.watch_removed
        assert rig.pilot._policy_timeouts == rig.pilot._policy_timeouts_consecutive == 0


async def test_five_consecutive_late_steps_end_and_restore_native_before_any_late_action(caplog):
    caplog.set_level("INFO", logger="gothic_ai.bodypilot")
    async with running() as rig:
        await rig.active()
        rig.policy.step_delays.extend([0.120] * 5)
        rig.policy.step_response_actions.extend([11] * 5)
        for count, seq in enumerate(range(4, 8), 1):
            rig.bridge.emit(fighter(seq, owned=True))
            await eventually(lambda count=count: rig.pilot._policy_timeouts_consecutive == count)
            assert not rig.bridge.commands("body_release")
        rig.bridge.emit(fighter(8, owned=True))
        await rig.ended()
        await eventually(lambda: rig.pilot.last_summary is not None)
        summary = rig.pilot.last_summary
        assert summary.reason == "policy_timeout" and summary.decisions == 1
        assert summary.policy_timeouts == summary.policy_timeouts_consecutive == 5
        assert summary.policy_timeout_wait_p95_ms is not None
        assert summary.release_confirmed and summary.watch_removed
        assert len(rig.bridge.commands("body_action")) == 1
        assert "policy_timeouts=5 policy_timeouts_consecutive=5" in caplog.text


async def test_one_second_without_policy_response_ends_even_without_more_observations():
    async with running() as rig:
        await rig.active()
        rig.policy.step_mode = "timeout"
        rig.bridge.emit(fighter(4, owned=True))
        await eventually(lambda: rig.pilot._policy_timeouts == 1)
        assert rig.link.body_owned("NPC") and not rig.bridge.commands("body_release")
        # The fake game sends no more rows, and its injected game clock never
        # advances. The service-silence guard must still use wall time.
        await rig.ended()
        await eventually(lambda: rig.pilot.last_summary is not None)
        summary = rig.pilot.last_summary
        assert summary.reason == "policy_timeout"
        assert summary.policy_timeouts == summary.policy_timeouts_consecutive == 1
        assert summary.policy_response_silence_ms >= 1000
        assert len(rig.policy.commands("step")) == 2
        assert len(rig.bridge.commands("body_action")) == 1
        assert summary.release_confirmed and summary.watch_removed


@pytest.mark.parametrize("mode", ["fallback", "error", "disconnect"])
async def test_service_failure_releases_to_native_and_does_not_block_loop(mode):
    async with running() as rig:
        await rig.active()
        rig.policy.step_mode = mode
        rig.bridge.emit(fighter(4, owned=True))
        await eventually(lambda: len(rig.policy.commands("step")) == 2)
        # A hung service must not hold the WebSocket receive loop or other server tasks.
        marker = asyncio.Event()
        asyncio.get_running_loop().call_soon(marker.set)
        await asyncio.wait_for(marker.wait(), 0.02)
        await rig.ended(policy=mode != "disconnect")
        assert len(rig.bridge.commands("body_action")) == 1


@pytest.mark.parametrize("mode", ["error", "timeout"])
async def test_reset_failure_restores_native_before_any_step(mode):
    async with running() as rig:
        rig.policy.reset_mode = mode
        await rig.watch()
        rig.bridge.emit(player_row(), fighter())
        await rig.ended()
        assert len(rig.policy.commands("reset")) == 1
        assert not rig.policy.commands("step")
        assert not rig.bridge.commands("body_action")


async def test_connection_failure_restores_native_before_first_policy_step():
    async with running() as rig:
        # A reserved local TCP port with no listening socket fails without an external service.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(("localhost", 0))
            rig.pilot.url = f"tcp://localhost:{reserved.getsockname()[1]}"
            await rig.watch()
            rig.bridge.emit(player_row(), fighter())
            await rig.ended(policy=False)
        assert not rig.policy.requests and not rig.bridge.commands("body_action")


async def test_five_minute_combat_limit_ends_episode():
    async with running() as rig:
        await rig.active()
        rig.clock.advance(300.01)
        rig.bridge.emit(fighter(4, owned=True, t_game_ms=300_151))
        await rig.ended()


@pytest.mark.parametrize("end", ["fallback", "error", "fight_timeout"])
async def test_native_remainder_of_same_combat_is_not_reacquired(end):
    async with running() as rig:
        await rig.active()
        if end == "fight_timeout":
            rig.clock.advance(300.01)
        else:
            rig.policy.step_mode = end
        rig.bridge.emit(fighter(4, owned=True))
        await rig.ended()
        await eventually(lambda: rig.pilot.npc is None)
        # The native bridge emits another attack for every subsequent hero blow.
        for kind in ("attack", "threatened", "weapon_drawn", "npc_attack"):
            rig.combat(kind=kind)
        rig.bridge.emit(fighter(5))
        await asyncio.sleep(0.04)
        assert len(rig.bridge.commands("body_acquire")) == 1
        assert len(rig.bridge.commands("body_watch")) == 2
        assert not rig.link.body_leases


async def test_repeated_native_attack_roster_does_not_reacquire_after_fallback():
    async with running() as rig:
        await rig.active()
        rig.policy.step_mode = "fallback"
        rig.bridge.emit(fighter(4, owned=True))
        await rig.ended()
        await eventually(lambda: rig.pilot.npc is None)
        for seq in range(5, 8):
            rig.roster()
            rig.bridge.emit(fighter(seq))
            await asyncio.sleep(0.01)
        assert len(rig.bridge.commands("body_watch")) == 2
        assert len(rig.bridge.commands("body_acquire")) == 1


@pytest.mark.parametrize("activity,state", [("routine", "ZS_SIT_BENCH"), ("down", "ZS_UNCONSCIOUS")])
async def test_new_native_attack_roster_rearms_after_the_previous_fight_ends(activity, state):
    async with running() as rig:
        await rig.active()
        rig.policy.step_mode = "fallback"
        rig.bridge.emit(fighter(4, owned=True))
        await rig.ended()
        await eventually(lambda: rig.pilot.npc is None)
        rig.roster(activity=activity, state=state)
        rig.roster(activity="fight", state="ZS_ATTACK")
        await eventually(lambda: len(rig.bridge.commands("body_watch")) == 3)
        rig.bridge.emit(fighter(5))
        await eventually(lambda: len(rig.bridge.commands("body_acquire")) == 2)
        assert rig.link.body_leases


async def test_fallback_npc_can_be_acquired_in_a_new_combat_after_returning_to_routine():
    async with running() as rig:
        await rig.active()
        rig.policy.step_mode = "fallback"
        rig.bridge.emit(fighter(4, owned=True))
        await rig.ended()
        await eventually(lambda: rig.pilot.npc is None)
        seq = 4
        for temporary_roster in (
            {"include": False},
            {"activity": "dialog"},
            {"activity": "attached"},
            {"activity": "routine", "state": "ZS_ATTACK"},
        ):
            # Absence, a takeover, and a transient activity label do not prove
            # that the native fight ended, even if another attack follows.
            rig.roster(**temporary_roster)
            rig.roster()
            rig.combat()
            seq += 1
            rig.bridge.emit(fighter(seq))
            await asyncio.sleep(0.01)
            assert len(rig.bridge.commands("body_acquire")) == 1
            assert len(rig.bridge.commands("body_watch")) == 2
        rig.roster(activity="routine", state="ZS_SIT_BENCH")
        rig.roster()
        rig.combat()
        await eventually(lambda: len(rig.bridge.commands("body_watch")) == 3)
        rig.bridge.emit(fighter(seq + 1))
        await eventually(lambda: len(rig.bridge.commands("body_acquire")) == 2)
        assert rig.link.body_leases


async def test_fallback_for_one_npc_does_not_block_a_different_npc():
    async with running() as rig:
        await rig.active()
        rig.policy.step_mode = "fallback"
        rig.bridge.emit(fighter(4, owned=True))
        await rig.ended()
        await eventually(lambda: rig.pilot.npc is None)
        rig.roster(extra=[RosterEntry(
            id="OTHER", name="Other Fighter", guild=1, distance=250.0,
            in_view=True, activity="fight", state="ZS_ATTACK",
        )])
        rig.combat("OTHER")
        await eventually(lambda: len(rig.bridge.commands("body_watch")) == 3)
        rig.bridge.emit(fighter(5, entity=4))
        await eventually(lambda: len(rig.bridge.commands("body_acquire")) == 2)
        assert rig.bridge.commands("body_watch")[-1]["npcs"] == ["OTHER"]
        assert rig.bridge.commands("body_acquire")[-1]["entity_id"] == 4


@pytest.mark.parametrize("denied", ["monster", "unknown", "companion", "twohand", "dialog", "attached"])
async def test_disallowed_npc_is_never_acquired(denied):
    async with running(companion=denied == "companion") as rig:
        rig.roster(
            guild=17 if denied == "monster" else 1,
            activity=denied if denied in {"dialog", "attached"} else "fight",
            include=denied != "unknown",
        )
        rig.combat()
        await asyncio.sleep(0.01)
        row = fighter()
        if denied == "twohand":
            row["self"]["weapon_state"] = 3
        rig.bridge.emit(player_row(), row)
        await asyncio.sleep(0.01)
        assert not rig.bridge.commands("body_acquire")
        assert not rig.policy.requests


@pytest.mark.parametrize("wrong_target", ["hidden", "other_npc"])
async def test_acquisition_needs_a_visible_hero_target(wrong_target):
    async with running() as rig:
        await rig.watch()
        own = fighter()
        if wrong_target == "hidden":
            own["target"]["visible"] = False
            own["target"]["current_valid"] = False
            for key in own["target"]:
                if key not in {"entity_id", "present", "visible", "current_valid"}:
                    own["target"][key] = None
        else:
            rig.bridge.target_is_hero = False
            own["target"]["entity_id"] = 8
            own["goal"]["target_id"] = 8
        rig.bridge.emit(player_row(), own)
        await asyncio.sleep(0.02)
        assert not rig.bridge.commands("body_acquire")


async def test_second_fight_does_not_change_watch_or_acquire_another_npc():
    async with running() as rig:
        await rig.active()
        rig.roster(extra=[RosterEntry(
            id="OTHER", name="Other Fighter", guild=1, distance=250.0,
            in_view=True, activity="fight", state="ZS_ATTACK",
        )])
        rig.combat("OTHER")
        rig.bridge.emit(fighter(4, owned=True, entity=4))
        await asyncio.sleep(0.02)
        assert len(rig.bridge.commands("body_acquire")) == 1
        assert len(rig.bridge.commands("body_watch")) == 1
        assert len(rig.policy.commands("step")) == 1


async def test_heartbeat_keeps_active_lease_alive_and_close_restores_native(caplog):
    caplog.set_level("INFO")
    async with running() as rig:
        await rig.active()
        await eventually(lambda: rig.bridge.commands("body_heartbeat"))
        assert rig.link.body_leases
        await rig.pilot.aclose()
        await rig.ended()
        assert "p50" in caplog.text and "p95" in caplog.text


async def test_no_decision_is_sent_after_takeover_during_slow_policy_reply():
    async with running() as rig:
        await rig.active()
        rig.policy.step_waiter = asyncio.Event()
        rig.bridge.emit(fighter(4, owned=True))
        await eventually(lambda: len(rig.policy.commands("step")) == 2)
        rig.link._dispatch(Attached(type="attached", npc="NPC", name="Fighter"))
        rig.policy.step_waiter.set()
        await rig.ended()
        assert len(rig.bridge.commands("body_action")) == 1
