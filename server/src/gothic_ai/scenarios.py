"""Scripted checks that drive an NPC without an LLM."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .brain import Decider, NpcPlanner, Outcome
from .brain.reflex import Reflexes
from .budget import Budget
from .dialogue import DialogueManager
from .game import GameError, GameLink
from .incidents import IncidentDesk
from .llm import ToolCall
from .llm.fake import FakeBackend, FakeStep
from .npc_tools import GESTURES as NPC_GESTURES
from .persona import PersonaProvider, habits_of
from .protocol import (
    ActionResult,
    Detached,
    FollowEnded,
    GameMessage,
    LineStarted,
    NpcActResult,
    NpcActType,
    NpcEvent,
    NpcIdle,
    NpcRoster,
    Perception,
    PerceptionHeld,
    Saved,
    SayCmd,
    Session,
    Snapshot,
    ThreatControlCmd,
)
from .trace import TraceWriter
from .voice import VoiceInput

log = logging.getLogger(__name__)

GESTURES = ("T_COMEOVERHERE", "T_GREETNOV", "T_YES")


@dataclass(frozen=True)
class Step:
    name: str
    result: ActionResult


async def wait_message[M: GameMessage](
    link: GameLink, kind: type[M], match: Callable[[M], bool] = lambda _: True, timeout: float = 120
) -> M:
    """The next message of `kind` from the game that satisfies `match`."""
    fut: asyncio.Future[M] = asyncio.get_running_loop().create_future()

    def listener(msg: GameMessage) -> None:
        if isinstance(msg, kind) and match(msg) and not fut.done():
            fut.set_result(msg)

    link.listeners.append(listener)
    try:
        return await asyncio.wait_for(fut, timeout)
    finally:
        link.listeners.remove(listener)


async def attach_when_free(link: GameLink, npc: str, *, attempts: int = 60, delay: float = 1.0) -> None:
    """Attach, retrying while the npc is busy (e.g. in a scripted dialog with the hero)."""
    for attempt in range(attempts):
        try:
            await link.attach(npc)
            return
        except GameError as err:
            if attempt == attempts - 1:
                raise
            log.info("attach %s failed (%s), retrying", npc, err)
            await asyncio.sleep(delay)


async def m0_smoke(link: GameLink, npc: str = "NONE_100_XARDAS") -> list[Step]:
    """M0 check: take the npc over, walk him around, gesture, bring him home and back to his routine."""
    world = await link.wait_world()
    log.info("world loaded: %s", world)
    await attach_when_free(link, npc)

    snap = await link.snapshot(npc)
    home = snap.npc.waypoint or snap.npc.nearest_waypoint
    away = max(snap.nearby_waypoints, key=lambda w: w.distance, default=None)
    log.info("%s (%s) home=%s away=%s", snap.npc.id, snap.npc.name, home, away and away.name)

    steps: list[Step] = []

    async def step(name: str, coro) -> ActionResult:
        res = await coro
        steps.append(Step(name, res))
        extra = ", ".join(
            x for x in (res.detail, f"{res.distance:.0f}cm" if res.distance is not None else None) if x
        )
        log.info("%-24s %s%s", name, res.status, f" ({extra})" if extra else "")
        return res

    if away is not None:
        await step(f"go_to_waypoint {away.name}", link.act(npc, "go_to_waypoint", waypoint=away.name))
    await step("turn_to PLAYER", link.act(npc, "turn_to", npc="PLAYER"))
    for gesture in GESTURES:
        res = await step(f"play_ani {gesture}", link.act(npc, "play_ani", name=gesture))
        if res.status != "rejected":
            break
    await step("wait 1500", link.act(npc, "wait", ms=1500))
    await step("go_to_npc PLAYER", link.act(npc, "go_to_npc", npc="PLAYER"))
    if home is not None:
        await step(f"go_to_waypoint {home}", link.act(npc, "go_to_waypoint", waypoint=home))
    await step("return_to_routine", link.act(npc, "return_to_routine"))
    return steps


async def m1_chat(
    link: GameLink, dialogue: DialogueManager, npc: str = "NONE_100_XARDAS", text: str = "Кто ты такой?"
) -> list[str]:
    """M1 check without the keyboard: player text injected on the server, the npc must speak every line."""
    await link.wait_world()
    await attach_when_free(link, npc)
    snap = await link.snapshot(npc)
    conv = dialogue.open(npc, snap.npc.name)

    started: list[str] = []
    link.listeners.append(lambda msg: started.append(msg.line) if isinstance(msg, LineStarted) else None)

    await dialogue.say_as_player(npc, text)
    lines = conv.last_lines
    log.info("%s said %d line(s), waiting for the npc to speak them", npc, len(lines))
    for _ in range(20 * max(1, len(lines))):  # ~10 s per line
        if all(line in started for line in lines):
            break
        await asyncio.sleep(0.5)
    await link.act(npc, "return_to_routine")
    missing = [line for line in lines if line not in started]
    if not lines or missing:
        raise RuntimeError(f"npc did not speak lines: {missing or 'no lines generated'}")
    return lines


async def m2_actions(link: GameLink, npc: str = "NONE_100_XARDAS") -> dict[str, str]:
    """M2 check of the game-side actions: snapshot contents, give_item, follow/stop, gestures."""
    await link.wait_world()
    await attach_when_free(link, npc)
    snap = await link.snapshot(npc)
    log.info("inventory of %s: %s", npc, [(i.item, i.name, i.count, i.equipped) for i in snap.npc.inventory])
    if snap.player is not None:
        p = snap.player
        log.info(
            "player: guild=%s armor=%s melee=%s ranged=%s hp=%s/%s",
            p.guild,
            p.armor,
            p.melee,
            p.ranged,
            p.hp,
            p.hp_max,
        )
    log.info("nearby npcs: %s", [(o.id, o.name, round(o.distance)) for o in snap.nearby_npcs])

    results: dict[str, str] = {}
    potion = "ITPO_HEALTH_01"
    results["debug_add_item"] = (await link.act(npc, "debug_add_item", item=potion, count=2)).status

    def count_of(s: Snapshot) -> int:
        return next((i.count for i in s.npc.inventory if i.item == potion), 0)

    # the whole path: the model speaks and calls give_item, the game transfers, the outcome becomes a note
    llm = FakeBackend(
        "Держи, это поможет тебе.", delay=0, tool_calls=[ToolCall("give_item", {"item": potion, "count": 1})]
    )
    dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False)
    conv = dialogue.open(npc, snap.npc.name)
    before = count_of(await link.snapshot(npc, fresh=True))
    await dialogue.say_as_player(npc, "Дай зелье")
    for _ in range(60):
        if any(m.role == "developer" for m in conv.history):
            break
        await asyncio.sleep(0.5)
    after = count_of(await link.snapshot(npc, fresh=True))
    note = next((m.content for m in conv.history if m.role == "developer"), "no note")
    results["give_item"] = (
        f"{'done' if after == before - 1 else 'failed'} ({potion}: {before} -> {after}, {note})"
    )

    res = await link.act(npc, "give_item", item="ITMI_NOT_A_REAL_ITEM", count=1)
    results["give_item_missing"] = res.status

    ended: list[str] = []
    link.listeners.append(lambda m: ended.append(m.reason) if isinstance(m, FollowEnded) else None)
    res = await link.act(npc, "follow", npc="PLAYER")
    following = (await link.snapshot(npc, fresh=True)).npc.following
    results["follow"] = f"{res.status}, following={following}"
    await asyncio.sleep(2)
    res = await link.act(npc, "stop")
    await asyncio.sleep(0.5)
    results["stop"] = f"{res.status}, follow_ended={ended}"

    for gesture, anim in NPC_GESTURES.items():
        results[f"gesture {gesture}"] = (await link.act(npc, "play_ani", name=anim)).status

    await link.act(npc, "return_to_routine")
    for name, outcome in results.items():
        log.info("%-24s %s", name, outcome)
    return results


async def tts_lines(
    link: GameLink, dialogue: DialogueManager, npc: str = "NONE_100_XARDAS"
) -> dict[str, str]:
    """TTS check: every line of a reply reaches the game with its audio and is spoken aloud."""
    await link.wait_world()
    await attach_when_free(link, npc)
    snap = await link.snapshot(npc)
    conv = dialogue.open(npc, snap.npc.name)
    voiced: dict[str, bool] = {}
    link.listeners.append(
        lambda m: voiced.__setitem__(m.line, m.voiced) if isinstance(m, LineStarted) else None
    )
    started = asyncio.get_running_loop().time()
    await dialogue.say_as_player(npc, "Расскажи о драконах.")
    lines = list(conv.last_lines)
    for _ in range(240):  # each line is voiced, then spoken for as long as its audio lasts
        if lines and all(line in voiced for line in lines):
            break
        await asyncio.sleep(0.25)
    first = asyncio.get_running_loop().time() - started
    await asyncio.sleep(3)  # let the last line play
    await link.act(npc, "return_to_routine")
    ok = bool(lines) and all(voiced.get(line) for line in lines)
    aloud = sum(voiced.get(line, False) for line in lines)
    results = {"voice": f"{'done' if ok else 'failed'} ({aloud}/{len(lines)} voiced)"}
    results["timing"] = f"done (all lines started within {first:.1f}s)"
    for name, outcome in results.items():
        log.info("%-10s %s", name, outcome)
    return results


async def m3_memory_voice(
    link: GameLink,
    dialogue: DialogueManager,
    voice: VoiceInput | None,
    npc: str = "NONE_100_XARDAS",
    slot: int = 9,
) -> dict[str, str]:
    """M3 check: memories survive save/load and follow the save, push-to-talk becomes a player line.

    Talk, save into `slot`, talk more, load the slot: the npc must remember the first talk only.
    Then one push-to-talk utterance (the server's audio source, e.g. a wav file) goes through ASR.
    """
    results: dict[str, str] = {}
    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    assert link.session is not None
    first = link.session.branch
    results["session"] = f"done ({first}, {link.session.reason})"

    await attach_when_free(link, npc)
    snap = await link.snapshot(npc)
    dialogue.open(npc, snap.npc.name)
    await dialogue.say_as_player(npc, "Запомни: меня зовут Тестер.")
    head = dialogue.memory.head(first)
    await asyncio.sleep(0.5)  # the game has the head by now

    for attempt in range(30):  # the game refuses to save during the opening dialog
        try:
            saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
            await link.debug("save", slot)
            break
        except GameError as err:
            saving.cancel()
            if attempt == 29:
                raise
            log.info("save refused (%s), retrying", err)
            await asyncio.sleep(2)
    saved = await saving
    results["save"] = f"{'done' if (saved.branch, saved.seq) == (first, head) else 'failed'} ({saved.seq})"

    await dialogue.say_as_player(npc, "А это я сказал уже после сохранения.")
    loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
    await link.debug("load", slot)
    session = await loaded
    ok = (session.parent, session.parent_seq) == (first, head) and session.branch != first
    results["load"] = (
        f"{'done' if ok else 'failed'} ({session.branch} from {session.parent}@{session.parent_seq})"
    )

    await link.wait_world()
    await attach_when_free(link, npc)
    snap = await link.snapshot(npc, fresh=True)
    conv = dialogue.open(npc, snap.npc.name)
    said = [m.content for m in conv.history if m.role == "user"]
    ok = said == ["Запомни: меня зовут Тестер."]
    results["memory"] = f"{'done' if ok else 'failed'} (remembers {said})"

    if voice is not None:
        started: list[str] = []
        link.listeners.append(lambda m: started.append(m.line) if isinstance(m, LineStarted) else None)
        before = len(conv.memory.entries)
        voice.start(npc)
        await asyncio.sleep(getattr(voice.source, "duration", 0.3) + 0.2)  # a wav is "spoken" in real time
        voice.stop(npc, recognize=True)
        for _ in range(120):
            if len(conv.memory.entries) >= before + 2 and conv.last_lines and conv.last_lines[0] in started:
                break
            await asyncio.sleep(0.25)
        heard = next((e.content for e in conv.memory.entries[before:] if e.role == "user"), None)
        results["voice"] = f"{'done' if heard else 'failed'} (heard {heard!r})"

    await link.act(npc, "return_to_routine")
    for name, outcome in results.items():
        log.info("%-10s %s", name, outcome)
    return results


async def m4_planner_bridge(
    link: GameLink, npc: str = "NONE_100_XARDAS", planner: NpcPlanner | None = None
) -> dict[str, str]:
    """M4 check of what the npc planner needs from the bridge (protocol v5): the roster, soft actions
    queued like script actions and their end (npc_idle), and that a taken-over npc is left alone."""
    events: list[NpcEvent] = []
    link.listeners.append(lambda m: events.append(m) if isinstance(m, NpcEvent) else None)
    await link.wait_world()
    results: dict[str, str] = {}
    roster = await wait_message(link, NpcRoster, lambda m: bool(m.npcs), timeout=120)
    if not any(e.id == npc for e in roster.npcs):  # e.g. a save elsewhere: the nearest npc on its routine
        free = sorted((e for e in roster.npcs if e.activity == "routine"), key=lambda e: e.distance)
        npc = (free or roster.npcs)[0].id
    me = next(e for e in roster.npcs if e.id == npc)
    log.info("roster: %s", [(e.id, e.name, round(e.distance), e.activity, e.in_view) for e in roster.npcs])
    results["roster"] = (
        f"done ({len(roster.npcs)} npc, {npc}: {me.distance:.0f} cm, {me.activity}, in_view={me.in_view}, "
        f"time {roster.hour}:{roster.minute:02d})"
    )

    async def soft(action: NpcActType, target: str = npc, wait_busy: bool = True, **args: object) -> str:
        for _ in range(60 if wait_busy else 1):  # the npc may be in a scripted dialog at a new game
            # the bridge works in the world tick: while the game pauses (menu, video) it answers later
            res = asyncio.ensure_future(
                wait_message(link, NpcActResult, lambda m: m.npc == target, timeout=15)
            )
            idle = asyncio.ensure_future(wait_message(link, NpcIdle, lambda m: m.npc == target, timeout=40))
            await asyncio.sleep(0)  # both listen before the command goes out
            await link.npc_act(target, action, **args)
            r = await res
            if r.status == "queued":
                i = await idle
                return f"{'done' if i.reason == 'done' else 'failed'} ({i.reason})"
            idle.cancel()
            if not wait_busy or "busy" not in (r.detail or ""):
                return f"rejected ({r.detail})"
            await asyncio.sleep(1)
        return "rejected (busy for a minute)"

    results["turn_to"] = await soft("turn_to")
    results["say_svm"] = await soft("say_svm", svm="NOTNOW")
    results["play_ani"] = await soft("play_ani", name="T_GREETNOV")
    results["look_at"] = await soft("look_at", ms=1000)
    results["unknown_npc"] = await soft("turn_to", target="NOT_AN_NPC")

    await attach_when_free(link, npc)
    busy = await wait_message(
        link, NpcRoster, lambda m: any(e.id == npc and e.activity == "attached" for e in m.npcs), timeout=5
    )
    results["attached_in_roster"] = "done" if busy else "failed"
    results["attached_soft_act"] = await soft("turn_to", wait_busy=False)
    await link.act(npc, "return_to_routine")

    near = [e for e in events if e.npc == npc and e.kind == "player_near"]
    if me.distance <= 500:  # the bridge reports the player coming within 5 m
        results["player_near"] = f"done ({near[0].distance:.0f} cm)" if near else "failed (no event)"
    if planner is not None:
        await asyncio.sleep(1)
        snap = planner.snapshot()
        asked = sum(snap["sent"].values())
        ok = snap["npcs"] and (asked > 0 or not near)  # an event near the player makes it ask the model
        results["planner"] = f"{'done' if ok else 'failed'} ({snap})"
    for name, outcome in results.items():
        log.info("%-20s %s", name, outcome)
    return results


class FixedDecider:
    """A stand-in for the behavioral model: per question the first of its choices the question offers, and a
    fixed level for score questions (escalate, attitude)."""

    def __init__(
        self, choices: dict[str, str | tuple[str, ...]], scores: dict[str, float] | None = None
    ) -> None:
        self.choices = choices
        self.scores = scores or {}

    async def decide(self, rid, lane, priority, deadline_ms, state, questions) -> Outcome:  # noqa: ANN001
        answers: dict[str, dict] = {}
        for name, q in questions.items():
            if q["type"] == "score":
                answers[name] = {"type": "score", "score": self.scores.get(name, 2.0), "confidence": 0.8}
                continue
            prefs = self.choices.get(name, ())
            prefs = (prefs,) if isinstance(prefs, str) else prefs
            pick = next((c for c in prefs if c in q["criteria"]), next(iter(q["criteria"])))
            answers[name] = {
                "type": "choice",
                "choice": pick,
                "probabilities": {pick: 0.9},
                "confidence": 0.9,
            }
        return Outcome("answers", answers, 0.0, 1.0)

    async def cancel(self, rid: str) -> None:
        pass


async def m5_turn_loop(
    link: GameLink,
    npc: str = "NONE_100_XARDAS",
    decider: Decider | None = None,
    trace: TraceWriter | None = None,
) -> dict[str, str]:
    """M5a on the game: the turn loop with checks, intents moving the body, and the two loops on an
    incident (the body reacts before the words). The conversational model is scripted; the behavioral
    model is the live service when given, a fixed stand-in otherwise. The game cannot hit an npc on
    command, so the incident's perception is injected on the server side."""
    await link.wait_world()
    await attach_when_free(link, npc)
    snap = await link.snapshot(npc, fresh=True)
    results: dict[str, str] = {}
    potion = "ITPO_HEALTH_01"
    results["debug_add_item"] = (await link.act(npc, "debug_add_item", item=potion, count=2)).status

    def count_of(s: Snapshot) -> int:
        return next((i.count for i in s.npc.inventory if i.item == potion and not i.equipped), 0)

    intent = {"target": None, "attitude": "friendly", "agenda": "wants the hero to visit Lord Hagen"}
    llm = FakeBackend(
        script=[
            FakeStep(
                "Держи свиток огненного шара.",
                [ToolCall("give_item", {"item": "ITSC_NOT_THERE", "count": 1})],
            ),
            FakeStep("Хотя нет, такого свитка у меня нет."),
            FakeStep("Вот, возьми зелье.", [ToolCall("give_item", {"item": potion, "count": 1})]),
            FakeStep("Хорошо, пойдём вместе.", [ToolCall("set_intent", {"goal": "follow_hero", **intent})]),
            FakeStep(
                "Довольно, у меня свои дела.", [ToolCall("set_intent", {"goal": "keep_routine", **intent})]
            ),
            FakeStep("Проклятая тварь! Ко мне, герой!"),
        ],
        delay=0,
    )
    budget = Budget()
    dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False, trace=trace, budget=budget)
    reflexes = Reflexes(decider or FixedDecider({"react": "run_to_hero", "gesture": "nod"}))
    desk = IncidentDesk(link, dialogue, reflexes, budget, trace)
    desk.attached.add(npc)  # it was attached before the desk was listening
    conv = dialogue.open(npc, snap.npc.name)

    async def following(expected: bool) -> bool:
        for _ in range(20):
            if (await link.snapshot(npc, fresh=True)).npc.following == expected:
                return True
            await asyncio.sleep(0.5)
        return False

    try:
        # 1. a commitment the npc cannot keep is rejected before the game and corrected in the same turn
        before = count_of(await link.snapshot(npc, fresh=True))
        await dialogue.say_as_player(npc, "Дай мне свиток огненного шара")
        said = conv.history[-1].content if conv.history else ""
        unchanged = count_of(await link.snapshot(npc, fresh=True)) == before
        ok = len(llm.calls) == 2 and "Хотя нет" in said and unchanged
        results["rejected_give"] = f"{'done' if ok else 'failed'} ({len(llm.calls)} calls: {said})"

        # 2. a commitment it can keep: the body gives after the line
        await dialogue.say_as_player(npc, "Тогда дай зелье")
        for _ in range(60):
            if any(m.role == "developer" and "отдал" in m.content for m in conv.history):
                break
            await asyncio.sleep(0.5)
        after = count_of(await link.snapshot(npc, fresh=True))
        results["give"] = f"{'done' if after == before - 1 else 'failed'} ({potion}: {before} -> {after})"

        # 3. intents move the body: follow, then back to its own business
        await dialogue.say_as_player(npc, "Пойдём со мной")
        results["intent_follow"] = "done" if await following(True) else "failed (not following)"
        await dialogue.say_as_player(npc, "Можешь остаться")
        results["intent_routine"] = "done" if await following(False) else "failed (still following)"

        # 4. an incident: the body reacts at once, the words come after
        calls = len(llm.calls)
        reacted_at: float | None = None
        started = time.monotonic()
        link._dispatch(
            Perception(type="perception", npc=npc, perc="ASSESSDAMAGE", other="WOLF", distance=300.0)
        )
        for _ in range(100):
            if reacted_at is None and any(r.question == "react" for r in desk.reactions):
                reacted_at = time.monotonic() - started
            if len(llm.calls) > calls and conv.reply is not None and conv.reply.done():
                break
            await asyncio.sleep(0.1)
        words_at = time.monotonic() - started
        reaction = next((r for r in reversed(desk.reactions) if r.question == "react"), None)
        spoke = conv.history[-1].content if conv.history and conv.history[-1].role == "assistant" else ""
        results["incident_reflex"] = (
            f"{'done' if reaction else 'failed'} "
            f"({reaction.choice if reaction else '-'}, applied={reaction.applied if reaction else '-'}, "
            f"{(reacted_at or 0) * 1000:.0f} ms)"
        )
        results["incident_words"] = (
            f"{'done' if spoke and (reacted_at or 99) < words_at else 'failed'} ({spoke})"
        )
    finally:
        await link.act(npc, "return_to_routine")
    for name, outcome in results.items():
        log.info("%-24s %s", name, outcome)
    return results


