import copy
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from gothic_ai import social
from gothic_ai.chronicle import Chronicle
from gothic_ai.deals import Deals
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


def test_one_who_promised_silence_tells_no_one(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 1.0)
    s = Social(PEOPLE, graph)
    r, rumor, facts = _rumors(s)
    # Lobart took the hero's gold for keeping quiet (deals.py): the rumor does not leave him
    r._on_fact({"fact": "deal", "npc": LOBART, "silence": [rumor.key]})
    assert r.spread(DAY + _h(9), 3 * DAY) == 0 and set(rumor.known) == {LOBART}
    # whom he told before still pass it on
    rumor.known[HILDA] = 1
    r.spread(3 * DAY, 3 * DAY + _h(23))
    assert VINO in rumor.known and all(f["from"] != LOBART for f in facts)


def test_a_debt_not_paid_back_goes_round(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    s = Social(PEOPLE, graph)
    r, rumor, facts = _rumors(s)
    del r.rumors[rumor.key]
    r._on_fact(
        {
            "fact": "broken",
            "debt": "d1",
            "npc": LOBART,
            "name": "Лобарт",
            "what": "25 золотых до конца дня 2",
            "day": 2,
        }
    )
    broken = r.rumors["broken:BAU_950_LOBART:d1"]
    assert broken.hops == 2
    r.spread(2 * DAY + _h(9), 2 * DAY + _h(23))
    told = next(f for f in facts if f["to"] == HILDA)
    assert (told["kind"], told["title"], told["quest"]) == ("broken", "25 золотых до конца дня 2", "d1")
    assert "задолжал человеку по имени Лобарт и не вернул в срок" in broken.told("Лобарт")


# --- staging (gossip.py): how a live pass looks ---------------------------------------------------


@dataclass(frozen=True)
class _Staging:
    """What gossip.Staging gives Rumors: a tier and the fact's keys."""

    tier: str
    where: str | None = None
    seen_by: tuple[str, ...] = ()

    def keys(self) -> dict[str, Any]:
        return {"where": self.where, "seen_by": list(self.seen_by)}


def _staged(r: Rumors, *tiers: str) -> tuple[list[tuple[str, str]], list[tuple]]:
    """Stage every pass with the next of `tiers` (the last one repeats); what was asked, what was staged."""
    asked: list[tuple[str, str]] = []
    staged: list[tuple] = []

    def stage(rumor: Rumor, a: str, b: str) -> _Staging:
        asked.append((a, b))
        return _Staging(tiers[min(len(asked), len(tiers)) - 1], "NW_FARM1", ("PLAYER", a))

    r.stage = stage
    r.on_staged = lambda rumor, a, b, staging, fact: staged.append((a, b, staging.tier, fact))
    return asked, staged


def _smalltalker(npc: str, *routine: tuple[int, int, str, str, list[int]]) -> dict:
    return {
        "npc": npc,
        "name": npc,
        "guild": 3,
        "routine": [
            {"start": s, "end": e, "state": st, "waypoint": wp, "pos": pos} for s, e, st, wp, pos in routine
        ],
    }


def test_routine_state_and_smalltalk_spans():
    a, b, c = "VLK_451_BUERGER", "VLK_452_BUERGER", "VLK_453_BUERGER"
    front, bed = [10000, 300, 5000], [40000, 300, 5000]
    s = Social(
        [
            _smalltalker(
                a,
                (_h(6), _h(13), "ZS_SMALLTALK", "FRONT_03", front),
                (_h(6), _h(13), "ZS_SMALLTALK", "FRONT_03", front),  # the same entry twice
                (_h(13), _h(15), "ZS_SMALLTALK", "FRONT_04", [10300, 300, 5000]),
                (_h(15), _h(6), "ZS_SLEEP", "BED", bed),
            ),
            _smalltalker(
                b,
                (_h(6), _h(20), "ZS_SMALLTALK", "FRONT_03_C", [10420, 300, 5000]),
                (_h(20), _h(6), "ZS_SLEEP", "BED_2", [40000, 300, 9000]),
            ),
            _smalltalker(c, (_h(6), _h(20), "ZS_STAND", "FRONT_05", [10000, 300, 5600])),
        ]
    )
    # deduped, sorted, not merged across the first one's two waypoints; the meetings are one span
    assert s.ties[a][b].talks == [(_h(6), _h(13), "FRONT_03"), (_h(13), _h(15), "FRONT_04")]
    assert s.ties[a][b].meets == [(_h(6), _h(15))]
    assert s.ties[a][c].meets and not s.ties[a][c].talks  # one only stands there
    assert "болтают 06:00–13:00 у FRONT_03" in s.describe(a)[0]
    assert s.smalltalk(a, b, _h(7)) == "FRONT_03"
    assert s.smalltalk(b, a, 3 * DAY + _h(14)) == "FRONT_04"  # any day, either way round
    assert s.smalltalk(a, b, _h(16)) is None and s.smalltalk(a, c, _h(7)) is None
    assert s.smalltalk(a, "VLK_999_NOBODY", _h(7)) is None
    assert not Social(PEOPLE).ties[LOBART][HILDA].talks  # ZS_STAND


def test_retold_rumor_keeps_said(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    s = Social(PEOPLE, graph)
    r, rumor, facts = _rumors(s)
    rumor.said = True  # his own words, seen false (sightings.py)
    r.spread(DAY + _h(9), DAY + _h(23))
    assert [(f["to"], f["hops"], f.get("said")) for f in facts] == [(HILDA, 1, True), (VINO, 2, True)]
    # two retellings away it is still his words, not a quest he claimed
    deals = Deals.__new__(Deals)
    deals.chronicle = type("C", (), {"facts": lambda self, kind=None: facts})()
    known, _, _ = deals.lies_of(VINO)
    assert known[rumor.key].words(VINO) == (
        "обманул человека по имени Лобарт: сказал «Убрать поле репы», а это неправда"
    )
    r2, _, facts2 = _rumors(s)
    r2.spread(DAY + _h(9), DAY + _h(23))
    assert facts2 and all("said" not in f for f in facts2)


def test_a_live_pass_is_staged_fact_first(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    r, rumor, facts = _rumors(Social(PEOPLE, graph))
    seen: list[list[dict]] = []
    asked, staged = _staged(r, "words")
    on_staged = r.on_staged
    r.on_staged = lambda *args: (seen.append(list(facts)), on_staged(*args))
    assert r.spread(DAY + _h(9), DAY + _h(9) + 1) == 1  # a tick: the farm's meeting goes on
    assert asked == [(LOBART, HILDA)]
    [(a, b, tier, fact)] = staged
    assert (a, b, tier) == (LOBART, HILDA, "words")
    assert facts == [fact] and seen == [[fact]]  # the fact was written before the scene
    assert (fact["how"], fact["hops"], fact["where"], fact["seen_by"]) == (
        "talked",
        1,
        "NW_FARM1",
        ["PLAYER", LOBART],
    )
    assert r.tells[(rumor.key, LOBART, 1)] == 1


def test_far_and_sight_keep_how(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    s = Social(PEOPLE, graph)
    # far: only the fact, as it always was
    r, _, facts = _rumors(s)
    _, staged = _staged(r, "fact")
    r.spread(DAY + _h(9), DAY + _h(9) + 1)
    assert [f["how"] for f in facts] == ["met"] and "where" not in facts[0] and not staged
    # seen, not heard: how it passed stays, where and who saw it come with it
    r, _, facts = _rumors(s)
    _, staged = _staged(r, "sight")
    r.spread(DAY + _h(9), DAY + _h(9) + 1)
    assert [(f["how"], f["where"], f["seen_by"]) for f in facts] == [("met", "NW_FARM1", ["PLAYER", LOBART])]
    assert [(a, b, tier) for a, b, tier, _ in staged] == [(LOBART, HILDA, "sight")]
    # dealings (Andre, once a day), seen
    monkeypatch.setattr(social, "P_MEET", 0.0)
    monkeypatch.setattr(social, "P_DEAL", 1.0)
    at = next(s.ties[LOBART][ANDRE].occasions(LOBART, ANDRE, DAY, 2 * DAY - 1))
    r, _, facts = _rumors(s)
    _staged(r, "sight")
    r.spread(at - 1, at)
    assert [(f["to"], f["how"], f["where"]) for f in facts] == [(ANDRE, "dealings", "NW_FARM1")]


def test_a_time_jump_stages_nothing(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 1.0)
    s = Social(PEOPLE, graph)
    # a sleep of 10 h; a jump whose target minute is the start of the farm's day (8:00); 3 minutes at once
    for after, until in (
        (DAY + _h(9), DAY + _h(19)),
        (DAY + _h(7.5) + 10, DAY + _h(8)),
        (DAY + 477, DAY + 480),
    ):
        before, _, facts_before = _rumors(s)
        before.spread(after, until)
        r, _, facts = _rumors(s)
        asked, staged = _staged(r, "words")
        r.spread(after, until)
        assert facts == facts_before and facts and not asked and not staged
        assert {f["how"] for f in facts} <= {"met", "dealings"}


def test_a_busy_pass_waits_while_the_meeting_goes_on(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    s = Social(PEOPLE, graph)
    # busy: no fact, no tell; the next tick asks again; then they are free and talk
    r, rumor, facts = _rumors(s)
    asked, _ = _staged(r, "wait", "wait", "words")
    assert r.spread(DAY + _h(9), DAY + _h(9) + 1) == 0
    assert not facts and not any(r.tells.values()) and HILDA not in rumor.known
    assert r.waiting == {(rumor.key, LOBART, HILDA): DAY + _h(9) + 1}
    assert r.spread(DAY + _h(9) + 1, DAY + _h(9) + 2) == 0
    assert r.spread(DAY + _h(9) + 2, DAY + _h(9) + 3) == 1
    assert asked == [(LOBART, HILDA)] * 3 and [f["how"] for f in facts] == ["talked"] and not r.waiting

    def ticks(r: Rumors, facts: list[dict], start: int) -> int:
        """Tick a minute at a time from `start` until a fact: the minute it came."""
        for m in range(start, start + DAY):
            r.spread(m, m + 1)
            if facts:
                return m + 1
        raise AssertionError("no fact")

    # busy for good: a fact as it always was after DEFER_MAX_MIN
    r, _, facts = _rumors(s)
    _, staged = _staged(r, "wait")
    assert ticks(r, facts, DAY + _h(9)) == DAY + _h(9) + 1 + social.DEFER_MAX_MIN
    assert [f["how"] for f in facts] == ["met"] and not staged and not r.waiting
    # busy until the meeting ends (the farm's day, at 22:00): the last minute it can come is 21:59
    r, _, facts = _rumors(s)
    _staged(r, "wait")
    assert ticks(r, facts, DAY + _h(21.5)) == DAY + _h(22) - 1 and [f["how"] for f in facts] == ["met"]
    # dealings come once a day: no waiting
    monkeypatch.setattr(social, "P_MEET", 0.0)
    monkeypatch.setattr(social, "P_DEAL", 1.0)
    at = next(s.ties[LOBART][ANDRE].occasions(LOBART, ANDRE, DAY, 2 * DAY - 1))
    r, _, facts = _rumors(s)
    asked, _ = _staged(r, "wait")
    assert r.spread(at - 1, at) == 1 and asked == [(LOBART, ANDRE)]
    assert [(f["to"], f["how"]) for f in facts] == [(ANDRE, "dealings")] and not r.waiting


def test_a_wait_of_an_earlier_meeting_is_over(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    r, rumor, facts = _rumors(Social(PEOPLE, graph))
    _staged(r, "wait")
    # busy at the end of the night by the field (till 7:30); the farm's day is another meeting: it waits anew
    r.spread(DAY + _h(7), DAY + _h(7) + 1)
    assert r.waiting == {(rumor.key, LOBART, HILDA): DAY + _h(7) + 1}
    r.spread(DAY + _h(8), DAY + _h(8) + 1)
    assert not facts and r.waiting == {(rumor.key, LOBART, HILDA): DAY + _h(8) + 1}


def test_a_slow_tick_is_live_and_cannot_wait_past_its_meeting(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    s = Social(PEOPLE, graph)
    # a tick of LIVE_MIN game minutes (a slow round trip) is still live
    r, _, facts = _rumors(s)
    asked, _ = _staged(r, "words")
    r.spread(DAY + _h(9), DAY + _h(9) + social.LIVE_MIN)
    assert asked == [(LOBART, HILDA)] and [f["how"] for f in facts] == ["talked"]
    # busy at 21:58 in a tick to 21:59: the farm's meeting ends at 22:00, so the next tick would not bring
    # them together again; a fact now, not a wait that is never tried again
    r, _, facts = _rumors(s)
    _staged(r, "wait")
    r.spread(DAY + _h(22) - 3, DAY + _h(22) - 1)
    assert [(f["to"], f["how"]) for f in facts] == [(HILDA, "met")] and not r.waiting


def test_gate_holds_and_forces(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 1.0)
    s = Social(PEOPLE, graph)
    r, rumor, facts = _rumors(s)
    r.gate = lambda rumor, a, b: False
    assert r.spread(DAY + _h(9), 2 * DAY) == 0 and not facts
    # True: past the lot and the day's tells; None: as always
    monkeypatch.setattr(social, "P_MEET", 0.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    r.gate = lambda rumor, a, b: True if (a, b) == (LOBART, HILDA) else None
    r.tells[(rumor.key, LOBART, 1)] = social.TELLS_PER_DAY
    assert r.spread(DAY + _h(9), DAY + _h(9) + 1) == 1
    assert [(f["to"], f["how"]) for f in facts] == [(HILDA, "met")] and ANDRE not in rumor.known
    # it does not raise the dead nor teach those who know
    r2, rumor2, facts2 = _rumors(s)
    r2.dead.add(HILDA)
    r2.gate = lambda rumor, a, b: True
    assert r2.spread(DAY + _h(9), DAY + _h(9) + 1) == 0 and not facts2


def test_offer_uses_the_same_checks(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    s = Social(PEOPLE, graph)
    r, rumor, facts = _rumors(s)
    assert not r.offer(rumor, LOBART, HILDA)  # no clock yet
    r.clock = DAY + _h(9)
    assert not r.offer(rumor, LOBART, VATRAS) and LOBART not in s.ties.get(VATRAS, {})  # unacquainted
    assert not r.offer(rumor, HILDA, VINO)  # Hilda does not know it
    r.dead.add(HILDA)
    assert not r.offer(rumor, LOBART, HILDA)
    r.dead.clear()
    rumor.silent.add(LOBART)
    assert not r.offer(rumor, LOBART, HILDA)
    rumor.silent.clear()
    r.tells[(rumor.key, LOBART, 1)] = social.TELLS_PER_DAY
    assert not r.offer(rumor, LOBART, HILDA)
    r.tells.clear()
    monkeypatch.setattr(social, "P_MEET", 0.0)
    assert not r.offer(rumor, LOBART, HILDA)  # the lot
    monkeypatch.setattr(social, "P_MEET", 1.0)
    asked, staged = _staged(r, "wait")
    assert not r.offer(rumor, LOBART, HILDA) and asked == [(LOBART, HILDA)]
    assert not facts and not any(r.tells.values()) and not r.waiting  # busy: the view asks again later
    r.stage = r.on_staged = None
    assert r.offer(rumor, LOBART, HILDA) and [(f["to"], f["how"]) for f in facts] == [(HILDA, "met")]
    assert not r.offer(rumor, LOBART, HILDA)  # she knows it now
    _, staged = _staged(r, "words")
    assert r.offer(rumor, LOBART, ANDRE)  # a tie by dealings only
    assert facts[-1]["how"] == "talked" and [(a, b) for a, b, *_ in staged] == [(LOBART, ANDRE)]
    assert r.tells[(rumor.key, LOBART, 1)] == 2


def test_busy_chain_in_one_spread(monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    farm = [(_h(8), _h(22), FARM)]
    s = Social(
        [
            _person(LOBART, "Лобарт", *farm),
            _person(HILDA, "Хильда", (_h(8), _h(22), [FARM[0] + 1500, FARM[1], FARM[2]])),
            _person(VINO, "Вино", (_h(8), _h(22), [FARM[0] + 3000, FARM[1], FARM[2]])),  # 30 m from Lobart
        ]
    )
    assert VINO in s.ties[HILDA] and VINO not in s.ties[LOBART]
    r, rumor, facts = _rumors(s)
    busy: set[str] = set()
    asked: list[tuple[str, str]] = []

    def stage(rumor: Rumor, a: str, b: str) -> _Staging:
        asked.append((a, b))
        return _Staging("wait" if busy & {a, b} else "words", "NW_FARM1")

    r.stage = stage
    r.on_staged = lambda rumor, a, b, staging, fact: busy.update((a, b))  # their scene begins
    t = DAY + _h(9) + 1
    assert r.spread(t - 1, t) == 1
    # Hilda heard it and would pass it on to Vino the same minute, but she stands in her scene
    assert asked == [(LOBART, HILDA), (HILDA, VINO)] and [f["to"] for f in facts] == [HILDA]
    assert VINO not in rumor.known and r.waiting == {(rumor.key, HILDA, VINO): t}
    busy.clear()  # the scene is over
    assert r.spread(t, t + 1) == 1
    assert [(f["from"], f["to"], f["hops"], f["how"]) for f in facts[1:]] == [(HILDA, VINO, 2, "talked")]
    assert not r.waiting


def test_new_attributes_have_class_defaults(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    bare = Rumors.__new__(Rumors)
    assert (bare.stage, bare.on_staged, bare.gate, bare.clock, bare.waiting) == (None,) * 5
    r, rumor, facts = _rumors(Social(PEOPLE, graph))
    assert r.spread(DAY + _h(9), DAY + _h(9) + 1) == 1  # nothing staged: as always
    r2, _, _ = _rumors(Social(PEOPLE, graph))
    _staged(r2, "wait")
    assert r2.spread(DAY + _h(9), DAY + _h(9) + 1) == 0 and r2.waiting and Rumors.waiting is None
    r2.chronicle.facts = lambda kind=None: []
    r2._load()  # a load: the waits are gone with the rumors
    assert r2.waiting is None and not r2.rumors


def test_a_failing_stage_leaves_a_plain_fact(graph, monkeypatch):
    monkeypatch.setattr(social, "P_MEET", 1.0)
    monkeypatch.setattr(social, "P_DEAL", 0.0)
    r, _, facts = _rumors(Social(PEOPLE, graph))

    def stage(rumor: Rumor, a: str, b: str) -> _Staging:
        raise RuntimeError("bug")

    r.stage = stage
    assert r.spread(DAY + _h(9), DAY + _h(9) + 1) == 1 and [f["how"] for f in facts] == ["met"]
    r2, _, facts2 = _rumors(Social(PEOPLE, graph))
    _staged(r2, "words")
    r2.on_staged = lambda *args: 1 / 0
    assert r2.spread(DAY + _h(9), DAY + _h(9) + 1) == 1 and [f["how"] for f in facts2] == ["talked"]
