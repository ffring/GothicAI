import copy
import json
from pathlib import Path
from typing import Any

import pytest

from gothic_ai.amends import Amends
from gothic_ai.chronicle import Chronicle
from gothic_ai.director import Director, Pacing
from gothic_ai.gamedata.storygraph import StoryGraph, load
from gothic_ai.llm import ToolCall
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.memory import MemoryStore
from gothic_ai.story import StoryPatches
from gothic_ai.trust import Trust
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import _until
from tests.test_director import GRAPH, HILDA, LOBART, NAMES, QUEST, SYMBOLS, _node

ANDRE, PECK = "MIL_311_ANDRE", "MIS_ANDRE_PECK"
JOIN, MISSION = "DIA_ANDRE_JOIN", "DIA_ANDRE_FIRSTMISSION"
LIE = f"caught:{LOBART}:{QUEST}"


@pytest.fixture
def graph(tmp_path: Path) -> StoryGraph:
    """Lobart's turnips, and Andre: joining the militia, a first mission."""
    g = copy.deepcopy(GRAPH)
    g["nodes"] += [
        _node(
            JOIN,
            ANDRE,
            [{"do": "set_guild", "npc": ["inst", "OTHER"], "guild": ["int", 2]}],
            description="Я готов вступить в ополчение!",
            permanent=True,
        ),
        _node(MISSION, ANDRE, [{"do": "set", "var": PECK, "value": ["int", 1]}], description="Есть задание?"),
    ]
    g["vars"][PECK] = {"readers": [], "writers": [MISSION], "others": []}
    path = tmp_path / "story.json"
    path.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    return load(path)


def _proposal(**change: Any) -> ToolCall:
    args = {
        "kind": "amends",
        "close": [JOIN],
        "lines": [
            {"speaker": "npc", "text": "Слыхал я, ты Лобарту про репу наврал."},
            {
                "speaker": "npc",
                "text": "Лжецов в ополчение не берём. Пятьдесят золотых штрафа — тогда поговорим.",
            },
        ],
        "to": "self",
        "gold": 50,
        "description": "Вот штраф — 50 золотых.",
        "amends_lines": [{"speaker": "npc", "text": "Ладно. Считай, забыли."}],
        "title": "Штраф за враньё",
        "journal_now": "Андре прослышал про репу Лобарта. Пока не заплачу 50 золотых, в ополчение не берёт.",
        "journal_done": "Я заплатил Андре штраф.",
        "reason": "Андре знает о лжи от Лобарта, ополчению лжецы не нужны",
    }
    return ToolCall("propose_amends", {**args, **change}, "c1")


async def _amends(fake: FakeGame, llm: FakeBackend, graph: StoryGraph, store: MemoryStore):
    fake.vars = {QUEST: 1, "KAPITEL": 1, PECK: 0}
    fake.npc_states = {n: {"state": "alive", "name": name} for n, name in {**NAMES, ANDRE: "Андре"}.items()}
    fake.symbols = {**SYMBOLS, ANDRE: "npc", PECK: "var"}
    link_cm = connected(fake)
    link = await link_cm.__aenter__()
    patches = StoryPatches(link, store)
    chronicle = Chronicle(link, store)
    director = Director(link, graph, patches, chronicle, llm, pacing=Pacing(cooldown_s=0), settle_s=0)
    amends = Amends(link, graph, patches, chronicle, director, llm)
    await _until(lambda: patches.branch is not None and chronicle.branch is not None)
    caught = {"fact": "caught", "quest": QUEST, "npc": LOBART, "name": "Лобарт", "title": "Убрать поле репы"}
    chronicle.record({**caught, "how": "видел сам", "day": 1})
    return link_cm, amends, chronicle


def _ops(fake: FakeGame) -> list[dict[str, Any]]:
    return [op for m in fake.graph for op in m["ops"]]


