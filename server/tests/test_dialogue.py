import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from websockets.asyncio.server import serve

from gothic_ai import dialogue as dialogue_module
from gothic_ai.dialogue import QUIET_STYLE, SCENE_GAP_S, TTS_PAUSE, DialogueManager, FirstReaction
from gothic_ai.journal import Journal
from gothic_ai.llm import Done, LLMError, LLMEvent, Message, TextDelta, Tool, ToolCall
from gothic_ai.llm.fake import FakeBackend, FakeStep
from gothic_ai.persona import MOOD_DELIVERY, PersonaProvider
from gothic_ai.scenarios import basics as scenarios
from gothic_ai.tts import Speech, TtsClient, TtsError
from tests.fake_game import FakeGame, connected

XARDAS = "NONE_100_XARDAS"
LOBART = "BAU_950_LOBART"
HILDA = "BAU_951_HILDA"


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


async def test_cavalorns_fragmented_tool_text_is_logged_without_being_said_or_remembered(
    caplog, monkeypatch
):
    npc = "BAU_4300_ADDON_CAVALORN"
    spoken = [
        "Вот это разговор.",
        "Внизу по дороге их пещера — там и вернём моё добро.",
        "Прикроешь мне спину, только не лезь под удар.",
    ]
    clean = " ".join(spoken)
    leak = "protoc function give_quest?"
    llm = FakeBackend(clean + "\n " + leak + " \n", chunk=3, delay=0)
    fake = FakeGame()
    entries: list[dict] = []
    monkeypatch.setattr(dialogue_module, "CHAT_TEXT_EVERY", 0)
    async with scene_tts(per_char=0.0001) as (url, requests), connected(fake) as link:
        dialogue = DialogueManager(
            link, llm, PersonaProvider(None), tts=TtsClient(url), journal=Journal(entries.append)
        )
        conv = dialogue.open(npc, "Кавалорн")
        await dialogue.say_as_player(npc, "Помочь тебе?")
        await _until(lambda: len(fake.say_ends) == 3 and fake.statuses[-1:] == ["idle"])

    assert fake.said == spoken
    assert [r["text"] for r in requests] == spoken
    texts = [m["text"] for m in fake.received if m["type"] == "chat_text"]
    assert texts[-1] == clean
    assert all(clean.startswith(text.strip()) for text in texts)  # no partial leaked prefix was shown
    assert conv.history[-1] == Message("assistant", clean)
    assert dialogue.memory.load("test-branch", npc).entries[-1].content == clean
    assert [entry["text"] for entry in entries if entry["kind"] == "npc"] == [clean]
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.name == "gothic_ai.dialogue" and r.levelname == "WARNING"
    ]
    assert any(leak in warning and (npc in warning or "Кавалорн" in warning) for warning in warnings)


class HiddenQuestTool:
    all_tools = (Tool("give_quest", "Assign a quest."),)

    async def prepare(self, npc: str) -> None:
        pass

    def situation(self, npc: str) -> None:
        return None

    def tools(self, npc: str) -> list[Tool]:
        return []


@pytest.mark.parametrize(
    "leak", ["give_item?", "set_intent?", "give_quest?", "turn_signal?", "functions.give_quest({...})"]
)
async def test_tool_names_are_filtered_from_base_extension_and_turn_tools(leak, caplog):
    fake = FakeGame()
    llm = FakeBackend(leak, chunk=2, delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), tools=False)
        dialogue.extensions.append(HiddenQuestTool())
        conv = dialogue.open(XARDAS, "Ксардас")
        await dialogue._turn(
            conv, Message("user", "Что скажешь?"), "player", tools=[Tool("turn_signal", "Signal.")]
        )
        await _until(lambda: fake.statuses[-1:] == ["idle"])

    assert fake.said == []
    assert not [m for m in fake.received if m["type"] == "chat_text" and m["text"]]
    assert conv.history == [Message("user", "Что скажешь?")]
    assert any(leak in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


@pytest.mark.parametrize(
    ("reply", "clean", "leak"),
    [
        (
            "Ладно, пошли. give_item(item=ITMW_MACE)? После боя поговорим.",
            "Ладно, пошли. После боя поговорим.",
            "give_item(item=ITMW_MACE)?",
        ),
        ("Жди здесь. set_intent", "Жди здесь.", "set_intent"),
        (
            "Держи. give_item(item=ITMI_GOLD)\nЛадно, пошли.",
            "Держи. Ладно, пошли.",
            "give_item(item=ITMI_GOLD)",
        ),
        (
            "Держи. give_item(item=ITMI_GOLD)\n\nЛадно, пошли.",
            "Держи. Ладно, пошли.",
            "give_item(item=ITMI_GOLD)",
        ),
        (
            "Вот это разговор\nprotoc function give_quest?",
            "Вот это разговор",
            "protoc function give_quest?",
        ),
        (
            "Держи. functions.give_quest({...})\nЛадно, пошли.",
            "Держи. Ладно, пошли.",
            "functions.give_quest({...})",
        ),
    ],
)
async def test_only_the_service_sentence_is_removed_from_a_merged_line_or_tail(
    reply, clean, leak, caplog
):
    fake = FakeGame()
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend(reply, chunk=3, delay=0), PersonaProvider(None))
        conv = dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Ладно")
        await _until(lambda: fake.statuses[-1:] == ["idle"])

    assert " ".join(fake.said) == clean
    assert conv.history[-1] == Message("assistant", clean)
    assert [m["text"] for m in fake.received if m["type"] == "chat_text"][-1] == clean
    assert any(
        leak in r.getMessage() and XARDAS in r.getMessage() and r.levelname == "WARNING"
        for r in caplog.records
    )


async def test_ordinary_speech_is_not_filtered(caplog):
    reply = (
        "Дай мне функцию подумать. Это задание как раз по тебе. Отдам тебе булаву после боя. "
        "Xardas знает больше. Ладно, пошли."
    )
    fake = FakeGame()
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend(reply, chunk=3, delay=0), PersonaProvider(None))
        conv = dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Что скажешь?")
        await _until(lambda: fake.statuses[-1:] == ["idle"])

    assert " ".join(fake.said) == reply
    assert conv.history[-1] == Message("assistant", reply)
    assert not [r for r in caplog.records if r.name == "gothic_ai.dialogue" and r.levelname == "WARNING"]


