"""`gothic-tts`: the text-to-speech service and a benchmark for providers."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from .audio import to_wav
from .providers import PROVIDERS, TtsError, TtsProvider, Voice, make_provider
from .service import TtsService
from .synthesizer import Synthesizer

log = logging.getLogger("gothic_tts")

# self-hosted VoxCPM2. No fallback by default: without a voice the game shows subtitles, which is
# safer than `codex` (its conversational voice paraphrases and improvises, see providers/codex.py)
DEFAULT_PROVIDER = os.environ.get("GOTHIC_TTS_PROVIDER", "voxcpm")
DEFAULT_FALLBACK = os.environ.get("GOTHIC_TTS_FALLBACK", "none")


def _synthesizer(args: argparse.Namespace) -> Synthesizer:
    fallback = make_provider(args.fallback) if args.fallback and args.fallback != "none" else None
    return Synthesizer(make_provider(args.provider), fallback)


async def serve(args: argparse.Namespace) -> int:
    synth = _synthesizer(args)
    if not args.no_warmup:
        await synth.warmup()
    try:
        await TtsService(synth, args.host, args.port).serve_forever()
    finally:
        await synth.aclose()
    return 0


async def say(args: argparse.Namespace) -> int:
    """Speak lines and report latency; --out keeps the audio."""
    synth = _synthesizer(args)
    voice = Voice(args.voice_id, args.voice or "", args.preset)
    try:
        await synth.warmup()
        for text in args.text:
            parts: list[bytes] = []
            rate = 0

            async def chunk(pcm: bytes, p: TtsProvider, parts: list[bytes] = parts) -> None:
                nonlocal rate
                rate = p.rate
                parts.append(pcm)

            s = await synth.speak(text, voice, chunk)
            print(
                f"[{s.provider}] {s.audio_s:.2f}s audio, first chunk {s.first_chunk_s:.2f}s, "
                f"done {s.latency_s:.2f}s (RTF {s.latency_s / max(s.audio_s, 0.01):.2f}): {text}"
            )
            if args.out:
                out = (
                    args.out
                    if len(args.text) == 1
                    else args.out.replace(".wav", f"_{args.text.index(text)}.wav")
                )
                _write(out, to_wav(b"".join(parts), rate))
    except TtsError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    finally:
        await synth.aclose()
    return 0


def _write(path: str, data: bytes) -> None:
    Path(path).write_bytes(data)


def _add_provider_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help=f"provider spec: name[:key=value,...]; names: {', '.join(PROVIDERS)}",
    )
    p.add_argument("--fallback", default=DEFAULT_FALLBACK, help="fallback provider spec, or none")


def main() -> None:
    parser = argparse.ArgumentParser(prog="gothic-tts")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the WebSocket service")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=47820)
    p.add_argument("--no-warmup", action="store_true")
    _add_provider_args(p)

    p = sub.add_parser("say", help="speak lines and measure latency")
    p.add_argument("text", nargs="+")
    p.add_argument("--voice-id", default="test")
    p.add_argument("--voice", help="voice description (VoxCPM voice design)")
    p.add_argument("--preset", help="preset voice name for preset-voice APIs")
    p.add_argument("--out", help="write the audio to this wav file (name_N.wav for several lines)")
    _add_provider_args(p)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        match args.command:
            case "serve":
                code = asyncio.run(serve(args))
            case "say":
                code = asyncio.run(say(args))
            case _:
                code = 2
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)
