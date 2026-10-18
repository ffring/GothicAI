from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from gothic_ai.chronicle import Chronicle
from gothic_ai.crimes import COMMUNITIES, Crimes, pickpocket_outcome
from gothic_ai.gamedata.storygraph import Node, StoryGraph
from gothic_ai.memory import MemoryStore
from gothic_ai.protocol import HeroDeed, Session

LOBART = "BAU_950_LOBART"
HILDA = "BAU_951_HILDA"
LEE = "SLD_800_LEE"


class Link:
    def __init__(self) -> None:
        self.listeners: list[Any] = []
        self.session = Session(type="session", branch="A")
        self.sent: list[Any] = []

    async def send(self, message: Any) -> None:
        self.sent.append(message)

    def receive(self, message: Any) -> None:
        for listener in list(self.listeners):
            listener(message)


@pytest.fixture
async def system():
    link = Link()
    memory = MemoryStore(":memory:")
    chronicle = Chronicle(link, memory)  # type: ignore[arg-type]
    crimes = Crimes(link, chronicle)  # type: ignore[arg-type]
    yield SimpleNamespace(link=link, memory=memory, chronicle=chronicle, crimes=crimes)
    await crimes.close()
    if chronicle._background:
        await asyncio.gather(*chronicle._background)
    memory.close()


def deed(**fields: Any) -> HeroDeed:
    return HeroDeed.model_validate(
        {
            "type": "hero_deed",
            "id": "deed-1",
            "kind": "take_item",
            "item": "ITFO_BREAD",
            "count": 1,
            "hero_guild": 0,
            "position": {"x": 12, "y": 23, "z": 34},
            "waypoint": "FARM1_IN",
            "day": 3,
            "hour": 9,
            "minute": 15,
            **fields,
        }
    )


@pytest.mark.parametrize(
    "ownership",
    [
        {},  # an unowned thing in the field
        {"room_guild": 15},  # GIL_PUBLIC
        {"room_guild": 0},
        {"owner": "PC_HERO", "room_guild": 8},
        {"owner": "PLAYER", "owner_guild": 8},
        {"owner_guild": 8, "hero_guild": 8},
        {"room_owner": "HERO", "room_guild": 8},
        {"room_guild": 8, "hero_guild": 8},
        {"chest": "CHEST", "chest_owner": "PC_HERO", "room_guild": 8},
        {"chest": "CHEST", "chest_guild": 8, "hero_guild": 8},
        {"item_dropped": True, "room_guild": 8},
    ],
)
async def test_own_public_and_dropped_items_are_not_crimes(system, ownership):
    assert system.crimes.record(deed(**ownership)) is None
    assert system.chronicle.facts() == []


@pytest.mark.parametrize(
    "ownership,owner,community",
    [
        ({"owner": LOBART, "owner_npc_guild": 8}, LOBART, "farm"),
        ({"owner_guild": 8}, "", "farm"),
        ({"room_owner": LOBART, "room_owner_guild": 8}, LOBART, "farm"),
        ({"room_guild": 8}, "", "farm"),
        ({"chest": "CHEST", "chest_owner": LOBART, "chest_owner_guild": 8}, LOBART, "farm"),
        ({"chest": "CHEST", "chest_guild": 8}, "", "farm"),
        ({"owner": LOBART, "owner_npc_guild": 8, "room_owner": "PC_HERO"}, LOBART, "farm"),
        ({"owner": LOBART, "owner_npc_guild": 8, "room_guild": 15}, LOBART, "farm"),
        ({"owner": LOBART, "owner_npc_guild": 8, "item_dropped": True}, LOBART, "farm"),
        ({"owner": "UNKNOWN_NPC"}, "UNKNOWN_NPC", None),
    ],
)
async def test_foreign_item_room_or_chest_is_theft(system, ownership, owner, community):
    result = system.crimes.record(deed(**ownership, witnesses=[HILDA, HILDA.lower()]))
    assert result is not None
    assert result == {
        "fact": "crime",
        "id": "deed-1",
        "kind": "theft",
        "victim": owner,
        "owner": owner,
        "community": community,
        "item": "ITFO_BREAD",
        "count": 1,
        "place": {"x": 12, "y": 23, "z": 34},
        "waypoint": "FARM1_IN",
        "day": 3,
        "hour": 9,
        "minute": 15,
        "witnesses": [HILDA],
    }
    assert system.chronicle.facts("crime_seen") == [
        {"fact": "crime_seen", "npc": HILDA, "crime": "deed-1"},
    ]


async def test_opening_foreign_chest_is_raw_until_an_item_was_taken(system):
    result = system.crimes.record(
        deed(
            kind="use_mob",
            item="",
            count=0,
            chest="CHEST_LOBART",
            chest_owner=LOBART,
            chest_owner_guild=8,
        )
    )
    assert result is None
    assert system.chronicle.facts("crime_seen") == []
    result = system.crimes.record(
        deed(
            id="deed-2",
            chest="CHEST_LOBART",
            chest_owner=LOBART,
            chest_owner_guild=8,
        )
    )
    assert result is not None and result["kind"] == "theft"
    assert result["count"] == 1 and result["item"] == "ITFO_BREAD" and result["witnesses"] == []


