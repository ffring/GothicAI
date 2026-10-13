import asyncio
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from websockets.asyncio.server import serve

from gothic_ai import dialogue as dialogue_module
from gothic_ai import scenarios
from gothic_ai.dialogue import QUIET_STYLE, SCENE_GAP_S, TTS_PAUSE, DialogueManager
from gothic_ai.fillers import Fillers
from gothic_ai.journal import Journal
from gothic_ai.llm import Done, LLMError, LLMEvent, Message, TextDelta, ToolCall
from gothic_ai.llm.fake import FakeBackend
from gothic_ai.persona import MOOD_DELIVERY, PersonaProvider
from gothic_ai.tts import TtsClient
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


class _Warming(Fillers):
    def __init__(self) -> None:
        super().__init__("ws://unused")
        self.warmed: list[str] = []

    def warm(self, npc: str, voice: str) -> None:
        self.warmed.append(npc)


async def test_notes_do_not_voice_an_npcs_sounds_but_talking_to_it_does():
    fake = FakeGame()
    fillers = _Warming()
    async with connected(fake) as link:
        dialogue = DialogueManager(
            link, FakeBackend(), PersonaProvider(None), tts=TtsClient("ws://unused"), fillers=fillers
        )
        dialogue.remember(HILDA, "Хильда", "Лобарт рассказал тебе о герое")  # a rumor
        dialogue.overheard(HILDA, "Хильда", "Привет", None)
        said = [("Лобарт", "Слыхала?"), ("Хильда", "Да ну?")]
        dialogue.overheard_talk(XARDAS, "Ксардас", ("Лобарт", "Хильда"), said)
        assert fillers.warmed == []
        dialogue.open(HILDA, "Хильда")  # the hero talks to her: made by a note before, warmed now
        assert fillers.warmed == [HILDA]

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
