import asyncio
from types import SimpleNamespace

import pytest

from gothic_ai.game import GameError
from gothic_ai.protocol import ActionCmd, ActionResult, DebugCmd
from gothic_ai.scenario_fight_reactions import (
    CombatTrace,
    combat_shouts,
    obstacles_clear,
    stop_and_hit,
    wait_hit_geometry,
)


def row(
    symbol=10, *, t=100, hp=100, attack=False, prehit=False, weapon=0,
    pos=(1.0, 2.0, 3.0), target=20, events=(),
):
    return {
        "script_symbol": symbol,
        "entity_id": symbol + 100,
        "t_game_ms": t,
        "self": {
            "hp": hp,
            "attack_anim": attack,
            "prehit": prehit,
            "weapon_state": weapon,
            "interruptible": True,
            "position_world_cm": pos,
        },
        "target": {
            "entity_id": target,
            "visible": True,
            "melee_line_clear": True,
            "in_w_range": True,
            "focus_30": True,
        },
        "local_obstacles": {
            "ray_range_cm": 300.0,
            "rays": [
                {"angle_rad": i * 0.392699, "distance_cm": 300.0, "hit": False}
                for i in range(16)
            ],
        },
        "events": list(events),
    }


def test_only_real_attack_animation_edges_count_as_strikes():
    trace = CombatTrace({10: "bandit"})
    trace.body({"observations": [row(), row(t=150, attack=True), row(t=200, attack=True)]})
    trace.body({"observations": [row(t=250), row(t=300, attack=True)]})
    assert trace.strikes == {"bandit": [150, 300]}


def test_damage_is_hp_loss_after_baseline_with_game_timestamp():
    trace = CombatTrace({10: "bandit"})
    trace.body({"observations": [row(t=100, hp=70), row(t=200, hp=60), row(t=300, hp=60)]})
    trace.body({"observations": [row(t=400, hp=100), row(t=500, hp=95)]})
    assert trace.damage == {"bandit": [(200, 10), (500, 5)]}
    assert len(trace.damage_wall["bandit"]) == 2


def test_unknown_body_cannot_supply_readiness_or_damage():
    trace = CombatTrace({10: "bandit"})
    trace.body({"observations": [row(symbol=99, hp=0, attack=True, weapon=2)]})
    assert not trace.hp
    assert not trace.strikes
    assert not trace.armed


def test_draw_time_is_first_actual_armed_state_and_distance_uses_both_bodies():
    trace = CombatTrace({10: "bandit", 11: "companion"})
    assert trace.distance("bandit", "companion") is None
    trace.body({"observations": [row(t=100), row(t=200, weapon=2), row(t=250, weapon=2)]})
    trace.body({"observations": [row(symbol=11, pos=(1.0, 2.0, 303.0))]})
    assert trace.armed == {"bandit": 200}
    assert trace.distance("bandit", "companion") == 300.0


def test_post_hit_strike_must_target_companion_at_the_time_of_its_animation():
    trace = CombatTrace({10: "bandit", 11: "companion"})
    trace.body({"observations": [row(symbol=11), row(t=150, attack=True, target=111)]})
    trace.body({"observations": [row(t=200), row(t=250, attack=True, target=20)]})
    assert trace.strike_after("bandit", "companion", 200) is None
    trace.body({"observations": [row(t=300), row(t=350, attack=True, target=111)]})
    assert trace.strike_after("bandit", "companion", 200) == 350


def test_window_interference_excludes_hit_and_includes_deadline():
    trace = CombatTrace({11: "companion"})

    def contact(event_id, at):
        return {
            "event_id": event_id, "source_entity_id": 111, "source_game_ms": at,
            "perspective": "self", "code": 3,
        }

    trace.body({"observations": [
        row(symbol=11, t=90),
        row(symbol=11, t=100, attack=True, events=[contact(1, 100)]),
        row(symbol=11, t=110),
        row(symbol=11, t=120, attack=True, events=[contact(2, 120)]),
        row(symbol=11, t=190),
        row(symbol=11, t=200, attack=True, events=[contact(3, 200)]),
        row(symbol=11, t=201),
        row(symbol=11, t=210, attack=True, events=[contact(4, 210)]),
    ]})

    assert trace.window_interference("companion", 100, 200) == ([120, 200], [120, 200])


