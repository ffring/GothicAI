"""Run a recorded turn again: the same request to another model, prompt or tool set, without the game.

The first step's request comes from the trace. When the model calls a tool, the recorded result of the
same call (name and arguments) is given back; a call the original turn did not make goes to `resolve`
(e.g. the checks against the recorded snapshot), or the turn is marked as diverged there.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from contextlib import aclosing
from typing import Any

from . import npc_tools
from .dialogue import (
    _FIRST_SENTENCE,
    QUICK_HINT,
    REACT_HINT,
    REACTION_S,
    SOUND_SAID,
    first_words,
    first_words_outcome,
    is_pure_sound,
    paragraph_end,
)
from .llm import Done, Item, LLMBackend, LLMError, Message, TextDelta, ToolCall, ToolResult
from .trace import CallTrace, item_from_json
from .turn import MAX_STEPS, ToolOutcome, go_on, tool_choice

Resolver = Callable[[ToolCall, dict[str, Any]], ToolOutcome | None]


async def replay_first_words(
    llm: LLMBackend,
    record: dict[str, Any],
    instructions: str,
    *,
    hint: str = REACT_HINT,
    runs: int = 1,
    temperature: float = 0.6,
    previous: Sequence[str] = (),
) -> dict[str, Any]:
    """Replay only the quick first reaction, with independent repetition history for each run."""
    added = record["steps"][0]["added"]
    situation_at = next(i for i in range(len(added) - 1, -1, -1) if added[i].get("role") == "developer")
    situation = next(
        (e["situation"] for e in record.get("events", []) if e["kind"] == "first_words" and "situation" in e),
        None,
    )
    if situation is None:
        situation = added[situation_at]["content"].split(SOUND_SAID.split("{}", 1)[0], 1)[0]
        situation = situation.removesuffix("\n" + QUICK_HINT).rstrip()
    history = [item for data in added[:situation_at] if (item := item_from_json(data)) is not None]
    phrase = record["trigger"]["text"]
    request = [*history, Message("developer", situation + "\n" + hint), Message("user", phrase)]
    results = []
    for run in range(runs):
        started = time.monotonic()
        text = ""
        outcome = "said"
        error = ""
        try:
            async with (
                asyncio.timeout(REACTION_S),
                aclosing(
                    llm.stream(instructions=instructions, messages=request, tools=[], temperature=temperature)
                ) as events,
            ):
                async for event in events:
                    if isinstance(event, TextDelta):
                        text += event.text
                        if _FIRST_SENTENCE.search(text) or paragraph_end(text) is not None:
                            break
        except TimeoutError:
            outcome = "late"
        except LLMError as err:
            outcome, error = "error", str(err)
        words, _ = first_words(text)
        if outcome == "said":
            outcome = first_words_outcome(words, previous[run] if run < len(previous) else "")
        result = {
            "text": text,
            "words": words,
            "outcome": outcome,
            "pure_sound": is_pure_sound(words),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }
        if error:
            result["error"] = error
        results.append(result)
    return {
        "turn": record.get("id"),
        "npc": record["npc"],
        "phrase": phrase,
        "situation": situation,
        "hint": hint,
        "temperature": temperature,
        "runs": results,
    }


async def replay(
    llm: LLMBackend,
    record: dict[str, Any],
    instructions: str,
    resolve: Resolver | None = None,
) -> dict[str, Any]:
    tools = [t for t in npc_tools.TOOLS if t.name in record.get("tools", [])]
    steps = record.get("steps") or []
    if not steps:
        raise ValueError(f"turn {record.get('id')} has no steps")
    request: list[Item] = [i for d in steps[0]["added"] if (i := item_from_json(d)) is not None]
    recorded = [CallTrace(**c) for s in steps for c in s.get("calls", [])]
    out: list[dict[str, Any]] = []
    said = ""
    diverged = False
    for step in range(MAX_STEPS):
        started = time.monotonic()
        first: float | None = None
        done: Done | None = None
        async for event in llm.stream(
            instructions=instructions, messages=request, tools=tools, tool_choice=tool_choice(step)
        ):
            if isinstance(event, TextDelta) and first is None:
                first = time.monotonic() - started
            elif isinstance(event, Done):
                done = event
        assert done is not None
        said += done.text
        calls = [i for i in done.items if isinstance(i, ToolCall)]
        outcomes: list[ToolOutcome] = []
        for call in calls:
            match = next((rc for rc in recorded if rc.matches(call)), None)
            outcome = ToolOutcome(match.result, match.outcome) if match else None  # type: ignore[arg-type]
            if outcome is None and resolve is not None:
                outcome = resolve(call, record)
            if outcome is None:
                diverged = True
                outcome = ToolOutcome("(этот вызов при воспроизведении не выполняется)", "ignored")
            outcomes.append(outcome)
        out.append(
            {
                "text": done.text.strip(),
                "calls": [
                    {"name": c.name, "arguments": c.arguments, "outcome": o.status}
                    for c, o in zip(calls, outcomes, strict=True)
                ],
                "first_token_s": round(first or 0.0, 2),
                "total_s": round(time.monotonic() - started, 2),
            }
        )
        if not go_on(outcomes, bool(said.strip())):
            break
        request += [
            *done.items,
            *(ToolResult(c.call_id, o.output) for c, o in zip(calls, outcomes, strict=True)),
        ]
    return {
        "turn": record.get("id"),
        "npc": record.get("npc"),
        "trigger": record.get("trigger"),
        "original": [
            {"text": s.get("text", "").strip(), "calls": [c["name"] for c in s.get("calls", [])]}
            for s in steps
        ],
        "replay": out,
        "diverged": diverged,
    }
