"""What an npc's own initiative may not do to the plot (docs/npc-tools.md, wave 2).

An npc that walks up to the hero by itself leaves its spot, and some must not: the plot's npc's (the same
rules the director keeps to), the guards of a passage, whose dialogs warn and attack by where SELF stands,
and whoever the scripts have leading or following the hero right now. They may still speak when the hero
is close. Works without the director; without a story graph the plot is known by instance prefixes only.
"""

from __future__ import annotations

from collections.abc import Iterable

from .gamedata.storygraph import StoryGraph, walk
from .validator import PLOT_NPCS, Rules

# the scripts' guard-passage idiom: FIRSTWARN, SECONDWARN, ATTACK by SELF's nearest waypoint
POST_CALL = "NPC_GETNEARESTWP"
# all a dialog may read of where SELF stands; too broad to block by (Vino's healing reads it)
SELF_PLACE_CALLS = ("NPC_GETDISTTOWP", "NPC_GETNEARESTWP", "NPC_GETDISTTONPC")
POST_STATES = ("ZS_GUARD_PASSAGE",)  # TA_GUARD_PASSAGE
# ZS_GUIDE_PLAYER, ZS_FOLLOW_PLAYER: the scripts have it lead or follow the hero now (the roster's state)
ESCORT_STATES = ("ZS_GUIDE", "ZS_FOLLOW")


class StoryGuard:
    """Whose body stays where it is."""

    def __init__(self, graph: StoryGraph | None) -> None:
        self.graph = graph
        self.rules = Rules.of(graph) if graph is not None else None
        self.posts = set(_readers(graph, (POST_CALL,))) if graph is not None else set()

    def plot(self, npc: str) -> bool:
        if self.rules is not None:
            return self.rules.plot_npc(npc)
        return npc.upper().startswith(PLOT_NPCS)

    def body(self, npc: str, state: str = "") -> str | None:
        """Why the npc must stay where it is (Russian, for the journal and the console), or None if it may
        walk. `state` is its script state from the roster."""
        state = state.upper()
        if self.plot(npc):
            return "сюжетный: только слова"
        if npc.upper() in self.posts or state.startswith(POST_STATES):
            return "стоит на посту"
        if state.startswith(ESCORT_STATES):
            return "ведёт или сопровождает героя по скриптам"
        return None

    def placed(self) -> dict[str, list[str]]:
        """Every npc whose dialogs read where SELF stands, with those dialogs: for review by hand (console
        `guard`), it blocks nothing."""
        if self.graph is None:
            return {}
        return dict(sorted(_readers(self.graph, SELF_PLACE_CALLS).items()))


def _readers(graph: StoryGraph, calls: Iterable[str]) -> dict[str, list[str]]:
    """npc -> its dialogs whose condition calls one of `calls` on SELF."""
    wanted = set(calls)
    out: dict[str, list[str]] = {}
    for n in graph.nodes.values():
        if n.is_choice:
            continue
        for x in walk(n.condition):
            if x[0] == "call" and x[1] in wanted and x[2:3] == [["inst", "SELF"]]:
                out.setdefault(n.npc, []).append(n.id)
                break
    return out
