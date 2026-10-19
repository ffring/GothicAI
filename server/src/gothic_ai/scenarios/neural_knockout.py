from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .. import triggers
from ..bodies import Bodies
from ..bodypilot import BodyPilot, FightSummary
from ..dialogue import DialogueManager
from ..game import GameError, GameLink
from ..llm import ToolCall
from ..llm.fake import FakeBackend, FakeStep
from ..perception import BEASTS
from ..protocol import GameMessage, LineStarted, NpcSpec, RoutineEntry
from .common import _ByNpc, _m5b_player_weapon, _until, _until_async
from .neural_load import _policy_resets

log = logging.getLogger("gothic_ai.scenarios")


async def neural_knockout_segment(
    link: GameLink, dialogue: DialogueManager, pilot: BodyPilot,
) -> dict[str, str]:
    """A citizen's ordinary AR_NONE fight, real unconsciousness and native recovery."""
    if dialogue.llm.name != "fake":
        raise GameError("scenario neural-talk requires LLM=fake")
    await link.wait_world()
    npc = "DIR_NPC_NEURAL_TALK_CITIZEN"
    arena = "NW_XARDAS_TOWER_VALLEY_09"
    parked_at = "NW_FARM1_FIELD_06"
    hp_trigger = "DIR_NEURAL_TALK_KNOCKOUT_HP"
    results: dict[str, str] = {}
    lines: list[LineStarted] = []

    def check(name: str, ok: bool, detail: str) -> bool:
        key = f"knockout_{name}"
        results[key] = f"{'done' if ok else 'failed'} ({detail})"
        log.info("%-28s %s", key, results[key])
        return ok

    def heard(message: GameMessage) -> None:
        if isinstance(message, LineStarted) and message.npc.upper() == npc:
            lines.append(message)

    def summary_after(previous: set[int]) -> FightSummary | None:
        return next((summary for summary in pilot.summaries if (
            summary.npc == npc and summary.lease is not None and id(summary) not in previous
        )), None)

    await link.debug("player_goto", waypoint="NW_XARDAS_TOWER_VALLEY_01")
    await link.story_npc(NpcSpec(
        npc=npc, like="BDT_1002_BANDIT_L", at=arena, name="Мечник", guild="GIL_VLK",
        routine=[RoutineEntry(start="00:00", end="23:59", state="ZS_STAND_ARMSCROSSED", waypoint=arena)],
    ))
    await link.debug("skip_important", near=npc)
    for other in (await link.snapshot(npc, fresh=True)).nearby_npcs:
        if not other.dead and other.guild >= BEASTS:
            await link.debug("kill", instance=other.id, near=npc)
    for view in await link.npc_view([npc]):
        for seen in view.sees:
            if seen.guild >= BEASTS and seen.body != "dead":
                await link.debug("kill", instance=seen.id, near=npc)
    ready = await Bodies(link).act(
        npc, "start_activity", state="ZS_STAND_ARMSCROSSED", waypoint=arena,
        minutes=60, arrive=True, radius=100, timeout=15,
    )
    if ready.status != "arrived":
        raise GameError(f"neural-talk knockout preparation: {ready.status} {ready.detail}")
    weapon = ["call", "NPC_GETEQUIPPEDMELEEWEAPON", ["inst", "SELF"]]
    symbol, sword = await link.story_eval(npc, [
        ["sym", npc], ["&", ["field", weapon, "FLAGS"], ["var", "ITEM_SWD"]],
    ])
    if not sword:
        raise GameError("neural-talk knockout preparation: citizen has no one-handed sword")
    await link.debug("player_hp", hp=1200)
    await link.debug("player_equip", item="ITMW_1H_BAU_MACE")
    await _m5b_player_weapon(link, "none")
    await link.debug("teleport", near=npc, distance=250)
    if not await _until(lambda: npc in pilot.people and pilot.people[npc].distance <= 400, 10):
        raise GameError("neural-talk knockout preparation: citizen missing from roster")

    original_llm = dialogue.llm
    dialogue.llm = _ByNpc({"Мечник": FakeBackend(script=[FakeStep(
        "Хочешь проверить мою шпагу? Ну что ж, защищайся!", [ToolCall("set_intent", {
            "goal": "fight", "target": "PLAYER", "attitude": "hostile", "agenda": None,
        })],
    )], delay=0.15)}, FakeBackend("Хм.", delay=0))
    link.listeners.append(heard)
    last_row: dict[str, Any] = {}

    def observe(packet: dict[str, Any]) -> None:
        fight = pilot.fights.get(npc)
        if fight is None or fight.lease is None:
            return
        lease = fight.lease
        for row in packet["observations"]:
            if (row["entity_id"], row["session_epoch"], row["world_epoch"]) != (
                lease.entity_id, lease.session_epoch, lease.world_epoch,
            ) or row["target"]["entity_id"] != lease.target_entity_id:
                continue
            own, target = row["self"], row["target"]
            last_row.update({
                "game_ms": row["t_game_ms"], "npc_pos": own["position_world_cm"],
                "hero_pos": target["position_world_cm"], "hero_hp": target["hp"],
                "hero_max_hp": target["hp_max"], "hero_weapon": target["weapon_state"],
                "hero_down": target["down"], "distance_cm": target["fight_distance_cm"],
            })

    link.raw_body_listeners.append(observe)
    try:
        with _policy_resets() as resets:
            previous = {id(summary) for summary in pilot.summaries}

            def fight_ready() -> bool:
                fight = pilot.fights.get(npc)
                return bool(
                    fight and fight.lease and link.body_leases.get(fight.lease.entity_id) == fight.lease
                    and fight._decisions >= 5
                )

            await link.debug("type", text="Мечник, проверим твою шпагу.")
            ready = await _until(fight_ready, 25)
            fight = pilot.fights.get(npc)
            lease = fight.lease if fight else None
            client = fight.client if fight else None
            if not check("talk_to_attack", bool(
                ready and lines and lease and lease.npc == npc
                and lease.provider == "neural-body" and lease.mode == "realtime"
                and resets.get(client) and fight._fallbacks == 0
            ), f"lines={len(lines)}, symbol={symbol}, "
               f"lease={lease.control_generation if lease else 0}, "
               f"decisions={fight._decisions if fight else 0}, "
               f"fallback={fight._fallbacks if fight else 0}, resets={len(resets.get(client, []))}"):
                return results

            async def draw_hero() -> bool:
                try:
                    await link.debug("player_weapon", state="melee")
                    return True
                except GameError as error:
                    if "cannot switch weapons" not in str(error):
                        raise
                    return False

            drawn = await _until_async(draw_hero, bool, 8)
            if drawn:
                drawn = await _until(lambda: last_row.get("hero_weapon") == 2, 5)
            if not check("hero_draws", drawn,
                         f"drawn={int(drawn)}, weapon_state={last_row.get('hero_weapon')}, "
                         f"decisions={fight._decisions}, leases={len(link.body_leases)}"):
                return results

            # Native attribute 0 is current HP; leave maximum HP (slot 1) at
            # 1200 so the unconscious script can recover the hero normally.
            await link.patch_graph([triggers.trigger(hp_trigger, triggers.STATE, [{
                "do": "call", "call": [
                    "call", "NPC_CHANGEATTRIBUTE", ["inst", "HERO"], ["int", 0],
                    ["-", ["int", 1], ["field", ["inst", "HERO"], "ATTRIBUTE", 0]],
                ],
            }])])
            try:
                applied = await _until_async(link.triggers, lambda current: any(
                    item["id"] == hp_trigger and item["count"] >= 1 for item in current
                ), 5)
            finally:
                await link.patch_graph([triggers.removal(hp_trigger)])
            snap = await link.snapshot(npc, fresh=True)
            if not check("lower_current_hp", bool(
                applied and snap.player and snap.player.hp_max == 1200
            ), f"trigger_count={int(applied)}, hp={snap.player.hp if snap.player else None}, "
               f"hp_max={snap.player.hp_max if snap.player else None}"):
                return results

            down_at = time.monotonic()
            await _until(lambda: summary_after(previous) is not None, 35)
            summary = summary_after(previous)
            ended_at = time.monotonic()
            if not check("fight_to_down", bool(
                summary and summary.reason == "hero_down" and summary.decisions >= 5
                and summary.fallbacks == 0 and summary.release_confirmed
                and summary.release_seconds is not None and summary.release_seconds <= 2
            ), f"decisions={summary.decisions if summary else fight._decisions}, "
               f"fallback={summary.fallbacks if summary else fight._fallbacks}, "
               f"reason={summary.reason if summary else 'active'}, "
               f"release_s={summary.release_seconds if summary else None}, "
               f"transition_s={time.monotonic() - down_at:.3f}, row={last_row}"):
                return results

            async def hero_status() -> list[int]:
                return await link.story_eval(npc, [
                    ["call", "NPC_ISINSTATE", ["inst", "HERO"], ["sym", "ZS_UNCONSCIOUS"]],
                    ["call", "NPC_ISDEAD", ["inst", "HERO"]],
                ])

            unconscious, dead = await hero_status()
            snap = await link.snapshot(npc, fresh=True)
            if not check("real_unconscious", bool(
                unconscious == 1 and dead == 0 and snap.player and snap.player.hp > 0
            ), f"unconscious={unconscious}, dead={dead}, hp={snap.player.hp if snap.player else None}, "
               f"after_down_s={time.monotonic() - ended_at:.3f}"):
                return results

            await asyncio.sleep(max(0, ended_at + 2 - time.monotonic()))
            entry = pilot.people.get(npc)
            if not check("release", bool(
                not pilot.handles(npc) and not link.body_leases and not link.body_latest
                and not pilot._watch_npcs and not pilot._pending_unwatch
                and summary.watch_removed and (entry is None or entry.activity != "body")
            ), f"after_down_s={time.monotonic() - ended_at:.3f}, leases={len(link.body_leases)}, "
               f"watched={len(pilot._watch_npcs)}, cached_rows={len(link.body_latest)}, "
               f"body={int(entry is not None and entry.activity == 'body')}"):
                return results

            nearest = float("inf")

            async def routine() -> bool:
                nonlocal nearest
                snapshot = await link.snapshot(npc, fresh=True)
                if snapshot.player:
                    nearest = min(nearest, snapshot.player.distance)
                entry = pilot.people.get(npc)
                return bool(
                    entry and entry.activity == "routine" and entry.state != "ZS_ATTACK"
                    and (await link.routine(npc)).current == "ZS_STAND_ARMSCROSSED"
                )

            returned = await _until_async(routine, bool, 45)
            if not check("native_to_routine", returned,
                         f"closest_cm={nearest:.0f}, wait_s={time.monotonic() - ended_at:.3f}, "
                         f"roster={pilot.people.get(npc)}"):
                return results

            status: list[int] = []

            async def standing() -> bool:
                status[:] = await hero_status()
                return status == [0, 0]

            up = await _until_async(standing, bool, 35)
            snap = await link.snapshot(npc, fresh=True)
            if not check("hero_gets_up", bool(up and snap.player and snap.player.hp > 0),
                         f"standing={int(up)}, hp={snap.player.hp if snap.player else None}, "
                         f"unconscious={status[0] if status else None}, "
                         f"dead={status[1] if status else None}, "
                         f"wait_s={time.monotonic() - ended_at:.3f}"):
                return results

            # Keep this citizen out of the later bandit's fight through a normal
            # native activity, after the knockout and recovery are complete.
            await link.debug("npc_goto", npc=npc, waypoint=parked_at, face="PC_HERO")
            parked = await Bodies(link).act(
                npc, "start_activity", state="ZS_STAND_ARMSCROSSED", waypoint=parked_at,
                minutes=60, arrive=True, radius=100, timeout=15,
            )
            [distance] = await link.story_eval(npc, [
                ["call", "NPC_GETDISTTOWP", ["inst", "SELF"], ["str", parked_at]],
            ])
            check("park_citizen", bool(
                parked.status == "arrived" and distance <= 100
                and not pilot.handles(npc) and not link.body_leases
            ), f"waypoint={parked_at}, distance_cm={distance}, status={parked.status}, "
               f"leases={len(link.body_leases)}")
    finally:
        dialogue.llm = original_llm
        link.listeners.remove(heard)
        link.raw_body_listeners.remove(observe)
    return results
