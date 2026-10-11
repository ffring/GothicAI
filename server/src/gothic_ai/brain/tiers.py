"""Tiers: who thinks how often. Boundaries follow the engine's own AI ranges (OpenGothic perceives within
30 m and simplifies beyond); caps keep a crowd around the player from blowing the budget."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum


class Tier(IntEnum):
    NEAR = 1  # within ~20 m or in view within 30 m: events + re-checks every 2-10 s
    MID = 2  # 20-30 m: salient events, re-check every 30-60 s
    FAR = 3  # beyond 30 m, the engine does not perceive there: a plan once per game hour
    OFF = 9  # in a conversation, under control, fighting or down: not ours


@dataclass(frozen=True)
class TierConfig:
    near_m: float = 20
    view_m: float = 30  # on screen counts as near up to this distance
    mid_m: float = 30
    hysteresis: float = 0.15  # leave a tier only this much further out than its border
    near_cap: int = 12
    mid_cap: int = 30


@dataclass(frozen=True)
class Placement:
    id: str
    distance_m: float
    in_view: bool
    active: bool  # following its routine (not in a dialog, fight, ...)
    importance: float


def raw_tier(p: Placement, prev: Tier | None, cfg: TierConfig) -> Tier:
    if not p.active:
        return Tier.OFF
    # a npc already in a tier keeps it until it is clearly outside: no flapping at the border
    near_m, view_m, mid_m = cfg.near_m, cfg.view_m, cfg.mid_m
    if prev is Tier.NEAR:
        near_m, view_m = near_m * (1 + cfg.hysteresis), view_m * (1 + cfg.hysteresis)
    if prev in (Tier.NEAR, Tier.MID):
        mid_m *= 1 + cfg.hysteresis
    if p.distance_m <= near_m or (p.in_view and p.distance_m <= view_m):
        return Tier.NEAR
    if p.distance_m <= mid_m:
        return Tier.MID
    return Tier.FAR


def assign_tiers(placements: Iterable[Placement], prev: dict[str, Tier], cfg: TierConfig) -> dict[str, Tier]:
    out: dict[str, Tier] = {}
    by_tier: dict[Tier, list[Placement]] = {Tier.NEAR: [], Tier.MID: [], Tier.FAR: []}
    for p in placements:
        t = raw_tier(p, prev.get(p.id), cfg)
        if t is Tier.OFF:
            out[p.id] = t
        else:
            by_tier[t].append(p)
    # over the cap, the least important move one tier out
    for tier, cap, lower in ((Tier.NEAR, cfg.near_cap, Tier.MID), (Tier.MID, cfg.mid_cap, Tier.FAR)):
        ranked = sorted(by_tier[tier], key=lambda p: (-p.importance, p.distance_m))
        by_tier[tier] = ranked[:cap]
        by_tier[lower].extend(ranked[cap:])
    for tier, ps in by_tier.items():
        for p in ps:
            out[p.id] = tier
    return out


def importance(
    distance_m: float, in_view: bool, since_interaction_s: float | None, weight: float = 0.0
) -> float:
    """How much it matters that this npc reacts well: close, seen, recently dealt with the player, story."""
    score = max(0.0, 1.0 - distance_m / 60.0) * 2.0
    if in_view:
        score += 1.0
    if since_interaction_s is not None:
        score += 2.0 * 0.5 ** (since_interaction_s / 30.0)  # half-life 30 s
    return score + weight