async def test_new_player_text_interrupts_the_reply_in_flight():
    fake = FakeGame()
    llm = FakeBackend("Слишком долгий ответ, который никто не дослушает до конца. " * 5, delay=0.05)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        dialogue.open(XARDAS, "Ксардас")
        first = dialogue.say_as_player(XARDAS, "Расскажи всё")
        await _until(lambda: any(m["type"] == "chat_text" for m in fake.received))
        assert not first.done()
        second = dialogue.say_as_player(XARDAS, "Стоп")
        await second
        assert first.cancelled()

    roles = [m.role for m in dialogue.conversations[XARDAS].history]
    assert roles == ["user", "assistant", "user", "assistant"]  # partial reply kept, turns stay paired


class FailingBackend:
    name = "failing"

    async def stream(
        self, *, instructions: str, messages: list[Message], tools=None, tool_choice=None, **_
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
        self, *, instructions: str, messages: list[Message], tools=None, tool_choice=None, **_
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


class CountedBackend(FakeBackend):
    """Counts model chunks before dialogue consumes them, independently of its line buffering."""

    chunks = 0

    async def stream(self, **kwargs) -> AsyncIterator[LLMEvent]:
        async for event in super().stream(**kwargs):
            if isinstance(event, TextDelta):
                self.chunks += 1
            yield event


@pytest.mark.parametrize(
    ("reply", "expected", "sentence_end_chunk"),
    [
        (
            "Ты вернулся из храма, и это хорошо. Теперь слушай меня внимательно.",
            [
                ("Ты вернулся из храма,", 8),
                ("и это хорошо. Теперь слушай меня внимательно.", 23),
            ],
            12,
        ),
        (
            "Когда ты пойдёшь в город через северные ворота мимо стражи найди там Лестера.",
            [
                ("Когда ты пойдёшь в город через северные ворота мимо стражи", 20),
                ("найди там Лестера.", 26),
            ],
            26,
        ),
    ],
    ids=["first-comma", "first-words-max"],
)
async def test_ordinary_speech_reaches_say_on_the_same_chunks_as_main(
    reply, expected, sentence_end_chunk, monkeypatch
):
    # Constants were measured through origin/main's DialogueManager with the same three-character chunks.
    fake = FakeGame()
    llm = CountedBackend(reply, chunk=3, delay=0)
    said: list[tuple[str, int]] = []
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), tts=TtsClient("ws://unused"))

        async def record_say(conv, text, *args, **kwargs):
            said.append((text, llm.chunks))

        monkeypatch.setattr(dialogue, "_say", record_say)
        conv = dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Что скажешь?")
        await _until(lambda: fake.statuses[-1:] == ["idle"])

    assert said == expected
    assert said[0][1] < sentence_end_chunk
    assert conv.history[-1] == Message("assistant", reply)


async def test_a_fragmented_tool_call_drops_its_arguments_after_a_short_sentence(caplog, monkeypatch):
    clean = "Держи."
    leak = 'functions.give_item({"item": "ITMI_GOLD", "count": 50})'
    fake = FakeGame()
    entries: list[dict] = []
    monkeypatch.setattr(dialogue_module, "CHAT_TEXT_EVERY", 0)
    async with connected(fake) as link:
        dialogue = DialogueManager(
            link,
            FakeBackend(clean + " " + leak, chunk=3, delay=0),
            PersonaProvider(None),
            journal=Journal(entries.append),
        )
        conv = dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Дашь золота?")
        await _until(lambda: fake.statuses[-1:] == ["idle"])

    assert fake.said == [clean]
    texts = [m["text"] for m in fake.received if m["type"] == "chat_text"]
    assert texts[-1] == clean
    assert all(clean.startswith(text.strip()) for text in texts)
    assert conv.history[-1] == Message("assistant", clean)
    assert dialogue.memory.load("test-branch", XARDAS).entries[-1].content == clean
    assert [entry["text"] for entry in entries if entry["kind"] == "npc"] == [clean]
    assert any(
        leak in r.getMessage() and XARDAS in r.getMessage() and r.levelname == "WARNING"
        for r in caplog.records
    )


@pytest.mark.parametrize(
    ("leak", "suffix"),
    [
        ('give_item({"reason": "Ты помог. Бери."})', ""),
        ('give_item({"reason": "Ты помог. Бери."})', " Ну, бывай."),
        (
            'give_item({"reasons": ["Ты помог.",\n"Забирай обещанное золото."]})',
            " Ну, бывай.",
        ),
        ('give_item "Ты помог. Бери.', ""),
    ],
    ids=["reason", "reason-before-speech", "multiline-array-before-speech", "unclosed-quote"],
)
async def test_a_tool_call_does_not_end_inside_its_arguments(leak, suffix, caplog, monkeypatch):
    clean = "Держи." + suffix
    fake = FakeGame()
    entries: list[dict] = []
    monkeypatch.setattr(dialogue_module, "CHAT_TEXT_EVERY", 0)
    async with connected(fake) as link:
        dialogue = DialogueManager(
            link,
            FakeBackend("Держи. " + leak + suffix, chunk=3, delay=0),
            PersonaProvider(None),
            journal=Journal(entries.append),
        )
        conv = dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Дашь золота?")
        await _until(lambda: fake.statuses[-1:] == ["idle"])

    assert " ".join(fake.said) == clean
    texts = [m["text"] for m in fake.received if m["type"] == "chat_text"]
    assert texts[-1] == clean
    assert all(clean.startswith(text.strip()) for text in texts)
    assert conv.history[-1] == Message("assistant", clean)
    assert dialogue.memory.load("test-branch", XARDAS).entries[-1].content == clean
    assert [entry["text"] for entry in entries if entry["kind"] == "npc"] == [clean]
    assert any(
        leak in r.getMessage() and XARDAS in r.getMessage() and r.levelname == "WARNING"
        for r in caplog.records
    )


