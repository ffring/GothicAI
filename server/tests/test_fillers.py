import asyncio
import random

from gothic_ai.dialogue import DialogueManager
from gothic_ai.fillers import ACK, THINK, Fillers, kind_of, strip_interjection, trim
from gothic_ai.llm.fake import FakeBackend
from gothic_ai.persona import PersonaProvider
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


def test_what_fits_the_phrase():
    assert kind_of("Где мой меч?") == "think"
    assert kind_of("Кто ты такой") == "think"
    assert kind_of("Скажи, как пройти к морю") == "think"
    assert kind_of("Я убил волков у реки") == "ack"
    assert kind_of("Привет, Ксардас") is None  # the answer is a greeting itself
    assert kind_of("Да") is None


def test_the_answer_does_not_say_hm_again():
    assert strip_interjection("Хм... Ты прав.") == "Ты прав."
    assert strip_interjection("Хм, ну ладно.") == "Ну ладно."
    assert strip_interjection("Так, слушай.") == "Слушай."
    assert strip_interjection("Так вот оно что.") == "Так вот оно что."  # not an interjection here
    assert strip_interjection("Хмурый сегодня день.") == "Хмурый сегодня день."
    assert strip_interjection("Хм.") == ""


def test_not_every_time_and_never_the_same_twice():
    now = [0.0]
    f = Fillers(None, rng=Always(), clock=lambda: now[0])
    first = f.pick(XARDAS, "Где мой меч?")
    assert first == THINK[0]
    f.said(XARDAS, first)
    now[0] += 1.0
    assert f.pick(XARDAS, "А где щит?") is None  # too soon after the last one
    now[0] += 10.0
    second = f.pick(XARDAS, "А где щит?")
    assert second is not None and second != first
    assert f.pick(XARDAS, "Привет") is None
    assert Fillers(None, rng=Always()).pick(XARDAS, "Я убил волков у реки") == ACK[0]


def test_voiced_fillers_are_read_from_their_files(tmp_path):
    f = Fillers("ws://unused", cache=tmp_path)
    assert f.audio(XARDAS, "old wise man", "Хм...") is None
    path = f.path(XARDAS, "old wise man", "Хм...")
    path.write_bytes(b"RIFF")
    assert f.audio(XARDAS, "old wise man", "Хм...") == b"RIFF"
    assert f.path(XARDAS, "another voice", "Хм...") != path


async def test_a_slow_model_gets_a_filler_before_its_answer():
    fake = FakeGame()
    llm = FakeBackend("Хм... Меч лежит у тебя под ногами.", first=1.0, delay=0.02)  # it thinks a second
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), fillers=Fillers(None, rng=Always()))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Где мой меч?")
        await _until(lambda: fake.statuses[-1:] == ["idle"])
    assert fake.fillers == [THINK[0]]
    assert fake.said[0] == THINK[0]
    assert fake.said[1:] == ["Меч лежит у тебя под ногами."]  # not "Хм..." again
    # the filler is not the model's: its memory has the answer as the model wrote it
    assert dialogue.conversations[XARDAS].history[-1].content == "Хм... Меч лежит у тебя под ногами."


def test_the_silence_around_a_filler_is_cut():
    import io
    import struct
    import wave

    rate = 1000
    pcm = [0] * 300 + [8000, -8000] * 100 + [0] * 500  # 0.3 s quiet, 0.2 s of sound, 0.5 s quiet
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(pcm)}h", *pcm))
    with wave.open(io.BytesIO(trim(buf.getvalue()))) as w:
        assert abs(w.getnframes() / rate - (0.2 + 2 * 0.06)) < 0.01


async def test_a_model_already_writing_gets_none():
    fake = FakeGame()
    async with connected(fake) as link:
        fillers = Fillers(None, rng=Always())
        llm = FakeBackend("Меч лежит у тебя под ногами, и давно.", delay=0.2, chunk=4)  # writes, slowly
        dialogue = DialogueManager(link, llm, PersonaProvider(None), fillers=fillers)
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Где мой меч?")
        await _until(lambda: fake.statuses[-1:] == ["idle"], timeout=5)
    assert fake.fillers == []


async def test_a_quick_model_or_a_greeting_gets_none():
    fake = FakeGame()
    async with connected(fake) as link:
        dialogue = DialogueManager(
            link,
            FakeBackend("Меч у тебя.", delay=0),
            PersonaProvider(None),
            fillers=Fillers(None, rng=Always()),
        )
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Где мой меч?")
        await _until(lambda: fake.statuses[-1:] == ["idle"])
        dialogue.llm = FakeBackend("Привет и тебе, путник.", first=1.0)
        await dialogue.say_as_player(XARDAS, "Привет, Ксардас")
        await _until(lambda: len(fake.said) == 2 and fake.statuses[-1:] == ["idle"])
    assert fake.fillers == []
