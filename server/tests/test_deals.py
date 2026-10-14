import asyncio

import gothic_ai.deals
from gothic_ai.chronicle import Chronicle
from gothic_ai.contracts import gold_text
from gothic_ai.deals import Deals, LieRef
from gothic_ai.dialogue import DialogueManager
from gothic_ai.llm import ToolCall
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.memory import MemoryStore
from gothic_ai.persona import PersonaProvider
from gothic_ai.talk import Talk, TalkDesk
from gothic_ai.trust import Trust
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
        assert "сделка состоялась" in llm.calls[2][1][-1].content
        assert [t.name for t in llm.calls[2][2]] == ["set_intent"]  # a wish fulfilled may be dropped
        notes = [e.content for e in dialogue.conversations[XARDAS].memory.entries]
        assert any("Герой принял твою сделку" in n and "30 золотых" in n for n in notes)
        assert XARDAS not in deals.open and deals.situation(XARDAS) is None
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_declined_the_npc_offers_otherwise():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = FakeBackend(
        script=[
            FakeStep("Эликсир — за пятьдесят.", [_offer(1, 50)]),
            FakeStep("Ну, тогда сорок.", [_offer(1, 40, "c2")]),
        ],
        delay=0,
    )
    link_cm, dialogue, chronicle, deals = await _setup(fake, llm, store)
    try:
        await dialogue.say_as_player(XARDAS, "Продай эликсир.")
        await _until(lambda: len(_shown(fake)) == 1)
        # N: the window is gone in the game already; the npc hears of it and may offer again
        await fake.send(type="deal_decline", deal="D1", version=1)
        await _until(lambda: len(llm.calls) == 2)
        assert "Герой отказался от сделки" in llm.calls[1][1][-1].content
        assert [t.name for t in llm.calls[1][2]] == ["propose_deal"]
        # offered less, the request's tools are the same: the start of the npc's requests does not change
        every = [t.name for t in llm.sent_tools[0]]
        assert "propose_deal" in every and "withdraw_deal" in every and "set_intent" in every
        assert [[t.name for t in tools] for tools in llm.sent_tools] == [every, every]
        assert llm.cache_keys == [XARDAS, XARDAS]
        await _until(lambda: len(_shown(fake)) == 2)
        again = _shown(fake)[1]
        assert (again["deal"], again["version"], again["hero_gives"][0]["count"]) == ("D2", 1, 40)
        assert {"type": "deal_hide", "deal": "D1"} in fake.deals
        notes = [e.content for e in dialogue.conversations[XARDAS].memory.entries]
        assert any("Герой отказался от твоего предложения" in n and "50 золотых" in n for n in notes)
        assert chronicle.facts("deal") == []
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


