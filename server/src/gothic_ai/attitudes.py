"""Live hostility checks shared by the director and the chain author's speaking cast."""

from collections.abc import Iterable

from .game import GameLink


async def non_hostile(link: GameLink, npcs: Iterable[str], *, context: str) -> set[str]:
    """One read-only batch; unknown results cannot qualify someone for a speaking part.

    NPC_GETATTITUDE can return a personal neutral attitude even for hostile guilds. Check both,
    using WLD_GETGUILDATTITUDE: NPC_GETGUILDATTITUDE has no binding in the live VM. Our camp bandits
    remain combat roles even if their personal attitude or the hero's guild changes.
    """
    names = list(dict.fromkeys(npcs))
    if not names:
        return set()
    exprs = []
    for npc in names:
        guild = ["field", ["inst", npc], "GUILD"]
        checks = [
            ["!=", ["call", "NPC_GETATTITUDE", ["inst", npc], ["inst", "HERO"]], ["var", "ATT_HOSTILE"]],
            [
                "!=",
                ["call", "WLD_GETGUILDATTITUDE", guild, ["field", ["inst", "HERO"], "GUILD"]],
                ["var", "ATT_HOSTILE"],
            ],
        ]
        if npc.startswith("DIR_NPC_"):
            checks.append(["!=", guild, ["var", "GIL_BDT"]])
        exprs.append(["&&", *checks])
    values = await link.story_eval(context, exprs)
    return {npc for npc, value in zip(names, values, strict=True) if value == 1}
