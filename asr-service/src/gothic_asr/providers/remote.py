"""External providers over HTTP."""

from __future__ import annotations

import os

import httpx
import numpy as np

from ..audio import to_wav
from .base import AsrError, Transcript


class OpenAICompatible:
    """`POST {base_url}/audio/transcriptions`: OpenAI, Groq and other OpenAI-compatible services.

    The API key is read from the environment variable named by `api_key_env`.
    """

    name = "openai"
    streaming = False
    languages = None

    def __init__(
        self,
        model: str = "gpt-4o-mini-transcribe",
        base_url: str = "https://api.openai.com/v1",
        api_key_env: str = "OPENAI_API_KEY",
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    def _key(self) -> str:
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise AsrError(f"no API key: set {self.api_key_env}")
        return key

    async def warmup(self) -> None:
        self._key()

    async def transcribe(self, audio: np.ndarray, lang: str) -> Transcript:
        try:
            resp = await self._client.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._key()}"},
                files={"file": ("speech.wav", to_wav(audio), "audio/wav")},
                data={"model": self.model, "language": lang, "response_format": "json"},
            )
        except httpx.HTTPError as err:
            raise AsrError(f"{self.base_url}: {type(err).__name__}: {err}") from err
        if resp.status_code != 200:
            detail = resp.text[:300]
            try:
                detail = resp.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                pass
            raise AsrError(f"{self.base_url} returned {resp.status_code}: {detail}")
        return Transcript(str(resp.json().get("text", "")).strip(), self.name)

    async def aclose(self) -> None:
        await self._client.aclose()
