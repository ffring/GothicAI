import asyncio

from gothic_ai.chronicle import Chronicle
from gothic_ai.deals import Deals, gold_text
from gothic_ai.dialogue import DialogueManager
from gothic_ai.llm import ToolCall
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.memory import MemoryStore
from gothic_ai.persona import PersonaProvider
from gothic_ai.talk import Talk, TalkDesk
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import XARDAS, _until

POTION, GOLD = "ITPO_HEALTH_01", "ITMI_GOLD"


def _offer(npc_gives: int, gold: int, call_id: str = "c1") -> ToolCall:
    args = {
        "npc_gives": [{"item": POTION, "count": npc_gives}],
        "hero_gives": [{"item": GOLD, "count": gold}],
    }
    return ToolCall("propose_deal", args, call_id)


async def _setup(fake: FakeGame, llm: FakeBackend, store: MemoryStore):
    fake.symbols = {GOLD: "item", POTION: "item"}
    fake.item_names = {GOLD: "Золото", POTION: "Эликсир лечения"}
    fake.npc_items = {XARDAS: {POTION: 3}}
    link_cm = connected(fake)
    link = await link_cm.__aenter__()
    dialogue = DialogueManager(link, llm, PersonaProvider(None), memory=store)
    chronicle = Chronicle(link, store)
    deals = Deals(link, dialogue, chronicle)
    dialogue.extensions.append(deals)
    await link.snapshot(XARDAS)
    dialogue.open(XARDAS, "Ксардас")
    return link_cm, dialogue, chronicle, deals


def _shown(fake: FakeGame) -> list[dict]:
    return [m for m in fake.deals if m["type"] == "deal_show"]


