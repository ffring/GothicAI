import asyncio

import pytest

import gothic_ai.dialogue
from gothic_ai.dialogue import DialogueManager
from gothic_ai.group import QUIET_S, GroupTalk
from gothic_ai.llm import FakeBackend
from gothic_ai.persona import PersonaProvider
from gothic_ai.talk import TalkDesk
from tests.fake_game import FakeGame, connected
from tests.fakes import Clock
from tests.test_dialogue import HILDA, LOBART, _until
from tests.test_talk import person


@pytest.fixture(autouse=True)
def short_lines(monkeypatch):
    """Lines are heard for 0.3 s: the floor waits for them to be over."""
    monkeypatch.setattr(gothic_ai.dialogue, "line_duration", lambda text: 0.3)


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


MALETH, VINO = "BAU_954_MALETH", "BAU_952_VINO"


def calls_of(llm: FakeBackend, name: str) -> list[int]:
    """Which of the model's calls were for the npc of that name."""
    return [i for i, (instructions, _, _) in enumerate(llm.calls) if name in instructions]


def says_of(fake: FakeGame, npc: str) -> list[float]:
    return [t for m, t in fake.says if m["npc"] == npc]


async def test_one_voice_at_a_time(monkeypatch):
    # 2026-10-13: Brago shouted «Держи его!» over his mate's answer to the hero, not knowing what it was
    monkeypatch.setattr(gothic_ai.dialogue, "line_duration", lambda text: 0.4)
    fake = FakeGame()
    llm = FakeBackend("Ну, здравствуй.", delay=0, first=0.3)  # each thinks a while before it writes
    cm, dialogue, talk, group = await _desk(fake, llm)
    try:
        await fake.roster(person(LOBART, "Лобарт", 3, True), person(HILDA, "Хильда", 5, True))
        await fake.send(type="player_text", text="Лобарт, привет")
        await _until(lambda: len(llm.calls) == 1)
        dialogue.open(HILDA, "Хильда")  # the fast loop's urgent shout of hers, while he thinks
        dialogue.event_turn(HILDA, "[Событие: герой сказал рядом: «Лобарт, привет»]", overlay=True)
        await _until(lambda: len(says_of(fake, HILDA)) == 1 and not dialogue.speaking(HILDA), 5)
    finally:
        await cm.__aexit__(None, None, None)

    assert calls_of(llm, "Лобарт") == [0] and calls_of(llm, "Хильда") == [1]
    # she thought once his text was ready: his words are in what she knows
    assert "[Ты слышал, как Лобарт говорит герою: «Ну, здравствуй.»]" in situation(llm, 1)
    # ...and was heard after him
    assert says_of(fake, HILDA)[0] >= says_of(fake, LOBART)[-1] + 0.35


async def test_three_voices_to_a_phrase_of_the_hero():
    fake = FakeGame()
    llm = FakeBackend("Ага.", delay=0, first=0.1)
    cm, dialogue, talk, group = await _desk(fake, llm)
    try:
        far = person("BAU_960_FAR", "Дальний", 30)
        near = [
            person(n, nm, d, True)
            for n, nm, d in ((HILDA, "Хильда", 4), (MALETH, "Малет", 5), (VINO, "Вино", 6))
        ]
        await fake.roster(person(LOBART, "Лобарт", 3, True), *near, far)
        await fake.send(type="player_text", text="Лобарт, привет")
        await _until(lambda: len(llm.calls) == 1)
        for npc, name in ((HILDA, "Хильда"), (MALETH, "Малет"), (VINO, "Вино"), ("BAU_960_FAR", "Дальний")):
            dialogue.open(npc, name)
            dialogue.event_turn(npc, "[Событие: герой сказал рядом: «Лобарт, привет»]", overlay=True)
        # one away from the conversation is not on its floor: it does not wait
        await _until(lambda: calls_of(llm, "Дальний") != [], 2)
        hilda = calls_of(llm, "Хильда")
        assert not hilda or calls_of(llm, "Дальний")[0] < hilda[0]
        await _until(lambda: calls_of(llm, "Малет") != [] and not dialogue.speaking(MALETH), 5)
        await asyncio.sleep(0.3)
    finally:
        await cm.__aexit__(None, None, None)

    # the addressee and two more, in the order they asked; the fourth says nothing
    order = sorted((calls_of(llm, n)[0], n) for n in ("Лобарт", "Хильда", "Малет"))
    assert [n for _, n in order] == ["Лобарт", "Хильда", "Малет"]
    assert calls_of(llm, "Вино") == [] and says_of(fake, VINO) == []


async def test_the_heros_voice_cuts_the_floor():
    fake = FakeGame()
    llm = FakeBackend("Сейчас скажу.", delay=0, first=0.5)
    cm, dialogue, talk, group = await _desk(fake, llm)
    try:
        await fake.roster(person(LOBART, "Лобарт", 3, True), person(HILDA, "Хильда", 5, True))
        await fake.send(type="player_text", text="Лобарт, привет")
        await _until(lambda: len(llm.calls) == 1)
        dialogue.open(HILDA, "Хильда")
        dialogue.event_turn(HILDA, "[Событие: герой сказал рядом: «Лобарт, привет»]", overlay=True)
        await asyncio.sleep(0.1)
        assert group.waiting(HILDA)
        await fake.send(type="ptt_start")  # he speaks again: Lobart stops, Hilda says nothing
        await _until(lambda: not dialogue.speaking(LOBART) and not dialogue.speaking(HILDA))
        await fake.send(type="player_text", text="Хильда, а ты что скажешь?")
        await _until(lambda: len(calls_of(llm, "Хильда")) == 1 and not dialogue.speaking(HILDA), 5)
    finally:
        await cm.__aexit__(None, None, None)

    assert len(calls_of(llm, "Лобарт")) == 1  # cut while it thought
    hilda = dialogue.conversations[HILDA].history
    assert "Событие" not in " ".join(m.content for m in hilda)  # her queued shout never came
    assert "Хильда, а ты что скажешь?" in [m.content for m in hilda if m.role == "user"]


async def test_a_phrase_to_several_goes_to_the_conversation():
    fake = FakeGame()
    llm = FakeBackend("Чего надо?", delay=0)
    cm, dialogue, talk, group = await _desk(fake, llm)
    talk.lead = {"BDT_1014_BANDIT_L": 8, "BDT_1015_BANDIT_L": 6}.get  # Brago leads
    try:
        await fake.roster(
            person("BDT_1015_BANDIT_L", "Бандит", 3, True), person("BDT_1014_BANDIT_L", "Браго", 5, True)
        )
        await fake.send(type="player_text", text="Пацаны, здорово!")
        await _until(lambda: len(llm.calls) == 1 and not dialogue.speaking("BDT_1014_BANDIT_L"))
    finally:
        await cm.__aexit__(None, None, None)

    assert calls_of(llm, "Браго") == [0]  # the leader answers first, the nearer one is not asked
    assert "Последнюю фразу герой сказал всем вам, ты отвечаешь первым." in situation(llm, 0)
    assert "В разговоре с героем ещё: Бандит." in situation(llm, 0)
    assert notes(dialogue, "BDT_1015_BANDIT_L")[0] == (
        "[Герой сказал всем вам, и тебе тоже: «Пацаны, здорово!» (первым отвечает Браго)]"
    )
