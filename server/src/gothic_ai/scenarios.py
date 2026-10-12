"""Scripted checks that drive an NPC without an LLM."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .amends import Amends
from .brain import Decider, NpcPlanner, Outcome
from .brain.reflex import Reflexes
from .budget import Budget
from .chronicle import Fact
from .dialogue import DialogueManager
from .director import PROPOSE, Director
from .game import GameError, GameLink
from .incidents import IncidentDesk
from .llm import Done, Item, LLMBackend, LLMEvent, Message, Tool, ToolCall, ToolChoice, ToolResult
from .llm.fake import FakeBackend, FakeStep
from .npc_tools import GESTURES as NPC_GESTURES
from .persona import PersonaProvider, habits_of
from .protocol import (
    ActionResult,
    Detached,
    DialogChosen,
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
from .quests import SEE_CM, QuestBoard
from .social import TICK_S, Rumors
from .story import StoryPatches
from .talk import TalkDesk
from .trace import TraceWriter
from .trust import Trust
from .vanilla import CHECK_CM, VanillaQuests
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
    dialogue = DialogueManager(link, llm, PersonaProvider(None))
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
        voice.start(focus=npc)  # the hero looks at him: talk.py gives him the phrase
        await asyncio.sleep(getattr(voice.source, "duration", 0.3) + 0.2)  # a wav is "spoken" in real time
        voice.stop(recognize=True)
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


ORIGINAL_LINE = "DIA_XARDAS_TODO_14_01"  # Xardas: "На этот раз самим нам не справиться..."


async def graph_dialogs(
    link: GameLink, story: StoryPatches, npc: str = "NONE_100_XARDAS", slot: int = 8
) -> dict[str, str]:
    """Stage 3 check (docs/story-graph.md): the game asks the story graph which dialogs an npc offers.

    First every npc of the world builds its menu and each script condition is compared with its lifted
    form (stage 2, live). Without changes the menu is the scripts' own. A dialog can be switched off and
    on, and get a condition; the director adds a dialog with a line of its own, an original voiced line
    and an effect, and the hero chooses it. A change made before a save comes back after loading it, one
    made after it does not.
    """
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: str = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)  # the session's patches are in the game

    live = await link.check_conditions()
    check("live_check", live.get("computed", 0) > 0 and live.get("differ") == 0, str(live))
    base = await link.offered_dialogs(npc)
    check("vanilla", bool(base), " ".join(base))
    if len(base) < 2:
        return results
    first, second = base[0], base[1]

    await story.apply({"op": "disable", "node": first})
    check("disable", first not in await link.offered_dialogs(npc), first)
    await story.apply({"op": "enable", "node": first})
    check("enable", first in await link.offered_dialogs(npc), first)

    never = ["==", ["int", 1], ["int", 0]]
    await story.apply({"op": "condition", "node": first, "expr": never})
    check("condition", first not in await link.offered_dialogs(npc), first)
    await story.apply({"op": "condition", "node": first, "expr": None})
    check("condition_dropped", first in await link.offered_dialogs(npc), first)
    try:
        await story.apply({"op": "condition", "node": first, "expr": ["var", "NO_SUCH_VARIABLE"]})
        check("bad_condition", False, "taken")
    except GameError as err:
        check("bad_condition", True, str(err))

    node = {
        "id": "DIR_M5_TEST",
        "npc": npc,
        "description": "Проверка режиссёра",
        "nr": 50,
        "condition": ["!", ["call", "NPC_ISDEAD", ["inst", "SELF"]]],
        "lines": [
            {"speaker": "other", "text": "Это проверка графа сюжета."},
            {"speaker": "self", "line": ORIGINAL_LINE},
        ],
        "effects": [{"do": "xp", "amount": 10}],
    }
    await story.apply({"op": "add_node", "node": node})
    check("add_node", "DIR_M5_TEST" in await link.offered_dialogs(npc))
    exp = ["field", ["inst", "HERO"], "EXP"]
    before = await link.evaluate(exp)
    chosen = asyncio.ensure_future(wait_message(link, DialogChosen, lambda m: m.node == "DIR_M5_TEST", 10))
    await link.choose_dialog(npc, "DIR_M5_TEST")
    await chosen
    after = await link.evaluate(exp)
    check("effects", after == before + 10, f"exp {before} -> {after}")
    check("told", "DIR_M5_TEST" not in await link.offered_dialogs(npc))

    # an original voiced line through `say`, as the director will make an npc use its own voice
    await attach_when_free(link, npc)
    started = asyncio.ensure_future(wait_message(link, LineStarted, lambda m: m.line == ORIGINAL_LINE, 15))
    await link.send(SayCmd(npc=npc, line=ORIGINAL_LINE, text=""))
    try:
        check("original_line", (await started).voiced, ORIGINAL_LINE)
    except TimeoutError:
        check("original_line", False, "did not start")
    await link.act(npc, "return_to_routine")

    await story.apply({"op": "disable", "node": second})
    await asyncio.sleep(0.5)  # the game has the memory head of that change
    saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
    await link.debug("save", slot)
    await saving
    await story.apply({"op": "disable", "node": first})  # after the save: gone with the load
    loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
    await link.debug("load", slot)
    await loaded
    await link.wait_world()
    await asyncio.sleep(1)
    offered = await link.offered_dialogs(npc)
    check(
        "after_load",
        second not in offered and first in offered and "DIR_M5_TEST" not in offered,
        " ".join(offered),
    )

    for name, outcome in results.items():
        log.info("%-18s %s", name, outcome)
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
    """A stand-in for the behavioral model: the same choice for every question that offers it, and a fixed
    level for score questions (escalate, attitude)."""

    def __init__(self, choices: dict[str, str], scores: dict[str, float] | None = None) -> None:
        self.choices = choices
        self.scores = scores or {}

    async def decide(self, rid, lane, priority, deadline_ms, state, questions) -> Outcome:  # noqa: ANN001
        answers: dict[str, dict] = {}
        for name, q in questions.items():
            if q["type"] == "score":
                answers[name] = {"type": "score", "score": self.scores.get(name, 2.0), "confidence": 0.8}
                continue
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
    dialogue = DialogueManager(link, llm, PersonaProvider(None), trace=trace, budget=budget)
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
    dialogue = DialogueManager(link, llm, PersonaProvider(None))
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


async def _until(check: Callable[[], object], timeout: float) -> bool:
    """Poll `check` until it is true or `timeout` seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        await asyncio.sleep(0.1)
    return bool(check())


