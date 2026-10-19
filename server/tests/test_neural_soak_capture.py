"""The soak denominator describes live opportunities, independently of the pilot."""

from collections import Counter, deque
from types import SimpleNamespace

from gothic_ai.protocol import BodyLease
from gothic_ai.scenarios.neural_soak_capture import CaptureTracker


def rig(*, actor="BANDIT", values=(7, 1, 1, 1, 0)):
    now, events, calls = [0.0], [], []

    async def story_eval(npc, expressions, timeout):
        calls.append((npc, expressions))
        return list(link.values)

    link = SimpleNamespace(body_leases={}, values=values, story_eval=story_eval)
    pilot = SimpleNamespace(
        people={actor: SimpleNamespace(state="ZS_ATTACK", activity="fight")},
        fights={}, summaries=deque(maxlen=32), _releasing={}, max_fights=4,
    )
    tracker = CaptureTracker(
        link, pilot, lambda kind, **fields: events.append({"kind": kind, **fields}),
        "COMPANION", clock=lambda: now[0],
    )
    return SimpleNamespace(
        tracker=tracker, link=link, pilot=pilot, now=now, events=events, calls=calls,
    )


def lease(npc="BANDIT", generation=1):
    return BodyLease(
        npc=npc, provider="neural-body", mode="realtime", session_epoch=1,
        world_epoch=1, entity_id=generation, control_generation=generation, goal_version=1,
        target_entity_id=10, acquired_observation_seq=1,
    )


async def test_foreign_target_is_excluded_even_when_the_pilot_has_a_lease():
    test = rig(values=(7, 1, 1, 0, 0))
    owned = lease()
    test.pilot.fights["BANDIT"] = SimpleNamespace(lease=owned, _refusals=Counter())
    test.link.body_leases[owned.entity_id] = owned

    await test.tracker.poll(1, {"BANDIT"})
    record, = test.tracker.finish_wave(1)

    assert not test.tracker.eligible and not test.tracker.captured
    assert record["status"] == "excluded" and record["reason"] == "target_not_allowed"
    assert test.events[-1]["kind"] == "capture_result"


async def test_releasing_owners_fill_capacity_but_an_actors_own_slot_does_not():
    test = rig()
    test.pilot._releasing = {f"OLD_{index}": object() for index in range(4)}
    await test.tracker.poll(1, {"BANDIT"})
    assert not test.tracker.eligible
    assert test.events[-1]["reason"] == "no_capacity"

    test.pilot._releasing.pop("OLD_3")
    owned = lease()
    test.pilot.fights["BANDIT"] = SimpleNamespace(lease=owned, _refusals=Counter())
    test.link.body_leases[owned.entity_id] = owned
    test.now[0] = 0.1
    await test.tracker.poll(1, {"BANDIT"})

    assert test.tracker.eligible == {(1, "BANDIT")}
    assert test.tracker.captured == {(1, "BANDIT")}
    assert test.tracker.finish_wave(1)[0]["status"] == "captured"


async def test_human_one_handed_and_live_attack_state_are_all_required():
    test = rig(values=(7, 1, 0, 1, 0))
    await test.tracker.poll(1, {"BANDIT"})
    assert test.events[-1]["reason"] == "not_one_handed"
    test.now[0] = 0.5
    test.link.values = (17, 1, 1, 1, 0)
    await test.tracker.poll(1, {"BANDIT"})
    assert test.events[-1]["reason"] == "not_human"
    test.now[0] = 1
    test.link.values = (7, 0, 1, 1, 0)
    await test.tracker.poll(1, {"BANDIT"})
    assert test.events[-1]["reason"] == "not_attacking"
    test.now[0] = 1.5
    test.link.values = (7, 1, 1, 0, 1)
    await test.tracker.poll(1, {"BANDIT", "COMPANION"})
    assert test.tracker.eligible == {(1, "BANDIT")}
    assert test.tracker.finish_wave(1)[0]["status"] == "missed"


async def test_refusals_are_not_double_counted_between_active_fight_and_summary():
    test = rig()
    fight = SimpleNamespace(lease=None, _refusals=Counter(native_queue_busy=2))
    test.pilot.fights["BANDIT"] = fight
    await test.tracker.poll(1, {"BANDIT"})
    test.now[0] = 2
    fight._refusals["native_queue_busy"] = 3
    await test.tracker.poll(1, {"BANDIT"})
    test.pilot.fights.clear()
    summary = SimpleNamespace(
        npc="BANDIT", lease=None, reason="acquire_exhausted",
        acquire_refusals=(("native_queue_busy", 3),),
    )
    test.pilot.summaries.append(summary)
    test.now[0] = 6
    await test.tracker.poll(1, {"BANDIT"})
    record, = test.tracker.finish_wave(1)

    assert record["miss_reason"] == "native_queue_busy"
    assert record["acquire_refusals"] == {"native_queue_busy": 3}
    assert record["end_reasons"] == {"acquire_exhausted": 1}
    assert record["window_s"] == 6
    assert test.tracker.finish_wave(1) == [record]


