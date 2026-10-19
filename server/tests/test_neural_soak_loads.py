"""Death cleanup may race a load; scheduled combat coverage stays independent."""

from dataclasses import dataclass, replace

import pytest

from gothic_ai.protocol import BodyLease
from gothic_ai.scenarios.neural_soak import (
    _has_scheduled_combat_load,
    _load_summaries_ok,
    _scheduled_load_due,
)


def lease(entity_id):
    return BodyLease(
        npc=f"BANDIT_{entity_id}", provider="neural-body", mode="realtime", session_epoch=1,
        world_epoch=1, entity_id=entity_id, control_generation=1, goal_version=1,
        target_entity_id=10, acquired_observation_seq=1,
    )


@dataclass(frozen=True)
class Summary:
    lease: BodyLease
    reason: str = "loading"
    target_kind: str | None = "hero"
    release_confirmed: bool = True
    fallbacks: int = 0
    release_seconds: float | None = 0.1
    release_late_seconds: float | None = None


def test_hero_death_accepts_cleanup_published_between_snapshot_and_load():
    old = [lease(1), lease(2)]
    # The first old fight notices the dead hero before Loading reaches it;
    # the second still observes Loading. Both release the snapshotted leases.
    summaries = [Summary(old[0], reason="hero_down"), Summary(old[1])]

    assert _load_summaries_ok(old, summaries, cause="hero_death")
    assert not _load_summaries_ok(old, summaries, cause="scheduled_10m")


def test_companion_death_accepts_cleanup_published_between_snapshot_and_load():
    old = [lease(1), lease(2)]
    summaries = [Summary(old[0], reason="target_down", target_kind="companion"), Summary(old[1])]

    assert _load_summaries_ok(old, summaries, cause="companion_death")
    assert not _load_summaries_ok(old, summaries, cause="hero_death")
    assert not _load_summaries_ok(old, summaries, cause="scheduled_10m")


@pytest.mark.parametrize("cause", ["hero_death", "companion_death"])
def test_simultaneous_deaths_allow_only_known_dead_targets(cause):
    old = [lease(1), lease(2)]
    summaries = [Summary(old[0], reason="hero_down", target_kind="hero"),
                 Summary(old[1], reason="target_down", target_kind="companion")]
    assert _load_summaries_ok(old, summaries, cause=cause, dead_targets={"hero", "companion"})
    assert not _load_summaries_ok(old, summaries, cause=cause)
    assert not _load_summaries_ok(
        old, summaries, cause="scheduled_10m", dead_targets={"hero", "companion"},
    )


@pytest.mark.parametrize("target_kind", ["hero", "other", None])
def test_companion_death_does_not_exempt_another_targets_summary(target_kind):
    old = lease(1)
    summary = Summary(old, reason="target_down", target_kind=target_kind)
    assert not _load_summaries_ok([old], [summary], cause="companion_death")


@pytest.mark.parametrize("reason", ["target_down", "target_lost", "native_restored", "pilot_error"])
def test_hero_death_does_not_accept_unrelated_endings(reason):
    old = lease(1)
    assert not _load_summaries_ok([old], [Summary(old, reason=reason)], cause="hero_death")


@pytest.mark.parametrize("target_kind", ["companion", "other", None])
def test_hero_death_does_not_exempt_another_targets_summary(target_kind):
    old = lease(1)
    summary = Summary(old, reason="hero_down", target_kind=target_kind)
    assert not _load_summaries_ok([old], [summary], cause="hero_death")


@pytest.mark.parametrize("cause, reason, target_kind", [
    ("hero_death", "hero_down", "hero"),
    ("companion_death", "target_down", "companion"),
])
@pytest.mark.parametrize("fields", [
    {"release_confirmed": False},
    {"fallbacks": 1},
    {"release_seconds": None},
    {"release_seconds": 2.001},
    {"release_late_seconds": 2.75},
])
def test_death_race_keeps_release_and_fallback_checks(fields, cause, reason, target_kind):
    old = lease(1)
    summary = replace(Summary(old, reason=reason, target_kind=target_kind), **fields)
    assert not _load_summaries_ok([old], [summary], cause=cause)


def test_load_cleanup_requires_every_exact_old_lease_once():
    first, second = lease(1), lease(2)
    assert not _load_summaries_ok([first, second], [Summary(first)], cause="hero_death")
    assert not _load_summaries_ok(
        [first, second], [Summary(first), Summary(first)], cause="hero_death",
    )
    changed = second.model_copy(update={"control_generation": 2})
    assert not _load_summaries_ok(
        [first, second], [Summary(first), Summary(changed)], cause="hero_death",
    )
    assert _load_summaries_ok(
        [first, second], [Summary(first), Summary(second, release_seconds=2)], cause="scheduled_20m",
    )


def test_deaths_before_both_markers_cannot_spend_planned_loads_in_gaps():
    loads, scheduled = [], set()
    # Each wave ends just before its checkpoint; post-death reacquisition
    # succeeds, but a death load still does not satisfy scheduled combat.
    for elapsed in (600, 1200):
        loads.append({"cause": "hero_death", "mid_fight": True, "ok": True, "reacquired": True})
        marker = _scheduled_load_due(elapsed, 1800, scheduled, loads, combat=False)
        assert marker is None
        assert not _has_scheduled_combat_load(loads)

    # The overdue first marker remains available in the next actual fight.
    assert _scheduled_load_due(1201, 1800, scheduled, loads, combat=True) == 10
    loads.append({"cause": "scheduled_10m", "mid_fight": True, "ok": True, "reacquired": True})
    scheduled.add(10)
    assert _has_scheduled_combat_load(loads)
    assert _scheduled_load_due(1202, 1800, scheduled, loads, combat=False) == 20


def test_only_a_planned_combat_load_opens_the_peaceful_gap():
    scheduled = {10}
    loads = [{"cause": "scheduled_10m", "mid_fight": False}]
    assert _scheduled_load_due(1200, 1800, scheduled, loads, combat=False) is None
    loads[0]["mid_fight"] = True
    assert _scheduled_load_due(1199, 1800, scheduled, loads, combat=False) is None
    assert _scheduled_load_due(1200, 1800, scheduled, loads, combat=False) == 20
    scheduled.add(20)
    assert _scheduled_load_due(1500, 1800, scheduled, loads, combat=True) is None


def test_short_run_markers_keep_their_scaled_timing():
    assert _scheduled_load_due(59.9, 180, set(), [], combat=True) is None
    assert _scheduled_load_due(60, 180, set(), [], combat=True) == 10
    loads = [{"cause": "scheduled_10m", "mid_fight": True}]
    assert _scheduled_load_due(120, 180, {10}, loads, combat=False) == 20