async def m5e_talk(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    voice: VoiceInput | None,
    npc: str = "NONE_100_XARDAS",
    witness: str = "VLK_408_JORA",
) -> dict[str, str]:
    """M5e on the game: the hero talks without a window. Lines go in as the player makes them (the talk
    key and the input line, through the bridge's own paths, with the hero's own focus); the server picks
    the addressee, the npc's body follows the behavioral model (a fixed stand-in here), the hero's doings,
    an interruption and a witness go into memory, the conversation ends when the hero walks away, and a
    walking npc answers on the move. The conversational model is scripted."""
    await link.wait_world()
    results: dict[str, str] = {}
    started: list[tuple[float, LineStarted]] = []
    link.listeners.append(
        lambda m: started.append((time.monotonic(), m)) if isinstance(m, LineStarted) else None
    )
    stand_in = FixedDecider({"posture": "listen"})
    talk.reflexes = Reflexes(stand_in)
    talk.end_silence_s = 3.0

    def lines_of(who: str, since: float = 0.0) -> list[LineStarted]:
        return [m for t, m in started if m.npc.upper() == who and t >= since]

    def notes(who: str) -> list[str]:
        conv = dialogue.conversations.get(who)
        return [m.content for m in conv.history if m.role == "developer"] if conv else []

    def heard() -> str:
        if talk.last is None:
            return "nothing heard"
        to, text = talk.last
        return f"{text!r} -> {to.id + ' by ' + to.why if to else 'nobody'}"

    if not await _until(lambda: npc in talk.people, 20):
        return {"roster": f"failed ({npc} is not around the hero)"}

    # 1. a typed line: the bridge adds the hero's focus, the server picks whom it is said to
    dialogue.llm = FakeBackend("Ты снова здесь. Говори, что нужно.", delay=0)
    t0 = time.monotonic()
    await link.debug("type", text="Что мне делать дальше?")
    ok = await _until(lambda: talk.partner is not None and bool(lines_of(npc, t0)), 20)
    results["typed_line"] = (
        f"{'done' if ok and talk.partner and talk.partner.npc == npc else 'failed'} "
        f"({heard()}; body: {talk.partner.posture if talk.partner else '-'}, held {talk.held.get(npc)})"
    )

    # 2. the talk key held: the voice (a wav through the ASR service) names the npc
    if voice is not None:
        await _until(lambda: not dialogue.speaking(npc), 10)  # else the key would cut it off (step 4)
        dialogue.llm = FakeBackend("Я слышу тебя.", delay=0)
        t0 = time.monotonic()
        await link.debug("ptt", state="start")
        await asyncio.sleep(getattr(voice.source, "duration", 1.0) + 0.3)  # a wav is spoken in real time
        await link.debug("ptt", state="stop")
        ok = await _until(lambda: bool(lines_of(npc, t0)) and talk.last is not None, 30)
        results["voice"] = f"{'done' if ok else 'failed'} ({heard()})"

    # 3. the hero draws his weapon mid-conversation
    await link.debug("player_weapon", state="melee")
    ok = await _until(lambda: any("достал оружие" in n for n in notes(npc)), 8)
    for _ in range(10):  # not while the drawing animation plays
        await asyncio.sleep(0.5)
        try:
            await link.debug("player_weapon", state="none")
            break
        except GameError:
            pass
    results["weapon_mid_talk"] = f"{'done' if ok else 'failed'} ({notes(npc)[-1:]})"

    # 4. the hero interrupts a long answer
    story = "Слушай внимательно. " + "Драконы пришли в долину рудников, и их ведёт сила Белиара. " * 5
    await _until(lambda: not dialogue.speaking(npc), 10)
    dialogue.llm = FakeBackend(story, delay=0.03)
    t0 = time.monotonic()
    await link.debug("type", text="Расскажи всё о драконах")
    await _until(lambda: bool(lines_of(npc, t0)), 20)
    await asyncio.sleep(0.5)
    await link.debug("ptt", state="start")  # he starts to speak
    t_cut = time.monotonic()
    await asyncio.sleep(0.5)
    await link.debug("ptt", state="cancel")
    ok = await _until(lambda: any("перебил" in n for n in notes(npc)), 8)
    await asyncio.sleep(3.0)
    after = len(lines_of(npc, t_cut + 0.3))
    conv = dialogue.conversations[npc]
    said = next((m.content for m in reversed(conv.history) if m.role == "assistant"), "")
    results["interrupt"] = (
        f"{'done' if ok and after == 0 and len(said) < len(story) else 'failed'} "
        f"(said {len(said)} of {len(story)} chars, {after} line(s) after the cut)"
    )

    # 5. a witness hears what the hero says to the npc
    await link.debug("spawn", instance=witness, distance=300)
    near = await _until(lambda: witness in talk.people and talk.people[witness].distance <= 1000, 10)
    dialogue.llm = FakeBackend("Не знаю его.", delay=0)
    await link.debug("type", text="Ксардас, кто этот человек?")
    ok = await _until(lambda: any("слышал" in n for n in notes(witness)), 10)
    results["witness"] = f"{'done' if near and ok else 'failed'} ({notes(witness)[-1:] or heard()})"

    # 6. the hero walks away: after a silence the conversation is over and the npc goes back to its routine
    await _until(lambda: not dialogue.speaking(npc), 10)
    await link.debug("player_goto", near=npc, distance=1500)
    away = await _until(lambda: npc in talk.people and talk.people[npc].distance > 1000, 5)
    dist = talk.people[npc].distance / 100 if npc in talk.people else -1
    ok = await _until(lambda: talk.partner is None and npc not in talk.held, 15)
    results["walk_away"] = (
        f"{'done' if away and ok else 'failed'} ({dist:.0f} m away; held {talk.held.get(npc)}; "
        f"{notes(npc)[-1:]})"
    )

    # 7. a walking npc answers on the move, without stopping
    stand_in.choices["posture"] = "keep_working"
    await link.npc_act(npc, "go_to_npc")  # walks to the hero
    moving = await _until(lambda: npc in talk.people and talk.people[npc].moving, 10)
    dialogue.llm = FakeBackend("Иду я, иду.", delay=0)
    t0 = time.monotonic()
    await link.debug("type", text="Ксардас, ты куда?")
    ok = await _until(lambda: bool(lines_of(npc, t0)), 20)
    still = npc in talk.people and talk.people[npc].moving
    results["answer_on_the_move"] = (
        f"{'done' if moving and ok and still and npc not in talk.held else 'failed'} "
        f"(walking {moving}, still walking at its line {still}, body: "
        f"{talk.partner.posture if talk.partner else '-'})"
    )

    for name, outcome in results.items():
        log.info("%-24s %s", name, outcome)
    return results