async def test_a_late_tool_marker_keeps_the_spoken_clause_and_removes_the_pending_sentence(caplog):
    prefix = "Ты вернулся из храма,"
    leak = "пока ждал тебя function give_item(item=ITMI_GOLD)?"
    clean = prefix + " После боя поговорим."
    fake = FakeGame()
    async with scene_tts(per_char=0.0001) as (url, requests), connected(fake) as link:
        dialogue = DialogueManager(
            link,
            FakeBackend(prefix + " " + leak + " После боя поговорим.", chunk=3, delay=0),
            PersonaProvider(None),
            tts=TtsClient(url),
        )
        conv = dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Что скажешь?")
        await _until(lambda: len(fake.say_ends) == 2 and fake.statuses[-1:] == ["idle"])

    assert fake.said == [prefix, "После боя поговорим."]
    assert [r["text"] for r in requests] == fake.said
    assert conv.history[-1] == Message("assistant", clean)
    assert [m["text"] for m in fake.received if m["type"] == "chat_text"][-1] == clean
    assert any(leak in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


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


class OneVoiceGame(FakeGame):
    """Keeps selected ordinary lines queued in the game instead of starting them at once."""

    def __init__(self, queued: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.queued = set(queued)

    async def handle(self, msg) -> None:
        if msg["type"] == "say" and msg["text"] in self.queued:
            self.drop_says.add(msg["npc"])
            try:
                await super().handle(msg)
            finally:
                self.drop_says.discard(msg["npc"])
            return
        await super().handle(msg)


class OneVoiceTts(TtsClient):
    """Streams a short fake voice; selected lines wait before their first sound."""

    def __init__(self, audio_s: float) -> None:
        super().__init__()
        self.audio_s = audio_s
        self.requested: list[str] = []
        self.held: dict[str, asyncio.Event] = {}
        self.finishing: dict[str, asyncio.Event] = {}
        self.failed: set[str] = set()

    async def stream(self, text, voice_id, description, on_start, on_chunk, style="") -> Speech:
        self.requested.append(text)
        if text in self.held:
            await self.held[text].wait()
        if text in self.failed:
            raise TtsError("no voice")
        await on_start(16000, "fake")
        await on_chunk(b"\0\1" * 16)
        if text in self.finishing:
            await self.finishing[text].wait()
        return Speech(b"", self.audio_s, "fake", 0.0)


@asynccontextmanager
async def one_voice_dialogue(with_tts, llm, *, queued=(), audio_s=0.1):
    fake = OneVoiceGame(queued)
    async with connected(fake) as link:
        dialogue = DialogueManager(
            link, llm, PersonaProvider(None), tts=OneVoiceTts(audio_s) if with_tts else None
        )
        conv = dialogue.open(XARDAS, "Ксардас")
        try:
            yield fake, dialogue, conv
        finally:
            tasks = conv.tasks()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("with_tts", [True, False])
@pytest.mark.parametrize("first_started", [True, False])
async def test_an_overlay_event_cuts_the_unheard_previous_reply(with_tts, first_started):
    old_texts = (
        "Я обещал ему пощаду и сдержу слово.",
        "Не лезь в драку раньше времени.",
        "Мы ещё поговорим об этом позже.",
    )
    old_reply = " ".join(
        f"[{mood}] {text}" for mood, text in zip(("neutral", "angry", "friendly"), old_texts, strict=True)
    )
    urgent = "Бандиты уже напали на героя!"
    llm = FakeBackend(script=[FakeStep(old_reply), FakeStep(urgent)], delay=0)
    queued = old_texts[1:] if first_started else old_texts
    async with one_voice_dialogue(with_tts, llm, queued=queued, audio_s=10.0) as (fake, dialogue, conv):
        await dialogue.say_as_player(XARDAS, "Что ты обещал?")
        await _until(lambda: len(fake.says) == 3 and (not with_tts or len(fake.say_ends) == 3))
        lines, reply = conv.last_lines[:], conv.reply_id
        if first_started:
            await _until(lambda: dialogue.started_at(lines[0]) is not None)
        assert all(dialogue.started_at(line) is None for line in lines[int(first_started) :])

        turn = dialogue.event_turn(XARDAS, "[Событие: герой напал на бандита]", overlay=True)
        assert turn is not None
        await turn
        await _until(lambda: any(m["text"] == urgent for m, _ in fake.says))

        said = old_texts[0] if first_started else ""
        expected = {"type": "hush", "npc": XARDAS, "reply": reply}
        if said:
            expected["said"] = said
        assert fake.hushed == [expected]
        overlay = next(m for m, _ in fake.says if m["text"] == urgent)
        assert fake.received.index(expected) < fake.received.index(overlay)
        assert set(lines[int(first_started) :]) <= conv.hushed
        assert overlay["line"] not in conv.hushed and overlay["reply"] != reply
        note = f"Ты оборвал себя на полуслове{f'; успел сказать: «{said}»' if said else ''}"
        assert Message("developer", f"[{note}]") in conv.history


@pytest.mark.parametrize("with_tts", [True, False])
async def test_an_overlay_does_not_hush_an_already_heard_reply(with_tts):
    llm = FakeBackend(script=[FakeStep("Я сдержу своё слово."), FakeStep("Бандиты напали!")], delay=0)
    async with one_voice_dialogue(with_tts, llm) as (fake, dialogue, conv):
        await dialogue.say_as_player(XARDAS, "Ты обещаешь?")
        await _until(lambda: bool(fake.says) and (not with_tts or bool(fake.say_ends)))
        (line,) = conv.last_lines
        await _until(lambda: dialogue.started_at(line) is not None)
        dialogue._lines[line] = (dialogue._lines[line][0], time.monotonic() - 30.0)

        turn = dialogue.event_turn(XARDAS, "[Событие: нападение]", overlay=True)
        assert turn is not None
        await turn
        await _until(lambda: len(fake.says) == 2)

        assert fake.hushed == [] and conv.hushed == set()
        assert not any("Ты оборвал себя" in m.content for m in conv.history)


@pytest.mark.parametrize("with_tts", [True, False])
async def test_two_overlays_of_one_turn_wait_without_hushing_each_other(with_tts, monkeypatch):
    monkeypatch.setattr(dialogue_module, "line_duration", lambda text: 0.2)
    llm = FakeBackend("[angry] Бандиты уже напали на героя! [friendly] Я обязательно тебя прикрою!", delay=0)
    async with one_voice_dialogue(with_tts, llm) as (fake, dialogue, conv):
        turn = dialogue.event_turn(XARDAS, "[Событие: нападение]", overlay=True)
        assert turn is not None
        await _until(lambda: len(fake.says) == 1 and (not with_tts or len(fake.say_ends) == 1))
        first_free_at = conv.overlay_free_at
        await turn
        await _until(lambda: len(fake.says) == 2)

        assert fake.says[1][1] >= first_free_at - 0.005
        assert all(m["overlay"] for m, _ in fake.says)
        assert fake.hushed == [] and conv.hushed == set()
        assert not any("Ты оборвал себя" in m.content for m in conv.history)


@pytest.mark.parametrize("with_tts", [True, False])
async def test_an_ordinary_reply_waits_for_the_sounding_overlay(with_tts, monkeypatch):
    monkeypatch.setattr(dialogue_module, "line_duration", lambda text: 0.2)
    llm = FakeBackend(
        script=[FakeStep("Я обязательно тебя прикрою!"), FakeStep("Теперь можно поговорить.")], delay=0
    )
    async with one_voice_dialogue(with_tts, llm) as (fake, dialogue, conv):
        overlay = dialogue.event_turn(XARDAS, "[Событие: нападение]", overlay=True)
        assert overlay is not None
        await overlay
        await _until(lambda: len(fake.says) == 1 and (not with_tts or len(fake.say_ends) == 1))
        free_at = conv.overlay_free_at
        assert time.monotonic() < free_at

        ordinary = dialogue.event_turn(XARDAS, "[Событие: герой подошёл]")
        assert ordinary is not None
        await ordinary
        await _until(lambda: len(fake.says) == 2)

        assert fake.says[1][1] >= free_at - 0.005
        assert "overlay" not in fake.says[1][0]
        assert fake.hushed == []


@pytest.mark.parametrize("with_tts", [True, False])
async def test_interrupt_releases_the_overlay_wait_for_the_next_reply(with_tts, monkeypatch):
    monkeypatch.setattr(dialogue_module, "line_duration", lambda text: 2.0)
    second = "Я обязательно тебя прикрою!"
    answer = "Теперь я тебя слушаю."
    llm = FakeBackend(
        script=[FakeStep(f"[angry] Бандиты уже напали на героя! [friendly] {second}"), FakeStep(answer)],
        delay=0,
    )
    async with one_voice_dialogue(with_tts, llm, audio_s=2.0) as (fake, dialogue, conv):
        overlay = dialogue.event_turn(XARDAS, "[Событие: нападение]", overlay=True)
        assert overlay is not None
        await _until(
            lambda: len(conv.last_lines) == 2
            and len(fake.says) == 1
            and (not with_tts or (len(fake.say_ends) == 1 and conv.voice.empty()))
        )
        old_free_at = conv.overlay_free_at
        assert dialogue.interrupt(XARDAS)
        assert conv.overlay_free_at <= time.monotonic()
        await asyncio.gather(overlay, return_exceptions=True)

        reply = dialogue.event_turn(XARDAS, "[Событие: герой заговорил]")
        assert reply is not None
        await reply
        await _until(lambda: any(m["text"] == answer for m, _ in fake.says), timeout=1.0)

        assert next(at for m, at in fake.says if m["text"] == answer) < old_free_at
        assert not any(m["text"] == second for m, _ in fake.says)


async def test_an_overlay_hushes_at_first_sound_and_drops_only_old_unvoiced_lines():
    old = "Я обещал ему пощаду и сдержу слово."
    urgent, following = "Бандиты уже напали на героя!", "Я обязательно тебя прикрою!"
    llm = FakeBackend(script=[FakeStep(old), FakeStep(f"[angry] {urgent} [friendly] {following}")], delay=0)
    async with one_voice_dialogue(True, llm, audio_s=0.5) as (fake, dialogue, conv):
        await dialogue.say_as_player(XARDAS, "Что ты обещал?")
        await _until(lambda: bool(fake.say_ends) and dialogue.started_at(conv.last_lines[0]) is not None)
        dialogue._audio[conv.last_lines[0]] = 10.0
        old_reply = conv.reply_id
        dialogue.tts.held[urgent] = asyncio.Event()
        turn = dialogue.event_turn(XARDAS, "[Событие: нападение]", overlay=True)
        assert turn is not None
        await turn
        await _until(lambda: urgent in dialogue.tts.requested)

        pending = []
        for text in ("Не лезь в драку раньше времени.", "Мы ещё поговорим об этом позже."):
            await dialogue._say(conv, text, old_reply)
            line = conv.last_lines.pop()
            conv.earlier_lines.append(line)
            pending.append((line, text))
        assert fake.hushed == [] and not conv.hushed

        dialogue.tts.held[urgent].set()
        await asyncio.wait_for(conv.voice.join(), 2.0)
        await _until(lambda: any(m["text"] == following for m, _ in fake.says))

        assert fake.hushed == [{"type": "hush", "npc": XARDAS, "reply": old_reply, "said": old}]
        assert {line for line, _ in pending} <= conv.hushed
        assert all(text not in dialogue.tts.requested for _, text in pending)
        assert all(m["text"] not in {text for _, text in pending} for m, _ in fake.says)
        assert [m["text"] for m, _ in fake.says] == [old, urgent, following]


async def test_a_stream_finishing_after_interrupt_does_not_restore_the_overlay_wait():
    urgent, answer = "Я обязательно тебя прикрою!", "Теперь я тебя слушаю."
    llm = FakeBackend(script=[FakeStep(urgent), FakeStep(answer)], delay=0)
    async with one_voice_dialogue(True, llm, audio_s=10.0) as (fake, dialogue, conv):
        finishing = dialogue.tts.finishing[urgent] = asyncio.Event()
        overlay = dialogue.event_turn(XARDAS, "[Событие: нападение]", overlay=True)
        assert overlay is not None
        await overlay
        await _until(lambda: bool(fake.says) and dialogue.started_at(conv.last_lines[0]) is not None)
        assert not fake.say_ends
        assert dialogue.interrupt(XARDAS)
        reset_at = conv.overlay_free_at

        reply = dialogue.event_turn(XARDAS, "[Событие: герой заговорил]")
        assert reply is not None
        await reply
        finishing.set()
        await _until(lambda: any(m["text"] == answer for m, _ in fake.says), timeout=1.0)

        assert conv.overlay_free_at == reset_at


async def test_tts_failure_fallback_hushes_old_speech_and_holds_the_next_reply(monkeypatch):
    monkeypatch.setattr(dialogue_module, "line_duration", lambda text: 0.2)
    old, urgent, answer = "Я сдержу своё слово.", "Бандиты уже напали!", "Теперь я тебя слушаю."
    llm = FakeBackend(script=[FakeStep(old), FakeStep(urgent), FakeStep(answer)], delay=0)
    async with one_voice_dialogue(True, llm, audio_s=10.0) as (fake, dialogue, conv):
        await dialogue.say_as_player(XARDAS, "Ты обещаешь?")
        await _until(lambda: bool(fake.say_ends) and dialogue.started_at(conv.last_lines[0]) is not None)
        old_reply = conv.reply_id
        dialogue.tts.failed.add(urgent)
        overlay = dialogue.event_turn(XARDAS, "[Событие: нападение]", overlay=True)
        assert overlay is not None
        await overlay
        await _until(lambda: len(fake.says) == 2)
        free_at = conv.overlay_free_at

        assert fake.hushed == [{"type": "hush", "npc": XARDAS, "reply": old_reply, "said": old}]
        assert fake.received.index(fake.hushed[0]) < fake.received.index(fake.says[1][0])
        assert "stream" not in fake.says[1][0]
        reply = dialogue.event_turn(XARDAS, "[Событие: герой заговорил]")
        assert reply is not None
        await reply
        await _until(lambda: len(fake.says) == 3)

        assert fake.says[2][1] >= free_at - 0.005
        assert dialogue.tts.requested == [old, urgent]


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


# --- two npc's talk to each other (play_scene) ---------------------------------------------

SCENE = [
    (LOBART, "Лобарт", HILDA, "Слыхала? Этот бродяга мне наврал.", "angry"),
    (HILDA, "Хильда", LOBART, "Да ну? А с виду смирный.", None),
    (LOBART, "Лобарт", HILDA, "Глаз с него не спускай.", None),
]


@asynccontextmanager
async def scene_tts(per_char: float = 0.01, fail: bool = False):
    """TTS service stand-in: `per_char` seconds of audio per character in two chunks, or an error."""
    requests: list[dict] = []

    async def handler(ws) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            requests.append(msg)
            if fail:
                await ws.send(json.dumps({"type": "error", "id": msg["id"], "detail": "no voice"}))
                continue
            start = {"type": "start", "id": msg["id"], "sample_rate": 16000, "provider": "fake"}
            await ws.send(json.dumps(start))
            pcm = b"\0\1" * int(16000 * per_char * len(msg["text"]))
            half = len(pcm) // 4 * 2
            await ws.send(pcm[:half])
            await ws.send(pcm[half:])
            await ws.send(json.dumps({"type": "done", "id": msg["id"]}))

    async with serve(handler, "127.0.0.1", 0) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", requests


def _scene_dialogue(link, pace: float = 0.05, **kwargs) -> DialogueManager:
    dialogue = DialogueManager(link, FakeBackend(), PersonaProvider(None), **kwargs)
    dialogue.scene_pace = lambda text: pace
    return dialogue


async def test_a_scene_is_said_in_turn_each_line_whole_and_to_the_other():
    fake = FakeGame()
    async with scene_tts() as (url, requests), connected(fake) as link:
        dialogue = _scene_dialogue(link, tts=TtsClient(url))
        play = dialogue.play_scene(SCENE, quiet=True)
        await asyncio.wait_for(play.task, 10)

    says = [m for m, _ in fake.says]
    assert [(m["npc"], m["to"], m["text"]) for m in says] == [(n, to, t) for n, _, to, t, _ in SCENE]
    assert all(m["stream"] and "audio" not in m and "reply" not in m for m in says)
    # each line goes out whole: the game knows its length when it starts (never a WAV: a hush can't stop it)
    flow = [m["type"] for m in fake.received if m["type"] in ("say", "say_audio", "say_end")]
    assert flow == ["say", "say_audio", "say_audio", "say_end"] * 3
    assert fake.say_ends == {line.line: round(line.audio_s, 3) for line in play.lines}
    # the next line only once the one before has been heard: started, its voice over, a breath
    for line, (_, sent_at) in zip(play.lines[:-1], fake.says[1:], strict=True):
        assert line.audio_s == pytest.approx(0.01 * len(line.text), abs=0.01)
        assert sent_at >= line.started + line.audio_s + SCENE_GAP_S - 0.005
    assert (play.sent, play.why, play.said()) == (3, "", play.lines)
    # each in its own voice, lowered, the mood heard; on a connection of its own (answers don't wait)
    assert [r["voice"]["id"] for r in requests] == [LOBART, HILDA, LOBART]
    assert all(QUIET_STYLE in r["style"] for r in requests)
    assert MOOD_DELIVERY["angry"] in requests[0]["style"]
    assert dialogue.tts._ws is None and dialogue._scene_tts is not None
    assert dialogue.conversations == {}  # nobody remembers it here: gossip.py writes the notes


async def test_without_a_voice_a_line_waits_for_the_one_before_to_start_and_be_said():
    fake = FakeGame()
    fake.start_delay = 0.3
    async with connected(fake) as link:
        play = _scene_dialogue(link).play_scene(SCENE[:2])
        await asyncio.wait_for(play.task, 5)

    (first, t0), (second, t1) = fake.says
    assert "stream" not in first and first["to"] == HILDA and first["mood"] == "angry"
    assert second["to"] == LOBART and "mood" not in second
    assert t1 - t0 >= 0.3 + 0.05 + SCENE_GAP_S - 0.005  # started late, said, then the breath
    assert [line.audio_s for line in play.lines] == [None, None]


async def test_a_line_the_game_never_starts_ends_the_scene(monkeypatch):
    monkeypatch.setattr(dialogue_module, "SCENE_START_S", 0.3)
    fake = FakeGame()
    fake.drop_says = {HILDA}  # she can't speak now: the bridge drops her line without a word
    async with connected(fake) as link:
        dialogue = _scene_dialogue(link)
        play = dialogue.play_scene(SCENE)
        await asyncio.wait_for(play.task, 5)
        await _until(lambda: bool(fake.hushed))

    assert [m["npc"] for m, _ in fake.says] == [LOBART, HILDA]  # the third is never sent
    assert fake.hushed == [{"type": "hush", "npc": HILDA}]  # if it starts later, it is cut at once
    assert (play.sent, play.why, play.said()) == (2, "not_started", play.lines[:1])
    assert dialogue._starts == {}


async def test_the_hero_cuts_a_scene_both_stop():
    fake = FakeGame()
    async with connected(fake) as link:
        dialogue = _scene_dialogue(link, pace=0.5)
        play = dialogue.play_scene(SCENE)
        await _until(lambda: play.sent == 2 and dialogue.started_at(play.lines[1].line) is not None)
        said = play.cut()
        assert said == play.lines[:2] and play.cut() == said  # once
        await asyncio.wait({play.task})  # a cut play does not raise
        await asyncio.sleep(0.9)  # the third line's time

    second = play.lines[1]
    assert fake.hushed == [
        {"type": "hush", "npc": HILDA, "line": second.line, "said": second.text},  # keeps her subtitle
        {"type": "hush", "npc": LOBART},
    ]
    assert len(fake.says) == 2 and play.why == "cut"


async def test_a_line_sent_but_not_started_is_hushed_plainly_and_a_load_hushes_nothing():
    fake = FakeGame()
    fake.start_delay = 0.5
    async with connected(fake) as link:
        dialogue = _scene_dialogue(link)
        play = dialogue.play_scene(SCENE)
        await _until(lambda: play.sent == 1)
        assert play.cut() == []
        await _until(lambda: len(fake.hushed) == 2)
        assert fake.hushed == [{"type": "hush", "npc": LOBART}, {"type": "hush", "npc": HILDA}]

        fake.hushed.clear()
        fake.start_delay = 0
        loaded = dialogue.play_scene(SCENE)
        await _until(lambda: dialogue.started_at(loaded.lines[0].line) is not None)
        assert loaded.cut(hush=False) == loaded.lines[:1]  # the world is gone: nothing to stop
        await asyncio.sleep(0.4)

    assert fake.hushed == [] and [m["text"] for m, _ in fake.says][-1] == SCENE[0][3]
    assert len(fake.says) == 2


async def test_a_voice_that_fails_leaves_the_scene_in_subtitles():
    fake = FakeGame()
    journal: list[dict] = []
    async with scene_tts(fail=True) as (url, requests), connected(fake) as link:
        dialogue = _scene_dialogue(link, tts=TtsClient(url), journal=Journal(journal.append))
        play = dialogue.play_scene(SCENE)
        await asyncio.wait_for(play.task, 5)

    assert [m["text"] for m, _ in fake.says] == [t for _, _, _, t, _ in SCENE]
    assert not any(m.get("stream") or m.get("audio") for m, _ in fake.says)
    assert len(requests) == 1  # the voice is off for a while: the next lines don't try it
    assert dialogue._tts_paused_until - time.monotonic() > TTS_PAUSE - 5
    assert [e["kind"] for e in journal] == ["tts_off"]


async def test_notes_do_not_warm_the_quick_model_but_talking_to_the_npc_does(monkeypatch):
    monkeypatch.setattr(dialogue_module, "PREWARM_DELAY_S", 0)
    fake = FakeGame()
    quick = FakeBackend("Хм...", delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend(), PersonaProvider(None), quick=quick)
        dialogue.remember(HILDA, "Хильда", "Лобарт рассказал тебе о герое")  # a rumor
        dialogue.overheard(HILDA, "Хильда", "Привет", None)
        said = [("Лобарт", "Слыхала?"), ("Хильда", "Да ну?")]
        dialogue.overheard_talk(XARDAS, "Ксардас", ("Лобарт", "Хильда"), said)
        await asyncio.sleep(0.05)
        assert quick.prewarms == []
        dialogue.open(HILDA, "Хильда")  # the hero talks to her: her history, notes and all, is read now
        await _until(lambda: len(quick.prewarms) == 1)
        assert quick.prewarms[0][1] == dialogue.conversations[HILDA].history

    (heard,) = [m.content for m in dialogue.conversations[XARDAS].history]
    assert heard == "[Ты слышал, как Лобарт и Хильда говорили о герое: Лобарт: «Слыхала?» — Хильда: «Да ну?»]"


async def test_a_line_that_never_starts_is_not_spoken_forever(monkeypatch):
    """The game does not start a line of an npc far from the hero (or one the bridge refused): after a while
    the npc is not counted as speaking any more, or whatever waits for it (a scene) would wait forever."""
    monkeypatch.setattr(dialogue_module, "LINE_LOST_S", 0.3)
    fake = FakeGame()
    fake.drop_says.add(XARDAS)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend("Ступай.", delay=0), PersonaProvider(None))
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Привет.")
        await _until(lambda: dialogue.conversations[XARDAS].reply.done())
        assert dialogue.speaking(XARDAS)  # its line may still come
        await asyncio.sleep(0.4)
        assert not dialogue.speaking(XARDAS)


async def test_an_npc_that_means_to_fight_the_hero_attacks_him_through_the_scripts():
    fake = FakeGame()
    fight = {"goal": "fight", "target": "PLAYER", "attitude": "hostile", "agenda": None}
    llm = FakeBackend("Кошелёк или жизнь!", delay=0, tool_calls=[ToolCall("set_intent", fight)])
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        dialogue.guild_of = lambda npc: 9  # a bandit
        await link.attach(XARDAS)
        dialogue.open(XARDAS, "Бандит")
        await dialogue.say_as_player(XARDAS, "Отойди с дороги")
        await _until(lambda: any(m.get("type") == "story_effect" for m in fake.story))
        await _until(lambda: dialogue.conversations[XARDAS].history[-1].content == "[Ты напал на героя.]")

    (attack,) = [m for m in fake.story if m["type"] == "story_effect"]
    assert attack["npc"] == XARDAS and attack["effects"] == [
        {"do": "attack", "reason": "AR_SUDDENENEMYINFERNO"}
    ]
    assert not any(
        m["type"] == "action" and m["action"] == "fight" for m in fake.received
    )  # the scripts fight


# --- the quick model of a fight ----------------------------------------------------------------------


def _situation_of(messages) -> str:
    return next(m.content for m in reversed(messages) if m.role == "developer" and "Обстановка" in m.content)


async def test_a_line_in_a_fight_is_the_quick_models_and_the_turns_model_only_acts():
    """The bandit's "Держи его!" comes from the quick model at once; the conversational model, told what was
    said, decides the attack, and its own words are not said."""
    fake = FakeGame()
    fight = {"goal": "fight", "target": "PLAYER", "attitude": "hostile", "agenda": None}
    quick = FakeBackend("[hostile] Это он! Держи его!", delay=0)
    llm = FakeBackend("Слова, которых никто не услышит.", delay=0, tool_calls=[ToolCall("set_intent", fight)])
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), quick=quick)
        dialogue.in_fight = lambda npc: True
        dialogue.guild_of = lambda npc: 9  # a bandit
        await link.attach(XARDAS)
        dialogue.open(XARDAS, "Бандит")
        await dialogue.event_turn(XARDAS, "[Событие: герой сказал рядом: «Давай поговорим»]")
        await _until(lambda: any(m.get("type") == "story_effect" for m in fake.story))

    assert " ".join(fake.said) == "Это он! Держи его!"
    (_, quick_messages, quick_tools), (_, messages, tools) = quick.calls[0], llm.calls[0]
    assert quick_tools == [] and "set_intent" in {t.name for t in tools}
    brief, full = _situation_of(quick_messages), _situation_of(messages)
    assert dialogue_module.QUICK_HINT in brief and "Цели для намерения" not in brief
    assert "Цели для намерения" in full and "Ты уже сказал вслух: «Это он! Держи его!»" in full
    history = dialogue.conversations[XARDAS].history
    assert history[-1].content.startswith("[Ты напал") or history[-2].content == "Это он! Держи его!"
    assert any(m.role == "assistant" and m.content == "Это он! Держи его!" for m in history)
    assert dialogue.conversations[XARDAS].intent.goal == "fight"


