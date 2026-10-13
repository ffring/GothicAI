"""The body's instant reactions for an npc we control (in a chat with the hero or following him), chosen
by the behavioral model in ~0.1 s, while the conversational model may still be thinking.

Four questions:
- `react` — an incident (attacked, the hero attacked, a murder nearby...): what the body does now. The
  menu is narrowed by the npc's intent (an npc that means to flee is not offered to fight);
- `gesture` and `voice` — the hero just spoke, asked together (`phrase`): a gesture in the pause before
  the npc answers, if any, and the sound it makes at once ("Хм...", "Что?!", fillers.py says it), so that
  the two agree;
- `posture` — the hero speaks to a free npc (M5e, talk.py): does it stop and listen, keep working, walk
  along or turn away. talk.py carries the choice out.

Fighting and fleeing are carried out by the game's own script states (the bridge hands the npc over
and takes it back). Until the model is trained on threats they are answered in *shadow* — traced, not
applied — unless `apply_threats` is set (docs/npc-agent.md). The code's own safety rule
(incidents.py) covers the dangerous cases meanwhile.
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
VOICE = "What did the hero just do with his words, and so what do you feel first?"
POSTURE = "The hero is talking to you. What does your body do while you answer him?"
INSTRUCTIONS = {"react": REACT, "gesture": GESTURE, "voice": VOICE, "posture": POSTURE}


@dataclass(frozen=True)
class Option:
    text: str  # English, may use {other}, {target}
    actions: tuple[Action, ...] = ()  # may use "{other}" / "{target}" as npc ids
    threat: bool = False  # a fight or flight: applied only when the reflexes may (apply_threats)
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
    "fight_back": Option("fight {other}", (("fight", {"npc": "{other}"}),), threat=True, ru="ты дерёшься"),
    "flee": Option("run away from {other}", (("flee", {"npc": "{other}"}),), threat=True, ru="ты убегаешь"),
    "back_away": Option(
        "step back from {other}",
        (("flee", {"npc": "{other}", "timeout_ms": 3000}),),
        threat=True,
        ru="ты отступил",
    ),
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
# the sound an npc makes at once (fillers.py says it): its kind, by what the hero's words were. Read in
# evals/talk-reactions: g7 told alarming news from an ordinary report badly, so "ack" sounds fit news as
# well; g9e, trained on this question (decider-service/tuning), gets 58 of 59 there, 57 as speech
# recognition writes them (no «!», no «ё»). A strong sound needs some confidence.
VOICE_OPTIONS: dict[str, Option] = {
    "none": Option(
        "he greets you, says goodbye, thanks you or makes small talk - you feel nothing special",
        threshold=0.0,
    ),
    "think": Option("he asks you a question you must think about - you ponder", threshold=0.0),
    "ack": Option("he tells you something ordinary or reports a task done - you take note", threshold=0.0),
    "surprise": Option(
        "he tells you alarming or shocking news - an attack, a death, a danger, a great find"
        " - you are startled",
        threshold=0.25,
    ),
    "displeasure": Option(
        "he insults you, calls you a liar or a fool, mocks or threatens you, or orders you around rudely"
        " - you are offended",
        threshold=0.25,
    ),
    "doubt": Option(
        "he boasts about himself or claims something about himself hard to believe - you doubt him",
        threshold=0.2,
    ),
}
# carried out by talk.py (holding the body, following); `ru` goes into the conversational model's situation
POSTURE_OPTIONS: dict[str, Option] = {
    "listen": Option(
        "stop what you are doing, turn to the hero and listen",
        threshold=0.4,
        ru="ты остановился, повернулся к герою и слушаешь",
    ),
    "keep_working": Option(
        "keep doing what you are doing and answer without stopping",
        threshold=0.4,
        ru="ты не прерываешь своё занятие и отвечаешь, не останавливаясь",
    ),
    "walk_with": Option(
        "walk along with the hero while you talk", threshold=0.6, ru="ты идёшь рядом с героем"
    ),
    "leave": Option(
        "turn away and go about your business", threshold=0.6, ru="ты отвернулся и уходишь по своим делам"
    ),
}


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
        apply_threats: bool = False,
    ) -> None:
        self.decider = decider
        self.apply_threats = apply_threats
        self.builder = builder or SituationBuilder()
        self.deadline_ms = deadline_ms
        self.clock = clock
        self._ids = itertools.count(1)
        self._last_gesture: dict[str, float] = {}

    async def posture(
        self,
        npc: NpcContext,
        words: str,
        doing: str,
        intent: Intent | None,
        clock: str | None = None,
    ) -> Reaction | None:
        """The hero speaks to a free npc that is `doing` something (English): what its body does now."""
        criteria = {k: o.text for k, o in POSTURE_OPTIONS.items()}
        state = self._state(npc, intent, clock, [f"Right now you {doing}.", f'The hero says: "{words}"'])
        return await self._ask(npc.id, "posture", state, criteria, POSTURE_OPTIONS, {})

    def _state(self, npc: NpcContext, intent: Intent | None, clock: str | None, lines: list[str]) -> str:
        parts = [f"You are {npc.name}. {npc.traits}" if npc.traits else self.builder.who(npc)]
        if clock:
            parts.append(f"It is {clock}.")
        parts.append(f"You are talking with the hero, {npc.distance_cm / 100:.0f} m away.")
        parts += npc.view
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

    async def phrase(
        self,
        npc: NpcContext,
        words: str,
        intent: Intent | None,
        clock: str | None = None,
        *,
        gesture: bool = True,
        voice: bool = True,
    ) -> dict[str, Reaction]:
        """The hero just said `words` to the npc: in one question to the model, the sound it makes at once
        (`voice`) and a gesture while it thinks of an answer (`gesture`, not again within
        GESTURE_COOLDOWN_S). Answers by question; none when the model did not answer."""
        now = self.clock()
        asks: dict[str, dict[str, Option]] = {}
        if voice:
            asks["voice"] = VOICE_OPTIONS
        if gesture and now - self._last_gesture.get(npc.id, float("-inf")) >= GESTURE_COOLDOWN_S:
            asks["gesture"] = GESTURE_OPTIONS
        if not asks:
            return {}
        state = self._state(npc, intent, clock, [f'The hero says: "{words}"'])
        reactions = await self._ask_all(
            npc.id, state, {q: ({k: o.text for k, o in opts.items()}, opts, {}) for q, opts in asks.items()}
        )
        if (g := reactions.get("gesture")) is not None and g.applied:
            self._last_gesture[npc.id] = now
        return reactions

    async def _ask(
        self,
        npc: str,
        question: str,
        state: str,
        criteria: dict[str, str],
        options: dict[str, Option],
        ids: dict[str, str],
    ) -> Reaction | None:
        return (await self._ask_all(npc, state, {question: (criteria, options, ids)})).get(question)

    async def _ask_all(
        self,
        npc: str,
        state: str,
        asks: dict[str, tuple[dict[str, str], dict[str, Option], dict[str, str]]],
    ) -> dict[str, Reaction]:
        """Questions about one state in one request: question -> (criteria, options, ids for the actions)."""
        started = self.clock()
        q = {
            question: {"type": "choice", "instructions": INSTRUCTIONS[question], "criteria": criteria}
            for question, (criteria, _, _) in asks.items()
        }
        out: Outcome = await self.decider.decide(
            f"chat:{npc}#{next(self._ids)}", "urgent", 1.0, self.deadline_ms, state, q
        )
        if out.kind != "answers" or not out.answers:
            return {}
        latency_ms = (self.clock() - started) * 1000
        reactions: dict[str, Reaction] = {}
        for question, (criteria, options, ids) in asks.items():
            if question not in out.answers:
                continue
            answer = out.answers[question]
            choice, conf = str(answer["choice"]), float(answer["confidence"])
            opt = options.get(choice)
            reaction = Reaction(
                npc,
                question,
                choice,
                conf,
                applied=False,
                latency_ms=latency_ms,
                state=state,
                options=list(criteria),
            )
            if opt is None:
                reaction.why = "unknown option"
            elif conf < opt.threshold:
                reaction.why = f"unsure ({conf:.2f} < {opt.threshold})"
            elif opt.threat and not self.apply_threats:
                reaction.why = "shadow"
            else:
                reaction.actions = [
                    (action, {k: v.format(**ids) if isinstance(v, str) else v for k, v in args.items()})
                    for action, args in opt.actions
                ]
                reaction.applied = True
            reactions[question] = reaction
        return reactions
