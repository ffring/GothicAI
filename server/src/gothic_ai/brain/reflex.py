"""The body's instant reactions for an npc we control (in a chat with the hero or following him), chosen
by the behavioral model in ~0.1 s, while the conversational model may still be thinking.

Two questions:
- `react` — an incident (attacked, the hero attacked, a murder nearby...): what the body does now. The
  menu is narrowed by the npc's intent (an npc that means to flee is not offered to fight);
- `gesture` — the hero just spoke: a gesture in the pause before the npc answers, if any.

Options the game cannot carry out for a controlled npc yet (fleeing, fighting) and threats in general
are answered in *shadow*: traced, not applied, until the model is trained on them (docs/npc-agent.md).
The code's own safety rule (incidents.py) covers the dangerous cases meanwhile.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..executor import Action, Intent
from .decider import Decider, Outcome
from .incident import Incident
from .situation import NpcContext, SituationBuilder

REACT = "What do you do right now?"
GESTURE = "The hero is talking to you. What gesture do you make while you think of an answer?"


@dataclass(frozen=True)
class Option:
    text: str  # English, may use {other}, {target}
    actions: tuple[Action, ...] = ()  # may use "{other}" / "{target}" as npc ids
    applied: bool = True  # False: decided in shadow only
    threshold: float = 0.5
    ru: str = ""  # what the body did, for the conversational model


REACT_OPTIONS: dict[str, Option] = {
    "stay": Option("stay where you are and keep an eye on {other}", ru="ты остался на месте"),
    "look": Option("turn to face {other}", (("turn_to", {"npc": "{other}"}),), ru="ты повернулся к нему"),
    "warn": Option(
        "make a warning gesture at {other}", (("play_ani", {"name": "T_NO"}),), ru="ты предостерегающе махнул"
    ),
    "run_to_hero": Option(
        "run to the hero for protection", (("go_to_npc", {"npc": "PLAYER"}),), ru="ты бежишь к герою"
    ),
    "run_to_helper": Option(
        "run to {target} for help", (("go_to_npc", {"npc": "{target}"}),), ru="ты бежишь за помощью"
    ),
    "fight_back": Option("fight {other}", applied=False),
    "flee": Option("run away from {other}", applied=False),
    "back_away": Option("step back from {other}", applied=False),
}
GESTURE_OPTIONS: dict[str, Option] = {
    "none": Option("no gesture, just listen"),
    "nod": Option("nod in agreement", (("play_ani", {"name": "T_YES"}),), threshold=0.7),
    "shake_head": Option("shake your head in disagreement", (("play_ani", {"name": "T_NO"}),), threshold=0.7),
    "shrug": Option("shrug: you do not know", (("play_ani", {"name": "T_DONTKNOW"}),)),
    "come_here": Option("beckon the hero closer", (("play_ani", {"name": "T_COMEOVERHERE"}),)),
    "dismiss": Option("wave it off dismissively", (("play_ani", {"name": "T_FORGETIT"}),)),
}
GESTURE_COOLDOWN_S = 10.0


def react_menu(intent: Intent | None, other: str | None) -> list[str]:
    """Options for an incident, narrowed by what the npc means to do."""
    goal = intent.goal if intent else "none"
    if goal in ("fight", "guard_hero"):
        menu = ["stay", "look", "warn", "fight_back"]
    elif goal == "flee":
        menu = ["flee", "back_away", "run_to_hero"]
    elif goal == "seek_help" and intent and intent.target and intent.target != "PLAYER":
        menu = ["run_to_helper", "flee", "stay"]
    else:
        menu = ["stay", "look", "warn", "run_to_hero", "back_away", "flee", "fight_back"]
    if other == "PLAYER":  # the hero is the danger: no running to him
        menu = [o for o in menu if o != "run_to_hero"]
    return menu


@dataclass
class Reaction:
    npc: str
    question: str
    choice: str
    confidence: float
    applied: bool  # carried out (False: shadow, unsure or nothing to do)
    actions: list[Action] = field(default_factory=list)
    latency_ms: float = 0.0
    state: str = ""
    options: list[str] = field(default_factory=list)
    why: str = ""  # why not applied

    def record(self) -> dict[str, Any]:
        return {
            "type": "reflex",
            "t": round(time.time(), 3),
            "npc": self.npc,
            "question": self.question,
            "state": self.state,
            "options": self.options,
            "choice": self.choice,
            "confidence": round(self.confidence, 3),
            "applied": self.applied,
            "why": self.why,
            "actions": [list(a) for a in self.actions],
            "latency_ms": round(self.latency_ms, 1),
        }


class Reflexes:
    def __init__(
        self,
        decider: Decider,
        builder: SituationBuilder | None = None,
        *,
        deadline_ms: int = 400,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.decider = decider
        self.builder = builder or SituationBuilder()
        self.deadline_ms = deadline_ms
        self.clock = clock
        self._ids = itertools.count(1)
        self._last_gesture: dict[str, float] = {}

    def _state(self, npc: NpcContext, intent: Intent | None, clock: str | None, lines: list[str]) -> str:
        parts = [f"You are {npc.name}. {npc.traits}" if npc.traits else self.builder.who(npc)]
        if clock:
            parts.append(f"It is {clock}.")
        parts.append(f"You are talking with the hero, {npc.distance_cm / 100:.0f} m away.")
        if intent is not None:
            parts.append(intent.describe_en())
        parts += lines
        return " ".join(parts)

    async def react(
        self, npc: NpcContext, incident: Incident, intent: Intent | None, clock: str | None = None
    ) -> Reaction | None:
        last = incident.events[-1]
        other = last.other or "someone"
        other_name = "the hero" if other == "PLAYER" else (last.other_name or other)
        target = intent.target if intent and intent.target else "PLAYER"
        menu = react_menu(intent, other)
        criteria = {k: REACT_OPTIONS[k].text.format(other=other_name, target=target) for k in menu}
        lines = [e.describe_en() for e in incident.events[-3:]]
        state = self._state(npc, intent, clock, lines)
        return await self._ask(
            npc.id, "react", state, criteria, REACT_OPTIONS, {"other": other, "target": target}
        )

    async def gesture(
        self, npc: NpcContext, words: str, intent: Intent | None, clock: str | None = None
    ) -> Reaction | None:
        now = self.clock()
        if now - self._last_gesture.get(npc.id, float("-inf")) < GESTURE_COOLDOWN_S:
            return None
        criteria = {k: o.text for k, o in GESTURE_OPTIONS.items()}
        state = self._state(npc, intent, clock, [f'The hero says: "{words}"'])
        reaction = await self._ask(npc.id, "gesture", state, criteria, GESTURE_OPTIONS, {})
        if reaction is not None and reaction.applied:
            self._last_gesture[npc.id] = now
        return reaction

    async def _ask(
        self,
        npc: str,
        question: str,
        state: str,
        criteria: dict[str, str],
        options: dict[str, Option],
        ids: dict[str, str],
    ) -> Reaction | None:
        started = self.clock()
        q = {
            question: {
                "type": "choice",
                "instructions": REACT if question == "react" else GESTURE,
                "criteria": criteria,
            }
        }
        out: Outcome = await self.decider.decide(
            f"chat:{npc}#{next(self._ids)}", "urgent", 1.0, self.deadline_ms, state, q
        )
        if out.kind != "answers" or not out.answers or question not in out.answers:
            return None
        answer = out.answers[question]
        choice, conf = str(answer["choice"]), float(answer["confidence"])
        opt = options.get(choice)
        reaction = Reaction(
            npc,
            question,
            choice,
            conf,
            applied=False,
            latency_ms=(self.clock() - started) * 1000,
            state=state,
            options=list(criteria),
        )
        if opt is None:
            reaction.why = "unknown option"
        elif conf < opt.threshold:
            reaction.why = f"unsure ({conf:.2f} < {opt.threshold})"
        elif not opt.applied:
            reaction.why = "shadow"
        else:
            reaction.actions = [
                (action, {k: v.format(**ids) if isinstance(v, str) else v for k, v in args.items()})
                for action, args in opt.actions
            ]
            reaction.applied = True
        return reaction
