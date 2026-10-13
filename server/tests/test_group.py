from gothic_ai.dialogue import DialogueManager
from gothic_ai.group import QUIET_S, GroupTalk
from gothic_ai.llm import FakeBackend
from gothic_ai.persona import PersonaProvider
from gothic_ai.talk import TalkDesk
from tests.fake_game import FakeGame, connected
from tests.fakes import Clock
from tests.test_dialogue import HILDA, LOBART, _until
from tests.test_talk import person


async def _desk(fake: FakeGame, llm, clock=None):
    cm = connected(fake)
    link = await cm.__aenter__()
    dialogue = DialogueManager(link, llm, PersonaProvider(None))
    talk = TalkDesk(link, dialogue, end_silence_s=30)
    group = GroupTalk(link, dialogue, talk, **({"clock": clock} if clock else {}))
    dialogue.extensions.append(group)
    return cm, dialogue, talk, group


def notes(dialogue: DialogueManager, npc: str) -> list[str]:
    conv = dialogue.conversations.get(npc)
    return [m.content for m in conv.history if m.role == "developer"] if conv else []


def situation(llm: FakeBackend, call: int) -> str:
    return "\n".join(m.content for m in llm.calls[call][1] if m.role == "developer")


async def test_what_an_npc_says_is_heard_by_whoever_stands_by():
    fake = FakeGame()
    fake.nearby_of[LOBART] = [{"npc": HILDA, "name": "Хильда", "distance": 400, "guild": 0}]
    cm, dialogue, talk, group = await _desk(fake, FakeBackend("Чего тебе?", delay=0))
    try:
        await fake.roster(person(LOBART, "Лобарт", 3, True), person(HILDA, "Хильда", 6))
        await fake.send(type="player_text", text="Привет", focus=LOBART)
        await _until(lambda: len(notes(dialogue, HILDA)) == 2)
    finally:
        await cm.__aexit__(None, None, None)

    assert notes(dialogue, HILDA) == [
        "[Ты слышал, как герой сказал собеседнику (Лобарт): «Привет»]",
        "[Ты слышал, как Лобарт говорит герою: «Чего тебе?»]",  # not deaf to Lobart any more
    ]
    assert "говорит герою" not in " ".join(notes(dialogue, LOBART))  # not to itself


async def test_who_is_in_the_conversation_and_who_drops_out():
    fake = FakeGame()
    llm = FakeBackend("Ну?", delay=0)
    cm, dialogue, talk, group = await _desk(fake, llm)
    try:
        await fake.roster(person(LOBART, "Лобарт", 3, True), person(HILDA, "Хильда", 5, True))
        await fake.send(type="player_text", text="Лобарт, привет")
        await _until(lambda: len(llm.calls) == 1 and not dialogue.speaking(LOBART))
        await fake.send(type="player_text", text="Хильда, а ты что скажешь?")
        await _until(lambda: len(llm.calls) == 2 and not dialogue.speaking(HILDA))
        assert set(group.participants()) == {LOBART, HILDA}
        # Lobart walks off: out of it
        await fake.roster(person(LOBART, "Лобарт", 25), person(HILDA, "Хильда", 5, True))
        await fake.send(type="player_text", text="Хильда, а где он?")
        await _until(lambda: len(llm.calls) == 3)
    finally:
        await cm.__aexit__(None, None, None)

    assert "В разговоре" not in situation(llm, 0)  # alone with the hero: nothing to tell
    assert "В разговоре с героем ещё: Лобарт." in situation(llm, 1)
    assert "В разговоре" not in situation(llm, 2)
    assert set(group.participants()) == {HILDA}


async def test_the_conversation_is_over_when_nobody_speaks_in_it():
    fake = FakeGame()
    clock = Clock()
    cm, dialogue, talk, group = await _desk(fake, FakeBackend("Ну?", delay=0), clock)
    try:
        await fake.roster(person(LOBART, "Лобарт", 3, True), person(HILDA, "Хильда", 5, True))
        await fake.send(type="player_text", text="Лобарт, привет")
        await _until(lambda: LOBART in group.participants() and not dialogue.speaking(LOBART))
        group.on_said(HILDA, "Хильда", "Опять он.", "incident")
        assert set(group.participants()) == {LOBART, HILDA}  # she spoke in it near the hero
        assert group.situation(HILDA) == "В разговоре с героем ещё: Лобарт."
        dialogue.speaking = lambda npc: npc == LOBART  # he answers the hero
        assert group.situation(HILDA) == "В разговоре с героем ещё: Лобарт. Сейчас говорит: Лобарт."
        clock.t += QUIET_S + 1
        assert group.participants() == {}
    finally:
        await cm.__aexit__(None, None, None)
