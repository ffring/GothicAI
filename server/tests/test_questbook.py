from dataclasses import replace
from typing import Any

import pytest

from gothic_ai import questbook
from gothic_ai.chronicle import Chronicle
from gothic_ai.director import Director, Pacing
from gothic_ai.game import GameError
from gothic_ai.llm.fake import FakeBackend
from gothic_ai.memory import MemoryStore
from gothic_ai.places import PlaceBook
from gothic_ai.questbook import Goal, Ground, Quest, QuestBook, Stage, Talk, build, problems
from gothic_ai.story import QUEST, StoryPatches
from tests.fake_game import FakeGame, connected
from tests.test_authoring import BOOK, graph  # noqa: F401 - the graph fixture
from tests.test_dialogue import _until
from tests.test_director import HILDA, LOBART, NAMES, SYMBOLS

THIEF = "DIR_NPC_2"
GOODS = "ITFO_WINE"
FUNCS = (
    "NPC_HASITEMS",
    "NPC_GETDISTTOWP",
    "NPC_GETDISTTONPC",
    "NPC_ISDEAD",
    "HLP_GETNPC",
    "WLD_GETDAY",
    "WLD_INSERTITEM",
    "LOG_SETTOPICSTATUS",
    "B_LOGENTRY",
)


def _talk(text: str) -> Talk:
    return Talk(text, [{"speaker": "self", "text": f"{text} — ответ."}])


def _quest(**change: Any) -> Quest:
    """Hilda's wine is stolen: find the thief's hiding place, get the wine back, bring it to her."""
    q = Quest(
        id="DIR_Q1",
        giver=HILDA,
        title="Украденное вино",
        give=_talk("Что случилось?"),
        stages=[
            Stage("У Хильды украли вино. Вор прячется на полях.", [Goal("reach", point="NW_FARM1_FIELD_01")]),
            Stage(
                "Я нашёл, где прячется вор. Вино у него.",
                [Goal("have", npc=THIEF, item=GOODS, count=2, journal="Вино у меня.")],
            ),
            Stage(
                "Надо отнести вино Хильде.",
                [Goal("bring", npc=HILDA, item=GOODS, count=2, talk=_talk("Вот вино."))],
            ),
        ],
        done_journal="Я вернул Хильде вино.",
        asked="найти вора и вернуть вино.",
        gold=20,
        xp=40,
        names={HILDA: "Хильда"},
    )
    return replace(q, **change)


def _ground(**change: Any) -> Ground:
    symbols = {**SYMBOLS, THIEF: "npc", GOODS: "item", "ITMI_SAW": "item"}
    states = {HILDA: "alive", LOBART: "alive", THIEF: "alive"}
    points = PlaceBook(BOOK)
    return replace(
        Ground(
            symbols,
            states,
            plot=lambda n: n == "NONE_100_XARDAS",
            point=lambda p: points.of(p) is not None,
            gold=50,
            xp=50,
        ),
        **change,
    )


