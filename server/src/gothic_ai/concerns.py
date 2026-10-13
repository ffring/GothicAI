"""What an npc has on its mind about the hero (docs/npc-tools.md, wave 2): concerns, not needs.

A concern comes only from the facts of the save branch, so an npc wants something of the hero only for
what really happened between them:

- debt_broken: he did not pay back his debt to it by its day (deals.py);
- lied: it found a lie of his out or caught him lying (quests.py), and did not forgive it in a deal or take
  amends for it;
- debt_due: today is the last day of his debt to it;
- witness: someone it asked far away (witnesses.py) saw the hero, and it has not spoken to him of it yet.

The most pressing one is the npc's agenda for the behavioral model (English, in the planner's wording) and
the occasion it may come up to the hero with (Russian, for the conversational model). Rumors of his lies to
others are not concerns.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .chronicle import Chronicle, Fact
from .deals import GOLD, Deals, Debt
from .game import GameLink
from .protocol import GameMessage, Loading, Session

RANK = {"debt_broken": 0, "lied": 1, "witness": 1, "debt_due": 2}
# the facts that change what an npc has on its mind; deals.py updates its debts before it records them
FACTS = frozenset(
    {"debt", "debt_paid", "broken", "deal", "found_out", "caught", "amends", "testimony", "initiative"}
)
# the agendas quests.py sets when the lie comes out
LIED_EN = {
    "found_out": "make the hero answer for lying to you and taking your reward",
    "caught": "make the hero prove what he claims; never take his word again",
}


@dataclass(frozen=True)
class Concern:
    key: str  # debt_broken:<debt id> | lied:<lie key> | debt_due:<debt id>
    npc: str
    rank: int  # 0 is the most pressing
    en: str  # the agenda for the behavioral model: English, at most 15 words
    ru: str  # the occasion, for the conversational model's cue


class Concerns:
    """Each npc's concerns about the hero, kept until a fact or a new game day changes them."""

    def __init__(self, link: GameLink, chronicle: Chronicle, deals: Deals) -> None:
        self.deals = deals
        self.on_change: list[Callable[[str], None]] = []  # an npc whose concerns may have changed
        self.extra: list[Callable[[str], list[Concern]]] = []  # more kinds, from other desks (witnesses.py)
        self._cache: dict[str, list[Concern]] = {}
        chronicle.on_fact.append(self._on_fact)
        deals.on_day.append(self._on_day)
        deals.on_reload.append(self._on_reload)
        link.listeners.append(self._on_message)

    def of(self, npc: str) -> list[Concern]:
        """The most pressing first."""
        npc = npc.upper()
        if (out := self._cache.get(npc)) is None:
            out = self._cache[npc] = self._compute(npc)
        return out

    def top(self, npc: str) -> Concern | None:
        return next(iter(self.of(npc)), None)

    def agenda(self, npc: str) -> str | None:
        return c.en if (c := self.top(npc)) is not None else None

    def current(self, npc: str, agenda: str | None) -> str | None:
        """The agenda the conversation keeps for the npc, unless it is the one quests.py set for a lie that is
        settled now (forgiven in a deal, amends made): that one ends with its concern."""
        if agenda in LIED_EN.values() and not any(c.key.startswith("lied:") for c in self.of(npc)):
            return None
        return agenda

    def _compute(self, npc: str) -> list[Concern]:
        out = []
        for d in self.deals.debts.values():
            if d.npc != npc or d.paid:
                continue
            if d.broken:
                ru = f"герой не вернул тебе долг в срок: {d.what()}"
                out.append(Concern(f"debt_broken:{d.id}", npc, RANK["debt_broken"], _debt_en(d), ru))
            elif self.deals.day == d.due:
                ru = f"сегодня последний день: герой должен вернуть тебе {d.give.text()}"
                out.append(Concern(f"debt_due:{d.id}", npc, RANK["debt_due"], _debt_en(d), ru))
        known, forgiven, _ = self.deals.lies_of(npc)
        for key, lie in known.items():
            if lie.about != npc or key in forgiven:
                continue  # a rumor of a lie to someone else is not its own grievance
            en = LIED_EN["found_out" if key.startswith("found_out:") else "caught"]
            out.append(Concern(f"lied:{key}", npc, RANK["lied"], en, f"герой {lie.words(npc)}"))
        for more in self.extra:
            out += more(npc)
        return sorted(out, key=lambda c: c.rank)

    def _on_fact(self, f: Fact) -> None:
        if f.get("fact") not in FACTS:
            return
        for npc in dict.fromkeys(str(n).upper() for n in (f.get("npc"), f.get("to")) if n):
            self._changed(npc)

    def _on_day(self, day: int) -> None:
        """A debt may be due today: its lender's concerns change."""
        for npc in dict.fromkeys(d.npc for d in self.deals.debts.values() if not d.paid and not d.broken):
            self._changed(npc)

    def _on_reload(self) -> None:
        """The debts were read again: every lender's concerns may have changed."""
        self._cache.clear()
        for npc in dict.fromkeys(d.npc.upper() for d in self.deals.debts.values()):
            self._changed(npc)

    def changed(self, npc: str) -> None:
        """The npc's concerns may be other now (time passed: witnesses.py)."""
        self._changed(npc.upper())

    def _changed(self, npc: str) -> None:
        self._cache.pop(npc, None)
        for listener in self.on_change:
            listener(npc)

    def _on_message(self, msg: GameMessage) -> None:
        match msg:
            case Session() | Loading():
                self._cache.clear()  # another branch, other facts


def _debt_en(d: Debt) -> str:
    """The lender's agenda: by tonight, or his day has passed."""
    gold = d.give.item == GOLD
    if d.broken:
        owed = f"the {d.give.count} gold he owes you" if gold else "what he borrowed from you"
        return f"make the hero pay back {owed}; his day has passed"
    owed = f"{d.give.count} gold to you" if gold else "what he borrowed from you"
    return f"remind the hero to pay back {owed} by tonight"
