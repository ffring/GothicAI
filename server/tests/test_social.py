import copy
import json
from collections import defaultdict
from pathlib import Path

import pytest

from gothic_ai import social
from gothic_ai.chronicle import Chronicle
from gothic_ai.dialogue import DialogueManager
from gothic_ai.gamedata.storygraph import StoryGraph, load
from gothic_ai.llm.fake import FakeBackend
from gothic_ai.memory import MemoryStore
from gothic_ai.persona import PersonaProvider
from gothic_ai.social import DAY, Rumor, Rumors, Social
from gothic_ai.trust import Trust
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import _until
from tests.test_director import GRAPH, HILDA, LOBART, QUEST, VINO

ANDRE, VATRAS, BANDIT = "MIL_311_ANDRE", "VLK_439_VATRAS", "BDT_1013_BANDIT_L"
FARM, FIELD, CITY = [13608, 1753, -15600], [14678, 1642, -10000], [10480, 320, 4920]  # FIELD: 57 m off


def _person(npc: str, name: str, *routine: tuple[int, int, list[int]], guild: int = 8) -> dict:
    return {
        "npc": npc,
        "name": name,
        "guild": guild,
        "routine": [
            {"start": s, "end": e, "state": "ZS_STAND", "waypoint": "WP", "pos": pos} for s, e, pos in routine
        ],
    }


def _h(hours: float) -> int:
    return int(hours * 60)


# Lobart and Hilda on the farm by day, by the field at night; Vino by the field in the evening (Hilda comes at
# 22, Lobart only at 23); Vatras and Andre in the city
PEOPLE = [
    _person(LOBART, "Лобарт", (_h(7.5), _h(23), FARM), (_h(23), _h(7.5), FIELD)),
    _person(HILDA, "Хильда", (_h(8), _h(22), FARM), (_h(22), _h(8), FIELD)),
    _person(VINO, "Вино", (_h(8), _h(20), [9703, 1343, -9000]), (_h(20), _h(23), FIELD)),
    _person(VATRAS, "Ватрас", (_h(6), _h(22), CITY), guild=14),
    _person(ANDRE, "Андре", (_h(6), _h(22), [10800, 320, 5100]), guild=2),
    _person(BANDIT, "Бандит", (_h(0), _h(24), FARM), guild=9),
]


@pytest.fixture
def graph(tmp_path: Path) -> StoryGraph:
    """The turnips (Lobart, Hilda), and a quest of Andre's that ends at Lobart's (as the field raiders do)."""
    g = copy.deepcopy(GRAPH)
    bugs = "MIS_ANDREHELPLOBART"
    g["nodes"] += [
        {
            **g["nodes"][0],
            "id": "DIA_ANDRE_HELPLOBART",
            "npc": ANDRE,
            "effects": [{"do": "set", "var": bugs, "value": ["int", 1]}],
            "kind": "dialog",
            "parents": [],
        },
        {
            **g["nodes"][0],
            "id": "DIA_LOBART_BUGDEAD",
            "npc": LOBART,
            "effects": [],
            "kind": "dialog",
            "parents": [],
            "condition": ["==", ["var", bugs], ["int", 1]],
        },
        {
            **g["nodes"][0],
            "id": "DIA_BANDIT_HI",
            "npc": BANDIT,
            "effects": [],
            "kind": "dialog",
            "parents": [],
            "condition": ["==", ["var", QUEST], ["int", 1]],
        },
    ]
    g["quests"].append(
        {
            "var": bugs,
            "topics": [],
            "running": ["DIA_ANDRE_HELPLOBART"],
            "success": [],
            "failed": [],
            "obsolete": [],
            "other": [],
        }
    )
    path = tmp_path / "story.json"
    path.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    return load(path)


