"""Run a recorded turn again: the same request to another model, prompt or tool set, without the game.

The first step's request comes from the trace. When the model calls a tool, the recorded result of the
same call (name and arguments) is given back; a call the original turn did not make goes to `resolve`
(e.g. the checks against the recorded snapshot), or the turn is marked as diverged there.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from . import npc_tools
from .dialogue import MoodTags, to_game_text
from .llm import Done, Item, LLMBackend, TextDelta, ToolCall, ToolResult
from .trace import CallTrace, item_from_json
from .turn import MAX_STEPS, ToolOutcome, go_on, tool_choice, tool_output
from .vanilla import VanillaQuests

Resolver = Callable[[ToolCall, dict[str, Any]], ToolOutcome | None]


async def replay(
    llm: LLMBackend,
    record: dict[str, Any],
    instructions: str,
    resolve: Resolver | None = None,
    *,
    after_step: int = 0,
) -> dict[str, Any]:
    tools = [t for t in [*npc_tools.TOOLS, *VanillaQuests.all_tools] if t.name in record.get("tools", [])]
    steps = record.get("steps") or []
    if not steps:
        raise ValueError(f"turn {record.get('id')} has no steps")
    if not 0 <= after_step < len(steps):
        raise ValueError(f"after_step must be between 0 and {len(steps) - 1}")
    request: list[Item] = [i for d in steps[0]["added"] if (i := item_from_json(d)) is not None]
    recorded = [CallTrace(**c) for s in steps for c in s.get("calls", [])]
    out: list[dict[str, Any]] = []
    said = " ".join(e["text"] for e in record.get("events", []) if e.get("kind") == "first_words")
    for n in range(after_step):
        previous, following = steps[n], steps[n + 1]
        if not previous.get("silent"):
            said = _said(said, previous.get("text", ""))
        added = [i for d in following["added"] if (i := item_from_json(d)) is not None]
        calls = {c["call_id"]: CallTrace(**c) for c in previous.get("calls", [])}
        for i, item in enumerate(added):
            if isinstance(item, ToolResult) and (call := calls.get(item.call_id)) is not None:
                outcome = ToolOutcome(call.result, call.outcome)  # type: ignore[arg-type]
                output = tool_output(outcome, said) if not previous.get("silent") else outcome.output
                added[i] = ToolResult(item.call_id, output)
        # The conversational model starts a whole new request after the quick model's fight line.
        request = added if previous.get("llm") else [*request, *added]
    said_before = said
    initial_tool_results = [i.output for i in request if isinstance(i, ToolResult)]
    silent = bool(steps[after_step].get("silent"))
    first_step = after_step - sum(bool(s.get("llm")) for s in steps[:after_step])
    diverged = False
    for step in range(first_step, MAX_STEPS):
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
        if not silent:
            said = _said(said, done.text)
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
        outputs = [tool_output(o, said) if not silent else o.output for o in outcomes]
        out.append(
            {
                "text": done.text.strip(),
                "silent": silent,
                "calls": [
                    {"name": c.name, "arguments": c.arguments, "outcome": o.status, "output": output}
                    for c, o, output in zip(calls, outcomes, outputs, strict=True)
                ],
                "first_token_s": round(first or 0.0, 2),
                "total_s": round(time.monotonic() - started, 2),
            }
        )
        if not go_on(outcomes, bool(said.strip())):
            break
        request += [
            *done.items,
            *(ToolResult(c.call_id, output) for c, output in zip(calls, outputs, strict=True)),
        ]
    return {
        "turn": record.get("id"),
        "npc": record.get("npc"),
        "trigger": record.get("trigger"),
        "after_step": after_step,
        "said_before": said_before,
        "initial_tool_results": initial_tool_results,
        "original": [
            {"text": s.get("text", "").strip(), "calls": [c["name"] for c in s.get("calls", [])]}
            for s in steps
        ],
        "replay": out,
        "diverged": diverged,
    }


def _said(previous: str, text: str) -> str:
    tags = MoodTags()
    words = "".join(p for p in [*tags.feed(text), *tags.flush()] if isinstance(p, str))
    return " ".join(p for p in (previous, to_game_text(words)) if p)
