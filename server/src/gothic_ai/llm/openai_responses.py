"""OpenAI Responses API backend.

The same code serves an API key and a ChatGPT-plan token ("Sign in with ChatGPT"): only the bearer
token differs. Plan tokens require `store=False` and `stream=True` and reject sampling parameters
(temperature, max_output_tokens, ...), so none are sent.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

import openai
from openai import AsyncOpenAI

from .base import Done, LLMError, LLMEvent, Message, TextDelta, Tool, ToolCall

log = logging.getLogger(__name__)

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
        # one client for the whole run: keeps the HTTPS connection warm between replies;
        # the (refreshable) bearer token is set per request
        self._client = AsyncOpenAI(
            api_key="per-request",
            base_url=base_url,
            organization=None,
            project=None,
            # plan quota errors (429) must surface immediately, not be retried
            max_retries=0,
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def stream(
        self, *, instructions: str, messages: list[Message], tools: list[Tool] | None = None
    ) -> AsyncIterator[LLMEvent]:
        token = await self._token()
        kwargs = {}
        if self._reasoning_effort:
            kwargs["reasoning"] = {"effort": self._reasoning_effort}
        if tools:
            kwargs["tools"] = [_tool(t) for t in tools]
        text = ""
        try:
            stream = await self._client.responses.create(
                model=self.model,
                instructions=instructions,
                input=[{"role": m.role, "content": m.content} for m in messages],
                store=False,
                stream=True,
                extra_headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
            async with stream:  # always release the pooled connection
                async for event in stream:
                    match event.type:
                        case "response.output_text.delta":
                            text += event.delta
                            yield TextDelta(event.delta)
                        case "response.output_item.done" if event.item.type == "function_call":
                            if call := _tool_call(event.item):
                                yield call
                        case "response.completed":
                            yield Done(text)
                            return
                        case "response.failed" | "error":
                            raise LLMError(
                                f"{self.name}: response failed: {_event_error(event)}", retryable=True
                            )
                        case "response.incomplete":
                            raise LLMError(f"{self.name}: response incomplete", retryable=True)
                raise LLMError(f"{self.name}: stream ended before completion", retryable=True)
        except openai.APIError as err:
            raise api_error(self.name, err) from err


def _tool(tool: Tool) -> dict:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        "strict": True,
    }


def _tool_call(item: object) -> ToolCall | None:
    name = getattr(item, "name", "")
    try:
        args = json.loads(getattr(item, "arguments", "") or "{}")
    except json.JSONDecodeError:
        log.warning("tool call %s with invalid arguments: %r", name, getattr(item, "arguments", None))
        return None
    if not isinstance(args, dict):
        return None
    return ToolCall(name=name, arguments=args, call_id=getattr(item, "call_id", ""))


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
