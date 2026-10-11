import asyncio
import json
import math
import struct
from contextlib import asynccontextmanager

from websockets.asyncio.server import serve

from gothic_ai.dialogue import DialogueManager
from gothic_ai.llm.fake import FakeBackend
from gothic_ai.persona import PersonaProvider
from gothic_ai.voice import MIC_DENIED, AsrClient, VoiceInput
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import XARDAS, _until


class FakeSource:
    """Says its whole recording the moment the key goes down."""

    rate = 16000

    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.recording = False

    def start(self, on_chunk) -> None:
        self.recording = True
        for i in range(0, len(self.pcm), 3200):
            on_chunk(self.pcm[i : i + 3200])

    def stop(self) -> None:
        self.recording = False


def speech(seconds: float = 1.0) -> bytes:
    n = int(seconds * 16000)
    return struct.pack(f"<{n}h", *(int(8000 * math.sin(i / 10)) for i in range(n)))


@asynccontextmanager
async def fake_asr(text: str = "Дай мне зелье"):
    """ASR service stand-in: a partial per audio chunk, then `text` as the final."""
    utterances: list[int] = []

    async def handler(ws) -> None:
        await ws.send(json.dumps({"type": "hello", "service": "fake-asr"}))
        size = 0
        async for raw in ws:
            if isinstance(raw, bytes):
                size += len(raw)
                words = text.split()[: size // 3200]
                await ws.send(json.dumps({"type": "partial", "text": " ".join(words)}))
                continue
            msg = json.loads(raw)
            if msg["type"] == "start":
                size = 0
            elif msg["type"] == "end":
                utterances.append(size)
                await ws.send(json.dumps({"type": "final", "text": text, "provider": "fake"}))

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}", utterances


async def test_push_to_talk_becomes_the_players_line():
    fake = FakeGame()
    llm = FakeBackend("Держи.", delay=0)
    async with fake_asr() as (url, utterances), connected(fake) as link:
        dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False)
        voice = VoiceInput(link, dialogue, FakeSource(speech()), AsrClient(url))
        await fake.send(type="chat_open", npc=XARDAS, name="Ксардас")
        await fake.send(type="ptt_start", npc=XARDAS)
        await _until(lambda: voice.active is not None and voice.active.npc == XARDAS)
        await fake.send(type="ptt_stop", npc=XARDAS)
        await _until(lambda: fake.said == ["Держи."])

        assert fake.transcripts == ["Дай мне зелье"]
        assert fake.partials[:2] == ["Дай", "Дай мне"]  # streamed while the key was held
        assert utterances == [32000]
        assert "recognizing" in fake.statuses
        assert llm.calls[-1][1][-1].content == "Дай мне зелье"


async def test_short_tap_is_cancelled_without_recognition():
    fake = FakeGame()
    async with fake_asr() as (url, utterances), connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend(delay=0), PersonaProvider(None), greet=False)
        source = FakeSource(speech())
        voice = VoiceInput(link, dialogue, source, AsrClient(url))
        await fake.send(type="ptt_start", npc=XARDAS)
        await _until(lambda: voice.active is not None and voice.active.npc == XARDAS)
        await fake.send(type="ptt_cancel", npc=XARDAS)
        await _until(lambda: voice.active is None)
        await asyncio.sleep(0.1)
        assert not source.recording and utterances == [] and fake.transcripts == []


async def test_silent_microphone_and_missing_service_are_reported():
    fake = FakeGame()
    async with connected(fake) as link:
        dialogue = DialogueManager(link, FakeBackend(delay=0), PersonaProvider(None), greet=False)
        voice = VoiceInput(link, dialogue, FakeSource(bytes(32000)), AsrClient("ws://127.0.0.1:9"))
        await fake.send(type="ptt_start", npc=XARDAS)
        await _until(lambda: voice.active is not None and voice.active.npc == XARDAS)
        await fake.send(type="ptt_stop", npc=XARDAS)
        await _until(lambda: fake.statuses[-1:] == ["error"])
        assert any(m.get("detail") == MIC_DENIED for m in fake.received if m["type"] == "chat_status")

        voice.source = FakeSource(speech())
        await fake.send(type="ptt_start", npc=XARDAS)
        await fake.send(type="ptt_stop", npc=XARDAS)
        await _until(lambda: sum(s == "error" for s in fake.statuses) == 2)
        assert "ASR-сервис недоступен" in fake.received[-1]["detail"]