async def lie_found_out(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    quests: QuestBoard,
    npc: str = "NONE_100_XARDAS",
    bystander: str = "VLK_408_JORA",
) -> dict[str, str]:
    """Stage 4 of the story graph on the game: lied to, paid, found out, a fight and a rumor.

    Xardas gives a quest: two sheep, 30 m away, are to be killed; the reward is gold he has. The hero says
    they are dead (they are not) and Xardas pays (the conversational model is scripted to believe him).
    Then one of the sheep turns up next to him: he finds the lie out, turns hostile, attacks the hero
    through the scripts, and Jora, standing by, hears about it and has it in mind when the hero speaks to
    her."""
    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    def notes(who: str) -> list[str]:
        conv = dialogue.conversations.get(who)
        return [m.content for m in conv.history if m.role == "developer"] if conv else []

    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    if not await _until(lambda: npc in talk.people, 20):
        return _logged({"roster": f"failed ({npc} is not around the hero)"})
    name = talk.people[npc].name

    # the reward: gold he has (he gets some first; he carries next to nothing)
    await link.attach(npc)
    await link.act(npc, "debug_add_item", item="ITMI_GOLD", count=50)
    await link.detach(npc)
    snap = await link.snapshot(npc, fresh=True)
    gold = next((i for i in snap.npc.inventory if i.item == "ITMI_GOLD" and not i.equipped), None)
    if gold is None:
        return _logged({"reward": f"failed ({name} has no gold: {snap.npc.inventory})"})
    q = await quests.give(
        npc, name, "овцы бродят у башни и мешают мне, перебей их", "SHEEP", 2, "ITMI_GOLD", gold.name, 5
    )
    ok = await _until(lambda: quests.chronicle.tags.get(q.tag) is not None, 5)
    tag = quests.chronicle.tags.get(q.tag)
    check("quest", ok and q.count == 2 and tag is not None and tag.alive == 2, f"{q.id}, {q.reward()}, {tag}")

    # the lie: the hero says it is done, the giver believes him and pays
    hero_gold = ["call", "NPC_HASITEMS", ["inst", "HERO"], ["sym", "ITMI_GOLD"]]
    before = await link.evaluate(hero_gold)
    dialogue.llm = FakeBackend(
        script=[FakeStep("Хм. Ладно, держи.", [ToolCall("pay_reward", {"quest": q.id}, "c1")])], delay=0
    )
    await link.debug("type", text=f"{name}, я перебил твоих овец. Где награда?")
    ok = await _until(lambda: bool(quests.chronicle.facts("lie")), 40)
    after = await link.evaluate(hero_gold)
    lies = quests.chronicle.facts("lie")
    check("paid_for_a_lie", ok and after > before, f"gold {before} -> {after}; {lies}")

    # someone standing by, held in place
    await link.debug("spawn", instance=bystander, near=npc, distance=400)
    if not await _until(lambda: bystander in talk.people, 10):
        return _logged({**results, "bystander": f"failed ({bystander} did not come)"})
    await link.attach(bystander)
    other = talk.people[bystander].name

    # found out: a living sheep turns up next to the giver
    await _until(lambda: not dialogue.speaking(npc), 10)
    await link.story_spawn("SHEEP", q.tag, 1, near=npc, distance=300)
    ok = await _until(lambda: q.found_out and bool(quests.chronicle.facts("rumor")), 10)
    check("found_out", ok, notes(npc)[-1:])
    intent = dialogue.conversations[npc].intent if npc in dialogue.conversations else None
    check("hostile", intent is not None and intent.attitude == "hostile", intent)
    attacking = ["call", "NPC_ISINSTATE", ["inst", "SELF"], ["sym", "ZS_ATTACK"]]
    ok = False
    for _ in range(20):  # the scripts take the attack over within a few frames
        if await link.evaluate(attacking, npc):
            ok = True
            break
        await asyncio.sleep(0.25)
    check("attack", ok)
    check("rumor", any("Слух" in n for n in notes(bystander)), notes(bystander)[-1:])

    # another npc in a conversation knows: the rumor is in front of its model
    dialogue.llm = llm = FakeBackend("Говорят, ты обманываешь людей.", delay=0)
    await link.debug("type", text=f"{other}, что обо мне говорят?")
    ok = await _until(lambda: bool(llm.calls), 20)
    seen = ok and any("Слух" in str(getattr(m, "content", "")) for m in llm.calls[0][1])
    to = talk.last[0].id if talk.last and talk.last[0] else "-"
    check("knows_in_talk", seen, f"{len(llm.calls)} call(s) to {to}")
    await link.detach(bystander)
    return _logged(results)


LOBART, HILDA = "BAU_950_LOBART", "BAU_951_HILDA"
RUEBEN, RUEBEN_TO_HILDA = "MIS_LOBART_RUEBEN", "MIS_LOBART_RUEBENTOHILDA"


