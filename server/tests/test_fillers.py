import asyncio
import random

from gothic_ai.brain.reflex import Reflexes
from gothic_ai.budget import Budget
from gothic_ai.dialogue import FIRST_REACTION_S, DialogueManager, FirstReaction
from gothic_ai.fillers import KINDS, Fillers, Sound, strip_said, trim
from gothic_ai.incidents import IncidentDesk
from gothic_ai.llm.fake import FakeBackend
from gothic_ai.persona import PersonaProvider
from gothic_ai.scenarios import FixedDecider
from gothic_ai.trace import TraceWriter, read
from tests.fake_game import FakeGame, connected

XARDAS = "NONE_100_XARDAS"


class Always(random.Random):
    """Every chance taken, the first option picked."""

    def random(self) -> float:
        return 0.0

    def choice(self, seq):
        return seq[0]


async def _until(predicate, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():  # noqa: ASYNC110 - polling a fake is fine in tests
            await asyncio.sleep(0.01)


def _reacts(sound: str | None, delay: float = 0.0):
    """A stand-in for the behavioral model's first reaction (incidents.py)."""

    async def first_reaction(npc: str, text: str, voice: bool) -> FirstReaction:
        await asyncio.sleep(delay)
        return FirstReaction(sound)

    return first_reaction


def test_the_sound_is_of_the_chosen_kind_but_not_to_a_greeting():
    f = Fillers(None, rng=Always())
    assert f.pick(XARDAS, "Орки напали на город!", "surprise") == Sound(
        "surprise", KINDS["surprise"].lines[0]
    )
    assert f.pick(XARDAS, "Ты старый дурак.", "displeasure").mood == "angry"
    assert f.pick(XARDAS, "Как победить драконов?", "none") is None
    # no choice (the behavioral model unsure or away): "Хм..." to a question, nothing to anything else
    assert f.pick(XARDAS, "Где мой меч?", None) == Sound("think", "Хм...")
    assert f.pick(XARDAS, "Ты старый дурак.", None) is None
    # the answer to a greeting or a word comes as quick as a sound would, whatever the model says
    assert f.pick(XARDAS, "Привет, Ксардас!", "think") is None
    assert f.pick(XARDAS, "Пока.", "ack") is None


def test_no_sound_promises_a_wait():
    lines = [t for k in KINDS.values() for t in k.lines]
    assert not any(w in t for t in lines for w in ("Сейчас", "Погоди", "Дай подумать"))


def test_not_every_time_and_never_the_same_twice():
    now = [0.0]
    f = Fillers(None, rng=Always(), clock=lambda: now[0])
    first = f.pick(XARDAS, "Где мой меч?", "think")
    assert first == Sound("think", KINDS["think"].lines[0])
    f.said(XARDAS, first)
    now[0] += 1.0
    assert f.pick(XARDAS, "А где щит?", "think") is None  # too soon after the last one
    now[0] += 10.0
    second = f.pick(XARDAS, "А где щит?", "think")
    assert second is not None and second != first


def test_the_answer_does_not_say_it_again():
    assert strip_said("Хм... Ты прав.", "Хм...") == "Ты прав."
    assert strip_said("Что?! Орки напали?", "Что?!") == "Орки напали?"
    assert strip_said("Что? Орки?", "Что?!") == "Орки?"
    assert strip_said("Ну-ну. Так уж и голыми?", "Ну-ну...") == "Так уж и голыми?"
    assert strip_said("Хм, ну ладно.", "Тьфу!") == "Ну ладно."  # an interjection after any sound
    assert strip_said("М-м... Пожалуй.", "Хм...") == "Пожалуй."
    assert strip_said("Так, слушай.", "Так...") == "Слушай."
    assert strip_said("Так вот оно что.", "Так...") == "Так вот оно что."  # not an interjection here
    assert strip_said("Что ты сказал?", "Что?!") == "Что ты сказал?"
    assert strip_said("Хмурый сегодня день.", "Хм...") == "Хмурый сегодня день."
    assert strip_said("Хм.", "Хм.") == ""


def test_voiced_sounds_are_read_from_their_files(tmp_path):
    f = Fillers("ws://unused", cache=tmp_path)
    hm = Sound("think", "Хм...")
    assert f.audio(XARDAS, "old wise man", hm) is None
    path = f.path(XARDAS, "old wise man", hm)
    path.write_bytes(b"RIFF")
    assert f.audio(XARDAS, "old wise man", hm) == b"RIFF"
    assert f.path(XARDAS, "another voice", hm) != path
    # the same words in another tone are another file: "Так..." of a question and of news
    assert f.path(XARDAS, "old wise man", Sound("ack", "Так...")) != f.path(
        XARDAS, "old wise man", Sound("think", "Так...")
    )


def _wav(pcm: list[int], rate: int = 1000) -> bytes:
    import io
    import struct
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(pcm)}h", *pcm))
    return buf.getvalue()


