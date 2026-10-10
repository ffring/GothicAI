"""OpenAI Responses API backend.

The same code serves an API key and a ChatGPT-plan token ("Sign in with ChatGPT"): only the bearer
token differs. Plan tokens require `store=False` and `stream=True` and reject sampling parameters
(temperature, max_output_tokens, ...), so none are sent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import openai
from openai import AsyncOpenAI

from .base import Done, LLMError, LLMEvent, Message, TextDelta

TokenProvider = Callable[[], Awaitable[str]]


class OpenAIResponsesBackend:
    def __init__(
        self,
        model: str,
        token: TokenProvider,
        *,
        reasoning_effort: str | None = "low",
        base_url: str = "https://api.openai.com/v1",
        name: str = "openai",
    ) -> None:
        self.name = name
        self.model = model
        self._token = token
        self._reasoning_effort = reasoning_effort
        self._base_url = base_url

    async def stream(self, *, instructions: str, messages: list[Message]) -> AsyncIterator[LLMEvent]:
        token = await self._token()
        client = AsyncOpenAI(
            api_key=token,
            base_url=self._base_url,
            organization=None,
            project=None,
            # plan quota errors (429) must surface immediately, not be retried
            max_retries=0,
        )
        kwargs = {}
        if self._reasoning_effort:
            kwargs["reasoning"] = {"effort": self._reasoning_effort}
        text = ""
        try:
            stream = await client.responses.create(
                model=self.model,
                instructions=instructions,
                input=[{"role": m.role, "content": m.content} for m in messages],
                store=False,
                stream=True,
                **kwargs,
            )
            async for event in stream:
                match event.type:
                    case "response.output_text.delta":
                        text += event.delta
                        yield TextDelta(event.delta)
                    case "response.completed":
                        yield Done(text)
                        return
                    case "response.failed" | "error":
                        raise LLMError(f"{self.name}: response failed: {_event_error(event)}", retryable=True)
                    case "response.incomplete":
                        raise LLMError(f"{self.name}: response incomplete", retryable=True)
            raise LLMError(f"{self.name}: stream ended before completion", retryable=True)
        except openai.APIError as err:
            raise api_error(self.name, err) from err
        finally:
            await client.close()


PLAN_LIMIT = "лимит подписки ChatGPT для сторонних приложений исчерпан, дождитесь сброса"


def api_error(name: str, err: openai.APIError) -> LLMError:
    """Map SDK errors (raised before or inside the stream) to LLMError; quota errors are not retryable."""
    if "usage limit" in str(err.message).lower() or getattr(err, "code", None) in (
        "subscription_sharing_usage_limit_exceeded",
        "usage_limit_reached",
    ):
        return LLMError(f"{name}: {PLAN_LIMIT}")
    match err:
        case openai.RateLimitError():
            return LLMError(f"{name}: rate limit reached")
        case openai.AuthenticationError():
            return LLMError(f"{name}: authentication failed, sign in again")
        case openai.APIStatusError():
            return LLMError(f"{name}: HTTP {err.status_code}: {err.message}")
        case openai.APIConnectionError():
            return LLMError(f"{name}: connection failed", retryable=True)
    return LLMError(f"{name}: {err.message}", retryable=True)


def _event_error(event: object) -> str:
    response = getattr(event, "response", None)
    error = (
        getattr(response, "error", None) or getattr(event, "error", None) or getattr(event, "message", None)
    )
    return str(error) if error else "unknown error"
