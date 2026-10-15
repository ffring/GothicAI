import asyncio
import base64
import json
import logging
import os

import httpx
import pytest

from gothic_tts.cli import load_env_file
from gothic_tts.providers import TtsError, Voice
from gothic_tts.providers.fake import FakeProvider
from gothic_tts.providers.inworld import CHECK_VOICE, InworldTTS
from gothic_tts.synthesizer import Synthesizer

XARDAS = Voice("NONE_100_XARDAS", "An old man with a deep voice")
NO_CREDITS = "You have no credits remaining. Please add credits to continue using the service."


class FakeInworld:
    """Stands in for api.inworld.ai: voice design and publishing, NDJSON speech streams."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict]] = []
        self.voices: set[str] = {"Dmitry"}
        self.designed = 0
        self.fail_line = False
        self.credits = True
        self.odd_check = False  # the check line gets an answer the provider does not know

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        self.requests.append((req.method, req.url.path, body))
        if req.headers.get("authorization") != "Basic test-key":
            return httpx.Response(401, json={"code": 16, "message": "Unauthenticated"})
        match req.url.path:
            case "/tts/v1/voices":
                return httpx.Response(200, json={"voices": [{"voiceId": v} for v in self.voices]})
            case "/voices/v1/voices:design":
                self.designed += 1
                preview = f"ws__design-voice-{self.designed}"
                return httpx.Response(200, json={"previewVoices": [{"voiceId": preview, "previewAudio": ""}]})
            case path if path.endswith(":publish"):
                voice_id = path.removeprefix("/voices/v1/voices/").removesuffix(":publish")
                self.voices.add(voice_id)
                return httpx.Response(200, json={"voiceId": voice_id, "tags": body["tags"]})
            case "/tts/v1/voice:stream":
                if not self.credits:  # asked before the voice is looked up, as the real one does
                    return httpx.Response(
                        402, json={"error": {"code": 7, "message": NO_CREDITS, "details": []}}
                    )
                if self.odd_check and body["voiceId"] == CHECK_VOICE:
                    return httpx.Response(400, json={"code": 3, "message": "Invalid voice id"})
                if body["voiceId"] not in self.voices:
                    return httpx.Response(
                        404, json={"code": 5, "message": f"Unknown voice: {body['voiceId']}"}
                    )
                # odd-sized pieces: a sample is split between two lines
                pcm = b"\1\0" * 100
                lines = [
                    {"result": {"audioContent": base64.b64encode(p).decode()}} for p in (pcm[:51], pcm[51:])
                ]
                if self.fail_line:
                    lines.append({"error": {"code": 13, "message": "synthesis broke"}})
                return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())
        return httpx.Response(404, json={"message": "no such path"})

    def streams(self) -> list[dict]:
        """Lines, without warmup's checks."""
        return [body for body in self._streams() if body["voiceId"] != CHECK_VOICE]

    def checks(self) -> int:
        return sum(body["voiceId"] == CHECK_VOICE for body in self._streams())

    def _streams(self) -> list[dict]:
        return [body for _, path, body in self.requests if path == "/tts/v1/voice:stream"]

    def voice_lists(self) -> int:
        return sum(path == "/tts/v1/voices" for _, path, _ in self.requests)


@pytest.fixture
def api(monkeypatch) -> FakeInworld:
    monkeypatch.setenv("INWORLD_API_KEY", "test-key")
    return FakeInworld()


def _provider(api: FakeInworld, tmp_path, **opts) -> InworldTTS:
    return InworldTTS(cache=tmp_path / "voices.json", transport=httpx.MockTransport(api), **opts)


async def _speak(p: InworldTTS, voice: Voice = XARDAS) -> tuple[bytes, float]:
    parts: list[bytes] = []

    async def on_chunk(pcm: bytes) -> None:
        assert len(pcm) % 2 == 0
        parts.append(pcm)

    seconds = await p.synthesize("Ты снова здесь.", voice, on_chunk)
    return b"".join(parts), seconds