def test_window_interference_ends_at_first_defender_strike():
    trace = CombatTrace({10: "bandit", 11: "companion"})

    def companion_strike(at):
        return row(symbol=11, t=at, attack=True, events=[{
            "event_id": at, "source_entity_id": 111, "source_game_ms": at,
            "perspective": "self", "code": 3,
        }])

    trace.body({"observations": [
        row(t=100), row(symbol=11, t=100),
        companion_strike(130), row(symbol=11, t=140),
        row(t=150, attack=True, target=111), companion_strike(150),
        row(symbol=11, t=160), companion_strike(170),
    ]})
    defender_strike = trace.strike_after("bandit", "companion", 100)
    assert defender_strike == 150
    assert trace.window_interference("companion", 100, defender_strike) == ([130, 150], [130, 150])


def test_window_interference_uses_contact_source_time_regardless_of_delivered_target():
    trace = CombatTrace({10: "bandit", 11: "companion"})
    trace.body({"observations": [row(t=100), row(symbol=11, t=100, target=110)]})
    delayed_contact = {
        "event_id": 1, "source_entity_id": 111, "source_game_ms": 150,
        "perspective": "self", "code": 3,
    }
    trace.body({"observations": [row(symbol=11, t=350, target=999, events=[delayed_contact])]})

    assert trace.first_contact("companion", "bandit") is None
    assert trace.window_interference("companion", 100, 200) == ([], [150])
    assert trace.window_interference("companion", 200, 400) == ([], [])


def test_window_interference_is_empty_for_quiet_companion():
    trace = CombatTrace({10: "bandit", 11: "companion"})
    trace.body({"observations": [row(t=100), row(symbol=11, t=100)]})
    trace.body({"observations": [
        row(t=150, attack=True, target=111, events=[{
            "event_id": 1, "source_entity_id": 110, "source_game_ms": 150,
            "perspective": "self", "code": 3,
        }]),
        row(symbol=11, t=150, events=[{
            "event_id": 2, "source_entity_id": 111, "source_game_ms": 150,
            "perspective": "self", "code": 9,
        }]),
    ]})

    assert trace.window_interference("companion", 100, 200) == ([], [])


def ready_trace(*, attacker_attack=False, attacker_prehit=False, defender_attack=False):
    trace = CombatTrace({10: "bandit", 11: "companion"})
    trace.body({"observations": [
        row(target=111, attack=defender_attack),
        row(symbol=11, target=110, attack=attacker_attack, prehit=attacker_prehit),
    ]})
    return trace


def test_hit_ready_allows_defender_strike_already_in_flight():
    trace = ready_trace(defender_attack=True)
    assert trace.hit_ready("companion", "bandit")


@pytest.mark.parametrize("attacker_state", [{"attacker_attack": True}, {"attacker_prehit": True}])
def test_hit_ready_rejects_attacker_attack_or_prehit(attacker_state):
    trace = ready_trace(**attacker_state)
    assert not trace.hit_ready("companion", "bandit")


@pytest.mark.parametrize("field,value", [
    ("entity_id", 999),
    ("visible", False),
    ("melee_line_clear", False),
    ("in_w_range", False),
    ("focus_30", False),
])
def test_hit_ready_requires_current_defender_target_and_open_melee_geometry(field, value):
    trace = ready_trace()
    trace.latest_rows["bandit"]["target"][field] = value
    assert not trace.hit_ready("companion", "bandit")


@pytest.mark.parametrize("who,section,field", [
    ("companion", "self", "attack_anim"),
    ("companion", "self", "prehit"),
    ("bandit", "target", "entity_id"),
    ("bandit", "target", "visible"),
    ("bandit", "target", "melee_line_clear"),
    ("bandit", "target", "in_w_range"),
    ("bandit", "target", "focus_30"),
])
def test_hit_ready_rejects_missing_readiness_fields(who, section, field):
    trace = ready_trace()
    del trace.latest_rows[who][section][field]
    assert not trace.hit_ready("companion", "bandit")


