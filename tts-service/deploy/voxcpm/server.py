"""VoxCPM2 behind a small WebSocket API, for the Gothic AI TTS service. Runs on the GPU server.

    client -> server {"type":"speak","id":"1","text":"...","voice":{"id":"xardas","description":"..."},
                      "style":"say angrily with force"}  (style: optional)
    server -> client binary frames: 16-bit little-endian mono PCM at `sample_rate`, as generated
    server -> client {"type":"done","id":"1","sample_rate":48000,"audio_s":2.1,"first_chunk_s":0.3}
    server -> client {"type":"error","id":"1","detail":"..."}

The model runs in audio.cpp's HTTP server next to this one (run.sh starts both; Q8_0 GGUF, ~3.2 GB of
GPU memory against 7.8 GB in PyTorch). A line is one `POST /v1/audio/speech` with `stream_format: sse`:
`speech.audio.delta{audio: base64 PCM16}` events, then `speech.audio.done`, then `[DONE]`.

A voice is designed once from its description (VoxCPM2 voice design: "(description)text") and kept
as a reference clip; every line is then cloned from that clip, so an npc sounds the same in every
line. A line's style steers how it is said (controllable cloning: "(style)text" with the reference: the
clip gives the timbre, the style the delivery). Requests are served one at a time (one GPU).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("voxcpm-server")

RATE = 48000  # VoxCPM2's AudioVAE decodes to 48 kHz
VOICE_SAMPLE = (
    "Ты вернулся. Хорошо. Времени у нас мало, так что слушай внимательно, я не буду повторять дважды."
)
DEFAULT_DESCRIPTION = "A middle-aged man with an ordinary, slightly rough voice, speaking calmly"


class EngineError(RuntimeError):
    """audio.cpp failed on a request (or is unreachable)."""


class Engine:
    def __init__(self, url: str, model: str, voices_dir: Path, steps: int) -> None:
        self.http = httpx.AsyncClient(base_url=url, timeout=httpx.Timeout(60.0, connect=3.0))
        self.model = model
        self.steps = steps
        self.voices_dir = voices_dir
        voices_dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()  # one generation at a time

    async def wait_ready(self, within_s: float) -> None:
        """Waits for audio.cpp to load the model (run.sh starts both at once)."""
        deadline = time.monotonic() + within_s
        while True:
            try:
                if (await self.http.get("/health")).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise EngineError(f"audio.cpp at {self.http.base_url} is not up after {within_s:.0f}s")
            await asyncio.sleep(1.0)

    async def reference(self, voice_id: str, description: str) -> Path:
        """The cached reference clip of a voice; designed from the description on first use."""
        digest = hashlib.sha1(description.encode()).hexdigest()[:10]
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", voice_id) or "voice"
        path = self.voices_dir / f"{safe}-{digest}.wav"
        if not path.exists():
            t = time.monotonic()
            seed = int(digest, 16) % 2**31
            # a whole clip in one response. The model serves streaming, so audio.cpp's own retry of bad
            # takes is off even here (retry_badcase=false); a take of an odd length is redone instead,
            # as one would be the npc's voice for good. The sample reads in ~6-7 s.
            for take in range(3):
                resp = await self.http.post(
                    "/v1/audio/speech",
                    json={
                        "model": self.model,
                        "input": f"({description}){VOICE_SAMPLE}",
                        "options": {"seed": seed + take, "num_inference_steps": 10, "retry_badcase": False},
                    },
                )
                if resp.status_code != 200:
                    raise EngineError(f"voice design: {resp.status_code} {resp.text[:300]}")
                seconds = (len(resp.content) - 44) / 2 / RATE  # 16-bit mono WAV at RATE
                if 3.0 <= seconds <= 15.0:
                    break
                log.warning("voice design take %d of %s is %.1fs long, again", take + 1, path.name, seconds)
            part = path.with_suffix(".part")
            part.write_bytes(resp.content)
            part.replace(path)
            log.info("designed voice %s in %.1fs: %s", path.name, time.monotonic() - t, description)
        return path

    async def speak(
        self,
        text: str,
        voice_id: str,
        description: str,
        on_chunk: Callable[[bytes], Awaitable[None]],
        style: str = "",
    ) -> float:
        """Generates `text` in the voice, awaiting on_chunk(pcm16 bytes) as audio comes. Returns seconds."""
        style = re.sub(r"[()]", "", style).strip()
        async with self.lock:
            ref = await self.reference(voice_id, description)
            body = {
                "model": self.model,
                "input": f"({style}){text}" if style else text,
                "voice_ref": {"type": "path", "path": str(ref)},
                "response_format": "pcm",
                "stream_format": "sse",
                # a random seed per line, as the PyTorch server had: a fixed one says a line the same way
                # every time, a bad take included
                "options": {
                    "retry_badcase": False,  # required for streaming
                    "seed": random.randrange(2**31),
                    "num_inference_steps": self.steps,
                },
            }
            samples = 0
            async with self.http.stream("POST", "/v1/audio/speech", json=body) as resp:
                if resp.status_code != 200:
                    raise EngineError(f"{resp.status_code} {(await resp.aread())[:300]!r}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    ev = json.loads(data)
                    if ev.get("type") == "speech.audio.delta":
                        pcm = base64.b64decode(ev["audio"])
                        samples += len(pcm) // 2
                        await on_chunk(pcm)
                    elif ev.get("type") == "error":
                        raise EngineError(json.dumps(ev, ensure_ascii=False)[:300])
            return samples / RATE

    async def aclose(self) -> None:
        await self.http.aclose()


async def handle(conn: ServerConnection, engine: Engine) -> None:
    log.info("client %s", conn.remote_address)
    try:
        async for raw in conn:
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                await conn.send(json.dumps({"type": "error", "detail": "malformed message"}))
                continue
            if msg.get("type") != "speak":
                await conn.send(json.dumps({"type": "error", "id": msg.get("id"), "detail": "unknown type"}))
                continue
            req_id = msg.get("id")
            text = str(msg.get("text", "")).strip()
            voice = msg.get("voice") or {}
            if not text:
                await conn.send(json.dumps({"type": "error", "id": req_id, "detail": "empty text"}))
                continue
            started = time.monotonic()
            first: float | None = None

            async def chunk(pcm: bytes, started: float = started) -> None:
                nonlocal first
                if first is None:
                    first = time.monotonic() - started
                await conn.send(pcm)

            try:
                audio_s = await engine.speak(
                    text,
                    str(voice.get("id", "default")),
                    str(voice.get("description") or DEFAULT_DESCRIPTION),
                    chunk,
                    str(msg.get("style") or ""),
                )
            except (EngineError, httpx.HTTPError) as err:  # the model failed on this text, or is down
                log.error("speak failed: %s", err)
                await conn.send(json.dumps({"type": "error", "id": req_id, "detail": str(err)[:300]}))
                continue
            took = time.monotonic() - started
            log.info("%.2fs of audio in %.2fs (first chunk %.2fs): %s", audio_s, took, first or 0, text[:60])
            await conn.send(
                json.dumps(
                    {
                        "type": "done",
                        "id": req_id,
                        "sample_rate": RATE,
                        "audio_s": round(audio_s, 3),
                        "first_chunk_s": round(first or 0.0, 3),
                    }
                )
            )
    except ConnectionClosed:
        pass


async def run(args: argparse.Namespace) -> None:
    engine = Engine(args.engine, args.model, args.voices, args.steps)
    try:
        t = time.monotonic()
        await engine.wait_ready(within_s=300)
        log.info("audio.cpp ready in %.1fs", time.monotonic() - t)

        async def drop(_pcm: bytes) -> None:
            pass

        # warm up: the first generation builds audio.cpp's graphs
        await engine.speak("Проверка.", "default", DEFAULT_DESCRIPTION, drop)
        async with serve(lambda c: handle(c, engine), args.host, args.port, max_size=2**20):
            log.info("listening on ws://%s:%d", args.host, args.port)
            await asyncio.Future()
    finally:
        await engine.aclose()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=47831)
    p.add_argument("--engine", default="http://127.0.0.1:47836", help="audio.cpp server running VoxCPM2")
    p.add_argument("--model", default="voxcpm2", help="the model's id in the audio.cpp config")
    p.add_argument("--voices", default=str(Path(__file__).parent / "voices"))
    p.add_argument("--steps", type=int, default=10, help="flow sampling steps per patch (quality vs speed)")
    args = p.parse_args()
    args.voices = Path(args.voices).resolve()  # absolute: audio.cpp opens the reference clips by path
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