async def test_voice_designed_once_then_reused(api, tmp_path) -> None:
    p = _provider(api, tmp_path)
    await p.warmup()
    pcm, seconds = await _speak(p)
    assert pcm == b"\1\0" * 100 and seconds == pytest.approx(100 / 24000)
    await _speak(p)
    await p.aclose()

    assert api.designed == 1
    design = next(body for _, path, body in api.requests if path == "/voices/v1/voices:design")
    assert design["designPrompt"] == XARDAS.description and design["languageCode"] == "ru-RU"
    first, second = api.streams()
    assert first["voiceId"] == second["voiceId"] == "ws__design-voice-1"
    assert first["audioConfig"] == {"audioEncoding": "PCM", "sampleRateHertz": 24000}
    assert "instruction" not in first  # the description is already in the designed voice

    # the next run remembers it too; a new description is a new voice
    again = _provider(api, tmp_path)
    await _speak(again)
    assert api.designed == 1
    await _speak(again, Voice(XARDAS.id, "A young woman"))
    assert api.designed == 2
    await again.aclose()


async def test_library_voice_steered_by_the_description(api, tmp_path) -> None:
    p = _provider(api, tmp_path, voice="Dmitry")
    await _speak(p)
    flash = _provider(api, tmp_path, voice="Dmitry", model="inworld-tts-2-flash")
    await _speak(flash)
    await _speak(flash, Voice("x", "calm", preset="Dmitry"))
    for provider in (p, flash):
        await provider.aclose()

    assert api.designed == 0
    tts2, *flash_bodies = api.streams()
    assert tts2["voiceId"] == "Dmitry" and tts2["instruction"] == XARDAS.description
    assert all("instruction" not in body for body in flash_bodies)  # Flash has no steering


async def test_the_line_style_steers_any_voice(api, tmp_path) -> None:
    angry = Voice(XARDAS.id, XARDAS.description, style="say angrily with force.")
    designed = _provider(api, tmp_path)
    await _speak(designed, angry)
    await _speak(designed)
    library = _provider(api, tmp_path, voice="Dmitry")
    await _speak(library, angry)
    flash = _provider(api, tmp_path, voice="Dmitry", model="inworld-tts-2-flash")
    await _speak(flash, angry)
    for provider in (designed, library, flash):
        await provider.aclose()

    angry_designed, calm_designed, angry_library, angry_flash = api.streams()
    assert angry_designed["instruction"] == "say angrily with force"  # the voice itself is designed
    assert "instruction" not in calm_designed
    assert angry_library["instruction"] == f"{XARDAS.description}. say angrily with force"
    assert "instruction" not in angry_flash


async def test_vanished_voice_is_designed_again(api, tmp_path) -> None:
    p = _provider(api, tmp_path)
    await _speak(p)
    api.voices.discard("ws__design-voice-1")  # deleted in the portal, or another workspace's key
    pcm, _ = await _speak(p)
    await p.aclose()
    assert pcm and api.designed == 2
    assert json.loads((tmp_path / "voices.json").read_text())[XARDAS.id]["voice_id"] == "ws__design-voice-2"


async def test_errors(api, tmp_path, monkeypatch) -> None:
    p = _provider(api, tmp_path, voice="Dmitry")
    api.fail_line = True
    with pytest.raises(TtsError, match="synthesis broke"):
        await _speak(p)
    monkeypatch.setenv("INWORLD_API_KEY", "wrong")
    with pytest.raises(TtsError, match="rejected the API key"):
        await p.warmup()
    monkeypatch.delenv("INWORLD_API_KEY")
    with pytest.raises(TtsError, match="INWORLD_API_KEY"):
        await _speak(p)
    await p.aclose()