def test_a_quest_of_three_stages_is_built_of_the_graphs_primitives():
    ops = build(_quest(days=3, late_journal="Не успел."))
    kinds = [(op["op"], op.get("id") or op.get("node", {}).get("id")) for op in ops]
    assert kinds == [
        ("vars", None),
        ("add_node", "DIR_Q1_GIVE"),
        ("trigger", "DIR_Q1_S1G1"),  # come to the field
        ("trigger", "DIR_Q1_S1"),  # the next stage
        ("trigger", "DIR_Q1_S2G1"),  # the wine
        ("trigger", "DIR_Q1_S2"),
        ("add_node", "DIR_Q1_DONE"),  # the turn-in
        ("trigger", "DIR_Q1_LATE"),
    ]
    assert ops[0]["vars"] == {"DIR_Q1": 0, "DIR_Q1_STAGE": 0, "DIR_Q1_DUE": 0}
    give = ops[1]["node"]
    assert give["effects"][1] == {"do": "set", "var": "DIR_Q1_STAGE", "value": 1}
    assert give["program"] == [  # the deadline, from the day it is given
        {"do": "set", "target": ["var", "DIR_Q1_DUE"], "value": ["+", ["call", "WLD_GETDAY"], ["int", 3]]}
    ]
    running = ["==", ["var", "DIR_Q1"], ["int", 1, "LOG_RUNNING"]]
    reach = ops[2]
    assert reach["on"] == "state" and reach["steps"] == []
    assert reach["when"] == [
        "&&",
        running,
        ["==", ["var", "DIR_Q1_STAGE"], ["int", 1]],
        ["<", ["call", "NPC_GETDISTTOWP", ["inst", "HERO"], ["str", "NW_FARM1_FIELD_01"]], ["int", 500]],
    ]
    # the stage's own trigger begins the next: its stage, its journal entry
    assert ops[3]["when"] == [
        "&&",
        running,
        ["==", ["var", "DIR_Q1_STAGE"], ["int", 1]],
        [">=", ["var", "DIR_Q1_S1G1"], ["int", 1]],
    ]
    assert ops[3]["steps"] == [
        {"do": "set", "target": ["var", "DIR_Q1_STAGE"], "value": ["int", 2]},
        {
            "do": "call",
            "call": [
                "fn",
                "B_LOGENTRY",
                ["str", "Украденное вино"],
                ["str", "Я нашёл, где прячется вор. Вино у него."],
            ],
        },
    ]
    assert ops[4]["steps"][0]["call"][2:] == [["str", "Украденное вино"], ["str", "Вино у меня."]]
    done = ops[6]["node"]
    assert done["npc"] == HILDA and done["condition"] == [
        "&&",
        running,
        ["==", ["var", "DIR_Q1_STAGE"], ["int", 3]],
        [">=", ["call", "NPC_HASITEMS", ["inst", "HERO"], ["sym", GOODS]], ["int", 2]],
    ]
    assert done["effects"] == [
        {"do": "give", "item": GOODS, "count": 2, "to": "self"},
        {"do": "give", "item": "ITMI_GOLD", "count": 20},
        {"do": "xp", "amount": 40},
        {"do": "set", "var": "DIR_Q1", "value": 2},
        {"do": "set", "var": "DIR_Q1_STAGE", "value": 4},
        {
            "do": "journal",
            "topic": "Украденное вино",
            "text": "Я вернул Хильде вино.",
            "status": "LOG_SUCCESS",
        },
    ]
    late = ops[7]
    assert late["when"][-1] == [">", ["call", "WLD_GETDAY"], ["var", "DIR_Q1_DUE"]]
    assert late["steps"][0] == {"do": "set", "target": ["var", "DIR_Q1"], "value": ["int", 3, "LOG_FAILED"]}


def test_goals_of_every_kind():
    talk = Goal("talk", npc=LOBART, talk=_talk("Ты видел вора?"), journal="Лобарт видел вора.")
    kill = Goal("kill", npcs=[THIEF, "DIR_NPC_3"])
    wait = Goal("wait", days=2)
    saw = Goal("have", item="ITMI_SAW", point="NW_FARM1_OUT_02", count=2)
    q = _quest(
        stages=[
            Stage("Расспросить Лобарта.", [talk, saw]),
            Stage("Разобраться с ворами и выждать.", [kill, wait]),
            Stage("К Хильде.", [Goal("talk", npc=HILDA, talk=_talk("Дело сделано."))]),
        ],
        after="DIR_Q0",
    )
    ops = build(q)
    assert ops[0]["vars"] == {"DIR_Q1": 0, "DIR_Q1_STAGE": 0, "DIR_Q1_DAY": 0, "DIR_Q1_S1G1": 0}
    give = ops[1]["node"]
    assert give["condition"][2] == ["==", ["var", "DIR_Q0"], ["int", 2, "LOG_SUCCESS"]]  # opened by the other
    # the saw lies at its point from the start: twice
    assert give["program"] == 2 * [
        {"do": "call", "call": ["call", "WLD_INSERTITEM", ["sym", "ITMI_SAW"], ["str", "NW_FARM1_OUT_02"]]}
    ]
    ask = ops[2]["node"]  # a talk that is a goal: its dialog sets its variable once
    assert ask["id"] == "DIR_Q1_S1G1_D" and ask["npc"] == LOBART
    assert ["==", ["var", "DIR_Q1_S1G1"], ["int", 0]] in ask["condition"]
    assert {"do": "set", "var": "DIR_Q1_S1G1", "value": 1} in ask["effects"]
    by_id = {op.get("id"): op for op in ops if op["op"] == "trigger"}
    assert by_id["DIR_Q1_S1"]["steps"][-1] == {
        "do": "set",
        "target": ["var", "DIR_Q1_DAY"],
        "value": ["call", "WLD_GETDAY"],
    }  # the stage of waiting notes its day
    assert by_id["DIR_Q1_S2G1"]["when"][-2:] == [
        ["call", "NPC_ISDEAD", ["call", "HLP_GETNPC", ["sym", THIEF]]],
        ["call", "NPC_ISDEAD", ["call", "HLP_GETNPC", ["sym", "DIR_NPC_3"]]],
    ]
    assert by_id["DIR_Q1_S2G2"]["when"][-1] == [
        ">=",
        ["call", "WLD_GETDAY"],
        ["+", ["var", "DIR_Q1_DAY"], ["int", 2]],
    ]
    # the turn-in is a talk: no thing taken
    done = next(op["node"] for op in ops if op["op"] == "add_node" and op["node"]["id"] == "DIR_Q1_DONE")
    assert done["effects"][0] == {"do": "give", "item": "ITMI_GOLD", "count": 20}
    # and it describes itself back
    assert Quest.of(q.to_dict()) == q


