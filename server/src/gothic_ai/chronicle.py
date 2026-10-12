"""What really happened (docs/story-graph.md, stage 4): code owns the truth, npc's own their beliefs.

Facts are JSON events in the memory log of the save branch (`kind='fact'`), so a load brings back
exactly what had happened before that save. Each has a `fact` kind:

- `died`: an npc died (`npc`, `tag` for the director's creatures, `killer`: PLAYER or an npc);
- `spawned`: the director put `count` creatures of `instance` into the world under `tag`;
- the quests (quests.py) add `quest`, `paid`, `belief`, `lie`, `found_out`, `rumor`.

The game also reports, once a second, how many of each tag still live and how far the nearest one is
from the npc's being watched (`story_world`): the rules that find lies out look at that.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from .game import GameError, GameLink
from .memory import MemoryStore
from .protocol import GameMessage, MemoryCmd, NpcDied, Session, StoryWorld

log = logging.getLogger(__name__)

Fact = dict[str, Any]


@dataclass
class Tag:
    """The director's creatures under one tag, as the game last reported them."""

    alive: int = 0
    dead: int = 0
    near: dict[str, float] = field(default_factory=dict)  # watched npc -> cm to the nearest living one


class Chronicle:
    def __init__(self, link: GameLink, memory: MemoryStore) -> None:
        self.link = link
        self.memory = memory
        self.branch: str | None = None
        self.tags: dict[str, Tag] = {}
        self.on_fact: list[Callable[[Fact], None]] = []  # every new fact, after it is written
        self._background: set[asyncio.Task[Any]] = set()
        link.listeners.append(self._on_message)
        if link.session is not None:
            self._on_session(link.session)

    def record(self, fact: Fact) -> None:
        if self.branch is None:
            log.warning("no session: fact not kept %s", fact)
            return
        seq = self.memory.add_fact(self.branch, json.dumps(fact, ensure_ascii=False))
        log.info("fact: %s", fact)
        # the head goes into the next save, so a load brings this fact back
        self._spawn(self.link.send(MemoryCmd(branch=self.branch, seq=seq)))
        for listener in self.on_fact:
            listener(fact)

    def facts(self, kind: str | None = None) -> list[Fact]:
        if self.branch is None:
            return []
        out = [json.loads(f) for f in self.memory.facts(self.branch)]
        return [f for f in out if kind is None or f.get("fact") == kind]

    def killed(self, tag: str, by: str | None = None) -> int:
        """How many of the tag died (by `by`, e.g. PLAYER), as written down."""
        return sum(
            1 for f in self.facts("died") if f.get("tag") == tag and (by is None or f.get("killer") == by)
        )

    def near(self, tag: str, npc: str) -> float | None:
        """cm from the npc to the nearest living creature of the tag; None: none lives, or not watched."""
        state = self.tags.get(tag)
        return state.near.get(npc.upper()) if state else None

    def _on_message(self, msg: GameMessage) -> None:
        match msg:
            case Session():
                self._on_session(msg)
            case NpcDied():
                self.record(
                    {"fact": "died", "npc": msg.npc, "name": msg.name, "tag": msg.tag, "killer": msg.killer}
                )
            case StoryWorld():
                self.tags = {
                    t.tag: Tag(t.alive, t.dead, {n.npc.upper(): n.distance for n in t.near}) for t in msg.tags
                }

    def _on_session(self, session: Session) -> None:
        self.branch = session.branch
        self.memory.ensure_branch(session.branch, session.parent, session.parent_seq)
        self.tags.clear()  # the game reports them again for this world

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        async def run() -> None:
            try:
                await coro
            except GameError:
                pass  # the game is gone; it learns the head again on reconnect

        task = asyncio.create_task(run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