async def test_idle_connection_kept_warm(api, tmp_path, monkeypatch, caplog) -> None:
    p = _provider(api, tmp_path, voice="Dmitry", keep_warm=0.1)
    await p.warmup()
    for _ in range(8):  # lines keep the connection open themselves
        await _speak(p)
        await asyncio.sleep(0.02)
    assert api.checks() == 1

    caplog.set_level(logging.DEBUG, logger="gothic_tts")
    await asyncio.sleep(0.55)  # silence: a free check every 0.1 s
    assert 3 <= api.checks() - 1 <= 6
    monkeypatch.setenv("INWORLD_API_KEY", "wrong")
    before = api.checks()
    await asyncio.sleep(0.35)  # a failed request neither stops it nor logs above debug
    assert api.checks() - before >= 2
    assert caplog.records and all(r.levelno == logging.DEBUG for r in caplog.records)

    await p.aclose()
    stopped = api.checks()
    await asyncio.sleep(0.25)
    assert api.checks() == stopped  # stops with the provider

    monkeypatch.setenv("INWORLD_API_KEY", "test-key")
    cold = _provider(api, tmp_path, voice="Dmitry", keep_warm=0)
    await cold.warmup()
    await asyncio.sleep(0.1)
    await cold.aclose()
    assert api.checks() == stopped + 1


async def test_out_of_credits_stays_out_until_topped_up(api, tmp_path, caplog) -> None:
    """An account without credits still gets the free voice list, but every line is refused with 402.
    Lines must go straight to the fallback, not into a 402 after each check, and quietly."""
    caplog.set_level(logging.DEBUG, logger="gothic_tts")
    inworld, fallback = _provider(api, tmp_path, voice="Dmitry", keep_warm=0), FakeProvider()
    synth = Synthesizer(inworld, fallback, retry_s=0.02, retry_max_s=0.05)

    async def line() -> str:
        async def chunk(pcm: bytes, p) -> None:
            pass

        return (await synth.speak("Да.", XARDAS, chunk)).provider

    def warnings() -> int:
        return sum(r.levelno >= logging.WARNING for r in caplog.records)

    async def checked(n: int) -> None:  # the background check asked Inworld n more times
        target = api.checks() + n
        for _ in range(100):
            if api.checks() >= target:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("no check")

    api.credits = False  # empty from the start: the fallback speaks the first line
    await synth.warmup()
    assert synth.available() == [fallback] and warnings() == 2  # inworld failed, left out
    for _ in range(3):
        assert await line() == "fake"
        await checked(2)
    assert api.streams() == [] and api.voice_lists() == 0  # no line and no voice list asked Inworld
    assert warnings() == 2  # checks that keep failing say nothing

    api.credits = True  # topped up: the next check finds out, for free
    for _ in range(100):
        if inworld in synth.available():
            break
        await asyncio.sleep(0.01)
    assert await line() == "inworld"
    assert len(api.streams()) == 1

    api.credits = False  # spent mid-game: one line pays the 402 round trip, then they skip it
    assert await line() == "fake"
    assert warnings() == 4  # inworld failed, left out: once
    for _ in range(3):
        await checked(2)
        assert await line() == "fake"
    assert len(api.streams()) == 2 and warnings() == 4
    await synth.aclose()


async def test_an_unknown_answer_to_the_check_leaves_it_to_the_voice_list(api, tmp_path) -> None:
    p = _provider(api, tmp_path, voice="Dmitry", keep_warm=0)
    api.odd_check = True  # Inworld changed: the check line is refused for something else
    await p.warmup()  # the voice list still decides, so Inworld is not left out for good
    assert api.checks() == 1 and api.voice_lists() == 1
    api.credits = False
    with pytest.raises(TtsError, match="no credits remaining"):
        await p.warmup()
    assert api.voice_lists() == 1
    await p.aclose()


def test_env_file_from_a_parent_directory(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("# keys\nGOTHIC_TEST_KEY='from file'\nexport GOTHIC_TEST_SET=file\n")
    nested = tmp_path / "tts-service" / "src"
    nested.mkdir(parents=True)
    monkeypatch.delenv("GOTHIC_TEST_KEY", raising=False)
    monkeypatch.setenv("GOTHIC_TEST_SET", "shell")
    assert load_env_file(nested) == tmp_path / ".env"
    assert os.environ["GOTHIC_TEST_KEY"] == "from file"
    assert os.environ["GOTHIC_TEST_SET"] == "shell"  # the shell's value wins
    monkeypatch.delenv("GOTHIC_TEST_KEY")
