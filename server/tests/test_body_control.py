import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from gothic_ai.bodies import Bodies
from gothic_ai.game import GameError, GameLink
from gothic_ai.protocol import (
    BODY_CONTROL_PROTOCOL,
    PROTOCOL_VERSION,
    BodyAcquireCmd,
    BodyActionCmd,
    BodyControlEvents,
    BodyControlResult,
    BodyControlStatus,
    BodyDisconnectCmd,
    Loading,
    Result,
    parse_game_message,
)
from tests.test_body_observations import message, observation, packet


def test_body_control_protocol_is_v41():
    assert PROTOCOL_VERSION >= BODY_CONTROL_PROTOCOL == 41


def receipt(**updates):
    value = {
        "type": "body_control_result", "accepted": True, "duplicate": False, "reason": "accepted",
        "session_epoch": 1, "world_epoch": 1, "entity_id": 1, "control_generation": 7,
        "goal_version": 1, "target_entity_id": 2, "command_id": 0, "execution_id": 0,
        "owner": "scripted", "release_pending": False,
    }
    value.update(updates)
    return value


def event(event_seq=1, **updates):
    value = receipt()
    for name in ("type", "accepted", "duplicate"):
        value.pop(name)
    value.update(
        event="body_control_event", event_seq=event_seq, phase="ownership", reason="acquired",
        provider="scripted", mode="realtime", t_game_ms=50, sim_step=2, observation_seq=1,
        action_id=0, effective_primitive=0, issued_game_ms=0, ttl_ms=0,
        watchdog_timeout_ms=500, infrastructure_failure=False,
        t_wall_ms=1000, last_heartbeat_wall_ms=1000, pose_identity="idle", segment_identity="",
        segment_started=False, primitive_consumed=False,
        schema_version=1, pose_before="idle", pose_after="idle", release_reason="", remaining_lock_game_ms=0,
    )
    value.update(updates)
    return value


def events(*rows, **updates):
    value = {
        "type": "body_control_events", "session_epoch": 1, "world_epoch": 1,
        "dropped_total": 0, "events": list(rows),
    }
    value.update(updates)
    return BodyControlEvents.model_validate(value)


def visibility_diagnostics(**updates):
    # BodyController::visibilityDiagnosticsJson, included in every live status response.
    value = {
        "type": "body_visibility_diagnostics", "enabled": False, "capture_failed": False,
        "diagnostic_generation": 1, "records_total": 0, "dropped_total": 0,
        "max_records_per_actor": 8192, "sample_period_ms": 50,
        "clock_domain": "linux-clock-monotonic", "journal": "events.jsonl",
        "phase": "visibility_diagnostic",
    }
    value.update(updates)
    return value


def control_status(*actors, **updates):
    value = {
        "type": "body_control_status", "session_epoch": 1, "world_epoch": 1,
        "paused": False, "infrastructure_failure": False, "infrastructure_reason": "",
        "actors": list(actors), "visibility_diagnostics": visibility_diagnostics(),
    }
    value.update(updates)
    return value


class Connection:
    def __init__(self):
        self.sent = []
        self.link = None
        self.reject = False
        self.invalidate_after_response = False
        self.control_status = control_status()

    async def send(self, raw):
        cmd = json.loads(raw)
        self.sent.append(cmd)
        value = receipt(
            accepted=not self.reject, reason="stale_observation" if self.reject else "accepted",
            goal_version=cmd.get("goal_version", 1), command_id=cmd.get("command_id", 0),
        )
        if cmd["type"] == "body_release":
            value.update(reason="release_pending", release_pending=True)
        elif cmd["type"] == "body_control_status":
            value = self.control_status
        self.link._dispatch(Result(type="result", req=cmd["req"], ok=True, value=value))
        if self.invalidate_after_response:
            self.link._dispatch(Loading(type="loading"))


def connected():
    conn = Connection()
    link = conn.link = GameLink(conn)
    link._dispatch(message(packet()))
    return link, conn


