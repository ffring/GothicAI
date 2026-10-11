"""Wire format of the service. Questions follow the System One shape (Jev / strands-decider):

    {"react":    {"type": "choice", "instructions": "...", "criteria": {"attack": "...", ...}},
     "is_enemy": {"type": "noul",   "instructions": "...", "criteria": {"true": "...", "false": "..."}},
     "attitude": {"type": "score",  "instructions": "...", "criteria": ["low", ..., "high"]}}

Answers come back in the same shape strands-decider returns: `choice`/`probabilities`/`confidence`,
`noul` (P(true)), `score`/`probabilities`/`confidence`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Lane(StrEnum):
    URGENT = "urgent"  # the player is waiting: small batches, served first
    BACKGROUND = "background"  # everything else: batched up to a time cap, within a GPU-time budget


MAX_DEADLINE_MS = 120_000
MAX_CHOICE_OPTIONS = 64


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class DecideRequest:
    id: str
    lane: Lane
    priority: float  # higher is served first within a lane
    deadline_ms: int  # from arrival; past it the request is dropped as `expired`
    state: str | dict | list
    questions: dict[str, dict]


def parse_decide(msg: dict[str, Any]) -> DecideRequest:
    rid = msg.get("id")
    if not isinstance(rid, str) or not rid:
        raise ProtocolError("decide needs a non-empty string id")
    try:
        lane = Lane(msg.get("lane", Lane.BACKGROUND))
    except ValueError:
        raise ProtocolError(f"unknown lane {msg.get('lane')!r}") from None
    deadline = msg.get("deadline_ms", 1000)
    if not isinstance(deadline, int | float) or not 0 < deadline <= MAX_DEADLINE_MS:
        raise ProtocolError(f"deadline_ms must be in (0, {MAX_DEADLINE_MS}]")
    priority = msg.get("priority", 0)
    if not isinstance(priority, int | float):
        raise ProtocolError("priority must be a number")
    state = msg.get("state")
    if isinstance(state, str):
        if not state.strip():
            raise ProtocolError("state must not be empty")
    elif not isinstance(state, dict | list) or not state:
        raise ProtocolError("state must be a non-empty string, object or array")
    questions = msg.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise ProtocolError("questions must be a non-empty object")
    for name, q in questions.items():
        _check_question(name, q)
    return DecideRequest(rid, lane, float(priority), int(deadline), state, questions)


def _check_question(name: str, q: Any) -> None:
    if not isinstance(q, dict) or not q.get("instructions"):
        raise ProtocolError(f"question {name!r}: needs type and instructions")
    crit = q.get("criteria")
    match q.get("type"):
        case "choice":
            if not isinstance(crit, dict) or not 2 <= len(crit) <= MAX_CHOICE_OPTIONS:
                raise ProtocolError(f"question {name!r}: choice needs 2..{MAX_CHOICE_OPTIONS} criteria")
        case "noul":
            if crit is not None and (not isinstance(crit, dict) or not set(crit) <= {"true", "false"}):
                raise ProtocolError(f"question {name!r}: noul criteria keys are 'true'/'false'")
        case "score":
            if not isinstance(crit, list) or not 2 <= len(crit) <= 10:
                raise ProtocolError(f"question {name!r}: score needs 2..10 levels")
        case other:
            raise ProtocolError(f"question {name!r}: unknown type {other!r}")