async def m5b_bridge(link: GameLink, npc: str = "NONE_100_XARDAS") -> dict[str, str]:
    """M5b on the game: threats from others reach the server, the player's drawn weapon is perceived,
    and the new body actions work for free and attached npc's. A wolf is spawned next to the npc."""
    await link.wait_world()
    events: list[NpcEvent] = []
    link.listeners.append(lambda m: events.append(m) if isinstance(m, NpcEvent) else None)
    results: dict[str, str] = {}

    async def event(kinds: set[str], timeout: float) -> NpcEvent | None:
        for _ in range(int(timeout / 0.2)):
            hit = next((e for e in events if e.kind in kinds), None)
            if hit is not None:
                return hit
            await asyncio.sleep(0.2)
        return None

    # 0. a free npc speaks a line, queued like its own (it waits for what the npc is busy with, e.g.
    # walking back to its routine spot: the engine does not take speech from the queue while walking)
    started_lines: list[LineStarted] = []
    link.listeners.append(lambda m: started_lines.append(m) if isinstance(m, LineStarted) else None)
    t0 = time.monotonic()
    await link.send(SayCmd(npc=npc, line="AIB_900001", text="Опять ты здесь.", overlay=None))
    for _ in range(150):
        if any(m.line == "AIB_900001" for m in started_lines):
            break
        await asyncio.sleep(0.1)
    queued = any(m.line == "AIB_900001" for m in started_lines)
    results["free_say"] = (
        f"{'done' if queued else 'failed'} (line started after {time.monotonic() - t0:.1f} s)"
    )

    # 1. the player draws a weapon next to the npc: the npc perceives it (it is free, scripts run)
    await attach_when_free(link, npc)
    await link.act(npc, "go_to_npc", npc="PLAYER", radius=300)
    await link.act(npc, "return_to_routine")
    events.clear()
    await link.debug("player_weapon", state="melee")
    e = await event({"weapon_drawn"}, 5)
    results["weapon_drawn"] = f"done ({e.npc}, {e.distance:.0f} cm)" if e else "failed (no weapon_drawn)"
    await link.debug("player_weapon", state="none")

    # 2. a wolf next to the npc: the npc senses it or sees it attack
    events.clear()
    await link.debug("spawn", instance="WOLF", near=npc, distance=400)
    e = await event({"monster_near", "monster_attack", "hero_attacked"}, 20)
    results["monster_event"] = (
        f"done ({e.npc}: {e.kind} {e.other} '{e.other_name}' {e.distance or 0:.0f} cm {e.detail or ''})"
        if e
        else "failed (no threat event)"
    )

    # 3. a free npc fights it with the game's own combat; npc_idle once it is over
    idles: list[NpcIdle] = []
    link.listeners.append(lambda m: idles.append(m) if isinstance(m, NpcIdle) else None)
    started = time.monotonic()
    act = await link.npc_act(npc, "fight", npc="WOLF")
    for _ in range(300):
        if any(i.act == act for i in idles):
            break
        await asyncio.sleep(0.2)
    idle = next((i for i in idles if i.act == act), None)
    took = time.monotonic() - started
    ok = idle is not None and idle.reason == "done"
    results["free_fight"] = (
        f"{'done' if ok else 'failed'} ({idle.reason if idle else 'no npc_idle'}, {took:.1f} s)"
    )

    # 4. an attached npc: weapons, its own voiced line, and fight/flee handed to the scripts and back
    detached: list[str] = []
    link.listeners.append(lambda m: detached.append(m.reason) if isinstance(m, Detached) else None)
    await attach_when_free(link, npc)

    def wolves(sn: Snapshot) -> str:
        return ", ".join(
            f"{o.name} {o.distance / 100:.0f} m{' dead' if o.dead else ''}" for o in sn.nearby_npcs
        )

    results["after_free_fight"] = f"done (around: {wolves(await link.snapshot(npc, fresh=True)) or 'nobody'})"
    for name, action, args in (
        ("attached_draw", "draw_weapon", {}),
        ("attached_remove", "remove_weapon", {}),
        ("attached_svm", "say_svm", {"svm": "NOTNOW"}),
    ):
        res = await link.act(npc, action, **args)  # type: ignore[arg-type]
        results[name] = f"{res.status} {res.detail or ''}".strip()
    await link.debug("spawn", instance="WOLF", near=npc, distance=500)
    started = time.monotonic()
    res = await link.act(npc, "fight", npc="WOLF", timeout=90)
    took = time.monotonic() - started
    snap = await link.snapshot(npc, fresh=True)
    results["attached_fight"] = (
        f"{'done' if res.status == 'done' and not detached else 'failed'} "
        f"({res.status} {res.detail or ''} in {took:.1f} s, still attached: {not detached}, "
        f"hp {snap.npc.hp}/{snap.npc.hp_max}, around: {wolves(snap)})"
    )
    await link.debug("spawn", instance="WOLF", near=npc, distance=800)
    res = await link.act(npc, "flee", npc="WOLF", timeout_ms=4000)
    results["attached_flee"] = (
        f"{'done' if res.status in ('done', 'timeout') and not detached else 'failed'} "
        f"({res.status} {res.detail or ''}, still attached: {not detached})"
    )
    await link.act(npc, "return_to_routine")

    # 5. a free npc speaks: queued like its own lines, or on the move (overlay) without stopping
    # right after return_to_routine the npc walks back to its spot: queued speech would wait for that,
    # a line on the move starts at once
    long_line = "Слушай внимательно, у нас мало времени, и я не стану повторять дважды: драконы уже здесь."
    t0 = time.monotonic()
    await link.send(SayCmd(npc=npc, line="AIB_900002", text=long_line, overlay=True))
    for _ in range(50):
        if any(m.line == "AIB_900002" for m in started_lines):
            break
        await asyncio.sleep(0.05)
    took = time.monotonic() - t0
    said = any(m.line == "AIB_900002" for m in started_lines)
    results["free_say_on_the_move"] = (
        f"{'done' if said and took < 1.0 else 'failed'} (started after {took:.2f} s while the npc walks back)"
    )

    # 6. the routine: read it, put the npc to an activity for a while, then back to a routine variant
    info = await link.routine(npc)
    entries = "; ".join(f"{e.start}-{e.end} {e.state} {e.waypoint}" for e in info.entries)
    results["routine"] = (
        f"{'done' if info.entries else 'failed'} (now {info.current}, variants {info.routines}, {entries})"
    )
    if info.entries:
        spot = info.entries[0].waypoint
        act = await link.npc_act(npc, "start_activity", state="ZS_SIT_CAMPFIRE", waypoint=spot, minutes=30)
        await asyncio.sleep(1.0)
        now = (await link.routine(npc)).current
        results["start_activity"] = (
            f"{'done' if now == 'ZS_SIT_CAMPFIRE' else 'failed'} (now {now} at {spot})"
        )
        variant = "START" if "START" in info.routines or not info.routines else info.routines[0]
        await link.npc_act(npc, "set_routine", name=variant)
        await asyncio.sleep(1.0)
        now = (await link.routine(npc)).current
        back = now == info.entries[0].state or now != "ZS_SIT_CAMPFIRE"
        results["set_routine"] = f"{'done' if back else 'failed'} ({variant}: now {now})"
    for name, outcome in results.items():
        log.info("%-24s %s", name, outcome)
    return results


