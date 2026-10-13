"""How often the conversational model may be called when nobody asked it directly.

The player's phrases always get an answer. Calls the server makes on its own — words for an incident,
summaries of old memories — come out of token buckets refilled per minute: one per npc and one for the
whole server. The player's turns are counted too (they use the same plan quota), but never refused.
One call for two npc's at once (their scene, gossip.py) takes from both of their buckets.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    capacity: float
    tokens: float
    at: float

    def refill(self, now: float, per_s: float) -> None:
        self.tokens = min(self.capacity, self.tokens + (now - self.at) * per_s)
        self.at = now


@dataclass
class Budget:
    per_npc_per_min: float = 6.0
    total_per_min: float = 20.0
    clock: Callable[[], float] = time.monotonic
    _npcs: dict[str, _Bucket] = field(default_factory=dict)
    _total: _Bucket | None = None

    def _buckets(self, npc: str) -> tuple[_Bucket, _Bucket]:
        now = self.clock()
        if self._total is None:
            self._total = _Bucket(self.total_per_min, self.total_per_min, now)
        b = self._npcs.setdefault(npc, _Bucket(self.per_npc_per_min, self.per_npc_per_min, now))
        b.refill(now, self.per_npc_per_min / 60)
        self._total.refill(now, self.total_per_min / 60)
        return b, self._total

    def allow(self, npc: str) -> bool:
        """Take one call for `npc` if both buckets have it."""
        own, total = self._buckets(npc)
        if own.tokens < 1 or total.tokens < 1:
            return False
        own.tokens -= 1
        total.tokens -= 1
        return True

    def can(self, npc: str) -> bool:
        """Whether a call would be allowed now, without taking it."""
        own, total = self._buckets(npc)
        return own.tokens >= 1 and total.tokens >= 1

    def can_all(self, npcs: Iterable[str]) -> bool:
        """Whether one call for several npc's (a scene) would be allowed now, without taking it."""
        own, total = self._all(npcs)
        return total is not None and total.tokens >= 1 and all(b.tokens >= 1 for b in own)

    def allow_all(self, npcs: Iterable[str]) -> bool:
        """One call for several npc's (a scene): one token from each one's bucket and one from the total,
        or nothing at all if any of them lacks it."""
        own, total = self._all(npcs)
        if total is None or total.tokens < 1 or any(b.tokens < 1 for b in own):
            return False
        for b in own:
            b.tokens -= 1
        total.tokens -= 1
        return True

    def _all(self, npcs: Iterable[str]) -> tuple[list[_Bucket], _Bucket | None]:
        """Each npc's bucket (once, however often it is named) and the total, refilled; no npc's: None."""
        pairs = [self._buckets(npc) for npc in dict.fromkeys(npcs)]
        return [own for own, _ in pairs], pairs[0][1] if pairs else None

    def note(self, npc: str) -> None:
        """A call that was not up to us (the player spoke): counted, never refused."""
        own, total = self._buckets(npc)
        own.tokens = max(0.0, own.tokens - 1)
        total.tokens = max(0.0, total.tokens - 1)
