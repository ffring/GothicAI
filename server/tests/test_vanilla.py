import asyncio
import copy
import json
from pathlib import Path

import pytest

from gothic_ai.chronicle import Chronicle
from gothic_ai.dialogue import DialogueManager
from gothic_ai.gamedata.storygraph import StoryGraph, load
from gothic_ai.llm import ToolCall, ToolResult
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.memory import MemoryStore
from gothic_ai.persona import PersonaProvider
from gothic_ai.vanilla import VanillaQuests, _takes
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
        assert "у героя с собой Репа ×0 из 20" in situation  # what Lobart sees for himself
        # he sees the hero has none on him: he may call it a lie to his face; nothing to go and check
        assert [t.name for t in vanilla.tools(LOBART)] == ["accept_quest", "expose_lie"]

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
        situation = vanilla.situation(LOBART)
        assert "будут мертвы: Бандит" in situation and "далеко, проверить сам не можешь" in situation
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
        near = [{"npc": LOBART, "distance": 900}]  # behind a wall: Lobart does not see him
        await fake.send(
            type="story_world", tags=[{"tag": f"{KILL}_ALIVE", "alive": 1, "dead": 0, "near": near}]
        )
        await asyncio.sleep(1.5)
        assert chronicle.facts("found_out") == []
        near = [{"npc": LOBART, "distance": 900, "seen": 900}]
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


def _outputs(llm: FakeBackend) -> str:
    """What came back to the model from its tool calls, all steps."""
    return " ".join(m.output for _, items, _ in llm.calls for m in items if isinstance(m, ToolResult))


def test_an_ending_takes_what_the_hero_has():
    effects = [
        {"do": "give", "item": "ITPL_BEET", "count": 20, "to": "self", "take": True},
        {"do": "give", "item": "ITMI_GOLD", "count": 5},
    ]
    assert _takes(effects, {"ITPL_BEET": 5}, truth=False) == [
        {"do": "give", "item": "ITPL_BEET", "count": 5, "to": "self"},
        {"do": "give", "item": "ITMI_GOLD", "count": 5},
    ]
    assert _takes(effects, {}, truth=False) == [{"do": "give", "item": "ITMI_GOLD", "count": 5}]
    assert _takes(effects, {"ITPL_BEET": 20}, truth=True)[0]["count"] == 20


