"""VoxCPM2 behind a small WebSocket API, for the Gothic AI TTS service. Runs on the GPU server.

    client -> server {"type":"speak","id":"1","text":"...","voice":{"id":"xardas","description":"..."},
                      "style":"say angrily with force"}  (style: optional)
    server -> client binary frames: 16-bit little-endian mono PCM at `sample_rate`, as generated
    server -> client {"type":"done","id":"1","sample_rate":48000,"audio_s":2.1,"first_chunk_s":0.05}
    server -> client {"type":"error","id":"1","detail":"..."}

A voice is designed once from its description (VoxCPM2 voice design: "(description)text") and kept
as a reference clip; every line is then cloned from that clip, so an npc sounds the same in every
line. A line's style steers how it is said (controllable cloning: "(style)text" with the reference: the
clip gives the timbre, the style the delivery). Requests are served one at a time (one GPU).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from voxcpm import VoxCPM
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("voxcpm-server")

VOICE_SAMPLE = (
    "Ты вернулся. Хорошо. Времени у нас мало, так что слушай внимательно, я не буду повторять дважды."
)
DEFAULT_DESCRIPTION = "A middle-aged man with an ordinary, slightly rough voice, speaking calmly"


class Engine:
    def __init__(self, voices_dir: Path, steps: int) -> None:
        t = time.monotonic()
        self.model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False)
        self.rate = self.model.tts_model.sample_rate
        self.steps = steps
        self.voices_dir = voices_dir
        voices_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()  # one generation at a time
        log.info("VoxCPM2 loaded in %.1fs, %d Hz", time.monotonic() - t, self.rate)

    def reference(self, voice_id: str, description: str) -> Path:
        """The cached reference clip of a voice; designed from the description on first use."""
        digest = hashlib.sha1(description.encode()).hexdigest()[:10]
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", voice_id) or "voice"
        path = self.voices_dir / f"{safe}-{digest}.wav"
        if not path.exists():
            t = time.monotonic()
            torch.manual_seed(int(digest, 16) % 2**31)
            wav = self.model.generate(
                text=f"({description}){VOICE_SAMPLE}", cfg_value=2.0, inference_timesteps=10
            )
            sf.write(path, wav, self.rate)
            log.info("designed voice %s in %.1fs: %s", path.name, time.monotonic() - t, description)
        return path

    def speak(self, text: str, voice_id: str, description: str, on_chunk, style: str = "") -> float:
        """Generates `text` in the voice, calling on_chunk(pcm16 bytes) as audio comes. Returns seconds."""
        style = re.sub(r"[()]", "", style).strip()
        with self.lock:
            ref = self.reference(voice_id, description)
            samples = 0
            for chunk in self.model.generate_streaming(
                text=f"({style}){text}" if style else text,
                reference_wav_path=str(ref),
                cfg_value=2.0,
                inference_timesteps=self.steps,
            ):
                pcm = (np.clip(chunk, -1.0, 1.0) * 32767.0).astype("<i2")
                samples += len(pcm)
                on_chunk(pcm.tobytes())
            return samples / self.rate


async def handle(conn: ServerConnection, engine: Engine) -> None:
    loop = asyncio.get_running_loop()
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
            queue: asyncio.Queue[bytes | None] = asyncio.Queue()
            started = time.monotonic()
            job = loop.run_in_executor(
                None,
                engine.speak,
                text,
                str(voice.get("id", "default")),
                str(voice.get("description") or DEFAULT_DESCRIPTION),
                lambda pcm, q=queue: loop.call_soon_threadsafe(q.put_nowait, pcm),
                str(msg.get("style") or ""),
            )
            job.add_done_callback(lambda _f, q=queue: loop.call_soon_threadsafe(q.put_nowait, None))
            first = None
            while (pcm := await queue.get()) is not None:
                if first is None:
                    first = time.monotonic() - started
                await conn.send(pcm)
            try:
                audio_s = await job
            except Exception as err:  # the model failed on this text
                log.exception("speak failed")
                await conn.send(json.dumps({"type": "error", "id": req_id, "detail": str(err)[:300]}))
                continue
            took = time.monotonic() - started
            log.info("%.2fs of audio in %.2fs (first chunk %.2fs): %s", audio_s, took, first or 0, text[:60])
            await conn.send(
                json.dumps(
                    {
                        "type": "done",
                        "id": req_id,
                        "sample_rate": engine.rate,
                        "audio_s": round(audio_s, 3),
                        "first_chunk_s": round(first or 0.0, 3),
                    }
                )
            )
    except ConnectionClosed:
        pass


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=47831)
    p.add_argument("--voices", default=str(Path(__file__).parent / "voices"))
    p.add_argument("--steps", type=int, default=10, help="diffusion steps per token (quality vs speed)")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname).1s %(name)s: %(message)s")
    engine = Engine(Path(args.voices), args.steps)
    # warm up: the first generation compiles kernels
    await asyncio.get_running_loop().run_in_executor(
        None, engine.speak, "Проверка.", "default", DEFAULT_DESCRIPTION, lambda _pcm: None
    )
    async with serve(lambda c: handle(c, engine), args.host, args.port, max_size=2**20):
        log.info("listening on ws://%s:%d", args.host, args.port)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
