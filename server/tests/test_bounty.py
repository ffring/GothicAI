from contextlib import asynccontextmanager

import pytest

from gothic_ai.bounty import BRAGO, LURE, Bounty
from gothic_ai.chronicle import Chronicle
from gothic_ai.contracts import GOLD, Give
from gothic_ai.dialogue import DialogueManager
from gothic_ai.executor import Intent
from gothic_ai.game import GameError
from gothic_ai.llm import FakeBackend, ToolCall
from gothic_ai.memory import MemoryStore
from gothic_ai.people_knowledge import PeopleKnowledge
from gothic_ai.persona import PersonaProvider
from gothic_ai.protocol import Detached, HeroDeed, NpcEvent, Perception, Session, Snapshot
from gothic_ai.social import Rumors, Social
from tests.fake_game import FakeGame, connected

MATE, ROAD, CITIZEN = "BDT_1015_BANDIT_L", "BDT_1090_BANDIT", "VLK_414_RUPERT"
DEXTER = "BDT_1060_DEXTER"


@asynccontextmanager
async def world():
    fake = FakeGame()
    fake.nearby = [
        {"id": MATE, "name": "Бандит", "distance": 350, "guild": 9},
        {"id": CITIZEN, "name": "Руперт", "distance": 100, "guild": 3},
    ]
    async with connected(fake) as link:
        store = MemoryStore(":memory:")
        dialogue = DialogueManager(link, FakeBackend(delay=0), PersonaProvider(None), memory=store)
        dialogue.guild_of = lambda npc: 3 if npc == CITIZEN else 9
        chronicle = Chronicle(link, store)
        await link.attach(BRAGO)
        await link.snapshot(BRAGO, fresh=True)
        for npc in (LURE, MATE, ROAD, CITIZEN):
            link.snapshots[npc] = Snapshot.model_validate(fake.snapshot(npc))
        bounty = Bounty(link, dialogue, chronicle)
        dialogue.extensions.append(bounty)
        effect = link.story_effect

        async def apply(npc, effects, **kwargs):
            await effect(npc, effects, **kwargs)
            for operation in effects:
                if operation.get("do") == "set":
                    fake.vars[operation["var"]] = operation["value"]

        link.story_effect = apply
        yield fake, link, dialogue, chronicle, bounty


async def disclose(dialogue, bounty, npc=BRAGO, secret="client_name"):
    conv = dialogue.open(npc, "Браго" if npc == BRAGO else "Бандит", warm=False)
    return await bounty.handle(
        dialogue, conv, ToolCall("tell_secret", {"secret": secret}), dialogue.link.snapshots.get(npc)
    )


def health(link, bounty, hp, maximum=100):
    snap = link.snapshots[BRAGO]
    snap = snap.model_copy(update={"npc": snap.npc.model_copy(update={"hp": hp, "hp_max": maximum})})
    link.snapshots[BRAGO] = snap
    bounty._on_message(snap)


async def calm(dialogue):
    conv = dialogue.open(BRAGO, "Браго", warm=False)
    conv.intent = Intent("calm", "PLAYER", "neutral")
    return await dialogue._calm(conv, conv.intent)


async def test_knowledge_tiers_keep_public_threat_separate_from_client_secrets():
    async with world() as (_, _, _, _, bounty):
        mate, lure, brago = [bounty.situation(npc) for npc in (MATE, LURE, BRAGO)]
        for situation in (mate, lure, brago):
            assert situation.startswith("Охота на героя:")
            assert "за его голову платят" in situation and "листовки" in situation
            assert "свободно" in situation
        assert "Декстер" not in mate and "CASTLEMINE_HUT" not in mate
        assert "не знаешь" in mate
        for situation in (lure, brago):
            assert "тайна client_name" in situation and "Декстер" in situation
            assert "тайна client_place" in situation and "CASTLEMINE_HUT" in situation
            assert "без tell_secret" in situation
        assert "Сам ты заказчика не видел" in lure and "только Браго" in lure
        assert "10 золотых" in lure and "300" not in lure and "150" not in lure
        assert "Ты сам видел Декстера" in brago and "300 золотых" in brago
        assert "150 за мёртвого" in brago and "пленника надо привести к нему" in brago
        assert "150 золотых" in brago
        assert bounty.situation(ROAD) == mate
        assert bounty.tools(MATE) == [] and bounty.tools(ROAD) == []
        assert [tool.name for tool in bounty.tools(BRAGO)] == ["tell_secret"]