def test_who_meets_whom_and_who_has_dealings(graph):
    s = Social(PEOPLE, graph)
    # the farm's day and its night (the beds by the field) are one span each
    assert s.ties[LOBART][HILDA].meets == [(0, _h(7.5)), (_h(8), _h(22)), (_h(23), DAY)]
    assert s.ties[VINO][HILDA].meets == [(_h(22), _h(23))]
    assert LOBART not in s.ties[VINO] or not s.ties[VINO][LOBART].meets
    assert s.ties[VATRAS][ANDRE].meets == [(_h(6), _h(22))]  # 3.6 m apart in the city
    assert LOBART not in s.ties[VATRAS]
    # dealings: the turnips (Lobart, Hilda), Andre's quest that ends at Lobart's
    assert s.ties[LOBART][HILDA].deals == [QUEST]
    assert s.ties[LOBART][ANDRE].deals == ["MIS_ANDREHELPLOBART"] and not s.ties[LOBART][ANDRE].meets
    # an outlaw at the farm all day reads the turnips quest: no acquaintance of theirs
    assert BANDIT not in s.ties[LOBART] and BANDIT not in s.ties[HILDA]
    assert "Андре (MIL_311_ANDRE): дела: MIS_ANDREHELPLOBART" in s.describe(LOBART)


def _rumors(s: Social) -> tuple[Rumors, Rumor, list[dict]]:
    """Rumors without a game: what it records goes into a list."""
    r = Rumors.__new__(Rumors)
    facts: list[dict] = []
    r.social, r.dead, r.rumors, r.tells = s, set(), {}, defaultdict(int)
    r.chronicle = type("C", (), {"record": lambda self, f: facts.append(f)})()
    r.dialogue = type("D", (), {"remember": lambda self, *a: None})()
    rumor = Rumor(QUEST, "caught", LOBART, "Лобарт", "Убрать поле репы")
    rumor.known[LOBART] = 0
    r.rumors[rumor.key] = rumor
    return r, rumor, facts