async def m5c_free_npc(link: GameLink, npc: str = "NONE_100_XARDAS") -> dict[str, str]:
    """M5c on the game: a free npc meets a wolf. The threat is held from the npc's scripts; the behavioral
    model (a fixed stand-in here: fight, escalate 3.6 — the live model is measured by evals) takes the body
    over with the game's own combat; the moment calls for words, and the conversational model (scripted)
    speaks for the npc on the move."""
    from .brain import GameApplier
    from .brain.planner import PlannerConfig

    await link.wait_world()
    results: dict[str, str] = {}
    started_lines: list[LineStarted] = []
    link.listeners.append(lambda m: started_lines.append(m) if isinstance(m, LineStarted) else None)
    held: list[PerceptionHeld] = []
    link.listeners.append(lambda m: held.append(m) if isinstance(m, PerceptionHeld) else None)

    applier = GameApplier(link)
    planner = NpcPlanner(
        FixedDecider({"react": "fight", "plan": "h1"}, {"escalate": 3.6}),
        applier,
        config=PlannerConfig(ask_attitude=False, plan_period_s=3.0),
        routine_of=link.routine,
        habits_of=habits_of,
    )
    link.listeners.append(planner.on_message)
    runner = asyncio.create_task(planner.run())
    llm = FakeBackend("Ещё одна тварь из тьмы. Прочь, или сгоришь!", delay=0)
    dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False)
    budget = Budget()
    IncidentDesk(link, dialogue, None, budget, None, planner=planner)
    await link.send(ThreatControlCmd(hold=True))
    try:
        for _ in range(50):  # the roster brings the npc to the planner
            if npc in planner.npcs:
                break
            await asyncio.sleep(0.2)

        # 1. a quiet moment: the npc thinks of its next hour, from its own day and habits
        plan = None
        for _ in range(300):
            plan = next((d for d in applier.recent if d.npc == npc and d.question == "plan"), None)
            if plan is not None:
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(1.0)
        now = (await link.routine(npc)).current
        results["day_plan"] = (
            f"{'done' if plan and plan.args and now == plan.args['state'] else 'failed'} "
            f"({plan.choice + ' ' + str(plan.args) if plan else 'no plan'}; now {now})"
        )
        await link.npc_act(npc, "set_routine", name="START")

        # 2. a wolf
        t0 = time.monotonic()
        await link.debug("spawn", instance="WOLF", near=npc, distance=400)
        decision = None
        for _ in range(150):
            decision = next((d for d in applier.recent if d.npc == npc and d.target), None)
            if decision is not None:
                break
            await asyncio.sleep(0.1)
        verdicts = list(applier.verdicts)
        results["threat_held"] = (
            f"done ({len(held)} held: {', '.join(f'{h.kind}/{h.other}' for h in held)}; verdicts {verdicts})"
            if held and any(to == "server" for _, to in verdicts)
            else f"failed (held {len(held)}, verdicts {verdicts})"
        )
        results["body_reacts"] = (
            f"done ({decision.choice} against {decision.target}, "
            f"{time.monotonic() - t0:.1f} s after the spawn)"
            if decision
            else "failed (no decision)"
        )
        words = None
        for _ in range(150):
            words = next((m for m in started_lines if m.npc == npc), None)
            if words is not None:
                break
            await asyncio.sleep(0.1)
        results["words_on_the_move"] = (
            f"done ({time.monotonic() - t0:.1f} s after the spawn: {llm.calls[0][1][-1].content})"
            if words and llm.calls
            else "failed (no words)"
        )
    finally:
        await link.send(ThreatControlCmd(hold=False))
        runner.cancel()
    for name, outcome in results.items():
        log.info("%-24s %s", name, outcome)
    return results


