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
from .llm import Done, Item, LLMBackend, TextDelta, ToolCall, ToolResult
from .trace import CallTrace, item_from_json
from .turn import MAX_STEPS, ToolOutcome, go_on, tool_choice

Resolver = Callable[[ToolCall, dict[str, Any]], ToolOutcome | None]


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