def test_the_code_goes_through_a_quest_before_it_is_made():
    assert problems(_quest(), _ground()) == []
    # the last stage ends in one talk
    no_turn_in = _quest(stages=[Stage("Найти вино.", [Goal("have", npc=THIEF, item=GOODS)])])
    assert problems(no_turn_in, _ground()) == [
        "стадия 1, последняя: в ней одна сдача — цель «принести» или «поговорить», а не 0"
    ]
    # the wine has to come from somewhere
    bring_only = _quest(stages=[Stage("Вино.", [Goal("bring", npc=HILDA, item=GOODS, talk=_talk("Вот."))])])
    assert problems(bring_only, _ground()) == [
        "стадия 1, цель 1: ITFO_WINE неоткуда взять: ни точки, где она лежит, ни цели «заполучить» раньше"
    ]
    # who is killed is not talked to after
    deadly = _quest(
        stages=[
            Stage("Убить вора.", [Goal("kill", npcs=[THIEF])]),
            Stage("Сдать вору.", [Goal("talk", npc=THIEF, talk=_talk("Ну?"))]),
        ]
    )
    assert problems(deadly, _ground()) == ["стадия 2, цель 1: DIR_NPC_2 к этому времени убит целью «убить»"]
    # people of the scripts are not killed, the plot's are not the director's
    vanilla = _quest(
        stages=[Stage("Убить.", [Goal("kill", npcs=[LOBART]), Goal("talk", npc=HILDA, talk=_talk("Всё."))])]
    )
    assert problems(vanilla, _ground()) == [
        "стадия 1, цель 1: убить можно только своих персонажей режиссёра (DIR_NPC_…), а не BAU_950_LOBART"
    ]
    assert problems(
        _quest(giver="NONE_100_XARDAS"), _ground(symbols={**SYMBOLS, THIEF: "npc", GOODS: "item"})
    ) == ["заказчик: NONE_100_XARDAS — сюжетный NPC, его не трогаем"]
    dead = _ground(states={HILDA: "dead", THIEF: "alive"})
    assert problems(_quest(), dead) == ["заказчик: BAU_951_HILDA мёртв"]
    # the measure of the reward, the deadline, the quest it opens after, the title
    assert problems(_quest(gold=60, days=40), _ground()) == [
        "золота 60, а можно от 0 до 50",
        "срок — от 1 до 30 дней, а не 40",
        "у квеста со сроком нужна запись журнала о провале",
    ]
    other = _quest(id="DIR_Q7")
    assert problems(_quest(after="DIR_Q5"), _ground()) == [
        "квест DIR_Q5 не найден: этот открывается после него"
    ]
    assert problems(_quest(), _ground(quests={"DIR_Q7": other})) == ["поручение «Украденное вино» уже было"]
    # a point the book of places does not know, a point and an npc at once
    far = _quest(
        stages=[Stage("Туда.", [Goal("reach", point="NW_NOWHERE", npc=THIEF)]), *_quest().stages[1:]]
    )
    assert problems(far, _ground()) == ["стадия 1, цель 1: дойти: или до точки, или до NPC"]
    nowhere = _quest(stages=[Stage("Туда.", [Goal("reach", point="NW_NOWHERE")]), *_quest().stages[1:]])
    assert problems(nowhere, _ground()) == ["стадия 1, цель 1: точки NW_NOWHERE нет в книге мест"]


