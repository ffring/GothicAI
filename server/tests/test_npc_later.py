from dataclasses import replace

import pytest

from gothic_ai.characters import Card
from gothic_ai.deals import MAX_DAYS, PROPOSE_TOOL
from gothic_ai.llm.fake import FakeBackend
from gothic_ai.memory import MemoryStore
from gothic_ai.protocol import RosterEntry
from tests.fake_game import FakeGame
from tests.test_deals import GOLD, POTION, _setup
from tests.test_dialogue import XARDAS

CARD = Card(XARDAS, "Ксардас", "", "", 3, 4, 4, 3, 3, 3)


async def offer(deals, *, day=3, items=None, **extra):
    args = {
        "npc_later": {"items": items or [{"item": POTION, "count": 1}], "day": day},
        **extra,
    }
    return await deals.propose(XARDAS, "Ксардас", args, deals.link.snapshots[XARDAS])


async def test_later_window_accept_due_payment_and_reload_once():
    fake = FakeGame(branch="A")
    link_cm, dialogue, chronicle, deals = await _setup(
        fake, FakeBackend("…", delay=0), MemoryStore(":memory:")
    )
    dialogue.event_turn = lambda *a, **kw: None
    deals.cards = {XARDAS: CARD}
    fake.hero_items = {GOLD: 20}
    try:
        result = await offer(deals, hero_gives=[{"item": GOLD, "count": 5}])
        assert result.status == "accepted"
        shown = deals.open[XARDAS].show()
        assert shown.npc_gives[0].item == "" and shown.npc_gives[0].count == 0
        assert shown.npc_gives[0].text == "отдаст потом: Эликсир лечения, с дня 3"
        await deals.accept(shown.deal, shown.version)
        assert fake.hero_items == {GOLD: 15}
        assert chronicle.facts("deal")[0]["npc_later"]["day"] == 2
        assert chronicle.facts("npc_intent")[0]["keeps"] is True
        await deals.prepare(XARDAS)  # day 2, too early
        assert POTION not in fake.hero_items
        fake.day = 2
        await deals.prepare(XARDAS)
        assert fake.hero_items[POTION] == 1
        assert len(chronicle.facts("npc_later_paid")) == 1
        effects = [x for x in fake.story if x["type"] == "story_effect"][-1]
        assert effects["check"] is True and effects["effects"] == [
            {"do": "give", "item": POTION, "count": 1, "to": "hero"}
        ]
        deals.reload()
        await deals.prepare(XARDAS)
        assert fake.hero_items[POTION] == 1 and len(chronicle.facts("npc_later_paid")) == 1
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_dishonest_refuses_once_and_intent_survives_reload_and_card_change():
    fake = FakeGame(branch="A")
    link_cm, dialogue, chronicle, deals = await _setup(
        fake, FakeBackend("…", delay=0), MemoryStore(":memory:")
    )
    dialogue.event_turn = lambda *a, **kw: None
    deals.cards = {XARDAS: replace(CARD, honesty=1)}
    deals.roster = lambda: {"HEARER": RosterEntry(id="HEARER", name="Слышал", distance=100)}
    try:
        assert (await offer(deals)).status == "accepted"
        deal = deals.open[XARDAS]
        assert "не собираешься" not in (deals.situation(XARDAS) or "")
        await deals.accept(deal.id, deal.version)
        assert "ты не собираешься выполнять" in deals.situation(XARDAS)
        assert not chronicle.facts("npc_broken")
        fake.day = 2
        await deals.prepare(XARDAS)
        assert POTION not in fake.hero_items
        broken = chronicle.facts("npc_broken")[0]
        assert broken["how"] == "unpaid" and broken["witnesses"] == ["HEARER"]
        assert "ты решил не отдавать" in deals.situation(XARDAS)
        deals.cards = {XARDAS: CARD}
        deals.reload()
        await deals.prepare(XARDAS)
        assert POTION not in fake.hero_items
        assert len(chronicle.facts("npc_broken")) == len(chronicle.facts("npc_intent")) == 1
        # The new, immediate exchange is still honest, even from this NPC.
        assert (
            await deals.propose(
                XARDAS,
                "Ксардас",
                {"npc_gives": [{"item": POTION, "count": 1}]},
                deals.link.snapshots[XARDAS],
            )
        ).status == "accepted"
        new = deals.open[XARDAS]
        await deals.accept(new.id, new.version)
        assert fake.hero_items[POTION] == 1
    finally:
        await link_cm.__aexit__(None, None, None)


async def test_honest_shortage_is_not_cheating_and_retries():
    fake = FakeGame(branch="A")
    link_cm, dialogue, chronicle, deals = await _setup(
        fake, FakeBackend("…", delay=0), MemoryStore(":memory:")
    )
    dialogue.event_turn = lambda *a, **kw: None
    deals.cards = {XARDAS: CARD}
    try:
        assert (await offer(deals)).status == "accepted"
        deal = deals.open[XARDAS]
        await deals.accept(deal.id, deal.version)
        fake.day = 2
        fake.npc_items[XARDAS] = {}
        await deals.link.snapshot(XARDAS)
        await deals.prepare(XARDAS)
        assert not chronicle.facts("npc_broken") and not chronicle.facts("npc_later_paid")
        assert "тебе нечем заплатить" in deals.situation(XARDAS)
        fake.npc_items[XARDAS] = {POTION: 1}
        await deals.link.snapshot(XARDAS)
        await deals.prepare(XARDAS)
        assert fake.hero_items[POTION] == 1 and len(chronicle.facts("npc_later_paid")) == 1
    finally:
        await link_cm.__aexit__(None, None, None)


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {},
        {"items": [], "day": 3},
        {"items": [{"item": POTION, "count": 1}], "day": 0},
        {"items": [{"item": POTION, "count": 1}], "day": 2 + MAX_DAYS + 1},
        {"items": [{"item": "ITMI_NO_SUCH_ITEM", "count": 1}], "day": 3},
    ],
)
async def test_later_validation(raw):
    fake = FakeGame(branch="A")
    link_cm, dialogue, chronicle, deals = await _setup(
        fake, FakeBackend("…", delay=0), MemoryStore(":memory:")
    )
    try:
        result = await deals.propose(XARDAS, "Ксардас", {"npc_later": raw}, deals.link.snapshots[XARDAS])
        assert result.status == "rejected"
        assert XARDAS not in deals.open
        assert "npc_later" in PROPOSE_TOOL.parameters["required"]
    finally:
        await link_cm.__aexit__(None, None, None)
