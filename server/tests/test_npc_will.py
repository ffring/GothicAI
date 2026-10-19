from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from gothic_ai.characters import Card
from gothic_ai.chronicle import Chronicle
from gothic_ai.crimes import Crimes
from gothic_ai.memory import MemoryStore
from gothic_ai.npc_crimes import NpcCrimes
from gothic_ai.npc_will import MOTIVES, LoopGuard, NpcWill
from gothic_ai.people_knowledge import PeopleKnowledge
from gothic_ai.protocol import Chest, NpcIdle, NpcRoster, NpcView, NpcViews, Session
from gothic_ai.social import Social
from tests.test_crimes import HILDA, LOBART
from tests.test_npc_crimes import NAMES, THIEF, NpcLink, deed

DAY = 24 * 60
NOW = 3 * DAY + 9 * 60


def character(npc: str, *, honesty: int = 3, greed: int = 3, courage: int = 3) -> Card:
    return Card(npc, NAMES.get(npc, npc), "", "", greed, honesty, courage, 3, 3, 3)


class WillLink(NpcLink):
    def __init__(self) -> None:
        super().__init__()
        self.views: dict[str, NpcView] = {}
        self.view_queries: list[str] = []
        self.people: list[dict[str, Any]] = []
        self.chests: dict[str, list[Chest]] = {}

    async def npc_view(self, npcs: list[str]) -> list[NpcView]:
        self.view_queries.extend(npcs)
        return [self.views[npc] for npc in npcs if npc in self.views]

    async def story_people(self) -> list[dict[str, Any]]:
        return self.people

    async def npc_chests(self, npc: str) -> list[Chest]:
        return self.chests.get(npc, [])


@pytest.fixture
async def system():
    link, memory = WillLink(), MemoryStore(":memory:")
    chronicle = Chronicle(link, memory)
    crimes = Crimes(link, chronicle, names=lambda npc: NAMES.get(npc, npc))
    cards = {npc: character(npc) for npc in (LOBART, HILDA, THIEF)}
    deeds = NpcCrimes(link, chronicle, crimes=crimes, cards=cards)
    moments = SimpleNamespace(now=100.0)
    people = PeopleKnowledge(chronicle)
    will = NpcWill(
        link, chronicle, deeds, cards=cards, people=people, memory=memory,
        clock=lambda: moments.now,
    )
    yield SimpleNamespace(
        link=link, memory=memory, chronicle=chronicle, crimes=crimes, deeds=deeds,
        cards=cards, will=will, people=people, moments=moments,
    )
    await will.close()
    await deeds.close()
    await crimes.close()
    if chronicle._background:
        await asyncio.gather(*chronicle._background)
    memory.close()


def scene(system, *, now: int = NOW, actor: str = LOBART, target: str = THIEF, **changes: Any) -> None:
    entries = [
        {"id": npc, "name": NAMES.get(npc, npc), "guild": 12, "distance": 500,
         "activity": "routine", "state": "ZS_STAND"}
        for npc in (actor, target)
    ]
    entries[0].update(changes)
    system.link.receive(NpcRoster(type="npc_roster", t=0, npcs=entries))
    views = [
        NpcView(
            npc=npc, name=NAMES.get(npc, npc), time=now, hp=100, hp_max=100,
            state="ZS_STAND", waypoint="FARM1_IN", nearest_waypoint="FARM1_IN",
            theft_ready=True, others_damage_ready=True,
            hero={"id": "PLAYER", "name": "Герой", "distance": 500, "seen": False},
            sees=[{"id": other, "name": NAMES.get(other, other), "distance": 300}]
            if (other := target if npc == actor else actor) else [],
        )
        for npc in (actor, target)
    ]
    system.link.views.update((view.npc, view) for view in views)
    system.link.receive(NpcViews(type="npc_views", t=0, views=views))


def theft(system, *, witnesses: list[str] | None = None, **fields: Any) -> dict[str, Any]:
    result = system.deeds.record(deed(owner=LOBART, witnesses=witnesses or [], **fields))
    assert result is not None
    return result