async def test_the_turns_model_speaks_when_the_quick_model_fails():
    fake = FakeGame()
    llm = FakeBackend("Сам справлюсь!", delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), quick=FailingBackend())
        dialogue.in_fight = lambda npc: True
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.event_turn(XARDAS, "[Событие: Волк нападает на тебя]")
        await _until(lambda: fake.said == ["Сам справлюсь!"])

    _, messages, _ = llm.calls[0]
    assert dialogue_module.QUICK_HINT not in _situation_of(messages)  # the whole situation, as usual


async def test_out_of_a_fight_the_quick_model_only_says_the_first_words():
    """The hero's phrase: the quick model's first words, then the conversational model's answer. An event
    outside a fight is the conversational model's alone."""
    fake = FakeGame()
    quick = FakeBackend("[angry] Тьфу! И это всё?", delay=0)
    llm = FakeBackend("Не спеши.", delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), quick=quick)
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Ты старый дурак")
        await dialogue.event_turn(XARDAS, "[Событие: герой подошёл к тебе]")
        await _until(lambda: fake.said == ["Тьфу!", "Не спеши.", "Не спеши."])

    assert len(quick.calls) == 1  # the phrase's first words; the event: no
    _, words_request, words_tools = quick.calls[0]
    assert words_tools == [] and dialogue_module.REACT_HINT in _situation_of(words_request)
    said = [m for m, _ in fake.says if m["text"] == "Тьфу!"]
    assert said[0]["filler"] is True and said[0]["mood"] == "angry" and said[0].get("reply") is None
    _, messages, _ = llm.calls[0]
    situation = _situation_of(messages)
    assert "Ты уже сказал вслух: «Тьфу!»" in situation and dialogue_module.REACT_HINT not in situation
    assert [m.content for m in dialogue.conversations[XARDAS].history][:2] == ["Ты старый дурак", "Не спеши."]