def controlled(seq=2, **updates):
    row = observation(seq)
    row.update(control_generation=7, control_allowed=True, can_decide=True, action_mask_valid=True,
               mask_kind="shared_melee_resolver", action_mask=[True] + [False] * 11)
    row["goal"]["version"] = 1
    row.update(updates)
    return row


async def test_adapter_routes_scoped_control_and_snapshot_anchored_commands():
    link, conn = connected()
    own = link.body_latest[1]
    lease = await link.body_acquire("npc", own, "scripted")
    assert link.body_owned("NPC") and conn.sent[-1]["body_control"] is True
    link._dispatch(message(packet(controlled())))
    lease = await link.body_goal(lease, 2, 2)
    assert link.body_leases[1].goal_version == 2
    next_goal = controlled(3)
    next_goal["goal"]["version"] = 2
    link._dispatch(message(packet(next_goal)))
    result = await link.body_action(lease, link.body_latest[1], 0)
    cmd = conn.sent[-1]
    assert result.accepted and not result.duplicate
    assert (cmd["entity_id"], cmd["control_generation"], cmd["goal_version"]) == (1, 7, 2)
    assert (cmd["observation_seq"], cmd["issued_game_ms"], cmd["target_entity_id"]) == (3, 150, 2)
    await link.body_heartbeat(lease)
    assert conn.sent[-1]["type"] == "body_heartbeat"
    released = await link.body_release(lease)
    assert released.release_pending and link.body_owned("NPC")
    link._dispatch(events(event(2, reason="native_restored", release_pending=True)))
    assert not link.body_owned("NPC")


async def test_rejection_does_not_install_lease_or_send_low_priority_act():
    link, conn = connected()
    conn.reject = True
    with pytest.raises(GameError, match="stale_observation"):
        await link.body_acquire("NPC", link.body_latest[1], "scripted")
    assert not link.body_leases
    conn.reject = False
    await link.body_acquire("NPC", link.body_latest[1], "scripted")
    count = len(conn.sent)
    bodies = Bodies(link)
    assert bodies.busy("NPC")
    outcome = await bodies.act("NPC", "go_to_npc", npc="PLAYER")
    assert outcome.status == "rejected" and len(conn.sent) == count


async def test_load_after_acquire_response_cannot_resurrect_invalid_lease():
    link, conn = connected()
    conn.invalidate_after_response = True
    with pytest.raises(GameError, match="invalidated world"):
        await link.body_acquire("NPC", link.body_latest[1], "scripted")
    assert not link.body_leases and not link.body_latest


async def test_loading_invalidates_goal_response_as_well_as_acquisition():
    link, conn = connected()
    lease = await link.body_acquire("NPC", link.body_latest[1], "scripted")
    conn.invalidate_after_response = True
    with pytest.raises(GameError, match="invalidated world"):
        await link.body_goal(lease, 2, 2)
    assert not link.body_leases


async def test_explicit_disconnect_is_scoped_to_lease_and_rejections_leave_owner():
    link, conn = connected()
    lease = await link.body_acquire("NPC", link.body_latest[1], "scripted")
    await link.body_disconnect(lease)
    cmd = conn.sent[-1]
    assert cmd["type"] == "body_disconnect"
    assert tuple(cmd[name] for name in (
        "session_epoch", "world_epoch", "entity_id", "control_generation",
    )) == (1, 1, 1, 7)
    conn.reject = True
    with pytest.raises(GameError, match="stale_observation"):
        await link.body_disconnect(lease)
    assert link.body_owned("NPC")
    with pytest.raises(ValidationError):
        BodyDisconnectCmd(req="bd", provider="scripted")


async def test_authority_is_not_cleared_by_pending_release_or_old_passive_snapshot():
    link, _ = connected()
    lease = await link.body_acquire("NPC", link.body_latest[1], "scripted")
    link._dispatch(message(packet(controlled(control_allowed=False, can_decide=False))))
    assert link.body_leases[1] == lease
    link._dispatch(message(packet(observation(3))))
    assert link.body_leases[1] == lease
    link._dispatch(message(packet(controlled(4, control_generation=8))))
    assert not link.body_leases