def _seconds(wav: bytes) -> float:
    import io
    import wave

    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / w.getframerate()


def test_the_silence_around_a_sound_is_cut():
    sound = [8000, -8000] * 100  # 0.2 s at 1000 Hz
    assert abs(_seconds(trim(_wav([0] * 300 + sound + [0] * 500))) - (0.2 + 2 * 0.06)) < 0.015
    # a breath after a pause is not part of it: "Хм..." would hold the answer up for a second more
    breath = [600, -600] * 50
    assert abs(_seconds(trim(_wav(sound + [0] * 600 + breath + [0] * 100))) - (0.2 + 0.06)) < 0.015
    # a short pause between two words is
    assert abs(_seconds(trim(_wav(sound + [0] * 150 + sound))) - 0.55) < 0.015


async def test_the_answer_goes_on_from_the_sound():
    fake = FakeGame()
    llm = FakeBackend("Что?! Орки? Где они?", delay=0.02)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), fillers=Fillers(None, rng=Always()))
        dialogue.first_reaction = _reacts("surprise")
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Орки напали на город!")
        await _until(lambda: fake.statuses[-1:] == ["idle"])
    said = KINDS["surprise"].lines[0]
    assert fake.fillers == [said]
    assert fake.said[0] == said
    assert said not in " ".join(fake.said[1:])  # not "Что?! Что?! Орки?"
    # the model was told what the npc said already, before it wrote a word
    situation = llm.calls[0][1][-2]
    assert situation.role == "developer" and f"Ты уже сказал вслух: «{said}»" in situation.content
    # its memory has the answer as the model wrote it
    assert dialogue.conversations[XARDAS].history[-1].content == "Что?! Орки? Где они?"


async def test_without_the_behavioral_model_a_question_gets_hm():
    fake = FakeGame()
    async with connected(fake) as link:
        llm = FakeBackend("Меч у тебя под ногами.", delay=0)
        dialogue = DialogueManager(link, llm, PersonaProvider(None), fillers=Fillers(None, rng=Always()))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Где мой меч?")
        await _until(lambda: fake.statuses[-1:] == ["idle"])
    assert fake.fillers == ["Хм..."]


async def test_a_slow_behavioral_model_is_not_waited_for():
    fake = FakeGame()
    async with connected(fake) as link:
        llm = FakeBackend("Следи за языком.", delay=0)
        dialogue = DialogueManager(link, llm, PersonaProvider(None), fillers=Fillers(None, rng=Always()))
        dialogue.first_reaction = _reacts("displeasure", delay=2.0)
        dialogue.open(XARDAS, "Ксардас")
        started = asyncio.get_running_loop().time()
        await dialogue.say_as_player(XARDAS, "Ты старый дурак.")
        took = asyncio.get_running_loop().time() - started
    assert fake.fillers == []  # no choice in time, and a statement gets nothing without one
    assert took < FIRST_REACTION_S + 0.5
    assert "Ты уже сказал вслух" not in llm.calls[0][1][-2].content


async def test_the_sound_and_the_gesture_come_from_one_question(tmp_path):
    fake = FakeGame()
    async with connected(fake) as link:
        trace = TraceWriter(tmp_path)
        llm = FakeBackend("Ну-ну. Так уж и голыми?", delay=0.05)
        dialogue = DialogueManager(
            link, llm, PersonaProvider(None), trace=trace, fillers=Fillers(None, rng=Always())
        )
        decider = FixedDecider({"voice": "doubt", "gesture": "shake_head"})
        desk = IncidentDesk(link, dialogue, Reflexes(decider), Budget(), trace)
        await link.attach(XARDAS)
        dialogue.open(XARDAS, "Ксардас")
        await _until(lambda: XARDAS in desk.attached)
        await dialogue.say_as_player(XARDAS, "Я убил дракона голыми руками.")
        await _until(lambda: any(m["type"] == "action" for m in fake.received))
    said = KINDS["doubt"].lines[0]
    assert fake.fillers == [said]
    kinds = [
        (m["type"], m.get("text") or m.get("args")) for m in fake.received if m["type"] in ("say", "action")
    ]
    # the sound first: the game's queue holds a line until a gesture before it is over
    assert kinds[:2] == [("say", said), ("action", {"name": "T_NO"})]
    reflexes = [r for r in read(trace.path()) if r["type"] == "reflex"]
    assert {r["question"]: r["choice"] for r in reflexes} == {"voice": "doubt", "gesture": "shake_head"}