async def test_no_first_words_to_a_greeting_or_without_the_quick_model_in_time(monkeypatch):
    monkeypatch.setattr(dialogue_module, "REACTION_S", 0.2)
    fake = FakeGame()
    nothing = FakeBackend("[friendly] —", delay=0)
    late = FakeBackend("Хм...", delay=0, first=1.0)
    async with connected(fake) as link:
        greeted = DialogueManager(
            link, FakeBackend("Здравствуй.", delay=0), PersonaProvider(None), quick=nothing
        )
        greeted.open(XARDAS, "Ксардас")
        await greeted.say_as_player(XARDAS, "Привет")
        slow = DialogueManager(link, FakeBackend("Слушаю.", delay=0), PersonaProvider(None), quick=late)
        slow.open(LOBART, "Лобарт")
        await slow.say_as_player(LOBART, "Где мне найти работу?")
        await _until(lambda: fake.said == ["Здравствуй.", "Слушаю."])

    for dialogue in (greeted, slow):
        _, messages, _ = dialogue.llm.calls[0]
        assert "Ты уже сказал вслух" not in _situation_of(messages)


async def test_the_quick_model_is_kept_warm_with_the_history_of_an_npc_in_a_fight(monkeypatch):
    """Whenever the history of an npc in a fight grows, and when the hero starts to speak, the quick model
    reads the npc's instructions and history: its next line only reads the situation and the trigger."""
    monkeypatch.setattr(dialogue_module, "PREWARM_DELAY_S", 0)
    fake = FakeGame()
    quick = FakeBackend("Держись!", delay=0)
    fighting = {LOBART}
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend("", delay=0), PersonaProvider(None), quick=quick)
        dialogue.in_fight = lambda npc: npc in fighting
        dialogue.open(XARDAS, "Ксардас", warm=False)  # nobody talks to them yet
        dialogue.open(LOBART, "Лобарт", warm=False)
        dialogue.note(XARDAS, "Ты видел волка")  # not in a fight: nothing to warm
        dialogue.note(LOBART, "Волк нападает на тебя")
        await _until(lambda: len(quick.prewarms) == 1)
        instructions, history = quick.prewarms[0]
        assert "Лобарт" in instructions and history == dialogue.conversations[LOBART].history

        await dialogue.event_turn(LOBART, "[Событие: Волк нападает на тебя]")
        # not during the turn (its line would wait for the server's one slot), once after it
        await _until(lambda: len(quick.prewarms) == 2)
        assert quick.prewarms[-1][1] == dialogue.conversations[LOBART].history
        assert quick.prewarms[-1][1][-1] == Message("assistant", "Держись!")

        await fake.send(type="ptt_start", focus=LOBART)  # the hero speaks: warmed again (the server's slot)
        await _until(lambda: len(quick.prewarms) == 3)