def greed_scene(system, *, honesty: int = 1, now: int = NOW) -> None:
    system.cards[THIEF] = character(THIEF, honesty=honesty, greed=5)
    system.link.people = [
        {"npc": npc, "name": NAMES[npc], "guild": 12, "routine": [
            {"start": 0, "end": 0, "state": "ZS_STAND", "waypoint": "FARM1_IN", "pos": [0, 0, 0]},
        ]}
        for npc in (THIEF, LOBART)
    ]
    system.will.rumors = SimpleNamespace(social=Social(system.link.people), clock=now)
    system.link.chests[LOBART] = [Chest(
        id="CHEST_LOBART", why="owner", pos=(100, 0, 0), distance=3000, waypoint="FARM1_IN",
        items=[{"item": "ITFO_BREAD", "name": "Хлеб", "count": 7}],
    )]
    scene(system, now=now, actor=THIEF, target=LOBART, distance=2500)
    current = system.link.views[THIEF]
    alone = current.model_copy(update={
        "sees": [], "hero": current.hero.model_copy(update={"distance": 2500}),
    })
    system.link.views[THIEF] = alone
    system.link.receive(NpcViews(type="npc_views", t=1, views=[alone]))


def refresh(system, now: int) -> None:
    views = [view.model_copy(update={"time": now}) for view in system.link.views.values()]
    system.link.views.update((view.npc, view) for view in views)
    system.link.receive(NpcViews(type="npc_views", t=now, views=views))


def finish_action(system) -> None:
    latest = system.chronicle.facts("npc_will")[-1]
    system.link.receive(NpcIdle(type="npc_idle", npc=latest["npc"], act=latest["action"], reason="done"))


@pytest.mark.parametrize("honesty,greed,allowed", [
    (1, 3, False), (1, 4, True), (1, 5, True),
    (2, 3, False), (2, 4, True), (2, 5, True),
    (3, 4, False), (3, 5, False), (5, 5, False),
])
def test_greed_rule_has_the_same_character_thresholds_as_promise_cheating(honesty, greed, allowed):
    rule = MOTIVES["greed"]
    card = character(THIEF, honesty=honesty, greed=greed)
    assert rule.accepts(card) is allowed
    if allowed:
        assert rule.action(card) == "steal"


@pytest.mark.parametrize("courage,action", [(1, "accuse"), (2, "accuse"), (3, "fight"), (5, "fight")])
def test_reclaim_rule_fights_only_from_courage_three(courage, action):
    rule = MOTIVES["reclaim"]
    card = character(LOBART, courage=courage)
    assert rule.accepts(card)
    assert rule.action(card) == action


@pytest.mark.parametrize("courage", [1, 2, 3, 4, 5])
def test_suspicion_rule_never_authorizes_a_fight_even_for_brave_npc(courage):
    rule = MOTIVES["suspect"]
    card = character(LOBART, courage=courage)
    assert rule.accepts(card)
    assert rule.action(card) == "accuse"


@pytest.mark.parametrize("honesty,days", [(1, 2), (2, 4)])
def test_greed_frequency_is_set_by_honesty_in_game_days(honesty, days):
    card = character(THIEF, honesty=honesty, greed=5)
    assert MOTIVES["greed"].cooldown(card) == days * DAY


@pytest.mark.parametrize("courage,action", [(2, "say_svm"), (3, "fight")])
async def test_owner_acts_once_per_known_theft_and_uses_native_body(system, courage, action):
    system.cards[LOBART] = character(LOBART, courage=courage)
    case = theft(system, witnesses=[LOBART])
    scene(system)
    await system.will.tick(NOW)
    await system.will.tick(NOW + 60)
    [motive] = system.chronicle.facts("npc_motive")
    [decision] = system.chronicle.facts("npc_will")
    assert (motive["npc"], motive["kind"], motive["target"], motive["crime"]) == (
        LOBART, "reclaim", THIEF, case["id"],
    )
    assert motive["why"]
    assert (decision["npc"], decision["kind"], decision["target"]) == (LOBART, "reclaim", THIEF)
    assert len(system.link.actions) == 1
    npc, command, args = system.link.actions[0]
    assert (npc, command) == (LOBART, action)
    if action == "fight":
        assert args["npc"] == THIEF and args["nonlethal"] is True
        assert args["deed"] is False and args["reason"] == "AR_NONE"
        assert decision["act"] == "fight"
    else:
        assert args["svm"] == "DIRTYTHIEF"
        assert decision["act"] == "accuse"
        assert system.memory.load("A", LOBART).entries


async def test_unknown_truth_does_not_give_owner_a_reclaim_motive(system):
    theft(system)
    scene(system)
    await system.will.tick(NOW)
    assert not system.link.actions
    assert not system.chronicle.facts("npc_motive")


@pytest.mark.parametrize("distance", [3000, 3001, 6000])
async def test_reclaim_waits_outside_honest_simulation_then_acts_when_close(system, distance):
    theft(system, witnesses=[LOBART])
    scene(system, distance=distance)
    await system.will.tick(NOW)
    assert not system.link.actions and not system.chronicle.facts("npc_will")
    scene(system)
    await system.will.tick(NOW + 1)
    assert [action for _, action, _ in system.link.actions] == ["fight"]
    assert len(system.chronicle.facts("npc_motive")) == 1


