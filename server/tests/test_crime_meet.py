from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from gothic_ai.chronicle import Chronicle
from gothic_ai.crime_meet import MURDER_REASON, CrimeMeet, reaction_for
from gothic_ai.crime_memory import CrimeMemoryRuntime
from gothic_ai.crime_react import CrimeReact
from gothic_ai.crimes import Crimes
from gothic_ai.memory import MemoryStore
from gothic_ai.protocol import Loading, NpcEvent, NpcRoster, Session
from tests.test_crime_react import GUARD, OTHER_GUARD, ReactLink, card
from tests.test_crimes import HILDA, LOBART

CITIZEN = "VLK_400_CITIZEN"
STRANGER = "BAU_900_STRANGER"


@pytest.mark.parametrize("kind,role,courage,law,expected", [
    ("theft", "owner", 2, False, None),
    ("theft", "owner", 3, False, "demand"),
    ("attack", "owner", 3, False, "demand"),
    ("attack", "owner", 1, False, None),
    ("theft", "guard", 5, True, None),
    ("attack", "guard", 5, True, None),
    ("theft", "member", 5, False, None),
    ("sheep", "owner", 5, False, None),
    ("sheep", "guard", 5, True, None),
    ("murder", "guard", 1, True, "fight"),
    ("murder", "guard", 5, False, None),
    ("murder", "member", 4, False, "fight"),
    ("murder", "member", 3, False, "refuse"),
    ("murder", "member", 2, False, "flee"),
    ("murder", "stranger", 5, False, "refuse"),
    ("murder", "stranger", 2, False, "flee"),
])
def test_role_and_character_table(kind, role, courage, law, expected):
    assert reaction_for(kind, role, courage=courage, law_known=law) == expected


class MeetLink(ReactLink):
    def __init__(self) -> None:
        super().__init__()
        self.native: dict[str, str] = {}
        self.reason: dict[str, str] = {}
        self.refusal: dict[str, int] = {}
        self.projection: dict[str, int] = {}
        self.visible = True
        self.evaluations: list[tuple[str, list[Any]]] = []
        self.before_act: Any = None
        self.before_metadata: asyncio.Event | None = None

    async def story_effect(self, npc, effects, *, check=False):
        await super().story_effect(npc, effects, check=check)
        for effect in effects:
            if effect["do"] == "crime_memory":
                self.projection[npc] = effect["value"]
            elif effect["do"] == "refuse_talk":
                self.refusal[npc] = effect["seconds"]
            elif effect["do"] == "calm":
                self.native[npc] = "ZS_STAND"

    async def npc_act(self, npc, action, /, **args):
        if action in {"fight", "flee"}:
            self.native[npc] = "ZS_ATTACK" if action == "fight" else "ZS_FLEE"
            self.reason[npc] = args.get("reason", "")
        if self.before_act is not None:
            self.before_act()
        return await super().npc_act(npc, action, **args)

    async def story_eval(self, npc, exprs):
        self.evaluations.append((npc, exprs))
        if exprs == [["var", "AIV_ATTACKREASON"]]:
            return [8]
        if len(exprs) == 2 and exprs[0][0] == "field":
            if self.before_metadata is not None:
                await self.before_metadata.wait()
            return [3, 0]
        if len(exprs) == 3 and exprs[1][0] == "==":
            return [
                int(self.native.get(npc) == "ZS_ATTACK"),
                int(self.reason.get(npc) == exprs[1][2][1]),
                int(self.native.get(npc) == "ZS_CLEARROOM"),
            ]
        values = []
        for expr in exprs:
            call = expr[1]
            if call == "NPC_REFUSETALK":
                values.append(self.refusal.get(npc, 0))
            elif call == "NPC_SETREFUSETALK":
                self.refusal[npc] = expr[3][1]
                values.append(0)
            elif call == "NPC_ISINSTATE":
                values.append(int(self.native.get(npc) == expr[3][1]))
            elif call == "AI_CONTINUEROUTINE":
                self.native[npc] = "ZS_STAND"
                values.append(0)
            elif call == "NPC_CLEARAIQUEUE":
                values.append(0)
            else:
                raise AssertionError(expr)
        return values

    async def npc_view(self, npcs):
        return [SimpleNamespace(npc=npc, hero=SimpleNamespace(seen=self.visible)) for npc in npcs]