async def test_only_khorinis_bandits_have_bounty_knowledge():
    async with world() as (_, link, _, _, bounty):
        assert bounty.situation(CITIZEN) is None and bounty.tools(CITIZEN) == []
        assert bounty.situation(DEXTER) is None and bounty.tools(DEXTER) == []
        link.world = "newworld.zen"
        assert bounty.situation(BRAGO).startswith("Охота на героя:")
        link.world = "OLDWORLD.ZEN"
        for npc in (MATE, LURE, BRAGO, ROAD):
            assert bounty.situation(npc) is None and bounty.tools(npc) == []


async def test_unpaid_and_unknown_secrets_are_rejected_without_effects():
    async with world() as (fake, _, dialogue, chronicle, bounty):
        outcome = await disclose(dialogue, bounty)
        assert outcome.status == "rejected" and "нет рычага" in outcome.output
        for npc, secret in ((MATE, "client_name"), (BRAGO, "raven")):
            outcome = await disclose(dialogue, bounty, npc, secret)
            assert outcome.status == "rejected" and "не знаешь" in outcome.output
        assert not chronicle.facts("bounty_secret") and not chronicle.facts("bounty_betrayal")
        assert not fake.story


@pytest.mark.parametrize(("npc", "minimum"), [(LURE, 10), (BRAGO, 150)])
async def test_secret_sale_requires_minimum_gold_paid_now(npc, minimum):
    async with world() as (_, _, _, _, bounty):
        assert bounty.validate_sale(npc, "client_name", [Give(GOLD, minimum - 1, "Gold")], [])
        assert bounty.validate_sale(npc, "client_name", [Give(GOLD, minimum, "Gold")], []) is None
        assert bounty.validate_sale(
            npc, "client_place", [Give(GOLD, minimum, "Gold")], [Give(GOLD, 1, "Gold")]
        )
        assert bounty.validate_sale(MATE, "client_name", [Give(GOLD, 999, "Gold")], [])


@pytest.mark.parametrize("secret", ["client_name", "client_place"])
async def test_paid_secret_has_native_consequences_and_is_idempotent(secret):
    async with world() as (fake, _, dialogue, chronicle, bounty):
        chronicle.record({"fact": "deal", "id": "paid-secret", "npc": BRAGO, "secret": secret})
        outcome = await disclose(dialogue, bounty, secret=secret)
        assert outcome.status == "accepted" and "paid" in outcome.output
        assert fake.vars["BDT13_DEXTER_VERRATEN"] == 1 and fake.vars["MIS_STECKBRIEFE"] == 1
        assert fake.story[-1]["effects"] == [
            {"do": "set", "var": "BDT13_DEXTER_VERRATEN", "value": 1},
            {"do": "set", "var": "MIS_STECKBRIEFE", "value": 1},
            {
                "do": "journal", "topic": "Объявления о розыске и бандиты",
                "text": "Главаря бандитов зовут Декстер. Он скрывается в шахте около поместья лендлорда.",
                "status": "LOG_RUNNING",
            },
        ]
        assert chronicle.facts("bounty_secret") == [
            {"fact": "bounty_secret", "npc": BRAGO, "name": "Браго", "secret": secret, "leverage": "paid"}
        ]
        [betrayal] = chronicle.facts("bounty_betrayal")
        assert betrayal["npc"] == BRAGO and betrayal["witnesses"] == [MATE]
        assert "bounty_betrayal" not in chronicle.facts("bounty_secret")[0]
        facts_before, effects_before = chronicle.facts(), list(fake.story)
        assert (await disclose(dialogue, bounty, secret=secret)).status == "accepted"
        assert chronicle.facts() == facts_before and fake.story == effects_before


async def test_a_paid_name_does_not_authorize_the_place():
    async with world() as (_, _, dialogue, chronicle, bounty):
        chronicle.record({"fact": "deal", "id": "name", "npc": BRAGO, "secret": "client_name"})
        assert (await disclose(dialogue, bounty, secret="client_place")).status == "rejected"
        assert (await disclose(dialogue, bounty)).status == "accepted"


async def test_vanilla_friendship_is_live_and_belongs_only_to_the_lure():
    async with world() as (fake, _, dialogue, chronicle, bounty):
        fake.vars["BDT13_FRIEND"] = 1
        await bounty.prepare(LURE)
        assert "BDT13_FRIEND" in bounty.situation(LURE)
        assert (await disclose(dialogue, bounty)).status == "rejected"
        fake.vars["BDT13_FRIEND"] = 0
        assert (await disclose(dialogue, bounty, LURE)).status == "rejected"
        fake.vars["BDT13_FRIEND"] = 1
        assert (await disclose(dialogue, bounty, LURE)).status == "accepted"
        assert chronicle.facts("bounty_secret")[0]["leverage"] == "bond"