async def rescue(
    link: GameLink,
    npc: str = "BAU_954_MALETH",
    planner: NpcPlanner | None = None,
    killer: str = "hero",
    dialogue: DialogueManager | None = None,
) -> dict[str, str]:
    """M5c on the game, an ordinary npc: the hero comes up to the shepherd, a wolf attacks him from behind.
    The body reacts at once (a call for help: he runs from the wolf, shouting, and the scripts keep out of it)
    and the conversational model speaks for him on the move. Then the wolf dies: the danger is over
    (`monster_killed`). Killed by the hero (`killer="hero"`), the shepherd reacts to his rescuer, his attitude
    is asked, and he speaks again; killed by the shepherd himself (`killer="npc"`), he says a word about the
    danger he spoke of. With the server's planner (DECIDER_URL) the live models decide and the server's LLM
    speaks; without it a fixed stand-in decides and the words are scripted."""
    from .brain import GameApplier
    from .incidents import FREE_NEW_REASON_S

    await link.wait_world()
    results: dict[str, str] = {}
    lines: list[tuple[float, LineStarted]] = []
    link.listeners.append(
        lambda m: lines.append((time.monotonic(), m)) if isinstance(m, LineStarted) else None
    )
    events: list[NpcEvent | PerceptionHeld] = []
    link.listeners.append(
        lambda m: events.append(m) if isinstance(m, NpcEvent | PerceptionHeld) and m.npc == npc else None
    )
    runner = None
    if planner is None:
        planner = NpcPlanner(
            FixedDecider({"react": ("thank", "call_help")}, {"escalate": 3.6, "attitude": 4.0}),
            GameApplier(link),
        )
        link.listeners.append(planner.on_message)
        runner = asyncio.create_task(planner.run())
        after = "Спасибо, выручил ты меня." if killer == "hero" else "Хм… а волк-то был не такой уж страшный."
        llm = FakeBackend(script=[FakeStep("Волк! Помогите, волк!"), FakeStep(after)], delay=0)
        dialogue = DialogueManager(link, llm, PersonaProvider(None), greet=False)
        IncidentDesk(link, dialogue, None, Budget(), None, planner=planner)
        await link.send(ThreatControlCmd(hold=True))
    # the npc's lines as the server sent them: line id -> (when, text); the game reports only the id
    texts: dict[str, tuple[float, str]] = {}
    send = link.send

    async def sending(cmd: Any) -> Any:
        if isinstance(cmd, SayCmd) and cmd.npc.upper() == npc:
            texts[cmd.line] = (time.monotonic(), cmd.text)
        return await send(cmd)

    link.send = sending  # type: ignore[method-assign]
    # when the npc's turns about events began: a line sent after one begun after the kill is about the kill
    # (a turn begins once the one before has sent its lines)
    turns: list[float] = []
    event_turn = dialogue.event_turn if dialogue is not None else None
    if dialogue is not None:

        def turn(who: str, *args: Any, **kwargs: Any) -> Any:
            if who.upper() == npc:
                turns.append(time.monotonic())
            return event_turn(who, *args, **kwargs)

        dialogue.event_turn = turn  # type: ignore[method-assign]
    recent = getattr(planner.applier, "recent", [])
    verdicts = getattr(planner.applier, "verdicts", [])
    # what the npc was doing, by the roster: (when it started, activity); a weapon out is "fight", an
    # original dialog with the hero "dialog"
    doing: list[tuple[float, str]] = []

    def track(m: GameMessage) -> None:
        if isinstance(m, NpcRoster):
            now = next((e.activity for e in m.npcs if e.id == npc), "away")
            if not doing or doing[-1][1] != now:
                doing.append((time.monotonic(), now))

    link.listeners.append(track)
    # what the behavioral model is asked about the npc, word for word (the results quote it)
    asked: list[tuple[str, str]] = []
    decide = planner.decider.decide

    async def recording(rid, lane, priority, deadline_ms, state, questions):  # noqa: ANN001, ANN202
        if rid.startswith(npc):
            asked.append(("+".join(questions), state))
        return await decide(rid, lane, priority, deadline_ms, state, questions)

    planner.decider.decide = recording  # type: ignore[method-assign]

    def decided(since: float, question: str, threat: bool | None = None) -> list:
        return [
            d
            for d in list(recent)[since:]
            if d.npc == npc and d.question == question and (threat is None or bool(d.target) == threat)
        ]

    async def wait(check: Callable[[], Any], timeout: float) -> Any:
        for _ in range(int(timeout / 0.1)):
            if found := check():
                return found
            await asyncio.sleep(0.1)
        return None

    try:
        # 1. the hero next to the npc, looking at it. The npc's important dialog (the start of the game: it
        # has not met the hero) would start as soon as the two are close and calm; lines on the move do not go
        # over a dialog
        await link.debug("skip_important", near=npc)
        await link.debug("teleport", near=npc, distance=600)
        rec = await wait(lambda: (r := planner.npcs.get(npc)) is not None and r.in_view and r, 10)
        results["hero_next_to_npc"] = (
            f"done ({rec.name} {rec.distance_cm / 100:.0f} m away, in view)"
            if rec
            else "failed (not in view)"
        )
        await asyncio.sleep(2.0)  # whatever the npc does about the hero coming up

        # 2. a wolf from behind the npc: the body reacts at once, the words follow on the move
        mark, t0 = len(recent), time.monotonic()
        await link.debug("spawn", instance="WOLF", near=npc, distance=-500)
        reaction = await wait(lambda: decided(mark, "react", threat=True), 15)
        seen = ", ".join(dict.fromkeys(f"{e.type}:{e.kind}/{e.other}" for e in events)) or "none"
        results["threat_reaction"] = (
            f"done ({reaction[0].choice} {reaction[0].confidence:.2f} against {reaction[0].target}, "
            f"{time.monotonic() - t0:.1f} s; events: {seen})"
            if reaction
            else f"failed (no reaction to the wolf; events: {seen}; planner: {planner.snapshot()})"
        )
        first = await wait(lambda: next(((t, m) for t, m in lines if m.npc == npc and t >= t0), None), 12)
        results["words_about_the_wolf"] = (
            f"done ({first[0] - t0:.1f} s after the wolf came: {texts.get(first[1].line, (0, '?'))[1]})"
            if first
            else "failed (no words)"
        )
        holds = {e.hold for e in events if isinstance(e, PerceptionHeld)}
        to = [v for h, v in list(verdicts) if h in holds]
        armed = [t for t, what in doing if what == "fight" and t >= t0]
        took = reaction[0].choice if reaction else None
        results["body_and_scripts"] = (
            f"{'done' if (took == 'fight') == bool(armed) else 'failed'} ({took}: "
            f"{to.count('server')} held from the scripts, {to.count('scripts')} given to them; "
            f"{f'weapon out {armed[0] - t0:.1f} s after the wolf came' if armed else 'no weapon out'})"
        )

        # 3. the wolf dies: the danger is over, the npc thanks its rescuer, or says a word about the danger it
        # beat itself. Soon, while the wolf is still after the npc (one it lost is too far for the npc to see
        # it die), but not before words about something new may follow (asked for right after the reaction)
        await asyncio.sleep(max(0.0, t0 + FREE_NEW_REASON_S + 1.0 - time.monotonic()))
        # the hero runs after it: an npc that ran from the wolf is soon out of the hero's reach (the bridge
        # reports npc's within 30 m of him) and of his hearing (20 m)
        await link.debug("teleport", near=npc, distance=700)
        mark, t1 = len(recent), time.monotonic()
        try:
            # the one after the npc, not a wild one
            await link.debug("kill", instance="WOLF", near=npc, by="PLAYER" if killer == "hero" else npc)
            killed = f"by the {killer}"
        except GameError as err:
            killed = f"not by the hero ({err})"
        ended = await wait(
            lambda: next((e for e in events if isinstance(e, NpcEvent) and e.kind == "monster_killed"), None),
            5,
        )
        results["monster_killed"] = (
            f"done ({killed}: {ended.other} killed {ended.victim} '{ended.detail}')"
            if ended
            else f"failed (no monster_killed; {killed})"
        )
        rec = planner.npcs.get(npc)
        threats = [s.kind for s in rec.stimuli if s.policy.threat and s.other == "WOLF"] if rec else []
        results["threat_over"] = "done" if rec and not threats else f"failed (still: {threats})"
        # an npc that fought the wolf itself reacts once its weapon is put away
        # about the kill (another threat may have come meanwhile)
        thanks = await wait(lambda: [d for d in decided(mark, "react") if "monster_killed" in d.stimuli], 15)
        attitude = await wait(lambda: decided(mark, "attitude"), 6)
        results["rescue_reaction"] = (
            f"done ({thanks[0].choice} {thanks[0].confidence:.2f}"
            f"{f', attitude {attitude[0].attitude:.1f}' if attitude else ', no attitude answer'})"
            if thanks
            else f"failed (nothing applied; planner: {planner.snapshot()})"
        )

        def after_kill() -> tuple[float, LineStarted] | None:
            begun = next((t for t in turns if t >= t1), None)
            return next(
                (
                    (t, m)
                    for t, m in lines
                    if m.npc == npc and begun and texts.get(m.line, (0.0, ""))[0] >= begun
                ),
                None,
            )

        second = await wait(after_kill, 20)
        results["words_after_the_kill"] = (
            f"done ({second[0] - t1:.1f} s after the kill: {texts.get(second[1].line, (0, '?'))[1]})"
            if second
            else "failed (no words)"
        )
        if second:
            await asyncio.sleep(3.0)  # the rest of the turn, for the trace
    finally:
        planner.decider.decide = decide  # type: ignore[method-assign]
        if dialogue is not None:
            dialogue.event_turn = event_turn  # type: ignore[method-assign]
        link.send = send  # type: ignore[method-assign]
        if runner is not None:
            await link.send(ThreatControlCmd(hold=False))
            runner.cancel()
    for name, outcome in results.items():
        log.info("%-24s %s", name, outcome)
    for question, state in dict.fromkeys(asked):
        log.info("asked %s: %s", question, state)
    if doing:
        log.info("doing: %s", ", ".join(f"{what} {t - doing[0][0]:.1f} s" for t, what in doing))
    return results