def test_a_rumor_goes_round_the_meetings_in_order(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    s = Social(PEOPLE, graph)
    r, rumor, facts = _rumors(s)
    # caught at 9:00; by 23:00 Hilda (on the farm) and, at 22:00 by the field, Vino from her
    assert r.spread(DAY + _h(9), DAY + _h(23)) == 2
    assert rumor.known == {LOBART: 0, HILDA: 1, VINO: 2}
    first, second = facts
    assert (first["from"], first["to"], first["hops"], first["how"]) == (LOBART, HILDA, 1, "met")
    assert (second["from_name"], second["to"], second["hops"]) == ("Хильда", VINO, 2)
    assert second["about_name"] == "Лобарт" and second["day"] == 1
    # the city has no meeting with the farm
    assert VATRAS not in rumor.known and ANDRE not in rumor.known


def test_dealings_bring_it_to_the_city_once_a_day(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 1.0)
    s = Social(PEOPLE, graph)
    r, rumor, facts = _rumors(s)
    # a jump of the clock over a night: Andre hears through his dealings with Lobart, Vatras meets Andre
    r.spread(DAY + _h(9), 2 * DAY + _h(23))
    assert rumor.known[ANDRE] == 1 and rumor.known[VATRAS] == 2
    assert next(f for f in facts if f["to"] == ANDRE)["how"] == "dealings"


def test_retellings_stop_and_the_dead_tell_no_one(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 1.0)
    monkeypatch.setitem(social.HOPS, "caught", 1)
    s = Social(PEOPLE, graph)
    r, rumor, _ = _rumors(s)
    r.spread(DAY + _h(9), 3 * DAY)
    assert set(rumor.known) == {LOBART, HILDA, ANDRE}  # the first retelling only
    monkeypatch.setitem(social.HOPS, "caught", 3)
    r2, rumor2, _ = _rumors(s)
    r2.dead.add(HILDA)
    r2.spread(DAY + _h(9), DAY + _h(23))
    assert HILDA not in rumor2.known and VINO not in rumor2.known


def test_the_chances_repeat():
    assert social._chance("a", "b", "1") == social._chance("a", "b", "1") != social._chance("a", "b", "2")


async def test_rumors_on_the_game_clock(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    fake = FakeGame(branch="A")
    fake.people = PEOPLE
    fake.hour, fake.minute = 9, 0
    store = MemoryStore(":memory:")
    link_cm = connected(fake)
    link = await link_cm.__aenter__()
    try:
        dialogue = DialogueManager(link, FakeBackend("Ну?", delay=0), PersonaProvider(None), memory=store)
        chronicle = Chronicle(link, store)
        await _until(lambda: chronicle.branch is not None)
        rumors = Rumors(link, dialogue, chronicle, graph)
        chronicle.record(
            {
                "fact": "caught",
                "quest": QUEST,
                "npc": LOBART,
                "name": "Лобарт",
                "title": "Убрать поле репы",
                "how": "видел сам",
            }
        )
        await rumors.tick()  # the people, and the clock
        assert rumors.social is not None and HILDA in rumors.social.ties[LOBART]
        fake.hour = 23
        await rumors.tick()
        heard = {f["to"]: f for f in chronicle.facts("rumor")}
        assert set(heard) == {HILDA, VINO}
        trust = Trust(chronicle)
        assert "ты слышал, что он обманул других: Лобарт («Убрать поле репы», со слов: Хильда)" in (
            trust.situation(VINO) or ""
        )
        assert "Слух (со слов: Хильда): герой обманул человека по имени Лобарт — уверял" in str(
            dialogue.conversations[VINO].history
        )
        # after a load of an earlier save the branch's facts are all there is
        rumors._load()
        assert set(rumors.rumors[f"caught:{LOBART}:{QUEST}"].known) == {LOBART, HILDA, VINO}
    finally:
        await link_cm.__aexit__(None, None, None)


def test_one_tells_a_rumor_to_a_few_a_day(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 1.0)
    monkeypatch.setattr(social, "TELLS_PER_DAY", 1)
    s = Social(PEOPLE, graph)
    r, rumor, facts = _rumors(s)
    r.spread(
        DAY + _h(9),
        DAY
        + _h(
            23,
        ),
    )
    assert [f["from"] for f in facts].count(LOBART) == 1  # Hilda by day; Andre waits
    r.spread(DAY + _h(23), 2 * DAY + _h(23))
    assert [f["from"] for f in facts].count(LOBART) == 2 and ANDRE in rumor.known


def test_old_news_is_not_retold(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 1.0)
    monkeypatch.setattr(social, "TELLS_PER_DAY", 1)
    monkeypatch.setattr(social, "FRESH_DAYS", 1)
    s = Social(PEOPLE, graph)
    r, rumor, facts = _rumors(s)
    rumor.since[LOBART] = 1  # he caught the hero on day 1
    r.spread(DAY + _h(9), 4 * DAY)
    assert [f["from"] for f in facts].count(LOBART) == 1  # one of Hilda and Andre, that day only


def test_a_lie_he_took_a_reward_for_goes_further(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 1.0)
    citizen = "VLK_4000_CITIZEN"  # in the city all day, by Vatras (24 m) and not by Andre (26 m)
    s = Social([*PEOPLE, _person(citizen, "Горожанин", (0, DAY, [10480, 320, 2520]), guild=3)], graph)
    assert set(s.ties[citizen]) == {VATRAS}
    reach = {}
    for kind in ("caught", "found_out"):
        r, rumor, _ = _rumors(s)
        rumor.kind = kind
        r.rumors = {rumor.key: rumor}
        r.spread(DAY + _h(9), 3 * DAY)
        reach[kind] = rumor.known.get(citizen)
    # Lobart -> Andre (dealings) -> Vatras (the city) -> the citizen: the third retelling is only for a lie
    # he took a reward for
    assert reach == {"caught": None, "found_out": 3}
