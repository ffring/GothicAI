from gothic_ai.cast import Cast, problems
from gothic_ai.chronicle import Chronicle
from gothic_ai.memory import MemoryStore
from gothic_ai.protocol import NpcLook, NpcSpec, NpcThing, RoutineEntry
from gothic_ai.story import StoryPatches
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import _until

LIKE = "VLK_4000_BUERGER"
SYMBOLS = {
    LIKE: "npc",
    "BAU_950_LOBART": "npc",
    "GIL_VLK": "const",
    "FACE_N_NORMAL05": "const",
    "FACE_N_LEE": "const",
    "ITAR_VLK_L": "item",
    "ITMI_GOLD": "item",
    "ITFO_APPLE": "item",
    "ZS_STAND_ARMSCROSSED": "func",
    "ZS_SLEEP": "func",
}


def _peddler(**change: object) -> NpcSpec:
    spec = {
        "npc": "DIR_NPC_1",
        "like": LIKE,
        "at": "NW_FARM1_PATH_01",
        "name": "Гарет",
        "guild": "GIL_VLK",
        "look": NpcLook(
            gender="MALE", head="Hum_Head_Pony", face="FACE_N_NORMAL05", skin="BODYTEX_N", armor="ITAR_VLK_L"
        ),
        "items": [NpcThing(item="ITMI_GOLD", count=40), NpcThing(item="ITFO_APPLE", count=5)],
        "routine": [
            RoutineEntry(
                start="06:00", end="21:00", state="ZS_STAND_ARMSCROSSED", waypoint="NW_FARM1_PATH_01"
            ),
            RoutineEntry(start="21:00", end="06:00", state="ZS_SLEEP", waypoint="NW_FARM1_OUT_01"),
        ],
        **change,
    }
    return NpcSpec.model_validate(spec)


def test_a_description_is_checked():
    assert problems(_peddler(), SYMBOLS, set()) == []
    # a person of the plot is no template; the face of one is no face for a stranger
    bad = problems(
        _peddler(
            like="BAU_950_LOBART",
            look=NpcLook(gender="MALE", head="Hum_Head_Unicorn", face="FACE_N_LEE", skin="BODYTEX_X"),
        ),
        SYMBOLS,
        set(),
    )
    assert "шаблон — безымянный крестьянин, горожанин или наёмник скриптов, а не BAU_950_LOBART" in bad
    assert any(p.startswith("голова Hum_Head_Unicorn не годится") for p in bad)
    assert "лицо FACE_N_LEE не годится: только лица безымянных" in bad
    assert any(p.startswith("кожа — одна из") for p in bad)
    # the day is whole: no gap between the entries
    gap = [
        RoutineEntry(start="06:00", end="20:00", state="ZS_STAND_ARMSCROSSED", waypoint="WP"),
        RoutineEntry(start="21:00", end="06:00", state="ZS_SLEEP", waypoint="WP"),
    ]
    assert problems(_peddler(routine=gap), SYMBOLS, set()) == [
        "распорядок: отрезки должны занимать ровно сутки"
    ]
    # names the game has not, a taken id, too much gold
    odd = problems(
        _peddler(npc="DIR_NPC_1", items=[NpcThing(item="ITMI_GOLD", count=5000), NpcThing(item="ITMI_NONE")]),
        SYMBOLS,
        {"DIR_NPC_1"},
    )
    assert odd == ["персонаж DIR_NPC_1 уже есть", "ITMI_GOLD: от 1 до 1000", "предмета ITMI_NONE нет в игре"]
    assert problems(_peddler(npc="NPC_1"), SYMBOLS, set()) == [
        "персонаж режиссёра называется DIR_NPC_…: NPC_1"
    ]


async def test_a_character_comes_once_and_stays_with_its_saves():
    store = MemoryStore(":memory:")
    fake = FakeGame(branch="A")
    fake.symbols = dict(SYMBOLS)
    async with connected(fake) as link:
        patches = StoryPatches(link, store)
        chronicle = Chronicle(link, store)
        cast = Cast(link, patches, chronicle)
        await _until(lambda: patches.branch is not None and chronicle.branch is not None)
        await patches.apply({"op": "disable", "node": "DIA_LOBART_WORKNOW"})
        await _until(lambda: fake.heads[-1:] == [("A", 1)])  # a save made before he came

        assert cast.next_id() == "DIR_NPC_1"
        made, issues = await cast.introduce(_peddler())
        assert issues == [] and made == {"npc": "DIR_NPC_1", "state": "made", "index": 74650}
        assert (
            fake.cast["DIR_NPC_1"]["name"] == "Гарет"
            and fake.cast["DIR_NPC_1"]["routine"][1]["state"] == "ZS_SLEEP"
        )
        assert [c.npc for c in cast.characters()] == ["DIR_NPC_1"] and cast.next_id() == "DIR_NPC_2"
        # he is no change of the graph
        assert patches.ops() == [{"op": "disable", "node": "DIA_LOBART_WORKNOW"}]
        assert [f["npc"] for f in chronicle.facts("cast")] == ["DIR_NPC_1"]
        # once only
        again, issues = await cast.introduce(_peddler())
        assert again is None and issues == ["персонаж DIR_NPC_1 уже есть"]

        # a new session of the branch: he is asked for first, before the graph, and the game has him
        graphs = len(fake.graph)
        await fake.send(type="session", branch="C", parent="A", parent_seq=10, reason="load")
        await _until(lambda: len(fake.graph) > graphs)
        assert fake.cast_asked == ["DIR_NPC_1", "DIR_NPC_1"] and fake.graph[-1]["ops"] == [
            {"op": "disable", "node": "DIA_LOBART_WORKNOW"}
        ]
        assert [c.npc for c in cast.characters()] == ["DIR_NPC_1"]

        # the save made before he came: no one asks for him
        graphs = len(fake.graph)
        await fake.send(type="session", branch="B", parent="A", parent_seq=1, reason="load")
        await _until(lambda: len(fake.graph) > graphs)
        assert fake.cast_asked == ["DIR_NPC_1", "DIR_NPC_1"] and cast.characters() == []


async def test_a_rejected_character_is_not_kept():
    fake = FakeGame(branch="A")
    fake.symbols = dict(SYMBOLS)
    async with connected(fake) as link:
        patches = StoryPatches(link, MemoryStore(":memory:"))
        cast = Cast(link, patches)
        await _until(lambda: patches.branch is not None)
        made, issues = await cast.introduce(_peddler(like="BAU_950_LOBART"))
        assert made is None and issues and fake.cast == {} and cast.characters() == []
