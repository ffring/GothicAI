"""Provider registry. A provider is chosen by a spec string: `name` or `name:key=value,key=value`,
e.g. `voxcpm` (self-hosted VoxCPM2), `codex:voice=spruce` (Codex's realtime voice, ChatGPT plan),
`openai:model=gpt-4o-mini-tts,voice=onyx` or `inworld:model=inworld-tts-2` (API keys).
Models are never run on this machine: self-hosted ones live on a GPU server.
"""

from __future__ import annotations

from collections.abc import Callable

from .base import TtsError, TtsProvider, Voice

__all__ = ["PROVIDERS", "TtsError", "TtsProvider", "Voice", "make_provider", "parse_spec"]


def _voxcpm(**opts: str) -> TtsProvider:
    from .voxcpm import VoxCPM

    return VoxCPM(**opts)


def _codex(**opts: str) -> TtsProvider:
    from .codex import CodexRealtime

    return CodexRealtime(**opts)


def _openai(**opts: str) -> TtsProvider:
    from .remote import OpenAISpeech

    return OpenAISpeech(**opts)


def _inworld(**opts: str) -> TtsProvider:
    from .inworld import InworldTTS

    return InworldTTS(**opts)


def _fake(**opts: str) -> TtsProvider:
    from .fake import FakeProvider

    return FakeProvider(**opts)


PROVIDERS: dict[str, Callable[..., TtsProvider]] = {
    "voxcpm": _voxcpm,
    "codex": _codex,
    "openai": _openai,
    "inworld": _inworld,
    "fake": _fake,
}


def parse_spec(spec: str) -> tuple[str, dict[str, str]]:
    name, _, rest = spec.partition(":")
    opts: dict[str, str] = {}
    for part in filter(None, rest.split(",")):
        key, sep, value = part.partition("=")
        if not sep:
            raise ValueError(f"bad provider option {part!r} in {spec!r}, expected key=value")
        opts[key.strip()] = value.strip()
    return name.strip(), opts


def make_provider(spec: str) -> TtsProvider:
    name, opts = parse_spec(spec)
    if name not in PROVIDERS:
        raise ValueError(f"unknown provider {name!r}, known: {', '.join(PROVIDERS)}")
    try:
        return PROVIDERS[name](**opts)
    except TypeError as err:
        raise ValueError(f"bad options for {name}: {err}") from err