@pytest.mark.parametrize("activity,state", [
    ("dialog", "ZS_STAND"), ("fight", "ZS_STAND"), ("attached", "ZS_STAND"),
    ("body", "ZS_STAND"), ("down", "ZS_STAND"),
    ("routine", "ZS_SLEEP"), ("routine", "ZS_GUARD_PASSAGE"),
    ("routine", "ZS_FOLLOW_PLAYER"),
])
async def test_busy_sleeping_or_script_owned_owner_waits_for_a_free_meeting(system, activity, state):
    theft(system, witnesses=[LOBART])
    scene(system, activity=activity, state=state)
    await system.will.tick(NOW)
    assert not system.link.actions and not system.chronicle.facts("npc_will")


async def test_neural_body_ownership_and_server_conversation_are_not_free_meetings(system):
    theft(system, witnesses=[LOBART])
    scene(system)
    system.link.owned.add(LOBART)
    await system.will.tick(NOW)
    assert not system.link.actions
    system.link.owned.clear()
    system.will.busy = lambda npc: npc == LOBART
    await system.will.tick(NOW + 60)
    assert not system.link.actions


@pytest.mark.parametrize("protected", ["owner", "thief"])
async def test_reclaim_keeps_storyguard_on_both_participants(system, protected):
    plot = "NONE_100_XARDAS"
    owner, culprit = (plot, THIEF) if protected == "owner" else (LOBART, plot)
    system.cards[owner] = character(owner, courage=5)
    system.deeds.record(deed(owner=owner, culprit=culprit, witnesses=[owner]))
    scene(system, actor=owner, target=culprit)
    await system.will.tick(NOW)
    assert not system.link.actions and not system.chronicle.facts("npc_will")


@pytest.mark.parametrize("enabled,hero_crimes", [(False, True), (True, False)])
async def test_existing_npc_crime_switches_disable_motives_and_actions(system, enabled, hero_crimes):
    theft(system, witnesses=[LOBART])
    scene(system)
    system.deeds.enabled = enabled and hero_crimes
    await system.will.tick(NOW)
    assert not system.link.actions
    assert not system.chronicle.facts("npc_motive") and not system.chronicle.facts("npc_will")


async def test_loading_completed_case_does_not_repeat_action_or_motive(system):
    theft(system, witnesses=[LOBART])
    scene(system)
    await system.will.tick(NOW)
    saved = system.memory.head("A")
    system.link.receive(Session(type="session", branch="loaded", parent="A", parent_seq=saved, reason="load"))
    scene(system, now=NOW + 60)
    await system.will.tick(NOW + 60)
    assert len(system.link.actions) == 1
    assert len(system.chronicle.facts("npc_motive")) == 1
    assert len(system.chronicle.facts("npc_will")) == 1


async def test_loading_a_waiting_motive_resumes_it_once_without_another_motive(system):
    theft(system, witnesses=[LOBART])
    scene(system, distance=4000)
    await system.will.tick(NOW)
    saved = system.memory.head("A")
    assert not system.link.actions
    system.link.receive(Session(type="session", branch="loaded", parent="A", parent_seq=saved, reason="load"))
    scene(system, now=NOW + 60)
    await system.will.tick(NOW + 60)
    await system.will.tick(NOW + 120)
    assert len(system.link.actions) == 1
    assert len(system.chronicle.facts("npc_motive")) == 1
    assert len(system.chronicle.facts("npc_will")) == 1


async def test_owner_can_learn_the_theft_from_people_knowledge_rather_than_seeing_it(system):
    case = theft(system, witnesses=[HILDA])
    system.chronicle.record({
        "fact": "rumor", "quest": f"npc_crime_seen:{case['id']}", "kind": "npc_theft",
        "subject": THIEF, "subject_name": "Вино", "about": LOBART, "about_name": "Лобарт",
        "from": HILDA, "from_name": "Хильда", "to": LOBART, "hops": 1, "day": 3,
    })
    scene(system)
    await system.will.tick(NOW)
    assert not system.deeds.seen(LOBART)
    assert [(npc, action) for npc, action, _ in system.link.actions] == [(LOBART, "fight")]
    assert system.chronicle.facts("npc_will")[0]["crime"] == case["id"]