class RepairScript:
    """The cloud model of the director, scripted: first a successor that is dead (the giver himself), then,
    told why not, Hilda, with the effects the brief offers for experience, gold and the turnips."""

    name = "repair-script"

    def __init__(self, giver: str, successor: str) -> None:
        self.giver = giver
        self.successor = successor
        self.calls: list[str] = []

    async def aclose(self) -> None:
        pass

    async def stream(
        self,
        *,
        instructions: str,
        messages: Sequence[Item],
        tools: list[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[LLMEvent]:
        brief = next(m.content for m in messages if isinstance(m, Message))
        self.calls.append(brief)
        ending = re.search(r"^1\. (\S+)", brief, re.M)
        wanted = ("опыт +", "герою: ITMI_GOLD", "забрать у героя ITPL_BEET")
        effects = [
            m.group(1)
            for m in re.finditer(r"^\s+(E\d+): (.*)$", brief, re.M)
            if m.group(2).startswith(wanted)
        ]
        args = {
            "kind": "handover",
            "ending": ending.group(1) if ending else "",
            "npc": self.giver if len(self.calls) == 1 else self.successor,
            "description": "Лобарт мёртв. Я собрал репу.",
            "lines": [
                {"speaker": "hero", "text": "Лобарт мёртв. Я собрал репу, как он велел."},
                {
                    "speaker": "npc",
                    "text": "Знаю. Давай её сюда. Работников кормить надо, а плату получишь, как он обещал.",
                },
            ],
            "effects": effects,
            "status": "obsolete",
            "journal_now": "Лобарт мёртв. Репу, наверное, теперь примет его жена Хильда.",
            "journal_done": "Я отдал репу Хильде, она заплатила за мужа.",
            "reason": "Хильда ведёт хозяйство вместе с мужем",
        }
        call = ToolCall(PROPOSE, args, f"r{len(self.calls)}")
        yield call
        yield Done("", (call,))


async def repair_after_death(
    link: GameLink, director: Director, story: StoryPatches, giver: str = LOBART, successor: str = HILDA
) -> dict[str, str]:
    """Stage 5 of the story graph on the game: a quest giver dies, the director repairs the quest, the hero
    finishes it.

    Lobart gives the hero the turnip field (the effects of his own dialog: the quest variable and the
    journal). He dies. The director finds the quest without an ending and asks its model for a repair
    (scripted unless the server runs a real one, LLM=chatgpt): first the dead Lobart himself, which the
    validator rejects, then his wife Hilda. Her dialog appears once the hero has 20 turnips; he chooses it,
    the quest ends with Lobart's reward, and the dialog is not offered again."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    script = None
    if isinstance(director.model.llm, FakeBackend):
        director.model.llm = script = RepairScript(giver, successor)
    facts = director.chronicle.facts
    hero = ["inst", "HERO"]

    async def var(name: str) -> int | None:
        return (await link.story_state(vars=[name])).get("vars", {}).get(name)

    async def has(item: str) -> int:
        return int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", item]]) or 0)

    # the quest, as Lobart gives it (his dialog's choice "Хорошо...": the variable and the journal)
    taken = [
        {"do": "set", "var": RUEBEN, "value": 1},
        {
            "do": "journal",
            "topic": "Убрать поле репы",
            "text": "Фермер Лобарт хочет, чтобы я собрал репу на поле. За это он заплатит мне золотом "
            "или продаст мне одежду по значительно сниженной цене.",
        },
    ]
    await link.story_effect(giver, taken)
    before = await director.story()
    check("quest_taken", await var(RUEBEN) == 1 and before.state.alive(giver), before.endings(RUEBEN))
    check("has_an_ending", RUEBEN not in before.dead_ends())

    # Lobart dies
    await link.debug("kill", npc=giver)
    ok = await _until(lambda: any(f.get("npc") == giver for f in facts("died")), 10)
    check("died", ok)
    ok = await _until(
        lambda: any(f.get("quest") == RUEBEN for f in facts("repair") + facts("unrepaired")), 60
    )
    dead_end = [f for f in facts("dead_end") if f.get("quest") == RUEBEN]
    check("dead_end", bool(dead_end), dead_end[-1:])
    rejected = [f for f in facts("repair_rejected") if f.get("quest") == RUEBEN]
    if script is not None:
        check("rejected", any("мёртв" in why for f in rejected for why in f["why"]), rejected[-1:])
    repair = next((f for f in facts("repair") if f.get("quest") == RUEBEN), None)
    check(
        "repaired",
        ok and repair is not None and (script is None or repair.get("npc") == successor),
        repair or facts("unrepaired"),
    )
    if repair is None or repair.get("kind") != "handover":
        return _logged(results)
    node, successor = repair["node"], repair["npc"]
    # what the repair gives: Lobart's reward when scripted, what the model chose when not
    made = next(op["node"] for op in story.ops() if op.get("op") == "add_node" and op["node"]["id"] == node)
    give_gold = sum(e["count"] for e in made["effects"] if e["do"] == "give" and e["item"] == "ITMI_GOLD")
    give_exp = sum(e["amount"] for e in made["effects"] if e["do"] == "xp")
    takes = any(e["do"] == "give" and e.get("to") == "self" for e in made["effects"])
    if script is not None:
        check("lobarts_reward", (give_gold, give_exp, takes) == (5, 50, True), made["effects"])

    # the hero brings the turnips (Hilda's dialog wants 20)
    offered = await link.offered_dialogs(successor)
    await link.attach(successor)
    await link.act(successor, "debug_add_item", item="ITPL_BEET", count=20)
    await link.detach(successor)
    await link.story_effect(successor, [{"do": "give", "item": "ITPL_BEET", "count": 20}])
    gold, exp = await has("ITMI_GOLD"), await link.evaluate(["field", hero, "EXP"])
    now = await link.offered_dialogs(successor)
    check(
        "offered", node not in offered and node in now and await has("ITPL_BEET") >= 20, f"{offered} -> {now}"
    )

    # he talks to her and chooses it
    await link.debug("player_goto", to=successor, distance=300)
    for _ in range(40):  # her own greeting may start as he comes in
        if not (await link.story_state()).get("dialog"):
            break
        await asyncio.sleep(0.5)
    await link.choose_dialog(successor, node)
    ok = await _until_async(lambda: var(RUEBEN), lambda v: v == 2, 15)
    check("quest_done", ok, await var(RUEBEN))
    after = (await has("ITMI_GOLD"), await link.evaluate(["field", hero, "EXP"]), await has("ITPL_BEET"))
    check(
        "reward",
        after[0] >= gold + give_gold and after[1] >= exp + give_exp and (after[2] == 0 or not takes),
        f"gold, exp, turnips {(gold, exp)} -> {after}",
    )
    await asyncio.sleep(1)
    check("once", node not in await link.offered_dialogs(successor))
    check("kept", any(op.get("op") == "add_node" for op in story.ops()))
    return _logged(results)


async def vanilla_lie(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    vanilla: VanillaQuests,
    giver: str = LOBART,
    bystander: str = "VLK_408_JORA",
) -> dict[str, str]:
    """Stage 5 on the game: a lie about a quest of the scripts. Lobart's turnip field: the hero, with no
    turnips, tells Lobart they are in the barn; Lobart's model (scripted unless LLM=chatgpt) believes him and
    the code plays Lobart's own ending: the quest is done, the hero gets the gold and the experience, the next
    step (bring them to Hilda) starts, and the lie is written down with the truth. The same day nothing
    happens; the next morning Lobart misses the turnips, turns against the hero and refuses to talk, and Jora,
    standing by, hears it."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    facts = vanilla.chronicle.facts
    hero = ["inst", "HERO"]

    async def var(name: str) -> int | None:
        return (await link.story_state(vars=[name])).get("vars", {}).get(name)

    # the quest, as Lobart gives it, and the hero next to him (no turnips)
    await link.story_effect(giver, [{"do": "set", "var": RUEBEN, "value": 1}])
    await link.debug("player_goto", to=giver, distance=250)
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    if not await _until(lambda: giver in talk.people, 20):
        return _logged({"roster": f"failed ({giver} is not around the hero)"})
    name = talk.people[giver].name
    gold = await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]])
    exp = await link.evaluate(["field", hero, "EXP"])

    # the lie
    llm = None
    if isinstance(dialogue.llm, FakeBackend):
        accept = [ToolCall("accept_quest", {"quest": RUEBEN}, "c1")]
        dialogue.llm = llm = FakeBackend(
            script=[FakeStep("В амбаре, говоришь? Ладно, держи плату.", accept)], delay=0
        )
    await link.debug(
        "type", text=f"{name}, я собрал всю репу, двадцать штук, и сложил в амбаре. Давай плату."
    )
    ok = await _until(lambda: any(f.get("quest") == RUEBEN for f in facts("claim")), 60)
    lie = next((f for f in facts("lie") if f.get("quest") == RUEBEN), None)
    check("believed", ok, facts("claim")[-1:])
    if llm is not None:
        seen = bool(llm.calls) and "Ты ждёшь от героя" in str(llm.calls[0][1])
        check("quest_in_situation", seen and "accept_quest" in [t.name for t in llm.calls[0][2]])
    check("lie", lie is not None and lie.get("truth") == "ITPL_BEET 0 of 20", lie)
    if lie is None:
        return _logged(results)
    await asyncio.sleep(1)
    after = (
        await var(RUEBEN),
        await var(RUEBEN_TO_HILDA),
        await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]]),
        await link.evaluate(["field", hero, "EXP"]),
    )
    check("ending_played", after[:2] == (2, 1) and after[2] >= gold + 5 and after[3] >= exp + 50, after)

    # the same day: nothing
    await asyncio.sleep(7)
    check("not_yet", not any(f.get("quest") == RUEBEN for f in facts("found_out")))

    # the next morning, Jora by Lobart
    await link.debug("time", hour=7, minute=0)
    await link.debug("spawn", instance=bystander, near=giver, distance=400)
    await asyncio.sleep(1)
    await link.attach(bystander)  # held there
    ok = await _until(lambda: any(f.get("quest") == RUEBEN for f in facts("rumor")), 20)
    found = next((f for f in facts("found_out") if f.get("quest") == RUEBEN), None)
    check("found_out", found is not None and "наутро" in found.get("how", ""), found)
    intent = dialogue.conversations[giver].intent if giver in dialogue.conversations else None
    check("hostile", intent is not None and intent.attitude == "hostile", intent)
    refuses = await link.evaluate(["call", "NPC_REFUSETALK", ["inst", "SELF"]], giver)
    check("refuses_talk", bool(refuses), refuses)
    check("rumor", ok, [f for f in facts("rumor") if f.get("quest") == RUEBEN][-1:])
    await link.detach(bystander)
    return _logged(results)


