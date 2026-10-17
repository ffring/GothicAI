"""Exercise the live pilot through the bridge adapter and a torch-free TCP policy."""

import asyncio
import json
import socket
from collections import deque
from contextlib import asynccontextmanager
from itertools import pairwise

import pytest
from websockets.exceptions import ConnectionClosedError

from gothic_ai.bodypilot import BodyPilot
from gothic_ai.game import GameLink
from gothic_ai.protocol import (
    Attached,
    Hello,
    Loading,
    NpcEvent,
    NpcRoster,
    Result,
    RosterEntry,
)
from tests.test_body_control import controlled, event, events, receipt
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
        self.target_is_hero = True
        self.release_pending_polls = 0
        self._released_pending = False
        self.response_delays = {}
        self._response_tasks = set()
        self._roster = {}
        self._watched_npc = None
        self._lease_timeout = 0.0
        self._lease_watchdog = None
        self.lease_expirations = 0

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
        # A result is resolved before GameLink has installed its lease cache.
        await eventually(lambda: self.link.body_owned(npc))
        self._activity(npc, "body")

    def _renew_lease(self):
        if self._lease_watchdog is not None:
            self._lease_watchdog.cancel()
        self._lease_watchdog = asyncio.get_running_loop().call_later(
            self._lease_timeout, self._expire_lease,
        )

    def _expire_lease(self):
        self.lease_expirations += 1
        if self.link.body_leases:
            lease = next(iter(self.link.body_leases.values()))
            self.link._dispatch(events(event(
                1, entity_id=lease.entity_id, reason="native_restored", owner="native",
            )))
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
        if kind == "body_watch":
            assert not self.link.body_leases, "watch must never change with an active lease"
            self._watched_npc = command["npcs"][0] if command["npcs"] else None
            value = {"generation": 1, "session_epoch": 1, "world_epoch": 1}
        elif kind == "story_eval":
            expressions = command["exprs"]
            value = [50 if expr[0] == "sym" else int(self.target_is_hero) for expr in expressions]
        elif kind == "body_events":
            if self._released_pending and self.release_pending_polls <= 0:
                lease = next(iter(self.link.body_leases.values()))
                value = events(event(
                    1, entity_id=lease.entity_id, reason="native_restored", owner="native",
                )).model_dump(mode="json")
                self._released_pending = False
                self._activity(lease.npc, "fight")
            else:
                value = events().model_dump(mode="json")
                if self._released_pending:
                    self.release_pending_polls -= 1
        else:
            value = receipt(
                entity_id=command.get("entity_id", 1),
                session_epoch=command.get("session_epoch", 1),
                world_epoch=command.get("world_epoch", 1),
                control_generation=command.get("control_generation", 7),
                goal_version=command.get("goal_version", 1),
                target_entity_id=command.get("target_entity_id", 2),
                command_id=command.get("command_id", 0),
            )
            if kind == "body_acquire" and self.acquire_rejections:
                value.update(accepted=False, reason=self.acquire_rejections.popleft())
            if kind == "body_acquire" and value["accepted"]:
                self._lease_timeout = command["timeout_ms"] / 1000
                self._renew_lease()
                self._background(self._acquired_roster(self._watched_npc))
            elif kind == "body_heartbeat":
                self._renew_lease()
            if kind == "body_release":
                if self._lease_watchdog is not None:
                    self._lease_watchdog.cancel()
                if self.release_pending_polls:
                    self._released_pending = True
                    value.update(owner="scripted", reason="release_pending", release_pending=True)
                else:
                    value.update(owner="native", reason="native_restored")
                    lease = self.link.body_leases.get(command["entity_id"])
                    if lease is not None:
                        self._activity(lease.npc, "fight")
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
        self.step_waiter = None
        self.step_delay = 0
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
                    if self.reset_mode == "error":
                        result.update(ok=False, reason="reset_error", error="fake reset failure")
                    elif self.reset_mode == "timeout":
                        continue
                    else:
                        result.update(reason="episode_reset", hidden_reset=True)
                elif command == "step":
                    if self.on_step is not None:
                        self.on_step(request)
                    if self.step_delay:
                        await asyncio.sleep(self.step_delay)
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
                        result.update(
                            status="decision" if eligible else "locked",
                            action_id=1 if eligible else None,
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


def fighter(seq=2, *, owned=False, entity=1, **changes):
    row = controlled(seq) if owned else observation(seq, entity=entity)
    if owned:
        row["action_mask"] = [False, True] + [False] * 10
    row["entity_id"] = entity
    row["self"]["weapon_state"] = 2  # WeaponState::W1H; 3 is two-handed.
    row["target"].update(
        present=True, visible=True, current_valid=True,
        position_world_cm=[100.0, 0.0, 450.0],
        relative_position_cm=[0.0, 0.0, 150.0],
        hp=300, hp_max=300, hp_fraction=1.0, down=False,
    )
    row.update(changes)
    return row


def player_row(seq=1):
    row = observation(seq, entity=2)
    row["script_symbol"] = 99
    row["goal"]["kind"] = "native_idle"
    row["goal"]["target_id"] = 0
    row["target"]["entity_id"] = 0
    row["target"]["present"] = False
    return row


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

    async def acquire(self, *, entity=1):
        await self.watch()
        self.bridge.emit(player_row(), fighter(entity=entity))
        await eventually(lambda: self.link.body_leases)

    async def active(self, *, entity=1):
        await self.acquire(entity=entity)
        self.bridge.emit(fighter(3, owned=True, entity=entity))
        await eventually(lambda: self.policy.commands("step"))
        await eventually(lambda: self.bridge.commands("body_action"))

    async def ended(self, *, policy=True):
        await eventually(lambda: self.bridge.commands("body_release"))
        await eventually(lambda: any(not cmd["npcs"] for cmd in self.bridge.commands("body_watch")))
        if policy:
            await eventually(lambda: self.policy.commands("end_episode"))
        assert not self.link.body_leases


@asynccontextmanager
async def running(*, companion=False, unavailable=False):
    bridge, policy, clock = Bridge(), Policy(), Clock()
    server = await asyncio.start_server(policy.handle, host="localhost", port=0, family=socket.AF_INET)
    port = server.sockets[0].getsockname()[1]
    pilot = BodyPilot(
        bridge.link, f"tcp://localhost:{port}", companion=lambda _: companion,
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


async def test_acquire_then_reset_on_first_owned_periodic_row_and_exact_wire_actions():
    async with running() as rig:
        await rig.acquire()
        acquire = rig.bridge.commands("body_acquire")[0]
        assert acquire["mode"] == "realtime" and acquire["provider"] == "neural-body"
        assert acquire["target_entity_id"] == 2
        assert not rig.policy.requests  # The passive acquisition row is outside the policy episode.
        own = fighter(3, owned=True)
        own["snapshot_wall_monotonic_ns"] = 123456789
        own["clock_domain"] = "cxx-steady-clock"
        own["self"]["position_world_cm"] = [101.25, 0.0, 302.5]
        rig.bridge.emit(own)
        await eventually(lambda: rig.bridge.commands("body_action"))
        assert [request["cmd"] for request in rig.policy.requests[:2]] == ["reset", "step"]
        reset, step = rig.policy.requests[:2]
        assert (reset["session_epoch"], reset["world_epoch"], reset["initial_game_ms"]) == (1, 1, 150)
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
        assert not rig.policy.requests
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


async def test_native_attack_roster_needs_the_actual_target_to_be_the_hero():
    async with running() as rig:
        rig.bridge.target_is_hero = False
        rig.roster(activity="fight", state="ZS_ATTACK")
        await eventually(lambda: rig.bridge.commands("body_watch"))
        own = fighter()
        own["target"]["entity_id"] = 8
        own["goal"]["target_id"] = 8
        rig.bridge.emit(player_row(), own)
        await eventually(lambda: len(rig.bridge.commands("story_eval")) == 2)
        await asyncio.sleep(0.01)
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


async def test_reset_waits_for_first_periodic_row_with_the_acquired_identity():
    async with running() as rig:
        await rig.acquire()
        rig.bridge.emit(fighter(3, owned=True, periodic=False, event_only=True))
        await asyncio.sleep(0.01)
        assert not rig.policy.requests
        own = fighter(4, owned=True, can_decide=False)
        rig.bridge.emit(own)
        await eventually(lambda: rig.policy.commands("step"))
        assert rig.policy.commands("reset")[0]["initial_game_ms"] == own["t_game_ms"]
        assert rig.policy.commands("step")[0]["observation"] == own
        assert not rig.bridge.commands("body_action")


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
        assert not rig.policy.commands("reset")
        rig.bridge.emit(fighter(6, owned=True))
        await eventually(lambda: rig.policy.commands("reset"))
        await rig.pilot.aclose()
        assert "unsafe_boundary" in caplog.text and "ai_queue_not_empty" in caplog.text


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


@pytest.mark.parametrize("change", [
    {"down": True}, {"hp": 0}, {"entity_id": 8},
    {"present": False, "visible": False, "current_valid": False},
    {"visible": False, "current_valid": False},
])
async def test_hero_down_dead_or_target_lost_or_changed_ends_episode(change):
    async with running() as rig:
        await rig.active()
        row = fighter(4, owned=True)
        row["target"].update(change)
        if not row["target"]["current_valid"]:
            allowed = {
                "entity_id", "present", "visible", "current_valid", "last_known_position_world_cm",
                "last_known_velocity_cm_s", "last_seen_game_ms", "last_known_age_ms",
            }
            for key in row["target"]:
                if key not in allowed:
                    row["target"][key] = None
        rig.bridge.emit(row)
        await rig.ended()


@pytest.mark.parametrize("takeover", ["native_restored", "lease_lost", "attached", "dialog", "unavailable"])
async def test_native_ownership_or_dialogue_takeover_ends_episode(takeover):
    async with running() as rig:
        await rig.active()
        if takeover == "native_restored":
            rig.link._dispatch(events(event(1, reason="native_restored", owner="native")))
        elif takeover == "lease_lost":
            rig.bridge.emit(fighter(4))
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


@pytest.mark.parametrize("failed_command", ["body_release", "body_events", "body_watch"])
async def test_websocket_closing_during_cleanup_still_closes_policy_and_pilot(failed_command, monkeypatch):
    async with running() as rig:
        await rig.active()
        client = rig.pilot.client
        assert client is not None
        if failed_command == "body_events":
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


@pytest.mark.parametrize("mode", ["fallback", "error", "disconnect", "timeout"])
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
        await rig.acquire()
        rig.policy.reset_mode = mode
        rig.bridge.emit(fighter(3, owned=True))
        await rig.ended()
        assert len(rig.policy.commands("reset")) == 1
        assert not rig.policy.commands("step")
        assert not rig.bridge.commands("body_action")


async def test_connection_failure_restores_native_before_first_policy_step():
    async with running() as rig:
        await rig.acquire()
        # A reserved local TCP port with no listening socket fails without an external service.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
            reserved.bind(("localhost", 0))
            rig.pilot.url = f"tcp://localhost:{reserved.getsockname()[1]}"
            rig.bridge.emit(fighter(3, owned=True))
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
