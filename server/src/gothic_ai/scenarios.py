"""Scripted checks that drive an NPC without an LLM."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from .brain import Decider, NpcPlanner, Outcome
from .brain.reflex import Reflexes
from .budget import Budget
from .dialogue import DialogueManager
from .game import GameError, GameLink
from .incidents import IncidentDesk
from .llm import ToolCall
from .llm.fake import FakeBackend, FakeStep
from .npc_tools import GESTURES as NPC_GESTURES
from .persona import PersonaProvider
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
    Saved,
    Session,
    Snapshot,
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
    """A stand-in for the behavioral model: the same choice for every question that offers it."""

    def __init__(self, choices: dict[str, str]) -> None:
        self.choices = choices

    async def decide(self, rid, lane, priority, deadline_ms, state, questions) -> Outcome:  # noqa: ANN001
        answers = {}
        for name, q in questions.items():
            pick = self.choices.get(name)
            pick = pick if pick in q["criteria"] else next(iter(q["criteria"]))
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
    await link.debug("spawn", instance="WOLF", near=npc, distance=700)
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
    for name, outcome in results.items():
        log.info("%-24s %s", name, outcome)
    return results
