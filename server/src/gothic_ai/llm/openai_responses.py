"""OpenAI Responses API backend.

The same code serves an API key and a ChatGPT-plan token ("Sign in with ChatGPT"): only the bearer
token differs. Plan tokens require `store=False` and `stream=True` and reject sampling parameters
(temperature, max_output_tokens, ...), so none are sent.

With `store=False` nothing of a response stays on the server: to continue a turn after a tool call, the
response's own items go back in the next request. Reasoning comes encrypted for that
(`include=["reasoning.encrypted_content"]`); item ids are dropped, since unstored ids cannot be resolved.
Checked on a plan token on 2026-10-11 (`gothic-ai llm-test --probe-tools`): the continuation then needs
no new reasoning at all.

The tools of a request may stay the same while fewer may be called: `allowed` goes as an `allowed_tools`
tool choice, so the request's start (and the prompt cache) does not change with them; `cache_key` goes as
`prompt_cache_key`. The plan token takes both (2026-10-14), though its cache seldom engages.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Sequence
from typing import Any

import openai
from openai import AsyncOpenAI

from .base import (
    Done,
    Item,
    LLMError,
    LLMEvent,
    Message,
    Reasoning,
    TextDelta,
    Tool,
    ToolCall,
    ToolChoice,
    ToolResult,
    Usage,
)

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
        self,
        *,
        instructions: str,
        messages: Sequence[Item],
        tools: list[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        token = await self._token()
        kwargs: dict[str, Any] = {}
        if self._reasoning_effort:
            kwargs["reasoning"] = {"effort": self._reasoning_effort}
            kwargs["include"] = ["reasoning.encrypted_content"]
        if tools:
            kwargs["tools"] = [_tool(t) for t in tools]
            if choice := _tool_choice(tools, tool_choice, allowed):
                kwargs["tool_choice"] = choice
        if cache_key:
            kwargs["prompt_cache_key"] = cache_key
        text = ""
        items: list[Item] = []
        try:
            stream = await self._client.responses.create(
                model=self.model,
                instructions=instructions,
                input=input_items(messages),
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
                        case "response.output_item.done":
                            item = _output_item(event.item)
                            if item is not None:
                                items.append(item)
                                if isinstance(item, ToolCall):
                                    yield item
                        case "response.completed":
                            yield Done(text, tuple(items), _usage(getattr(event.response, "usage", None)))
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


def input_items(messages: Sequence[Item]) -> list[dict[str, Any]]:
    """Request items in Responses API form."""
    out: list[dict[str, Any]] = []
    for m in messages:
        match m:
            case Message():
                out.append({"role": m.role, "content": m.content})
            case ToolCall():
                out.append(
                    {
                        "type": "function_call",
                        "call_id": m.call_id,
                        "name": m.name,
                        "arguments": m.raw or json.dumps(m.arguments, ensure_ascii=False),
                    }
                )
            case ToolResult():
                out.append({"type": "function_call_output", "call_id": m.call_id, "output": m.output})
            case Reasoning():
                out.append(dict(m.data))
    return out


def _output_item(item: Any) -> Item | None:
    """A finished output item as a request item for the next step (None: not needed there)."""
    match getattr(item, "type", None):
        case "function_call":
            return _tool_call(item)
        case "reasoning":
            data = item.model_dump(exclude_none=True)
            data.pop("id", None)
            data.pop("status", None)
            return Reasoning(data) if data.get("encrypted_content") else None
        case "message":
            parts = [getattr(c, "text", "") for c in getattr(item, "content", None) or []]
            text = "".join(p for p in parts if p)
            return Message("assistant", text) if text else None
    return None


def _usage(usage: Any) -> Usage | None:
    if usage is None:
        return None
    details = getattr(usage, "input_tokens_details", None)
    out_details = getattr(usage, "output_tokens_details", None)
    return Usage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        cached_tokens=int(getattr(details, "cached_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        reasoning_tokens=int(getattr(out_details, "reasoning_tokens", 0) or 0),
    )


def _tool_choice(
    tools: list[Tool], choice: ToolChoice | None, allowed: Collection[str] | None
) -> ToolChoice | dict[str, Any] | None:
    """`choice`, or the tools of `allowed` when it leaves some out (none of them: no calls at all)."""
    if choice == "none" or allowed is None or {t.name for t in tools} <= set(allowed):
        return choice
    names = [t.name for t in tools if t.name in allowed]
    if not names:
        return "none"
    return {
        "type": "allowed_tools",
        "mode": "auto",
        "tools": [{"type": "function", "name": n} for n in names],
    }


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
    raw = getattr(item, "arguments", "") or ""
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        log.warning("tool call %s with invalid arguments: %r", name, raw)
        return None
    if not isinstance(args, dict):
        return None
    return ToolCall(name=name, arguments=args, call_id=getattr(item, "call_id", ""), raw=raw)


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
