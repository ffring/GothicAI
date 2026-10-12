"""Changes to the story graph in the game (docs/story-graph.md, stage 3).

Dialogs can be switched off, get a condition on top of their script one, or be added: the director will
do it (stage 5), for now the console does. Each change is one operation (see protocol.GraphPatchCmd). It
goes to the game and into the memory log of the save branch, so a load brings back exactly the changes
made before that save, like the rest of the world; the game gets them again whenever a session starts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Coroutine
from typing import Any

from .game import GameError, GameLink
from .memory import MemoryStore
from .protocol import DialogChosen, GameMessage, MemoryCmd, Session

log = logging.getLogger(__name__)

Op = dict[str, Any]


class StoryPatches:
    def __init__(self, link: GameLink, memory: MemoryStore) -> None:
        self.link = link
        self.memory = memory
        self.branch: str | None = None
        self._background: set[asyncio.Task[Any]] = set()
        link.listeners.append(self._on_message)
        if link.session is not None:
            self._on_session(link.session)

    def ops(self) -> list[Op]:
        """The changes on the current branch, oldest first."""
        return [json.loads(op) for op in self.memory.patches(self.branch)] if self.branch else []

    async def apply(self, op: Op) -> None:
        """One change: the game takes it (or GameError says why not), then the branch remembers it."""
        if self.branch is None:
            raise GameError("the game has not reported a session yet")
        await self.link.patch_graph([op])
        self._record(op)

    def _on_message(self, msg: GameMessage) -> None:
        match msg:
            case Session():
                self._on_session(msg)
            case DialogChosen(made=True):
                # the hero chose an added dialog: it is not offered again, also after a load
                self._record({"op": "told", "node": msg.node})
            case DialogChosen():
                log.info("dialog chosen: %s %s", msg.npc, msg.node)

    def _on_session(self, session: Session) -> None:
        self.branch = session.branch
        self.memory.ensure_branch(session.branch, session.parent, session.parent_seq)
        ops = self.ops()
        if ops:
            log.info("story graph: %d change(s) on branch %s", len(ops), session.branch)
        # also when there are none: whatever the game had belongs to another line of play
        self._spawn(self.link.patch_graph(ops, replace=True))

    def _record(self, op: Op) -> None:
        if self.branch is None:
            return
        seq = self.memory.add_patch(self.branch, json.dumps(op, ensure_ascii=False))
        # the head goes into the next save: a load must bring this change back
        self._spawn(self.link.send(MemoryCmd(branch=self.branch, seq=seq)))

    def _spawn(self, coro: Coroutine[Any, Any, Any]) -> None:
        async def run() -> None:
            try:
                await coro
            except (GameError, TimeoutError) as err:
                log.warning("story graph: %s", err)

        task = asyncio.create_task(run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