async def test_an_npc_acting_on_its_quick_line_is_done_speaking():
    """While the conversational model decides what to do about the line, the npc is not speaking: the hero's
    next phrase is a new turn, not an interruption, and the line said is remembered as it was."""
    fake = FakeGame()
    quick = FakeBackend("Держись!", delay=0)
    llm = FakeBackend("Неслышные слова.", delay=0, first=5.0)  # still deciding when the hero speaks again
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), quick=quick)
        dialogue.in_fight = lambda npc: True
        dialogue.open(XARDAS, "Ксардас")
        first = dialogue.event_turn(XARDAS, "[Событие: Волк нападает на тебя]")
        conv = dialogue.conversations[XARDAS]
        await _until(lambda: conv.acting and fake.statuses[-1:] == ["idle"])
        _, sounding, unsaid = dialogue._said(conv)
        assert dialogue.speaking(XARDAS) == (sounding or unsaid)  # the running turn does not count
        dialogue.say_as_player(XARDAS, "Бей его!")
        await _until(lambda: first.done())

    assert first.cancelled()
    history = [m.content for m in dialogue.conversations[XARDAS].history]
    assert history[:2] == ["[Событие: Волк нападает на тебя]", "Держись!"]
    assert not any("перебил" in h for h in history)


async def test_a_quick_line_ends_at_its_first_blank_line():
    """Gemma, seeing tool calls in the history, may write one out after its line: it is never said."""
    fake = FakeGame()
    quick = FakeBackend("[hostile] Это он! Держи его!\n\nset_intent(goal=fight, target=PLAYER)", delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend("", delay=0), PersonaProvider(None), quick=quick)
        dialogue.in_fight = lambda npc: True
        dialogue.open(XARDAS, "Бандит")
        await dialogue.event_turn(XARDAS, "[Событие: герой сказал рядом: «Давай поговорим»]")
        await _until(lambda: fake.statuses[-1:] == ["idle"] and fake.said)

    assert " ".join(fake.said) == "Это он! Держи его!"
    assert dialogue.conversations[XARDAS].history[-1] == Message("assistant", "Это он! Держи его!")