async def test_andre_hears_of_the_lie_and_asks_for_amends(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(script=[FakeStep("", [_proposal()])], delay=0)
    link_cm, amends, chronicle = await _amends(fake, llm, graph, store)
    try:
        assert {s.id for s in amends.stakes(ANDRE)} == {JOIN, MISSION} and amends.stakes(LOBART) == []
        s = await amends.consider(LIE, ANDRE, "Лобарт")
        assert s is not None
        brief = llm.calls[0][1][0].content
        assert "Человек: Андре (MIL_311_ANDRE)" in brief and "Откуда: слух, рассказал Лобарт" in brief
        assert f"- {JOIN}: «Я готов вступить в ополчение!» — принимает в гильдию" in brief
        assert f"- {MISSION}: «Есть задание?» — даёт работу" in brief
        assert "уверял, что сделал «Убрать поле репы», а это неправда" in brief

        # the talk Andre starts, and joining closed until the hero pays
        ops = _ops(fake)
        talk = ops[0]["node"]
        assert (talk["id"], talk["npc"], talk["important"]) == (f"DIR_AMENDS_{ANDRE}_1_TALK", ANDRE, True)
        assert talk["lines"][0] == {"speaker": "self", "text": "Слыхал я, ты Лобарту про репу наврал."}
        assert ops[1] == {"op": "disable", "node": JOIN}
        assert "Пока не заплатит, для него закрыто: «Я готов вступить в ополчение!»" in amends.situation(
            ANDRE
        )

        # the hero has heard him out: the fine is offered
        await fake.send(type="dialog_chosen", npc=ANDRE, node=talk["id"], made=True)
        await _until(lambda: len(_ops(fake)) == 3)
        pay = _ops(fake)[2]["node"]
        assert pay["npc"] == ANDRE and pay["description"] == "Вот штраф — 50 золотых."
        assert pay["condition"] == [
            ">=",
            ["call", "NPC_HASITEMS", ["inst", "OTHER"], ["sym", "ITMI_GOLD"]],
            ["int", 50],
        ]
        assert {"do": "give", "item": "ITMI_GOLD", "count": 50, "to": "self"} in pay["effects"]

        # he pays: joining opens again, and Andre counts it
        await fake.send(type="dialog_chosen", npc=ANDRE, node=pay["id"], made=True)
        await _until(lambda: chronicle.facts("amends") != [])
        await _until(lambda: _ops(fake)[-1] == {"op": "enable", "node": JOIN})
        assert "он загладил вину за ложь: «Убрать поле репы» — заплатил 50 золотых" in Trust(
            chronicle
        ).situation(ANDRE)
        # Lobart was not paid: his word is still broken for him
        assert "он уже лгал тебе" in Trust(chronicle).situation(LOBART)
        # all of it is in the branch: a load brings back where it stands
        amends._load()
        assert amends.storylets[s.id].stage == "done"
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_a_rejected_storylet_and_one_who_does_not_care(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    wrong = _proposal(close=["DIA_LOBART_WORKNOW"], gold=500)
    none = ToolCall(
        "propose_amends", {**_proposal().arguments, "kind": "none", "reason": "не его дело"}, "c2"
    )
    llm = FakeBackend(script=[FakeStep("", [wrong]), FakeStep("", [none])], delay=0)
    link_cm, amends, chronicle = await _amends(fake, llm, graph, store)
    try:
        assert await amends.consider(LIE, ANDRE, "Лобарт") is None
        why = chronicle.facts("amends_rejected")[0]["why"]
        assert any("close" in w for w in why) and any("gold: от 0 до 100" in w for w in why)
        assert "Отклонено" in llm.calls[1][1][-1].content
        assert chronicle.facts("amends_none")[0]["reason"] == "не его дело"
        assert _ops(fake) == []
        # asked once per lie
        assert await amends.consider(LIE, ANDRE, "Лобарт") is None and len(llm.calls) == 2
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_amends_to_the_one_lied_to(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(script=[FakeStep("", [_proposal(to="victim", gold=20)])], delay=0)
    link_cm, amends, chronicle = await _amends(fake, llm, graph, store)
    try:
        s = await amends.consider(LIE, ANDRE, "Лобарт")
        assert s is not None and s.to == LOBART
        await fake.send(type="dialog_chosen", npc=ANDRE, node=s.talk, made=True)
        await _until(lambda: len(_ops(fake)) == 3)
        assert _ops(fake)[2]["node"]["npc"] == LOBART
        assert "требует, чтобы герой повинился перед тобой и заплатил тебе 20 золотых" in amends.situation(
            LOBART
        )
        await fake.send(type="dialog_chosen", npc=LOBART, node=s.pay, made=True)
        await _until(lambda: chronicle.facts("amends") != [])
        # Lobart was paid: the lie no longer breaks the hero's word with him
        line = Trust(chronicle).situation(LOBART) or ""
        assert "он уже лгал тебе" not in line and "он загладил вину за ложь" in line
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_the_queue_skips_those_with_nothing_at_stake(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(script=[FakeStep("", [_proposal()])], delay=0)
    link_cm, amends, chronicle = await _amends(fake, llm, graph, store)
    import asyncio

    run = asyncio.create_task(amends.run())
    try:
        # the caught fact queued Lobart: nothing the hero comes to him for here, no model call
        chronicle.record(
            {
                "fact": "rumor",
                "quest": QUEST,
                "kind": "caught",
                "about": LOBART,
                "from": LOBART,
                "from_name": "Лобарт",
                "to": ANDRE,
                "hops": 1,
            }
        )
        await _until(lambda: chronicle.facts("amends_open") != [])
        assert len(llm.calls) == 1 and chronicle.facts("amends_open")[0]["npc"] == ANDRE
    finally:
        run.cancel()
        await link_cm.__aexit__(None, None, None)


async def test_one_confession_settles_both_who_asked_for_it(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    to_lobart = _proposal(to="victim", gold=0, description="Повиниться перед Лобартом")
    # joining is closed by the first: the second closes the mission
    also = _proposal(to="victim", gold=0, description="Повиниться перед Лобартом", close=[MISSION])
    llm = FakeBackend(script=[FakeStep("", [to_lobart]), FakeStep("", [also])], delay=0)
    link_cm, amends, chronicle = await _amends(fake, llm, graph, store)
    try:
        first = await amends.consider(LIE, ANDRE, "Лобарт")
        assert first is not None
        # the next one asked about the same lie sees what is asked already
        amends.stakes = lambda npc: amends.__class__.stakes(amends, ANDRE)  # Hilda has the same dealings here
        second = await amends.consider(LIE, HILDA, "Лобарт")
        assert (
            second is not None
            and "тогда одна расплата закроет оба требования): Андре — повиниться — Лобарт"
            in llm.calls[1][1][0].content
        )
        for s in (first, second):
            await fake.send(type="dialog_chosen", npc=s.npc, node=s.talk, made=True)
        await _until(lambda: first.stage == "told" and second.stage == "told")
        # he confesses to Lobart once: both are settled, the other confession is gone from Lobart's menu
        await fake.send(type="dialog_chosen", npc=LOBART, node=first.pay, made=True)
        await _until(lambda: second.stage == "done")
        assert {"op": "remove_node", "node": second.pay} in _ops(fake)
        assert len(chronicle.facts("amends")) == 2
    finally:
        await link_cm.__aexit__(None, None, None)
