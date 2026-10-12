"""Incidents: what happens to an npc we control, as one story rather than a stream of events.

Events close together (an attack, the next hit, the hero hurt too) make one incident; a quiet gap ends
it. Each change bumps the incident's version: a late answer of the slow model about an incident that
is over, or has changed since it was asked, is dropped.

Escalation — whether the conversational model is called, in parallel with the body's instant
reaction — is a rule here, not a question to the behavioral model (it answers that one poorly, see
evals/npc-decisions). All of these must hold:
1. the incident has a salient event (worth words);
2. someone hears the words: the hero is within `hearing_cm`;
3. at most one call per incident, unless it changed and `recall_s` passed since the last call;
4. the budget allows it (the caller checks, see budget.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

INCIDENT_GAP_S = 20.0  # no new events this long: the incident is over
HEARING_CM = 2000.0
RECALL_S = 30.0


@dataclass(frozen=True)
class EventKind:
    text: str  # English, for the behavioral model; {other}, {victim}, {detail}
    note: str  # Russian, for the conversational model and memory
    urgent: bool = False  # a threat to life: the body reacts in the urgent lane
    salient: bool = False  # worth words


KINDS: dict[str, EventKind] = {
    "attacked": EventKind("{other} attacks you.", "{other} нападает на тебя", urgent=True, salient=True),
    "hero_attacked": EventKind(
        "{other} attacks the hero.", "{other} нападает на героя", urgent=True, salient=True
    ),
    "other_attacked": EventKind(
        "{other} attacks {victim} nearby.", "{other} нападает на: {victim}", salient=True
    ),
    "murder": EventKind(
        "{other} killed {victim} nearby.", "{other} убил: {victim}", urgent=True, salient=True
    ),
    "magic": EventKind("{other} casts a spell at you.", "{other} колдует на тебя", urgent=True, salient=True),
    "follow_ended": EventKind(
        "You stopped following the hero ({detail}).", "Ты перестал идти за героем ({detail})", salient=True
    ),
    "action_failed": EventKind("What you meant to do failed: {detail}.", "Не вышло: {detail}", salient=True),
    "down": EventKind("You were knocked down.", "Тебя сбили с ног"),
}


@dataclass(frozen=True)
class IncidentEvent:
    kind: str
    at: float
    other: str | None = None  # who did it: PLAYER, an npc id or a monster's symbol
    other_name: str | None = None
    victim: str | None = None
    detail: str | None = None

    @property
    def spec(self) -> EventKind:
        return KINDS[self.kind]

    def _fill(self, template: str, hero: str) -> str:
        def name(who: str | None, display: str | None) -> str:
            if who == "PLAYER":
                return hero
            return display or who or "someone"

        return template.format(
            other=name(self.other, self.other_name),
            victim=name(self.victim, None),
            detail=self.detail or "",
        )

    def describe_en(self) -> str:
        return self._fill(self.spec.text, "the hero")

    def describe_ru(self) -> str:
        return self._fill(self.spec.note, "герой")


@dataclass
class Incident:
    npc: str
    id: int
    started: float
    events: list[IncidentEvent] = field(default_factory=list)
    version: int = 0
    escalated_version: int | None = None
    escalated_at: float | None = None
    closed: bool = False

    @property
    def last(self) -> float:
        return self.events[-1].at if self.events else self.started

    @property
    def urgent(self) -> bool:
        return any(e.spec.urgent for e in self.events)

    @property
    def salient(self) -> bool:
        return any(e.spec.salient for e in self.events)

    def active(self, now: float, gap_s: float = INCIDENT_GAP_S) -> bool:
        return not self.closed and now - self.last < gap_s

    def hits_from(self, other: str, since: float) -> int:
        return sum(1 for e in self.events if e.kind == "attacked" and e.other == other and e.at >= since)

    def describe_ru(self) -> str:
        seen: list[str] = []
        for e in self.events:
            text = e.describe_ru()
            if text not in seen:
                seen.append(text)
        return "; ".join(seen)


class IncidentTracker:
    def __init__(self, gap_s: float = INCIDENT_GAP_S) -> None:
        self.gap_s = gap_s
        self.current: dict[str, Incident] = {}
        self._ids = 0

    def add(self, npc: str, event: IncidentEvent) -> Incident:
        inc = self.current.get(npc)
        if inc is None or not inc.active(event.at, self.gap_s):
            self._ids += 1
            inc = Incident(npc, self._ids, event.at)
            self.current[npc] = inc
        inc.events.append(event)
        inc.version += 1
        return inc

    def close(self, npc: str) -> None:
        inc = self.current.pop(npc, None)
        if inc is not None:
            inc.closed = True
            inc.version += 1

    def is_current(self, npc: str, incident_id: int, now: float) -> bool:
        """Still the same incident and still going on (for a late answer of the slow model)."""
        inc = self.current.get(npc)
        return inc is not None and inc.id == incident_id and inc.active(now, self.gap_s)


def escalation_reason(
    inc: Incident,
    now: float,
    hero_distance_cm: float | None,
    *,
    hearing_cm: float = HEARING_CM,
    recall_s: float = RECALL_S,
) -> str | None:
    """Why the conversational model should be called about this incident now (None: it should not)."""
    if inc.closed or not inc.salient:
        return None
    if hero_distance_cm is None or hero_distance_cm > hearing_cm:
        return None  # nobody would hear the words
    if inc.escalated_version is None:
        return "new incident"
    if inc.version != inc.escalated_version and now - (inc.escalated_at or 0.0) >= recall_s:
        return "incident changed"
    return None