BUGS = "MIS_LOBARTKILLBUGS"
# Lobart's field raiders where Andre's dialog puts them (DIA_Andre_HilfBauerLobart)
FIELD = [
    "NW_FARM1_FIELD_06",
    "NW_FARM1_FIELD_06",
    "NW_FARM1_FIELD_05",
    "NW_FARM1_FIELD_05",
    "NW_FARM1_FIELD_04",
    "NW_FARM1_FIELD_04",
    "NW_FARM1_FIELD_03",
]


class ClaimScript:
    """A giver's conversational model, scripted by what it hears: the step for a phrase in the hero's words
    or the cue (`said`), or in the outcome of its last tool call (`told`); else a plain reply."""

    name = "claim-script"

    def __init__(
        self,
        said: list[tuple[str, FakeStep]],
        told: list[tuple[str, FakeStep]],
        default: str = "Ну?",
        only: str | None = None,
        others: LLMBackend | None = None,
    ) -> None:
        """`only`: the scripted npc's name; the other npc's turns go to `others` (the live model)."""
        self.said, self.told, self.default = said, told, default
        self.only, self.others = only, others
        self.calls: list[tuple[str, list[Item], list[Tool]]] = []
        self.outputs: list[str] = []  # what came of its tool calls

    async def aclose(self) -> None:
        pass

    async def stream(
        self,
        *,
        instructions: str,
        messages: Sequence[Item],
        tools: list[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[LLMEvent]:
        if self.others is not None and self.only is not None and f"по имени {self.only}" not in instructions:
            async for event in self.others.stream(
                instructions=instructions, messages=messages, tools=tools, tool_choice=tool_choice
            ):
                yield event
            return
        self.calls.append((instructions, list(messages), list(tools or [])))
        last = messages[-1] if messages else None
        if isinstance(last, ToolResult):
            self.outputs.append(last.output)
            rules, text = self.told, last.output
        else:
            rules, text = self.said, last.content if isinstance(last, Message) else ""
        step = next((s for k, s in rules if k in text), FakeStep(self.default))
        fake = FakeBackend(script=[step], delay=0)
        async for event in fake.stream(
            instructions=instructions, messages=messages, tools=tools, tool_choice=tool_choice
        ):
            yield event


async def vanilla_check(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, vanilla: VanillaQuests, giver: str = LOBART
) -> dict[str, str]:
    """Lies as a mechanic the player can read (stage 5), on the game: Lobart's field raiders, where Andre's
    dialog puts them, and his turnips. The model is scripted unless LLM=chatgpt.
    1. The hero says he killed the raiders. Lobart can't see the field from where he stands, but it is a short
       walk: he goes to see (check_claim), sees them alive and catches the lie — no reward, he is wary of the
       hero, the people around hear it.
    2. The hero says the turnips are in the barn: Lobart takes his word no more (the code refuses it).
    3. The raiders are killed and the hero says so: Lobart goes to see again, sees them dead and pays; the
       scripts' ending plays (gold, the quest, the farmhands back to work)."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    facts = vanilla.chronicle.facts
    hero = ["inst", "HERO"]
    bugs = [f"LOBARTS_GIANT_BUG{i + 1}" for i in range(len(FIELD))]

    async def var(name: str) -> int | None:
        return (await link.story_state(vars=[name])).get("vars", {}).get(name)

    async def gold() -> int:
        return int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]]) or 0)

    async def say(text: str) -> None:
        """The hero, next to Lobart, says it; until Lobart's turn about it is over."""
        await link.debug("player_goto", to=giver, distance=250)
        await _until(lambda: giver in talk.people and talk.people[giver].distance < 600, 10)
        conv = dialogue.conversations.get(giver)
        before = conv.reply if conv else None
        await link.debug("type", text=text)

        def answered() -> bool:
            c = dialogue.conversations.get(giver)
            return c is not None and c.reply is not None and c.reply is not before and c.reply.done()

        await _until(answered, 60)

    async def settled() -> None:
        """Lobart is back from seeing for himself and has said his say."""
        await _until(lambda: giver not in vanilla.checking, 90)
        await asyncio.sleep(1)
        conv = dialogue.conversations.get(giver)
        await _until(lambda: conv is None or conv.reply is None or conv.reply.done(), 30)

    # the quests, as Andre's and Lobart's dialogs start them, and the raiders on his field
    await link.story_effect(
        giver, [{"do": "set", "var": BUGS, "value": 1}, {"do": "set", "var": RUEBEN, "value": 1}]
    )
    for bug, wp in zip(bugs, FIELD, strict=True):
        await link.story_spawn(bug, "LOBART_BUGS", 1, waypoint=wp)
    await link.debug("player_goto", to=giver, distance=250)
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    if not await _until(lambda: giver in talk.people, 20):
        return _logged({"roster": f"failed ({giver} is not around the hero)"})
    name = talk.people[giver].name
    field = (await link.story_state(npcs=bugs, near=giver)).get("npcs", {})
    ds = sorted(round(p.get("distance", 0) / 100) for p in field.values() if "distance" in p)
    check("field", bool(ds) and SEE_CM / 100 < ds[0] <= CHECK_CM / 100, f"{len(ds)} raiders, {ds} m")
    script = None
    if isinstance(dialogue.llm, FakeBackend):
        script = ClaimScript(
            said=[
                ("правда перебил", FakeStep("Посмотрим.", [ToolCall("accept_quest", {"quest": BUGS})])),
                (
                    "перебил всех",
                    FakeStep("Перебил, говоришь? Сам посмотрю.", [ToolCall("check_claim", {"quest": BUGS})]),
                ),
                ("репа", FakeStep("В амбаре?", [ToolCall("accept_quest", {"quest": RUEBEN})])),
                ("Он тебе солгал", FakeStep("Ах ты брехун! Они живы, все до одного!")),
                ("он сказал правду", FakeStep("И правда. Держи, заслужил.")),
            ],
            told=[
                ("Можешь сходить", FakeStep("Сам посмотрю.", [ToolCall("check_claim", {"quest": BUGS})])),
                ("он уже лгал тебе", FakeStep("Нет уж. Тебе я на слово больше не верю. Неси репу сюда.")),
            ],
        )
        dialogue.llm = script
    gold0 = await gold()

    # 1. the lie: he goes to see for himself and catches it
    await say(f"{name}, я перебил всех полевых хищников на твоём поле. Плати.")
    ok = await _until(lambda: any(f.get("quest") == BUGS for f in facts("caught")), 90)
    got = next((f for f in facts("caught") if f.get("quest") == BUGS), None)
    check("caught", ok and got is not None and "сходил проверить" in got.get("how", ""), got)
    if script is not None:
        tools = [t.name for t in script.calls[0][2]] if script.calls else []
        check("check_offered", "check_claim" in tools, tools)
    check("no_reward", (await var(BUGS), await gold()) == (1, gold0))
    conv = dialogue.conversations.get(giver)
    check("wary", conv is not None and conv.intent.attitude in ("wary", "hostile"), conv and conv.intent)
    await settled()  # back to the hero, he says what he thinks of it

    # 2. a lie he can't check: not on his word any more
    await say(f"{name}, репа уже в амбаре, все двадцать штук. Давай плату.")
    await asyncio.sleep(2)
    check("not_on_word", not any(f.get("quest") == RUEBEN for f in facts("claim")), facts("claim")[-1:])
    if script is not None:
        check("refused", any("он уже лгал тебе" in o for o in script.outputs), script.outputs[-1:])

    # 3. the truth: he goes to see again, and pays
    for bug in bugs:
        await link.debug("kill", npc=bug)
    await say(f"{name}, вот теперь я правда перебил всех хищников. Сходи посмотри сам.")
    ok = await _until(lambda: any(f.get("quest") == BUGS for f in facts("claim")), 90)
    claim = next((f for f in facts("claim") if f.get("quest") == BUGS), None)
    check("paid_after_check", ok and claim is not None and claim.get("truth") and claim.get("checked"), claim)
    await settled()
    check("ending_played", (await var(BUGS), await gold() >= gold0 + 20) == (2, True))
    kept = vanilla.trust.situation(giver) or ""
    check("kept_word", "он уже говорил тебе правду" in kept and "он уже лгал тебе" in kept, kept)
    return _logged(results)


async def rumor_spread(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    rumors: Rumors,
    giver: str = LOBART,
    log_dir: str | None = None,
) -> dict[str, str]:
    """Rumors among acquaintances (stage 5), on the game. Lobart catches the hero lying about the turnips (the
    model scripted unless LLM=chatgpt); the people of the world and their days make who knows whom (Hilda on
    the farm, Andre through the quest that sends the hero to Lobart's field). The clock goes to the next
    morning: the rumor goes round, in the order of the meetings, to people that never stood by Lobart, also
    in the city. The hero, put next to one of them, speaks to it: it has the rumor in mind."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    facts = rumors.chronicle.facts

    # who knows whom, from the routines of the world's people and the quests of the scripts
    if not await _until(lambda: rumors.social is not None, 30):
        return _logged({"social": "failed (no people)"})
    social = rumors.social
    assert social is not None
    if log_dir:
        text = "\n".join(f"{n}: {'; '.join(social.describe(n))}" for n in sorted(social.names))
        await asyncio.to_thread(Path(log_dir, "ties.txt").write_text, text, encoding="utf-8")
    ties = social.ties.get(giver, {})
    check(
        "ties",
        HILDA in ties and bool(ties[HILDA].meets) and "MIL_311_ANDRE" in ties,
        f"{len(social.names)} people; {giver}: {', '.join(sorted(social.names.get(n, n) for n in ties))}",
    )

    # Lobart catches the lie: the turnips in the barn, and the hero has none on him
    await link.story_effect(giver, [{"do": "set", "var": RUEBEN, "value": 1}])
    await link.debug("player_goto", to=giver, distance=250)
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    if not await _until(lambda: giver in talk.people, 20):
        return _logged({**results, "roster": f"failed ({giver} is not around the hero)"})
    name = talk.people[giver].name
    # Lobart is scripted (the lie must be caught); with LLM=chatgpt the others are the live model
    live = None if isinstance(dialogue.llm, FakeBackend) else dialogue.llm
    expose = FakeStep("Врёшь! Нет у тебя никакой репы.", [ToolCall("expose_lie", {"quest": RUEBEN})])
    script = ClaimScript(
        said=[("репа", expose)], told=[], default="Ну, здравствуй. Чего тебе?", only=name, others=live
    )
    dialogue.llm = script
    await link.debug("type", text=f"{name}, репа уже в амбаре, все двадцать штук. Давай плату.")
    ok = await _until(lambda: any(f.get("quest") == RUEBEN for f in facts("caught")), 60)
    check("caught", ok, facts("caught")[-1:])
    if not ok:
        return _logged(results)
    before = {f["to"] for f in facts("rumor")}

    # the next morning: the meetings in between
    await link.debug("time", hour=7, minute=0)
    await _until(lambda: any(int(f.get("hops", 1)) >= 2 for f in facts("rumor")), 30)
    await asyncio.sleep(TICK_S + 1)
    heard = [f for f in facts("rumor") if f.get("quest") == RUEBEN]
    hops = sorted({int(f.get("hops", 1)) for f in heard})
    check(
        "went_round",
        len(heard) > len(before) and max(hops, default=0) >= 2,
        f"{len(heard)} people, hops {hops}",
    )
    knowers = sorted({f["to"] for f in heard} - before)
    where = (await link.story_state(npcs=knowers, near=giver)).get("npcs", {})
    far = sorted(
        (n for n in knowers if (where.get(n) or {}).get("state") == "alive"),
        key=lambda n: -float((where.get(n) or {}).get("distance", 0)),
    )
    farthest = far[0] if far else None
    d = float((where.get(farthest) or {}).get("distance", 0)) / 100 if farthest else 0
    check(
        "far_away",
        farthest is not None and d > 100,
        f"{social.names.get(farthest or '', farthest)}, {d:.0f} m",
    )
    if farthest is None:
        return _logged(results)

    # the hero speaks to it: the rumor is in its mind
    told = next(f for f in heard if f["to"] == farthest)
    line = Trust(rumors.chronicle).situation(farthest) or ""
    check("in_mind", "ты слышал, что он обманул других" in line and "Лобарт" in line, line.splitlines()[-1:])
    await link.debug("player_goto", to=farthest, distance=250)
    if not await _until(lambda: farthest in talk.people, 20):
        return _logged({**results, "roster2": f"failed ({farthest} is not around the hero)"})
    other = talk.people[farthest].name
    calls = len(script.calls)
    await link.debug("type", text=f"{other}, здравствуй. Не одолжишь мне пару монет? Верну завтра.")

    def answered() -> bool:
        conv = dialogue.conversations.get(farthest)
        return conv is not None and conv.reply is not None and conv.reply.done()

    ok = await _until(answered, 60)
    if live is None:
        request = " ".join(str(m) for _, items, _ in script.calls[calls:] for m in items)
        check(
            "in_request",
            ok and "со слов: " in request and "Лобарт" in request,
            f"heard from {told.get('from_name')}",
        )
    else:
        conv = dialogue.conversations.get(farthest)
        said = [
            m.content
            for m in (conv.history if conv else [])
            if isinstance(m, Message) and m.role == "assistant"
        ]
        check("answer", ok and bool(said), f"{other}: {said[-1] if said else ''}")
    return _logged(results)


ANDRE = "MIL_311_ANDRE"


class AmendsScript:
    """The director's model for storylets, scripted: Andre asks for a fine and closes joining the militia (the
    first of his dealings in the brief that has it, else the first); anyone else does not care."""

    name = "amends-script"

    def __init__(self, npc: str = ANDRE, gold: int = 50) -> None:
        self.npc, self.gold = npc, gold
        self.calls: list[str] = []

    async def aclose(self) -> None:
        pass

    async def stream(
        self,
        *,
        instructions: str,
        messages: Sequence[Item],
        tools: list[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[LLMEvent]:
        brief = next(m.content for m in messages if isinstance(m, Message))
        self.calls.append(brief)
        stakes = re.findall(r"^- (\S+): «([^»]*)» — (.*)$", brief, re.M)
        if f"({self.npc})" not in brief.splitlines()[0] or not stakes:
            args: dict[str, Any] = {"kind": "none", "reason": "не его дело"}
        else:
            join = [id for id, _, what in stakes if what == "принимает в гильдию"]
            args = {
                "kind": "amends",
                "close": join[:1] or [stakes[0][0]],
                "lines": [
                    {"speaker": "npc", "text": "Слыхал я, ты Лобарта обмануть пытался."},
                    {
                        "speaker": "npc",
                        "text": f"Лжецу в ополчении не место. {self.gold} золотых штрафа — тогда поговорим.",
                    },
                ],
                "to": "self",
                "gold": self.gold,
                "description": f"Вот штраф — {self.gold} золотых.",
                "amends_lines": [{"speaker": "npc", "text": "Ладно. Считай, забыли. Но смотри у меня."}],
                "title": "Штраф за враньё",
                "journal_now": (
                    f"Андре прослышал, что я соврал Лобарту. Без штрафа ({self.gold} золотых) "
                    "в ополчение не возьмёт."
                ),
                "journal_done": "Я заплатил Андре штраф.",
                "reason": "до Андре дошёл слух, а в ополчение он берёт только честных",
            }
        call = ToolCall(
            "propose_amends",
            {
                **{k: "" for k in ("description", "title", "journal_now", "journal_done")},
                "close": [],
                "lines": [],
                "to": "self",
                "gold": 0,
                "amends_lines": [],
                **args,
            },
            "a1",
        )
        yield call
        yield Done("", (call,))


async def _caught_by(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, giver: str, facts: Callable[..., list[Fact]]
) -> tuple[bool, ClaimScript | None]:
    """The hero tells `giver` the turnips are in the barn and is caught (the giver scripted; with LLM=chatgpt
    the others are the live model)."""
    await link.story_effect(giver, [{"do": "set", "var": RUEBEN, "value": 1}])
    await link.debug("player_goto", to=giver, distance=250)
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    if not await _until(lambda: giver in talk.people, 20):
        return False, None
    name = talk.people[giver].name
    live = None if isinstance(dialogue.llm, FakeBackend) else dialogue.llm
    expose = FakeStep("Врёшь! Нет у тебя никакой репы.", [ToolCall("expose_lie", {"quest": RUEBEN})])
    script = ClaimScript(
        said=[("репа", expose)], told=[], default="Ну, здравствуй. Чего тебе?", only=name, others=live
    )
    dialogue.llm = script
    await link.debug("type", text=f"{name}, репа уже в амбаре, все двадцать штук. Давай плату.")
    return await _until(lambda: any(f.get("quest") == RUEBEN for f in facts("caught")), 60), script


async def amends_storylet(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    amends: Amends,
    giver: str = LOBART,
    fine: int = 50,
) -> dict[str, str]:
    """The director's storylet of amends (stage 5), on the game. Lobart catches the hero lying; by the next
    morning the rumor has gone round. The director's model, scripted unless LLM=chatgpt, has Andre (who has
    dealings with Lobart) ask for a fine and close joining the militia until then; a live model decides who
    asks and what for. The hero comes, hears the npc out, makes amends, and what was closed opens again."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    facts = amends.chronicle.facts
    hero = ["inst", "HERO"]
    if isinstance(amends.model.llm, FakeBackend):
        amends.model.llm = AmendsScript(ANDRE, fine)
    amends.director.pacing.cooldown_s = 2.0  # no need to wait between the steps here

    ok, _ = await _caught_by(link, dialogue, talk, giver, facts)
    check("caught", ok, facts("caught")[-1:])
    if not ok:
        return _logged(results)

    # the next morning the rumor has gone round; someone asks for amends (scripted: Andre)
    await link.debug("time", hour=7, minute=0)
    ok = await _until(lambda: bool(amends.storylets), 180)
    if ok and isinstance(amends.model.llm, AmendsScript):
        ok = await _until(lambda: any(x.npc == ANDRE for x in amends.storylets.values()), 120)
    looked = [
        (f.get("fact"), f.get("npc")) for f in facts() if f.get("fact") in ("amends_open", "amends_none")
    ]
    check("asked", ok, looked)
    if not ok:
        return _logged(results)
    s = next((x for x in amends.storylets.values() if x.npc == ANDRE), next(iter(amends.storylets.values())))
    closed = [op["node"] for op in amends.patches.ops() if op.get("op") == "disable"]
    check("closed", bool(s.close) and set(s.close) <= set(closed), f"{s.name}: {s.close}")

    # the hero comes: the npc starts the talk himself (an important dialog)
    await link.debug("player_goto", to=s.npc, distance=250)
    await _until(lambda: s.npc in talk.people, 20)
    by_himself = await _until(lambda: s.stage != "open", 20)
    if not by_himself:
        try:
            await link.choose_dialog(s.npc, s.talk)
        except GameError as err:
            log.info("amends: %s", err)
    heard = await _until(lambda: s.stage != "open", 20)
    check("talked", heard, f"{s.talk}{', he started it' if by_himself else ''}")

    # he makes amends: what was closed is open again
    gold = int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]]) or 0)
    if gold < s.gold:
        await link.story_effect(s.npc, [{"do": "give", "item": "ITMI_GOLD", "count": s.gold - gold + 10}])
        gold = s.gold + 10
    if s.to != s.npc:
        await link.debug("player_goto", to=s.to, distance=250)
        await _until(lambda: s.to in talk.people, 20)
    await asyncio.sleep(1)
    try:
        await link.choose_dialog(s.to, s.pay)
        paid = await _until(lambda: s.stage == "done", 20)
    except GameError as err:
        paid = False
        log.info("amends: %s", err)
    await asyncio.sleep(1)
    left = int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]]) or 0)
    opened = {op["node"] for op in amends.patches.ops() if op.get("op") == "enable"}
    check(
        "paid",
        paid and left == gold - s.gold and set(s.close) <= opened,
        f"{s.description}: {gold} -> {left} gold",
    )
    line = Trust(amends.chronicle).situation(s.npc) or ""
    check("forgiven", "он загладил вину" in line, line.splitlines()[-1:])
    return _logged(results)
    s = next(x for x in amends.storylets.values() if x.npc == ANDRE)
    closed = [op["node"] for op in amends.patches.ops() if op.get("op") == "disable"]
    check("closed", bool(s.close) and set(s.close) <= set(closed), s.close)

    # the hero comes to Andre: Andre starts the talk himself (an important dialog)
    await link.debug("player_goto", to=ANDRE, distance=250)
    await _until(lambda: ANDRE in talk.people, 20)
    by_himself = await _until(lambda: s.stage != "open", 20)
    if not by_himself:
        try:
            await link.choose_dialog(ANDRE, s.talk)
        except GameError as err:
            log.info("amends: %s", err)
    heard = await _until(lambda: s.stage != "open", 20)
    check("talked", heard, f"{s.talk}{', he started it' if by_himself else ''}")

    # he pays the fine: the militia is open to him again
    gold = int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]]) or 0)
    if gold < s.gold:
        await link.story_effect(ANDRE, [{"do": "give", "item": "ITMI_GOLD", "count": s.gold - gold + 10}])
        gold = s.gold + 10
    await asyncio.sleep(1)
    try:
        await link.choose_dialog(s.to, s.pay)
        paid = await _until(lambda: s.stage == "done", 20)
    except GameError as err:
        paid = False
        log.info("amends: %s", err)
    await asyncio.sleep(1)
    left = int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]]) or 0)
    opened = {op["node"] for op in amends.patches.ops() if op.get("op") == "enable"}
    check("paid", paid and left == gold - s.gold and set(s.close) <= opened, f"{gold} -> {left} gold")
    line = Trust(amends.chronicle).situation(ANDRE) or ""
    check("forgiven", "он загладил вину" in line, line.splitlines()[-1:])
    return _logged(results)


async def _until_async(get: Callable[[], Any], ok: Callable[[Any], bool], timeout: float) -> bool:
    """Poll an async getter until its value is ok or `timeout` seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ok(await get()):
            return True
        await asyncio.sleep(0.25)
    return False


def _logged(results: dict[str, str]) -> dict[str, str]:
    for key, outcome in results.items():
        log.info("%-15s %s", key, outcome)
    return results