class Writer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.storylet = SimpleNamespace(stage="open")
        self.gate: asyncio.Event | None = None
        self.started = asyncio.Event()

    async def demand(self, npc, crime):
        self.calls.append((npc, crime))
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return self.storylet


class Approach:
    def __init__(self, link, writer) -> None:
        self.link, self.writer = link, writer
        self.calls: list[tuple[str, str]] = []
        self.started = asyncio.Event()
        self.gate: asyncio.Event | None = None
        self.accept = True
        self.closed = False

    async def approach(self, npc, key, *, valid, told, release):
        assert valid()
        self.calls.append((npc, key))
        async with release():
            assert self.link.projection[npc] == 0
            self.started.set()
            if self.gate is not None:
                await self.gate.wait()
            if self.accept:
                self.writer.storylet.stage = "told"
                assert told()
        return self.accept

    async def close(self):
        self.closed = True


@pytest.fixture
async def system():
    link = MeetLink()
    memory = MemoryStore(":memory:")
    chronicle = Chronicle(link, memory)  # type: ignore[arg-type]
    crimes = Crimes(link, chronicle)  # type: ignore[arg-type]
    reactions = CrimeReact(link, crimes)  # type: ignore[arg-type]
    projection = CrimeMemoryRuntime(link, crimes)  # type: ignore[arg-type]
    writer = Writer()
    approach = Approach(link, writer)
    meets = CrimeMeet(link, crimes, reactions, projection, amends=writer, approach=approach)  # type: ignore
    crimes.people.update({
        LOBART: {"guild": 12, "npctype": 0, "home_room": "LOBART"},
        HILDA: {"guild": 12, "npctype": 0, "home_room": "LOBART"},
        GUARD: {"guild": 2, "npctype": 0},
        OTHER_GUARD: {"guild": 2, "npctype": 0},
        CITIZEN: {"guild": 3, "npctype": 0},
        STRANGER: {"guild": 8, "npctype": 0},
    })
    yield SimpleNamespace(
        link=link, memory=memory, chronicle=chronicle, crimes=crimes,
        react=reactions, projection=projection, meet=meets, writer=writer, approach=approach,
    )
    await meets.close()
    await projection.close()
    await reactions.close()
    await crimes.close()
    if chronicle._background:
        await asyncio.gather(*chronicle._background)
    memory.close()


def case(system, *, crime="case-1", kind="theft", npc=LOBART, degree="seen", community=None):
    value = {
        "fact": "crime", "id": crime, "kind": kind, "owner": LOBART, "victim": LOBART,
        "community": community, "day": 3,
    }
    system.chronicle.record(value)
    if degree == "seen":
        system.chronicle.record({"fact": "crime_seen", "npc": npc, "crime": crime})
    elif degree == "heard":
        system.chronicle.record({"fact": "rumor", "to": npc, "from": HILDA, "crime": crime})
    elif degree == "suspects":
        system.chronicle.record({"fact": "crime_suspected", "npc": npc, "crime": crime})
    return value


def near(system, npc=LOBART, *, event="player_near"):
    system.link.receive(NpcEvent(type="npc_event", npc=npc, kind=event, distance=400))


def roster(system, npc, *, distance=400, state="ZS_STAND", activity="routine"):
    system.link.native[npc] = state
    system.link.receive(NpcRoster(type="npc_roster", t=0, npcs=[{
        "id": npc, "name": npc, "distance": distance, "activity": activity, "state": state,
    }]))


async def finish_demand(system):
    tasks = tuple(system.meet._demands.values())
    assert tasks
    await asyncio.wait_for(asyncio.gather(*tasks), 0.5)


async def test_owner_approaches_for_existing_amends_and_restores_home_memory(system):
    case(system)
    near(system)
    await system.meet.tick()
    await finish_demand(system)
    assert system.writer.calls == [(LOBART, "case-1")]
    assert system.approach.calls == [(LOBART, "crime:case-1")]
    assert system.link.projection[LOBART] == 3
    assert system.chronicle.facts("crime_meet") == [{
        "fact": "crime_meet", "npc": LOBART, "crime": "case-1", "reaction": "demand",
        "event": "player_near", "source": "personal", "role": "owner",
    }]
    assert "demand" in system.meet.summary("case-1")
    await system.meet.tick()
    assert len(system.writer.calls) == 1