async def test_suspicion_of_an_innocent_person_accuses_once_without_turning_belief_into_truth(system):
    case = theft(system)
    system.cards[LOBART] = character(LOBART, courage=5)
    system.chronicle.record({
        "fact": "npc_crime_suspected", "npc": LOBART, "crime": case["id"], "suspect": HILDA,
    })
    scene(system, target=HILDA)
    await system.will.tick(NOW)
    await system.will.tick(NOW + 60)
    [decision] = system.chronicle.facts("npc_will")
    assert (decision["kind"], decision["target"], decision["act"]) == ("suspect", HILDA, "accuse")
    assert [(npc, action) for npc, action, _ in system.link.actions] == [(LOBART, "say_svm")]
    assert case["culprit"] == THIEF and system.deeds.seen(LOBART) == []
    assert all(entry.kind != "npc_theft" for entry in system.people.of(LOBART))
    assert system.memory.load("A", LOBART).entries


async def test_suspicion_is_once_per_case_suspect_pair_not_once_per_case(system):
    case = theft(system)
    for suspect in (THIEF, HILDA):
        system.chronicle.record({
            "fact": "npc_crime_suspected", "npc": LOBART, "crime": case["id"], "suspect": suspect,
        })
        scene(system, target=suspect)
        await system.will.tick(NOW)
        finish_action(system)
    scene(system)
    await system.will.tick(NOW + 60)
    assert [(npc, action) for npc, action, _ in system.link.actions] == [
        (LOBART, "say_svm"), (LOBART, "say_svm"),
    ]
    assert {fact["target"] for fact in system.chronicle.facts("npc_will")} == {THIEF, HILDA}


async def test_suspected_hero_is_left_for_a_later_layer(system):
    case = theft(system)
    system.chronicle.record({
        "fact": "npc_crime_suspected", "npc": LOBART, "crime": case["id"], "suspect": "PLAYER",
    })
    scene(system)
    await system.will.tick(NOW)
    assert not system.link.actions
    assert not system.chronicle.facts("npc_motive")
    assert not system.crimes.facts()


async def test_greedy_npc_steals_one_item_by_the_native_checked_command(system):
    greed_scene(system)
    await system.will.tick(NOW)
    [motive] = system.chronicle.facts("npc_motive")
    [decision] = system.chronicle.facts("npc_will")
    assert (motive["npc"], motive["kind"], motive["target"]) == (THIEF, "greed", "CHEST_LOBART")
    assert motive["why"]
    assert (decision["npc"], decision["kind"], decision["act"]) == (THIEF, "greed", "steal")
    [action] = system.link.actions
    assert action[:2] == (THIEF, "steal")
    assert action[2] == {
        "chest": "CHEST_LOBART", "what": "ITFO_BREAD", "count": 1, "deed": True,
        "expected_owner": LOBART, "expected_item": "ITFO_BREAD",
    }


@pytest.mark.parametrize("honesty,days", [(1, 2), (2, 4)])
async def test_greed_cannot_repeat_before_its_character_cooldown(system, honesty, days):
    greed_scene(system, honesty=honesty)
    await system.will.tick(NOW)
    finish_action(system)
    before = NOW + days * DAY - 60
    system.moments.now += 60
    refresh(system, before)
    await system.will.tick(before)
    assert len(system.link.actions) == 1
    due = NOW + days * DAY
    system.moments.now += 60
    refresh(system, due)
    await system.will.tick(due)
    assert len(system.link.actions) == 2
    assert len(system.chronicle.facts("npc_will")) == 2


@pytest.mark.parametrize("observer", ["PLAYER", HILDA, "SHEEP"])
async def test_greed_waits_if_the_npc_perceives_someone(system, observer):
    greed_scene(system)
    view = system.link.views[THIEF]
    if observer == "PLAYER":
        view = view.model_copy(update={"hero": view.hero.model_copy(update={"seen": True})})
    else:
        view = NpcView.model_validate({**view.model_dump(), "sees": [{
            "id": observer, "name": observer, "distance": 300,
            "kind": "monster" if observer == "SHEEP" else "human",
        }]})
    system.link.views[THIEF] = view
    system.link.receive(NpcViews(type="npc_views", t=1, views=[view]))
    await system.will.tick(NOW)
    assert not system.link.actions and not system.chronicle.facts("npc_will")


async def test_hidden_witness_in_the_true_nearby_roster_does_not_make_thief_omniscient(system):
    greed_scene(system)
    system.link.nearby = [{"npc": HILDA, "guild": 12, "distance": 200}]
    system.link.receive(NpcRoster(type="npc_roster", t=1, full=False, npcs=[{
        "id": HILDA, "name": "Хильда", "guild": 12, "distance": 300, "activity": "routine",
    }]))
    await system.will.tick(NOW)
    assert system.link.view_queries == [THIEF]
    assert [(npc, action) for npc, action, _ in system.link.actions] == [(THIEF, "steal")]


