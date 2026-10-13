"""How often the conversational model may be called when nobody asked it directly.

The player's phrases always get an answer. Calls the server makes on its own — words for an incident,
summaries of old memories — come out of token buckets refilled per minute: one per npc and one for the
whole server. The player's turns are counted too (they use the same plan quota), but never refused.
"""

from __future__ import annotations

import time
from collections.abc import Callable
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

    def note(self, npc: str) -> None:
        """A call that was not up to us (the player spoke): counted, never refused."""
        own, total = self._buckets(npc)
        own.tokens = max(0.0, own.tokens - 1)
        total.tokens = max(0.0, total.tokens - 1)