async def test_paid_demand_does_not_react_on_next_encounter(system):
    case(system)
    near(system)
    await system.meet.tick()
    await finish_demand(system)
    system.chronicle.record({"fact": "forgiven", "crime": "case-1", "npc": LOBART, "paid": 50})
    await system.meet.tick()
    assert system.link.projection[LOBART] == 0
    roster(system, LOBART, distance=800)
    near(system)
    await system.meet.tick()
    assert len(system.writer.calls) == 1
    assert len(system.chronicle.facts("crime_meet")) == 1


async def test_told_demand_is_not_restarted_at_every_future_meeting(system):
    case(system)
    near(system)
    await system.meet.tick()
    await finish_demand(system)
    roster(system, LOBART, distance=800)
    near(system)
    await system.meet.tick()
    await finish_demand(system)
    assert len(system.approach.calls) == 1
    assert len(system.chronicle.facts("crime_meet")) == 1


async def test_busy_important_approach_reuses_storylet_without_false_demand_fact(system):
    case(system)
    near(system)
    system.approach.accept = False
    await system.meet.tick()
    await finish_demand(system)
    assert not system.chronicle.facts("crime_meet")
    system.approach.accept = True
    await system.meet.tick()
    await finish_demand(system)
    assert len(system.chronicle.facts("crime_meet")) == 1


@pytest.mark.parametrize("degree", ["seen", "heard"])
async def test_coward_owner_keeps_existing_cold_talk_without_approaching(system, degree):
    case(system, degree=degree)
    system.meet.cards = {LOBART: card(LOBART, courage=2)}
    near(system)
    await system.meet.tick()
    assert not system.writer.calls
    assert not system.link.actions
    assert system.crimes.distrust(LOBART)


@pytest.mark.parametrize("degree", ["suspects", None])
async def test_suspicion_or_ownership_alone_cannot_start_demand(system, degree):
    case(system, degree=degree)
    near(system)
    await system.meet.tick()
    assert not system.writer.calls
    assert not system.chronicle.facts("crime_meet")


async def test_law_informs_guard_who_never_saw_the_murder(system):
    case(system, kind="murder", npc=CITIZEN, community="city")
    assert not system.crimes.known(GUARD)
    system.chronicle.record({"fact": "crime_known", "community": "city", "crime": "case-1"})
    near(system, GUARD)
    await system.meet.tick()
    assert system.link.actions == [(GUARD, "fight", {"npc": "PLAYER", "reason": MURDER_REASON})]
    assert system.chronicle.facts("crime_meet")[-1]["source"] == "law"
    assert not system.crimes.known(GUARD)  # no manufactured eyewitness or rumor
    await system.meet.tick()
    assert len(system.link.actions) == 1


async def test_guard_waits_for_law_and_line_of_sight_then_uses_pending_near_event(system):
    case(system, kind="murder", npc=GUARD, community="city")
    near(system, GUARD)
    await system.meet.tick()
    assert not system.link.actions
    system.chronicle.record({"fact": "crime_known", "community": "city", "crime": "case-1"})
    system.link.visible = False
    await system.meet.tick()
    assert not system.link.actions
    system.link.visible = True
    await system.meet.tick()
    assert system.link.actions[-1][1] == "fight"


async def test_other_community_law_does_not_authorize_guard_attack(system):
    case(system, kind="murder", npc=CITIZEN, community="farm")
    system.chronicle.record({"fact": "crime_known", "community": "farm", "crime": "case-1"})
    near(system, GUARD)
    await system.meet.tick()
    assert not system.link.actions


@pytest.mark.parametrize("courage,expected", [(2, "flee"), (3, "refuse"), (4, "fight")])
async def test_heard_murder_depends_on_courage_and_victims_community(system, courage, expected):
    case(system, kind="murder", npc=CITIZEN, community="city", degree="heard")
    system.meet.cards = {CITIZEN: card(CITIZEN, courage=courage)}
    near(system, CITIZEN)
    await system.meet.tick()
    fact = system.chronicle.facts("crime_meet")[-1]
    assert fact["reaction"] == expected
    if expected == "fight":
        assert system.link.actions[-1][2]["reason"] == MURDER_REASON
    else:
        assert all(action != "fight" for _, action, _ in system.link.actions)
        assert system.link.refusal[CITIZEN] == 2


