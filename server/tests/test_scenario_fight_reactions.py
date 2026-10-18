from gothic_ai.scenario_fight_reactions import CombatTrace, combat_shouts


def row(symbol=10, *, t=100, hp=100, attack=False, weapon=0, pos=(1.0, 2.0, 3.0), target=20):
    return {
        "script_symbol": symbol,
        "entity_id": symbol + 100,
        "t_game_ms": t,
        "self": {
            "hp": hp,
            "attack_anim": attack,
            "weapon_state": weapon,
            "position_world_cm": pos,
        },
        "target": {"entity_id": target},
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


def test_native_voice_requires_successful_audio_playback(tmp_path):
    game_log = tmp_path / "game.log"
    game_log.write_text(
        "[ai] BANDIT combat shout $WEAPONDOWN audio_ms=0 game_ms=100\n"
        "[ai] OTHER combat shout $WEAPONDOWN audio_ms=500 game_ms=200\n"
        "[ai] BANDIT combat shout $WEAPONDOWN audio_ms=1200 game_ms=300\n"
    )
    assert combat_shouts(game_log, "BANDIT") == [300]
    assert combat_shouts(tmp_path / "absent.log", "BANDIT") == []
