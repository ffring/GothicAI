"""Live-body regressions for Cavalorn's fights reported on 2026-10-18.

The default decision and speech models are scripted, as in m5. DECIDER_URL can replace the
decision fixture. All perceptions, voice starts, weapon animations, strikes and damage come
from the game; an accepted fight command alone never passes a combat check.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bodies import Bodies
from .brain import GameApplier, NpcPlanner
from .brain.planner import PlannerConfig
from .dialogue import DialogueManager
from .executor import Intent
from .game import GameError, GameLink
from .incidents import IncidentDesk
from .protocol import (
    ActionCmd,
    Beasts,
    GameMessage,
    LineStarted,
    NpcActCmd,
    NpcEvent,
    PerceptionHeld,
    ThreatControlCmd,
)
from .scenarios import FixedDecider, _until
from .talk import TalkDesk

log = logging.getLogger(__name__)

CAVALORN = "BAU_4300_ADDON_CAVALORN"
BANDITS = ("BDT_1014_BANDIT_L", "BDT_1015_BANDIT_L")
ARENA = "NW_FARM1_FIELD_06"
IDLE = "ZS_STAND_ARMSCROSSED"


@dataclass
class CombatTrace:
    """Keep actual game timestamps, independently of command receipts and wall-clock polling."""

    symbols: dict[int, str]
    armed: dict[str, int] = field(default_factory=dict)
    strikes: dict[str, list[int]] = field(default_factory=dict)
    strike_targets: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    damage: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    damage_wall: dict[str, list[float]] = field(default_factory=dict)
    positions: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    target: dict[str, int] = field(default_factory=dict)
    entities: dict[str, int] = field(default_factory=dict)
    hp: dict[str, int] = field(default_factory=dict)
    attacking: dict[str, bool] = field(default_factory=dict)
    events: list[tuple[float, GameMessage]] = field(default_factory=list)
    commands: list[tuple[float, str, str, dict[str, Any]]] = field(default_factory=list)
    wolves: list[str] = field(default_factory=list)

    def body(self, packet: dict[str, Any]) -> None:
        for row in packet["observations"]:
            who = self.symbols.get(row["script_symbol"])
            if who is None:
                continue
            own, now = row["self"], row["t_game_ms"]
            self.positions[who] = tuple(own["position_world_cm"])
            self.target[who] = row["target"]["entity_id"]
            self.entities[who] = row["entity_id"]
            if own["weapon_state"] >= 2:
                self.armed.setdefault(who, now)
            attacking = bool(own["attack_anim"])
            if attacking and not self.attacking.get(who, False):
                self.strikes.setdefault(who, []).append(now)
                self.strike_targets.setdefault(who, []).append((now, self.target[who]))
                log.info(
                    "fight_reactions strike npc=%s target_entity=%d game_ms=%d", who, self.target[who], now
                )
            self.attacking[who] = attacking
            before = self.hp.get(who)
            if before is not None and own["hp"] < before:
                self.damage.setdefault(who, []).append((now, before - own["hp"]))
                self.damage_wall.setdefault(who, []).append(time.monotonic())
                log.info("fight_reactions damage npc=%s game_ms=%d hp_lost=%d", who, now, before - own["hp"])
            self.hp[who] = own["hp"]

    def message(self, msg: GameMessage) -> None:
        if isinstance(msg, Beasts):
            self.wolves = [w.id for w in msg.wolves]
        if isinstance(msg, LineStarted | NpcEvent | PerceptionHeld):
            self.events.append((time.monotonic(), msg))

    def distance(self, one: str, other: str) -> float | None:
        a, b = self.positions.get(one), self.positions.get(other)
        return math.dist(a, b) if a is not None and b is not None else None

    def strike_after(self, npc: str, target: str, after: int) -> int | None:
        """A real post-hit strike at the attacker, with the target captured when the strike began."""
        entity = self.entities.get(target)
        return next(
            (t for t, victim in self.strike_targets.get(npc, []) if t >= after and victim == entity), None
        )


def combat_shouts(log_file: Path, npc: str) -> list[int]:
    """Only successful native audio playback logs count; a queued or suppressed SVM is insufficient."""
    if not log_file.exists():
        return []
    pattern = re.compile(rf"\[ai\] {re.escape(npc)} combat shout \S+ audio_ms=(\d+) game_ms=(\d+)")
    return [int(t) for ms, t in pattern.findall(log_file.read_text(errors="replace")) if int(ms) > 0]


async def fight_reactions(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    incidents: IncidentDesk,
    planner: NpcPlanner | None,
    case: str,
    log_dir: str,
) -> dict[str, str]:
    """wolf: intercept before its bite; sight: bloodfly at 30 m; bandits: two armed responses;
    flee: a coward runs from Cavalorn, while the unarmed hero stands still.
    """
    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object) -> None:
        results[name] = f"{'done' if ok else 'failed'} ({detail})"
        log.info("fight_reactions %-26s %s", name, results[name])

    runner: asyncio.Task[None] | None = None
    if planner is None:
        planner = NpcPlanner(
            FixedDecider(
                {"react": ("flee", "continue") if case == "flee" else ("fight", "continue")},
                {"escalate": 3.6},
                npcs=BANDITS,
            ),
            GameApplier(link),
            config=PlannerConfig(ask_attitude=False),
        )
        link.listeners.append(planner.on_message)
        incidents.follow(planner)
        runner = asyncio.create_task(planner.run())
    old_traits = planner.builder.traits
    traits = dict(old_traits)
    for bandit in BANDITS:
        traits[bandit] = (
            "A terrified, cowardly bandit, outmatched by Cavalorn. Runs from armed attackers."
            if case == "flee"
            else "A bold, hardened bandit. Defends himself against armed attackers."
        )
    planner.builder.traits = traits

    trace = CombatTrace({})
    link.listeners.append(trace.message)
    link.raw_body_listeners.append(trace.body)
    send = link.send

    async def recorded(cmd: Any) -> None:
        if isinstance(cmd, ActionCmd | NpcActCmd):
            trace.commands.append((time.monotonic(), cmd.npc.upper(), cmd.action, dict(cmd.args)))
        await send(cmd)

    link.send = recorded  # type: ignore[method-assign]
    await link.send(ThreatControlCmd(hold=True))
    try:
        await link.debug("player_goto", waypoint=ARENA)
        await link.debug("player_equip", item="ITAR_PAL_H")
        await link.debug("player_hp", hp=1000)
        await link.debug("skip_important", near=CAVALORN)
        await link.debug("npc_goto", npc=CAVALORN, waypoint=ARENA)
        ready = await Bodies(link).act(
            CAVALORN,
            "start_activity",
            state=IDLE,
            waypoint=ARENA,
            minutes=60,
            arrive=True,
            radius=100,
            timeout=15,
        )
        if ready.status != "arrived":
            raise GameError(f"fight_reactions setup: Cavalorn idle {ready.status}: {ready.detail}")
        anchor = (await link.snapshot(CAVALORN, fresh=True)).npc.pos

        # The field is an open, gently sloping pasture. Move actors with the existing debug API:
        # npc_goto(to=PC_HERO,distance=0) supports arbitrary ground positions without a new command.
        def point(x: float, z: float = 0) -> list[float]:
            return [anchor[0] + x, anchor[1], anchor[2] + z]

        async def put(npc: str, x: float, z: float = 0, face: str | None = None) -> None:
            await link.debug("player_goto", pos=point(x, z))
            await link.debug("npc_goto", npc=npc, to="PC_HERO", distance=0, face=face)

        # No stock wolf or sheep can become an unintended target or mask whose damage was observed.
        animals = {
            n.id
            for n in (await link.snapshot(CAVALORN, fresh=True)).nearby_npcs
            if n.guild >= 16 and not n.dead
        }
        animals.update(
            n.id
            for v in await link.npc_view([CAVALORN])
            for n in v.sees
            if n.guild >= 16 and n.body != "dead"
        )
        for other in animals:
            if other != CAVALORN:
                try:
                    await link.debug("kill", instance=other, near=CAVALORN)
                except GameError as err:
                    if str(err) != "it can't die now":
                        raise

        # The hero stays passive throughout all cases. No player attack or threat is injected.
        await link.debug("player_goto", pos=point(-900))
        if not await _until(lambda: CAVALORN in talk.people, 10):
            raise GameError("fight_reactions setup: Cavalorn absent from roster")
        dialogue.open(CAVALORN, "Кавалорн").intent = Intent("follow_hero", "PLAYER", "friendly")

        if case in {"wolf", "sight"}:
            monster = f"DIR_NPC_FIGHT_{case.upper()}"
            kind = "WOLF" if case == "wolf" else "BLOODFLY"
            # Create separated from the hero, then relocate before the companion is attached.
            await link.debug("player_goto", pos=point(-8000))
            await link.own_npc(monster, like=kind, waypoint=ARENA)
            await put(monster, 1800 if case == "wolf" else 3000)
            await put(CAVALORN, 0, face=monster)
            await link.debug("player_goto", pos=point(-600 if case == "wolf" else -900))
            if not await talk.hold(CAVALORN, "follow"):
                raise GameError("fight_reactions setup: cannot hold companion")
            await link.act(CAVALORN, "stop")
            # Marked party members get the companion sight range in the bridge.
            await link.story_effect(CAVALORN, [{"do": "party", "value": 1}])
            symbols = await link.story_eval(CAVALORN, [["sym", n] for n in (CAVALORN, monster, "PC_HERO")])
            trace.symbols = dict(zip(symbols, (CAVALORN, monster, "PLAYER"), strict=True))
            await link.body_watch([CAVALORN, monster, "PLAYER"])
            if not await _until(lambda: all(n in trace.positions for n in (CAVALORN, monster, "PLAYER")), 5):
                raise GameError("fight_reactions setup: missing body observations")

            if case == "sight":
                views = await link.npc_view([CAVALORN])
                seen = next((o for v in views for o in v.sees if o.id.upper() == monster), None)
                distance = trace.distance(CAVALORN, monster)
                check(
                    "bloodfly_30m",
                    seen is not None and distance is not None and 2850 <= distance <= 3250,
                    f"visible={seen is not None}, actual_cm={distance}, "
                    f"seen_cm={seen.distance if seen else None}",
                )
            else:
                start_hp = trace.hp.get("PLAYER")
                if not await _until(lambda: any(w.startswith(monster + "#") for w in trace.wolves), 5):
                    raise GameError("fight_reactions setup: unique wolf absent from native beast watch")
                wolf_id = next(w for w in trace.wolves if w.startswith(monster + "#"))
                await link.beast(wolf_id, "attack")
                await _until(lambda: bool(trace.strikes.get(CAVALORN)), 20)
                first_companion = next(iter(trace.strikes.get(CAVALORN, [])), None)
                # Wait through the encounter, so an early command cannot hide a late bite.
                await _until(lambda: trace.hp.get(monster, 1) <= 0 or bool(trace.strikes.get(monster)), 25)
                first_wolf = next(iter(trace.strikes.get(monster, [])), None)
                hero_damage = trace.damage.get("PLAYER", [])
                check(
                    "intercepts_before_bite",
                    first_companion is not None
                    and (first_wolf is None or first_companion < first_wolf)
                    and bool(trace.damage.get(monster))
                    and not hero_damage,
                    f"first_companion_game_ms={first_companion}, first_wolf_game_ms={first_wolf}, "
                    f"hero_hp={start_hp}->{trace.hp.get('PLAYER')}, wolf_hp={trace.hp.get(monster)}",
                )
        else:
            # Both real cave bandits stand idle beside the unarmed hero. The second is held
            # listening, matching the ownership gap in the user's game.
            for i, bandit in enumerate(BANDITS):
                await link.debug("skip_important", near=bandit)
                await link.debug("npc_goto", npc=bandit, waypoint=ARENA)
                ready = await Bodies(link).act(
                    bandit,
                    "start_activity",
                    state=IDLE,
                    waypoint=ARENA,
                    minutes=60,
                    arrive=True,
                    radius=100,
                    timeout=15,
                )
                if ready.status != "arrived":
                    raise GameError(f"fight_reactions setup: {bandit} idle {ready.status}: {ready.detail}")
                await put(bandit, 1200, i * 500)
            await put(CAVALORN, -600, 0, face=BANDITS[0])
            await link.debug("player_goto", pos=point(400, -650))
            if not await talk.hold(CAVALORN, "follow"):
                raise GameError("fight_reactions setup: cannot hold companion")
            await link.act(CAVALORN, "stop")
            if not await talk.hold(BANDITS[1], "listen"):
                raise GameError("fight_reactions setup: cannot hold listening bandit")
            watch = [CAVALORN, *BANDITS, "PLAYER"]
            symbols = await link.story_eval(
                CAVALORN, [["sym", "PC_HERO" if n == "PLAYER" else n] for n in watch]
            )
            trace.symbols = dict(zip(symbols, watch, strict=True))
            await link.body_watch(watch)
            if not await _until(lambda: all(n in trace.positions for n in watch), 5):
                raise GameError("fight_reactions setup: missing bandit body observations")
            if not await _until(lambda: all(n in planner.npcs for n in BANDITS), 5):
                raise GameError("fight_reactions setup: bandits absent from planner roster")

            if case == "flee":
                bandit = BANDITS[1]
                before = trace.distance(bandit, CAVALORN)
                started = time.monotonic()
                assert talk.orders is not None
                talk.orders.fight_intent(CAVALORN, bandit)
                fled = await _until(
                    lambda: any(
                        t >= started and who == bandit and action == "flee" and args.get("npc") == CAVALORN
                        for t, who, action, args in trace.commands
                    ),
                    15,
                )
                # Distance alone is not enough: the pursuer moves too. Require the coward's
                # own body to move at least 3 m, with its flee command aimed at Cavalorn.
                origin = trace.positions.get(bandit)
                moved = await _until(
                    lambda: (
                        origin is not None
                        and bandit in trace.positions
                        and math.dist(origin, trace.positions[bandit]) >= 300
                    ),
                    8,
                )
                check(
                    "flees_companion",
                    fled and moved and trace.target.get(bandit) == trace.entities.get(CAVALORN),
                    f"flee_target={CAVALORN if fled else None}, moved={moved}, "
                    f"distance_cm={before}->{trace.distance(bandit, CAVALORN)}",
                )
                check(
                    "hero_passive",
                    not trace.strikes.get("PLAYER") and not trace.damage.get("PLAYER"),
                    f"hero_strikes={trace.strikes.get('PLAYER', [])}, "
                    f"hero_damage={trace.damage.get('PLAYER', [])}",
                )
            else:
                assert talk.orders is not None
                charge_started = time.monotonic()
                for bandit in BANDITS:
                    talk.orders.fight_intent(CAVALORN, bandit)
                    await _until(lambda b=bandit: bool(trace.damage.get(b)), 35)
                    hits = trace.damage.get(bandit, [])
                    hit = hits[0][0] if hits else None
                    await _until(
                        lambda b=bandit, h=hit: (
                            h is not None and trace.strike_after(b, CAVALORN, h) is not None
                        ),
                        3.1,
                    )
                    first = trace.strike_after(bandit, CAVALORN, hit) if hit is not None else None
                    threat = next(
                        (
                            t
                            for t, m in trace.events
                            if t >= charge_started
                            and isinstance(m, NpcEvent | PerceptionHeld)
                            and m.npc == bandit
                            and m.kind == "npc_threat"
                            and m.other == CAVALORN
                        ),
                        None,
                    )
                    voices = [
                        m
                        for t, m in trace.events
                        if t >= charge_started and isinstance(m, LineStarted) and m.npc == bandit and m.voiced
                    ]
                    armed = trace.armed.get(bandit)
                    # Game time is authoritative for draw/strike/damage. Voice events carry wall time;
                    # compare those with the arrival time of the first actual damage observation.
                    hit_wall = next(iter(trace.damage_wall.get(bandit, [])), None)
                    voice_before = hit_wall is not None and any(
                        charge_started <= t < hit_wall
                        and isinstance(m, LineStarted)
                        and m.npc == bandit
                        and m.voiced
                        for t, m in trace.events
                    )
                    shouts = combat_shouts(Path(log_dir) / "game.log", bandit)
                    native_voice_before = hit is not None and any(t < hit for t in shouts)
                    check(
                        f"{bandit}_prepares",
                        threat is not None
                        and (voice_before or native_voice_before)
                        and hit is not None
                        and armed is not None
                        and armed < hit,
                        f"threat={threat is not None}, voiced={len(voices)}, native_shout_game_ms={shouts}, "
                        f"draw_game_ms={armed}, first_hit_game_ms={hit}",
                    )
                    check(
                        f"{bandit}_fights_3s",
                        hit is not None and first is not None and first <= hit + 3000,
                        f"first_hit_game_ms={hit}, first_return_strike_game_ms={first}, "
                        f"delay_ms={first - hit if first is not None and hit is not None else None}",
                    )
                    # Finish this target before charging the other, preserving attribution of damage.
                    if bandit == BANDITS[0]:
                        await _until(
                            lambda b=bandit: (
                                trace.hp.get(b, 1) <= 1
                                or (b in talk.people and talk.people[b].activity == "down")
                            ),
                            50,
                        )
                check(
                    "hero_passive",
                    not trace.strikes.get("PLAYER") and not trace.damage.get("PLAYER"),
                    f"hero_strikes={trace.strikes.get('PLAYER', [])}, "
                    f"hero_damage={trace.damage.get('PLAYER', [])}",
                )
    finally:
        link.send = send  # type: ignore[method-assign]
        link.listeners.remove(trace.message)
        link.raw_body_listeners.remove(trace.body)
        await link.body_watch([])
        planner.builder.traits = old_traits
        if runner is not None:
            await link.send(ThreatControlCmd(hold=False))
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
            link.listeners.remove(planner.on_message)
    return results