async def test_unprovoked_attack_records_the_victims_id_and_real_witnesses(system):
    system.link.receive(deed(kind="attack", victim=LOBART, victim_guild=8, witnesses=[HILDA, LOBART]))
    (result,) = system.crimes.facts()
    assert result["kind"] == "attack" and result["victim"] == LOBART
    assert result["community"] == "farm" and result["witnesses"] == [HILDA, LOBART]


@pytest.mark.parametrize(
    "context",
    [
        {"victim_in_fight": True},  # already ZS_Attack: this blow did not start another assault
        {"victim_started_fight": True},  # defending the hero
        {"victim_enemy": True},  # B_AssessEnemy's hostile target
        {"victim_tough_guy": True},
        {"victim_npctype": 2},  # NPCTYPE_FRIEND
        {"victim_guild": 7},  # GIL_SLD (a mercenary, not GIL_BAU=8)
        {"victim_guild": 6},
        {"victim_guild": 9},
        {"victim_guild": 13},
        {"victim_is_monster": True},
        {"victim_guild": 18},
    ],
)
async def test_combat_retaliation_enemies_and_vanilla_tough_guys_are_not_assaults(system, context):
    fields = {"kind": "attack", "victim": LOBART, "victim_guild": 8, **context}
    assert system.crimes.record(deed(**fields)) is None
    assert system.crimes.facts() == []


async def test_human_kill_is_murder_even_after_assault_or_finishing_blow(system):
    result = system.crimes.record(
        deed(
            kind="murder",
            victim=LOBART,
            victim_guild=8,
            victim_in_fight=True,
            witnesses=[HILDA],
        )
    )
    assert result is not None and result["kind"] == "murder" and result["victim"] == LOBART
    assert result["community"] == "farm"


async def test_monster_kill_is_not_human_murder(system):
    assert system.crimes.record(deed(kind="murder", victim="WOLF", victim_guild=22)) is None


async def test_foreign_sheep_uses_actual_witness_community_without_inventing_its_owner(system):
    result = system.crimes.record(
        deed(
            kind="murder",
            victim="SHEEP",
            victim_guild=18,
            victim_is_monster=True,
            victim_is_sheep=True,
            witnesses=[HILDA],
            witnesses_metadata=[{"npc": HILDA, "guild": 8, "npctype": 0}],
        )
    )
    assert result is not None and result["kind"] == "sheep" and result["community"] == "farm"
    assert result["victim"] == "SHEEP" and result["owner"] == ""


