"""What an npc noticed, how urgent it is and how long it matters.

Threats and crimes (weapon drawn, theft, attack, magic) are left to the game's own scripts: the eval
showed the decision model handles them badly (~45-55%), while the scripts already react correctly.
They are kept as context for later decisions but never trigger one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventPolicy:
    text: str  # English, for the state; may use {other}, {detail}, {dist}
    ttl_s: float  # how long it stays relevant
    urgent: bool = False  # the player is right there and expects a reaction now
    salient: bool = False  # worth a decision for MID npc's too (NEAR ones react to any event)
    scripted: bool = False  # the game's scripts react; context only


POLICIES: dict[str, EventPolicy] = {
    "player_near": EventPolicy("The hero came close to you ({dist} away).", 15),
    "player_speech": EventPolicy('The hero said nearby: "{detail}"', 20, urgent=True, salient=True),
    "enter_room": EventPolicy("The hero walked into your house.", 30, urgent=True, salient=True),
    "item_given": EventPolicy("The hero gave you {detail}.", 60, urgent=True, salient=True),
    "item_dropped": EventPolicy("The hero dropped {detail} on the ground near you.", 30, salient=True),
    "player_sleeps": EventPolicy("The hero lay down to sleep nearby.", 60),
    "player_sits": EventPolicy("The hero sat down nearby.", 30),
    "weapon_drawn": EventPolicy("The hero drew a weapon near you.", 20, scripted=True),
    "threatened": EventPolicy("The hero aims a weapon at you.", 10, scripted=True),
    "theft": EventPolicy("The hero stole {detail}.", 300, scripted=True),
    "attack": EventPolicy("The hero attacked {detail}.", 300, scripted=True),
    "magic": EventPolicy("The hero cast a spell nearby.", 30, scripted=True),
    "murder": EventPolicy("The hero killed {detail}.", 600, scripted=True),
    # threats from others (v6); left to the scripts for now, the behavioral model learns them (M5c/M5d)
    "monster_near": EventPolicy("{other} is prowling nearby ({dist} away).", 20, scripted=True),
    "monster_attack": EventPolicy("{other} attacks {detail}.", 30, scripted=True),
    "npc_attack": EventPolicy("{other} attacks you.", 60, scripted=True),
    "hero_attacked": EventPolicy("{other} attacks the hero.", 30, scripted=True),
}
DEFAULT_POLICY = EventPolicy("Something happened: {kind} ({detail}).", 15)


def policy(kind: str) -> EventPolicy:
    return POLICIES.get(kind, DEFAULT_POLICY)


@dataclass(frozen=True)
class Stimulus:
    kind: str
    at: float  # monotonic time it arrived
    other: str = "PLAYER"
    distance_cm: float | None = None
    detail: str | None = None
    other_name: str | None = None  # shown name of `other` (not the player)

    @property
    def policy(self) -> EventPolicy:
        return policy(self.kind)

    def alive(self, now: float) -> bool:
        return now - self.at <= self.policy.ttl_s

    def describe(self, now: float) -> str:
        other = "the hero" if self.other == "PLAYER" else (self.other_name or self.other)
        dist = f"{self.distance_cm / 100:.0f} m" if self.distance_cm is not None else "a few metres"
        detail = {"you": "you", "PLAYER": "the hero"}.get(self.detail or "", self.detail or "something")
        text = self.policy.text.format(other=other, detail=detail, dist=dist, kind=self.kind)
        ago = now - self.at
        return text if ago < 3 else f"{text} ({ago:.0f} s ago)"


def merge(stimuli: list[Stimulus], new: Stimulus, now: float, keep: int = 5) -> list[Stimulus]:
    """Alive stimuli plus the new one; the same kind from the same source is replaced, not repeated."""
    alive = [s for s in stimuli if s.alive(now) and not (s.kind == new.kind and s.other == new.other)]
    alive.append(new)
    # most urgent first, then the newest
    alive.sort(key=lambda s: (not s.policy.urgent, -s.at))
    return alive[:keep]