async def test_live_status_with_visibility_diagnostics_clears_restored_native_lease():
    link, conn = connected()
    lease = await link.body_acquire("NPC", link.body_latest[1], "scripted")
    link._dispatch(message(packet(observation(3))))
    assert link.body_leases[1] == lease
    actor = receipt(owner="native", control_generation=0)
    for name in ("type", "accepted", "duplicate", "reason"):
        actor.pop(name)
    actor.update(
        provider="", mode="native", t_game_ms=150, observation_seq=3, observed_game_ms=150,
        watchdog_timeout_ms=0, control_allowed=False, can_decide=False, action_mask=[False] * 12,
    )
    conn.control_status = control_status(actor)
    status = await link.body_control_status()
    assert status.visibility_diagnostics is not None
    assert status.visibility_diagnostics.max_records_per_actor == 8192
    assert status.actors[0].owner == "native"
    assert not link.body_leases and not link.body_owned("NPC")


@pytest.mark.parametrize("clock_domain", ["linux-clock-monotonic", "cxx-steady-clock"])
def test_control_events_parse_live_engine_timestamps(clock_domain):
    stream = events(event(1, wall_monotonic_ns=123_000_000, clock_domain=clock_domain))
    assert stream.events[0].wall_monotonic_ns == 123_000_000
    assert stream.events[0].clock_domain == clock_domain


@pytest.mark.parametrize("clock_domain", ["linux-clock-monotonic", "cxx-steady-clock"])
def test_status_parses_live_visibility_diagnostics(clock_domain):
    diagnostics = visibility_diagnostics(clock_domain=clock_domain, enabled=True, records_total=3)
    status = BodyControlStatus.model_validate(control_status(visibility_diagnostics=diagnostics))
    assert status.visibility_diagnostics.model_dump() == diagnostics


@pytest.mark.parametrize("updates", [
    {"enabled": 1}, {"records_total": "0"}, {"dropped_total": -1}, {"unknown": False},
])
def test_visibility_diagnostics_remain_strict(updates):
    with pytest.raises(ValidationError):
        BodyControlStatus.model_validate(control_status(
            visibility_diagnostics=visibility_diagnostics(**updates),
        ))


async def test_client_rejects_observation_from_other_actor_before_transmission():
    link, conn = connected()
    lease = await link.body_acquire("NPC", link.body_latest[1], "scripted")
    other = copy.deepcopy(observation())
    other["entity_id"] = 3
    row = message(packet(other)).packet.observations[0]
    count = len(conn.sent)
    with pytest.raises(GameError, match="different actor or world"):
        await link.body_action(lease, row, 0)
    assert len(conn.sent) == count


async def test_action_needs_post_acquire_snapshot_and_current_goal_before_transmission():
    link, conn = connected()
    lease = await link.body_acquire("NPC", link.body_latest[1], "scripted")
    count = len(conn.sent)
    with pytest.raises(GameError, match="control generation"):
        await link.body_action(lease, link.body_latest[1], 0)
    link._dispatch(message(packet(controlled())))
    lease = await link.body_goal(lease, 2, 2)
    with pytest.raises(GameError, match="older goal"):
        await link.body_action(lease, link.body_latest[1], 0)
    assert len(conn.sent) == count + 1  # only the goal request, neither stale action


async def test_live_adapter_rejects_lockstep_mode_without_sending():
    link, conn = connected()
    with pytest.raises(GameError, match="realtime mode"):
        await link.body_acquire("NPC", link.body_latest[1], "scripted", mode="lockstep")
    assert not conn.sent


def test_shared_mask_permissions_parse_without_weakening_passive_schema():
    own = message(packet(controlled())).packet.observations[0]
    assert own.can_decide and own.action_mask[0] and own.control_generation == 7
    invalid = controlled(action_mask=None)
    with pytest.raises(ValidationError, match="action mask"):
        message(packet(invalid))
    invalid = observation()
    invalid["can_decide"] = True
    with pytest.raises(ValidationError, match="passive"):
        message(packet(invalid))


