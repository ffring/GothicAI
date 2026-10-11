"""Inworld Realtime TTS-2 by API key (`INWORLD_API_KEY`, the Base64 key from the Inworld portal).

An npc's voice is designed once from its description (Voice Design), published to the workspace's
voice library and remembered in a local file, so every line of that npc has the same voice. A library
voice can be used instead: `voice=Dmitry` for every npc, or a request's `preset`; its description then
steers the delivery (`instruction`, TTS-2 only, Flash has no steering).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from .base import ChunkCallback, TtsError, Voice

log = logging.getLogger(__name__)

DEFAULT_CACHE = Path.home() / ".local/share/gothic-ai/inworld-voices.json"
# Voice Design shapes the voice on a 1-30 s script in the voice's language
PREVIEW_TEXT = (
    "Ты снова здесь. Значит, Иннос ещё не отвернулся от тебя. Слушай внимательно, у нас мало времени."
)


class _UnknownVoice(TtsError):
    """The voice is not in the workspace (deleted, or the API key is of another workspace)."""


class InworldTTS:
    name = "inworld"

    def __init__(
        self,
        model: str = "inworld-tts-2",
        voice: str | None = None,
        language: str = "ru-RU",
        rate: str | int = 24000,
        delivery: str | None = None,
        cache: str | Path = DEFAULT_CACHE,
        base_url: str = "https://api.inworld.ai",
        api_key_env: str = "INWORLD_API_KEY",
        timeout: str | float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.voice = voice
        self.language = language
        self.rate = int(rate)
        self.delivery = delivery  # STABLE / BALANCED / CREATIVE (TTS-2 only)
        self.cache = Path(cache)
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        # lines come tens of seconds apart; httpx drops an idle connection after 5 s, and a new TLS
        # connection to Inworld added ~1 s to the next line's first audio
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout), connect=3.0),
            transport=transport,
            limits=httpx.Limits(keepalive_expiry=120),
        )
        self._designing = asyncio.Lock()

    @property
    def steerable(self) -> bool:
        return "flash" not in self.model

    def _headers(self) -> dict[str, str]:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise TtsError(f"no API key: set {self.api_key_env}")
        return {"Authorization": f"Basic {key}"}

    async def warmup(self) -> None:
        """Checks the key (and opens the connection) with a cheap voice list request."""
        lang = self.language.split("-")[0].lower()
        await self._call("GET", "/tts/v1/voices", params={"filter": f"language={lang}"})

    async def synthesize(self, text: str, voice: Voice, on_chunk: ChunkCallback) -> float:
        preset = voice.preset or self.voice
        if preset:
            instruction = voice.description if self.steerable else ""
            return await self._stream(text, preset, instruction, on_chunk)
        voice_id = await self._designed(voice)
        try:
            return await self._stream(text, voice_id, "", on_chunk)
        except _UnknownVoice:
            log.warning("inworld: designed voice %s of %s is gone, designing again", voice_id, voice.id)
            self._forget(voice.id)
            return await self._stream(text, await self._designed(voice), "", on_chunk)

    async def _stream(self, text: str, voice_id: str, instruction: str, on_chunk: ChunkCallback) -> float:
        body: dict[str, Any] = {
            "text": text,
            "voiceId": voice_id,
            "modelId": self.model,
            "language": self.language,
            "audioConfig": {"audioEncoding": "PCM", "sampleRateHertz": self.rate},
        }
        if instruction:
            body["instruction"] = instruction
        if self.delivery and self.steerable:
            body["deliveryMode"] = self.delivery.upper()
        size = 0
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/tts/v1/voice:stream", headers=self._headers(), json=body
            ) as resp:
                if resp.status_code != 200:
                    _raise(resp.status_code, (await resp.aread()).decode(errors="replace"))
                carry = b""
                async for line in resp.aiter_lines():  # NDJSON, one result per line
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if failure := msg.get("error"):
                        if isinstance(failure, dict):
                            failure = failure.get("message", failure)
                        raise TtsError(f"inworld: {failure}")
                    data = carry + base64.b64decode(msg.get("result", {}).get("audioContent", ""))
                    cut = len(data) // 2 * 2  # whole samples only
                    carry = data[cut:]
                    if cut:
                        size += cut
                        await on_chunk(data[:cut])
        except httpx.HTTPError as err:
            raise TtsError(f"inworld: {_describe(err)}") from err
        except ValueError as err:  # a line that is not JSON or not Base64
            raise TtsError(f"inworld: bad stream: {err}") from err
        return size / 2 / self.rate

    async def _designed(self, voice: Voice) -> str:
        """The library voice made for `voice` (designed and published on its first line)."""
        async with self._designing:
            entry = self._load().get(voice.id, {})
            if entry.get("description") == voice.description and entry.get("language") == self.language:
                return str(entry["voice_id"])
            if not voice.description:
                raise TtsError(f"inworld: voice {voice.id!r} has no description to design it from")
            design = await self._call(
                "POST",
                "/voices/v1/voices:design",
                json={
                    "languageCode": self.language,
                    "designPrompt": voice.description[:1000],
                    "previewText": PREVIEW_TEXT,
                    "voiceDesignConfig": {"numberOfSamples": 1},
                },
            )
            try:
                preview = design["previewVoices"][0]["voiceId"]
            except (KeyError, IndexError, TypeError) as err:
                raise TtsError(f"inworld: voice design returned no voice: {design}") from err
            published = await self._call(
                "POST",
                f"/voices/v1/voices/{preview}:publish",
                json={
                    "displayName": f"gothic-ai {voice.id}",
                    "description": voice.description[:500],
                    "tags": ["gothic-ai"],
                },
            )
            voice_id = str(published.get("voiceId") or preview)
            log.info("inworld: designed voice %s for %s", voice_id, voice.id)
            entries = self._load()
            entries[voice.id] = {
                "voice_id": voice_id,
                "description": voice.description,
                "language": self.language,
            }
            self._save(entries)
            return voice_id

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = await self._client.request(
                method, f"{self.base_url}{path}", headers=self._headers(), **kwargs
            )
        except httpx.HTTPError as err:
            raise TtsError(f"inworld: {_describe(err)}") from err
        if resp.status_code != 200:
            _raise(resp.status_code, resp.text)
        return resp.json()

    def _load(self) -> dict[str, dict[str, str]]:
        try:
            return json.loads(self.cache.read_text())
        except FileNotFoundError:
            return {}
        except ValueError:
            log.warning("inworld: %s is not JSON, starting over", self.cache)
            return {}

    def _save(self, entries: dict[str, dict[str, str]]) -> None:
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1))
        tmp.replace(self.cache)

    def _forget(self, key: str) -> None:
        entries = self._load()
        if entries.pop(key, None) is not None:
            self._save(entries)

    async def aclose(self) -> None:
        await self._client.aclose()


def _describe(err: Exception) -> str:
    return f"{type(err).__name__}: {err}" if str(err) else type(err).__name__  # timeouts have no text


def _raise(status: int, detail: str) -> None:
    detail = detail[:300]
    if status in (401, 403):
        raise TtsError(f"inworld rejected the API key ({status}): {detail}")
    if status == 404 and "Unknown voice" in detail:
        raise _UnknownVoice(f"inworld: {detail}")
    raise TtsError(f"inworld returned {status}: {detail}")