@pytest.mark.parametrize("missing", ["companion", "bandit"])
def test_hit_ready_rejects_missing_body(missing):
    trace = ready_trace()
    del trace.latest_rows[missing]
    assert not trace.hit_ready("companion", "bandit")


@pytest.mark.parametrize("who", ["companion", "bandit"])
def test_hit_ready_requires_clear_obstacles_for_both_bodies(who):
    trace = ready_trace()
    trace.latest_rows[who]["local_obstacles"]["rays"][7].update(hit=True, distance_cm=119.9)
    assert not trace.hit_ready("companion", "bandit")


@pytest.mark.parametrize("clearance", [120.0, 300.0])
def test_obstacles_clear_includes_clearance_boundary_and_ignores_no_hit_distance(clearance):
    observation = row()
    ray = observation["local_obstacles"]["rays"][0]
    ray.update(hit=True, distance_cm=clearance)
    assert obstacles_clear(observation, clearance)
    ray["distance_cm"] = clearance - 0.1
    assert not obstacles_clear(observation, clearance)
    ray.update(hit=False, distance_cm=0.0)
    assert obstacles_clear(observation, clearance)


@pytest.mark.parametrize("clearance", [120.0, 300.0])
@pytest.mark.parametrize("invalid_scan", ["missing", "short_range", "fewer_rays", "extra_rays"])
def test_obstacles_clear_requires_complete_scan_covering_clearance(clearance, invalid_scan):
    observation = row()
    scan = observation["local_obstacles"]
    if invalid_scan == "missing":
        del observation["local_obstacles"]
    elif invalid_scan == "short_range":
        scan["ray_range_cm"] = clearance - 0.1
    elif invalid_scan == "fewer_rays":
        scan["rays"].pop()
    else:
        scan["rays"].append(scan["rays"][0].copy())
    assert not obstacles_clear(observation, clearance)


async def test_wait_hit_geometry_accepts_current_ready_body_without_listener():
    trace = ready_trace(defender_attack=True)
    link = SimpleNamespace(raw_body_listeners=[trace.body])
    assert await wait_hit_geometry(link, trace, "companion", "bandit")
    assert link.raw_body_listeners == [trace.body]


async def test_wait_hit_geometry_rechecks_transient_geometry_until_current_body_is_ready():
    trace = ready_trace(attacker_attack=True, defender_attack=True)
    link = SimpleNamespace(raw_body_listeners=[trace.body])
    waiting = asyncio.create_task(wait_hit_geometry(link, trace, "companion", "bandit", timeout=1))
    await asyncio.sleep(0)

    for packet in (
        {"observations": [row(symbol=11, t=150)]},
        {"observations": [row(symbol=11, t=160, prehit=True)]},
    ):
        for listener in tuple(link.raw_body_listeners):
            listener(packet)

    await asyncio.sleep(0)
    assert not waiting.done()
    packet = {"observations": [row(symbol=11, t=170)]}
    for listener in tuple(link.raw_body_listeners):
        listener(packet)

    await asyncio.sleep(0)
    assert waiting.done()
    assert await waiting
    assert link.raw_body_listeners == [trace.body]


async def test_wait_hit_geometry_unknown_bodies_timeout_and_remove_listener():
    trace = CombatTrace({10: "bandit", 11: "companion"})
    link = SimpleNamespace(raw_body_listeners=[trace.body])
    waiting = asyncio.create_task(wait_hit_geometry(link, trace, "companion", "bandit", timeout=0.01))
    await asyncio.sleep(0)
    packet = {"observations": [row(symbol=11)]}
    for listener in tuple(link.raw_body_listeners):
        listener(packet)

    assert not await waiting
    assert link.raw_body_listeners == [trace.body]


async def test_wait_hit_geometry_cancellation_removes_listener():
    trace = ready_trace(attacker_prehit=True)
    link = SimpleNamespace(raw_body_listeners=[trace.body])
    waiting = asyncio.create_task(wait_hit_geometry(link, trace, "companion", "bandit"))
    await asyncio.sleep(0)
    assert len(link.raw_body_listeners) == 2
    waiting.cancel()

    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert link.raw_body_listeners == [trace.body]