async def test_a_thing_of_its_story_by_a_guessed_id():
    # 2026-10-14: Cavalorn asked for his letter as ITWR_CAVALORNLETTER twice, and no window came
    sealed, opened, map_ = (
        "ITWR_SATURASFIRSTMESSAGE_ADDON_SEALED",
        "ITWR_SATURASFIRSTMESSAGE_ADDON",
        "ITWR_MAP_NEWWORLD_ORNAMENTS_ADDON",
    )
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    link_cm, dialogue, chronicle, deals = await _setup(fake, FakeBackend("…", delay=0), store)
    fake.symbols.update({sealed: "item", opened: "item", map_: "item", "DIA_X": "dialog"})
    fake.item_names.update({sealed: "Запечатанное послание", opened: "Вскрытое послание", map_: "Карта"})
    deals.things = lambda npc: {sealed, opened, map_, GOLD, "ITMI_NOT_IN_THE_GAME", "DIA_X"}
    try:
        snap = dialogue.link.snapshots[XARDAS]

        async def offer(hero_gives, bring=()):
            args = {
                "npc_gives": [{"item": POTION, "count": 1}],
                "hero_gives": hero_gives,
                "task": {"kill": [], "bring": list(bring), "days": 0},
            }
            return await deals.propose(XARDAS, "Ксардас", args, snap)

        letter = {"item": "ITWR_CAVALORNLETTER", "count": 1}
        fake.hero_items = {map_: 1, sealed: 1, GOLD: 30}  # the map is a letter too, but not his: one of his
        deals.things = lambda npc: {sealed, opened, GOLD}
        o = await offer([letter, {"item": GOLD, "count": 5}])
        assert o.status == "accepted"
        assert "Предмета ITWR_CAVALORNLETTER нет" in o.output
        assert f"Запечатанное послание ({sealed})" in o.output
        await _until(lambda: _shown(fake) != [])
        assert [t["item"] for t in _shown(fake)[-1]["hero_gives"]] == [sealed, GOLD]

        # he has none of its letters, or two: it is told the things of its story, of the kind it asked first
        deals.things = lambda npc: {sealed, opened, map_, GOLD, "ITMI_NOT_IN_THE_GAME", "DIA_X"}
        for items in ({GOLD: 30}, {sealed: 1, map_: 1}):
            fake.hero_items = items
            o = await offer([letter])
            assert o.status == "rejected" and o.output == (
                "Отклонено: нет таких предметов: ITWR_CAVALORNLETTER. Вещи из твоих дел: "
                f"Карта ({map_}); Вскрытое послание ({opened}); Запечатанное послание ({sealed}); "
                f"Золото ({GOLD})."
            )
        # what it wants brought some day is not looked for on him
        fake.hero_items = {sealed: 1}
        o = await offer([], bring=[letter])
        assert o.status == "rejected" and "нет таких предметов: ITWR_CAVALORNLETTER." in o.output
        # with no story graph it is just told there is no such thing
        deals.things = None
        o = await offer([letter])
        assert o.output == "Отклонено: нет таких предметов: ITWR_CAVALORNLETTER."
        assert len(_shown(fake)) == 1
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


BOOK = f"caught:{XARDAS}:MIS_BOOK"


async def test_he_forgives_a_lie_and_keeps_quiet_about_it():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    link_cm, dialogue, chronicle, deals = await _setup(fake, FakeBackend("…", delay=0), store)
    try:
        snap = dialogue.link.snapshots[XARDAS]
        caught = {
            "fact": "caught",
            "quest": "MIS_BOOK",
            "npc": XARDAS,
            "name": "Ксардас",
            "title": "Принести книгу",
        }
        chronicle.record({**caught, "how": "видел сам", "day": 1})
        assert "он уже лгал тебе" in Trust(chronicle).situation(XARDAS)
        situation = deals.situation(XARDAS) or ""
        assert f"- {BOOK}: он обманул тебя: уверял, что сделал «Принести книгу», а это неправда" in situation

        async def offer(**terms):
            args = {
                "npc_gives": [],
                "hero_gives": [],
                "forgive": [],
                "silence": [],
                "hero_later": [],
                "repay": [],
            }
            return await deals.propose(XARDAS, "Ксардас", {**args, **terms}, snap)

        o = await offer(forgive=["caught:NOBODY:MIS_X"])
        assert o.status == "rejected" and "ты не знаешь такой лжи" in o.output and BOOK in o.output
        o = await offer(forgive=[BOOK], silence=[BOOK], hero_gives=[{"item": GOLD, "count": 30}])
        assert o.status == "accepted" and "прощаешь ему ложь про «Принести книгу»" in o.output
        await _until(lambda: _shown(fake) != [])
        shown = _shown(fake)[-1]
        assert [t["text"] for t in shown["npc_gives"]] == [
            "Прощает ложь про «Принести книгу»",
            "Молчит о лжи про «Принести книгу»",
        ]
        assert shown["npc_gives"][0]["item"] == ""  # nothing to check in his purse

        fake.hero_items = {GOLD: 30}
        await deals.accept(shown["deal"], 1)
        fact = chronicle.facts("deal")[0]
        assert (fact["forgive"], fact["silence"], fact["gold"]) == ([BOOK], [BOOK], 30)
        line = Trust(chronicle).situation(XARDAS) or ""
        assert (
            "он уже лгал тебе" not in line
            and "он загладил вину за ложь: «Принести книгу» — заплатил 30" in line
        )
        # forgiven: no longer among what he can forgive
        assert BOOK not in (deals.situation(XARDAS) or "")
        o = await offer(forgive=[BOOK])
        assert o.status == "rejected" and "уже простил" in o.output
    finally:
        await link_cm.__aexit__(None, None, None)


