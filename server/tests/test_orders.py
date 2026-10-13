import asyncio

from gothic_ai.dialogue import DialogueManager
from gothic_ai.executor import Intent
from gothic_ai.llm import FakeBackend
from gothic_ai.orders import Orders
from gothic_ai.persona import PersonaProvider
from gothic_ai.talk import TalkDesk
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import LOBART, XARDAS, _until
from tests.test_talk import person

GORN = "PC_FIGHTER"
BANDIT = "BDT_1013_BANDIT"


async def _desk(fake: FakeGame, llm, **orders):
    cm = connected(fake)
    link = await cm.__aenter__()
    dialogue = DialogueManager(link, llm, PersonaProvider(None))
    talk = TalkDesk(link, dialogue, end_silence_s=0.2)
    talk.orders = Orders(link, talk, dialogue, **orders)
    return cm, dialogue, talk


def companion(dialogue: DialogueManager, npc: str, name: str) -> None:
    dialogue.open(npc, name).intent = Intent("follow_hero", "PLAYER", "friendly")


def acts(fake: FakeGame) -> list[tuple[str, str]]:
    return [(m["npc"], m["action"]) for m in fake.received if m["type"] == "action"]


async def test_a_companion_stops_at_once_and_follows_again_later():
    fake = FakeGame()
    llm = FakeBackend("Стою.", delay=0)
    cm, dialogue, talk = await _desk(fake, llm, halt_s=0.1)
    try:
        await fake.roster(person(GORN, "Горн", 4), person(XARDAS, "Ксардас", 3, True))
        companion(dialogue, GORN, "Горн")
        await fake.send(type="player_text", text="Стой!", focus=XARDAS)
        await _until(lambda: (GORN, "stop") in acts(fake))
        assert talk.orders.standing[GORN].kind == "halt"
        assert talk.last[0].id == GORN and talk.last[0].why == "order"  # not Xardas he looks at
        await _until(lambda: (GORN, "follow") in acts(fake))  # HALT_S later, it goes on as before
        await _until(lambda: talk.held.get(GORN) == {"follow"})  # free of the order once it follows
    finally:
        await cm.__aexit__(None, None, None)

    assert acts(fake) == [(GORN, "stop"), (GORN, "stop"), (GORN, "follow")]
    assert llm.calls == []  # no model was asked
    assert GORN not in talk.orders.standing and talk.held[GORN] == {"follow"}
    notes = [m.content for m in dialogue.conversations[GORN].history]
    assert any("Герой приказал тебе: «Стой!» — остановиться. Ты подчинился." in n for n in notes)


async def test_an_order_to_someone_who_is_no_companion_is_words_to_him():
    fake = FakeGame()
    llm = FakeBackend("С чего бы мне идти за тобой?", delay=0)
    cm, dialogue, talk = await _desk(fake, llm)
    try:
        await fake.roster(person(LOBART, "Лобарт", 3, True, state="ZS_STAND"))
        await fake.send(type="player_text", text="За мной!", focus=LOBART)
        await _until(lambda: fake.said == ["С чего бы мне идти за тобой?"])
    finally:
        await cm.__aexit__(None, None, None)

    assert talk.orders.standing == {}
    assert (LOBART, "follow") not in acts(fake)  # he decides in words


async def test_attack_whom_the_hero_looks_at_then_back_to_him():
    fake = FakeGame()
    cm, dialogue, talk = await _desk(fake, FakeBackend("На кого?", delay=0))
    try:
        await fake.roster(person(GORN, "Горн", 4), person(BANDIT, "Бандит", 9, True))
        companion(dialogue, GORN, "Горн")
        await fake.send(type="player_text", text="В атаку!", focus=BANDIT)
        await _until(lambda: (GORN, "follow") in acts(fake))
    finally:
        await cm.__aexit__(None, None, None)

    fight = next(m for m in fake.received if m["type"] == "action" and m["action"] == "fight")
    assert fight["args"] == {"npc": BANDIT}
    assert acts(fake) == [(GORN, "fight"), (GORN, "stop"), (GORN, "follow")]  # the fight over: follows


async def test_attack_with_nobody_to_attack_is_a_question_for_the_model():
    fake = FakeGame()
    llm = FakeBackend("На кого?", delay=0)
    cm, dialogue, talk = await _desk(fake, llm)
    try:
        await fake.roster(person(GORN, "Горн", 4, True))
        companion(dialogue, GORN, "Горн")
        await fake.send(type="player_text", text="В атаку!", focus=GORN)
        await _until(lambda: fake.said == ["На кого?"])
    finally:
        await cm.__aexit__(None, None, None)

    assert (GORN, "fight") not in acts(fake)


async def test_a_name_picks_one_companion_out_of_two():
    fake = FakeGame()
    cm, dialogue, talk = await _desk(fake, FakeBackend("-", delay=0), wait_s=60)
    try:
        await fake.roster(person(GORN, "Горн", 4), person(LOBART, "Лобарт", 6))
        companion(dialogue, GORN, "Горн")
        companion(dialogue, LOBART, "Лобарт")
        await fake.send(type="player_text", text="Лобарт, жди здесь!")
        await _until(lambda: (LOBART, "stop") in acts(fake))
        assert set(talk.orders.standing) == {LOBART}
        # the weapon changes no standing order: it waits with the weapon out
        await fake.send(type="player_text", text="Все, к оружию!")
        await _until(
            lambda: (
                {a for a in acts(fake) if a[1] == "draw_weapon"}
                == {
                    (GORN, "draw_weapon"),
                    (LOBART, "draw_weapon"),
                }
            )
        )
        assert talk.orders.standing[LOBART].kind == "wait"
        await _until(lambda: GORN not in talk.held)  # held only to draw: free again once it is out
    finally:
        await cm.__aexit__(None, None, None)


async def test_dismissed_it_goes_back_to_its_business():
    fake = FakeGame()
    cm, dialogue, talk = await _desk(fake, FakeBackend("-", delay=0))
    try:
        await fake.roster(person(GORN, "Горн", 4))
        companion(dialogue, GORN, "Горн")
        await fake.send(type="player_text", text="За мной!")
        await _until(lambda: (GORN, "follow") in acts(fake))
        assert GORN in fake.attached
        await fake.send(type="player_text", text="Свободен.")
        await _until(lambda: GORN not in fake.attached)
        await asyncio.sleep(0.05)
    finally:
        await cm.__aexit__(None, None, None)

    assert dialogue.conversations[GORN].intent.goal == "keep_routine"
    assert GORN not in talk.held and talk.orders.standing == {}
    assert acts(fake) == [(GORN, "follow"), (GORN, "stop")]  # no second stop from the intent