@pytest.mark.parametrize("name,bad", [
    ("entity_id", True), ("session_epoch", "1"), ("world_epoch", -1),
    ("control_generation", 0), ("goal_version", 0), ("command_id", 0),
    ("observation_seq", 0), ("action_id", 12), ("issued_game_ms", 1.5), ("ttl_ms", 1001),
])
def test_action_commands_reject_coercions_and_invalid_scope(name, bad):
    value = {
        "req": "r1", "entity_id": 1, "session_epoch": 1, "world_epoch": 1, "provider": "scripted",
        "control_generation": 7, "command_id": 1, "goal_version": 1, "target_entity_id": 2,
        "observation_seq": 1, "issued_game_ms": 50, "ttl_ms": 200, "action_id": 0,
    }
    value[name] = bad
    with pytest.raises(ValidationError):
        BodyActionCmd(**value)


@pytest.mark.parametrize("permission", [False, 1, "true"])
def test_acquire_requires_explicit_boolean_permission(permission):
    with pytest.raises(ValidationError):
        BodyAcquireCmd(req="r1", entity_id=1, session_epoch=1, world_epoch=1,
                       provider="scripted", body_control=permission)


def test_event_cache_bounds_and_rejects_stale_epochs_duplicates():
    link, _ = connected()
    received = []
    link.listeners.append(received.append)
    for index in range(1, 514):
        link._dispatch(events(event(index)))
    assert link.body_control_dropped_events == 1
    assert link.pop_body_control_event().event_seq == 2
    count = len(received)
    link._dispatch(events(event(513)))
    link._dispatch(events(event(514, session_epoch=2), session_epoch=2))
    assert len(received) == count
    link._dispatch(Loading(type="loading"))
    assert link.pop_body_control_event() is None and not link.body_leases


def test_event_schema_rejects_mixed_epochs_unordered_or_unbounded_batches():
    for rows in ([event(2), event(1)], [event(world_epoch=2)], [event(i) for i in range(1, 514)]):
        with pytest.raises(ValidationError):
            events(*rows)
    with pytest.raises(ValidationError):
        BodyControlResult.model_validate(receipt(accepted=1))
    parsed = parse_game_message(events(event()).model_dump_json())
    assert isinstance(parsed, BodyControlEvents) and parsed.events[0].phase == "ownership"


def test_actual_native_control_encoder_reaches_strict_models_and_bridge_cache():
    # Common C++ encoder output from dev-ubuntu stage4-v5/wait_300. Provenance records the
    # source/binary/IPC hashes and response IDs. Only the runner's observation envelope was stripped;
    # result/status/events and every observation row are original numeric/structured game data.
    fixture = json.loads((Path(__file__).parent / "fixtures/body_control_native.json").read_text())
    acquire = BodyControlResult.model_validate(fixture["acquire"])
    status = BodyControlStatus.model_validate(fixture["status"])
    stream = parse_game_message(json.dumps(fixture["events"]))
    observed = message(fixture["observations"])
    assert acquire.accepted and acquire.session_epoch > 1
    owner = next(actor for actor in status.actors if actor.entity_id == acquire.entity_id)
    assert owner.owner == "scripted" and owner.control_generation == acquire.control_generation
    owned_rows = [row for row in observed.packet.observations if row.entity_id == acquire.entity_id]
    assert owned_rows and all(row.action_mask_valid for row in owned_rows)
    assert all(row.control_generation == acquire.control_generation for row in owned_rows)
    assert {row.phase for row in stream.events} >= {"accepted", "started"}
    link = GameLink(Connection())
    link._dispatch(observed)
    link._dispatch(stream)
    assert link.body_session_epoch == acquire.session_epoch
    assert link.body_latest[acquire.entity_id].control_generation == acquire.control_generation
    assert link.pop_body_control_event().event_seq == stream.events[0].event_seq
