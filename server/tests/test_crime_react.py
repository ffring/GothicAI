from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from gothic_ai import crime_react
from gothic_ai.characters import Card
from gothic_ai.chronicle import Chronicle
from gothic_ai.crime_law import CrimeLaw
from gothic_ai.crime_react import CrimeReact, reaction_for
from gothic_ai.crimes import Crimes
from gothic_ai.memory import MemoryStore
from gothic_ai.protocol import HoldVerdictCmd, NpcActResult, NpcRoster, PerceptionHeld
from tests.test_crimes import HILDA, LOBART, Link, deed

GUARD = "MIL_305_JERGEL"
OTHER_GUARD = "MIL_306_GUARD"
MATTEO = "VLK_406_MATTEO"
CITIZEN = "VLK_400_CITIZEN"


@pytest.mark.parametrize("kind,role,courage,honesty,guards,expected", [
    ("theft", "owner", 3, 3, False, "fight"),
    ("theft", "owner", 2, 3, False, "demand"),
    ("theft", "owner", 2, 3, True, "call_guards"),
    ("theft", "guard", 1, 1, False, "fight"),
    ("theft", "member", 3, 4, True, "call_guards"),
    ("theft", "member", 3, 4, False, "shout"),
    ("theft", "member", 3, 2, True, "silent"),
    ("theft", "member", 3, 3, True, "shout"),
    ("theft", "stranger", 5, 5, True, "ignore"),
    ("attack", "owner", 5, 5, True, None),
    ("attack", "guard", 1, 1, False, "fight"),
    ("attack", "member", 4, 3, False, "fight"),
    ("attack", "member", 3, 3, False, "shout"),
    ("attack", "member", 2, 3, False, "flee"),
    ("attack", "stranger", 1, 5, True, "ignore"),
    ("murder", "owner", 5, 5, True, None),
    ("murder", "guard", 1, 1, False, "fight"),
    ("murder", "member", 4, 3, False, "fight"),
    ("murder", "member", 3, 3, False, "flee"),
    ("murder", "stranger", 2, 3, False, "flee"),
    ("murder", "stranger", 3, 3, False, "silent"),
    ("sheep", "owner", 1, 1, False, "fight"),
    ("sheep", "guard", 1, 1, False, "fight"),
    ("sheep", "member", 3, 3, False, "shout"),
    ("sheep", "stranger", 5, 5, False, "ignore"),
    ("enter_room", "owner", 5, 5, True, None),
])
def test_reaction_table(kind, role, courage, honesty, guards, expected):
    reaction = reaction_for(kind, role, courage=courage, honesty=honesty, guards=guards)
    assert (reaction.kind if reaction else None) == expected


def test_vanilla_fight_reasons_keep_theft_nonlethal_and_murder_native():
    assert reaction_for("theft", "guard").fight_reason == "AR_THEFT"
    assert reaction_for("murder", "guard").fight_reason == "AR_HUMANMURDEREDHUMAN"
    assert reaction_for("sheep", "guard").fight_reason == "AR_SHEEPKILLER"
    assert reaction_for("attack", "guard").fight_reason == "AR_GUARDSTOPSFIGHT"


class ReactLink(Link):
    def __init__(self) -> None:
        super().__init__()
        self.actions: list[tuple[str, str, dict[str, Any]]] = []
        self.effects: list[tuple[str, list[dict[str, Any]], bool]] = []
        self.people: list[dict[str, Any]] = []
        self.nearby: list[dict[str, Any]] = []
        self.accept = True
        self.ack = True
        self.owned: set[str] = set()
        self.eval_values = [3, 0]

    def body_owned(self, npc):
        return npc in self.owned

    async def story_people(self):
        return self.people

    async def story_eval(self, npc, exprs):
        return self.eval_values

    async def story_nearby(self, npc, radius):
        return self.nearby

    async def story_effect(self, npc, effects, *, check=False):
        self.effects.append((npc, effects, check))

    async def npc_act(self, npc, action, /, **args):
        self.actions.append((npc, action, args))
        action_id = f"a{len(self.actions)}"
        if self.ack:
            self.receive(NpcActResult(
                type="npc_act_result", id=action_id, npc=npc,
                status="queued" if self.accept else "rejected", detail="busy" if not self.accept else None,
            ))
        return action_id


def card(npc, *, courage=3, honesty=3):
    return Card(npc, npc, "", "", 3, honesty, courage, 3, 3, 3)


@pytest.fixture
async def system():
    link = ReactLink()
    memory = MemoryStore(":memory:")
    chronicle = Chronicle(link, memory)  # type: ignore[arg-type]
    crimes = Crimes(link, chronicle)  # type: ignore[arg-type]
    law = CrimeLaw(crimes)
    reactions = CrimeReact(link, crimes, law)  # type: ignore[arg-type]
    crimes.people.update({
        HILDA: {"guild": 12, "npctype": 0, "home_room": "LOBART"},
        LOBART: {"guild": 12, "npctype": 0, "home_room": "LOBART"},
        GUARD: {"guild": 2, "npctype": 0},
        OTHER_GUARD: {"guild": 2, "npctype": 0},
        MATTEO: {"guild": 3, "npctype": 0},
        CITIZEN: {"guild": 3, "npctype": 0},
    })
    yield SimpleNamespace(
        link=link, memory=memory, chronicle=chronicle, crimes=crimes, law=law, react=reactions,
    )
    await reactions.close()
    await crimes.close()
    if chronicle._background:
        await asyncio.gather(*chronicle._background)
    memory.close()