async def test_the_first_words_go_out_before_the_gesture():
    """The game's queue holds a line until the gesture before it is over: the words go first."""
    fake = FakeGame()
    seen: list[bool] = []
    async with connected(fake) as link:
        dialogue = DialogueManager(
            link, FakeBackend("Слушаю.", delay=0), PersonaProvider(None), quick=FakeBackend("Хм...", delay=0)
        )

        async def first_reaction(npc: str, text: str) -> FirstReaction:
            return FirstReaction(lambda: seen.append(any(t == "Хм..." for t, _ in dialogue._lines.values())))

        dialogue.first_reaction = first_reaction
        dialogue.open(XARDAS, "Ксардас")
        await dialogue.say_as_player(XARDAS, "Что ты знаешь о драконах?")
        await _until(lambda: fake.said == ["Хм...", "Слушаю."])

    assert seen == [True]


async def test_a_companion_answers_an_order_through_the_quick_model():
    fake = FakeGame()
    quick = FakeBackend("[neutral] Стою.", delay=0)
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend(), PersonaProvider(None), quick=quick)
        dialogue.remember(LOBART, "Лобарт", "Герой приказал тебе: «Стой!» — остановиться. Ты подчинился.")
        await dialogue.acknowledge(LOBART, "Лобарт")
        silent = DialogueManager(link, FakeBackend(), PersonaProvider(None))
        await silent.acknowledge(LOBART, "Лобарт")  # no quick model: no answer
        await _until(lambda: fake.said == ["Стою."])

    (_, messages, tools) = quick.calls[0]
    assert tools == [] and "Герой приказал тебе: «Стой!»" in messages[-2].content
    assert messages[-1].role == "developer" and dialogue_module.ACK_HINT in messages[-1].content
    (said,) = [m for m, _ in fake.says if m["text"] == "Стою."]
    assert said["overlay"] is True and not said.get("filler")  # on the move: the order is carried out


