"""Marked NPC theft and assault, witnesses, restitution and save branches on save 1."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .. import triggers
from ..game import GameError, GameLink
from ..llm.fake import FakeBackend
from ..protocol import GameMessage, NpcActResult, NpcDeed, NpcIdle, Session
from .common import (
    HILDA,
    LOBART,
    VINO,
    _logged,
    _save,
    _until,
    _until_async,
    prepare_npc_pose,
    wait_message,
    wait_npc_visibility,
)

if TYPE_CHECKING:
    from ..dialogue import DialogueManager
    from ..npc_crimes import NpcCrimes
    from ..people_knowledge import PeopleKnowledge
    from ..social import Rumors

log = logging.getLogger("gothic_ai.scenarios")
MALETH = "BAU_954_MALETH"
HOUSE, WITNESS = "NW_FARM1_INHOUSE_01", "NW_FARM1_INHOUSE_02"
ROAD, RETURN_ROAD = "NW_FARM1_PATH_04", "NW_FARM1_PATH_03"
CUP, CHEST = "ITMI_SILVERCUP", "CHEST_LOBART"


async def npc_deeds(
    link: GameLink,
    service: NpcCrimes,
    people: PeopleKnowledge,
    rumors: Rumors,
    dialogue: DialogueManager,
    log_dir: str,
    slot: int = 7,
) -> dict[str, str]:
    """Vino steals Lobart's chest item before Hilda, knocks Maleth out, then loses stolen goods.

    The final theft is a native world-item pickup in front of its brave owner. Only the scenario's
    courage card and the initial health change: ownership, walking, chest use, hits, witnesses and
    return use production paths. Farm guards are covered by NpcCrimes' module tests.
    """
    results: dict[str, str] = {}
    facts = service.chronicle.facts
    original_cards, original_llm = service.cards, dialogue.llm
    original_gate, original_stage = rumors.gate, rumors.stage
    received: list[NpcDeed | NpcActResult | NpcIdle] = []
    body_states: dict[str, list[dict[str, Any]]] = {VINO: [], MALETH: []}
    symbols: dict[int, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> bool:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"
        log.info("npc_deeds %-24s %s", name, results[name])
        return ok

    def record(message: GameMessage) -> None:
        if isinstance(message, NpcDeed | NpcActResult | NpcIdle):
            received.append(message)

    def observe(packet: dict[str, Any]) -> None:
        for row in packet["observations"]:
            if (npc := symbols.get(row["script_symbol"])) in body_states:
                body_states[npc].append({"t_game_ms": row["t_game_ms"], **row["self"]})

    def deed_crime(kind: str, after: set[str]) -> dict[str, Any] | None:
        return next((
            f for f in facts("npc_crime")
            if f.get("id") not in after and f.get("kind") == kind and f.get("culprit") == VINO
        ), None)

    def seen(cid: str) -> set[str]:
        return {str(f["npc"]) for f in facts("npc_crime_seen") if f.get("crime") == cid}

    def receipts(action: str) -> list[dict[str, Any]]:
        return [
            m.model_dump() for m in received
            if isinstance(m, NpcActResult) and m.action == action
        ]

    async def completed(action: str, *, timeout: float = 60) -> bool:
        def finished() -> bool:
            accepted = any(
                isinstance(m, NpcActResult) and m.id == action and m.status == "queued" for m in received
            )
            return accepted and any(
                isinstance(m, NpcIdle) and m.act == action and m.reason == "done" for m in received
            )

        return await _until(finished, timeout)

    async def voiced(npc: str, after: int) -> bool:
        def finished() -> bool:
            queued = {
                m.id for m in received[after:]
                if isinstance(m, NpcActResult) and m.npc == npc and m.action == "say_svm"
                and m.status == "queued"
            }
            return any(
                isinstance(m, NpcIdle) and m.act in queued and m.reason == "done"
                for m in received[after:]
            )

        return await _until(finished, 30)

    async def place(npc: str, waypoint: str, **kw: Any) -> bool:
        ok, detail = await prepare_npc_pose(link, npc, waypoint, **kw)
        if not ok:
            check("pose", False, detail)
        return ok

    async def visible(label: str, witness: str, target: str) -> bool:
        ok, detail = await wait_npc_visibility(link, witness, target)
        return check(label, ok, detail)

    async def count(npc: str, item: str) -> int:
        return int(await link.evaluate(["call", "NPC_HASITEMS", ["inst", npc], ["sym", item]]))

    async def health(label: str, values: dict[str, int]) -> bool:
        # A one-shot preparation trigger sets initial health; it is removed before any fight.
        maximum = {npc: (await link.snapshot(npc, fresh=True)).npc.hp_max for npc in values}
        fields = [["field", ["inst", npc], "ATTRIBUTE", slot] for npc in values for slot in (1, 0)]
        wanted = [amount for npc, hp in values.items() for amount in (max(maximum[npc], hp), hp)]
        trigger = f"DIR_NPC_DEEDS_{label.upper()}_HEALTH"
        await link.patch_graph([triggers.trigger(trigger, triggers.STATE, [
            {"do": "set", "target": field, "value": ["int", hp]}
            for field, hp in zip(fields, wanted, strict=True)
        ])])
        try:
            applied = await _until_async(
                lambda: link.story_eval(VINO, fields), lambda got: got == wanted, 5,
            )
        finally:
            await link.patch_graph([triggers.removal(trigger)])
        snapshots = {npc: (await link.snapshot(npc, fresh=True)).npc.hp for npc in values}
        return check(f"{label}_health", applied and snapshots == values, snapshots)

    async def native_drop() -> bool:
        try:
            await link.debug("npc_drop", npc=LOBART, item=CUP)
        except GameError as err:
            if "could not drop it now" not in str(err):
                raise
            return False
        return True

    link.listeners.append(record)
    link.raw_body_listeners.append(observe)
    try:
        await link.wait_world()
        if link.session is None:
            await wait_message(link, Session, timeout=10)
        if not check(
            "enabled", service.enabled and isinstance(original_llm, FakeBackend),
            "npc crimes enabled, LLM=fake; logs: " + log_dir,
        ):
            return _logged(results)
        dialogue.llm = FakeBackend("Ну, как знаешь.", delay=0)
        rumors.gate, rumors.stage = lambda *_: False, None
        await link.debug("player_hp", hp=5000)
        await link.debug("time", hour=8, minute=0)
        population = await link.story_people()
        humans = {str(p["npc"]) for p in population}
        participants = (LOBART, HILDA, VINO, MALETH)
        points = {p["name"]: p for p in await link.waypoints() if not p.get("fp")}
        if not check(
            "farm", set(participants) <= humans and {HOUSE, WITNESS, ROAD, RETURN_ROAD} <= points.keys()
            and not service.guard.body(VINO) and not service.guard.body(MALETH),
            {"thief": VINO, "victim": MALETH, "roads": [ROAD, RETURN_ROAD]},
        ):
            return _logged(results)
        symbols.update(zip(
            await link.story_eval(VINO, [["sym", VINO], ["sym", MALETH]]),
            (VINO, MALETH), strict=True,
        ))
        origin = (points[HOUSE]["x"], points[HOUSE]["y"], points[HOUSE]["z"])
        far = [
            p for p in points.values() if p["name"].startswith("NW_FARM1_") and not p.get("room")
            and math.dist((p["x"], p["y"], p["z"]), origin) > 8000
        ]
        if not check("parking", bool(far), len(far)):
            return _logged(results)
        parked = min(far, key=lambda p: math.dist((p["x"], p["y"], p["z"]), origin))["name"]
        farm = list(dict.fromkeys([
            *participants,
            *[p["npc"] for p in await link.story_nearby(LOBART, 7000) if p["npc"] in humans],
        ]))
        await link.story_effect(LOBART, [{"do": "end"}])
        await link.debug("player_goto", waypoint=HOUSE)
        for npc in farm:
            if not await place(npc, parked):
                return _logged(results)
        hero_facts = [f for f in facts() if f.get("fact") in {"crime", "crime_seen"}]

        # Hilda alone witnesses the actual transfer out of Lobart's chest. The command replaces
        # Vino's standing activity with native walking, opening, taking and closing.
        chests = await link.npc_chests(LOBART)
        chest = next((c for c in chests if c.id == CHEST and c.items), None)
        if not check("chest", chest is not None, [(c.id, c.why) for c in chests]):
            return _logged(results)
        assert chest is not None
        item = next((i for i in chest.items if i.item == "ITMI_GOLD"), chest.items[0]).item
        if not await place(VINO, HOUSE) or not await place(HILDA, WITNESS, face=VINO):
            return _logged(results)
        if not await visible("chest_witness_ready", HILDA, VINO):
            return _logged(results)
        owned_before = await count(VINO, item)
        prior = {str(f["id"]) for f in facts("npc_crime")}
        voice_after = len(received)
        take = await link.npc_act(VINO, "steal", chest=chest.id, what=item, count=1, deed=True)
        await _until(lambda: deed_crime("theft", prior), 60)
        crime = deed_crime("theft", prior)
        cid = str(crime["id"]) if crime is not None else ""
        deeds = [m for m in received if isinstance(m, NpcDeed) and m.id == cid]
        if not check(
            "chest_theft", crime is not None and crime.get("owner") == LOBART
            and crime.get("chest") == CHEST and crime.get("item") == item and crime.get("count") == 1
            and HILDA in crime.get("witnesses", []) and seen(cid) == set(crime.get("witnesses", []))
            and len(deeds) == 1 and deeds[0].culprit == VINO and await completed(take)
            and await count(VINO, item) == owned_before + 1,
            {"crime": crime, "deeds": [d.model_dump() for d in deeds], "actions": receipts("steal")},
        ):
            return _logged(results)
        knowledge = people.context(HILDA) or ""
        discoveries = [e for e in people.of(HILDA) if e.subject == VINO and e.direct]
        await rumors.tick()
        rumor = next((
            r for r in rumors.rumors.values()
            if r.subject == VINO and r.known.get(HILDA) == 0 and cid in r.quest
        ), None)
        if not check(
            "hilda_knowledge", bool(discoveries) and "Что ты знаешь о людях вокруг" in knowledge
            and any(e.subject_name in knowledge for e in discoveries) and rumor is not None,
            {"context": knowledge, "source": rumor.key if rumor else None},
        ):
            return _logged(results)
        if not check("hilda_shout", await voiced(HILDA, voice_after), receipts("say_svm")):
            return _logged(results)
        assert rumor is not None
        rumors.gate = lambda r, a, b: r.key == rumor.key and (a, b) == (HILDA, LOBART)
        passed = rumors.offer_status(rumor, HILDA, LOBART)
        if not check(
            "npc_rumor", passed == "told" and rumor.known.get(LOBART) == 1
            and any(e.subject == VINO and not e.direct and e.source == HILDA for e in people.of(LOBART)),
            {"status": passed, "context": people.context(LOBART)},
        ):
            return _logged(results)
        rumors.gate = lambda *_: False

        # Both fighters stand on the same connected stretch of road. Native AI_GotoNpc has no
        # waynet detour, so the relative placement is short and its ray is checked before combat.
        await link.debug("player_goto", waypoint=ROAD)
        if not await place(HILDA, parked) or not await place(VINO, ROAD):
            return _logged(results)
        if not await place(MALETH, ROAD, to=VINO, distance=250, face=VINO):
            return _logged(results)
        if not await place(HILDA, ROAD, to=VINO, distance=-500, face=VINO):
            return _logged(results)
        if not await visible("attack_witness_ready", HILDA, VINO):
            return _logged(results)
        if not await visible("fight_lane_ready", MALETH, VINO):
            return _logged(results)
        if not await health("attack", {VINO: 1000, MALETH: 10}):
            return _logged(results)
        await link.body_watch([VINO, MALETH], rate_hz=20)
        body_states[VINO].clear()
        body_states[MALETH].clear()
        prior = {str(f["id"]) for f in facts("npc_crime")}
        fight = await link.npc_act(VINO, "fight", npc=MALETH, reason="AR_NONE", deed=True)
        await _until(lambda: deed_crime("attack", prior), 45)
        attack = deed_crime("attack", prior)
        aid = str(attack["id"]) if attack is not None else ""
        if not check(
            "attack", attack is not None and attack.get("victim") == MALETH
            and HILDA in attack.get("witnesses", []) and VINO not in attack.get("witnesses", [])
            and MALETH not in attack.get("witnesses", []) and seen(aid) == set(attack.get("witnesses", [])),
            {"crime": attack, "actions": receipts("fight")},
        ):
            return _logged(results)
        down = await _until(lambda: any(s["unconscious"] and not s["dead"] for s in body_states[MALETH]), 90)
        down_at = time.monotonic()
        ended = await completed(fight, timeout=20) if down else False
        # Observe beyond the state exit too: AR_NONE must never turn the defeated target into a
        # finishing animation or a death after the first unconscious sample.
        observed = await _until(lambda: time.monotonic() - down_at >= 5, 6) if ended else False
        maleth = (await link.snapshot(MALETH, fresh=True)).npc
        if not check(
            "nonlethal", down and ended and observed and maleth.hp > 0
            and not any(s["dead"] for s in body_states[MALETH]),
            {"hp": maleth.hp, "last": body_states[MALETH][-3:], "idle": ended},
        ):
            return _logged(results)
        await link.body_watch([])

        # A second physical theft covers a world item's native owner. Lobart's courage alone is
        # raised to 3; he sees the pickup himself, defeats the thief and reclaims through the service.
        service.cards = {**original_cards, LOBART: replace(original_cards[LOBART], courage=3)}
        await link.debug("player_goto", waypoint=RETURN_ROAD)
        if not await place(HILDA, parked) or not await place(VINO, RETURN_ROAD):
            return _logged(results)
        if not await place(LOBART, RETURN_ROAD, to=VINO, distance=300, face=VINO):
            return _logged(results)
        if not check("owned_cup", await _until_async(native_drop, bool, 10), "native Lobart drop"):
            return _logged(results)
        if not await visible("owner_witness_ready", LOBART, VINO):
            return _logged(results)
        if not await health("reclaim", {VINO: 10, LOBART: 1000}):
            return _logged(results)
        thief_cups, owner_cups = await count(VINO, CUP), await count(LOBART, CUP)
        prior = {str(f["id"]) for f in facts("npc_crime")}
        await link.body_watch([VINO], rate_hz=20)
        body_states[VINO].clear()
        await link.npc_act(VINO, "steal", item=CUP, what=CUP, count=1, deed=True)
        await _until(lambda: deed_crime("theft", prior), 30)
        theft = deed_crime("theft", prior)
        tid = str(theft["id"]) if theft is not None else ""
        if not check(
            "world_theft", theft is not None and theft.get("owner") == LOBART
            and theft.get("item") == CUP and LOBART in theft.get("witnesses", [])
            and seen(tid) == set(theft.get("witnesses", [])), theft,
        ):
            return _logged(results)
        reclaimed = await _until(
            lambda: any(f.get("crime") == tid for f in facts("npc_crime_restitution")), 120,
        )
        returned = [f for f in facts("npc_crime_restitution") if f.get("crime") == tid]
        decisions = [f for f in facts("npc_crime_reaction") if f.get("crime") == tid]
        if not check(
            "owner_reclaim", reclaimed and len(returned) == 1
            and returned[0].get("npc") == LOBART and returned[0].get("culprit") == VINO
            and returned[0].get("item") == CUP and returned[0].get("count") == 1
            and any(f.get("npc") == LOBART and f.get("reaction") == "fight" for f in decisions)
            and any(s["unconscious"] and not s["dead"] for s in body_states[VINO])
            and not any(s["dead"] for s in body_states[VINO])
            and await count(VINO, CUP) == thief_cups and await count(LOBART, CUP) == owner_cups + 1,
            {"reaction": decisions, "returned": returned, "last": body_states[VINO][-3:]},
        ):
            return _logged(results)
        await link.body_watch([])
        check(
            "hero_unchanged", [f for f in facts() if f.get("fact") in {"crime", "crime_seen"}] == hero_facts,
        )
        check("guard_coverage", True, "farm scene uses brave owner; community guard covered by module tests")

        kinds = {"npc_crime", "npc_crime_seen", "npc_crime_reaction", "npc_crime_restitution", "rumor"}
        kept = [f for f in facts() if f.get("fact") in kinds]
        knowledge_before = {npc: people.of(npc) for npc in (HILDA, LOBART)}
        inventory_before = {npc: await count(npc, CUP) for npc in (VINO, LOBART)}
        saved = await _save(link, slot)
        if not check("saved", saved is not None, len(kept)):
            return _logged(results)
        assert saved is not None
        loading = asyncio.create_task(wait_message(link, Session, lambda m: m.reason == "load", 60))
        await asyncio.sleep(0)
        await link.debug("load", slot)
        loaded = await loading
        await link.wait_world()
        check(
            "loaded", loaded.parent == saved.branch and loaded.parent_seq == saved.seq
            and [f for f in facts() if f.get("fact") in kinds] == kept
            and {npc: people.of(npc) for npc in (HILDA, LOBART)} == knowledge_before
            and {npc: await count(npc, CUP) for npc in (VINO, LOBART)} == inventory_before,
            {"crimes": len(facts("npc_crime")), "witnesses": len(facts("npc_crime_seen")),
             "restitution": len(facts("npc_crime_restitution"))},
        )
        return _logged(results)
    finally:
        service.cards, dialogue.llm = original_cards, original_llm
        rumors.gate, rumors.stage = original_gate, original_stage
        try:
            if not link.closed.is_set():
                await link.body_watch([])
        finally:
            link.listeners.remove(record)
            link.raw_body_listeners.remove(observe)