async def _book(fake: FakeGame, store: MemoryStore, graph) -> tuple[Any, QuestBook, StoryPatches, Chronicle]:  # noqa: F811
    fake.vars = {"KAPITEL": 1, "XP_AMBIENT": 50}
    fake.npc_states = {n: {"state": "alive", "name": name} for n, name in NAMES.items()}
    fake.npc_states[THIEF] = {"state": "alive", "name": "Вор"}
    fake.symbols = {**SYMBOLS, THIEF: "npc", GOODS: "item", **dict.fromkeys(FUNCS, "func")}
    link_cm = connected(fake)
    link = await link_cm.__aenter__()
    patches = StoryPatches(link, store)
    chronicle = Chronicle(link, store)
    director = Director(
        link,
        graph,
        patches,
        chronicle,
        FakeBackend(script=[], delay=0),
        pacing=Pacing(cooldown_s=0),
        settle_s=0,
    )
    book = QuestBook(link, graph, patches, chronicle, director, places=PlaceBook(BOOK))
    await _until(lambda: patches.branch is not None and chronicle.branch is not None)
    return link_cm, book, patches, chronicle


async def test_the_book_makes_a_quest_and_follows_it(graph):  # noqa: F811
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    link_cm, book, patches, chronicle = await _book(fake, store, graph)
    try:
        assert book.next_id() == "DIR_Q1"
        q = _quest()
        assert await book.make(q) == []
        # one patch: the changes, the record stays on the server
        sent = fake.graph[-1]
        assert not sent.get("replace")
        assert [op["op"] for op in sent["ops"]] == [
            "vars",
            "add_node",
            "trigger",
            "trigger",
            "trigger",
            "trigger",
            "add_node",
        ]
        assert [r["quest"]["id"] for r in patches.records(QUEST)] == ["DIR_Q1"]
        assert all(op["op"] != QUEST for op in patches.ops()) and len(patches.ops()) == 7
        assert book.all() == {"DIR_Q1": q} and book.next_id() == "DIR_Q2"
        assert book.progress("DIR_Q1") == questbook.Progress("open", 0)

        # given, the stages through their triggers, done
        await fake.send(type="dialog_chosen", npc=HILDA, node="DIR_Q1_GIVE", made=True)
        await _until(lambda: book.progress("DIR_Q1").state == "given")
        assert "Ты дал герою поручение «Украденное вино»: найти вора и вернуть вино." in (
            book.situation(HILDA) or ""
        )
        await fake.send(type="trigger_fired", id="DIR_Q1_S1G1", count=1, npc="PC_HERO", other="PC_HERO")
        await fake.send(type="trigger_fired", id="DIR_Q1_S1", count=1, npc="PC_HERO", other="PC_HERO")
        await _until(lambda: book.progress("DIR_Q1").stage == 2)
        await fake.send(type="dialog_chosen", npc=HILDA, node="DIR_Q1_DONE", made=True)
        await _until(lambda: book.progress("DIR_Q1").state == "done")
        assert (
            "Поручение «Украденное вино» герой выполнил, ты заплатил: 20 золотых, опыт +40."
            == book.situation(HILDA)
        )
        facts = [f["fact"] for f in chronicle.facts() if f["fact"].startswith("story_quest")]
        assert facts == ["story_quest", "story_quest_given", "story_quest_stage", "story_quest_done"]

        # the same title again is refused
        assert await book.make(_quest(id="DIR_Q2")) == ["поручение «Украденное вино» уже было"]
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_the_save_has_the_truth_the_book_catches_up(graph):  # noqa: F811
    fake = FakeGame(branch="A")
    link_cm, book, _, chronicle = await _book(fake, MemoryStore(":memory:"), graph)
    try:
        assert await book.make(_quest()) == []
        # the messages were lost (the server was away): the game's variables say where it stands
        fake.vars.update({"DIR_Q1": 1, "DIR_Q1_STAGE": 3})
        await book.prepare(HILDA)
        assert book.progress("DIR_Q1") == questbook.Progress("given", 3)
        fake.vars["DIR_Q1"] = 3
        await book.settle()
        assert book.progress("DIR_Q1").state == "failed"
        assert book.situation(HILDA) == "Поручение «Украденное вино» герой не выполнил в срок."
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_a_refused_change_is_taken_back(graph):  # noqa: F811
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    link_cm, _, patches, _ = await _book(fake, store, graph)
    try:
        await patches.apply({"op": "disable", "node": "DIA_X"})
        with pytest.raises(GameError):
            await patches.make(
                {"op": QUEST, "quest": {}},
                [{"op": "vars", "vars": {"DIR_Q9": 0}}, {"op": "disable", "node": "BAD"}],
            )
        # nothing of it is remembered, and the game gets the branch's changes as they were
        await _until(lambda: fake.graph[-1].get("replace") is True)
        assert fake.graph[-1]["ops"] == [{"op": "disable", "node": "DIA_X"}]
        assert patches.records(QUEST) == [] and patches.ops() == [{"op": "disable", "node": "DIA_X"}]
    finally:
        await link_cm.__aexit__(None, None, None)