class StopHitLink:
    def __init__(self, *, fail_hit=False):
        self.listeners = []
        self.sent = []
        self.fail_hit = fail_hit
        self.proof = {"attacker": "companion", "victim": "bandit", "game_ms": 100}

    def _next_id(self, prefix):
        return f"{prefix}-{len(self.sent)}"

    async def send(self, command):
        assert isinstance(command, ActionCmd)
        self.sent.append(command)

    async def _request(self, command, timeout):
        assert len(self.sent) == 1
        stop = self.sent[0]
        assert isinstance(stop, ActionCmd) and stop.action == "stop"
        assert isinstance(command, DebugCmd)
        self.sent.append(command)
        if self.fail_hit:
            raise GameError("npc_hit: weapon not drawn")
        receipt = ActionResult(type="action_result", id=stop.id, npc=stop.npc, status="done")
        for listener in tuple(self.listeners):
            listener(receipt)
        return SimpleNamespace(value=self.proof)


async def test_stop_and_hit_sends_both_before_waiting_for_stop_receipt():
    link = StopHitLink()
    received = []
    existing = received.append
    link.listeners.append(existing)

    proof = await asyncio.wait_for(stop_and_hit(link, "companion", "bandit"), timeout=0.1)

    assert [(command.type, command.npc) for command in link.sent] == [
        ("action", "companion"), ("debug", "bandit"),
    ]
    assert link.sent[0].action == "stop"
    assert link.sent[1].cmd == "npc_hit" and link.sent[1].by == "companion"
    assert proof is link.proof
    assert link.listeners == [existing]


async def test_stop_and_hit_removes_stop_listener_when_hit_request_fails():
    link = StopHitLink(fail_hit=True)
    received = []
    existing = received.append
    link.listeners.append(existing)

    with pytest.raises(GameError, match="weapon not drawn"):
        await stop_and_hit(link, "companion", "bandit")

    assert len(link.sent) == 2
    assert link.listeners == [existing]


def test_native_voice_requires_successful_audio_playback(tmp_path):
    game_log = tmp_path / "game.log"
    game_log.write_text(
        "[ai] BANDIT combat shout $WEAPONDOWN audio_ms=0 game_ms=100\n"
        "[ai] OTHER combat shout $WEAPONDOWN audio_ms=500 game_ms=200\n"
        "[ai] BANDIT combat shout $WEAPONDOWN audio_ms=1200 game_ms=300\n"
    )
    assert combat_shouts(game_log, "BANDIT") == [300]
    assert combat_shouts(tmp_path / "absent.log", "BANDIT") == []


def test_native_contacts_deduplicate_keep_victim_and_include_absorbed_bites():
    trace = CombatTrace({10: "wolf", 11: "companion", 12: "PLAYER"})
    trace.body({"observations": [row(symbol=10), row(symbol=11), row(symbol=12)]})
    wolf_contact = {
        "event_id": 7, "source_entity_id": 110, "source_game_ms": 200,
        "perspective": "self", "code": 3,
    }
    trace.body({"observations": [row(t=200, target=111, events=[wolf_contact])]})
    trace.body({"observations": [row(t=250, target=112, events=[wolf_contact])]})
    assert trace.contacts["wolf"] == [(200, 111)]
    assert trace.first_contact("wolf", "companion") == 200
    assert trace.first_contact("wolf", "PLAYER") is None

    wolf_received = {
        "event_id": 8, "source_entity_id": 110, "source_game_ms": 250,
        "perspective": "self", "code": 9,
    }
    trace.body({"observations": [
        row(symbol=11, t=275, target=0),
        row(t=275, hp=95, events=[wolf_received]),
    ]})
    assert trace.first_contact("companion", "wolf") is None
    assert trace.first_received_contact("wolf") == 250

    absorbed_bite = {
        "event_id": 9, "source_entity_id": 112, "source_game_ms": 300,
        "perspective": "self", "code": 10,
    }
    packet = {"observations": [row(symbol=12, t=350, hp=100, events=[absorbed_bite])]}
    trace.body(packet)
    trace.body(packet)
    assert not trace.damage.get("PLAYER")
    assert trace.received_contacts["PLAYER"] == [(300, 10)]
    assert trace.first_received_contact("PLAYER") == 300
