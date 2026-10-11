import asyncio
import json

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from gothic_tts.providers import TtsError, Voice, make_provider, parse_spec
from gothic_tts.providers.fake import FakeProvider
from gothic_tts.providers.voxcpm import VoxCPM
from gothic_tts.service import TtsService
from gothic_tts.synthesizer import Synthesizer

XARDAS = Voice("xardas", "An old man with a deep voice")


def test_specs() -> None:
    assert parse_spec("openai:voice=onyx,model=gpt-4o-mini-tts") == (
        "openai",
        {"voice": "onyx", "model": "gpt-4o-mini-tts"},
    )
    with pytest.raises(ValueError):
        make_provider("nope")


async def _collect(synth: Synthesizer, text: str) -> tuple[bytes, list[str]]:
    parts: list[bytes] = []
    providers: list[str] = []

    async def chunk(pcm: bytes, p) -> None:
        parts.append(pcm)
        providers.append(p.name)

    await synth.speak(text, XARDAS, chunk)
    return b"".join(parts), providers


async def test_fallback_only_before_audio() -> None:
    primary, fallback = FakeProvider(fail=True), FakeProvider()
    pcm, providers = await _collect(Synthesizer(primary, fallback), "Привет")
    assert pcm and primary.calls and fallback.calls

    class HalfSpoken(FakeProvider):
        async def synthesize(self, text, voice, on_chunk):
            await on_chunk(b"\0\0" * 100)
            raise TtsError("broke mid-line")

    second = FakeProvider()
    with pytest.raises(TtsError, match="mid-line"):
        await _collect(Synthesizer(HalfSpoken(), second), "Привет")
    assert not second.calls  # no voice switch halfway through a line


@pytest.fixture
async def service():
    fake = FakeProvider()
    svc = TtsService(Synthesizer(fake), port=0)
    started = asyncio.Event()
    task = asyncio.create_task(svc.serve_forever(started))
    await started.wait()
    yield svc, fake
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_websocket_speak(service) -> None:
    svc, fake = service
    async with connect(f"ws://127.0.0.1:{svc.port}") as ws:
        await ws.send(
            json.dumps({"type": "speak", "id": "1", "text": "Драконы уже здесь.", "voice": {"id": "x"}})
        )
        start = json.loads(await ws.recv())
        assert start == {"type": "start", "id": "1", "sample_rate": 16000, "provider": "fake"}
        size = 0
        while isinstance(msg := await ws.recv(), bytes):
            size += len(msg)
        done = json.loads(msg)
        assert done["type"] == "done" and done["audio_s"] == pytest.approx(size / 2 / 16000)
        assert fake.calls[0][1] == Voice("x", "", None)

        await ws.send(json.dumps({"type": "speak", "id": "2", "text": "  "}))
        assert json.loads(await ws.recv())["type"] == "error"


async def test_voxcpm_client_against_a_stand_in_server() -> None:
    seen: list[dict] = []

    async def server(ws) -> None:
        async for raw in ws:
            msg = json.loads(raw)
            seen.append(msg)
            if msg["text"] == "сломайся":
                await ws.send(json.dumps({"type": "error", "id": msg["id"], "detail": "bad text"}))
                continue
            await ws.send(b"\1\0" * 480)
            await ws.send(b"\2\0" * 480)
            await ws.send(
                json.dumps({"type": "done", "id": msg["id"], "sample_rate": 48000, "audio_s": 0.02})
            )

    async with serve(server, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        p = VoxCPM(url=f"ws://127.0.0.1:{port}", timeout=2)
        chunks: list[bytes] = []

        async def on_chunk(pcm: bytes) -> None:
            chunks.append(pcm)

        assert await p.synthesize("Привет", XARDAS, on_chunk) == pytest.approx(0.02)
        assert len(chunks) == 2
        with pytest.raises(TtsError, match="bad text"):
            await p.synthesize("сломайся", XARDAS, on_chunk)
        await p.aclose()
    assert seen[0]["voice"] == {"id": "xardas", "description": "An old man with a deep voice"}


async def test_codex_provider_over_a_fake_app_server(tmp_path) -> None:
    import shlex
    import sys
    from pathlib import Path

    from gothic_tts.providers.codex import CodexRealtime

    log = tmp_path / "rpc.log"
    fake = Path(__file__).with_name("fake_codex.py")
    p = CodexRealtime(
        codex=f"{shlex.quote(sys.executable)} {shlex.quote(str(fake))} {shlex.quote(str(log))}",
        transport="websocket",
        timeout=5,
    )
    chunks: list[bytes] = []

    async def on_chunk(pcm: bytes) -> None:
        chunks.append(pcm)

    try:
        seconds = await p.synthesize("Привет", Voice("xardas", "An old man"), on_chunk)
        assert seconds == pytest.approx(6 * 0.02)  # 20 ms per character
        assert len(chunks) == 6
        assert await p.synthesize("Да", Voice("lady", "A young woman"), on_chunk) == pytest.approx(0.04)
    finally:
        await p.aclose()

    calls = [json.loads(line) for line in log.read_text().splitlines()]
    methods = [c["method"] for c in calls]
    assert methods[:4] == ["initialize", "initialized", "thread/start", "thread/realtime/start"]
    assert calls[0]["params"]["capabilities"]["experimentalApi"] is True
    start = calls[3]["params"]
    assert start["outputModality"] == "audio" and start["voice"] == "spruce" and start["version"] == "v3"
    assert "word for word" in start["prompt"]
    # another voice needs another realtime session
    restarts = [c["params"]["voice"] for c in calls if c["method"] == "thread/realtime/start"]
    assert restarts == ["spruce", "juniper"]