async def test_greed_rechecks_live_perception_after_the_last_empty_cached_view(system):
    greed_scene(system)
    cached = system.link.views[THIEF]
    system.link.views[THIEF] = NpcView.model_validate({**cached.model_dump(), "sees": [{
        "id": HILDA, "name": "Хильда", "distance": 300,
    }]})
    await system.will.tick(NOW)
    assert system.link.view_queries == [THIEF]
    assert not system.link.actions


@pytest.mark.parametrize("why", ["key", "home"])
async def test_only_another_person_real_owned_chest_is_a_greed_target(system, why):
    greed_scene(system)
    system.link.chests[LOBART][0] = system.link.chests[LOBART][0].model_copy(update={"why": why})
    await system.will.tick(NOW)
    assert not system.link.actions


@pytest.mark.parametrize("distance", [0, 1999, 2000])
async def test_greed_waits_while_chest_owner_is_near_the_chest(system, distance):
    greed_scene(system)
    system.link.chests[LOBART][0] = system.link.chests[LOBART][0].model_copy(update={"distance": distance})
    await system.will.tick(NOW)
    assert not system.link.actions


async def test_greed_uses_only_chests_in_the_thief_ordinary_routine_places(system):
    greed_scene(system)
    system.link.chests[LOBART][0] = system.link.chests[LOBART][0].model_copy(update={"pos": (2001, 0, 0)})
    await system.will.tick(NOW)
    assert not system.link.actions


@pytest.mark.parametrize("target", [
    {"owner": "NONE_100_XARDAS", "item": "ITFO_BREAD", "mission": False},
    {"owner": LOBART, "item": "ITFO_BREAD", "mission": True},
    {"owner": LOBART, "item": "ITFO_BREAD", "mission": False, "owner_state": "ZS_GUARD_PASSAGE"},
])
async def test_greed_keeps_layer_four_storyguard_for_owner_mission_and_posts(system, target):
    greed_scene(system)
    system.link.target = target
    if target.get("owner_state"):
        system.link.receive(NpcRoster(type="npc_roster", t=1, full=False, npcs=[{
            "id": LOBART, "name": "Лобарт", "guild": 12, "distance": 500,
            "activity": "routine", "state": target["owner_state"],
        }]))
    await system.will.tick(NOW)
    assert not system.link.actions and not system.chronicle.facts("npc_will")


async def test_loading_restores_greed_cooldown_and_older_save_has_no_future_cooldown(system):
    greed_scene(system)
    before = system.memory.head("A")
    await system.will.tick(NOW)
    saved = system.memory.head("A")
    system.link.receive(Session(type="session", branch="saved", parent="A", parent_seq=saved, reason="load"))
    greed_scene(system, now=NOW + DAY)
    await system.will.tick(NOW + DAY)
    assert len(system.link.actions) == 1
    assert len(system.chronicle.facts("npc_motive")) == len(system.chronicle.facts("npc_will")) == 1
    system.link.receive(Session(type="session", branch="old", parent="A", parent_seq=before, reason="load"))
    greed_scene(system, now=NOW + DAY)
    await system.will.tick(NOW + DAY)
    assert len(system.link.actions) == 2
    assert len(system.chronicle.facts("npc_motive")) == len(system.chronicle.facts("npc_will")) == 1


def test_loop_guard_rejects_ninth_start_in_a_minute_without_throttling_another_npc():
    moments = SimpleNamespace(now=100.0)
    guard = LoopGuard(lambda: moments.now)
    assert all(guard.claim(LOBART) for _ in range(8))
    assert not guard.claim(LOBART.lower())
    assert guard.refused == 1
    assert guard.claim(THIEF)
    moments.now += 59.9
    assert not guard.claim(LOBART)
    moments.now += 0.1
    assert guard.claim(LOBART)


async def test_loop_backstop_catches_broken_rule_that_keeps_producing_new_cases(system, caplog):
    system.cards[LOBART] = character(LOBART, courage=2)
    for index in range(9):
        theft(system, id=f"broken-rule-{index}", witnesses=[LOBART])
    scene(system)
    with caplog.at_level("WARNING", logger="gothic_ai.npc_will"):
        for _ in range(9):
            await system.will.tick(NOW)
            if system.chronicle.facts("npc_will"):
                finish_action(system)
    assert len(system.link.actions) == len(system.chronicle.facts("npc_will")) == 8
    assert len(system.chronicle.facts("npc_motive")) == 9
    assert system.will.loop_guard.refused >= 1
    assert "npc will loop" in caplog.text