async def test_brave_outsider_refuses_instead_of_avenging_another_community(system):
    case(system, kind="murder", npc=STRANGER, community="city", degree="heard")
    system.meet.cards = {STRANGER: card(STRANGER, courage=5)}
    near(system, STRANGER)
    await system.meet.tick()
    assert [action for _, action, _ in system.link.actions] == ["say_svm", "turn_away"]
    assert system.link.actions[0][2]["svm"] == "NOTNOW"
    assert system.chronicle.facts("crime_meet")[-1]["reaction"] == "refuse"


@pytest.mark.parametrize("kind", ["theft", "attack", "sheep"])
async def test_guard_never_starts_meeting_fight_for_lesser_known_cases(system, kind):
    case(system, kind=kind, npc=GUARD, community="city")
    system.chronicle.record({"fact": "crime_known", "community": "city", "crime": "case-1"})
    near(system, GUARD)
    await system.meet.tick()
    assert not system.link.actions


@pytest.mark.parametrize("settlement", ["silenced", "forgiven", "paid", "died"])
@pytest.mark.parametrize("courage", [2, 3, 4])
async def test_every_personal_settlement_clears_active_reaction_in_same_poll(system, settlement, courage):
    case(system, kind="murder", npc=CITIZEN, community="city", degree="heard")
    system.meet.cards = {CITIZEN: card(CITIZEN, courage=courage)}
    near(system, CITIZEN)
    await system.meet.tick()
    fact = {"fact": settlement, "npc": CITIZEN, "crime": "case-1"}
    if settlement == "paid":
        fact.update(fact="forgiven", paid=50)
    system.chronicle.record(fact)
    await system.meet.tick()
    assert CITIZEN not in system.meet._active
    assert system.link.projection[CITIZEN] == 0
    if settlement != "died":
        assert system.link.native.get(CITIZEN, "ZS_STAND") == "ZS_STAND"
        assert system.link.refusal.get(CITIZEN, 0) == 0
    assert len(system.chronicle.facts("crime_meet")) == 1


async def test_absolution_stops_law_guard_without_dropping_citizens_personal_claim(system):
    case(system, kind="murder", npc=CITIZEN, community="city", degree="heard")
    system.chronicle.record({"fact": "crime_known", "community": "city", "crime": "case-1"})
    near(system, GUARD)
    near(system, CITIZEN)
    await system.meet.tick()
    system.chronicle.record({"fact": "absolved", "community": "city", "crimes": ["case-1"]})
    await system.meet.tick()
    assert GUARD not in system.meet._active
    assert system.link.native[GUARD] == "ZS_STAND"
    assert CITIZEN in system.meet._active
    assert system.link.refusal[CITIZEN] == 2


@pytest.mark.parametrize("settlement", ["silenced", "forgiven"])
async def test_guard_personal_agreement_does_not_erase_law_or_bind_other_guards(system, settlement):
    case(system, kind="murder", degree=None, community="city")
    system.chronicle.record({"fact": "crime_known", "community": "city", "crime": "case-1"})
    system.chronicle.record({"fact": settlement, "npc": GUARD, "crime": "case-1"})
    near(system, GUARD)
    near(system, OTHER_GUARD)
    await system.meet.tick()
    assert [npc for npc, action, _ in system.link.actions if action == "fight"] == [OTHER_GUARD]
    assert len(system.meet.law.unpaid("city")) == 1


async def test_settlement_never_calms_a_later_unrelated_fight(system):
    case(system, kind="murder", npc=CITIZEN, community="city", degree="heard")
    system.meet.cards = {CITIZEN: card(CITIZEN, courage=4)}
    near(system, CITIZEN)
    await system.meet.tick()
    system.link.reason[CITIZEN] = "AR_NONE"
    system.chronicle.record({"fact": "forgiven", "npc": CITIZEN, "crime": "case-1"})
    await system.meet.tick()
    assert not any(e["do"] == "calm" for _, effects, _ in system.link.effects for e in effects)
    assert system.link.native[CITIZEN] == "ZS_ATTACK"


