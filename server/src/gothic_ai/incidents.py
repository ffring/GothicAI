"""The two loops together, for the npc's we control (in a chat with the hero, or following him).

Something happens to the npc → an incident (brain/incident.py). At once, without waiting:
- the **behavioral model** picks the body's reaction (brain/reflex.py) — in ~0.1 s;
- the code's **safety rule** hands a beaten npc back to the game's scripts (they fight back or flee):
  a second hit from the same attacker within `SECOND_HIT_S`, or health below half.

In parallel, if the incident is worth words and someone hears them, the **conversational model** gets
an event turn (dialogue.py): words and maybe a new intent, 3–5 s later. Its answer is dropped if the
incident is over by then. Calls of its own come out of the budget (budget.py).

The hero's phrase is an event too: the body may gesture in the pause before the answer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from .brain.apply import Decision
from .brain.incident import INCIDENT_GAP_S, IncidentEvent, IncidentTracker, escalation_reason
from .brain.planner import Flight, NpcPlanner
from .brain.reflex import REACT_OPTIONS, Reaction, Reflexes
from .brain.situation import NpcContext, clock_text
from .brain.stimuli import Stimulus
from .budget import Budget
from .dialogue import DialogueManager
from .game import GameError, GameLink
from .protocol import (
    Attached,
    ChatEndCmd,
    Detached,
    FollowEnded,
    GameMessage,
    Loading,
    Perception,
    Session,
)
from .trace import TraceWriter

log = logging.getLogger(__name__)

SECOND_HIT_S = 10.0
FREE_HEARING_CM = 2000.0  # a free npc's words are worth the call if the hero is this close (or sees it)
FREE_COOLDOWN_S = 30.0  # one call per free npc at most this often
FREE_TOOLS = ("set_intent", "recall")  # an npc nobody controls cannot hand over items or end a chat

# a free npc's stimuli, for the conversational model (Russian); the rest are told in English
EVENTS_RU = {
    "monster_near": "{other} идёт на тебя",
    "npc_attack": "{other} напал на тебя",
    "fight_nearby": "рядом драка: {detail}",
    "cry_for_help": "{other} зовёт на помощь",
    "player_near": "герой подошёл к тебе",
    "player_speech": "герой сказал рядом: «{detail}»",
    "enter_room": "герой вошёл в твой дом",
    "item_given": "герой отдал тебе {detail}",
    "item_dropped": "герой бросил рядом {detail}",
}
# what the body already did, by the behavioral model's choice
REACTIONS_RU = {
    "fight": "ты вступил в бой",
    "flee": "ты убегаешь",
    "call_help": "ты зовёшь на помощь",
    "call_guards": "ты зовёшь стражу",
    "comment": "ты вскрикнул",
    "look_at": "ты смотришь на героя",
    "greet": "ты кивнул герою",
    "approach_talk": "ты идёшь к герою",
    "step_away": "ты отвернулся",
    "tell_to_leave": "ты велел герою убираться",
    "thank": "ты кивнул в знак благодарности",
}


def describe_ru(stim: Stimulus) -> str:
    template = EVENTS_RU.get(stim.kind)
    if template is None:
        return stim.describe(stim.at)
    other = "герой" if stim.other == "PLAYER" else (stim.other_name or stim.other)
    return template.format(other=other, detail=stim.detail or "")


LOW_HEALTH = 0.5
SETTLE_S = 0.5  # events this close together go to the conversational model as one

# how a perception of a controlled npc reads as an incident event (the rest are ignored)
FOLLOW_REASONS = {
    "target_gone": "герой пропал",
    "target_too_far": "герой ушёл слишком далеко",
    "timeout": "надоело",
}


def classify(p: Perception, names: dict[str, str], now: float) -> IncidentEvent | None:
    other_name = names.get(p.other)
    match p.perc:
        case "ASSESSDAMAGE":
            return IncidentEvent("attacked", now, p.other, other_name)
        case "ASSESSOTHERSDAMAGE":
            kind = "hero_attacked" if p.victim == "PLAYER" else "other_attacked"
            return IncidentEvent(kind, now, p.other, other_name, names.get(p.victim or "", p.victim))
        case "ASSESSMURDER":
            return IncidentEvent("murder", now, p.other, other_name, names.get(p.victim or "", p.victim))
        case "ASSESSMAGIC":
            return IncidentEvent("magic", now, p.other, other_name)
    return None


class IncidentDesk:
    def __init__(
        self,
        link: GameLink,
        dialogue: DialogueManager,
        reflexes: Reflexes | None,
        budget: Budget,
        trace: TraceWriter | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        settle_s: float = SETTLE_S,
        gap_s: float = INCIDENT_GAP_S,
        planner: NpcPlanner | None = None,
    ) -> None:
        self.link = link
        self.dialogue = dialogue
        self.reflexes = reflexes
        self.budget = budget
        self.trace = trace
        self.clock = clock
        self.settle_s = settle_s
        self.tracker = IncidentTracker(gap_s)
        self.attached: set[str] = set()
        self.reactions: list[Reaction] = []  # recent ones, for the console and tests
        self._tasks: set[asyncio.Task[Any]] = set()
        link.listeners.append(self.on_message)
        dialogue.on_player_phrase = self.on_player_phrase
        dialogue.on_body_failed = self.on_body_failed
        dialogue.controls = lambda npc: npc.upper() in self.attached
        self.planner: NpcPlanner | None = None
        self._spoke_at: dict[str, float] = {}
        if planner is not None:
            self.follow(planner)

    # --- events ---------------------------------------------------------------------------

    def on_message(self, msg: GameMessage) -> None:
        match msg:
            case Attached():
                self.attached.add(msg.npc.upper())
            case Detached():
                npc = msg.npc.upper()
                self.attached.discard(npc)
                if msg.reason == "down":
                    self._event(npc, IncidentEvent("down", self.clock()))
                self.tracker.close(npc)
            case Perception():
                npc = msg.npc.upper()
                if npc in self.attached and npc in self.dialogue.conversations:
                    event = classify(msg, self._names(npc), self.clock())
                    if event is not None:
                        self._event(npc, event)
            case FollowEnded():
                npc = msg.npc.upper()
                if msg.reason in FOLLOW_REASONS and npc in self.dialogue.conversations:
                    self._event(
                        npc, IncidentEvent("follow_ended", self.clock(), detail=FOLLOW_REASONS[msg.reason])
                    )
            case Loading() | Session():
                for npc in list(self.tracker.current):
                    self.tracker.close(npc)

    def on_body_failed(self, npc: str, what: str) -> None:
        if npc in self.attached:
            self._event(npc, IncidentEvent("action_failed", self.clock(), detail=what.strip("[]")))

    def on_player_phrase(self, npc: str, text: str) -> None:
        if self.reflexes is not None and npc in self.attached:
            self._spawn(self._gesture(npc, text))

    def _event(self, npc: str, event: IncidentEvent) -> None:
        inc = self.tracker.add(npc, event)
        log.info("%s incident #%d: %s", npc, inc.id, event.describe_en())
        if inc.version == 1:
            self._spawn(self._watch(npc, inc.id))
        if event.kind == "down":
            self.dialogue.note(npc, event.describe_ru())
            return
        if event.kind == "attacked" and self._beaten(npc, inc, event):
            self._spawn(self._release(npc, event))
            return
        if self.reflexes is not None and event.spec.urgent:
            self._spawn(self._react(npc, inc.id))
        self._spawn(self._escalate_later(npc, inc.id))

    def _beaten(self, npc: str, inc: Any, event: IncidentEvent) -> bool:
        if event.other and inc.hits_from(event.other, event.at - SECOND_HIT_S) >= 2:
            return True
        snap = self.link.snapshots.get(npc)
        return snap is not None and snap.npc.hp_max > 0 and snap.npc.hp < snap.npc.hp_max * LOW_HEALTH

    # --- the fast loop ----------------------------------------------------------------------

    def _context(self, npc: str) -> tuple[NpcContext, str | None]:
        conv = self.dialogue.conversations.get(npc)
        snap = self.link.snapshots.get(npc)
        name = conv.persona.name if conv else npc
        traits = conv.persona.traits if conv else None
        distance = snap.player.distance if snap is not None and snap.player is not None else 0.0
        clock = clock_text(snap.world.hour, snap.world.minute) if snap is not None else None
        return NpcContext(npc, name, -1, distance, True, traits=traits), clock

    async def _react(self, npc: str, incident_id: int) -> None:
        assert self.reflexes is not None
        inc = self.tracker.current.get(npc)
        if inc is None or inc.id != incident_id:
            return
        ctx, clock = self._context(npc)
        conv = self.dialogue.conversations.get(npc)
        reaction = await self.reflexes.react(ctx, inc, conv.intent if conv else None, clock)
        if reaction is not None:
            await self._apply(reaction, incident_id)

    async def _gesture(self, npc: str, text: str) -> None:
        assert self.reflexes is not None
        ctx, clock = self._context(npc)
        conv = self.dialogue.conversations.get(npc)
        reaction = await self.reflexes.gesture(ctx, text, conv.intent if conv else None, clock)
        if reaction is None:
            return
        if reaction.applied and (self.dialogue.has_spoken(npc) or self.dialogue.body_busy(npc)):
            # too late (the answer started) or the body is busy with a commitment
            reaction.applied, reaction.why = (
                False,
                "too late" if self.dialogue.has_spoken(npc) else "body busy",
            )
        await self._apply(reaction, None)

    async def _apply(self, reaction: Reaction, incident_id: int | None) -> None:
        self.reactions = [*self.reactions[-49:], reaction]
        if self.trace is not None:
            self.trace.write({**reaction.record(), "incident": incident_id})
        log.info(
            "%s reflex %s=%s (%.2f, %.0f ms)%s",
            reaction.npc,
            reaction.question,
            reaction.choice,
            reaction.confidence,
            reaction.latency_ms,
            "" if reaction.applied else f" not applied: {reaction.why}",
        )
        if not reaction.applied or reaction.npc not in self.attached:
            return
        for action, args in reaction.actions:
            try:
                await self.link.act(reaction.npc, action, timeout=30, **args)  # type: ignore[arg-type]
            except (GameError, TimeoutError) as err:
                log.info("%s reflex action %s failed: %s", reaction.npc, action, err)

    async def _release(self, npc: str, event: IncidentEvent) -> None:
        """The safety rule: a beaten npc goes back to the game's scripts, which fight back or flee."""
        log.info("%s: beaten (%s), handing over to the game's scripts", npc, event.describe_en())
        self.tracker.close(npc)
        self.dialogue.note(npc, f"{event.describe_ru()}. Ты прервал разговор и защищаешься")
        try:
            await self.link.send(ChatEndCmd(npc=npc))
            await self.link.act(npc, "return_to_routine", timeout=10)
        except (GameError, TimeoutError) as err:
            log.info("%s: release failed: %s", npc, err)

    async def _watch(self, npc: str, incident_id: int) -> None:
        """When the incident is over, so are the goals that only held during it."""
        while True:
            inc = self.tracker.current.get(npc)
            if inc is None or inc.id != incident_id or not inc.active(self.clock(), self.tracker.gap_s):
                break
            await asyncio.sleep(max(0.05, inc.last + self.tracker.gap_s - self.clock()))
        self.dialogue.incident_over(npc)

    # --- the slow loop ----------------------------------------------------------------------

    async def _escalate_later(self, npc: str, incident_id: int) -> None:
        await asyncio.sleep(self.settle_s)  # a burst of events becomes one call
        inc = self.tracker.current.get(npc)
        if inc is None or inc.id != incident_id or npc not in self.attached:
            return
        snap = self.link.snapshots.get(npc)
        hero = snap.player.distance if snap is not None and snap.player is not None else None
        reason = escalation_reason(inc, self.clock(), hero)
        if reason is None:
            return
        if not self.budget.allow(npc):
            log.info("%s incident #%d: no budget for words", npc, inc.id)
            return
        inc.escalated_version, inc.escalated_at = inc.version, self.clock()
        body = [r for r in self.reactions if r.npc == npc and r.applied and r.question == "react"]
        did = REACT_OPTIONS[body[-1].choice].ru if body and body[-1].choice in REACT_OPTIONS else ""
        done = f" Твоё тело уже среагировало: {did} — это уже случилось, не противоречь этому." if did else ""
        cue = f"[Событие: {inc.describe_ru()}.{done}]"
        log.info("%s incident #%d: calling the conversational model (%s)", npc, inc.id, reason)
        self.dialogue.event_turn(
            npc,
            cue,
            relevant=lambda: self.tracker.is_current(npc, incident_id, self.clock()),
            overlay=inc.urgent,  # shouted on the move: the body keeps running or fighting
        )

    # --- free npc's -------------------------------------------------------------------------

    def follow(self, planner: NpcPlanner) -> None:
        """Free npc's: the planner's fast loop reacts; when the moment calls for words, they come here."""
        self.planner = planner
        planner.on_escalate = self.on_planner_escalate

    def on_planner_escalate(self, npc: str, level: float, reaction: Decision | None, flight: Flight) -> None:
        """The behavioral model says a free npc's moment calls for its own words: the conversational model
        speaks for it, on the move, if the hero hears it, not too often and within the budget."""
        assert self.planner is not None
        npc = npc.upper()
        rec = self.planner.npcs.get(npc)
        if rec is None or npc in self.attached:  # a controlled npc's incidents are handled above
            return
        hears = rec.distance_cm <= FREE_HEARING_CM or (rec.in_view and rec.distance_cm <= 3000)
        now = self.clock()
        if not hears or now - self._spoke_at.get(npc, float("-inf")) < FREE_COOLDOWN_S:
            return
        if not self.budget.allow(npc):
            log.info("%s: no budget for words (escalate %.1f)", npc, level)
            return
        self._spoke_at[npc] = now
        alive = [s for s in rec.stimuli if s.alive(self.planner.clock())]
        happened = "; ".join(describe_ru(s) for s in alive) or "что-то случилось"
        did = REACTIONS_RU.get(reaction.choice, "") if reaction is not None else ""
        done = f" Твоё тело уже среагировало: {did} — это уже случилось, не противоречь этому." if did else ""
        self.dialogue.open(npc, rec.name)
        log.info("%s: escalate %.1f, the conversational model speaks for it", npc, level)
        self.dialogue.event_turn(
            npc,
            f"[Событие: {happened}.{done}]",
            relevant=lambda: self.planner is not None and self.planner.stirred(npc),
            overlay=True,
            tools=FREE_TOOLS,
        )

    # --- helpers ----------------------------------------------------------------------------

    def _names(self, npc: str) -> dict[str, str]:
        snap = self.link.snapshots.get(npc)
        names = {"PLAYER": "герой"}
        if snap is not None:
            names.update({o.id: o.name for o in snap.nearby_npcs})
        return names

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
