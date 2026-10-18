import pytest

from gothic_ai.dialogue import DialogueManager, follow_agreement
from gothic_ai.executor import Intent
from gothic_ai.journal import Journal
from gothic_ai.llm import FakeBackend, ToolCall
from gothic_ai.persona import PersonaProvider
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import _until

CAVALORN = "BAU_4300_ADDON_CAVALORN"


@pytest.mark.parametrize(
    ("heard", "reply"),
    [
        (
            "Пойдем, бодитов, пиздеть.",
            "Вот это по делу. Вниз по дороге прячутся бандиты, которые меня ограбили — "
            "пойдём разберёмся с ними вместе. Только держись рядом и прикрывай мне спину.",
        ),
        ("Ну ты идешь, нет?", "Иду, прикроешь мне спину."),
        ("Давай за мной.", "Ладно, я за тобой."),
        ("Кавалорн, давай, иди за мной.", "Веди, я за тобой. Только не лезь вперёд без оглядки."),
    ],
)
async def test_a_spoken_follow_agreement_repairs_the_missing_intent(heard, reply):
    fake = FakeGame()
    llm = FakeBackend(reply, delay=0)
    entries = []
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), journal=Journal(entries.append))
        await link.attach(CAVALORN)
        conv = dialogue.open(CAVALORN, "Кавалорн")
        conv.intent = Intent(attitude="friendly", agenda="Help with the bandits")
        await dialogue.say_as_player(CAVALORN, heard)
        await _until(lambda: any(m.get("action") == "follow" for m in fake.received))

    assert conv.intent == Intent("follow_hero", "PLAYER", "friendly", "Help with the bandits")
    note = next(m for m in conv.history if "Ты согласился и идёшь за героем" in m.content)
    assert heard in note.content
    assert any(m.role == "assistant" and m.content == reply for m in conv.history)
    calls = [e for e in entries if e.get("action") == "set_intent"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("heard", "reply"),
    [
        ("Что будем делать с бандитами?", "Пойдём, разберёмся с ними вместе."),
        ("Расскажи про своё дело.", "Пойдём разберёмся с ними вместе."),
        ("Куда ты идешь?", "Иду, прикроешь мне спину."),
        ("Он сказал: пойдем к бандитам.", "Пошли."),
        ("Не пойдем туда.", "Ладно, пойдем."),
        ("Пойдем к бандитам.", "Не пойду, я остаюсь здесь."),
        ("Пошли к пещере.", "Иди, я останусь у костра."),
        ("Пойдем к бандитам.", "Пойдем, если заплатишь мне сто золотых."),
        ("Пойдем к бандитам.", "Сначала принеси воды, тогда пойдем."),
        ("Пойдем к бандитам.", "Я бы пошел, но позже."),
        ("Пойдем к бандитам.", "Не думаю, что пойдем туда вместе."),
        ("Пойдем к бандитам.", "Может, пойдем, а может, нет."),
        ("Пойдем к бандитам.", "Не сейчас. Пойдем завтра."),
        ("Пойдем к бандитам.", "Пойдем без меня, я занят."),
        ("Пойдем к бандитам.", "Я думаю, что мы пойдем туда вместе."),
        ("Пойдем к бандитам.", "Ты хочешь, чтобы я за тобой шёл?"),
        ("Пойдем к бандитам.", "Пойдем?"),
        ("Ну ты идешь, нет?", "Иду к Лобарту. Ты разбирайся с бандитами сам."),
        ("Пойдем к бандитам.", "Ты сказал «пойдем», а я ничего не обещал."),
        ("Пойдем к бандитам.", "Да, давай поговорим об этом."),
        ("Пойдём к бандитам.", "Пошли за мной, я знаю дорогу."),
        ("Пойдём к бандитам.", "Идём, я поведу."),
        ("Ну ты идёшь, нет?", "Иду, но без тебя."),
        ("Пошли слухи, что бандиты вернулись.", "Пойдём проверим."),
        ("Пойдём ли мы завтра к бандитам?", "Иду, прикроешь мне спину."),
    ],
)
async def test_other_words_do_not_make_a_follow_agreement(heard, reply):
    assert not follow_agreement(heard, reply, "Кавалорн")
    fake = FakeGame()
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend(reply, delay=0), PersonaProvider(None))
        conv = dialogue.open(CAVALORN, "Кавалорн")
        await dialogue.say_as_player(CAVALORN, heard)

    assert conv.intent.goal == "none"
    assert not any(m.get("action") == "follow" for m in fake.received)


async def test_the_models_follow_intent_is_not_applied_twice():
    fake = FakeGame()
    llm = FakeBackend(
        "Иду, прикроешь мне спину.",
        delay=0,
        tool_calls=[ToolCall("set_intent", {"goal": "follow_hero", "target": "PLAYER"})],
    )
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        await link.attach(CAVALORN)
        conv = dialogue.open(CAVALORN, "Кавалорн")
        await dialogue.say_as_player(CAVALORN, "Ну ты идешь, нет?")
        await _until(lambda: any(m.get("action") == "follow" for m in fake.received))

    assert conv.intent.goal == "follow_hero"
    assert len([m for m in fake.received if m.get("action") == "follow"]) == 1
    assert not any("Ты согласился и идёшь за героем" in m.content for m in conv.history)


async def test_an_event_turn_cannot_make_a_follow_agreement():
    fake = FakeGame()
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend("Пошли.", delay=0), PersonaProvider(None))
        conv = dialogue.open(CAVALORN, "Кавалорн")
        await dialogue.event_turn(CAVALORN, "Пойдём к бандитам.")

    assert conv.intent.goal == "none"
    assert not any(m.get("action") == "follow" for m in fake.received)
