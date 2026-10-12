import asyncio
import copy
import json
from pathlib import Path

import pytest

from gothic_ai.chronicle import Chronicle
from gothic_ai.dialogue import DialogueManager
from gothic_ai.gamedata.storygraph import StoryGraph, load
from gothic_ai.llm import ToolCall
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.memory import MemoryStore
from gothic_ai.persona import PersonaProvider
from gothic_ai.vanilla import VanillaQuests
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import _until
from tests.test_director import GRAPH, HAS_20, HILDA, LOBART, QUEST, _node

TO_HILDA = "MIS_LOBART_RUEBENTOHILDA"
BANDIT = "BDT_1_BANDIT"
KILL = "MIS_KILLBANDIT"
ACCEPT = FakeStep("Ладно, верю. Держи плату.", [ToolCall("accept_quest", {"quest": QUEST}, "c1")])


@pytest.fixture
def graph(tmp_path: Path) -> StoryGraph:
    """Lobart's turnips (as in test_director), with the next step (bring them to Hilda) and a kill quest of
    Lobart's: the bandit must be dead."""
    g = copy.deepcopy(GRAPH)
    ending = next(n for n in g["nodes"] if n["id"] == "DIA_LOBART_RUEBENRUNNING")
    ending["effects"].insert(
        2, {"do": "set", "var": TO_HILDA, "value": ["int", 1, "LOG_RUNNING"], "when": HAS_20}
    )
    dead = ["call", "NPC_ISDEAD", ["inst", "BANDIT"]]
    g["nodes"].append(
        _node(
            "DIA_LOBART_BANDITDEAD",
            LOBART,
            [
                {"do": "set", "var": KILL, "value": ["int", 2, "LOG_SUCCESS"], "when": dead},
                {"do": "xp", "amount": ["int", 100], "when": dead},
            ],
            condition=["==", ["var", KILL], ["int", 1]],
            description="Бандит мёртв.",
        )
    )
    g["instances"]["BANDIT"] = BANDIT
    g["quests"] += [
        {
            "var": TO_HILDA,
            "topics": [],
            "running": ["DIA_LOBART_RUEBENRUNNING"],
            "success": [],
            "failed": [],
            "obsolete": [],
            "other": [],
        },
        {
            "var": KILL,
            "topics": [{"topic": "TOPIC_BANDIT", "title": "Бандит у фермы"}],
            "running": [],
            "success": ["DIA_LOBART_BANDITDEAD"],
            "failed": [],
            "obsolete": [],
            "other": [],
        },
    ]
    path = tmp_path / "story.json"
    path.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
    return load(path)


async def _setup(fake: FakeGame, llm: FakeBackend, graph: StoryGraph, store: MemoryStore):
    fake.vars = {QUEST: 1, "KAPITEL": 1, TO_HILDA: 0}
    fake.npc_states = {
        n: {"state": "alive", "name": name} for n, name in [(LOBART, "Лобарт"), (HILDA, "Хильда")]
    }
    fake.item_names = {"ITPL_BEET": "Репа", "ITMI_GOLD": "Золото"}
    fake.instances = {"HILDA": HILDA, "BANDIT": BANDIT}
    link_cm = connected(fake)
    link = await link_cm.__aenter__()
    dialogue = DialogueManager(link, llm, PersonaProvider(None), memory=store)
    chronicle = Chronicle(link, store)
    vanilla = VanillaQuests(link, dialogue, chronicle, graph, hero_distance=lambda npc: 300.0)
    await _until(lambda: chronicle.branch is not None)
    return link_cm, dialogue, chronicle, vanilla


def _effects(fake: FakeGame, npc: str) -> list[list[dict]]:
    return [m["effects"] for m in fake.story if m["type"] == "story_effect" and m["npc"] == npc]


