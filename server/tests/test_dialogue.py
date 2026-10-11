import asyncio
from collections.abc import AsyncIterator

from gothic_ai import scenarios
from gothic_ai.dialogue import GREETING_CUE, DialogueManager
from gothic_ai.llm import LLMError, LLMEvent, Message, ToolCall
from gothic_ai.llm.fake import FakeBackend
from gothic_ai.persona import PersonaProvider
from tests.fake_game import FakeGame, connected

XARDAS = "NONE_100_XARDAS"


async def _until(predicate, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():  # noqa: ASYNC110 - polling a fake is fine in tests
            await asyncio.sleep(0.01)


async def test_chat_open_greets_and_player_text_gets_a_reply():
    fake = FakeGame()
    llm = FakeBackend("Ты наконец вернулся из храма. Хорошо, теперь слушай меня внимательно.", delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        await fake.send(type="chat_open", npc=XARDAS, name="Ксардас")
        await _until(lambda: fake.statuses[-2:] == ["thinking", "idle"])
        assert fake.said == ["Ты наконец вернулся из храма.", "Хорошо, теперь слушай меня внимательно."]

        await fake.send(type="player_text", npc=XARDAS, text="Где я?")
        await _until(lambda: len(fake.said) == 4 and fake.statuses[-1] == "idle")

    history = dialogue.conversations[XARDAS].history
    assert [m.role for m in history] == ["user", "assistant", "user", "assistant"]
    assert history[0].content == GREETING_CUE and history[2].content == "Где я?"
    # the llm saw the whole conversation and the persona
    instructions, messages, tools = llm.calls[-1]
    assert "Ксардас" in instructions and messages[-1] == Message("user", "Где я?")
    assert messages[-2].role == "developer" and "Обстановка" in messages[-2].content
    assert {t.name for t in tools} == {"set_intent", "give_item", "end_conversation"}  # no body tools


async def test_new_player_text_interrupts_the_reply_in_flight():
    fake = FakeGame()
    llm = FakeBackend("Слишком долгий ответ, который никто не дослушает до конца. " * 5, delay=0.05)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False)
        dialogue.open(XARDAS, "Ксардас")
        first = dialogue.say_as_player(XARDAS, "Расскажи всё")
        await asyncio.sleep(0.2)
        second = dialogue.say_as_player(XARDAS, "Стоп")
        await second
        assert first.cancelled()

    roles = [m.role for m in dialogue.conversations[XARDAS].history]
    assert roles == ["user", "assistant", "user", "assistant"]  # partial reply kept, turns stay paired


class FailingBackend:
    name = "failing"

    async def stream(
        self, *, instructions: str, messages: list[Message], tools=None, tool_choice=None
    ) -> AsyncIterator[LLMEvent]:
        raise LLMError("plan limit reached")
        yield  # pragma: no cover


async def test_llm_error_is_reported_to_the_game_and_history_untouched():
    fake = FakeGame()
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FailingBackend(), PersonaProvider(None), greet=False)
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Привет")
        await _until(lambda: fake.statuses[-1:] == ["error"])

    assert dialogue.conversations[XARDAS].history == []
    assert fake.said == []


async def test_m1_scenario_with_fake_llm():
    fake = FakeGame()
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend(delay=0), PersonaProvider(None), greet=False)
        lines = await asyncio.wait_for(scenarios.m1_chat(link, dialogue), 10)

    assert len(lines) >= 2
    assert fake.attached == set()  # returned to routine


async def test_tool_call_runs_in_the_game_and_its_outcome_becomes_a_note():
    fake = FakeGame()
    llm = FakeBackend(
        "Возьми это зелье, оно тебе пригодится.",
        delay=0,
        tool_calls=[ToolCall("give_item", {"item": "ITPO_HEALTH_01", "count": 2})],
    )
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False)
        await link.attach(XARDAS)
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Дай что-нибудь")
        await _until(lambda: any(m.role == "developer" for m in dialogue.conversations[XARDAS].history))

    give = next(m for m in fake.received if m["type"] == "action")
    assert give["action"] == "give_item" and give["args"] == {"item": "ITPO_HEALTH_01", "count": 2}
    history = dialogue.conversations[XARDAS].history
    assert [m.role for m in history] == ["user", "assistant", "developer"]
    assert history[-1].content == "[Ты отдал герою: Эликсир лечения ×2]"


async def test_end_conversation_waits_for_the_goodbye_to_be_spoken():
    fake = FakeGame()
    llm = FakeBackend(
        "Довольно. Ступай, мне нужно работать.", delay=0, tool_calls=[ToolCall("end_conversation", {})]
    )
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False)
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Ты старый дурак")
        assert not any(m["type"] == "chat_end" for m in fake.received)  # goodbye not spoken yet
        await _until(lambda: any(m["type"] == "chat_end" for m in fake.received), timeout=6)

    kinds = [m["type"] for m in fake.received]
    assert kinds.index("say") < kinds.index("chat_end")
    assert dialogue.conversations[XARDAS].history[-1].content == "[Ты закончил разговор]"


async def test_reply_text_streams_to_the_chat_before_its_lines():
    fake = FakeGame()
    llm = FakeBackend(
        "Ты наконец вернулся из храма. Хорошо, теперь слушай меня внимательно.", chunk=4, delay=0.02
    )
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False)
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Где я?")
        await _until(lambda: any(m["type"] == "chat_text" and m["final"] for m in fake.received))

    texts = [m for m in fake.received if m["type"] == "chat_text"]
    says = [m for m in fake.received if m["type"] == "say"]
    assert len(texts) > 2 and texts[-1]["text"] == llm.reply and texts[-1]["final"]
    assert all(len(a["text"]) <= len(b["text"]) for a, b in zip(texts, texts[1:], strict=False))
    assert {m["reply"] for m in texts} == {s["reply"] for s in says}
    # the first words were on screen before the first line was complete
    first_text = fake.received.index(texts[0])
    assert first_text < fake.received.index(says[0]) and len(texts[0]["text"]) < len(says[0]["text"])
