from gothic_ai.scenario_fight_reactions import CombatTrace, combat_shouts


def row(symbol=10, *, t=100, hp=100, attack=False, weapon=0, pos=(1.0, 2.0, 3.0), target=20, events=()):
    return {
        "script_symbol": symbol,
        "entity_id": symbol + 100,
        "t_game_ms": t,
        "self": {
            "hp": hp,
            "attack_anim": attack,
            "weapon_state": weapon,
            "interruptible": True,
            "position_world_cm": pos,
        },
        "target": {"entity_id": target},
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
