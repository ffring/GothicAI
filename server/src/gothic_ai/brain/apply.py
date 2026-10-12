"""Decisions and what happens to them: `GameApplier` turns them into soft actions in the game (npc_act,
queued like script actions; the routine resumes by itself), `LogApplier` only logs them."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..game import GameLink

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Decision:
    npc: str
    question: str  # "react" or "plan"
    choice: str
    confidence: float
    probabilities: dict[str, float]
    stimuli: tuple[str, ...] = field(default_factory=tuple)  # kinds that led to it
    attitude: float | None = None  # expected level 0..4 when it was asked
    latency_ms: float = 0.0  # from the request leaving the planner to the answer
    target: str | None = None  # whom a fight or flight is about (the newest threat's `other`)


class Applier(Protocol):
    def apply(self, decision: Decision) -> None: ...


class LogApplier:
    def __init__(self, keep: int = 200) -> None:
        self.recent: deque[Decision] = deque(maxlen=keep)

    def apply(self, decision: Decision) -> None:
        self.recent.append(decision)
        log.info(
            "decision %s %s=%s (%.2f) after %s in %.0f ms",
            decision.npc,
            decision.question,
            decision.choice,
            decision.confidence,
            ",".join(decision.stimuli) or "timer",
            decision.latency_ms,
        )


# choice -> soft actions; voiced SVM lines are the npc's own recordings ("$NAME" in the scripts)
ACTIONS: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "look_at": [("look_at", {"ms": 3000})],
    "greet": [("play_ani", {"name": "T_GREETNOV"})],
    "approach_talk": [("go_to_npc", {})],
    "step_away": [("turn_away", {})],
    "tell_to_leave": [("say_svm", {"svm": "GETOUTOFHERE"})],
    "thank": [("play_ani", {"name": "T_YES"})],
    "call_help": [("say_svm", {"svm": "HELP"})],
    "call_guards": [("say_svm", {"svm": "GUARDS"})],
    # the game's own combat and fleeing, against the threat (protocol v6)
    "fight": [("fight", {"npc": "{target}"})],
    "flee": [("flee", {"npc": "{target}"})],
}
# "comment": the line fitting the newest event; without one the npc just looks at the player
COMMENT_SVM = {
    "enter_room": "GETOUTOFHERE",
    "weapon_drawn": "WEAPONDOWN",
    "theft": "DIRTYTHIEF",
    "magic": "STOPMAGIC",
    "item_given": "NOTBAD",
    "attack": "NEVERHITMEAGAIN",
    "monster_near": "HELP",
    "monster_attack": "HELP",
    "npc_attack": "HELP",
    "fight_nearby": "OHMYGODITSAFIGHT",
}


def actions_for(decision: Decision) -> list[tuple[str, dict[str, Any]]]:
    if decision.question != "react":
        return []  # plans and attitudes have no game action yet
    if decision.choice == "comment":
        svm = next((COMMENT_SVM[k] for k in decision.stimuli if k in COMMENT_SVM), None)
        return [("say_svm", {"svm": svm})] if svm else ACTIONS["look_at"]
    acts = ACTIONS.get(decision.choice, [])
    if any(v == "{target}" for _, args in acts for v in args.values()):
        if not decision.target:
            return []  # a fight or flight without someone to fight or flee from
        acts = [
            (a, {k: decision.target if v == "{target}" else v for k, v in args.items()}) for a, args in acts
        ]
    return acts


class GameApplier(LogApplier):
    """Sends decisions to the game as soft actions."""

    def __init__(self, link: GameLink, keep: int = 200) -> None:
        super().__init__(keep)
        self.link = link
        self._tasks: set[asyncio.Task[None]] = set()

    def apply(self, decision: Decision) -> None:
        super().apply(decision)
        acts = actions_for(decision)
        if acts:
            task = asyncio.get_running_loop().create_task(self._send(decision.npc, acts))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _send(self, npc: str, acts: list[tuple[str, dict[str, Any]]]) -> None:
        try:
            for action, args in acts:
                await self.link.npc_act(npc, action, **args)  # type: ignore[arg-type]
        except Exception as err:  # the game went away: nothing to do
            log.debug("npc_act for %s failed: %s", npc, err)