async def test_preexisting_native_refusal_is_not_cleared_by_our_settlement(system):
    case(system, kind="murder", npc=CITIZEN, community="city", degree="heard")
    system.link.refusal[CITIZEN] = 30
    near(system, CITIZEN)
    await system.meet.tick()
    system.chronicle.record({"fact": "forgiven", "npc": CITIZEN, "crime": "case-1"})
    await system.meet.tick()
    assert system.link.refusal[CITIZEN] == 30


@pytest.mark.parametrize("during", ["writer", "walk"])
async def test_payment_cancels_pending_work_and_never_restores_stale_memory(system, during):
    case(system)
    near(system)
    gate = asyncio.Event()
    if during == "writer":
        system.writer.gate = gate
        started = system.writer.started
    else:
        system.approach.gate = gate
        started = system.approach.started
    await system.meet.tick()
    await asyncio.wait_for(started.wait(), 0.5)
    system.chronicle.record({"fact": "forgiven", "crime": "case-1", "npc": LOBART, "paid": 50})
    await system.meet.tick()
    assert not system.meet._active
    assert system.link.projection[LOBART] == 0
    assert not system.chronicle.facts("crime_meet")


async def test_payment_during_action_receipt_rolls_back_the_just_started_native_fight(system):
    case(system, kind="murder", npc=CITIZEN, community="city", degree="heard")
    system.meet.cards = {CITIZEN: card(CITIZEN, courage=4)}
    near(system, CITIZEN)
    system.link.before_act = lambda: system.chronicle.record({
        "fact": "forgiven", "crime": "case-1", "npc": CITIZEN,
    })
    await system.meet.tick()
    assert system.link.native[CITIZEN] == "ZS_STAND"
    assert system.link.projection[CITIZEN] == 0
    assert not system.chronicle.facts("crime_meet")


async def test_loading_cancels_approach_and_rebuilds_only_new_branch_knowledge(system):
    case(system)
    near(system)
    system.approach.gate = asyncio.Event()
    await system.meet.tick()
    await asyncio.wait_for(system.approach.started.wait(), 0.5)
    old = tuple(system.meet._demands.values())
    system.link.receive(Loading(type="loading"))
    system.link.receive(Session(type="session", branch="B"))
    await asyncio.gather(*old, return_exceptions=True)
    assert system.projection._suspended == {}
    assert not system.meet._active
    near(system)
    await system.meet.tick()
    assert not system.chronicle.facts("crime_meet")
    assert len(system.writer.calls) == 1
    case(system, crime="branch-b", kind="murder", npc=CITIZEN, community="city", degree="heard")
    near(system, CITIZEN)
    await system.meet.tick()
    assert system.chronicle.facts("crime_meet")[-1]["crime"] == "branch-b"


async def test_metadata_await_revalidates_the_current_branch_before_acting(system):
    case(system, kind="murder", npc=CITIZEN, community="city", degree="heard")
    system.crimes.people.pop(CITIZEN)
    system.link.before_metadata = asyncio.Event()
    near(system, CITIZEN)
    task = asyncio.create_task(system.meet.tick())
    await asyncio.sleep(0)
    system.link.receive(Session(type="session", branch="B"))
    system.link.before_metadata.set()
    await task
    assert not system.link.actions
    assert not system.chronicle.facts("crime_meet")


async def test_native_known_theft_home_warning_is_observed_without_new_fight_mechanism(system):
    case(system, npc=HILDA)
    roster(system, HILDA, state="ZS_CLEARROOM")
    near(system, HILDA, event="enter_room")
    await system.meet.tick()
    assert system.link.projection[HILDA] == 3
    assert not system.link.actions
    assert system.chronicle.facts("crime_meet")[-1] == {
        "fact": "crime_meet", "npc": HILDA, "crime": "case-1", "reaction": "room",
        "event": "enter_room", "source": "personal", "role": "member",
    }
    system.chronicle.record({"fact": "silenced", "npc": HILDA, "crime": "case-1"})
    await system.meet.tick()
    assert system.link.projection[HILDA] == 0
    assert system.link.native[HILDA] == "ZS_STAND"