async def test_what_the_hero_has_on_him_is_seen_and_the_rest_is_the_lie(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(script=[ACCEPT, FakeStep("Ну?")], delay=0)
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    fake.hero_items = {"ITPL_BEET": 5}
    try:
        dialogue.open(LOBART, "Лобарт")
        await vanilla.prepare(LOBART)
        assert "у героя с собой Репа ×5 из 20 — это он отдаст тебе сразу" in vanilla.situation(LOBART)
        await dialogue.say_as_player(LOBART, "Вот пять, остальные пятнадцать в амбаре.")
        await _until(lambda: chronicle.facts("lie") != [])
        lie = chronicle.facts("lie")[0]
        assert (lie["truth"], lie["items"]) == ("ITPL_BEET 5 of 20", "Репа ×15")

        fake.day = 2
        run = asyncio.create_task(vanilla.run())
        await _until(lambda: chronicle.facts("found_out") != [], 10)
        run.cancel()
        assert "(Репа ×15)" in chronicle.facts("found_out")[0]["how"]
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_someone_alive_before_its_eyes_is_not_taken_on_word(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    accept = FakeStep("Мёртв, говоришь?", [ToolCall("accept_quest", {"quest": KILL}, "c1")])
    expose = FakeStep("Врёшь! Вон он стоит.", [ToolCall("expose_lie", {"quest": KILL}, "c2")])
    llm = FakeBackend(script=[accept, expose, FakeStep("Пошёл вон.")], delay=0)
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    fake.vars[KILL] = 1
    fake.npc_states[BANDIT] = {"state": "alive", "name": "Бандит", "distance": 1200}
    fake.people_near = [{"npc": HILDA, "name": "Хильда", "distance": 400, "guild": 0}]
    try:
        dialogue.open(LOBART, "Лобарт")
        await vanilla.prepare(LOBART)
        assert "у тебя на глазах: Бандит — жив" in vanilla.situation(LOBART)
        assert [t.name for t in vanilla.tools(LOBART)] == ["accept_quest", "expose_lie"]

        await dialogue.say_as_player(LOBART, "Бандит мёртв, я его убил.")
        await _until(lambda: chronicle.facts("caught") != [])
        assert "Отклонено: Бандит жив — у тебя на глазах, в 12 м" in _outputs(llm)
        assert chronicle.facts("claim") == [] and chronicle.facts("lie") == []  # nothing paid
        c = chronicle.facts("caught")[0]
        assert (c["quest"], c["npc"], c["how"]) == (
            KILL,
            LOBART,
            "видел сам: Бандит жив — у тебя на глазах, в 12 м",
        )
        await _until(lambda: chronicle.facts("rumor") != [])
        assert chronicle.facts("rumor")[0]["to"] == HILDA
        assert dialogue.conversations[LOBART].intent.attitude == "wary"
        assert not any(m["type"] == "story_effect" for m in fake.story)  # no beating: he took nothing
        trust = vanilla.trust
        assert "он уже лгал тебе: «Бандит у фермы» — видел сам" in (trust.situation(LOBART) or "")
        assert "ты слышал, что он обманул других: Лобарт («Бандит у фермы»)" in (trust.situation(HILDA) or "")
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_the_npc_goes_to_see_and_catches_the_lie(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    check = FakeStep("Сейчас сам посмотрю.", [ToolCall("check_claim", {"quest": KILL}, "c1")])
    llm = FakeBackend(script=[check, FakeStep("Ах ты лжец!")], delay=0)
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    fake.vars[KILL] = 1
    fake.npc_states[BANDIT] = {"state": "alive", "name": "Бандит", "distance": 4000}
    try:
        await vanilla.link.attach(LOBART)
        dialogue.open(LOBART, "Лобарт")
        await vanilla.prepare(LOBART)
        assert "Бандит — в 40 м, отсюда не видно: можешь сходить и посмотреть сам" in vanilla.situation(
            LOBART
        )
        # (expose_lie is for the turnips: the hero has none on him)
        assert [t.name for t in vanilla.tools(LOBART)] == ["accept_quest", "check_claim", "expose_lie"]

        await dialogue.say_as_player(LOBART, "Бандит мёртв.")
        await _until(lambda: chronicle.facts("caught") != [])
        await _until(lambda: LOBART not in vanilla.checking)
        go = [m["args"] for m in fake.received if m["type"] == "action" and m["action"] == "go_to_npc"]
        # there, to see; back to the hero, to say it to his face
        assert go == [{"npc": BANDIT, "radius": 1800}, {"npc": "PLAYER", "radius": 300}]
        assert "сходил проверить и увидел: Бандит жив, в 18 м" in chronicle.facts("caught")[0]["how"]
        await _until(lambda: len(llm.calls) >= 2)  # he says what he thinks of it
        assert "Он тебе солгал" in str(llm.calls[-1][1])
        assert chronicle.facts("claim") == []
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_behind_a_wall_is_not_before_its_eyes(graph):
    """The bandit is 10 m from Lobart, behind the barn: Lobart does not see him (the engine's sight), so he
    can neither call it a lie to his face nor refuse on what he sees; he goes, looks that way, and sees."""
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    check = FakeStep("Пойду гляну.", [ToolCall("check_claim", {"quest": KILL}, "c1")])
    llm = FakeBackend(script=[check, FakeStep("Ах ты лжец!")], delay=0)
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    fake.vars[KILL] = 1
    fake.npc_states[BANDIT] = {"state": "alive", "name": "Бандит", "distance": 1000, "seen": False}
    try:
        await vanilla.link.attach(LOBART)
        dialogue.open(LOBART, "Лобарт")
        await vanilla.prepare(LOBART)
        situation = vanilla.situation(LOBART) or ""
        assert "на глазах" not in situation
        assert "Бандит — в 10 м, отсюда не видно: можешь сходить и посмотреть сам" in situation
        assert "check_claim" in [t.name for t in vanilla.tools(LOBART)]

        await dialogue.say_as_player(LOBART, "Бандит мёртв.")
        await _until(lambda: chronicle.facts("caught") != [])
        acts = [(m["action"], m["args"].get("npc")) for m in fake.received if m["type"] == "action"]
        assert acts[:2] == [("go_to_npc", BANDIT), ("turn_to", BANDIT)]  # there, and looks that way
        assert "сходил проверить и увидел: Бандит жив, в 18 м" in chronicle.facts("caught")[0]["how"]
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_the_npc_goes_to_see_and_pays_for_the_truth(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    check = FakeStep("Сейчас сам посмотрю.", [ToolCall("check_claim", {"quest": KILL}, "c1")])
    llm = FakeBackend(script=[check, FakeStep("Правда. Молодец.")], delay=0)
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    fake.vars[KILL] = 1
    fake.npc_states[BANDIT] = {"state": "dead", "name": "Бандит", "distance": 4000}
    try:
        await vanilla.link.attach(LOBART)
        dialogue.open(LOBART, "Лобарт")
        await dialogue.say_as_player(LOBART, "Бандит мёртв.")
        await _until(lambda: chronicle.facts("claim") != [])
        claim = chronicle.facts("claim")[0]
        assert (claim["truth"], claim["checked"]) == (True, True)
        assert {"do": "xp", "amount": 100} in _effects(fake, LOBART)[0]
        assert chronicle.facts("caught") == [] and chronicle.facts("lie") == []
        assert "он уже говорил тебе правду: «Бандит у фермы»" in (vanilla.trust.situation(LOBART) or "")
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_once_lied_to_it_takes_his_word_no_more(graph):
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(
        script=[ACCEPT, FakeStep("Не верю."), ACCEPT, FakeStep("Вот это другое дело.")], delay=0
    )
    link_cm, dialogue, chronicle, vanilla = await _setup(fake, llm, graph, store)
    try:
        chronicle.record(
            {"fact": "caught", "quest": KILL, "npc": LOBART, "title": "Бандит у фермы", "how": "видел сам"}
        )
        dialogue.open(LOBART, "Лобарт")
        await dialogue.say_as_player(LOBART, "Репа в амбаре, двадцать штук.")
        await _until(lambda: "Отклонено: он уже лгал тебе" in _outputs(llm))
        assert chronicle.facts("claim") == []

        # what it sees for itself it takes: he brings all twenty
        fake.hero_items = {"ITPL_BEET": 20}
        await dialogue.say_as_player(LOBART, "Вот вся репа.")
        await _until(lambda: chronicle.facts("claim") != [])
        assert chronicle.facts("claim")[0]["truth"] is True
    finally:
        await link_cm.__aexit__(None, None, None)