async def test_watch_refusal_and_acquire_window_have_explicit_miss_reasons():
    test = rig()
    await test.tracker.poll(1, {"BANDIT"})
    test.pilot.summaries.append(SimpleNamespace(
        npc="BANDIT", lease=None, reason="watch_refused", acquire_refusals=(),
    ))
    test.now[0] = 1
    record, = test.tracker.finish_wave(1)
    assert record["miss_reason"] == "watch_refused"

    test.now[0] = 10
    await test.tracker.poll(2, {"BANDIT"})
    test.now[0] = 16
    record, = test.tracker.finish_wave(2)
    assert record["miss_reason"] == "acquire_window"
    assert record["acquire_refusals"] == {}  # Prior wave's summary does not leak.


async def test_excluded_periods_are_removed_from_opportunity_window_and_queries_are_cached():
    test = rig()
    await test.tracker.poll(1, {"BANDIT"})
    test.now[0] = 0.2
    await test.tracker.poll(1, {"BANDIT"})
    assert len(test.calls) == 1
    test.now[0] = 1
    test.link.values = (7, 1, 1, 0, 0)
    await test.tracker.poll(1, {"BANDIT"})
    test.now[0] = 5
    test.link.values = (7, 1, 1, 1, 0)
    await test.tracker.poll(1, {"BANDIT"})
    test.now[0] = 6
    record, = test.tracker.finish_wave(1)
    assert record["window_s"] == 2
    assert record["eligible"] and not record["captured"]


async def test_reused_actor_cannot_change_a_finished_wave():
    test = rig(values=(7, 1, 1, 0, 0))
    await test.tracker.poll(1, {"BANDIT"})
    first, = test.tracker.finish_wave(1)
    test.now[0] = 90
    test.link.values = (7, 1, 1, 1, 0)
    owned = lease(generation=2)
    test.pilot.fights["BANDIT"] = SimpleNamespace(lease=owned, _refusals=Counter())
    test.link.body_leases[owned.entity_id] = owned
    await test.tracker.poll(1, {"BANDIT"})
    await test.tracker.poll(2, {"BANDIT"})
    second, = test.tracker.finish_wave(2)
    assert first["status"] == "excluded" and second["status"] == "captured"
    assert test.tracker.eligible == test.tracker.captured == {(2, "BANDIT")}
    assert len(test.tracker.records) == 2


async def test_late_summary_and_old_owner_do_not_leak_into_the_next_wave():
    test = rig()
    owned = lease()
    test.pilot.fights["BANDIT"] = SimpleNamespace(lease=owned, _refusals=Counter())
    test.link.body_leases[owned.entity_id] = owned
    await test.tracker.poll(1, {"BANDIT"})
    test.tracker.finish_wave(1)
    test.now[0] = 90
    await test.tracker.poll(2, {"BANDIT"})
    test.pilot.fights.clear()
    test.pilot.summaries.append(SimpleNamespace(
        npc="BANDIT", lease=owned, reason="finish_deadline", acquire_refusals=(("old_refusal", 5),),
    ))
    test.now[0] = 96
    second, = test.tracker.finish_wave(2)
    assert not second["captured"]
    assert second["acquire_refusals"] == {} and second["end_reasons"] == {}


async def test_teardown_preserves_live_foreign_target_exclusion_and_its_history():
    test = rig(values=(7, 1, 1, 0, 0))  # The bandit attacks Cavalorn, rather than our party.
    for tick in (0, 0.5, 1):
        test.now[0] = tick
        await test.tracker.poll(1, {"BANDIT"})
    test.pilot.people["BANDIT"].state = "ZS_STAND"
    test.pilot.people["BANDIT"].activity = "routine"
    for tick in (2, 3, 4, 5, 6):
        test.now[0] = tick
        await test.tracker.poll(1, {"BANDIT"})
    test.pilot.people.clear()  # Load/removal happens before the final poll.
    test.now[0] = 7
    await test.tracker.poll(1, {"BANDIT"})
    record, = test.tracker.finish_wave(1)

    assert record["status"] == "excluded"
    assert record["reason"] == record["exclusion_reason"] == "target_not_allowed"
    assert record["validation_reasons"] == {
        "target_not_allowed": 3, "not_attacking": 5, "not_in_roster": 1,
    }
    assert test.events[-1]["validation_reasons"] == record["validation_reasons"]


async def test_primary_exclusion_uses_live_validation_instead_of_acquire_refusals():
    test = rig(values=(7, 1, 0, 0, 0))  # Unsupported weapon, regardless of its current target.
    test.pilot.fights["BANDIT"] = SimpleNamespace(
        lease=None, _refusals=Counter(target_not_allowed=48),
    )
    await test.tracker.poll(1, {"BANDIT"})
    test.pilot.people.clear()
    test.now[0] = 1
    await test.tracker.poll(1, {"BANDIT"})
    record, = test.tracker.finish_wave(1)

    assert record["exclusion_reason"] == "not_one_handed"
    assert record["acquire_refusals"] == {"target_not_allowed": 48}
    assert record["validation_reasons"] == {"not_one_handed": 1, "not_in_roster": 1}
