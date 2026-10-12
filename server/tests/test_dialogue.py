import asyncio
from collections.abc import AsyncIterator

from gothic_ai import scenarios
from gothic_ai.dialogue import DialogueManager
from gothic_ai.llm import Done, LLMError, LLMEvent, Message, TextDelta, ToolCall
from gothic_ai.llm.fake import FakeBackend
from gothic_ai.persona import PersonaProvider
from tests.fake_game import FakeGame, connected

XARDAS = "NONE_100_XARDAS"


async def _until(predicate, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():  # noqa: ASYNC110 - polling a fake is fine in tests
            await asyncio.sleep(0.01)


async def test_player_phrases_get_replies_line_by_line():
    fake = FakeGame()
    llm = FakeBackend("Ты наконец вернулся из храма. Хорошо, теперь слушай меня внимательно.", delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Привет")
        await _until(lambda: fake.statuses[-2:] == ["thinking", "idle"])
        assert fake.said == ["Ты наконец вернулся из храма.", "Хорошо, теперь слушай меня внимательно."]

        await dialogue.say_as_player(XARDAS, "Где я?")
        await _until(lambda: len(fake.said) == 4 and fake.statuses[-1] == "idle")

    history = dialogue.conversations[XARDAS].history
    assert [m.role for m in history] == ["user", "assistant", "user", "assistant"]
    assert history[0].content == "Привет" and history[2].content == "Где я?"
    # the llm saw the whole conversation and the persona
    instructions, messages, tools = llm.calls[-1]
    assert "Ксардас" in instructions and messages[-1] == Message("user", "Где я?")
    assert messages[-2].role == "developer" and "Обстановка" in messages[-2].content
    assert {t.name for t in tools} == {
        "set_intent",
        "give_item",
        "recall",
        "end_conversation",
    }  # no body tools


async def test_new_player_text_interrupts_the_reply_in_flight():
    fake = FakeGame()
    llm = FakeBackend("Слишком долгий ответ, который никто не дослушает до конца. " * 5, delay=0.05)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
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
        dialogue = DialogueManager(link, FailingBackend(), PersonaProvider(None))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Привет")
        await _until(lambda: fake.statuses[-1:] == ["error"])

    assert dialogue.conversations[XARDAS].history == []
    assert fake.said == []


class StallingBackend:
    """Goes quiet on its first `stalls` calls (after `said`, if any), then answers. Counts the streams that
    were closed: a stalled one must not be left holding its connection."""

    name = "stalling"

    def __init__(self, reply: str, stalls: int = 1, said: str = "") -> None:
        self.reply, self.stalls, self.said = reply, stalls, said
        self.calls = self.closed = 0

    async def stream(
        self, *, instructions: str, messages: list[Message], tools=None, tool_choice=None
    ) -> AsyncIterator[LLMEvent]:
        self.calls += 1
        try:
            if self.calls <= self.stalls:
                if self.said:
                    yield TextDelta(self.said)
                await asyncio.sleep(3600)
            yield TextDelta(self.reply)
            yield Done(self.reply, (Message("assistant", self.reply),))
        finally:
            self.closed += 1


async def _quick(dialogue: DialogueManager) -> DialogueManager:
    dialogue.llm_first_s = dialogue.llm_idle_s = 0.2
    return dialogue


async def test_a_model_gone_quiet_is_asked_again():
    fake = FakeGame()
    llm = StallingBackend("Я здесь, говори.")
    async with connected(fake) as link:
        dialogue = await _quick(DialogueManager(link, llm, PersonaProvider(None)))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Ты тут?")
        await _until(lambda: fake.statuses[-2:] == ["thinking", "idle"])

    assert fake.said == ["Я здесь, говори."]
    assert (llm.calls, llm.closed) == (2, 2)  # the stalled stream was closed, not left hanging


async def test_a_model_gone_quiet_after_some_words_is_not_asked_again():
    fake = FakeGame()
    llm = StallingBackend("Это повтор.", said="Сейчас скажу. ")
    async with connected(fake) as link:
        dialogue = await _quick(DialogueManager(link, llm, PersonaProvider(None)))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Ну?")
        await _until(lambda: fake.statuses[-1:] == ["error"])

    assert llm.calls == 1  # asked again, it would say its first words twice
    assert fake.said == ["Сейчас скажу."]
    history = dialogue.conversations[XARDAS].history
    assert [(m.role, m.content) for m in history] == [("user", "Ну?"), ("assistant", "Сейчас скажу.")]


async def test_a_model_quiet_twice_gives_up():
    fake = FakeGame()
    llm = StallingBackend("Поздно.", stalls=2)
    async with connected(fake) as link:
        dialogue = await _quick(DialogueManager(link, llm, PersonaProvider(None)))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Эй")
        await _until(lambda: fake.statuses[-1:] == ["error"])

    assert (llm.calls, fake.said) == (2, [])  # one retry a turn


async def test_m1_scenario_with_fake_llm():
    fake = FakeGame()
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend(delay=0), PersonaProvider(None))
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
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
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
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        ended: list[tuple[str, int]] = []
        dialogue.on_end = lambda npc: ended.append((npc, len(fake.said)))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Ты старый дурак")
        assert not ended  # goodbye not spoken yet
        await _until(lambda: bool(ended), timeout=6)

    assert ended == [(XARDAS, 1)] and fake.said  # once the goodbye was said
    assert dialogue.conversations[XARDAS].history[-1].content == "[Ты закончил разговор]"


async def test_reply_text_streams_to_the_chat_before_its_lines():
    fake = FakeGame()
    llm = FakeBackend(
        "Ты наконец вернулся из храма. Хорошо, теперь слушай меня внимательно.", chunk=4, delay=0.02
    )
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
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


async def test_mood_tags_set_the_face_of_the_lines_and_are_not_said():
    fake = FakeGame()
    llm = FakeBackend(
        "[angry] Опять ты здесь? Чего тебе нужно? [friendly] Ладно, проходи, раз уж пришёл.", delay=0
    )
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Привет")
        await _until(lambda: fake.statuses[-2:] == ["thinking", "idle"])

    says = [m for m in fake.received if m["type"] == "say"]
    assert [(s["text"], s.get("mood")) for s in says] == [
        ("Опять ты здесь? Чего тебе нужно?", "angry"),
        ("Ладно, проходи, раз уж пришёл.", "friendly"),
    ]
    final = [m for m in fake.received if m["type"] == "chat_text"][-1]
    assert final["text"] == "Опять ты здесь? Чего тебе нужно? Ладно, проходи, раз уж пришёл."
    assert dialogue.conversations[XARDAS].history[-1].content == final["text"]
    instructions, _, _ = llm.calls[-1]
    assert "[angry]" in instructions  # the model is told how to tag


async def test_lines_without_a_tag_have_no_mood():
    fake = FakeGame()
    llm = FakeBackend("Ты наконец вернулся из храма. Хорошо, теперь слушай меня внимательно.", delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Привет")
        await _until(lambda: fake.statuses[-2:] == ["thinking", "idle"])
    assert all(m.get("mood") is None for m in fake.received if m["type"] == "say")


async def test_a_new_mood_starts_a_new_line():
    fake = FakeGame()
    llm = FakeBackend("[friendly] Ты вернулся. [angry] Времени мало, слушай внимательно.", delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Привет")
        await _until(lambda: fake.statuses[-2:] == ["thinking", "idle"])
    # "Ты вернулся." is short and would be merged with the next sentence, but its mood is another
    says = [(m["text"], m.get("mood")) for m in fake.received if m["type"] == "say"]
    assert says == [("Ты вернулся.", "friendly"), ("Времени мало, слушай внимательно.", "angry")]