@pytest.mark.parametrize("injury", ["below_half", "knockdown"])
async def test_hero_must_win_and_the_npc_must_successfully_calm(injury):
    async with world() as (_, link, dialogue, chronicle, bounty):
        bounty._on_message(Perception(
            type="perception", npc=BRAGO, perc="ASSESSDAMAGE", other="PLAYER", distance=100
        ))
        assert (await disclose(dialogue, bounty)).status == "rejected"
        if injury == "below_half":
            health(link, bounty, 49)
        else:
            bounty._on_message(Detached(type="detached", npc=BRAGO, reason="down"))
        assert (await disclose(dialogue, bounty)).status == "rejected"
        assert (await calm(dialogue))[1]
        assert (await disclose(dialogue, bounty)).status == "accepted"
        assert chronicle.facts("bounty_secret")[0]["leverage"] == "beaten"


@pytest.mark.parametrize("attacker", [None, MATE])
async def test_low_health_and_calm_do_not_prove_the_hero_won(attacker):
    async with world() as (_, link, dialogue, _, bounty):
        health(link, bounty, 49)
        if attacker:
            bounty._on_message(Perception(
                type="perception", npc=BRAGO, perc="ASSESSDAMAGE", other=attacker, distance=100
            ))
            bounty._on_message(NpcEvent(type="npc_event", npc=BRAGO, kind="attack", other=attacker))
        assert (await calm(dialogue))[1]
        assert (await disclose(dialogue, bounty)).status == "rejected"


async def test_exactly_half_health_is_not_a_defeat():
    async with world() as (_, link, dialogue, _, bounty):
        bounty._on_message(HeroDeed(
            type="hero_deed", id="hero-hit", kind="attack", victim=BRAGO,
            position={"x": 0, "y": 0, "z": 0},
        ))
        health(link, bounty, 50)
        assert (await calm(dialogue))[1]
        assert (await disclose(dialogue, bounty)).status == "rejected"


async def test_failed_calm_does_not_authorize_interrogation(monkeypatch):
    async with world() as (_, link, dialogue, _, bounty):
        bounty._on_message(NpcEvent(type="npc_event", npc=BRAGO, kind="attack", detail="you"))
        health(link, bounty, 49)

        async def failed(*args, **kwargs):
            raise GameError("calm failed")

        monkeypatch.setattr(link, "story_effect", failed)
        assert not (await calm(dialogue))[1]
        assert (await disclose(dialogue, bounty)).status == "rejected"


async def test_betrayal_uses_existing_rumors_and_dexter_only_knows_after_hearing():
    async with world() as (_, link, dialogue, chronicle, bounty):
        rumors = Rumors(link, dialogue, chronicle)
        rumors.social = Social([
            {"npc": MATE, "name": "Бандит", "guild": 9},
            {"npc": DEXTER, "name": "Декстер", "guild": 9},
        ])
        rumors.clock = 8 * 60
        chronicle.record({"fact": "deal", "id": "paid", "npc": BRAGO, "secret": "client_name"})
        await disclose(dialogue, bounty)
        [rumor] = list(rumors.rumors.values())
        assert rumor.subject == BRAGO and rumor.known == {MATE: 0}
        people = PeopleKnowledge(chronicle)
        assert "Браго сдал Декстера" in people.situation(MATE)
        assert people.situation(DEXTER) is None
        assert rumors.tell(rumor, MATE, DEXTER) == "told"
        [fact] = chronicle.facts("rumor")
        assert fact["subject"] == BRAGO and fact["to"] == DEXTER
        assert "Браго сдал Декстера" in people.situation(DEXTER)
        assert "со слов: Бандит" in people.situation(DEXTER)


async def test_save_branch_rolls_back_disclosure_and_defeat_leverage():
    async with world() as (_, link, dialogue, chronicle, bounty):
        before = chronicle.memory.head(chronicle.branch)
        bounty._on_message(NpcEvent(type="npc_event", npc=BRAGO, kind="attack", detail="you"))
        health(link, bounty, 49)
        await calm(dialogue)
        assert (await disclose(dialogue, bounty)).status == "accepted"
        session = Session(type="session", branch="before-bounty", parent=chronicle.branch, parent_seq=before)
        chronicle._on_message(session)
        bounty._on_message(session)
        assert bounty.revealed(BRAGO, "client_name") is None
        assert not chronicle.facts("bounty_betrayal")
        assert (await disclose(dialogue, bounty)).status == "rejected"