async def test_suspicion_cannot_be_reported_as_a_native_home_reaction(system):
    case(system, npc=HILDA, degree="suspects")
    roster(system, HILDA, state="ZS_CLEARROOM")
    near(system, HILDA, event="enter_room")
    await system.meet.tick()
    assert not system.chronicle.facts("crime_meet")


async def test_busy_body_waits_without_interfering_with_other_combat_provider(system):
    case(system, kind="murder", npc=CITIZEN, community="city", degree="heard")
    system.meet.cards = {CITIZEN: card(CITIZEN, courage=4)}
    system.link.owned.add(CITIZEN)
    near(system, CITIZEN)
    await system.meet.tick()
    assert not system.link.actions
    system.link.owned.clear()
    await system.meet.tick()
    assert system.link.actions[-1][1] == "fight"


async def test_close_removes_the_important_approach_listener(system):
    await system.meet.close()
    assert system.approach.closed


@pytest.mark.parametrize("settlement", ["silenced", "forgiven", "paid", "died", "loading"])
async def test_conversational_refusal_reads_live_knowledge_without_waiting_for_poll(system, settlement):
    case(system, kind="murder", npc=CITIZEN, community="city", degree="heard")
    assert system.meet.refuses(CITIZEN.lower())
    if settlement == "loading":
        system.link.receive(Loading(type="loading"))
    else:
        system.chronicle.record({
            "fact": "forgiven" if settlement == "paid" else settlement,
            "npc": CITIZEN, "crime": "case-1", **({"paid": 50} if settlement == "paid" else {}),
        })
    assert not system.meet.refuses(CITIZEN)


async def test_refusal_does_not_turn_a_suspicion_into_a_conversation_ban(system):
    case(system, kind="murder", npc=CITIZEN, community="city", degree="suspects")
    assert not system.meet.refuses(CITIZEN)


@pytest.mark.parametrize("settlement", ["silenced", "forgiven"])
async def test_guard_agreement_ends_only_its_active_enforcement_same_poll(system, settlement):
    case(system, kind="murder", degree=None, community="city")
    system.chronicle.record({"fact": "crime_known", "community": "city", "crime": "case-1"})
    near(system, GUARD)
    near(system, OTHER_GUARD)
    await system.meet.tick()
    system.chronicle.record({"fact": settlement, "npc": GUARD, "crime": "case-1"})
    await system.meet.tick()
    assert system.link.native[GUARD] == "ZS_STAND"
    assert system.link.native[OTHER_GUARD] == "ZS_ATTACK"
    assert len(system.meet.law.unpaid("city")) == 1


async def test_home_settlement_stops_its_tracked_nonlethal_fight(system):
    case(system, npc=HILDA)
    roster(system, HILDA, state="ZS_CLEARROOM")
    near(system, HILDA, event="enter_room")
    await system.meet.tick()
    system.link.native[HILDA] = "ZS_ATTACK"
    system.link.reason[HILDA] = "AR_CLEARROOM"
    system.chronicle.record({"fact": "forgiven", "npc": HILDA, "crime": "case-1"})
    await system.meet.tick()
    assert system.link.projection[HILDA] == 0
    assert system.link.native[HILDA] == "ZS_STAND"


async def test_fact_wake_and_poll_do_not_cancel_a_writer_rollback_twice(system, monkeypatch):
    case(system)
    near(system)
    writing, rolling_back, finish_rollback = (asyncio.Event() for _ in range(3))
    cancellations = []

    async def demand(npc, crime):
        writing.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            rolling_back.set()
            await finish_rollback.wait()
            cancellations.append(asyncio.current_task().cancelling())
            raise

    monkeypatch.setattr(system.writer, "demand", demand)
    await system.meet.tick()
    await writing.wait()
    system.chronicle.record({"fact": "forgiven", "npc": LOBART, "crime": "case-1"})
    await rolling_back.wait()
    poll = asyncio.create_task(system.meet.tick())
    await asyncio.sleep(0)
    finish_rollback.set()
    await poll
    assert cancellations == [1]
    assert LOBART not in system.meet._active