async def test_haggle_then_accept_what_he_saw():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(
        script=[
            FakeStep("Эликсир — за пятьдесят золотых.", [_offer(1, 50)]),
            FakeStep("Ладно, тридцать.", [_offer(1, 30, "c2")]),
            FakeStep("По рукам."),
        ],
        delay=0,
    )
    link_cm, dialogue, chronicle, deals = await _setup(fake, llm, store)
    try:
        assert "propose_deal" in [t.name for t in dialogue._offered(XARDAS)]
        assert "withdraw_deal" not in [t.name for t in dialogue._offered(XARDAS)]
        await dialogue.say_as_player(XARDAS, "Продай эликсир.")
        await _until(lambda: len(_shown(fake)) == 1)
        v1 = _shown(fake)[0]
        assert (v1["version"], v1["npc"]) == (1, XARDAS)
        assert v1["npc_gives"] == [{"item": POTION, "count": 1, "text": "Эликсир лечения"}]
        assert v1["hero_gives"] == [{"item": GOLD, "count": 50, "text": "50 золотых"}]
        assert "withdraw_deal" in [t.name for t in dialogue._offered(XARDAS)]

        # haggling changes the terms in the same window
        await dialogue.say_as_player(XARDAS, "Дорого.")
        await _until(lambda: len(_shown(fake)) == 2)
        v2 = _shown(fake)[1]
        assert (v2["deal"], v2["version"], v2["hero_gives"][0]["count"]) == (v1["deal"], 2, 30)
        situation = llm.calls[1][1][-2].content
        assert (
            "Твоё предложение герою ждёт его согласия: ты отдаёшь — Эликсир лечения; герой отдаёт — 50"
            in (situation)
        )

        # he has too little: nothing changes hands, the offer stays
        fake.hero_items = {GOLD: 10}
        await fake.send(type="deal_accept", deal=v1["deal"], version=2)
        await _until(lambda: fake.statuses.count("notice") == 1)
        assert deals.open[XARDAS].state == "open"
        assert fake.hero_items == {GOLD: 10} and chronicle.facts("deal") == []

        # what he saw a moment ago is not what is shown now: not carried out, the window shows v2 again
        fake.hero_items = {GOLD: 40}
        await fake.send(type="deal_accept", deal=v1["deal"], version=1)
        await _until(lambda: len(_shown(fake)) == 3)
        assert _shown(fake)[2]["version"] == 2 and fake.hero_items == {GOLD: 40}

        # he accepts v2: everything changes hands at once, the npc answers
        await fake.send(type="deal_accept", deal=v1["deal"], version=2)
        await _until(lambda: chronicle.facts("deal") != [])
        assert fake.hero_items == {GOLD: 10, POTION: 1} and fake.npc_items[XARDAS] == {POTION: 2, GOLD: 30}
        effects = [m for m in fake.story if m["type"] == "story_effect"][-1]
        assert effects["check"] is True
        assert {"type": "deal_hide", "deal": v1["deal"]} in fake.deals
        fact = chronicle.facts("deal")[0]
        assert (fact["npc"], fact["version"], fact["hero_gives"][0]["count"]) == (XARDAS, 2, 30)
        await _until(lambda: len(llm.calls) == 3)
        assert "сделка состоялась" in llm.calls[2][1][-1].content and llm.calls[2][2] == []
        notes = [e.content for e in dialogue.conversations[XARDAS].memory.entries]
        assert any("Герой принял твою сделку" in n and "30 золотых" in n for n in notes)
        assert XARDAS not in deals.open and deals.situation(XARDAS) is None
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_what_cannot_be_offered():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    link_cm, dialogue, chronicle, deals = await _setup(fake, FakeBackend("…", delay=0), store)
    try:
        snap = dialogue.link.snapshots[XARDAS]

        async def offer(npc_gives, hero_gives):
            return await deals.propose(
                XARDAS, "Ксардас", {"npc_gives": npc_gives, "hero_gives": hero_gives}, snap
            )

        o = await offer([{"item": "ITAR_PAL_H", "count": 1}], [])
        assert (
            o.status == "rejected" and "у тебя нет ITAR_PAL_H" in o.output and "Эликсир лечения" in o.output
        )
        o = await offer([{"item": POTION, "count": 5}], [])
        assert o.status == "rejected" and "у тебя только 3" in o.output
        o = await offer([{"item": "ITMW_ZWEIHAENDER1", "count": 1}], [])  # in its hands
        assert o.status == "rejected"
        o = await offer([], [{"item": "ITMI_NOTHING", "count": 1}])
        assert o.status == "rejected" and "нет таких предметов: ITMI_NOTHING" in o.output
        o = await offer([], [])
        assert o.status == "rejected" and "ничего нет" in o.output
        o = await offer([{"item": POTION, "count": 0}], [])
        assert o.status == "rejected"
        assert _shown(fake) == []

        # the same item twice is added up; the same terms again change nothing
        o = await offer(
            [{"item": POTION, "count": 1}, {"item": POTION, "count": 1}], [{"item": GOLD, "count": 5}]
        )
        assert o.status == "accepted"
        await _until(lambda: _shown(fake) != [])
        assert _shown(fake)[-1]["npc_gives"][0]["count"] == 2
        o = await offer([{"item": POTION, "count": 2}], [{"item": GOLD, "count": 5}])
        assert "уже видит" in o.output and len(_shown(fake)) == 1

        # withdrawn: the window goes
        call = ToolCall("withdraw_deal", {}, "w1")
        conv = dialogue.conversations[XARDAS]
        o = await deals.handle(dialogue, conv, call, snap)
        assert o is not None and o.status == "accepted" and XARDAS not in deals.open
        await _until(lambda: fake.deals[-1] == {"type": "deal_hide", "deal": "D1"})
        o = await deals.handle(dialogue, conv, call, snap)
        assert o is not None and o.status == "rejected"
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_an_offer_not_taken_goes_with_the_conversation():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    link_cm, dialogue, chronicle, deals = await _setup(fake, FakeBackend("…", delay=0), store)
    try:
        snap = dialogue.link.snapshots[XARDAS]
        args = {"npc_gives": [{"item": POTION, "count": 1}], "hero_gives": [{"item": GOLD, "count": 20}]}
        await deals.propose(XARDAS, "Ксардас", args, snap)
        deals.end(XARDAS, "walked_away")
        await _until(lambda: [m["type"] for m in fake.deals] == ["deal_show", "deal_hide"])
        notes = [e.content for e in dialogue.conversations[XARDAS].memory.entries]
        assert any("Герой ушёл, не приняв его, твоё предложение снято" in n for n in notes)
        # accepting it now does nothing
        fake.hero_items = {GOLD: 50}
        await fake.send(type="deal_accept", deal="D1", version=1)
        await asyncio.sleep(0.2)
        assert not any(m["type"] == "story_effect" for m in fake.story)
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_any_end_of_the_conversation_takes_the_offer():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    link_cm, dialogue, chronicle, deals = await _setup(fake, FakeBackend("…", delay=0), store)
    try:
        talk = TalkDesk(dialogue.link, dialogue)
        talk.on_talk_end.append(deals.end)
        snap = dialogue.link.snapshots[XARDAS]
        args = {"npc_gives": [{"item": POTION, "count": 1}], "hero_gives": [{"item": GOLD, "count": 20}]}
        await deals.propose(XARDAS, "Ксардас", args, snap)
        talk.partner = Talk(XARDAS, "Ксардас", 0.0, 0.0)
        talk._end("switched", "Диего")  # the hero turned to someone else
        await _until(lambda: XARDAS not in deals.open)
        await _until(lambda: fake.deals[-1] == {"type": "deal_hide", "deal": "D1"})
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_the_npc_no_longer_has_it():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend("Ох, а эликсира-то уже нет.", delay=0)
    link_cm, dialogue, chronicle, deals = await _setup(fake, llm, store)
    try:
        snap = dialogue.link.snapshots[XARDAS]
        args = {"npc_gives": [{"item": POTION, "count": 1}], "hero_gives": [{"item": GOLD, "count": 20}]}
        await deals.propose(XARDAS, "Ксардас", args, snap)
        fake.npc_items[XARDAS] = {}  # sold it to someone else meanwhile
        fake.hero_items = {GOLD: 50}
        await fake.send(type="deal_accept", deal="D1", version=1)
        await _until(lambda: XARDAS not in deals.open)
        assert fake.hero_items == {GOLD: 50} and chronicle.facts("deal") == []
        assert fake.deals[-1] == {"type": "deal_hide", "deal": "D1"}
        await _until(lambda: len(llm.calls) == 1)
        assert "Сделка сорвалась" in llm.calls[0][1][-1].content
    finally:
        await link_cm.__aexit__(None, None, None)


def test_gold_reads_right():
    assert [gold_text(n) for n in (1, 2, 11, 21, 30)] == [
        "1 золотой",
        "2 золотых",
        "11 золотых",
        "21 золотой",
        "30 золотых",
    ]
