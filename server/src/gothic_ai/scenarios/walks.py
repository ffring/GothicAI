"""Native waynet travel to an unseen body and back to the hero."""

from __future__ import annotations

import time

from .. import vanilla
from ..dialogue import DialogueManager
from ..game import GameError, GameLink
from .common import _logged

CAVALORN = "BAU_4300_ADDON_CAVALORN"
BRAGO = "BDT_1014_BANDIT_L"
BANDITS = (BRAGO, "BDT_1013_BANDIT_L", "BDT_1015_BANDIT_L")
STAIRS = "NW_XARDAS_STAIRS_01"


async def check_walk(link: GameLink, dialogue: DialogueManager) -> dict[str, str]:
    """Check an occluded trip from Xardas's stairs into the bandit cave."""
    if dialogue.llm.name != "fake":
        raise GameError("scenario check_walk requires LLM=fake and a new game without SAVE")
    await link.wait_world()
    for npc in (CAVALORN, *BANDITS):
        await link.debug("skip_important", near=npc)
    for npc in BANDITS:
        await link.debug("kill", npc=npc)
    await link.debug("player_goto", waypoint=STAIRS)
    await link.debug("npc_goto", npc=CAVALORN, waypoint=STAIRS)
    await link.attach(CAVALORN)
    results: dict[str, str] = {}
    try:
        body = (await link.story_state(npcs=[BRAGO], near=CAVALORN))["npcs"][BRAGO]
        results["cant_see_body"] = (
            f"{'done' if body['seen'] is False else 'failed'} (distance={body['distance']:.0f} cm)"
        )
        if not results["cant_see_body"].startswith("done"):
            return _logged(results)

        started = time.monotonic()
        went = await link.act(CAVALORN, "go_to_npc", npc=BRAGO, radius=vanilla.LOOK_CM)
        elapsed = time.monotonic() - started
        await link.act(CAVALORN, "turn_to", npc=BRAGO)
        body = (await link.story_state(npcs=[BRAGO], near=CAVALORN))["npcs"][BRAGO]
        where = (await link.snapshot(CAVALORN, fresh=True)).npc
        results["went"] = (
            f"{'done' if went.ok and body['seen'] else 'failed'} "
            f"(status={went.status}, detail={went.detail}, distance={body['distance']:.0f} cm, "
            f"seen={body['seen']}, seconds={elapsed:.1f}, "
            f"waypoint={where.waypoint}, nearest={where.nearest_waypoint}, pos={where.pos})"
        )
        if not results["went"].startswith("done"):
            return _logged(results)

        started = time.monotonic()
        back = await link.act(CAVALORN, "go_to_npc", npc="PLAYER", radius=vanilla.BACK_CM)
        results["came_back"] = (
            f"{'done' if back.ok and back.distance is not None and back.distance <= 300 else 'failed'} "
            f"(status={back.status}, detail={back.detail}, distance={back.distance} cm, "
            f"seconds={time.monotonic() - started:.1f})"
        )
        return _logged(results)
    finally:
        await link.detach(CAVALORN)
