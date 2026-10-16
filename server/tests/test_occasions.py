"""World occasions are deterministic, branch-local facts; no model is involved."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from gothic_ai.chronicle import Chronicle
from gothic_ai.game import GameError
from gothic_ai.memory import MemoryStore
from gothic_ai.occasions import OccasionFailed, Occasions, WaypointUnavailable
from gothic_ai.places import PlaceBook
from tests.fake_game import FakeGame, connected
from tests.fakes import Clock

LOBART, HILDA, VINO = "BAU_950_LOBART", "BAU_951_HILDA", "BAU_952_VINO"


@pytest.fixture
def places():
    spots = [("HOME", "house"), ("ROAD", "road"), ("CAMP", "camp"), ("CAVE", "cave")]
    return PlaceBook(
        {
            "version": 2,
            "world": "NEWWORLD.ZEN",
            "places": [
                {
                    "id": name,
                    "ru": name,
                    "where_ru": name,
                    "en": name,
                    "where_en": name,
                    "kind": kind,
                    "center": [i * 10000, 0, 0],
                }
                for i, (name, kind) in enumerate(spots)
            ],
            "points": {f"{name}_{n}": i for i, (name, _) in enumerate(spots) for n in (1, 2)},
        }
    )


@asynccontextmanager
async def world(places):
    fake, clock, store = FakeGame(), Clock(), MemoryStore(":memory:")
    fake.nearest_waypoint = "ROAD_1"
    fake.vars["KAPITEL"] = 1
    fake.people = [
        {
            "npc": npc,
            "name": npc,
            "guild": 0,
            "routine": [{"start": 0, "end": 1440, "state": "ZS_STAND", "waypoint": "HOME_1"}],
        }
        for npc in (LOBART, HILDA, VINO)
    ]
    fake.npc_states = {
        LOBART: {"state": "alive", "name": "Лобарт", "distance": 200},
        HILDA: {"state": "dead", "name": "Хильда", "distance": 100},
        VINO: {"state": "alive", "name": "Вино", "distance": 6000},
    }
    async with connected(fake) as link:
        chronicle = Chronicle(link, store)
        occasions = Occasions(link, chronicle, places, clock=clock, busy_poll_s=0.01)
        yield fake, clock, chronicle, occasions


async def settle(fake, clock, occasions, place):
    fake.nearest_waypoint = f"{place}_1"
    await occasions.poll()
    clock.t += 10
    await occasions.poll()


async def load(fake, chronicle, branch="loaded", head=None):
    parent = chronicle.branch
    if head is None:
        head = chronicle.memory.head(parent)
    await fake.send(type="session", branch=branch, parent=parent, parent_seq=head, reason="load")
    # The reply follows the session message on the same socket: every listener has seen it now.
    await chronicle.link.story_state()


def kinds(occasions):
    return [f["kind"] for f in occasions.pending]


async def test_a_place_must_hold_for_ten_seconds_not_just_one_waypoint(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        fake.nearest_waypoint = "HOME_1"
        await occasions.poll()
        assert occasions.current is None
        clock.t += 5
        fake.nearest_waypoint = "HOME_2"  # another waypoint of the same place does not restart the clock
        await occasions.poll()
        assert chronicle.facts("visited") == []
        clock.t += 4.99
        await occasions.poll()
        assert chronicle.facts("visited") == []
        clock.t += 0.01
        await occasions.poll()
        assert occasions.current.id == "HOME"
        assert occasions.waypoint == "HOME_2"
        assert [f["place"] for f in chronicle.facts("visited")] == ["HOME"]
        assert kinds(occasions) == ["arrived"]


async def test_a_transient_place_does_not_count_as_a_visit(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "HOME")
        fake.nearest_waypoint = "ROAD_1"
        await occasions.poll()
        clock.t += 5
        fake.nearest_waypoint = "HOME_1"
        await occasions.poll()
        clock.t += 10
        await occasions.poll()
        assert occasions.current.id == "HOME"
        assert occasions.visited == {"HOME"}
        assert len(chronicle.facts("occasion")) == 1


async def test_every_visit_is_kept_but_only_inhabited_places_camps_and_caves_are_occasions(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        for place in ("ROAD", "HOME", "CAMP", "CAVE"):
            await settle(fake, clock, occasions, place)
        assert occasions.visited == {"ROAD", "HOME", "CAMP", "CAVE"}
        assert [f["place"] for f in chronicle.facts("visited")] == ["ROAD", "HOME", "CAMP", "CAVE"]
        assert [f["place"] for f in chronicle.facts("occasion")] == ["HOME", "CAMP", "CAVE"]
        arrived = chronicle.facts("occasion")[0]
        assert arrived == {
            "fact": "occasion",
            "kind": "arrived",
            "place": "HOME",
            "waypoint": "HOME_1",
            "people": [LOBART],
            "day": 1,
            "hour": 8,
            "minute": 0,
        }
        assert sum(m["type"] == "story_people" for m in fake.received) == 1
        # story_eval needs a concrete SELF; story_state.near likewise needs HERO's actual instance.
        assert all(m["npc"] == LOBART for m in fake.received if m["type"] == "story_eval")
        assert all(m["near"] == "PC_HERO" for m in fake.received if m["type"] == "story_state")


async def test_return_and_load_keep_visits_without_repeating_or_requeueing_occasions(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "HOME")
        saved = chronicle.memory.head(chronicle.branch)
        original = chronicle.facts("occasion")
        await settle(fake, clock, occasions, "ROAD")
        await settle(fake, clock, occasions, "HOME")
        assert chronicle.facts("occasion") == original
        assert len(occasions.pending) == 1
        await load(fake, chronicle, head=saved)
        assert occasions.pending == []
        assert occasions.visited == {"HOME"}
        await settle(fake, clock, occasions, "HOME")
        assert chronicle.facts("occasion") == original
        assert occasions.pending == []
        assert [f["place"] for f in chronicle.facts("visited")] == ["HOME"]
        assert sum(m["type"] == "story_people" for m in fake.received) == 2


async def test_visits_after_a_save_are_new_on_its_loaded_branch(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "HOME")
        saved = chronicle.memory.head(chronicle.branch)
        await settle(fake, clock, occasions, "CAMP")
        await load(fake, chronicle, head=saved)
        assert occasions.visited == {"HOME"}
        await settle(fake, clock, occasions, "CAMP")
        assert [f["place"] for f in chronicle.facts("occasion")] == ["HOME", "CAMP"]
        assert kinds(occasions) == ["arrived"]


async def test_load_during_a_poll_discards_the_previous_branch_response(places, monkeypatch):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "HOME")
        saved, arrival = chronicle.memory.head(chronicle.branch), chronicle.facts("occasion")[0]
        chronicle.record({"fact": "found_out", "quest": "OLD_BRANCH"})
        state_received, resume = asyncio.Event(), asyncio.Event()
        original_state = chronicle.link.story_state

        async def delayed_state(*args, **kwargs):
            result = await original_state(*args, **kwargs)
            state_received.set()
            await resume.wait()
            return result

        monkeypatch.setattr(chronicle.link, "story_state", delayed_state)
        polling = asyncio.create_task(occasions.poll())
        try:
            await asyncio.wait_for(state_received.wait(), 1)
            await fake.send(
                type="session", branch="loaded", parent=chronicle.branch, parent_seq=saved, reason="load"
            )
            await chronicle.link.evaluate(["var", "KAPITEL"])
            assert chronicle.branch == "loaded"
            resume.set()
            await asyncio.wait_for(polling, 1)
            assert occasions.pending == []
            assert occasions.visited == {"HOME"}
            assert occasions.current is None and occasions.waypoint is None
            assert chronicle.facts("occasion") == [arrival]
            assert chronicle.facts("found_out") == []
        finally:
            resume.set()
            polling.cancel()
            await asyncio.gather(polling, return_exceptions=True)


async def test_an_unavailable_nearest_waypoint_stops_without_a_visit_or_occasion(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        fake.nearest_waypoint = None
        with pytest.raises(WaypointUnavailable):
            await occasions.poll()
        assert chronicle.facts("visited") == []
        assert chronicle.facts("occasion") == []


async def test_chapter_is_baselined_then_checked_every_thirty_seconds_and_on_load(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await occasions.poll()
        assert chronicle.facts("chapter")[-1]["value"] == 1
        assert chronicle.facts("occasion") == []
        fake.vars["KAPITEL"] = 2
        clock.t += 29
        await occasions.poll()
        assert chronicle.facts("occasion") == []
        clock.t += 1
        await occasions.poll()
        assert kinds(occasions) == ["chapter"]
        assert chronicle.facts("chapter")[-1]["value"] == 2
        await occasions.poll()
        assert len(chronicle.facts("occasion")) == 1
        saved = chronicle.memory.head(chronicle.branch)
        await load(fake, chronicle, head=saved)
        await occasions.poll()
        assert occasions.pending == []
        fake.vars["KAPITEL"] = 3
        await load(fake, chronicle, branch="loaded-again")
        await occasions.poll()
        assert kinds(occasions) == ["chapter"]
        assert chronicle.facts("chapter")[-1]["value"] == 3


async def test_lower_chapter_does_not_create_an_occasion(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        fake.vars["KAPITEL"] = 3
        await occasions.poll()
        fake.vars["KAPITEL"] = 2
        clock.t += 30
        await occasions.poll()
        assert chronicle.facts("occasion") == []


@pytest.mark.parametrize("kind", ["found_out", "story_quest_done", "authored_done"])
async def test_deeds_preserve_the_original_chronicle_fact(places, kind):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "HOME")
        await occasions.next()
        deed = {"fact": kind, "quest": "FIRST", "npc": LOBART}
        chronicle.record(deed)
        await occasions.poll()
        assert kinds(occasions) == ["deed"]
        result = await occasions.next()
        assert result["deed"] == deed
        assert result["people"] == [LOBART]
        assert result["place"] == "HOME"
        assert occasions.pending == []


async def test_only_a_person_killed_by_the_hero_is_a_deed(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await occasions.poll()
        ignored = [
            {"npc": LOBART, "killer": "WOLF", "tag": None},
            {"npc": "WOLF", "killer": "PLAYER", "tag": "wolves", "monster": True},
            {"npc": "UNKNOWN_BEAST", "killer": "PLAYER", "tag": "wolves"},
        ]
        for fields in ignored:
            chronicle.record({"fact": "died", **fields})
        await occasions.poll()
        assert chronicle.facts("occasion") == []
        human = {"fact": "died", "npc": HILDA, "killer": "PLAYER", "tag": None}
        chronicle.record(human)
        await occasions.poll()
        assert kinds(occasions) == ["deed"]
        assert (await occasions.next())["deed"] == human
        tagged = {"fact": "died", "npc": "HUMAN", "killer": "PLAYER", "tag": "cast", "monster": False}
        chronicle.record(tagged)
        await occasions.poll()
        assert (await occasions.next())["deed"] == tagged


async def test_live_death_messages_keep_the_bridge_human_or_monster_distinction(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await occasions.poll()
        await fake.send(type="hero_kill", npc="WOLF", monster=True)
        await fake.send(type="npc_died", npc="WOLF", tag="wolves", killer="PLAYER")
        await chronicle.link.story_state()
        await occasions.poll()
        assert chronicle.facts("occasion") == []
        await fake.send(type="hero_kill", npc=LOBART, monster=False)
        await fake.send(type="npc_died", npc=LOBART, tag="cast", killer="PLAYER")
        await chronicle.link.story_state()
        await occasions.poll()
        assert kinds(occasions) == ["deed"]
        assert (await occasions.next())["deed"]["npc"] == LOBART


async def test_quiet_counts_a_full_game_day_including_hours_and_minutes(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        fake.day, fake.hour, fake.minute = 4, 23, 45
        await settle(fake, clock, occasions, "ROAD")
        fake.day, fake.hour, fake.minute = 5, 23, 44
        await occasions.poll()
        assert chronicle.facts("occasion") == []
        fake.minute = 45
        await occasions.poll()
        assert kinds(occasions) == ["quiet"]
        assert chronicle.facts("occasion")[-1]["day"] == 5
        assert chronicle.facts("occasion")[-1]["hour"] == 23
        await occasions.poll()
        assert len(chronicle.facts("occasion")) == 1


@pytest.mark.parametrize("kind", ["story_quest", "chain", "authored"])
async def test_a_new_director_quest_restarts_the_quiet_day(places, kind):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "ROAD")
        fake.hour = 20
        await occasions.poll()
        chronicle.record({"fact": kind, "id": "NEW_LINE"})
        await occasions.poll()
        fake.day, fake.hour = 2, 8
        await occasions.poll()
        assert chronicle.facts("occasion") == []
        fake.hour = 20
        await occasions.poll()
        assert kinds(occasions) == ["quiet"]


async def test_quiet_clock_survives_a_load_and_any_occasion_restarts_it(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "ROAD")
        fake.hour = 20
        await occasions.poll()
        await load(fake, chronicle)
        await settle(fake, clock, occasions, "ROAD")
        fake.day, fake.hour = 2, 8
        await occasions.poll()
        assert kinds(occasions) == ["quiet"]
        chronicle.record({"fact": "found_out", "quest": "LIE"})
        await occasions.poll()
        fake.day, fake.hour = 3, 7
        await occasions.poll()
        assert len(chronicle.facts("occasion")) == 2
        fake.hour = 8
        await occasions.poll()
        assert [f["kind"] for f in chronicle.facts("occasion")] == ["quiet", "deed", "quiet"]


async def test_the_single_take_call_returns_priority_order_and_consumes_once(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "ROAD")
        fake.day += 1
        await occasions.poll()
        await settle(fake, clock, occasions, "HOME")
        fake.vars["KAPITEL"] = 2
        clock.t += 30
        await occasions.poll()
        chronicle.record({"fact": "found_out", "quest": "LIE"})
        await occasions.poll()
        assert kinds(occasions) == ["deed", "chapter", "arrived", "quiet"]
        assert [(await occasions.next())["kind"] for _ in range(4)] == ["deed", "chapter", "arrived", "quiet"]
        assert occasions.pending == []
        assert len(chronicle.facts("occasion")) == 4


@pytest.mark.parametrize("kind", ["arrived", "quiet"])
async def test_local_occasions_expire_only_after_leaving_more_than_fifty_metres(places, kind):
    async with world(places) as (fake, clock, chronicle, occasions):
        if kind == "arrived":
            await settle(fake, clock, occasions, "HOME")
            origin, destination = "HOME", "ROAD"
        else:
            await settle(fake, clock, occasions, "ROAD")
            fake.day += 1
            await occasions.poll()
            origin, destination = "ROAD", "HOME"
        fake.waypoint_distances[f"{origin}_1"] = 5000
        fake.waypoint_distances[f"{origin}_2"] = 5000
        fake.nearest_waypoint = f"{destination}_1"
        await occasions.poll()
        assert kind in kinds(occasions)
        fake.waypoint_distances[f"{origin}_1"] = 5001
        fake.waypoint_distances[f"{origin}_2"] = 6000
        await occasions.poll()
        assert kind not in kinds(occasions)
        assert any(f["kind"] == kind for f in chronicle.facts("occasion"))  # history stays


async def test_every_kind_expires_after_a_game_day_but_deeds_and_chapters_survive_distance(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "HOME")
        fake.vars["KAPITEL"] = 2
        clock.t += 30
        await occasions.poll()
        chronicle.record({"fact": "found_out", "quest": "LIE"})
        await occasions.poll()
        fake.nearest_waypoint = "ROAD_1"
        fake.waypoint_distances.update(HOME_1=100000, HOME_2=100000)
        await occasions.poll()
        assert kinds(occasions) == ["deed", "chapter"]
        clock.t += 10
        await occasions.poll()
        fake.day += 1
        await occasions.poll()
        assert kinds(occasions) == ["quiet"]


@pytest.mark.parametrize("expired_by", ["distance", "time"])
async def test_next_refreshes_freshness_before_handoff_without_waiting_for_the_watcher(places, expired_by):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "HOME")
        if expired_by == "distance":
            fake.nearest_waypoint = "ROAD_1"
            fake.waypoint_distances.update(HOME_1=5001, HOME_2=5001)
        else:
            fake.day += 1
        taking = asyncio.create_task(occasions.next())
        try:
            await asyncio.sleep(0.03)
            assert not taking.done()
            assert occasions.pending == []
            chronicle.record({"fact": "found_out", "quest": "LIE"})
            await occasions.poll()
            assert (await asyncio.wait_for(taking, 1))["kind"] == "deed"
        finally:
            taking.cancel()
            await asyncio.gather(taking, return_exceptions=True)


@pytest.mark.parametrize("busy", ["in_dialog", "in_cutscene"])
async def test_next_waits_while_the_director_is_busy_then_takes_the_fresh_best(places, busy):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "HOME")
        setattr(fake, busy, True)
        taking = asyncio.create_task(occasions.next())
        try:
            await asyncio.sleep(0.03)
            assert not taking.done()
            chronicle.record({"fact": "found_out", "quest": "LIE"})
            await occasions.poll()
            setattr(fake, busy, False)
            assert (await asyncio.wait_for(taking, 1))["kind"] == "deed"
            assert kinds(occasions) == ["arrived"]
        finally:
            taking.cancel()
            await asyncio.gather(taking, return_exceptions=True)


async def test_next_can_wait_for_the_first_occasion(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await occasions.poll()
        taking = asyncio.create_task(occasions.next())
        try:
            await asyncio.sleep(0.03)
            assert not taking.done()
            chronicle.record({"fact": "found_out", "quest": "LIE"})
            await occasions.poll()
            assert (await asyncio.wait_for(taking, 1))["kind"] == "deed"
            assert occasions.pending == []
        finally:
            taking.cancel()
            await asyncio.gather(taking, return_exceptions=True)


async def test_only_a_truthful_accepted_script_quest_claim_is_a_deed(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await occasions.poll()
        for fields in ({"truth": False, "believed": True}, {"truth": True, "believed": False}, {}):
            chronicle.record({"fact": "claim", "quest": "SCRIPT", **fields})
        await occasions.poll()
        assert occasions.pending == []
        claim = {"fact": "claim", "quest": "SCRIPT", "truth": True, "believed": True}
        chronicle.record(claim)
        await occasions.poll()
        assert (await occasions.next())["deed"] == claim


async def test_a_quest_saved_before_the_next_sample_still_resets_quiet(places):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "ROAD")
        fake.hour = 20
        chronicle.record({"fact": "story_quest", "id": "NEW_LINE"})
        await load(fake, chronicle)  # no poll between making the quest and the save
        await settle(fake, clock, occasions, "ROAD")
        fake.day, fake.hour = 2, 8
        await occasions.poll()
        assert occasions.pending == []
        fake.hour = 20
        await occasions.poll()
        assert kinds(occasions) == ["quiet"]


async def test_next_retries_a_transient_game_error_without_losing_the_occasion(places, monkeypatch):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "HOME")
        original = chronicle.link.story_state
        calls = 0

        async def interrupted(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise GameError("loading")
            return await original(**kwargs)

        monkeypatch.setattr(chronicle.link, "story_state", interrupted)
        assert (await asyncio.wait_for(occasions.next(), 1))["kind"] == "arrived"
        assert calls == 2 and occasions.pending == []


async def test_next_passes_an_unexpected_handoff_failure_with_the_consumed_occasion(places, monkeypatch):
    async with world(places) as (fake, clock, chronicle, occasions):
        await settle(fake, clock, occasions, "HOME")
        [pending] = occasions.pending

        async def broken(**kwargs):
            raise RuntimeError("handoff broken")

        monkeypatch.setattr(chronicle.link, "story_state", broken)
        with pytest.raises(OccasionFailed) as caught:
            await occasions.next()
        assert caught.value.occasion == pending
        assert isinstance(caught.value.error, RuntimeError)
        assert occasions.pending == []