async def test_unwitnessed_sheep_keeps_unknown_community_and_own_sheep_is_not_a_crime(system):
    result = system.crimes.record(deed(kind="murder", victim="SHEEP", victim_guild=18))
    assert result is not None and result["kind"] == "sheep" and result["community"] is None
    assert (
        system.crimes.record(
            deed(
                id="deed-2",
                kind="murder",
                victim="FOLLOW_SHEEP",
                victim_guild=18,
                victim_tough_guy=True,
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "guild,npctype,expected",
    [
        (1, 0, "city"),
        (2, 1, "city"),
        (3, 0, "city"),
        (1, 3, "oldcamp"),
        (2, 4, "oldcamp"),
        (3, 3, "oldcamp"),
        (7, 0, "farm"),
        (8, 1, "farm"),
        (4, 0, "monastery"),
        (5, 0, "monastery"),
        (9, 5, "bl"),
        (9, 7, "bl"),
        (9, 0, None),
        (0, 0, None),
        (18, 0, None),
    ],
)
async def test_vanilla_communities(system, guild, npctype, expected):
    assert system.crimes.community(guild, npctype) == expected


async def test_community_uses_lifted_helpers_and_lawkeepers_use_the_graph(system):
    # A different DAT can assign different guilds: the lifted expression is authoritative.
    field = ["field", ["param", 0], "GUILD"]
    graph = StoryGraph(
        nodes={
            "DIA_LEE_PETZMASTER": Node(
                id="DIA_LEE_PETZMASTER",
                kind="dialog",
                npc=LEE,
                description="",
                effects=[],
                writes=set(),
            )
        },
        functions={
            "C_NPCBELONGSTOFARM": {
                "params": 1,
                "body": ["==", field, ["int", 42]],
                "lifted": True,
            }
        },
        instances={"LOBART": LOBART},
        vars={},
        quests={},
    )
    system.crimes.graph = graph
    assert system.crimes.community(42) == "farm"
    assert system.crimes.community(8) is None
    assert system.crimes.lawkeepers("farm") == (LEE,)
    assert system.crimes.lawkeepers("bl") == ()
    assert system.crimes.lawkeepers("city") == ()  # no such PETZMASTER in this graph
    result = system.crimes.record(deed(owner="LOBART", owner_npc_guild=42))
    assert result is not None and result["owner"] == LOBART and result["community"] == "farm"
    assert set(COMMUNITIES) == {"city", "farm", "monastery", "oldcamp", "bl"}


@pytest.mark.parametrize(
    "before,after,picked_before,picked_after,caught,expected",
    [
        ([], [{"item": "ITMI_GOLD", "count": 80}], 0, 1, False, "success"),
        (
            [{"item": "ITMI_GOLD", "count": 100}],
            [{"item": "ITMI_GOLD", "count": 180}],
            0,
            1,
            False,
            "success",
        ),
        ([], [{"item": "ITKE_KEY", "count": 1}], 0, 0, False, "success"),
        ([], [{"item": "ITKE_KEY", "count": 1}], 0, 1, True, "success"),  # stale AR_THEFT
        ([], [], 0, 0, True, "failure"),
        ([], [], 0, 0, False, "failure"),
        ([], [], 0, 1, True, "failure"),  # an explicit caught attempt outweighs the pocket flag
        ([], [], 1, 1, False, None),  # the pocket was already marked, no new attempt proved
        ([], [], 0, 1, False, None),
    ],
)
def test_pickpocket_outcome_from_inventory_and_original_script_flag(
    before,
    after,
    picked_before,
    picked_after,
    caught,
    expected,
):
    assert pickpocket_outcome(before, after, picked_before, picked_after, caught) == expected


@pytest.mark.parametrize("caught", [False, True])
async def test_successful_pickpocket_has_no_witnesses_even_with_people_nearby(system, caught):
    result = system.crimes.record(
        deed(
            kind="pickpocket",
            item="",
            count=0,
            victim=LOBART,
            victim_guild=8,
            inventory_before=[{"item": "ITMI_GOLD", "count": 50}],
            inventory_after=[{"item": "ITMI_GOLD", "count": 130}],
            picked_pocket_after=1,
            caught=caught,
            witnesses=[LOBART, HILDA],
        )
    )
    assert result is not None and result["outcome"] == "success" and result["kind"] == "theft"
    assert result["item"] == "ITMI_GOLD" and result["count"] == 80
    assert result["owner"] == LOBART and result["community"] == "farm"
    assert result["witnesses"] == [] and system.chronicle.facts("crime_seen") == []


async def test_failed_pickpocket_adds_the_catching_victim_as_witness(system):
    result = system.crimes.record(
        deed(
            kind="pickpocket",
            victim=LOBART,
            victim_guild=8,
            caught=True,
            witnesses=[HILDA],
        )
    )
    assert result is not None and result["outcome"] == "failure" and result["kind"] == "theft"
    assert result["witnesses"] == [LOBART, HILDA] and result["count"] == 0
    assert [f["npc"] for f in system.chronicle.facts("crime_seen")] == [LOBART, HILDA]


async def test_each_actual_item_gain_is_preserved_for_bespoke_pickpockets(system):
    result = system.crimes.record(
        deed(
            kind="pickpocket",
            victim=LOBART,
            victim_guild=8,
            inventory_after=[{"item": "ITMI_GOLD", "count": 80}, {"item": "ITKE_KEY", "count": 1}],
        )
    )
    assert result is not None and result["items"] == [
        {"item": "ITMI_GOLD", "count": 80},
        {"item": "ITKE_KEY", "count": 1},
    ]
    assert len(system.crimes.facts()) == 1


async def test_save_load_restores_crime_and_seen_at_the_saved_branch_head(system):
    theft = deed(owner=LOBART, owner_npc_guild=8, witnesses=[HILDA])
    system.link.receive(theft)
    saved = system.memory.head("A")
    system.link.receive(deed(id="deed-2", kind="attack", victim=LOBART, victim_guild=8))
    assert len(system.crimes.facts()) == 2

    system.link.receive(Session(type="session", branch="B", parent="A", parent_seq=saved, reason="load"))
    assert [f["id"] for f in system.crimes.facts()] == ["deed-1"]
    assert system.chronicle.facts("crime_seen") == [{"fact": "crime_seen", "npc": HILDA, "crime": "deed-1"}]
    system.link.receive(theft)  # reconnect replay of the same deed
    assert len(system.crimes.facts()) == 1

    system.link.receive(Session(type="session", branch="C", reason="new_game"))
    assert system.crimes.facts() == [] and system.chronicle.facts("crime_seen") == []
    system.link.receive(theft)
    assert len(system.crimes.facts()) == 1  # dedup is scoped to the branch


async def test_no_session_does_not_create_truth_and_close_removes_listener(system):
    system.chronicle.branch = None
    assert system.crimes.record(deed(owner=LOBART)) is None
    await system.crimes.close()
    assert system.crimes._on_message not in system.link.listeners