async def test_first_words_are_short_and_not_the_same_twice_in_a_row():
    """Gemma now and then answers at once in a whole sentence, or says "Хм..." to everything: such first
    words are not said (the answer would follow a second one; the same sound grows tiresome)."""
    fake = FakeGame()
    quick = FakeBackend(
        script=[
            FakeStep("Хм..."),
            FakeStep("Хм..."),
            FakeStep("Ты ещё слишком слаб, чтобы полагаться на чужую силу."),
            FakeStep("[angry] Ишь ты!"),
        ],
        delay=0,
    )
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend("Слушаю.", delay=0), PersonaProvider(None), quick=quick)
        dialogue.open(XARDAS, "Ксардас")
        for phrase in (
            "Что это за башня?",
            "А кто тут живёт?",
            "Помоги мне.",
            "Я убью дракона голыми руками!",
        ):
            await dialogue.say_as_player(XARDAS, phrase)
        await _until(lambda: fake.said.count("Слушаю.") == 4)

    assert [m["text"] for m, _ in fake.says if m.get("filler")] == ["Хм...", "Ишь ты!"]


async def test_one_fighting_the_hero_may_call_it_off_and_keeps_to_it():
    fake = FakeGame()
    calm = {"goal": "none", "target": None, "attitude": "wary", "agenda": None}
    llm = FakeBackend("Ладно, ладно. Живи пока.", delay=0, tool_calls=[ToolCall("set_intent", calm)])
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        dialogue.fights_hero = lambda npc: True  # the scripts' attack on the hero
        calmed: list[str] = []
        dialogue.on_calm.append(calmed.append)
        dialogue.open(XARDAS, "Бандит")
        await dialogue.say_as_player(XARDAS, "Воу, воу, ты чего? Спокуха!")
        await _until(
            lambda: dialogue.conversations[XARDAS].history[-1].content == "[Ты прекратил драку с героем.]"
        )

    (effect,) = [m for m in fake.story if m["type"] == "story_effect"]
    assert effect["npc"] == XARDAS and effect["effects"] == [{"do": "calm", "attitude": "ATT_NEUTRAL"}]
    assert calmed == [XARDAS]
    situation = next(m.content for m in llm.calls[0][1] if m.role == "developer")
    assert "Ты дерёшься с героем. Прекратить драку" in situation


async def test_words_with_a_hostile_heart_do_not_end_the_fight():
    fake = FakeGame()
    still = {"goal": "none", "target": None, "attitude": "hostile", "agenda": None}
    llm = FakeBackend(
        "Спокуха? Сейчас я тебе покажу спокуху!", delay=0, tool_calls=[ToolCall("set_intent", still)]
    )
    async with connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
        dialogue.fights_hero = lambda npc: True
        dialogue.open(XARDAS, "Бандит")
        await dialogue.say_as_player(XARDAS, "Спокуха!")
        await _until(lambda: dialogue.conversations[XARDAS].intent.attitude == "hostile")
        await asyncio.sleep(0.1)
    assert not [m for m in fake.story if m["type"] == "story_effect"]