async def test_a_lie_to_lobart_is_paid_and_found_out_the_next_morning(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(script=[ACCEPT, FakeStep("Ну?")], delay=0)
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    try:
        dialogue.open(LOBART, "Лобарт")
        await vanilla.prepare(LOBART)
        situation = vanilla.situation(LOBART)
        assert "Ты ждёшь от героя: «Убрать поле репы» (id MIS_LOBART_RUEBEN)" in situation
        assert "герой принесёт тебе Репа ×20" in situation and "Золото ×5, опыт +50" in situation
        assert [t.name for t in vanilla.tools(LOBART)] == ["accept_quest"]

        # the hero has no turnips and says they are in the barn; Lobart believes him
        await dialogue.say_as_player(LOBART, "Репа в амбаре, двадцать штук. Плати.")
        await _until(lambda: chronicle.facts("lie") != [])
        assert "accept_quest" in [t.name for t in llm.calls[0][2]]
        assert "Ты ждёшь от героя" in str(llm.calls[0][1])
        effects = _effects(fake, LOBART)[0]
        assert {"do": "set", "var": QUEST, "value": 2} in effects
        assert {"do": "set", "var": TO_HILDA, "value": 1} in effects  # the next step, as the scripts do it
        assert {"do": "xp", "amount": 50} in effects and {
            "do": "give",
            "item": "ITMI_GOLD",
            "count": 5,
        } in effects
        assert not any(e.get("to") == "self" for e in effects)  # nothing to take
        lie = chronicle.facts("lie")[0]
        assert (lie["kind"], lie["truth"], lie["follow"]) == ("fetch", "ITPL_BEET 0 of 20", [TO_HILDA])
        assert "Ты засчитал герою дело «Убрать поле репы»" in vanilla.situation(LOBART)
        assert vanilla.tools(LOBART) == []

        # the same day nothing happens; the next morning he misses the turnips
        run = asyncio.create_task(vanilla.run())
        await asyncio.sleep(1.5)
        assert chronicle.facts("found_out") == []
        fake.day = 2
        fake.people_near = [{"npc": HILDA, "name": "Хильда", "distance": 400, "guild": 0}]
        await _until(lambda: chronicle.facts("rumor") != [], 10)
        run.cancel()
        assert "наутро хватился" in chronicle.facts("found_out")[0]["how"]
        consequences = _effects(fake, LOBART)[-1]
        assert {"do": "refuse_talk", "seconds": 300} in consequences and {
            "do": "attack",
            "reason": "AR_NONE",
        } in consequences
        assert dialogue.conversations[LOBART].intent.attitude == "hostile"
        assert "Ты знаешь, что герой солгал тебе" in vanilla.situation(LOBART)
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_the_truth_is_no_lie(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(script=[ACCEPT, FakeStep("Ну?")], delay=0)
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    fake.hero_items = {"ITPL_BEET": 20}
    try:
        dialogue.open(LOBART, "Лобарт")
        await dialogue.say_as_player(LOBART, "Вот твоя репа.")
        await _until(lambda: chronicle.facts("claim") != [])
        assert chronicle.facts("claim")[0]["truth"] is True and chronicle.facts("lie") == []
        await _until(lambda: _effects(fake, LOBART) != [])
        # as in the scripts: Lobart pays, the turnips stay with the hero for Hilda
        assert {"do": "set", "var": TO_HILDA, "value": 1} in _effects(fake, LOBART)[0]
        assert vanilla.told[QUEST].lie is False
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_a_lie_is_mended_when_the_next_step_gets_the_goods(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(script=[ACCEPT, FakeStep("Ну?")], delay=0)
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    try:
        dialogue.open(LOBART, "Лобарт")
        await dialogue.say_as_player(LOBART, "Репа в амбаре.")
        await _until(lambda: chronicle.facts("lie") != [])
        fake.vars[TO_HILDA] = 2  # he got turnips after all and brought them to Hilda
        fake.day = 2
        run = asyncio.create_task(vanilla.run())
        await _until(lambda: chronicle.facts("lie_mended") != [], 10)
        run.cancel()
        assert chronicle.facts("found_out") == []
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_a_lie_about_a_killing_is_found_out_when_the_victim_turns_up(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    accept = FakeStep("Мёртв, говоришь? Хорошо.", [ToolCall("accept_quest", {"quest": KILL}, "c1")])
    llm = FakeBackend(script=[accept, FakeStep("Ну?")], delay=0)
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    fake.vars[KILL] = 1
    fake.npc_states[BANDIT] = {"state": "alive", "name": "Бандит"}
    try:
        dialogue.open(LOBART, "Лобарт")
        await vanilla.prepare(LOBART)
        assert "будут мертвы: BDT_1_BANDIT" in vanilla.situation(LOBART)
        await dialogue.say_as_player(LOBART, "Бандит мёртв.")
        await _until(lambda: chronicle.facts("lie") != [])
        lie = chronicle.facts("lie")[0]
        assert (lie["kind"], lie["tag"], lie["targets"]) == ("kill", f"{KILL}_ALIVE", [BANDIT])
        await _until(lambda: any(m["type"] == "story_tag" for m in fake.story))
        tag = next(m for m in fake.story if m["type"] == "story_tag")
        assert (tag["tag"], tag["npcs"]) == (f"{KILL}_ALIVE", [BANDIT])
        watch = [m for m in fake.story if m["type"] == "story_watch"][-1]
        assert (watch["npcs"], watch["owner"]) == ([LOBART], "vanilla")

        run = asyncio.create_task(vanilla.run())
        near = [{"npc": LOBART, "distance": 900}]
        await fake.send(
            type="story_world", tags=[{"tag": f"{KILL}_ALIVE", "alive": 1, "dead": 0, "near": near}]
        )
        await _until(lambda: chronicle.facts("found_out") != [], 5)
        run.cancel()
        assert "увидел живым" in chronicle.facts("found_out")[0]["how"]
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_lies_follow_the_save_branch(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(script=[ACCEPT, FakeStep("Ну?")], delay=0)
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    try:
        dialogue.open(LOBART, "Лобарт")
        await dialogue.say_as_player(LOBART, "Репа в амбаре.")
        await _until(lambda: chronicle.facts("lie") != [])
        assert vanilla.told[QUEST].lie
        vanilla._load()  # as after a load of a save made after the lie
        t = vanilla.told[QUEST]
        assert (t.lie, t.kind, t.follow, t.found_out) == (True, "fetch", [TO_HILDA], False)
    finally:
        await link_cm.__aexit__(None, None, None)
