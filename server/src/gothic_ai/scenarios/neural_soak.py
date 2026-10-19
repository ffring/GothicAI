"""Timed full-world P30 waves; the policy and native world keep their normal clocks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import triggers
from ..bodies import Bodies
from ..bodypilot import ACQUIRE_WINDOW, BodyFight, BodyPilot
from ..dialogue import DialogueManager
from ..game import GameError, GameLink
from ..llm.fake import FakeBackend
from ..protocol import BodyLease, GameMessage, LineStarted, NpcSpec, RoutineEntry, Session
from .common import _m5b_player_weapon, _save_or_fail, _until, _until_async, wait_message
from .neural_load import _policy_resets
from .neural_soak_capture import CaptureTracker
from .neural_soak_metrics import SoakMetrics

log = logging.getLogger("gothic_ai.scenarios")
BANDITS = tuple(f"DIR_NPC_SOAK_BANDIT_{i}" for i in range(1, 5))
COMPANION = "DIR_NPC_SOAK_COMPANION"
TALKER = "DIR_NPC_SOAK_TALKER"
REFUGE = "NW_XARDAS_TOWER_VALLEY_01"
PLACES = ("NW_XARDAS_GOBBO_02", "NW_XARDAS_TOWER_VALLEY_09")


@dataclass
class Episode:
    fight: BodyFight
    npc: str
    lease: BodyLease
    target_kind: str | None
    wave: int
    started: float
    last_damage: float
    hp: int | None = None
    stall_broken: int = 0
    break_wait_s: float | None = None
    stalls: list[float] = field(default_factory=list)
    down: bool = False
    ended: float | None = None
    checked: bool = False
    target_attackers_peak: int = 1


async def neural_soak_scenario(
    link: GameLink, dialogue: DialogueManager, pilot: BodyPilot | None, *, log_dir: Path,
) -> dict[str, str]:
    if pilot is None or pilot.max_fights != 4:
        raise GameError("neural-soak requires GOTHIC_BODY_URL and --neural-body-max=4")
    if dialogue.llm.name != "fake":
        raise GameError("neural-soak requires LLM=fake")
    duration = float(os.environ.get("GOTHIC_SOAK_SECONDS", "1800"))
    if not 90 <= duration <= 2100:
        raise GameError("GOTHIC_SOAK_SECONDS must be between 90 and 2100")
    await link.wait_world()
    if link.hello is None or link.hello.protocol < 45:
        raise GameError("neural-soak requires protocol >=45")
    metrics = SoakMetrics(link, pilot, log_dir)
    sequence = 0
    wave = 0
    episodes: dict[int, Episode] = {}
    capture = CaptureTracker(link, pilot, metrics.event, COMPANION)
    release_issues: list[dict[str, Any]] = []
    load_checks: list[dict[str, Any]] = []
    conversations: list[bool] = []
    wave_records: list[dict[str, Any]] = []
    active_wave: set[str] = set()
    latest_identity: dict[str, tuple[Any, Any]] = {}
    reacquire_checks: list[bool] = []
    lines: list[LineStarted] = []
    scheduled_loads: set[int] = set()
    started = time.monotonic()
    hero_dead = False
    last_hero_check = float("-inf")
    orphan_since: dict[str, float] = {}
    orphan_reported: set[str] = set()
    sampler: asyncio.Task[None] | None = None

    async def sample_loop() -> None:
        while True:
            metrics.sample()
            await asyncio.sleep(.1)

    def pending_probe() -> bool:
        return any(not item["reacquired"] for item in load_checks)

    async def calls(expressions: list[list[Any]]) -> None:
        nonlocal sequence
        sequence += 1
        name = f"DIR_SOAK_OP_{sequence}"
        await link.patch_graph([triggers.trigger(name, triggers.STATE, [
            {"do": "call", "call": expression} for expression in expressions
        ])])
        try:
            if not await _until_async(link.triggers, lambda rows: any(
                row["id"] == name and row["count"] >= 1 for row in rows
            ), 5):
                raise GameError(f"neural-soak preparation: trigger {name} did not fire")
        finally:
            await link.patch_graph([triggers.removal(name)])

    async def remove_bandits() -> None:
        # Wld_RemoveNpc removes corpses as well as survivors. Reinsert the same
        # four symbols next wave; own_npc cannot allocate an existing symbol.
        await calls([["call", "WLD_REMOVENPC", ["sym", npc]] for npc in BANDITS])

    async def heal() -> None:
        await link.debug("player_hp", hp=1200)
        await calls([
            ["call", "NPC_CHANGEATTRIBUTE", ["inst", COMPANION], ["int", attribute],
             ["-", ["int", 900], ["field", ["inst", COMPANION], "ATTRIBUTE", attribute]]]
            for attribute in (1, 0)
        ])

    def observe(packet: dict[str, Any]) -> None:
        nonlocal hero_dead
        now = time.monotonic()
        for row in packet["observations"]:
            for episode in list(episodes.values()):
                lease = episode.lease
                if (
                    row["entity_id"], row["session_epoch"], row["world_epoch"],
                    row["control_generation"],
                ) != (lease.entity_id, lease.session_epoch, lease.world_epoch, lease.control_generation):
                    continue
                target = row["target"]
                if target["entity_id"] != lease.target_entity_id:
                    continue
                hp = target["hp"]
                if hp is not None:
                    if episode.hp is not None and hp < episode.hp:
                        if now - episode.last_damage >= 15:
                            episode.stalls.append(now - episode.last_damage)
                        episode.last_damage = now
                    episode.hp = hp
                if target["down"] or hp is not None and hp <= 0:
                    episode.down = True
                    if episode.target_kind == "hero" and hp is not None and hp <= 0:
                        hero_dead = True
                own = row["self"]
                if episode.ended is None and (episode.down or own["down"] or own["hp"] <= 0):
                    episode.ended = now
                    if now - episode.last_damage >= 15:
                        episode.stalls.append(now - episode.last_damage)

    def heard(message: GameMessage) -> None:
        if isinstance(message, LineStarted) and message.npc.upper() == TALKER:
            lines.append(message)

    async def poll(*, break_stalls: bool = False) -> None:
        nonlocal hero_dead, last_hero_check
        metrics.sync_summaries()
        now = time.monotonic()
        orphan_now = {npc for npc, entry in pilot.people.items() if (
            entry.activity == "body" and not any(lease.npc == npc for lease in link.body_leases.values())
        )}
        for npc in set(orphan_since) - orphan_now:
            del orphan_since[npc]
            orphan_reported.discard(npc)
        for npc in orphan_now:
            orphan_since.setdefault(npc, now)
            if now - orphan_since[npc] >= 2 and npc not in orphan_reported:
                orphan_reported.add(npc)
                release_issues.append(dict(npc=npc, orphan_seconds=now - orphan_since[npc]))
        if break_stalls and now - last_hero_check >= 1:
            last_hero_check = now
            dead, = await link.story_eval(TALKER, [["call", "NPC_ISDEAD", ["inst", "HERO"]]])
            hero_dead = hero_dead or bool(dead)
        if active_wave:
            attackers = active_wave | {npc for npc, entry in pilot.people.items() if (
                entry.state.upper() == "ZS_ATTACK" and entry.activity in {"fight", "body"}
                and entry.guild <= 16 and not pilot._companion(npc)
            )}
            await capture.poll(wave, attackers)
        for npc in active_wave:
            fight = pilot.fights.get(npc)
            if fight is None or fight.lease is None:
                continue
            lease = fight.lease
            if link.body_leases.get(lease.entity_id) != lease:
                continue
            if id(fight) not in episodes and resets.get(fight.client):
                episodes[id(fight)] = Episode(fight, npc, lease, fight._target_kind, wave, now, now)
                previous = latest_identity.get(npc)
                if previous is not None and previous[0] != lease:
                    reacquire_checks.append(bool(
                        previous[1] is not fight.client and resets.get(fight.client)
                        and resets[fight.client][0]["world_epoch"] == lease.world_epoch
                        and resets[fight.client][0]["session_epoch"] == lease.session_epoch
                    ))
                latest_identity[npc] = (lease, fight.client)
                for loaded in load_checks:
                    old_worlds = loaded["old_worlds"]
                    if (not loaded["reacquired"]
                            and lease.session_epoch == loaded["new_world"][0]
                            and lease.world_epoch >= loaded["new_world"][1]
                            and [lease.session_epoch, lease.world_epoch] not in old_worlds):
                        loaded["reacquired"] = bool(resets.get(fight.client))
                        metrics.event("load_reacquire", cause=loaded["cause"], npc=npc,
                                      ok=loaded["reacquired"], lease=lease)
        for episode in list(episodes.values()):
            fight, lease = episode.fight, episode.lease
            npc = episode.npc
            current = pilot.fights.get(npc) is fight
            if current:
                episode.target_attackers_peak = max(episode.target_attackers_peak, sum(
                    other.target_entity_id == lease.target_entity_id
                    and (other.session_epoch, other.world_epoch) == (lease.session_epoch, lease.world_epoch)
                    for other in link.body_leases.values()
                ))
            if episode.ended is None and not current:
                episode.ended = now
                if now - episode.last_damage >= 15:
                    episode.stalls.append(now - episode.last_damage)
            if episode.ended is not None and not episode.checked and now >= episode.ended + 2:
                episode.checked = True
                entry = pilot.people.get(npc)
                orphan = bool(entry and entry.activity == "body" and not any(
                    item.npc == npc for item in link.body_leases.values()
                ))
                old_lease = link.body_leases.get(lease.entity_id) == lease
                summary = next((s for s in metrics.summaries if s.lease == lease), None)
                ok = bool(not old_lease and not orphan and summary and summary.release_confirmed)
                detail = dict(npc=npc, wave=episode.wave, old_lease=old_lease,
                              orphan=orphan, release_s=summary.release_seconds if summary else None,
                              release_late_s=summary.release_late_seconds if summary else None,
                              checked_after_s=now - episode.ended)
                metrics.event("release_check", ok=ok, **detail)
                metrics.event(
                    "fight_diagnostics", npc=npc, wave=episode.wave, lease=lease,
                    target_attackers_peak=episode.target_attackers_peak, target_down=episode.down,
                    stall_broken=episode.stall_broken, stalls_s=episode.stalls,
                    stall_count=len(episode.stalls), break_wait_s=episode.break_wait_s,
                )
                if not ok:
                    release_issues.append(detail)
            if (break_stalls and current and episode.ended is None and not episode.stall_broken
                    and now - episode.last_damage >= 15):
                stalled = now - episode.last_damage
                episode.stall_broken = 1
                episode.break_wait_s = stalled
                snapshot = await link.snapshot(npc, fresh=True)
                if pilot.fights.get(npc) is not fight or episode.ended is not None or snapshot.npc.hp <= 0:
                    continue
                if snapshot.player and snapshot.player.distance > 450:
                    await link.debug("teleport", near=npc, distance=250)
                if pilot.fights.get(npc) is not fight or episode.ended is not None:
                    continue
                hit = "done"
                try:
                    await link.debug("player_hit", npc=npc)
                except GameError as error:
                    if str(error) != "the hit caused no damage":
                        raise
                    hit = str(error)
                metrics.event("stall_broken", npc=npc, wave=episode.wave,
                              seconds=stalled, hit=hit)
        metrics.sample()

    async def pause(seconds: float, *, combat: bool = False) -> None:
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            await poll(break_stalls=combat)
            await asyncio.sleep(min(.1, max(0, until - time.monotonic())))

    async def load_wave(saved: Any, *, cause: str) -> None:
        nonlocal hero_dead
        old = {npc: (fight.lease, fight.client) for npc, fight in pilot.fights.items()
               if fight.lease is not None}
        old_worlds = sorted({(lease.session_epoch, lease.world_epoch)
                             for lease, _ in latest_identity.values()})
        before = len(metrics.summaries)
        waiter = asyncio.create_task(wait_message(link, Session, lambda msg: msg.reason == "load"))
        await asyncio.sleep(0)
        try:
            await link.debug("load", 7)
            session = await waiter
        finally:
            waiter.cancel()
        await link.wait_world()
        await pause(2.1)
        status = await link.body_control_status()
        new_world = [status.session_epoch, status.world_epoch]
        summaries = metrics.summaries[before:]
        old_summaries = [s for s in summaries if s.lease in {item[0] for item in old.values()}]
        packets = len(link.body_latest)
        ok = bool(
            session.parent == saved.branch and session.parent_seq == saved.seq
            and new_world not in [list(scope) for scope in old_worlds]
            and len(old_summaries) == len(old)
            and all(s.reason == "loading" and s.release_confirmed and s.fallbacks == 0
                    and s.release_seconds is not None and s.release_seconds <= 2 for s in old_summaries)
            and not pilot.active_count and not link.body_leases and not packets
            and not pilot._watch_npcs and not pilot._pending_unwatch
            and not any(entry.activity == "body" for entry in pilot.people.values())
        )
        detail = dict(cause=cause, ok=ok, mid_fight=bool(old), old_fights=len(old),
                      summaries=len(old_summaries), active_count=pilot.active_count,
                      leases=len(link.body_leases), cached_rows=packets,
                      old_worlds=[list(scope) for scope in old_worlds], reacquired=False,
                      new_world=new_world,
                      reasons=[s.reason for s in old_summaries])
        load_checks.append(detail)
        metrics.event("load_check", **detail)
        # Loading deliberately clears GameLink.party. Re-establish this fixture's
        # declared swordsman on both sides before testing post-load combat.
        await link.story_effect(COMPANION, [{"do": "party", "value": 1}])
        hero_dead = False

    async def conversation() -> None:
        await _m5b_player_weapon(link, "none")
        await link.debug("player_goto", waypoint=REFUGE)
        await link.debug("npc_goto", npc=TALKER, to="PC_HERO", distance=150, face="PC_HERO")
        await link.attach(TALKER)
        mark = len(lines)
        await link.debug("type", text="Путник, как дорога? Всё спокойно?")
        answered = await _until(lambda: len(lines) > mark, 15)
        conversations.append(answered)
        metrics.event("conversation", wave=wave, answered=answered)
        await _until(lambda: not dialogue.speaking(TALKER), 10)
        await link.detach(TALKER)

    # Prepare a fixed four-symbol pool outside the measured interval. The world
    # is neither frozen nor cleared of its people, wildlife or native fights.
    await link.debug("player_goto", waypoint=REFUGE)
    for npc in BANDITS:
        await link.own_npc(npc, like="BDT_1002_BANDIT_L", waypoint=PLACES[1])
    await remove_bandits()
    for npc, name in ((COMPANION, "Спутник-мечник"), (TALKER, "Путник")):
        await link.story_npc(NpcSpec(
            npc=npc, like="BDT_1002_BANDIT_L" if npc == COMPANION else "NONE_100_XARDAS",
            at=REFUGE, name=name, guild="GIL_VLK" if npc == COMPANION else "GIL_NONE",
            routine=[RoutineEntry(start="00:00", end="23:59", state="ZS_STAND_ARMSCROSSED",
                                  waypoint=REFUGE)],
        ))
        await link.debug("skip_important", near=npc)
    await link.story_effect(COMPANION, [{"do": "party", "value": 1}])
    await link.debug("player_equip", item="ITMW_1H_BAU_MACE")
    original_llm = dialogue.llm
    dialogue.llm = FakeBackend("Пока спокойно. Держи меч наготове.", delay=.15)
    link.raw_body_listeners.append(observe)
    link.listeners.append(heard)
    try:
        metrics.started = time.monotonic()
        capture.started = metrics.started
        with metrics.observe(), _policy_resets() as resets:
            started = time.monotonic()
            sampler = asyncio.create_task(sample_loop())
            # Put the 10-minute checkpoint inside a wave, rather than its gap.
            next_wave = started + (30 if duration >= 1200 else 0)
            while time.monotonic() - started < duration or pending_probe():
                if time.monotonic() - started >= duration + 30:
                    break
                if pending_probe():
                    next_wave = min(next_wave, max(time.monotonic(), started + duration - 15))
                deadline = started + duration + (30 if pending_probe() else 0)
                while time.monotonic() < min(next_wave, deadline):
                    # Once a combat load is covered, the second scheduled load
                    # may run in the peaceful gap even if the next wave is later.
                    elapsed = time.monotonic() - started
                    for minute in (10, 20):
                        due = minute * (60 if duration >= 1200 else duration / 30)
                        if (minute not in scheduled_loads and elapsed >= due and not pending_probe() and any(
                            item["mid_fight"] for item in load_checks
                        )):
                            saved = await _save_or_fail(link, 7)
                            await load_wave(saved, cause=f"scheduled_{minute}m")
                            scheduled_loads.add(minute)
                            if duration < 1200:
                                next_wave = min(next_wave, time.monotonic() + 1)
                    await pause(min(.1, max(0, min(next_wave, deadline) - time.monotonic())))
                if time.monotonic() - started >= duration and not pending_probe():
                    break
                probe_only = time.monotonic() - started >= duration
                wave += 1
                place = PLACES[(wave - 1) % 2]
                count = 3 + (wave % 2 == 0)
                active_wave = set(BANDITS[:count])
                await link.debug("player_goto", waypoint=REFUGE)
                await link.debug("npc_goto", npc=COMPANION, waypoint=REFUGE)
                await heal()
                await link.debug("player_equip", item="ITMW_1H_BAU_MACE")
                try:
                    await _m5b_player_weapon(link, "melee", timeout=15)
                except GameError:
                    snapshot = await link.snapshot(TALKER, fresh=True)
                    log.error("neural-soak weapon preparation: hero=%s", snapshot.player)
                    raise
                saved = await _save_or_fail(link, 7)
                await calls([["call", "WLD_INSERTNPC", ["sym", npc], ["str", PLACES[1]]]
                             for npc in BANDITS[:count]])
                await calls([
                    ["call", "NPC_CHANGEATTRIBUTE", ["inst", npc], ["int", attribute],
                     ["-", ["int", 1800], ["field", ["inst", npc], "ATTRIBUTE", attribute]]]
                    for npc in BANDITS[:count] for attribute in (1, 0)
                ])
                symbols = []
                for index, npc in enumerate(BANDITS[:count]):
                    await link.debug("skip_important", near=npc)
                    await link.debug("npc_goto", npc=npc, waypoint=PLACES[1], face="PC_HERO")
                    ready = await Bodies(link).act(
                        npc, "start_activity", state="ZS_STAND_ARMSCROSSED", waypoint=PLACES[1],
                        minutes=60, arrive=True, radius=150, timeout=15,
                    )
                    if ready.status != "arrived":
                        raise GameError(f"neural-soak preparation: {npc} {ready.status}")
                    weapon = ["call", "NPC_GETEQUIPPEDMELEEWEAPON", ["inst", "SELF"]]
                    symbol, sword, guild = await link.story_eval(npc, [
                        ["sym", npc], ["&", ["field", weapon, "FLAGS"], ["var", "ITEM_SWD"]],
                        ["==", ["field", ["inst", "SELF"], "GUILD"], ["var", "GIL_BDT"]],
                    ])
                    if not sword or not guild:
                        raise GameError(f"neural-soak preparation: {npc} sword={sword} bandit={guild}")
                    symbols.append(symbol)
                    await Bodies(link).act(npc, "draw_weapon", kind="melee")
                    await link.debug("npc_goto", npc=npc, to=npc, distance=700 + index * 350)
                if len(set(symbols)) != count:
                    raise GameError("neural-soak preparation: duplicate symbols")
                # Three attackers initially share the hero. On four-actor waves
                # the fourth starts against the swordsman: compare actual target
                # overlap from the recorded leases rather than infer it from spawn.
                await link.debug("npc_goto", npc=BANDITS[0], waypoint=place, face="PC_HERO")
                for index, npc in enumerate(BANDITS[1:count], 1):
                    await link.debug("npc_goto", npc=npc, to=BANDITS[0],
                                     distance=(-250, 500, 750)[index - 1], face="PC_HERO")
                await link.debug("npc_goto", npc=COMPANION, to=BANDITS[0], distance=1000, face=BANDITS[0])
                await Bodies(link).act(COMPANION, "draw_weapon", kind="melee")
                await link.debug("teleport", near=BANDITS[0], distance=250)
                wave_start = time.monotonic()
                metrics.event("wave_start", wave=wave, place=place, count=count)
                if wave == 1:
                    await link.debug("destruction_traces", text="signal soak_wave")
                await calls([["fn", "B_ATTACK", triggers.npc(npc),
                              triggers.HERO if index < 3 else triggers.npc(COMPANION),
                              ["var", "AR_NONE"], ["int", 0]]
                             for index, npc in enumerate(BANDITS[:count])])
                loaded = False
                combat_deadline = started + duration + (30 if probe_only else 0)
                while time.monotonic() - wave_start < 75 and time.monotonic() < combat_deadline:
                    await poll(break_stalls=True)
                    if probe_only and not pending_probe():
                        break
                    if hero_dead:
                        await load_wave(saved, cause="hero_death")
                        loaded = True
                        break
                    elapsed = time.monotonic() - started
                    marker = next((minute for minute in (10, 20) if (
                        minute not in scheduled_loads
                        and elapsed >= minute * (60 if duration >= 1200 else duration / 30)
                    )), None)
                    if (marker is not None and link.body_leases and not pending_probe()
                            and time.monotonic() - wave_start >= ACQUIRE_WINDOW):
                        # Keep a combat checkpoint, then restore the peaceful
                        # pre-wave save, as neural-load does. Native combat must
                        # not immediately hide old-world ownership behind new leases.
                        checkpoint = await _save_or_fail(link, 8)
                        metrics.event("checkpoint", minute=marker, seq=checkpoint.seq)
                        await load_wave(saved, cause=f"scheduled_{marker}m")
                        scheduled_loads.add(marker)
                        loaded = True
                        break
                    if time.monotonic() - wave_start > 5 and not any(
                        pilot.handles(npc) for npc in active_wave
                    ):
                        break
                    await asyncio.sleep(.1)
                end = "load" if loaded else "finished"
                if not loaded:
                    if any(pilot.handles(npc) for npc in active_wave):
                        end = "escape_75s" if time.monotonic() - wave_start >= 75 else "escape_duration"
                    await link.debug("player_goto", waypoint=REFUGE)
                    await link.debug("npc_goto", npc=COMPANION, waypoint=REFUGE)
                    await _until(
                        lambda npcs=tuple(active_wave): not any(pilot.handles(npc) for npc in npcs), 8,
                    )
                    await pause(2.1)
                    capture.finish_wave(wave)
                    await remove_bandits()
                record = dict(wave=wave, place=place, count=count, end=end,
                              seconds=round(time.monotonic() - wave_start, 3))
                wave_records.append(record)
                metrics.event("wave_end", **record)
                capture.finish_wave(wave)
                active_wave.clear()
                await heal()
                await conversation()
                await poll()
                first_wave_delay = 30 if duration >= 1200 else 0
                next_wave = max(started + first_wave_delay + wave * 90, time.monotonic())
                if loaded and duration < 1200:
                    # A short smoke run needs a new fight after its final load
                    # to check the fresh client/reset before its deadline.
                    next_wave = time.monotonic() + 1
            await pause(2.1)
            metrics.sample(force=True)
            extra = dict(
                duration_s=duration, loads_scaled=duration < 1200, waves=wave_records, loads=load_checks,
                conversations=conversations, reacquire_checks=reacquire_checks,
                scheduled_loads=sorted(scheduled_loads),
                capture_details=capture.records,
                episodes=[dict(npc=e.npc, wave=e.wave, lease=e.lease.model_dump(),
                               stall_broken=e.stall_broken, stalls_s=e.stalls,
                               break_wait_s=e.break_wait_s, stall_count=len(e.stalls),
                               target_attackers_peak=e.target_attackers_peak,
                               target_down=e.down, seconds=(e.ended or time.monotonic()) - e.started)
                          for e in episodes.values()],
            )
            extra_checks: dict[str, str] = {}
            loads_ok = all(item["ok"] and item["reacquired"] for item in load_checks) and (
                all(reacquire_checks)
            ) and (
                duration < 180 or scheduled_loads == {10, 20}
                and any(item["mid_fight"] and item["cause"].startswith("scheduled_") for item in load_checks)
            )
            deaths = sum(item["cause"] == "hero_death" for item in load_checks)
            extra_checks["soak_loads"] = (
                f"{'done' if loads_ok else 'failed'} (scheduled={sorted(scheduled_loads)}, death={deaths}, "
                f"checks={len(load_checks)}, fresh={sum(reacquire_checks)}/{len(reacquire_checks)})"
            )
            talk_ok = bool(conversations and all(conversations))
            extra_checks["soak_talk"] = (
                f"{'done' if talk_ok else 'failed'} (answered={sum(conversations)}/{len(conversations)})"
            )
            results = metrics.write_result(
                eligible=len(capture.eligible), captured=len(capture.captured), release_issues=release_issues,
                extra=extra, extra_checks=extra_checks,
            )
            for name, value in results.items():
                log.info("%-28s %s", name, value)
            return results
    finally:
        if sampler is not None:
            sampler.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sampler
        dialogue.llm = original_llm
        link.raw_body_listeners.remove(observe)
        link.listeners.remove(heard)