def held(**fields):
    return PerceptionHeld(
        type="perception_held", hold="h1", npc=HILDA, other="PLAYER", kind="theft", deed="deed-1", **fields,
    )


def crime(system, *, npc=HILDA, owner=LOBART, guild=12):
    system.link.receive(deed(
        owner=owner, owner_npc_guild=guild, witnesses=[npc],
        witnesses_metadata=[{"npc": npc, "guild": system.crimes.people.get(npc, {}).get("guild", 3),
                             "npctype": 0}],
    ))
    return system.crimes.crime("deed-1")


async def finish(system):
    # Only holds, not the separate post-combat restitution task, need to finish here.
    for _ in range(30):
        if system.react.history:
            return system.react.history[-1]
        await asyncio.sleep(0.002)
    raise AssertionError("hold was not settled")


def verdicts(system):
    return [message for message in system.link.sent if isinstance(message, HoldVerdictCmd)]


async def test_roles_use_true_ownership_community_and_real_shared_house(system):
    case = crime(system)
    assert system.react.role(LOBART, case) == "owner"
    assert system.react.role(HILDA, case) == "member"
    system.crimes.people[HILDA]["home_room"] = "ANOTHER_HOUSE"
    assert system.react.role(HILDA, case) == "stranger"
    del system.crimes.people[HILDA]["home_room"]
    assert system.react.role(HILDA, case) is None
    city = {**case, "community": "city", "owner": MATTEO, "victim": MATTEO}
    assert system.react.role(GUARD, city) == "guard"
    assert system.react.role("MIL_311_ANDRE", city) == "guard"
    assert system.react.role(CITIZEN, city) == "member"
    system.crimes.people["BANDIT"] = {"guild": 9, "npctype": 5}
    assert system.react.role("BANDIT", {**city, "community": "bl"}) == "stranger"


async def test_room_and_chest_owner_are_owners_even_when_item_owner_is_another_npc(system):
    system.link.receive(deed(
        owner=LOBART, owner_npc_guild=12, room_owner=HILDA, room_owner_guild=12,
        chest_owner="CHESTKEEPER", chest_owner_guild=12,
    ))
    case = system.crimes.crime("deed-1")
    assert system.react.role(HILDA, case) == "owner"
    assert system.react.role("CHESTKEEPER", case) == "owner"


async def test_hold_waits_for_crime_and_keeps_real_seen_before_one_server_verdict(system):
    system.link.receive(held())
    await asyncio.sleep(0)
    assert not verdicts(system)
    crime(system)
    row = await finish(system)
    assert row["role"] == "member" and row["reaction"] == "shout"
    assert system.crimes.known(HILDA)[0].degree == "seen"
    assert system.link.actions == [(HILDA, "say_svm", {"svm": "DIRTYTHIEF", "overlay": True})]
    assert [(v.hold, v.to) for v in verdicts(system)] == [("h1", "server")]
    system.link.receive(held())
    await asyncio.sleep(0)
    assert len(verdicts(system)) == 1
    assert "shout" in system.react.summary("deed-1")


async def test_missing_card_uses_three_and_projection_precedes_fight(system):
    seen = []

    async def project(npc):
        seen.append((npc, list(system.link.actions)))

    system.react.project = project
    crime(system, npc=LOBART)
    system.link.receive(held().model_copy(update={"npc": LOBART}))
    row = await finish(system)
    assert row["reaction"] == "fight"
    assert seen == [(LOBART, [])]
    assert system.link.actions[-1] == (LOBART, "fight", {"npc": "PLAYER", "reason": "AR_THEFT"})


async def test_silence_and_ignore_are_valid_server_verdicts_without_body_action(system):
    system.react.cards = {HILDA: card(HILDA, honesty=2)}
    crime(system)
    system.link.receive(held())
    assert (await finish(system))["reaction"] == "silent"
    assert verdicts(system)[0].to == "server"
    assert system.link.actions == []
    assert system.crimes.known(HILDA)[0].degree == "seen"


