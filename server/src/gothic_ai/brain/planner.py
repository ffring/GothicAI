"""Who asks the decision model, when, and what happens to the answer.

Every npc the game reports has a tier (tiers.py). It gets a decision:
- on an event it noticed (stimuli.py): NEAR npc's on any, MID only on salient ones; urgent events
  of NEAR npc's go to the decider's urgent lane;
- when its action queue runs empty (NEAR), but not more often than every `near_min_gap_s`;
- on a timer with a random phase: NEAR every 6-10 s, MID 30-60 s, FAR about once per game hour
  (a plan, not a reaction); an npc coming in from FAR gets a quick catch-up decision.
Idle and timer checks of NEAR/MID npc's are only made when there is something to react to: a fresh
stimulus or the npc's own `agenda` (what it wants from the player). With neither, asking the model
gives a vague answer at best, so the check is skipped and the npc keeps its routine.

Per npc there is at most one pending request (new events merge into it) and one in flight. A pending
request that waits too long is dropped; an answer is applied only if nothing happened to the npc
since the request was built (`version`) and it is still ours. Anything dropped, unanswered or
unsure means the npc keeps its routine: the game's scripts are the fallback.

Background requests are limited by a rate budget and a cap on requests in flight; when the decider
falls behind (expired / errors), MID and FAR timers stretch.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..protocol import (
    Attached,
    ChatClose,
    ChatOpen,
    Detached,
    GameMessage,
    Loading,
    NpcEvent,
    NpcIdle,
    NpcRoster,
    PlayerText,
    RosterEntry,
)
from .apply import Applier, Decision
from .decider import Decider, Lane, Outcome
from .situation import NpcContext, Situation, SituationBuilder, clock_text
from .stimuli import Stimulus, merge, policy
from .tiers import Placement, Tier, TierConfig, assign_tiers, importance

log = logging.getLogger(__name__)

NO_ACTION = {"continue", "keep_routine"}
OVERHEAR_CM = 1000  # npc's this close hear what the player says to someone else


@dataclass
class PlannerConfig:
    tiers: TierConfig = field(default_factory=TierConfig)
    near_min_gap_s: float = 2.0
    near_period_s: tuple[float, float] = (6.0, 10.0)
    mid_period_s: tuple[float, float] = (30.0, 60.0)
    far_period_s: tuple[float, float] = (200.0, 280.0)  # a game hour is ~4 real minutes (x14.5)
    catch_up_s: tuple[float, float] = (0.2, 1.5)
    debounce_s: float = 0.2  # non-urgent events this close together make one request
    stale_s: dict[Tier, float] = field(
        default_factory=lambda: {Tier.NEAR: 2.0, Tier.MID: 10.0, Tier.FAR: 60.0}
    )
    deadline_ms: dict[str, int] = field(
        default_factory=lambda: {"urgent": 300, "near": 1000, "mid": 5000, "far": 30000, "attitude": 5000}
    )
    react_threshold: float = 0.5  # below it the npc keeps its routine
    plan_threshold: float = 0.5
    # per-option thresholds: harmless gestures need less certainty than walking up to the player
    option_threshold: dict[str, float] = field(
        default_factory=lambda: {"look_at": 0.25, "greet": 0.3, "approach_talk": 0.6, "give_back": 0.6}
    )
    # the same action is not repeated by one npc within this time (walking up to the player twice a minute
    # is pestering, not liveliness)
    cooldown_s: dict[str, float] = field(
        default_factory=lambda: {
            "approach_talk": 120.0,
            "greet": 30.0,
            "comment": 20.0,
            "look_at": 8.0,
            "thank": 30.0,
            "give_back": 60.0,
            "tell_to_leave": 15.0,
        }
    )
    default_cooldown_s: float = 10.0
    ask_attitude: bool = True  # after an urgent reaction, ask in the background how the attitude changed
    max_inflight: int = 48
    bg_rate: float = 20.0  # background requests per second
    tick_s: float = 0.05


@dataclass
class Pending:
    purpose: str  # react | plan
    urgent: bool
    created: float
    deadline: float  # dropped if not sent by then
    not_before: float


@dataclass
class Flight:
    rid: str
    purpose: str
    urgent: bool
    version: int
    sent: float
    stimuli: tuple[str, ...]
    situation: Situation


@dataclass
class NpcRecord:
    id: str
    name: str
    guild: int = 0
    distance_cm: float = 0.0
    in_view: bool = False
    activity: str = "routine"
    tier: Tier = Tier.FAR
    importance: float = 0.0
    version: int = 0
    stimuli: list[Stimulus] = field(default_factory=list)
    next_check: float = 0.0
    last_decision: float | None = None
    last_interaction: float | None = None
    agenda: str | None = None  # what the npc wants from the player, e.g. help with wolves
    last_applied: dict[str, float] = field(default_factory=dict)  # action -> when it was last done
    pending: Pending | None = None
    flight: Flight | None = None


@dataclass
class PlannerStats:
    sent: dict[str, int] = field(default_factory=lambda: {"urgent": 0, "background": 0})
    outcomes: dict[str, int] = field(default_factory=dict)
    applied: int = 0
    unsure: int = 0
    no_action: int = 0
    discarded: int = 0  # answer came back but the npc had changed meanwhile
    stale: int = 0  # pending dropped before it could be sent
    quiet: int = 0  # idle/timer checks skipped: nothing to react to
    cooldown: int = 0  # decided an action it did just recently
    events: int = 0
    scripted_events: int = 0


class NpcPlanner:
    def __init__(
        self,
        decider: Decider,
        applier: Applier,
        builder: SituationBuilder | None = None,
        config: PlannerConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        agenda_of: Callable[[str], str | None] | None = None,
    ) -> None:
        self.decider = decider
        # what an npc wants from the hero, as its last conversation left it (memory); read when it appears
        self.agenda_of = agenda_of
        self.applier = applier
        self.builder = builder or SituationBuilder()
        self.cfg = config or PlannerConfig()
        self.clock = clock
        self.rng = rng or random.Random()
        self.npcs: dict[str, NpcRecord] = {}
        self.game_time: tuple[int, int] | None = None
        self.stats = PlannerStats()
        self.slowdown = 1.0  # stretches MID/FAR timers while the decider falls behind
        self._tokens = self.cfg.bg_rate
        self._refilled = clock()
        self._ids = itertools.count(1)
        self._tasks: set[asyncio.Task[None]] = set()

    # --- input ----------------------------------------------------------------------------

    def on_message(self, msg: GameMessage) -> None:
        """GameLink listener."""
        now = self.clock()
        match msg:
            case NpcRoster():
                if msg.hour is not None and msg.minute is not None:
                    self.game_time = (msg.hour, msg.minute)
                self.on_roster(msg.npcs, now, full=msg.full)
            case NpcEvent():
                self.on_event(
                    msg.npc, msg.kind, now, other=msg.other, distance_cm=msg.distance, detail=msg.detail
                )
            case NpcIdle():
                self.on_idle(msg.npc, now)
            case PlayerText():
                self.on_player_text(msg.npc, msg.text, now)
            case ChatOpen() | Attached():
                self._set_activity(msg.npc, "dialog" if isinstance(msg, ChatOpen) else "attached")
            case ChatClose():
                self._after_chat(msg.npc, now)
                self._set_activity(msg.npc, "routine")
            case Detached():
                self._set_activity(msg.npc, "routine")
            case Loading():
                self.reset()

    def reset(self) -> None:
        for task in self._tasks:
            task.cancel()
        self.npcs.clear()

    def on_roster(self, entries: list[RosterEntry], now: float, *, full: bool = True) -> None:
        seen = set()
        for e in entries:
            npc = e.id.upper()
            seen.add(npc)
            rec = self.npcs.get(npc)
            if rec is None:
                rec = self.npcs[npc] = NpcRecord(npc, e.name, e.guild)
                rec.tier = Tier.OFF  # placed below; a new npc gets a random phase
                if self.agenda_of is not None:
                    rec.agenda = self.agenda_of(npc)
            rec.distance_cm, rec.in_view, rec.guild = e.distance, e.in_view, e.guild
            if rec.activity not in ("dialog", "attached") or e.activity != "routine":
                rec.activity = e.activity
        if full:
            for npc in [n for n in self.npcs if n not in seen]:
                del self.npcs[npc]  # its flight's answer will find no record and be ignored
        self._retier(now)

    def on_event(
        self,
        npc: str,
        kind: str,
        now: float,
        *,
        other: str = "PLAYER",
        distance_cm: float | None = None,
        detail: str | None = None,
    ) -> None:
        rec = self.npcs.get(npc.upper())
        if rec is None or rec.tier is Tier.OFF:
            return
        self.stats.events += 1
        stim = Stimulus(kind, now, other, distance_cm, detail)
        rec.stimuli = merge(rec.stimuli, stim, now)
        if other == "PLAYER":
            rec.last_interaction = now
        p = policy(kind)
        if p.scripted:  # the game's scripts react; it stays as context for later decisions
            self.stats.scripted_events += 1
            return
        if rec.tier is Tier.FAR or (rec.tier is Tier.MID and not p.salient):
            return
        rec.version += 1
        urgent = p.urgent and rec.tier is Tier.NEAR
        self._want(rec, "react", urgent, now, not_before=now if urgent else now + self.cfg.debounce_s)
        if urgent and rec.flight is not None:
            self._spawn(self.decider.cancel(rec.flight.rid))  # its answer is outdated now

    def on_idle(self, npc: str, now: float) -> None:
        rec = self.npcs.get(npc.upper())
        if rec is None or rec.tier is not Tier.NEAR:
            return
        if not self._has_reason(rec, now):
            self.stats.quiet += 1
            return
        earliest = now if rec.last_decision is None else max(now, rec.last_decision + self.cfg.near_min_gap_s)
        self._want(rec, "react", False, now, not_before=earliest)

    def on_player_text(self, partner: str, text: str, now: float) -> None:
        """The player said something in the chat with `partner`: npc's close by overhear it."""
        partner = partner.upper()
        for rec in list(self.npcs.values()):
            if rec.id != partner and rec.distance_cm <= OVERHEAR_CM:
                self.on_event(rec.id, "player_speech", now, distance_cm=rec.distance_cm, detail=text)

    def set_agenda(self, npc: str, agenda: str | None) -> None:
        """What the npc wants from the player (from memory or the big model's plan); None clears it."""
        rec = self.npcs.get(npc.upper())
        if rec is not None and rec.agenda != agenda:
            rec.agenda = agenda
            rec.version += 1

    # --- scheduling -----------------------------------------------------------------------

    def tick(self, now: float) -> None:
        """Turn due timers into requests and send what the budget allows."""
        self._refill(now)
        for rec in self.npcs.values():
            if rec.tier is Tier.OFF or rec.pending or rec.flight or rec.next_check > now:
                continue
            if rec.tier is not Tier.FAR and not self._has_reason(rec, now):
                self.stats.quiet += 1
                self._schedule(rec, now)
                continue
            self._want(rec, "plan" if rec.tier is Tier.FAR else "react", False, now, not_before=now)

        ready = []
        for rec in self.npcs.values():
            pend = rec.pending
            if pend is None or rec.flight is not None or pend.not_before > now:
                continue
            if pend.deadline < now:
                rec.pending = None
                self.stats.stale += 1
                self._schedule(rec, now)
                continue
            ready.append(rec)
        # urgent first, then the most important, then the longest waiting
        ready.sort(key=lambda r: (not r.pending.urgent, -r.importance, r.pending.created))
        inflight = sum(1 for r in self.npcs.values() if r.flight is not None)
        for rec in ready:
            urgent = rec.pending.urgent
            if not urgent and (inflight >= self.cfg.max_inflight or self._tokens < 1):
                break
            if not urgent:
                self._tokens -= 1
            self._send(rec, now)
            inflight += 1

    async def run(self) -> None:
        try:
            while True:
                self.tick(self.clock())
                await asyncio.sleep(self.cfg.tick_s)
        finally:
            self.reset()

    def snapshot(self) -> dict:
        tiers: dict[str, int] = {}
        for rec in self.npcs.values():
            tiers[rec.tier.name] = tiers.get(rec.tier.name, 0) + 1
        s = self.stats
        return {
            "npcs": len(self.npcs),
            "tiers": tiers,
            "sent": dict(s.sent),
            "outcomes": dict(s.outcomes),
            "applied": s.applied,
            "unsure": s.unsure,
            "no_action": s.no_action,
            "discarded": s.discarded,
            "stale": s.stale,
            "quiet": s.quiet,
            "cooldown": s.cooldown,
            "events": s.events,
            "slowdown": round(self.slowdown, 2),
        }

    # --- internals ------------------------------------------------------------------------

    @staticmethod
    def _has_reason(rec: NpcRecord, now: float) -> bool:
        return rec.agenda is not None or any(s.alive(now) for s in rec.stimuli)

    def _after_chat(self, npc: str, now: float) -> None:
        """The hero just talked with the npc: it should not walk up to him or greet him again at once."""
        rec = self.npcs.get(npc.upper())
        if rec is not None:
            rec.last_interaction = now
            for choice in ("approach_talk", "greet"):
                rec.last_applied[choice] = now

    def _set_activity(self, npc: str, activity: str) -> None:
        rec = self.npcs.get(npc.upper())
        if rec is not None and rec.activity != activity:
            rec.activity = activity
            self._retier(self.clock())

    def _retier(self, now: float) -> None:
        placements = []
        for rec in self.npcs.values():
            since = None if rec.last_interaction is None else now - rec.last_interaction
            rec.importance = importance(rec.distance_cm / 100, rec.in_view, since)
            placements.append(
                Placement(
                    rec.id, rec.distance_cm / 100, rec.in_view, rec.activity == "routine", rec.importance
                )
            )
        prev = {rec.id: rec.tier for rec in self.npcs.values() if rec.tier is not Tier.OFF}
        for npc, tier in assign_tiers(placements, prev, self.cfg.tiers).items():
            rec = self.npcs[npc]
            if tier is rec.tier:
                continue
            old, rec.tier = rec.tier, tier
            rec.version += 1
            if tier is Tier.OFF:
                rec.pending = None
            elif old is Tier.OFF and npc not in prev and rec.last_decision is None:
                rec.next_check = now + self.rng.uniform(0, self._period(tier)[1])  # new: spread first checks
            elif tier < old or old is Tier.OFF:
                # coming closer (or back to its routine): decide soon, it may have missed things
                rec.next_check = min(rec.next_check, now + self.rng.uniform(*self.cfg.catch_up_s))
                if rec.pending:
                    rec.pending.purpose = "plan" if tier is Tier.FAR else "react"
            else:
                rec.next_check = now + self.rng.uniform(0, self._period(tier)[1])
                if rec.pending and tier is Tier.FAR:
                    rec.pending = None  # its reaction is moot out there

    def _want(self, rec: NpcRecord, purpose: str, urgent: bool, now: float, *, not_before: float) -> None:
        stale = now + self.cfg.stale_s[rec.tier] + (not_before - now)
        if rec.pending is None:
            rec.pending = Pending(purpose, urgent, now, stale, not_before)
            return
        p = rec.pending
        if purpose == "react":
            p.purpose = "react"
        p.urgent = p.urgent or urgent
        p.not_before = min(p.not_before, not_before) if urgent else max(p.not_before, not_before)
        p.deadline = max(p.deadline, stale)

    def _send(self, rec: NpcRecord, now: float) -> None:
        pend = rec.pending
        assert pend is not None
        rec.pending = None
        rec.stimuli = [s for s in rec.stimuli if s.alive(now)]
        ctx = NpcContext(rec.id, rec.name, rec.guild, rec.distance_cm, rec.in_view, agenda=rec.agenda)
        clock = clock_text(*self.game_time) if self.game_time else None
        if pend.purpose == "plan":
            sit = self.builder.plan(ctx, clock)
            deadline = self.cfg.deadline_ms["far"]
        else:
            sit = self.builder.react(ctx, rec.stimuli, now, clock)
            key = "urgent" if pend.urgent else ("near" if rec.tier is Tier.NEAR else "mid")
            deadline = self.cfg.deadline_ms[key]
        lane: Lane = "urgent" if pend.urgent else "background"
        flight = Flight(
            f"{rec.id}#{next(self._ids)}",
            pend.purpose,
            pend.urgent,
            rec.version,
            now,
            tuple(s.kind for s in rec.stimuli),
            sit,
        )
        rec.flight = flight
        self.stats.sent[lane] += 1
        self._spawn(self._ask(rec.id, flight, lane, rec.importance, deadline))

    async def _ask(self, npc: str, flight: Flight, lane: Lane, priority: float, deadline_ms: int) -> None:
        sit = flight.situation
        outcome = await self.decider.decide(flight.rid, lane, priority, deadline_ms, sit.state, sit.questions)
        self._on_outcome(npc, flight, outcome, self.clock())

    def _on_outcome(self, npc: str, flight: Flight, outcome: Outcome, now: float) -> None:
        self.stats.outcomes[outcome.kind] = self.stats.outcomes.get(outcome.kind, 0) + 1
        if flight.urgent is False:
            if outcome.kind in ("expired", "error"):
                self.slowdown = min(4.0, self.slowdown * 1.25)
            elif outcome.kind == "answers":
                self.slowdown = max(1.0, self.slowdown * 0.98)
        rec = self.npcs.get(npc)
        if rec is None or rec.flight is not flight:
            return
        rec.flight = None
        if outcome.kind != "answers" or not outcome.answers:
            self._schedule(rec, now)
            return
        if rec.tier is Tier.OFF or rec.version != flight.version:
            self.stats.discarded += 1  # something happened meanwhile: a new pending request covers it
            if rec.pending is None:
                self._schedule(rec, now)
            return
        rec.last_decision = now
        self._schedule(rec, now)
        answer = outcome.answers.get(flight.purpose)
        if not answer:
            return
        choice, conf = answer["choice"], float(answer["confidence"])
        if flight.purpose == "plan":
            threshold = self.cfg.plan_threshold
        else:
            threshold = self.cfg.option_threshold.get(choice, self.cfg.react_threshold)
        if conf < threshold:
            self.stats.unsure += 1
            return
        if choice in NO_ACTION:
            self.stats.no_action += 1
            return
        if now - rec.last_applied.get(choice, float("-inf")) < self.cfg.cooldown_s.get(
            choice, self.cfg.default_cooldown_s
        ):
            self.stats.cooldown += 1
            return
        rec.last_applied[choice] = now
        self.stats.applied += 1
        decision = Decision(
            npc,
            flight.purpose,
            choice,
            conf,
            answer.get("probabilities", {}),
            flight.stimuli,
            latency_ms=(now - flight.sent) * 1000,
        )
        self.applier.apply(decision)
        if flight.urgent and self.cfg.ask_attitude:  # urgent events are about the player
            self._spawn(self._ask_attitude(npc, flight))

    async def _ask_attitude(self, npc: str, flight: Flight) -> None:
        sit = self.builder.attitude(flight.situation)
        out = await self.decider.decide(
            f"{flight.rid}a", "background", 0.0, self.cfg.deadline_ms["attitude"], sit.state, sit.questions
        )
        if out.kind == "answers" and out.answers and "attitude" in out.answers:
            a = out.answers["attitude"]
            self.applier.apply(
                Decision(
                    npc,
                    "attitude",
                    str(round(float(a["score"]))),
                    float(a["confidence"]),
                    a.get("probabilities", {}),
                    flight.stimuli,
                    attitude=float(a["score"]),
                )
            )

    def _schedule(self, rec: NpcRecord, now: float) -> None:
        if rec.tier is Tier.OFF:
            return
        lo, hi = self._period(rec.tier)
        rec.next_check = now + self.rng.uniform(lo, hi)

    def _period(self, tier: Tier) -> tuple[float, float]:
        if tier is Tier.NEAR:
            return self.cfg.near_period_s
        lo, hi = self.cfg.mid_period_s if tier is Tier.MID else self.cfg.far_period_s
        return lo * self.slowdown, hi * self.slowdown

    def _refill(self, now: float) -> None:
        self._tokens = min(self.cfg.bg_rate, self._tokens + (now - self._refilled) * self.cfg.bg_rate)
        self._refilled = now

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
