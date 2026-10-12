"""What an npc knows of the hero's word (docs/story-graph.md, stage 5), from the facts of the save branch.

A lie is a mechanic the player can read, not the model's mood: what the npc sees for itself is in the
situation of its quests (vanilla.py), and what it knows of the hero's honesty is here.

- lied: it caught him lying (`caught`) or found a lie of his out later (`found_out`). It takes his word
  no more: a quest is accepted only on what it sees for itself (quests.py and vanilla.py enforce it);
- heard: rumors that he lied to someone else (`rumor`);
- kept: claims of his to it that were true (`claim` with `truth`).

The last two the conversational model weighs in character.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .chronicle import Chronicle, Fact
from .llm import Tool, ToolCall
from .protocol import Snapshot
from .turn import ToolOutcome

if TYPE_CHECKING:
    from .dialogue import Conversation, DialogueManager

WORD_BROKEN = "На слово ты ему больше не веришь: засчитаешь только то, что увидишь сам."


@dataclass
class Record:
    """An npc's knowledge of the hero's word."""

    lied: list[str] = field(default_factory=list)  # lies to it it knows of: what and how it learned
    heard: list[str] = field(default_factory=list)  # rumors: whom he lied to, about what
    kept: list[str] = field(default_factory=list)  # true claims to it

    @property
    def broken(self) -> bool:
        return bool(self.lied)


class Trust:
    """The hero's word in the eyes of each npc: a TurnExtension with situation lines only."""

    def __init__(self, chronicle: Chronicle) -> None:
        self.chronicle = chronicle

    def of(self, npc: str) -> Record:
        npc = npc.upper()
        facts = self.chronicle.facts()
        titles = _titles(facts)
        r = Record()
        for f in facts:
            quest = f.get("quest", "")
            title = f.get("title") or titles.get(quest) or quest
            match f.get("fact"):
                case "found_out" | "caught" if f.get("npc") == npc:
                    how = f.get("how", "")
                    r.lied.append(f"«{title}»{f' — {how}' if how else ''}")
                case "rumor" if f.get("to") == npc:
                    r.heard.append(f"{f.get('name') or f.get('from', '')} («{title}»)")
                case "claim" if f.get("npc") == npc and f.get("truth"):
                    r.kept.append(f"«{title}»")
        r.heard = list(dict.fromkeys(r.heard))
        return r

    def broken(self, npc: str) -> bool:
        return self.of(npc).broken

    # --- the conversation (TurnExtension) ------------------------------------------------------

    async def prepare(self, npc: str) -> None:
        pass  # the chronicle is here already

    def situation(self, npc: str) -> str | None:
        r = self.of(npc)
        lines = []
        if r.lied:
            lines.append(f"- он уже лгал тебе: {'; '.join(r.lied)}. {WORD_BROKEN}")
        if r.heard:
            lines.append(f"- ты слышал, что он обманул других: {', '.join(r.heard)}")
        if r.kept:
            lines.append(f"- он уже говорил тебе правду: {', '.join(r.kept)}")
        return "Что ты знаешь о честности героя:\n" + "\n".join(lines) if lines else None

    def tools(self, npc: str) -> list[Tool]:
        return []

    async def handle(
        self, dialogue: DialogueManager, conv: Conversation, call: ToolCall, snap: Snapshot | None
    ) -> ToolOutcome | None:
        return None


def _titles(facts: list[Fact]) -> dict[str, str]:
    """Quest -> its title, from the facts that name it (the director's quests, the lies)."""
    out: dict[str, str] = {}
    for f in facts:
        quest = f.get("quest") or (f.get("id") if f.get("fact") == "quest" else None)
        if quest and f.get("title"):
            out.setdefault(quest, f["title"])
    return out