@pytest.mark.parametrize("failure", ["missing_crime", "missing_deed", "busy", "owned", "unknown_role"])
async def test_uncertain_hold_returns_to_scripts_exactly_once(system, monkeypatch, failure):
    monkeypatch.setattr(crime_react, "HOLD_SECONDS", 0.02)
    if failure != "missing_crime":
        crime(system)
    message = held()
    if failure == "missing_deed":
        message = message.model_copy(update={"deed": None})
    elif failure == "busy":
        system.link.receive(NpcRoster(type="npc_roster", t=0, npcs=[{
            "id": HILDA, "name": "Хильда", "guild": 12, "distance": 200, "activity": "dialog",
        }]))
    elif failure == "owned":
        system.link.owned.add(HILDA)
    elif failure == "unknown_role":
        del system.crimes.people[HILDA]["home_room"]
    system.link.receive(message)
    row = await finish(system)
    expected = "busy" if failure == "owned" else failure
    assert row["verdict"] == "scripts" and row["reason"] == expected
    assert len(verdicts(system)) == 1 and system.link.actions == []


@pytest.mark.parametrize("accept,ack,reason", [(False, True, "action_rejected:busy"),
                                            (True, False, "deadline")])
async def test_body_receipt_failure_returns_perception_to_scripts(system, monkeypatch, accept, ack, reason):
    monkeypatch.setattr(crime_react, "HOLD_SECONDS", 0.02)
    system.link.accept, system.link.ack = accept, ack
    crime(system)
    system.link.receive(held())
    row = await finish(system)
    assert row["verdict"] == "scripts" and row["reason"] == reason
    assert len(verdicts(system)) == 1


async def test_other_threats_remain_for_the_planner(system):
    system.link.receive(held().model_copy(update={"deed": None, "other": "WOLF"}))
    await asyncio.sleep(0)
    assert not system.react._tasks and not verdicts(system)


async def test_called_guards_hear_and_inform_law_and_nearest_free_guard_fights(system):
    system.react.cards = {CITIZEN: card(CITIZEN, honesty=4)}
    system.link.nearby = [
        {"npc": OTHER_GUARD, "guild": 2, "distance": 900},
        {"npc": GUARD, "guild": 2, "distance": 300},
        {"npc": "SLD_800_LEE", "guild": 7, "distance": 100},
    ]
    system.crimes.people["SLD_800_LEE"] = {"guild": 7, "npctype": 0}
    crime(system, npc=CITIZEN, owner=MATTEO, guild=3)
    system.link.receive(held().model_copy(update={"npc": CITIZEN}))
    row = await finish(system)
    assert row["reaction"] == "call_guards" and row["verdict"] == "server"
    assert system.crimes.known(GUARD)[0].degree == "heard"
    assert system.crimes.known(OTHER_GUARD)[0].source == CITIZEN
    assert system.chronicle.facts("crime_known")[0]["how"] == "called"
    assert system.link.actions[-1] == (GUARD, "fight", {"npc": "PLAYER", "reason": "AR_GUARDCALLEDTOTHIEF"})
    assert system.crimes.known("SLD_800_LEE") == []


async def test_guard_witness_fights_and_law_knows_without_model(system):
    crime(system, npc=GUARD, owner=MATTEO, guild=3)
    system.link.receive(held().model_copy(update={"npc": GUARD}))
    row = await finish(system)
    assert row["role"] == "guard" and row["reaction"] == "fight"
    assert system.law.tick(3 * 1440 + 9 * 60 + 15)[0]["how"] == "guard"


async def test_prepare_uses_real_house_metadata_and_preserves_npctype(system):
    system.link.people = [{"npc": HILDA, "guild": 12, "home_room": "NW_HOUSE_LOBART"}]
    await system.react.prepare()
    assert system.crimes.people[HILDA] == {
        "npc": HILDA, "guild": 12, "npctype": 0, "home_room": "NW_HOUSE_LOBART",
    }


async def test_restitution_only_moves_existing_goods_after_proven_unconsciousness(system):
    case = crime(system)
    system.link.eval_values = [1, 0, 0, 1, 0, 1]
    await system.react._reclaim(case, LOBART, 0)
    assert system.link.effects == [(LOBART, [{
        "do": "give", "item": "ITFO_BREAD", "count": 1, "to": "self",
    }], True)]
    assert system.chronicle.facts("crime_restitution")[0]["how"] == "defeated_thief"


@pytest.mark.parametrize("values", [[1, 1, 0, 1, 1, 0], [1, 0, 1, 1, 1, 0], [1, 0, 0, 0, 1, 0]])
async def test_dead_hero_dead_witness_or_missing_goods_never_restitute(system, values):
    system.link.eval_values = values
    await system.react._reclaim(crime(system), LOBART, 0)
    assert not system.link.effects and not system.chronicle.facts("crime_restitution")


async def test_low_hp_or_unrelated_state_does_not_count_as_a_defeated_thief(system):
    system.link.eval_values = [0, 0, 0, 1, 1, 0]
    moments = iter([0.0, 0.0, 181.0])
    system.react.clock = lambda: next(moments)
    await system.react._reclaim(crime(system), LOBART, 0)
    assert not system.link.effects


async def test_world_change_cancels_pending_hold_without_writing_into_loaded_branch(system):
    system.link.receive(held())
    await asyncio.sleep(0)
    system.link.receive(system.link.session.model_copy(update={"branch": "B"}))
    await asyncio.gather(*tuple(system.react._tasks), return_exceptions=True)
    assert not system.react.history and not verdicts(system)