def test_a_lie_in_his_own_words():
    said = LieRef(f"caught:{XARDAS}:", "всё утро был в городе", XARDAS, "Ксардас", said=True)
    assert said.words(XARDAS) == "обманул тебя: сказал «всё утро был в городе», а это неправда"
    assert said.words("LESTER") == (
        "обманул человека по имени Ксардас: сказал «всё утро был в городе», а это неправда"
    )
    quest = LieRef(BOOK, "Принести книгу", XARDAS, "Ксардас")
    assert quest.words(XARDAS) == "обманул тебя: уверял, что сделал «Принести книгу», а это неправда"
    # a reward taken for nothing reads the same with or without it
    found = LieRef(f"found_out:{XARDAS}:MIS_BOOK", "Принести книгу", XARDAS, "Ксардас", said=True)
    assert "взял награду за «Принести книгу»" in found.words(XARDAS)


async def test_lies_of_keeps_his_own_words():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    link_cm, _dialogue, chronicle, deals = await _setup(fake, FakeBackend("…", delay=0), store)
    try:
        lie = {"quest": "", "title": "всё утро был в городе", "said": True, "day": 1}
        chronicle.record({"fact": "caught", "npc": XARDAS, "name": "Ксардас", "how": "видел сам", **lie})
        chronicle.record({"fact": "caught", "npc": XARDAS, "name": "Ксардас", "quest": "MIS_BOOK"})
        heard = {"kind": "caught", "about": XARDAS, "about_name": "Ксардас", "from": XARDAS, "hops": 1}
        chronicle.record({"fact": "rumor", "to": "LESTER", **heard, **lie})
        known, _, _ = deals.lies_of(XARDAS)
        assert known[f"caught:{XARDAS}:"].said and not known[BOOK].said
        heard_of = deals.lies_of("LESTER")[0][f"caught:{XARDAS}:"]
        assert heard_of.said and heard_of.words("LESTER").endswith(
            "сказал «всё утро был в городе», а это неправда"
        )
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_a_debt_by_its_day_or_a_broken_word():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    link_cm, dialogue, chronicle, deals = await _setup(fake, FakeBackend("…", delay=0), store)
    try:
        snap = dialogue.link.snapshots[XARDAS]
        args = {
            "npc_gives": [],
            "hero_gives": [],
            "forgive": [],
            "silence": [],
            "hero_later": [],
            "repay": [],
        }
        o = await deals.propose(
            XARDAS,
            "Ксардас",
            {
                **args,
                "npc_gives": [{"item": POTION, "count": 1}],
                "hero_later": [{"item": GOLD, "count": 25, "days": 1}],
            },
            snap,
        )
        assert o.status == "accepted" and "отдаст позже 25 золотых до конца дня 3" in o.output  # today: day 2
        await _until(lambda: _shown(fake) != [])
        shown = _shown(fake)[-1]
        assert shown["hero_gives"] == [{"item": "", "count": 0, "text": "Позже: 25 золотых — до конца дня 3"}]
        await deals.accept(shown["deal"], 1)
        assert fake.hero_items == {POTION: 1}  # the potion now, the gold later
        (debt,) = deals.debts.values()
        assert (debt.due, debt.give.count, debt.paid) == (2, 25, False)
        assert f"- {debt.id}: 25 золотых до конца дня 3" in (deals.situation(XARDAS) or "")

        # the day is over: a broken word, to him and in his memory
        assert deals.overdue(2) == []
        assert deals.overdue(3) == [debt] and deals.overdue(4) == []
        assert chronicle.facts("broken")[0]["what"] == "25 золотых до конца дня 3"
        line = Trust(chronicle).situation(XARDAS) or ""
        assert "он не сдержал слово: обещал вернуть 25 золотых до конца дня 3, а не вернул." in line
        assert "На слово ты ему больше не веришь" in line and Trust(chronicle).broken(XARDAS)
        assert "срок прошёл, он не сдержал слово" in (deals.situation(XARDAS) or "")
        notes = [e.content for e in dialogue.conversations[XARDAS].memory.entries]
        assert any("Герой не вернул тебе долг в срок" in n for n in notes)

        # late, he pays it back
        o = await deals.propose(XARDAS, "Ксардас", {**args, "repay": ["nope"]}, snap)
        assert o.status == "rejected" and debt.id in o.output
        # the debt again in his gold as well (a live model did that): told what it comes to
        twice = {**args, "repay": [debt.id], "hero_gives": [{"item": GOLD, "count": 25}]}
        o = await deals.propose(XARDAS, "Ксардас", twice, snap)
        assert (
            o.status == "accepted" and "долг уже включает своё" in o.output and "всего 50 золотых" in o.output
        )
        await deals.propose(XARDAS, "Ксардас", {**args, "repay": [debt.id]}, snap)
        await _until(lambda: len(_shown(fake)) == 3)
        shown = _shown(fake)[-1]
        assert shown["hero_gives"] == [{"item": GOLD, "count": 25, "text": "Долг: 25 золотых"}]
        fake.hero_items = {GOLD: 25}
        await deals.accept(shown["deal"], shown["version"])
        assert debt.paid and chronicle.facts("debt_paid")[0]["id"] == debt.id and fake.hero_items[GOLD] == 0
        assert "Долги героя тебе" not in (deals.situation(XARDAS) or "")
        # a load brings the debts back as they stand
        deals._load()
        assert deals.debts[debt.id].paid and deals.debts[debt.id].broken
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_day_is_told_when_it_changes(monkeypatch):
    monkeypatch.setattr(gothic_ai.deals, "DEBTS_EVERY_S", 0.01)
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    link_cm, dialogue, chronicle, deals = await _setup(fake, FakeBackend("…", delay=0), store)
    watch = None
    try:
        days: list[int] = []
        deals.on_day.append(days.append)
        assert deals.day is None
        # a deal with a debt reads the day
        later = [{"item": GOLD, "count": 25, "days": 2}]
        o = await deals.propose(XARDAS, "Ксардас", {"hero_later": later}, None)
        assert o.status == "accepted"
        await _until(lambda: _shown(fake) != [])
        await deals.accept(_shown(fake)[-1]["deal"], 1)
        assert days == [1] and deals.day == 1

        # the debts' watch reads it again: the same day is not told twice, a new one is
        watch = asyncio.create_task(deals.run())
        await asyncio.sleep(0.1)
        assert days == [1]
        fake.day = 2
        await _until(lambda: days == [1, 2])
        assert deals.day == 2 and chronicle.facts("broken") == []
        fake.day = 4
        await _until(lambda: chronicle.facts("broken") != [])
        assert days == [1, 2, 4]

        # another world may be of another day
        await fake.send(type="loading")
        await _until(lambda: deals.day is None)

        # a debt written past the deals is theirs once they reload
        chronicle.record({"fact": "debt", "id": "e2e", "npc": XARDAS, "item": GOLD, "count": 5, "due": 3})
        assert "e2e" not in deals.debts
        deals.reload()
        assert deals.debts["e2e"].give.count == 5 and len(deals.debts) == 2
    finally:
        if watch is not None:
            watch.cancel()
        await link_cm.__aexit__(None, None, None)


class _IgnoresAllowed(FakeBackend):
    """A model that calls what it sees in the request, whether it is offered or not."""

    async def stream(
        self, *, instructions, messages, tools=None, tool_choice=None, allowed=None, cache_key=None
    ):
        async for event in super().stream(
            instructions=instructions, messages=messages, tools=tools, tool_choice=tool_choice
        ):
            yield event


async def test_a_tool_not_offered_in_the_turn_is_rejected():
    fake = FakeGame(branch="A")
    store = MemoryStore(":memory:")
    llm = _IgnoresAllowed(
        script=[FakeStep("Эликсир — за пятьдесят.", [_offer(1, 50)]), FakeStep("Ладно.")], delay=0
    )
    link_cm, dialogue, _, deals = await _setup(fake, llm, store)
    try:
        dialogue.event_turn(XARDAS, "[Герой кивнул.]", tools=("set_intent",))
        await _until(lambda: len(llm.calls) == 2)
        assert "propose_deal" in [t.name for t in llm.calls[0][2]]  # in the request
        assert "сейчас propose_deal тебе недоступен" in llm.calls[1][1][-1].output
        assert XARDAS not in deals.open and _shown(fake) == []
    finally:
        await link_cm.__aexit__(None, None, None)
