"""Scripted checks that drive an NPC without an LLM."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Collection, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, cast

from . import characters, programs, questbook, triggers
from .amends import Amends
from .authoring import PROPOSE as PROPOSE_QUEST
from .authoring import Author
from .beasts import BeastDesk
from .bodies import Bodies
from .bodypilot import BodyPilot
from .brain import Decider, NpcPlanner, Outcome
from .brain.reflex import Reflexes
from .budget import Budget
from .cast import TEMPLATES, Cast, Person, PersonTie
from .casting import PROPOSE as PROPOSE_CHARACTER
from .casting import Casting
from .chains import PROPOSE as PROPOSE_CHAIN
from .chains import ChainAuthor
from .chests import BACK, CHEST, OWNED, ChestDesk
from .chronicle import Fact
from .concerns import Concerns
from .contracts import gold_text
from .daylife import DayDesk
from .deals import GOLD, Deals
from .dialogue import DialogueManager, SceneLine, line_duration
from .director import PROPOSE, Director
from .errands import Errands
from .game import GameError, GameLink
from .gamedata.storygraph import Expr
from .gossip import WORDS_CM, GossipDesk, Scene
from .group import ANSWER_CUE, ROUND_GAP_S
from .guide import Guide
from .incidents import IncidentDesk
from .initiative import ARRIVE_CM, CHECKINFO, InitiativeDesk
from .interject import Interjections
from .journal import Journal
from .knowledge import Knowledge, Topic, roots
from .llm import Done, Item, LLMBackend, LLMEvent, Message, Tool, ToolCall, ToolChoice, ToolResult
from .llm.fake import FakeBackend, FakeStep
from .lore import Lore
from .meetings import DAY as GAME_DAY
from .meetings import Meetings, clock
from .npc_tools import GESTURES as NPC_GESTURES
from .perception import BEASTS, PerceptionDesk, Sight, trace_name
from .persona import PersonaProvider, habits_of
from .places import SIDES, PlaceBook
from .protocol import (
    ActionResult,
    Beasts,
    DebugCmd,
    Detached,
    DialogChosen,
    FollowEnded,
    GameMessage,
    LineStarted,
    Loading,
    Look,
    NpcActCmd,
    NpcActResult,
    NpcActType,
    NpcDied,
    NpcEvent,
    NpcIdle,
    NpcLook,
    NpcRoster,
    NpcSpec,
    NpcThing,
    NpcView,
    Perception,
    PerceptionHeld,
    PlayerText,
    RoutineCmd,
    RoutineEntry,
    Saved,
    SayCmd,
    Session,
    Snapshot,
    ThreatControlCmd,
)
from .quests import RUMOR_CM, SEE_CM, QuestBoard
from .sightings import Sighting, Sightings
from .social import DAY, TICK_S, Rumor, Rumors
from .story import StoryPatches
from .storyguard import StoryGuard
from .talk import TalkDesk, fights
from .trace import TraceWriter
from .trace import read as read_trace
from .trust import Trust
from .vanilla import CHECK_CM, VanillaQuests
from .voice import VoiceInput
from .whereabouts import Whereabouts
from .witnesses import Witnesses
from .wrath import TAKE_OFFENSE, TEMPERS, Wrath

log = logging.getLogger(__name__)

GESTURES = ("T_COMEOVERHERE", "T_GREETNOV", "T_YES")
QUICK_LINE_S = (
    1.5  # m5 with the quick model: its line starts this soon (the conversational model takes 3-5 s)
)


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
    # the graph runs the dialogs' functions in place of the VM (docs/story-graph.md, «Исполнение без VM»):
    # every condition of the check ran as a program, none failed, none was left to the VM
    programs = live.get("programs") or {}
    check(
        "programs",
        bool(programs.get("on"))
        and programs.get("runs", 0) > 0
        and programs.get("failed") == 0
        and programs.get("vm") == 0,
        str(programs),
    )
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


async def graph_programs(
    link: GameLink,
    story: StoryPatches,
    npc: str = "NONE_100_XARDAS",
    slot: int = 8,
    dialogue: DialogueManager | None = None,
) -> dict[str, str]:
    """Level 1, step 2 of «Исполнение без VM» (docs/story-graph.md): the director's programs.

    The game gives a dialog's program as JSON and takes it back unchanged. The director's program in place of
    a dialog's information runs when the hero chooses it: a line of its own, gold and experience. One in place
    of a condition hides the dialog; null brings the scripts' program back; a program the game can't run is
    rejected with the reason. A dialog of the director runs a program of its own. A change made before a save
    comes back after loading it.
    """
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: str = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)  # the session's patches are in the game

    base = await link.offered_dialogs(npc)
    check("vanilla", len(base) >= 2, " ".join(base))
    if len(base) < 2:
        return results
    info, cond = base[0], base[1]

    own = await link.story_program(info)
    steps = own.get("steps") or []
    calls = [s for s in programs.steps_of(steps) if s.get("do") == "call"]
    check(
        "read",
        own.get("patched") is False and own.get("returns") is False and bool(calls),
        f"{own.get('fn')}: {len(steps)} steps",
    )
    await story.apply({"op": "program", "node": info, "steps": steps})
    same = await link.story_program(info)
    check("same", same.get("patched") is True and same.get("steps") == steps)

    gold = ["call", "NPC_HASITEMS", ["inst", "HERO"], ["sym", "ITMI_GOLD"]]
    exp = ["field", ["inst", "HERO"], "EXP"]

    async def hero() -> tuple[int, int]:
        return int(await link.evaluate(gold)), int(await link.evaluate(exp))

    def give(count: int) -> dict[str, Any]:
        made = ["call", "CREATEINVITEMS", ["inst", "OTHER"], ["sym", "ITMI_GOLD"], ["int", count]]
        return {"do": "call", "call": made}

    def xp(amount: int) -> dict[str, Any]:
        return {"do": "call", "call": ["fn", "B_GIVEPLAYERXP", ["int", amount]]}

    mine = [{"do": "say", "speaker": "self", "text": "Это говорит программа режиссёра."}, give(7), xp(10)]
    await story.apply({"op": "program", "node": info, "steps": [*mine, {"do": "return"}]})
    before = await hero()
    said = asyncio.ensure_future(
        wait_message(link, LineStarted, lambda m: m.npc == npc and m.line.startswith("AIB_G"), 30)
    )
    chosen = asyncio.ensure_future(wait_message(link, DialogChosen, lambda m: m.node == info, 10))
    await link.choose_dialog(npc, info)
    await chosen
    after = await hero()
    check("replaced", after == (before[0] + 7, before[1] + 10), f"gold, exp {before} -> {after}")
    try:
        own = await said
        check("said", True, own.line)
        # in the npc's voice (v26), when the server has one: it may come a moment after the line starts
        if dialogue is not None and dialogue.tts is not None:
            voiced = await _until(lambda: dialogue.story_voiced(own.line) is not None, 10)
            check("voiced", voiced, f"{dialogue.story_voiced(own.line)} s, voiced at the start: {own.voiced}")
    except TimeoutError:
        check("said", False, "the line of its own did not start")

    never = [{"do": "return", "value": ["int", 0]}]
    await story.apply({"op": "program", "node": cond, "part": "condition", "steps": never})
    check("condition", cond not in await link.offered_dialogs(npc), cond)
    await story.apply({"op": "program", "node": cond, "part": "condition", "steps": None})
    check("condition_back", cond in await link.offered_dialogs(npc), cond)

    await story.apply({"op": "program", "node": info, "steps": None})
    back = await link.story_program(info)
    check("restored", back.get("patched") is False and back.get("steps") == steps)
    try:
        bad = [{"do": "call", "call": ["call", "CREATEINVITEMS", ["inst", "OTHER"]]}]
        await story.apply({"op": "program", "node": info, "steps": bad})
        check("bad_program", False, "taken")
    except GameError as err:
        check("bad_program", "takes 3 arguments" in str(err), str(err))

    # a dialog of the director runs a program of its own: lines, gold, a branch
    node = {
        "id": "DIR_PROGRAM_TEST",
        "npc": npc,
        "description": "Проверка программы режиссёра",
        "nr": 50,
        "program": [
            {"do": "say", "speaker": "other", "text": "Проверка программы."},
            give(3),
            {"do": "if", "cond": [">=", gold, ["int", 3]], "then": [xp(5)], "else": [xp(1000)]},
        ],
    }
    await story.apply({"op": "add_node", "node": node})
    before = await hero()
    chosen = asyncio.ensure_future(wait_message(link, DialogChosen, lambda m: m.node == node["id"], 10))
    await link.choose_dialog(npc, "DIR_PROGRAM_TEST")
    await chosen
    after = await hero()
    check("node_program", after == (before[0] + 3, before[1] + 5), f"gold, exp {before} -> {after}")

    # choices of a dialog of the director (v24): offered after its lines, as the dialog window asks; one
    # that is picked is no longer offered and its steps run; a choice may offer others
    ask = {
        "id": "DIR_CHOICE_TEST",
        "npc": npc,
        "description": "Проверка вариантов",
        "nr": 51,
        "permanent": True,
        "program": [
            {"do": "say", "speaker": "self", "text": "Выбирай."},
            {"do": "choice", "text": "Золото", "steps": [give(2)]},
            {
                "do": "choice",
                "text": "Дальше",
                "steps": [{"do": "clear_choices"}, {"do": "choice", "text": "Опыт", "steps": [xp(3)]}],
            },
        ],
    }
    await story.apply({"op": "add_node", "node": ask})
    chosen = asyncio.ensure_future(wait_message(link, DialogChosen, lambda m: m.node == ask["id"], 10))
    await link.choose_dialog(npc, ask["id"])
    await chosen
    offered = await link.dialog_choices(npc, ask["id"])
    check(
        "choices",
        [(c["id"], c["text"]) for c in offered]
        == [("DIR_CHOICE_TEST.1", "Золото"), ("DIR_CHOICE_TEST.2", "Дальше")],
        str(offered),
    )
    before = await hero()
    await link.choose_dialog(npc, "DIR_CHOICE_TEST.1")
    after = await hero()
    # the window asks with what was picked: the choice, whose dialog offers the rest
    left = [c["text"] for c in await link.dialog_choices(npc, "DIR_CHOICE_TEST.1")]
    check(
        "choice_picked",
        after[0] == before[0] + 2 and left == ["Дальше"],
        f"gold {before[0]} -> {after[0]}, {left}",
    )
    await link.choose_dialog(npc, "DIR_CHOICE_TEST.2")
    deeper = [c["text"] for c in await link.dialog_choices(npc, "DIR_CHOICE_TEST.2")]
    check("choice_offers_more", deeper == ["Опыт"], str(deeper))

    # the same in the dialog window, as the player has it: the dialog, after its line its choices, one that
    # offers another, then the dialogs again
    async def menu(ok: Callable[[list[str]], bool], timeout: float = 30) -> list[str]:
        seen: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            seen = await link.dialog_menu()
            if ok(seen):
                break
            await asyncio.sleep(0.3)
        return seen

    await link.debug("teleport", near=npc, distance=150)
    await link.start_talk(npc)
    window: list[list[str]] = [await menu(lambda m: ask["description"] in m)]
    exp_before = (await hero())[1]
    for pick, then in ((ask["description"], ["Золото", "Дальше"]), ("Дальше", ["Опыт"]), ("Опыт", None)):
        if pick not in window[-1]:
            break
        await link.pick_in_menu(pick)
        # after the last choice the window offers the dialogs again
        window.append(await menu(lambda m, then=then: m == then if then else ask["description"] in m))
    gained = (await hero())[1] - exp_before
    check(
        "window",
        window[1:3] == [["Золото", "Дальше"], ["Опыт"]] and gained == 3 and ask["description"] in window[-1],
        f"{' -> '.join(map(str, window))}; exp +{gained}",
    )
    if window[-1]:
        await link.pick_in_menu(window[-1][-1])  # the last one ends the talk
        await menu(lambda m: not m, 15)

    # a program in place of a condition, made before a save, comes back after loading it
    await story.apply({"op": "program", "node": cond, "part": "condition", "steps": never})
    await asyncio.sleep(0.5)  # the game has the memory head of that change
    saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
    await link.debug("save", slot)
    await saving
    loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
    await link.debug("load", slot)
    await loaded
    await link.wait_world()
    await asyncio.sleep(1)
    offered = await link.offered_dialogs(npc)
    kept = await link.story_program(cond, "condition")
    check("after_load", cond not in offered and kept.get("patched") is True, " ".join(offered))

    live = await link.check_conditions()
    counts = live.get("programs") or {}
    check("counters", counts.get("replaced", 0) >= 1 and counts.get("failed") == 0, str(counts))

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
    """A stand-in for the behavioral model: per question the first of its choices the question offers, and a
    fixed level for score questions (escalate, attitude). With `npcs` it decides so only for those; the others
    keep to what they do: the first option the question offers (continue, keep_routine) and level 0."""

    def __init__(
        self,
        choices: dict[str, str | tuple[str, ...]],
        scores: dict[str, float] | None = None,
        npcs: Collection[str] | None = None,
    ) -> None:
        self.choices = choices
        self.scores = scores or {}
        self.npcs = {n.upper() for n in npcs} if npcs is not None else None

    async def decide(self, rid, lane, priority, deadline_ms, state, questions) -> Outcome:  # noqa: ANN001
        mine = self.npcs is None or rid.split("#", 1)[0].upper() in self.npcs
        answers: dict[str, dict] = {}
        for name, q in questions.items():
            if q["type"] == "score":
                score = self.scores.get(name, 2.0) if mine else 0.0
                answers[name] = {"type": "score", "score": score, "confidence": 0.8}
                continue
            prefs = self.choices.get(name, ()) if mine else ()
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
    quick: LLMBackend | None = None,
) -> dict[str, str]:
    """M5a on the game: the turn loop with checks, intents moving the body, and the two loops on an
    incident (the body reacts before the words). The conversational model is scripted; the behavioral
    model is the live service when given, a fixed stand-in otherwise. The game cannot hit an npc on
    command, so the incident's perception is injected on the server side. With `quick` (the quick model
    of fights, live) the hero's phrases get its first words and the incident's words are its line, said
    at once; the scripted model only acts."""
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
    spoken: list[dict[str, Any]] = []  # the journal's entries: who said a line, how soon
    dialogue = DialogueManager(
        link,
        llm,
        PersonaProvider(None),
        trace=trace,
        budget=budget,
        journal=Journal(spoken.append),
        quick=quick,
    )
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

        if quick is not None and trace is not None:  # the quick model's first words to the hero's phrases
            words = [
                e["text"]
                for r in read_trace(trace.path())
                if r.get("type") == "turn" and r.get("npc") == npc
                for e in r.get("events", [])
                if e.get("kind") == "first_words"
            ]
            results["first_words"] = f"{'done' if words else 'failed'} ({len(words)} of 4: {words})"

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
        if quick is not None:
            line = next((e for e in reversed(spoken) if e["kind"] == "npc"), {})
            ok = line.get("llm") == quick.name and line.get("first_token_s", 99) < QUICK_LINE_S
            results["incident_quick"] = (
                f"{'done' if ok else 'failed'} ({line.get('llm')}, first token {line.get('first_token_s')} s)"
            )
    finally:
        await link.act(npc, "return_to_routine")
    for name, outcome in results.items():
        log.info("%-24s %s", name, outcome)
    return results


async def _m5b_player_weapon(link: GameLink, state: Literal["none", "melee"], *, timeout: float = 5) -> None:
    """Wait out a saved or newly started weapon transition, with one deadline for all retries."""
    try:
        async with asyncio.timeout(timeout):
            await link.body_watch(["PLAYER"], rate_hz=10)
            while True:
                # Weapon mode changes inside the clip: an already matching mode can report
                # success before the transition ends. Wait for the animation gate as well.
                if any(row.self.interruptible for row in link.body_latest.values()):
                    try:
                        await link.debug("player_weapon", state=state)
                        return
                    except GameError as err:
                        if str(err) != "the player cannot switch weapons now":
                            raise
                        log.info("m5b player_weapon %s refused (%s), retrying", state, err)
                await asyncio.sleep(0.1)
    except TimeoutError as err:
        raise GameError(f"m5b player_weapon {state}: not ready within {timeout:g} s") from err
    finally:
        await link.body_watch([], timeout=1)


async def neural_scenario(link: GameLink, pilot: BodyPilot | None) -> dict[str, str]:
    """A real one-handed bandit/hero fight, from native acquisition through confirmed release."""
    if pilot is None:
        raise GameError("scenario neural requires GOTHIC_BODY_URL and --neural-body")
    await link.wait_world()
    npc = "DIR_NPC_NEURAL_BANDIT"
    arena = "NW_XARDAS_TOWER_VALLEY_09"
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object) -> None:
        results[name] = f"{'done' if ok else 'failed'} ({detail})"

    # Prepare while separated: no scripted opening conversation, wildlife or allies in the duel.
    await link.debug("player_goto", waypoint="NW_XARDAS_TOWER_VALLEY_01")
    # Stock BDT_1002 carries a sword but has several instances in the world. Give this
    # new bandit its own symbol so body_watch and the lease identify exactly one actor.
    await link.own_npc(npc, like="BDT_1002_BANDIT_L", waypoint=arena)
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
        raise GameError(f"neural arena preparation: {ready.status} {ready.detail}")
    weapon = ["call", "NPC_GETEQUIPPEDMELEEWEAPON", ["inst", "SELF"]]
    symbol, sword, weapon_name = await link.story_eval(npc, [
        ["sym", npc], ["&", ["field", weapon, "FLAGS"], ["var", "ITEM_SWD"]],
        ["field", weapon, "NAME"],
    ])
    if not sword:
        raise GameError(f"neural arena preparation: {npc} has no one-handed sword ({weapon_name})")
    log.info("neural arena: npc=%s weapon=%s", npc, weapon_name)
    # The passive hero survives enough decisions, then falls to actual melee damage.
    await link.debug("player_hp", hp=300)
    await link.debug("player_equip", item="ITMW_1H_BAU_MACE")
    await _m5b_player_weapon(link, "melee")
    drawn = await Bodies(link).act(npc, "draw_weapon", kind="melee")
    if drawn.status != "done":
        raise GameError(f"neural arena preparation: sword draw {drawn.status} {drawn.detail}")
    previous = pilot.last_summary
    leased_entity: int | None = None
    one_handed = False
    down_at: float | None = None
    down_reason: str | None = None

    def observe(packet: dict[str, Any]) -> None:
        nonlocal leased_entity, one_handed, down_at, down_reason
        lease = pilot.lease
        if lease is None or lease.npc != npc:
            return
        for row in packet["observations"]:
            if (
                row["script_symbol"] != symbol
                or (row["entity_id"], row["session_epoch"], row["world_epoch"], row["control_generation"])
                != (lease.entity_id, lease.session_epoch, lease.world_epoch, lease.control_generation)
                or not row["periodic"]
            ):
                continue
            leased_entity = row["entity_id"]
            one_handed |= row["self"]["weapon_state"] == 2
            own, target = row["self"], row["target"]
            if down_at is None:
                if own["dead"] or own["unconscious"] or own["down"] or own["hp"] <= 0:
                    down_reason = "npc_down"
                elif target["entity_id"] == lease.target_entity_id and (
                    target["down"] or (target["hp"] is not None and target["hp"] <= 0)
                ):
                    down_reason = "hero_down"
                if down_reason is not None:
                    down_at = time.monotonic()

    link.raw_body_listeners.append(observe)
    started = time.monotonic()
    try:
        await link.debug("npc_goto", npc=npc, waypoint=arena, face="PC_HERO")
        await link.debug("teleport", near=npc, distance=250)
        await link.story_effect(npc, [{"do": "attack", "reason": "AR_NONE"}])
        finished = await _until(lambda: pilot.last_summary is not previous, 92)
        completed_at = time.monotonic()
    finally:
        link.raw_body_listeners.remove(observe)
    summary = pilot.last_summary if finished else None
    if summary is not None:
        log.info(
            "neural acquire: requests=%d completed=%d request_to_lease_ms p50=%s max=%s",
            summary.acquire_requests, summary.acquire_completed,
            summary.acquire_wait_p50_ms, summary.acquire_wait_max_ms,
        )
        log.info(
            "neural pipeline ms p50/p95: packet_to_request=%s/%s packet_to_response=%s/%s "
            "response_to_action=%s/%s packet_to_action=%s/%s; acquire_refusals=%s action_rejections=%s "
            "transient_rejections_consecutive=%d",
            summary.packet_to_request_p50_ms, summary.packet_to_request_p95_ms,
            summary.packet_to_response_p50_ms, summary.packet_to_response_p95_ms,
            summary.response_to_action_p50_ms, summary.response_to_action_p95_ms,
            summary.packet_to_action_p50_ms, summary.packet_to_action_p95_ms,
            dict(summary.acquire_refusals), dict(summary.action_rejections),
            summary.transient_rejections_consecutive,
        )
    lease = summary.lease if summary else None
    check(
        "neural_lease",
        bool(lease and summary.npc == npc and lease.npc == npc and lease.entity_id == leased_entity
             and lease.provider == "neural-body" and lease.mode == "realtime" and one_handed),
        f"npc={npc}, entity={leased_entity}, sword={bool(sword)}, one_handed={one_handed}, lease={lease}",
    )
    check("neural_decisions", summary is not None and summary.decisions >= 10,
          f"decisions={summary.decisions if summary else None}, minimum=10")
    check("neural_fallback", summary is not None and summary.fallbacks == 0,
          f"fallback={summary.fallbacks if summary else None}")
    duration = down_at - started if down_at is not None else None
    check(
        "neural_outcome",
        bool(summary and summary.reason in {"npc_down", "hero_down"} and summary.reason == down_reason
             and duration is not None and duration <= 90),
        f"reason={summary.reason if summary else None}, observed={down_reason}, duration_s={duration}",
    )
    # Completion includes policy close and unwatch, so this also bounds the earlier native release.
    released_in = completed_at - down_at if down_at is not None else None
    check(
        "neural_release",
        bool(summary and summary.release_confirmed and released_in is not None and released_in <= 2),
        f"confirmed={summary.release_confirmed if summary else False}, after_down_s={released_in}",
    )
    p95 = summary.service_p95_ms if summary else None
    check("neural_service_p95", p95 is not None and p95 <= 20, f"p95_ms={p95}, maximum_ms=20")
    await asyncio.sleep(0.3)
    check(
        "neural_idle",
        bool(summary and summary.watch_removed and pilot.npc is None and pilot.lease is None
             and pilot.client is None and not link.body_leases),
        f"npc={pilot.npc}, lease={pilot.lease}, watch_removed={summary.watch_removed if summary else False}",
    )
    return _logged(results)


async def m5b_bridge(link: GameLink, npc: str = "NONE_100_XARDAS") -> dict[str, str]:
    """M5b on the game: threats from others reach the server, the player's drawn weapon is perceived,
    and the new body actions work for free and attached npc's. A wolf is spawned next to the npc."""
    await link.wait_world()
    await _m5b_player_weapon(link, "none")
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

    # _091 puts Xardas beside a rock that blocks spells from his hand even when
    # the wolf is visible. Use _09's flat ground and its open southeast corridor.
    arena = "NW_XARDAS_TOWER_VALLEY_09"
    # Clear both neighbourhoods: a relocation can retain the stock encounter's
    # enemy, and animals by the new spot must not join either test fight.
    # debug kill drops victims by the hero; leave that food away from the wolves.
    async def clear_animals() -> None:
        around = (await link.snapshot(npc, fresh=True)).nearby_npcs
        for other in around:
            if not other.dead and other.guild >= BEASTS:
                await link.debug("kill", instance=other.id, near=npc)
        # Perception reaches beyond snapshot's 15 m, but only within its field of view.
        for view in await link.npc_view([npc]):
            for seen in view.sees:
                if seen.guild >= BEASTS and seen.body != "dead":
                    await link.debug("kill", instance=seen.id, near=npc)

    await link.debug("player_goto", waypoint="NW_XARDAS_TOWER_VALLEY_01")
    for clearing in ("NW_XARDAS_TOWER_VALLEY_091", arena):
        await link.debug("npc_goto", npc=npc, waypoint=clearing)
        await clear_animals()
    # npc_goto resolves script instances, so face needs PC_HERO, not the PLAYER alias.
    # This position fixes the direction for both wolf spawns, away from the rocks.
    await link.debug("player_goto", pos=[23472, 1032, -10816])
    await link.debug("npc_goto", npc=npc, waypoint=arena, face="PC_HERO")
    await link.debug("teleport", near=npc, distance=300)

    # 1. the player draws a weapon next to the npc: the npc perceives it (it is free, scripts run)
    await attach_when_free(link, npc)
    await link.act(npc, "go_to_npc", npc="PLAYER", radius=300)
    # The approach changes the standing pose and can expose animals hidden from
    # the relocated book-reading pose. Clear that view before native perceptions resume.
    await link.debug("player_goto", waypoint="NW_XARDAS_TOWER_VALLEY_01")
    await clear_animals()
    await link.debug("teleport", near=npc, distance=300)
    await link.act(npc, "return_to_routine")
    # Returning control starts the old tower routine, and the approach above moved
    # Xardas off the arena waypoint. Place this preparation pose directly: this check
    # needs initialized native perceptions, not an incidental walk back to the arena.
    bodies = Bodies(link)
    ready = await bodies.act(
        npc, "start_activity", state="ZS_STAND_ARMSCROSSED", waypoint=arena,
        minutes=60, place=True, arrive=True, radius=100, timeout=15,
    )
    if ready.status != "arrived":
        current = (await link.routine(npc)).current
        snap = await link.snapshot(npc, fresh=True)
        views = await link.npc_view([npc])
        raise GameError(
            f"m5b arena preparation: {ready.status} {ready.detail}; state={current}; "
            f"pos={snap.npc.pos}; rot={snap.npc.rot}; nearby={snap.nearby_npcs}; "
            f"sees={[v.sees for v in views]}"
        )
    # Scan the final standing position too: the earlier relocated book-reading pose
    # can hide wildlife that only becomes visible after initialization.
    await clear_animals()
    events.clear()
    await _m5b_player_weapon(link, "melee")
    e = await event({"weapon_drawn"}, 5)
    results["weapon_drawn"] = f"done ({e.npc}, {e.distance:.0f} cm)" if e else "failed (no weapon_drawn)"
    await _m5b_player_weapon(link, "none")
    # Keep the hero beyond the 400/500 cm wolf spawns so he cannot intercept
    # Xardas's projectiles. The weapon perception above still runs at 300 cm.
    await link.debug("player_goto", pos=[23967, 1032, -11311])

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
    # The flee wolf may stay with the hero outside the fleeing NPC's snapshot.
    # Remove that encounter before restoring the routine, even if it is out of view.
    try:
        await link.debug("kill", instance="WOLF", near="PC_HERO")
    except GameError as err:
        if str(err) != "no such creature alive":
            raise
    around = (await link.snapshot(npc, fresh=True)).nearby_npcs
    for other in around:
        if not other.dead and other.guild >= BEASTS:
            await link.debug("kill", instance=other.id, near=npc)
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
    from .brain.day import Habit
    from .brain.planner import PlannerConfig

    await link.wait_world()
    current = (await link.routine(npc)).current
    # The native starting pose already is Xardas's only usual habit. The day menu correctly excludes
    # it, so give this fixed-decider fixture one distinct, executable option for its h1 choice.
    habit = Habit(
        "stand quietly and think", "ZS_STAND_WP" if current != "ZS_STAND_WP" else "ZS_STAND_ARMSCROSSED"
    )
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
        habits_of=lambda who: [habit] if who == npc else habits_of(who),
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
            FixedDecider({"react": ("thank", "call_help", "comment")}, {"escalate": 3.6, "attitude": 4.0}),
            GameApplier(link),
        )
        link.listeners.append(planner.on_message)
        runner = asyncio.create_task(planner.run())
        after = "Спасибо, выручил ты меня." if killer == "hero" else "Хм… а волк-то был не такой уж страшный."
        llm = FakeBackend(script=[FakeStep("Волк! Помогите, волк!"), FakeStep(after)], delay=0)
        dialogue = DialogueManager(link, llm, PersonaProvider(None))
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
        await asyncio.sleep(1.0)  # the roster (twice a second) has him close: the npc's reaction is urgent
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


async def orders_scenario(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, npc: str = "NONE_100_XARDAS"
) -> dict[str, str]:
    """Orders to a companion on the game (orders.py): the hero's words go in through the bridge's input
    line with his own focus (the attack with a set focus, through the server), the body obeys at once,
    no model is asked. Each check notes how long from the words to the game's answer for the first
    action ("in N ms")."""
    from contextlib import asynccontextmanager

    from .executor import Intent

    await link.wait_world()
    results: dict[str, str] = {}
    calls: list[tuple[float, float, str, str]] = []  # (sent, answered, action, status)
    act = link.act

    async def recorded(who: str, action: Any, /, **kw: Any) -> Any:
        t = time.monotonic()
        res = await act(who, action, **kw)
        calls.append((t, time.monotonic(), action, res.status))
        return res

    link.act = recorded  # type: ignore[method-assign]
    llm = FakeBackend("Это не приказ, а разговор.", delay=0)
    dialogue.llm = llm
    if not await _until(lambda: npc in talk.people, 20):
        link.act = act  # type: ignore[method-assign]
        return {"roster": f"failed ({npc} is not around the hero)"}
    initial = await link.snapshot(npc, fresh=True)
    home = initial.npc.waypoint or initial.npc.nearest_waypoint
    # The open southeast corridor used by m5b keeps the mage's shots away from the
    # road's slopes and trees. Finish the tower state before placing an ordinary idle.
    arena = "NW_XARDAS_TOWER_VALLEY_09"
    await link.debug("player_hp", hp=5000)
    await link.debug("player_goto", pos=[23472, 1032, -10816])
    await link.debug("skip_important", near=npc)
    await link.debug("npc_goto", npc=npc, waypoint=arena, face="PC_HERO")

    async def clear_animals() -> None:
        for other in (await link.snapshot(npc, fresh=True)).nearby_npcs:
            if not other.dead and other.guild >= BEASTS:
                await link.debug("kill", instance=other.id, near=npc)
        for view in await link.npc_view([npc]):
            for seen in view.sees:
                if seen.guild >= BEASTS and seen.body != "dead":
                    await link.debug("kill", instance=seen.id, near=npc)

    for attempt in range(3):
        # A newly placed native pose can see a rat hidden from the old reading pose.
        # Only this setup is retried; no order or combat assertion has started yet.
        await clear_animals()
        await attach_when_free(link, npc)
        await link.act(npc, "return_to_routine")
        ready = await Bodies(link).act(
            npc, "start_activity", state="ZS_STAND_ARMSCROSSED", waypoint=arena,
            minutes=60, place=True, arrive=True, radius=100, timeout=15,
        )
        if ready.status == "arrived":
            break
        state = (await link.routine(npc)).current
        snap = await link.snapshot(npc, fresh=True)
        reason = (
            f"{ready.status} {ready.detail}; state={state}; "
            f"pos={snap.npc.pos}; nearby={snap.nearby_npcs}"
        )
        log.info("orders arena preparation reset %d/3: %s", attempt + 1, reason)
        if state != "ZS_ATTACK" or attempt == 2:
            raise GameError(f"orders arena preparation: {reason}")
    await clear_animals()
    if not await talk.hold(npc, "follow"):
        link.act = act  # type: ignore[method-assign]
        return {"roster": f"failed (cannot hold {npc} for the arena setup)"}
    await link.act(npc, "turn_to", npc="PLAYER")
    await link.debug("teleport", near=npc, distance=900)
    ready = await _until(
        lambda: npc in talk.people
        and 750 <= talk.people[npc].distance <= 1150
        and talk.people[npc].earshot <= 1200,
        10,
    )
    snap = await link.snapshot(npc, fresh=True)
    level = snap.player is not None and abs(snap.player.pos[1] - snap.npc.pos[1]) <= 200
    if not ready or not level:
        link.act = act  # type: ignore[method-assign]
        return {"roster": f"failed (open arena setup: {talk.people.get(npc)}, same floor {level})"}
    dialogue.open(npc, "Ксардас").intent = Intent("follow_hero", "PLAYER", "friendly")

    companion = "BAU_4300_ADDON_CAVALORN"
    watched = (npc, "PC_HERO", "WOLF", companion)
    symbols = await link.story_eval(npc, [["sym", who] for who in watched])
    names = dict(zip(symbols, (npc, "PLAYER", "WOLF", companion), strict=True))

    async def diagnose(stage: str, who: str = npc) -> None:
        try:
            snap = await link.snapshot(who, fresh=True)
            views = await link.npc_view([who])
            log.info(
                "orders snapshot stage=%s npc=%s pos=%s hp=%d/%d busy=%s action=%s "
                "hero=%s roster=%s views=%s",
                stage, who, snap.npc.pos, snap.npc.hp, snap.npc.hp_max,
                snap.npc.busy, snap.npc.action,
                snap.player.model_dump() if snap.player else None,
                talk.people.get(who), [view.model_dump() for view in views],
            )
        except (GameError, TimeoutError) as err:
            log.info("orders snapshot stage=%s failed: %s", stage, err)

    @asynccontextmanager
    async def observe(stage: str, actors: list[str]) -> AsyncIterator[None]:
        last: dict[str, tuple[int, int, int]] = {}

        def body(packet: dict[str, Any]) -> None:
            for row in packet["observations"]:
                who = names.get(row["script_symbol"], str(row["script_symbol"]))
                own, target = row["self"], row["target"]
                now, hp, foe = row["t_game_ms"], own["hp"], target["entity_id"]
                before = last.get(who)
                if before is not None and now - before[0] < 1000 and (hp, foe) == before[1:]:
                    continue
                last[who] = (now, hp, foe)
                log.info(
                    "orders body stage=%s npc=%s game_ms=%d %s",
                    stage, who, now,
                    {"entity": row["entity_id"], "pos": own["position_world_cm"],
                     "velocity": own["linear_velocity_cm_s"], "hp": hp,
                     "weapon": own["weapon_state"], "body": own["body_state_base"],
                     "attack": own["attack_anim"], "gates": own["gates"],
                     "target": foe, "target_pos": target["position_world_cm"],
                     "target_hp": target["hp"], "target_visible": target["visible"]},
                )

        link.raw_body_listeners.append(body)
        try:
            await link.body_watch(actors, rate_hz=10)
            yield
        finally:
            link.raw_body_listeners.remove(body)
            try:
                await link.body_watch([])
            except (GameError, TimeoutError) as err:
                log.info("orders body watch cleanup failed: %s", err)

    def dist() -> float:
        return talk.people[npc].distance / 100 if npc in talk.people else -1.0

    given: set[str] = set()

    async def order(
        text: str, action: str, focus: str | None = None, timeout: float = 15
    ) -> tuple[bool, str]:
        await diagnose(f"before_{action}")
        # WOLF is a shared instance symbol; its position/HP arrive through Xardas's
        # actual target, while body_watch requires each actor's symbol to be unique.
        async with observe(action, [npc, "PLAYER"]):
            if action == "fight" and talk.orders is not None and npc in talk.orders.fights:
                raise GameError("orders attack preparation: automatic defence started before the order")
            t0 = time.monotonic()
            given.add(text)
            if focus is None:
                await link.debug("type", text=text)
            else:
                talk.hear(text, focus, {"via": "scenario"})
            ok = await _until(lambda: any(a == action and t >= t0 for t, _, a, _ in calls), timeout)
            first = next(((t1, a, st) for t, t1, a, st in calls if t >= t0), None)
            took = f"{(first[0] - t0) * 1000:.0f} ms to {first[1]} {first[2]}" if first else "no action"
            done = ok and all(st == "done" for t, _, a, st in calls if t >= t0 and a == action)
            if not done:
                await diagnose(f"failed_{action}")
            return done, took

    ok, took = await order("Ксардас, ко мне!", "follow")
    came = await _until(lambda: 0 <= dist() <= 5, 20)
    results["come"] = f"{'done' if ok and came else 'failed'} (in {took}; {dist():.1f} m from the hero)"
    if not ok or not came:
        await diagnose("come_failed")

    ok, took = await order("Стой!", "stop")
    await link.debug("teleport", near=npc, distance=900)
    await asyncio.sleep(4)
    stood = dist()
    results["halt"] = f"{'done' if ok and stood >= 7.5 else 'failed'} (in {took}; stays {stood:.1f} m behind)"
    if not ok or stood < 7.5:
        await diagnose("halt_failed")

    ok, took = await order("За мной!", "follow")
    closer = await _until(lambda: 0 <= dist() <= stood - 2, 15)
    results["follow"] = f"{'done' if ok and closer else 'failed'} (in {took}; {stood:.1f} -> {dist():.1f} m)"
    if not ok or not closer:
        await diagnose("follow_failed")

    ok, took = await order("К оружию!", "draw_weapon")
    results["draw"] = f"{'done' if ok else 'failed'} (in {took})"

    # The follow check is over. Keep the passive hero behind and to the side of
    # the mage, away from the projectile lane. Its end point is on a slope, so
    # stage this separate combat check on the pasture's tested flat ground.
    await link.act(npc, "stop")
    await link.act(npc, "return_to_routine")
    await _until(lambda: npc not in talk.attached, 5)
    attack_arena = "NW_FARM1_FIELD_06"
    await link.debug("player_goto", waypoint=attack_arena)
    await link.debug("npc_goto", npc=npc, waypoint=attack_arena)
    await clear_animals()
    if not await talk.hold(npc, "follow"):
        raise GameError("orders attack preparation: cannot hold the mage")
    await link.act(npc, "stop")
    await asyncio.sleep(0.5)  # settle on npc_goto's native ground before choosing the lane
    dialogue.open(npc, "Ксардас").intent = Intent("follow_hero", "PLAYER", "friendly")
    await diagnose("attack_arena_placed")
    origin = (await link.snapshot(npc, fresh=True)).npc.pos
    wolf, ray = None, {}
    for step in range(8):
        await clear_animals()
        angle = step * math.pi / 4
        dx, dz = math.cos(angle), math.sin(angle)
        await link.debug("player_goto", pos=[origin[0] + 500 * dx, origin[1], origin[2] + 500 * dz])
        await link.act(npc, "turn_to", npc="PLAYER")
        # debug spawn independently tests support and the observer's native ray.
        try:
            await link.debug("spawn", instance="WOLF", near=npc, distance=1400)
        except GameError as err:
            if not str(err).startswith("no visible supported ground"):
                raise
            log.info("orders attack preparation lane=%d: %s", step, err)
            continue
        await link.debug(
            "player_goto",
            pos=[origin[0] - 400 * dx - 500 * dz, origin[1], origin[2] - 400 * dz + 500 * dx],
        )
        await link.act(npc, "turn_to", npc="WOLF")
        await asyncio.sleep(1)
        views = await link.npc_view([npc])
        wolf = next(
            (other for view in views for other in view.sees
             if other.id == "WOLF" and other.kind == "monster" and other.body != "dead"),
            None,
        )
        response = await link._request(
            DebugCmd(req=link._next_id("r"), cmd="npc_los", npc=npc, to="WOLF"), 5,
        )
        ray = response.value or {}
        automatic = talk.orders is not None and npc in talk.orders.fights
        log.info(
            "orders attack preparation lane=%d: wolf=%s native_ray=%s automatic=%s",
            step, wolf, ray, automatic,
        )
        if automatic:
            raise GameError("orders attack preparation: automatic defence before the order")
        if (
            wolf is not None and wolf.distance > 1000 and wolf.hero_cm is not None and wolf.hero_cm > 1500
            and (ray.get("center_clear") or ray.get("head_clear"))
        ):
            break
    else:
        raise GameError(f"orders attack preparation: no clear lane; wolf={wolf}; ray={ray}")
    ok, took = await order("В атаку!", "fight", focus="WOLF", timeout=60)
    over = await _until(lambda: any(a == "fight" for _, _, a, _ in calls) and calls[-1][2] == "follow", 60)
    fight = next((st for _, _, a, st in reversed(calls) if a == "fight"), "-")
    results["attack"] = (
        f"{'done' if ok and over and fight == 'done' else 'failed'} "
        f"(in {took}; fight {fight}, follows after {over})"
    )
    if not ok or not over or fight != "done":
        await diagnose("attack_failed")

    ok, took = await order("Убери оружие.", "remove_weapon")
    results["sheathe"] = f"{'done' if ok else 'failed'} (in {took})"

    ok, took = await order("Свободен.", "stop")
    free = await _until(lambda: npc not in talk.attached, 10)
    goal = dialogue.conversations[npc].intent.goal
    results["dismiss"] = (
        f"{'done' if ok and free and goal == 'keep_routine' else 'failed'} "
        f"(in {took}; detached {free}, goal {goal})"
    )

    # Combat incidents may speak on their own. Only the hero's orders must skip a dialogue turn.
    order_calls = sum(1 for _, messages, _ in llm.calls if messages and messages[-1].content in given)
    results["no_model"] = (
        f"{'done' if not order_calls else 'failed'} "
        f"({order_calls} order model call(s), {len(llm.calls) - order_calls} incident call(s))"
    )
    link.act = act  # type: ignore[method-assign]
    if home is not None:
        await link.debug("npc_goto", npc=npc, waypoint=home)
    async with observe("companion_attack_words", [companion, "PLAYER"]):
        await diagnose("companion_attack_words_before", companion)
        attack_words = await _companion_attack_words(link, dialogue, talk)
        if any(not result.startswith("done") for result in attack_words.values()):
            await diagnose("companion_attack_words_failed", companion)
        results.update(attack_words)
    for name, outcome in results.items():
        log.info("%-12s %s", name, outcome)
    return results


async def _companion_attack_words(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, npc: str = "BAU_4300_ADDON_CAVALORN"
) -> dict[str, str]:
    """2026-10-18: «бей волка» without focus belongs to Cavalorn, before a wolf bites the hero.

    Retreat during setup keeps an automatic monster defence from satisfying the attack check. The
    explicit attack must replace it; the wolf's tone model and judge must never receive the words.
    """
    from .executor import Intent

    beasts = talk.beasts
    if beasts is None or talk.orders is None:
        return {"wolf_ready": "failed (no beasts or companion orders)"}
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object) -> None:
        results[name] = f"{'done' if ok else 'failed'} ({detail})"

    def dist() -> float:
        return talk.people[npc].distance / 100 if npc in talk.people else -1.0

    await link.debug("player_hp", hp=5000)
    await link.debug("skip_important", near=npc)
    for _ in range(2):
        try:
            await link.debug("kill", instance="YGOBBO_GREEN", near=npc, by=npc)
        except GameError:
            pass
    arena = "NW_FARM1_FIELD_06"
    await link.debug("player_goto", waypoint=arena)
    await asyncio.sleep(1)  # the arena's roster, rather than the one still at Cavalorn's fire
    for _ in range(8):  # clear the arena's own wolves before its one controlled encounter
        if not beasts.wolves:
            break
        wolf = min(beasts.wolves.values(), key=lambda w: w.distance)
        await link.debug("kill", instance=wolf.id.split("#", 1)[0], by="PLAYER")
        await _until(lambda wolf=wolf: wolf.id not in beasts.wolves, 5)
    # Stage the free NPC first: teleporting the hero from the fire after follow starts ends that order
    # with follow_ended (the hero went too far). Its first real follow now covers only two metres.
    await link.debug("player_goto", waypoint=arena)
    await link.debug("npc_goto", npc=npc, waypoint=arena, face="PC_HERO")
    if not await talk.hold(npc, "follow"):
        return {"wolf_ready": f"failed (cannot hold {npc} for the arena setup)"}
    await link.act(npc, "turn_to", npc="PLAYER")
    await link.debug("teleport", near=npc, distance=700)
    if not await _until(
        lambda: npc in talk.people
        and 500 <= talk.people[npc].distance <= 900
        and talk.people[npc].earshot <= 1000,
        15,
    ):
        return {"wolf_ready": f"failed (Cavalorn {dist():.1f} m from the hero)"}
    dialogue.open(npc, "Кавалорн").intent = Intent("follow_hero", "PLAYER", "friendly")
    talk.hear("Кавалорн, за мной!", None, {"via": "scenario"})
    following = await _until(
        lambda: 0 <= dist() <= 5
        and npc in talk.attached
        and getattr(talk.orders.standing.get(npc), "kind", "") == "follow",
        20,
    )
    check("wolf_follow", following, f"Cavalorn {dist():.1f} m from the hero")
    if not following:
        return results

    snap = await link.snapshot(npc, fresh=True)
    if snap.player is None:
        return {**results, "wolf_ready": "failed (no hero in Cavalorn's snapshot)"}
    toward = (snap.player.pos[0] - snap.npc.pos[0], snap.player.pos[2] - snap.npc.pos[2])
    toward_len = math.hypot(*toward) or 1
    dx, dz = toward[0] / toward_len, toward[1] / toward_len
    hp = snap.player.hp if snap.player is not None else None
    talk.hear("Кавалорн, отступай!", None, {"via": "scenario"})
    await _until(lambda: getattr(talk.orders.standing.get(npc), "kind", "") == "retreat", 5)
    await link.act(npc, "turn_to", npc="PLAYER")
    before = set(beasts.wolves)
    await link.story_spawn("WOLF", tag="companion_attack_words", near=npc, distance=1400)
    ready = await _until(
        lambda: any(w.id not in before and w.state == "threaten" for w in beasts.wolves.values()), 15
    )
    wolf_id = next((w.id for w in beasts.wolves.values() if w.id not in before), "")
    check("wolf_ready", ready, f"{wolf_id} spawned 14 m from Cavalorn, before its first attack")
    if not ready:
        return results

    # The wolf has growled, as this check requires. Now keep the hero away from
    # both the attacker's reach and Cavalorn's projectile lane before giving the order.
    await link.act(npc, "stop")
    origin = (await link.snapshot(npc, fresh=True)).npc.pos
    await link.debug(
        "player_goto", pos=[origin[0] - 400 * dx - 500 * dz, origin[1], origin[2] - 400 * dz + 500 * dx]
    )
    await link.act(npc, "turn_to", npc="WOLF")
    views = await link.npc_view([npc])
    wolf = next(
        (other for view in views for other in view.sees
         if other.id == "WOLF" and other.kind == "monster" and other.body != "dead"),
        None,
    )
    response = await link._request(
        DebugCmd(req=link._next_id("r"), cmd="npc_los", npc=npc, to="WOLF"), 5,
    )
    ray = response.value or {}
    automatic = npc in talk.orders.fights
    log.info("orders Cavalorn attack preparation: wolf=%s native_ray=%s automatic=%s", wolf, ray, automatic)
    if (
        wolf is None or wolf.distance <= 1000 or wolf.hero_cm is None or wolf.hero_cm <= 1500
        or not (ray.get("center_clear") or ray.get("head_clear")) or automatic
    ):
        raise GameError(f"orders Cavalorn attack preparation: wolf={wolf}; ray={ray}; automatic={automatic}")

    actions: list[tuple[float, str, str]] = []  # dispatch time, target, wolf state at dispatch
    finished: list[str] = []
    states: list[tuple[float, str]] = []
    killed: list[str] = []
    judged: list[str] = []
    heard: list[str] = []
    act, judge, hears, quick = link.act, beasts.judge, beasts.hears, beasts.quick
    tone_model = FakeBackend("hostile", delay=0)
    beasts.quick = tone_model

    async def record_act(who: str, action: Any, /, **kw: Any) -> Any:
        mine = who.upper() == npc and action == "fight"
        if mine:
            w = beasts.wolves.get(wolf_id)
            actions.append((time.monotonic(), str(kw.get("npc", "")), w.state if w else "gone"))
        result = await act(who, action, **kw)
        if mine:
            finished.append(result.status)
        return result

    async def record_judge(text: str, hearers: Any) -> Any:
        judged.append(text)
        return await judge(text, hearers)

    def record_hears(text: str, *args: Any) -> Any:
        heard.append(text)
        return hears(text, *args)

    def on_beasts(msg: GameMessage) -> None:
        if isinstance(msg, Beasts):
            states.extend((time.monotonic(), w.state) for w in msg.wolves if w.id == wolf_id)
        elif isinstance(msg, NpcDied) and msg.tag == "companion_attack_words":
            killed.append(msg.killer.upper())

    link.act = record_act  # type: ignore[method-assign]
    beasts.judge = record_judge  # type: ignore[method-assign]
    beasts.hears = record_hears  # type: ignore[method-assign]
    link.listeners.append(on_beasts)
    try:
        mark = time.monotonic()
        talk.hear("Бей волка!", None, {"via": "scenario"})
        dispatched = await _until(lambda: bool(actions), 10)
        # A weak wolf can die to Cavalorn's first blow between the 500 ms beast reports. Its tagged
        # death names the actual killer, so that is also proof of the companion's real fight.
        fought = await _until(lambda: any(state == "fight" for _, state in states) or npc in killed, 15)
        to = talk.last[0] if talk.last else None
        check(
            "attack_without_focus",
            dispatched
            and fought
            and to is not None
            and to.id == npc
            and to.why == "order"
            and actions[0][1].split("#", 1)[0] == wolf_id.split("#", 1)[0],
            f"to {to}; fights {actions}, wolf fought Cavalorn {fought}, killers {killed}",
        )
        before_bite = (
            dispatched
            and actions[0][2] != "attack"
            and not any(state == "attack" and t <= actions[0][0] for t, state in states)
        )
        snap = await link.snapshot(npc, fresh=True)
        after_hp = snap.player.hp if snap.player is not None else None
        check(
            "attack_before_bite",
            before_bite and hp is not None and after_hp == hp,
            f"dispatch in {actions[0][0] - mark:.2f} s, hp {hp} -> {after_hp}"
            if actions
            else f"no fight, hp {hp} -> {after_hp}",
        )
        check(
            "wolf_unheard",
            not heard and not judged and not tone_model.calls,
            f"hears {len(heard)}, judge {len(judged)}, tone model {len(tone_model.calls)}",
        )
        await _until(lambda: bool(finished), 60)
    finally:
        link.act = act  # type: ignore[method-assign]
        beasts.judge = judge  # type: ignore[method-assign]
        beasts.hears = hears  # type: ignore[method-assign]
        beasts.quick = quick
        link.listeners.remove(on_beasts)
    return results


class _Rolls:
    """The dice of a scenario: the rolls it is to come up with, in turn (0.5 once they run out)."""

    def __init__(self) -> None:
        self.queue: list[float] = []

    def random(self) -> float:
        return self.queue.pop(0) if self.queue else 0.5


WOLF_HERO = "NW_XARDAS_PATH_FARM1_05"  # on the road from Xardas's tower to the farm


async def wolf_scenario(link: GameLink, talk: TalkDesk, beasts: BeastDesk) -> dict[str, str]:
    """Wolves hear the hero (docs/beasts.md) on the road below Xardas's tower. A wolf growls at him; once
    he starts to speak it waits for his words past its four seconds; soothed, it lets him pass: sits down,
    watches him go by, lets him be once he has gone and come back. Then a wolf each: a hostile phrase
    (it attacks), «Стой!» with no one looked at (the wolf's, not a companion order) and then walking right
    up to it (it bites), a threat (it runs), a soothing that fails (it attacks). The dice are the
    scenario's; the tone is the quick model's with QUICK_URL, the rules' else."""
    await link.wait_world()
    results: dict[str, str] = {}
    rng, rolls = beasts.rng, _Rolls()
    beasts.rng = rolls  # type: ignore[assignment]

    def check(name: str, ok: bool, detail: str) -> None:
        results[name] = f"{'done' if ok else 'failed'} ({detail})"
        log.info("%-12s %s", name, results[name])

    def state(wolf: str | None) -> str:
        e = beasts.wolves.get(wolf or "")
        return e.state if e is not None else "gone"

    def dist(wolf: str | None) -> float:
        e = beasts.wolves.get(wolf or "")
        return round(e.distance / 100, 1) if e is not None else -1.0

    async def hero_hp() -> int:
        snap = await link.snapshot("NONE_100_XARDAS", fresh=True)
        return snap.player.hp or 0 if snap.player is not None else 0

    def cm(a: dict[str, Any], b: dict[str, Any]) -> float:
        return float(((a["x"] - b["x"]) ** 2 + (a["z"] - b["z"]) ** 2) ** 0.5)

    points = {p["name"]: p for p in await link.waypoints()}
    hero = points[WOLF_HERO]
    ground = [p for p in points.values() if abs(p["y"] - hero["y"]) < 250 and not p["fp"]]
    at = min(ground, key=lambda p: abs(cm(p, hero) - 1000))  # ~10 m off: it growls at him
    # beyond the wolf and 4.5 m aside of it (he walks by); back behind him, 19 m from it (gone, still listed)
    dx, dz = (at["x"] - hero["x"]) / cm(at, hero), (at["z"] - hero["z"]) / cm(at, hero)
    past = (at["x"] + dx * 300 - dz * 450, at["y"], at["z"] + dz * 300 + dx * 450)
    away = (hero["x"] - dx * 1000, hero["y"], hero["z"] - dz * 1000)
    log.info("wolf at %s, %.0f m from the hero", at["name"], cm(at, hero) / 100)

    async def kill(wolf: str | None) -> None:
        if wolf in beasts.wolves:
            await link.debug("kill", instance=wolf.split("#", 1)[0], by="PLAYER")  # the nearest of its kind
            await _until(lambda: wolf not in beasts.wolves, 5)

    async def clear() -> list[str]:
        """No wolf about but the scenario's (the road's own young wolf, a pack)."""
        killed = []
        for _ in range(8):
            if not beasts.wolves:
                break
            wolf = min(beasts.wolves.values(), key=lambda w: w.distance).id
            try:
                await kill(wolf)
            except GameError as err:
                log.info("could not kill %s: %s", wolf, err)
                break
            killed.append(wolf)
        return killed

    async def wolf_at_him(n: int) -> str | None:
        """A wolf at its point, the hero at his; the wolf, once it growls at him."""
        await link.debug("player_goto", waypoint=WOLF_HERO)
        await asyncio.sleep(1)
        await clear()
        before = set(beasts.wolves)
        await link.story_spawn("WOLF", tag=f"beast_{n}", waypoint=at["name"])
        await _until(lambda: any(w not in before for w in beasts.wolves), 10)
        wolf = next((w for w in beasts.wolves if w not in before), None)
        await _until(lambda: state(wolf) == "threaten", 15)
        return wolf if state(wolf) == "threaten" else None

    def said(text: str, focus: str | None = "WOLF") -> float:
        talk.hear(text, focus, {"via": "scenario"})
        return time.monotonic()

    def verdict() -> str:
        v, t = (beasts.verdicts[0] if beasts.verdicts else None), beasts.last_tone
        if v is None or t is None:
            return "no verdict"
        return f"{v.outcome}, chance {v.chance:.2f}, tone {t[0]} by {t[1]} in {t[2]} ms"

    await link.debug("player_hp", hp=5000)  # the bites of the failing tries do not kill him
    await link.debug("player_goto", waypoint=WOLF_HERO)
    await asyncio.sleep(2)
    log.info("cleared: %s", await clear() or "nobody about")

    # 1. a wolf growls at him, waits for his words, lets him pass and be
    wolf = await wolf_at_him(1)
    check("threatens", wolf is not None, f"{wolf} {state(wolf)} at {dist(wolf)} m")
    listening = beasts.listen()  # as the talk key does
    listens = await _until(lambda: state(wolf) == "listen", 3)
    hp = await hero_hp()
    await asyncio.sleep(6)  # past its four seconds of growling
    still = state(wolf)
    check(
        "listens",
        listens and still == "listen",
        f"{listening}: {still} after 6 s, hp {hp} -> {await hero_hp()}",
    )
    rolls.queue = [0.0]
    t0 = said("Тихо, тихо, хороший мальчик. Я просто мимо пройду.")
    calm = await _until(lambda: state(wolf) == "calm", 5)
    to = talk.last[0] if talk.last else None
    check(
        "calm",
        calm and to is not None and to.why == "beast",
        f"{verdict()}, in {time.monotonic() - t0:.1f} s, said to {to.id if to else None}",
    )
    sits = await _until(lambda: (e := beasts.wolves.get(wolf or "")) is not None and e.sitting, 10)
    check("sits", sits, f"{state(wolf)}, sitting {sits}, hp {await hero_hp()}")
    await link.debug("player_goto", pos=past)
    await asyncio.sleep(4)
    check("passes", state(wolf) == "calm" and await hero_hp() == hp, f"{state(wolf)} at {dist(wolf)} m")
    await link.debug("player_goto", pos=away)
    over = await _until(lambda: state(wolf) == "routine", 5)
    await link.debug("player_goto", waypoint=WOLF_HERO)
    await asyncio.sleep(8)
    e = beasts.wolves.get(wolf or "")
    check(
        "lets_be",
        over and e is not None and e.state == "routine" and e.tolerant and await hero_hp() == hp,
        f"{state(wolf)} at {dist(wolf)} m, tolerant {e.tolerant if e else None}",
    )
    await kill(wolf)

    # 2. hostility: it attacks at once, no dice
    wolf = await wolf_at_him(2)
    t0 = said("Иди сюда, тварь, я тебя прикончу!")
    went = await _until(lambda: state(wolf) == "attack", 4)
    check(
        "hostile", wolf is not None and went, f"{verdict()}, {state(wolf)} in {time.monotonic() - t0:.1f} s"
    )
    await kill(wolf)

    # 3. «Стой!» looking at no one is the wolf's; let pass, then walked right up to, it bites
    wolf = await wolf_at_him(3)
    rolls.queue = [0.0]
    said("Стой!", None)
    calm = await _until(lambda: state(wolf) == "calm", 5)
    to = talk.last[0] if talk.last else None
    check(
        "halt_word", calm and to is not None and to.why == "beast", f"{verdict()}, to {to.id if to else None}"
    )
    await asyncio.sleep(1)
    await link.debug("player_goto", waypoint=at["name"])
    bit = await _until(lambda: state(wolf) == "attack", 4)
    check("too_close", bit, f"{state(wolf)} at {dist(wolf)} m")
    await kill(wolf)

    # 4. a threat scares it off
    wolf = await wolf_at_him(4)
    rolls.queue = [0.0]
    said("Пошёл вон! Брысь!")
    ran = await _until(lambda: state(wolf) == "flee", 4)
    d0 = dist(wolf)
    await asyncio.sleep(3)
    check(
        "scared",
        ran and (dist(wolf) > d0 or wolf not in beasts.wolves),
        f"{verdict()}, {d0} -> {dist(wolf)} m",
    )
    await kill(wolf)

    # 5. a soothing the dice say no to: it did not believe him
    wolf = await wolf_at_him(5)
    rolls.queue = [0.99]
    said("Тихо, тихо, хороший. Я тебя не трону.")
    went = await _until(lambda: state(wolf) == "attack", 4)
    check("disbelief", wolf is not None and went, f"{verdict()}, {state(wolf)}")

    # 6. the author's case: it bites him already, he talks it down; it steps back and lets him be
    bites = await _until(lambda: state(wolf) == "attack" and dist(wolf) < 3, 10)
    rolls.queue = [0.0]
    said("Стой! Хороший мальчик, я тут мимо пройду.")
    calm = await _until(lambda: state(wolf) == "calm", 4)
    back = await _until(lambda: dist(wolf) >= 4, 5)
    hp = await hero_hp()
    await asyncio.sleep(4)
    check(
        "calm_bite",
        bites and calm and back and state(wolf) == "calm" and await hero_hp() == hp,
        f"{verdict()}, {state(wolf)} at {dist(wolf)} m, hp {hp} -> {await hero_hp()}",
    )
    await kill(wolf)

    beasts.rng = rng
    return results


class _OnWords:
    """A scripted model that answers by what it is asked: the step of the first words found in the last
    message (the hero's phrase or an event's cue), `default` else."""

    name = "fake"

    def __init__(self, replies: list[tuple[str, FakeStep]], default: FakeStep) -> None:
        self.replies, self.default = replies, default

    async def aclose(self) -> None:
        pass

    def stream(
        self,
        *,
        instructions: str,
        messages: Sequence[Item],
        tools: list[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        last = messages[-1] if messages else None
        text = last.content if isinstance(last, Message) else ""
        step = next((s for words, s in self.replies if words.lower() in text.lower()), self.default)
        return FakeBackend(script=[step], delay=0).stream(
            instructions=instructions,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            allowed=allowed,
            cache_key=cache_key,
        )


class _Recorded:
    """A model as it is, and what it was given: each request's messages, for a scenario to read."""

    def __init__(self, llm: Any) -> None:
        self.llm = llm
        self.name = llm.name
        self.asked: list[str] = []

    async def aclose(self) -> None:
        await self.llm.aclose()

    def stream(self, *, messages: Sequence[Item], **kw: Any) -> AsyncIterator[LLMEvent]:
        self.asked.append("\n".join(m.content for m in messages if isinstance(m, Message)))
        return self.llm.stream(messages=messages, **kw)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.llm, name)


class _ByNpc:
    """A scripted model for each npc: the one whose «Ты — {name}» its instructions have; `default` else."""

    name = "fake"

    def __init__(self, by: dict[str, Any], default: Any) -> None:
        self.by, self.default = by, default

    async def aclose(self) -> None:
        pass

    def stream(
        self,
        *,
        instructions: str,
        messages: Sequence[Item],
        tools: list[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        llm = next((b for name, b in self.by.items() if f"Ты — {name}" in instructions), self.default)
        return llm.stream(
            instructions=instructions,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            allowed=allowed,
            cache_key=cache_key,
        )


async def companion_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    npc: str = "BAU_4300_ADDON_CAVALORN",
    bandit: str = "BDT_1013_BANDIT_L",
    cave: tuple[str, ...] = ("BDT_1014_BANDIT_L", "BDT_1015_BANDIT_L"),
    perception: PerceptionDesk | None = None,
) -> dict[str, str]:
    """The hero's companion on the game (orders.py), as the user played it on 2026-10-13: Cavalorn walked
    after a running hero, watched a bandit beat him and only talked, and did not understand "go into the
    cave"; the bandits below Xardas's tower ran their scripted ambush. Checks:
    - pace: a companion left behind runs to the hero (it walked within 12 m before);
    - freed: the bandit at the steps does not start his scripted "Where are you going?" (story.FREED);
    - go_to: told to go into the cave first, it walks into the bandits' cave and waits there; the freed
      bandits in it do not fall on it;
    - attack: the bandit's own model decides to attack the hero (scripted): the scripts' attack;
    - defend: the companion, told «Погоди.» 20 m off (an order now: 2026-10-14 the model took it for «I
      no longer need you», and Cavalorn stood by while the bandits beat the hero 20 m from him), fights
      him at once, with no order, and follows the hero again after;
    - party: it is the scripts' party member while it goes with the hero, so that
    - xp: the bandit it beats gives the hero the experience (ZS_Dead, ZS_Unconscious);
    - sees_cave, focus (with `perception`, step M1 of docs/npc-tools.md): in the cave, having looked around,
      it sees a bandit there (the other may be behind the cave's bend), with its id for its intent; whom the
      hero faces is his focus in its view, and a target.
    The models are scripted (LLM=fake); the hero wears heavy armor, so that he lives through the beating."""
    from .executor import Intent

    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    calls: list[tuple[float, str, str, dict[str, Any]]] = []  # (sent, action, status, args)
    act = link.act

    async def recorded(who: str, action: Any, /, **kw: Any) -> Any:
        t = time.monotonic()
        res = await act(who, action, **kw)
        if who.upper() == npc:
            calls.append((t, action, res.status, kw))
        return res

    link.act = recorded  # type: ignore[method-assign]
    chosen: list[DialogChosen] = []
    link.listeners.append(lambda m: chosen.append(m) if isinstance(m, DialogChosen) else None)
    entries: list[dict[str, Any]] = []
    sink = dialogue.journal.sink

    def keep(entry: dict[str, Any]) -> None:
        entries.append(entry)
        if sink is not None:
            sink(entry)

    dialogue.journal.sink = keep
    fight = {"goal": "fight", "target": "PLAYER", "attitude": "hostile", "agenda": None}
    go = ToolCall("go_to", {"place": "XARDAS_BANDITS"})
    dialogue.llm = _ByNpc(
        {
            "Кавалорн": _OnWords(
                [
                    ("пещеру первым", FakeStep("Пойду первым.", [go])),
                    ("пришёл первым", FakeStep("Тут двое.")),
                ],
                FakeStep("Держись, я с тобой!"),
            ),
            "Бандит": FakeBackend(
                script=[FakeStep("Ну всё, сам напросился!", [ToolCall("set_intent", fight)])], delay=0
            ),
        },
        FakeBackend("Хм.", delay=0),
    )

    def dist(who: str = npc) -> float:
        return talk.people[who].distance / 100 if who in talk.people else -1.0

    def since(t0: float, action: str) -> list[tuple[float, str, str, dict[str, Any]]]:
        return [c for c in calls if c[0] >= t0 and c[1] == action]

    try:
        await link.debug("player_equip", item="ITAR_PAL_H")
    except GameError as err:
        log.info("companion: no armor for the hero: %s", err)
    await link.debug("skip_important", near=npc)  # Cavalorn's own greeting would stop the hero
    for _ in range(2):  # the goblins by his fire: they would beat the hero down before anything is checked
        try:
            await link.debug("kill", instance="YGOBBO_GREEN", near=npc, by=npc)
        except GameError as err:
            log.info("companion: goblin: %s", err)
    await link.debug("player_goto", to=npc, distance=300)
    if not await _until(lambda: 0 <= dist() <= 15, 20):
        link.act = act  # type: ignore[method-assign]
        return _logged({"roster": f"failed ({npc} is not around the hero: {dist():.1f} m)"})
    dialogue.open(npc, "Кавалорн").intent = Intent("follow_hero", "PLAYER", "friendly")
    talk.hear("Кавалорн, за мной!", None, {"via": "scenario"})
    following = await _until(lambda: since(0, "follow") and 0 <= dist() <= 5, 20)
    check("follow", following, f"{dist():.1f} m")

    # pace: the hero is suddenly 11+ m off; a run covers it in about 2 s, a walk in 5 or more
    await link.debug("player_goto", near=npc, distance=1100)
    t0 = time.monotonic()
    await _until(lambda: dist() >= 9, 3)
    d0 = dist()
    caught_up = await _until(lambda: 0 <= dist() <= 5, 20)
    took = time.monotonic() - t0
    speed = (d0 - 5) / took if took > 0 else 0.0
    check("pace", caught_up and speed >= 2.5, f"{d0:.1f} m -> 5 m in {took:.1f} s, {speed:.1f} m/s")

    # freed: next to the bandit at the steps, nothing starts on its own
    await link.debug("player_goto", to=bandit, distance=300)
    near = await _until(lambda: 0 <= dist(bandit) <= 6, 15)
    await asyncio.sleep(8)
    ambush = [c.node for c in chosen if c.npc.upper() == bandit]
    check("freed", near and not ambush, f"{dist(bandit):.1f} m from him, dialogs {ambush or 'none'}")

    # go_to: "into the cave first"; the bandits there let it be
    await _until(lambda: 0 <= dist() <= 6, 30)  # it has caught up from its fire: it hears its name
    t0 = time.monotonic()
    talk.hear("Кавалорн, иди в пещеру первым.", None, {"via": "scenario"})
    walked = await _until(lambda: since(t0, "go_to_waypoint"), 20)
    arrived = await _until(lambda: any(c[2] == "done" for c in since(t0, "go_to_waypoint")), 100)
    walk = since(t0, "go_to_waypoint")
    snap = await link.snapshot(npc, fresh=True)
    standing = talk.orders.standing.get(npc) if talk.orders else None
    check(
        "go_to",
        walked and arrived and standing is not None and standing.kind == "go",
        f"{walk[-1][2] if walk else 'not sent'}, at {snap.npc.waypoint or snap.npc.nearest_waypoint}",
    )
    # it looks around in there before it says what it found
    told = await _until(
        lambda: any(
            e["kind"] == "npc" and e.get("npc") == npc and "Тут" in e.get("text", "") for e in entries
        ),
        90,
    )
    snap = await link.snapshot(npc, fresh=True)
    log.info("companion: %s at %s, told %s", npc, snap.npc.waypoint or snap.npc.nearest_waypoint, told)
    if perception is not None:
        await perception.fresh(npc)
        sight = perception.sight(npc, snap)
        sees = set(sight.targets) if sight is not None else set()
        why = sight.people if sight is not None else "no view"
        if not set(cave) & sees:
            # why not: its senses range, the engine's own sight with and without the eyes' angle
            me = ["inst", npc]
            probe = [f"senses {await link.evaluate(['field', me, 'SENSES_RANGE'])}"]
            near = {o.id: o.distance for o in snap.nearby_npcs}
            for other in cave:
                them = ["inst", other]
                sees_it = await link.evaluate(["call", "NPC_CANSEENPC", me, them])
                free = await link.evaluate(["call", "NPC_CANSEENPCFREELOS", me, them])
                probe.append(f"{other} {near.get(other, -1) / 100:.1f} m: sees {sees_it}, free {free}")
            why += " | " + "; ".join(probe)
        check("sees_cave", bool(set(cave) & sees), why)
    hp = snap.npc.hp
    await asyncio.sleep(6)
    snap = await link.snapshot(npc, fresh=True)
    fought = since(t0, "fight")
    check("cave_quiet", not fought and snap.npc.hp == hp, f"hp {hp} -> {snap.npc.hp}, fights {len(fought)}")
    talk.hear("Кавалорн, за мной!", None, {"via": "scenario"})
    back = await _until(lambda: 0 <= dist() <= 6, 40)
    check("back", back, f"{dist():.1f} m")

    # the bandit's own decision; the gang in the cave would join a fight it hears: out of it for this check
    for other in cave:
        try:
            await link.debug("kill", npc=other)
        except GameError as err:
            log.info("companion: kill %s: %s", other, err)
    await link.debug("teleport", near=bandit, distance=250)  # facing him: he is the hero's focus
    await _until(lambda: 0 <= dist(bandit) <= 5 and 0 <= dist() <= 8, 15)
    if perception is not None:
        view = await perception.fresh(npc)
        focus = view.hero.focus if view is not None and view.hero is not None else None
        sight = perception.sight(npc)
        check(
            "focus",
            focus == bandit and sight is not None and bandit in sight.targets,
            f"hero looks at {focus}",
        )
    # «Погоди.» 20 m from the bandit, then the hero goes up to him alone
    hero = ["inst", "HERO"]
    party = await link.evaluate(["field", ["inst", npc], "AIVAR", 15])
    check("party", party == 1, f"AIV_PARTYMEMBER {party}")
    await link.debug("player_goto", near=bandit, distance=2000)
    await _until(lambda: 0 <= dist() <= 6 and dist(bandit) >= 15, 20)
    talk.hear("Кавалорн, погоди.", None, {"via": "scenario"})
    halted = await _until(
        lambda: talk.orders is not None and getattr(talk.orders.standing.get(npc), "kind", "") == "halt", 5
    )
    await link.debug("teleport", near=bandit, distance=250)
    await _until(lambda: 0 <= dist(bandit) <= 5 and dist() >= 12, 15)
    far = dist()
    exp = await link.evaluate(["field", hero, "EXP"])
    t0 = time.monotonic()
    talk.hear("Эй, ты! А ну отойди с дороги.", bandit, {"via": "scenario"})
    attacked = await _until(lambda: any(e["kind"] == "attack" for e in entries), 20)
    check("attack", attacked, "the scripts' attack" if attacked else "no attack")
    defended = await _until(
        lambda: any(c[3].get("npc", "").upper() == bandit for c in since(t0, "fight")), 30
    )
    started = since(t0, "fight")
    check(
        "defend",
        halted and defended and far >= 12,
        f"halted {halted} {far:.1f} m off, fights {started[0][0] - t0:.1f} s after the words"
        if started
        else f"halted {halted} {far:.1f} m off, no fight",
    )
    down = await _until(lambda: bandit not in talk.people or talk.people[bandit].activity == "down", 90)
    await asyncio.sleep(1)
    gained = await link.evaluate(["field", hero, "EXP"])
    check("xp", down and gained > exp, f"exp {exp} -> {gained}")
    after = await _until(lambda: calls[-1][1] == "follow" and 0 <= dist() <= 8, 30)
    defends = [e for e in entries if e["kind"] == "defend"]
    check("after", down and after, f"bandit down {down}, follows {after}, stood up {len(defends)} time(s)")
    link.act = act  # type: ignore[method-assign]
    return _logged(results)


async def calm_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    bandit: str = "BDT_1013_BANDIT_L",
    gang: tuple[str, ...] = ("BDT_1014_BANDIT_L", "BDT_1015_BANDIT_L"),
) -> dict[str, str]:
    """Talking an attacker down (the user, 2026-10-14: attacked by a bandit, he spoke to him and was told
    nobody heard; he wants to calm one with words). The bandit at the steps decides to attack the hero (its
    model scripted); the hero, looking at no one, says «Воу, воу, ты чего? Спокуха!». Checks:
    - attack: the scripts' attack, the bandit in ZS_ATTACK;
    - addressed: the words go to whoever is at the hero, though not in his focus;
    - calm: its model calls the fight off (scripted) and the effect `calm` goes to the game;
    - stopped: it is out of the attack state;
    - stays_calm: 15 s on it has not fallen on the hero again, and the hero has not lost health.
    The gang in the cave is out of it (a fight's noise would bring them). The hero wears heavy armor."""

    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    entries: list[dict[str, Any]] = []
    sink = dialogue.journal.sink

    def keep(entry: dict[str, Any]) -> None:
        entries.append(entry)
        if sink is not None:
            sink(entry)

    dialogue.journal.sink = keep
    fight = {"goal": "fight", "target": "PLAYER", "attitude": "hostile", "agenda": None}
    calm = {"goal": "none", "target": None, "attitude": "wary", "agenda": None}
    dialogue.llm = _ByNpc(
        {
            "Бандит": _OnWords(
                [
                    ("отойди", FakeStep("Ну всё, сам напросился!", [ToolCall("set_intent", fight)])),
                    ("спокуха", FakeStep("Ладно, ладно. Живи пока.", [ToolCall("set_intent", calm)])),
                ],
                FakeStep("Хм."),
            )
        },
        FakeBackend("Хм.", delay=0),
    )

    def state() -> str:
        e = talk.people.get(bandit)
        return e.state.upper() if e is not None else "?"

    async def hero_hp() -> int:
        snap = await link.snapshot(bandit, fresh=True)
        return snap.player.hp if snap.player is not None and snap.player.hp is not None else -1

    try:
        await link.debug("player_equip", item="ITAR_PAL_H")
    except GameError as err:
        log.info("calm: no armor for the hero: %s", err)
    for other in gang:
        try:
            await link.debug("kill", npc=other)
        except GameError as err:
            log.info("calm: kill %s: %s", other, err)
    await link.debug("teleport", near=bandit, distance=250)
    if not await _until(lambda: bandit in talk.people and talk.people[bandit].distance <= 500, 15):
        return _logged({"near": f"failed ({bandit} is not near the hero)"})

    talk.hear("Эй, ты! А ну отойди с дороги.", bandit, {"via": "scenario"})
    attacked = await _until(
        lambda: any(e["kind"] == "attack" for e in entries) and state() == "ZS_ATTACK", 20
    )
    check("attack", attacked, f"state {state()}")

    hp = await hero_hp()
    talk.hear("Воу, воу, ты чего? Спокуха!", None, {"via": "scenario"})
    to = talk.last[0] if talk.last else None
    check(
        "addressed",
        to is not None and to.id == bandit and to.why == "attacker",
        f"{to.why if to else 'nobody'}",
    )
    calmed = await _until(lambda: any(e["kind"] == "calm" for e in entries), 20)
    check("calm", calmed, next((e["attitude"] for e in entries if e["kind"] == "calm"), "no effect"))
    stopped = await _until(lambda: state() != "ZS_ATTACK", 10)
    check("stopped", stopped, f"state {state()}")
    after = await hero_hp()
    again = False
    t_end = time.monotonic() + 15
    while time.monotonic() < t_end:
        again = again or state() == "ZS_ATTACK"
        await asyncio.sleep(0.5)
    last = await hero_hp()
    check(
        "stays_calm",
        stopped and not again and last >= after,
        f"hero hp {hp} -> {after} -> {last}, state {state()}",
    )
    return _logged(results)


async def ground_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    perception: PerceptionDesk,
    npc: str = "BAU_4300_ADDON_CAVALORN",
    item: str = "ITWR_SATURASFIRSTMESSAGE_ADDON_SEALED",
) -> dict[str, str]:
    """Things lying about (the user, 2026-10-14: he threw Cavalorn's letter down at his feet and said «бери»;
    Cavalorn: «я даже не вижу, куда ты его швырнул»). Cavalorn follows the hero, the hero throws the letter
    down. Checks:
    - sees: it is in Cavalorn's view, with its id;
    - situation: its situation tells it, «На земле: …»;
    - take: told «бери», its model (scripted) calls take_item: it walks up and picks it up;
    - has: the letter is in its inventory and no longer on the ground;
    - follows: it goes with the hero again, and catches up when he is 11 m off."""
    from .executor import Intent

    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    calls: list[tuple[float, str, str]] = []  # (sent, action, status)
    act = link.act

    async def recorded(who: str, action: Any, /, **kw: Any) -> Any:
        t = time.monotonic()
        res = await act(who, action, **kw)
        if who.upper() == npc:
            calls.append((t, action, res.status))
        return res

    link.act = recorded  # type: ignore[method-assign]
    take = ToolCall("take_item", {"item": item})
    dialogue.llm = _ByNpc(
        {"Кавалорн": _OnWords([("бери", FakeStep("Давай сюда.", [take]))], FakeStep("Идём."))},
        FakeBackend("Хм.", delay=0),
    )

    def dist() -> float:
        return talk.people[npc].distance / 100 if npc in talk.people else -1.0

    def things() -> list[str]:
        view = perception.view(npc)
        return [t.item for t in view.items] if view is not None else []

    def since(t0: float, action: str, status: str = "done") -> bool:
        return any(t >= t0 and a == action and s == status for t, a, s in calls)

    try:
        await link.debug("skip_important", near=npc)  # Cavalorn's own greeting would stop the hero
        for _ in range(2):  # the goblins by his fire would beat the hero down
            try:
                await link.debug("kill", instance="YGOBBO_GREEN", near=npc, by=npc)
            except GameError as err:
                log.info("ground: goblin: %s", err)
        await link.debug("player_goto", to=npc, distance=300)
        if not await _until(lambda: 0 <= dist() <= 15, 20):
            return _logged({"roster": f"failed ({npc} is not around the hero: {dist():.1f} m)"})
        dialogue.open(npc, "Кавалорн").intent = Intent("follow_hero", "PLAYER", "friendly")
        talk.hear("Кавалорн, за мной!", None, {"via": "scenario"})
        if not await _until(lambda: since(0, "follow") and 0 <= dist() <= 5, 20):
            return _logged({"follow": f"failed ({dist():.1f} m)"})
        await asyncio.sleep(2)  # it has stopped by him

        await link.debug("player_drop", item=item)
        sees = await _until(lambda: item in things(), 8)
        check("sees", sees, things() or "nothing on the ground")
        sight = perception.sight(npc)
        ground = sight.ground if sight is not None else None
        check("situation", ground is not None and item in ground, ground or "no line")

        t0 = time.monotonic()
        talk.hear("Кавалорн, вот твоё письмо, бери.", None, {"via": "scenario"})
        took = await _until(lambda: since(t0, "take_item") or since(t0, "take_item", "failed"), 45)
        check("take", since(t0, "take_item"), [a for t, a, s in calls if t >= t0] if took else "no pick-up")
        snap = await link.snapshot(npc, fresh=True)
        has = any(i.item == item for i in snap.npc.inventory)
        await asyncio.sleep(1.5)  # a pushed view after it
        check("has", has and item not in things(), f"in hands: {has}, on the ground: {things()}")

        following = await _until(lambda: since(t0, "follow"), 10)
        await link.debug("player_goto", near=npc, distance=1100)
        await _until(lambda: dist() >= 9, 3)
        back = await _until(lambda: 0 <= dist() <= 5, 20)
        check("follows", following and back, f"{dist():.1f} m")
    finally:
        link.act = act  # type: ignore[method-assign]
    return _logged(results)


async def cave_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    whereabouts: Whereabouts,
    npc: str = "BAU_4300_ADDON_CAVALORN",
) -> dict[str, str]:
    """The map of places (docs/npc-tools.md М3): Cavalorn goes with the hero to the steps below Xardas's
    tower and is told «зайди в пещеру», no place named. Checks:
    - follow: he goes with the hero;
    - listed: among the places around him, the bandits' cave, of the kind «пещера», which way from the hero,
      its way in on the steps;
    - where: `where{пещера}` finds the same cave, its way in and what is next to it;
    - go_to: «Кавалорн, зайди в пещеру.» — he goes into the bandits' cave and waits there.
    The model is scripted with LLM=fake (it is sent to XARDAS_BANDITS on «зайди в пещеру»); a live one
    (LLM=chatgpt) chooses the cave itself from the places around."""
    from .executor import Intent

    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    def dist() -> float:
        return talk.people[npc].distance / 100 if npc in talk.people else -1.0

    entries: list[dict[str, Any]] = []
    sink = dialogue.journal.sink

    def keep(entry: dict[str, Any]) -> None:
        entries.append(entry)
        if sink is not None:
            sink(entry)

    dialogue.journal.sink = keep
    live = dialogue.llm.name != "fake"
    if not live:
        go = ToolCall("go_to", {"place": "XARDAS_BANDITS"})
        dialogue.llm = _ByNpc(
            {
                "Кавалорн": _OnWords(
                    [("зайди в пещеру", FakeStep("Иду.", [go])), ("пришёл первым", FakeStep("Тут пусто."))],
                    FakeStep("Да."),
                )
            },
            FakeBackend("Хм.", delay=0),
        )
    try:
        await link.debug("player_equip", item="ITAR_PAL_H")  # the bandits by the steps may fall on him
    except GameError as err:
        log.info("cave: no armor for the hero: %s", err)
    await link.debug("skip_important", near=npc)  # Cavalorn's own greeting would stop the hero
    for _ in range(2):  # the goblins by his fire
        try:
            await link.debug("kill", instance="YGOBBO_GREEN", near=npc, by=npc)
        except GameError as err:
            log.info("cave: goblin: %s", err)
    await link.debug("player_goto", to=npc, distance=300)
    if not await _until(lambda: 0 <= dist() <= 15, 20):
        return _logged({"roster": f"failed ({npc} is not around the hero: {dist():.1f} m)"})
    dialogue.open(npc, "Кавалорн").intent = Intent("follow_hero", "PLAYER", "friendly")
    talk.hear("Кавалорн, за мной!", None, {"via": "scenario"})
    following = await _until(
        lambda: talk.orders is not None and talk.orders.companion(npc) and dist() <= 5, 20
    )
    check("follow", following, f"{dist():.1f} m")

    # the hero walks to the steps below the tower; Cavalorn comes after him
    book = whereabouts.book
    steps = book.get("XARDAS_STAIRS") if book is not None else None
    if steps is None or steps.center is None:
        return _logged({**results, "book": "failed (no XARDAS_STAIRS in the book of places)"})
    await link.debug("player_goto", waypoint="NW_XARDAS_STAIRS_02")

    middle = steps.center

    def by_steps(who: Snapshot | None) -> bool:
        return who is not None and math.dist(who.npc.pos, middle) < 1500

    there = await _until_async(lambda: link.snapshot(npc, fresh=True), by_steps, 40)
    await asyncio.sleep(1)
    await link.snapshot(npc, fresh=True)
    log.info("cave: %s by the steps: %s, %.1f m from the hero", npc, there, dist())
    situation = talk.orders.situation(npc) if talk.orders is not None else None
    line = next((x for x in (situation or "").splitlines() if x.startswith("Места рядом")), "")
    cave = next((part for part in line.split("; XARDAS") if "пещера бандитов" in part), "")
    check(
        "listed",
        "пещера бандитов" in cave
        and ": пещера," in cave
        and any(side in cave for side in SIDES.values())
        and "вход — на ступенях" in line,
        line or "no places around",
    )
    answer = whereabouts.where(npc, "пещера")
    check(
        "where",
        answer.startswith("XARDAS_BANDITS — ") and "вход — на ступенях" in answer and "Рядом:" in answer,
        answer.replace("\n", " / "),
    )

    # «зайди в пещеру», no place named
    t0 = len(entries)
    talk.hear("Кавалорн, зайди в пещеру.", None, {"via": "scenario"})

    def sent() -> list[dict[str, Any]]:
        return [e for e in entries[t0:] if e.get("kind") == "action" and e.get("action") == "go_to"]

    went = await _until(sent, 60)
    where = sent()[0].get("arguments", {}).get("place") if went else None
    standing = talk.orders.standing.get(npc) if talk.orders is not None else None

    def place_of(snap: Snapshot) -> str | None:
        # where it stands: its nearest point (`waypoint` is where it walks to)
        place = book.of(snap.npc.nearest_waypoint) if book is not None else None
        return place.id if place is not None else None

    inside = await _until_async(
        lambda: link.snapshot(npc, fresh=True), lambda snap: place_of(snap) == "XARDAS_BANDITS", 120
    )
    snap = await link.snapshot(npc, fresh=True)
    check(
        "go_to",
        where == "XARDAS_BANDITS" and standing is not None and standing.kind == "go" and inside,
        f"go_to {where or 'not called'}, at {snap.npc.nearest_waypoint} ({place_of(snap)})",
    )
    said = [e.get("text", "") for e in entries[t0:] if e.get("kind") == "npc" and e.get("npc") == npc]
    log.info("cave: %s said %s", npc, said)
    return _logged(results)


async def knows_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    knowledge: Knowledge,
    lore: Lore,
    whereabouts: Whereabouts,
    npc: str = "BAU_4300_ADDON_CAVALORN",
) -> dict[str, str]:
    """What an npc knows of where people are (docs/npc-tools.md М4): Cavalorn by his fire knows the bandits
    who robbed him sit in the cave below the steps, though the hero has told him nothing. Checks:
    - known: among his facts the story lets him tell now, one of the bandits' cave (XARDAS_BANDITS);
    - situation: the hero asks «что тут поблизости опасного?»; the fact is in the situation of his turn;
    - where: `where{пещера}` tells it too;
    - said (live model only): his answer speaks of the bandits or the cave.
    The model is scripted with LLM=fake; LLM=chatgpt answers itself."""
    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    entries: list[dict[str, Any]] = []
    sink = dialogue.journal.sink

    def keep(entry: dict[str, Any]) -> None:
        entries.append(entry)
        if sink is not None:
            sink(entry)

    dialogue.journal.sink = keep
    live = dialogue.llm.name != "fake"
    model = _Recorded(dialogue.llm if live else FakeBackend("Держи ухо востро.", delay=0))
    dialogue.llm = model
    await link.debug("skip_important", near=npc)  # Cavalorn's own greeting would stop the hero
    for _ in range(2):  # the goblins by his fire
        try:
            await link.debug("kill", instance="YGOBBO_GREEN", near=npc, by=npc)
        except GameError as err:
            log.info("knows: goblin: %s", err)
    await link.debug("player_goto", to=npc, distance=300)
    if not await _until(lambda: npc in talk.people and talk.people[npc].distance <= 600, 20):
        return _logged({"roster": f"failed ({npc} is not around the hero)"})

    await knowledge.prepare(npc)
    cave = [f for f in lore.known(npc) if f.place == "XARDAS_BANDITS"]
    check("known", bool(cave), cave[0].ru if cave else [f.ru for f in lore.known(npc)])

    t0 = len(entries)
    talk.hear("Кавалорн, что тут поблизости опасного?", npc, {"via": "scenario"})
    answered = await _until(
        lambda: any(e.get("kind") == "npc" and e.get("npc") == npc for e in entries[t0:]), 60
    )
    said = " ".join(e.get("text", "") for e in entries[t0:] if e.get("kind") == "npc" and e.get("npc") == npc)
    shown = bool(cave) and any(cave[0].ru in asked for asked in model.asked)
    check("situation", answered and shown, said or "no answer")
    answer = whereabouts.where(npc, "пещера")
    check("where", "Ты знаешь:" in answer and bool(cave) and cave[0].ru in answer, answer.split("\n")[-1])
    if live:
        words = roots(said)
        check("said", any(w.startswith(("бандит", "пещер", "дыр")) for w in words), said)
    return _logged(results)


async def lead_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    deals: Deals,
    guide: Guide,
    npc: str = "BAU_954_MALETH",
    to: str = "FARM1_MILL",
) -> dict[str, str]:
    """The npc leads the hero (docs/npc-tools.md, wave 2 step 4: `lead`): «Малет, отведи меня к мельнице».
    Checks:
    - offered: a deal with the way there in its window («Проводит: мельница фермы Лобарта»);
    - sets_off: the hero says yes (Y), Malet sets off for the mill;
    - waits: the hero is put back where Malet started: it stops and waits for him; he comes up, it goes on;
    - there: the hero after it, it gets to the mill and says so; a fact `led` (arrived).
    How fast it walks is logged (`pace`). The model is scripted with LLM=fake; LLM=chatgpt answers itself."""
    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    entries: list[dict[str, Any]] = []
    sink = dialogue.journal.sink

    def keep(entry: dict[str, Any]) -> None:
        entries.append(entry)
        if sink is not None:
            sink(entry)

    dialogue.journal.sink = keep
    if dialogue.llm.name == "fake":
        offer = {"npc_gives": [], "hero_gives": [], "lead": to}
        dialogue.llm = _OnWords(
            [
                ("отведи меня", FakeStep("Пойдём, покажу.", [ToolCall("propose_deal", offer, "lead1")])),
                ("ты ведёшь его", FakeStep("Иди за мной.")),
                ("Герой отстал", FakeStep("Эй, не отставай!")),
                ("Вы пришли", FakeStep("Вот и мельница.")),
            ],
            FakeStep("Да."),
        )
    book = guide.book
    mill = book.get(to) if book is not None else None
    if mill is None or mill.center is None:
        return _logged({"book": f"failed (no {to} in the book of places)"})
    await link.debug("time", hour=10, minute=0)
    await link.debug("skip_important", near=npc)  # Malet's own dialog would stop the hero at once
    await link.debug("teleport", near=npc, distance=300)
    if not await _until(lambda: npc in talk.people and talk.people[npc].distance <= 600, 20):
        him = await link.snapshot(npc, fresh=True)
        around = sorted((round(e.distance / 100), e.id) for e in talk.people.values())[:5]
        return _logged({"roster": f"failed ({npc} at {him.npc.pos}, the hero at "
                        f"{him.player.pos if him.player else '?'}; around him {around})"})  # fmt: skip

    talk.hear("Малет, отведи меня к мельнице.", npc, {"via": "scenario"})
    shown = await _until(lambda: npc in deals.open, 60)
    deal = deals.open.get(npc)
    check("offered", shown and deal is not None and deal.lead is not None and deal.lead.place.id == to,
          deal.terms() if deal is not None else "no deal")  # fmt: skip
    if deal is None:
        return _logged(results)
    await asyncio.sleep(1.2)  # Y waits until the terms have stood a moment
    pressed = await link.press_deal()
    started = await _until(lambda: npc in guide.leads and guide.leads[npc].state == "going", 30)
    start = await link.snapshot(npc, fresh=True)
    away = await _until_async(
        lambda: link.snapshot(npc, fresh=True), lambda snap: math.dist(snap.npc.pos, start.npc.pos) > 800, 30
    )
    check("sets_off", pressed == "sent" and started and away, f"Y: {pressed}")

    # where it is on the way, for the log, and how fast it goes while it goes (not while it waits)
    speeds: list[float] = []

    async def trace() -> None:
        last: tuple[float, tuple[float, float, float]] | None = None
        while True:
            snap = link.snapshots.get(npc)
            lead = guide.leads.get(npc)
            now = time.monotonic()
            if snap is not None and lead is not None and lead.state == "going":
                if last is not None and (v := math.dist(snap.npc.pos, last[1]) / 100 / (now - last[0])) > 0.3:
                    speeds.append(v)
                last = (now, snap.npc.pos)
            else:
                last = None
            if snap is not None and snap.player is not None:
                log.info("lead: %s at %s (%s), %.0f m from %s, the hero %.0f m off, %s", npc,
                         [round(x) for x in snap.npc.pos], snap.npc.nearest_waypoint,
                         math.dist(snap.npc.pos, mill.center) / 100, to, snap.player.distance / 100,
                         lead.state if lead else "over")  # fmt: skip
            await asyncio.sleep(2)

    tracing = asyncio.create_task(trace())
    # the hero is back where it set off: it stops and waits for him; he comes up, it goes on
    await _until_async(
        lambda: link.snapshot(npc, fresh=True), lambda snap: math.dist(snap.npc.pos, start.npc.pos) > 1500, 60
    )
    await link.debug("player_goto", waypoint=start.npc.nearest_waypoint or start.npc.waypoint or "")
    waited = await _until(lambda: npc in guide.leads and guide.leads[npc].state == "waiting", 20)
    called = any(e.get("kind") == "npc" and e.get("npc") == npc for e in entries[-20:])
    await link.debug("teleport", near=npc, distance=300)
    on = await _until(lambda: npc in guide.leads and guide.leads[npc].state == "going", 20)
    check("waits", waited and on, f"waited {waited}, called {called}, went on {on}")

    # he goes after it: put behind it now and then, as a player who follows
    async def after() -> None:
        while npc in guide.leads:
            if guide.leads[npc].state == "waiting":
                await link.debug("teleport", near=npc, distance=300)
            await asyncio.sleep(1)

    follow = asyncio.create_task(after())
    try:
        await _until(lambda: npc not in guide.leads, 180)
    finally:
        follow.cancel()
        tracing.cancel()
    led = [f for f in guide.chronicle.facts("led") if f.get("npc") == npc]
    snap = await link.snapshot(npc, fresh=True)
    there = book.of(snap.npc.nearest_waypoint) if book is not None else None
    pace = sorted(speeds)[len(speeds) // 2] if speeds else 0.0
    results["pace"] = f"done ({pace:.1f} m/s on the move, the hero runs)"
    await asyncio.sleep(4)
    said = [e.get("text", "") for e in entries if e.get("kind") == "npc" and e.get("npc") == npc]
    check(
        "there",
        bool(led) and led[-1].get("how") == "arrived" and math.dist(snap.npc.pos, mill.center) < 2000,
        f"{led[-1].get('how') if led else 'no fact'}, at {snap.npc.nearest_waypoint} "
        f"({there.id if there else '?'}), {math.dist(snap.npc.pos, mill.center) / 100:.0f} m from {to}",
    )
    log.info("lead: %s said %s", npc, said)
    return _logged(results)


async def meet_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    deals: Deals,
    meetings: Meetings,
    *,
    missed: bool = False,
    npc: str = "BAU_954_MALETH",
    to: str = "FARM1_WOLF",
) -> dict[str, str]:
    """A meeting with the hero (docs/npc-tools.md, wave 2 step 4: `meet`): at 19:00 the hero agrees with
    Malet to meet at dawn by the field in front of the city gates. Checks:
    - offered: a deal with the meeting in its window;
    - agreed: Y; the meeting is the desk's;
    - there: the clock leaps past when Malet sets off, as when the hero sleeps: the engine puts everyone at
      their routine's spot, and Malet is put at the meeting at once (`start_activity.place`);
    - kept: the hero comes, a fact `meet_kept`, and Malet speaks first (`first`).
    With `missed` (SCENARIO=meet_missed) the clock goes to a little before he sets off: he walks there himself
    and stands there by the hour. Then the hero does not come: the clock past its end, his word is broken
    (`broken`, promise meet), Malet goes back to his routine (`routine`), and when the hero comes up to him
    the missed meeting is in front of him (`reproach`; live: in his words).
    The model is scripted with LLM=fake; LLM=chatgpt answers itself."""
    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    entries: list[dict[str, Any]] = []
    sink = dialogue.journal.sink

    def keep(entry: dict[str, Any]) -> None:
        entries.append(entry)
        if sink is not None:
            sink(entry)

    def said(since: int) -> list[str]:
        return [e.get("text", "") for e in entries[since:] if e.get("kind") == "npc" and e.get("npc") == npc]

    dialogue.journal.sink = keep
    live = dialogue.llm.name != "fake"
    await link.debug("time", hour=19, minute=0)
    await link.debug("skip_important", near=npc)  # Malet's own dialog would stop the hero at once
    await link.debug("teleport", near=npc, distance=300)
    if not await _until(lambda: npc in talk.people and talk.people[npc].distance <= 600, 20):
        return _logged({"roster": f"failed ({npc} is not around the hero)"})
    snap = await link.snapshot(npc, fresh=True)
    tomorrow = snap.world.day + 2  # as the npc sees days: from 1
    if not live:
        dawn = {"where": to, "day": tomorrow, "hour": 5, "minute": 0, "hours": 2}
        offer = ToolCall("propose_deal", {"meet": dawn}, "meet1")
        dialogue.llm = _OnWords(
            [
                ("встретимся", FakeStep("Ладно, на рассвете у поля перед воротами.", [offer])),
                ("сделка состоялась", FakeStep("Договорились. Не опаздывай.")),
                ("пришёл на встречу", FakeStep("Пришёл всё-таки. Хорошо.")),
                ("привет", FakeStep("Ты где был на рассвете? Я тебя ждал.")),
            ],
            FakeStep("Да."),
        )
    model = _Recorded(dialogue.llm)
    dialogue.llm = model

    talk.hear(
        "Малет, давай встретимся завтра на рассвете у поля перед городскими воротами. Мне нужно с тобой "
        "поговорить без лишних ушей.",
        npc,
        {"via": "scenario"},
    )
    shown = await _until(lambda: npc in deals.open and deals.open[npc].meet is not None, 60)
    if not shown:  # a live Malet asks what it is about first
        talk.hear(
            "Про бандитов на дороге к городу: я знаю, где они прячутся. Здесь говорить не буду — на рассвете "
            "у поля перед воротами.",
            npc,
            {"via": "scenario"},
        )
        shown = await _until(lambda: npc in deals.open and deals.open[npc].meet is not None, 90)
    if not shown and live:  # it agrees in words and asks the hour first («В шесть часов подойдёт?»)
        talk.hear("Да, в шесть подойдёт. Договорились.", npc, {"via": "scenario"})
        shown = await _until(lambda: npc in deals.open and deals.open[npc].meet is not None, 90)
    deal = deals.open.get(npc)
    check("offered", shown, deal.terms() if deal is not None else "no deal")
    if not shown or deal is None or deal.meet is None:
        return _logged(results)
    await asyncio.sleep(1.2)  # Y waits until the terms have stood a moment
    pressed = await link.press_deal()

    def meeting() -> Any:
        return next((m for m in meetings.meetings.values() if m.npc == npc and m.deal == deal.id), None)

    agreed = await _until(lambda: meeting() is not None, 20)
    m = meeting()
    check("agreed", pressed == "sent" and agreed, f"Y: {pressed}, {m.term.text() if m else 'no meeting'}")
    if m is None:
        return _logged(results)

    # the clock past when it sets off, near enough the hour for him to come (it is put there), or a little
    # before it sets off (it walks there by the hour)
    before = m.term.start - m.lead_min - 3 if missed else max(m.term.start - m.lead_min, m.term.start - 25)
    await link.debug("time", hour=before % GAME_DAY // 60, minute=before % 60)
    there = await _until(lambda: m.state in ("there", "kept"), 300)
    snap = await link.snapshot(npc, fresh=True)
    now = _game_minute(snap)
    far = math.dist(snap.npc.pos, m.term.way.pos) / 100
    detail = f"{m.state} at {clock(now)}, {far:.0f} m from it"
    check("there", there and far < 10 and now <= m.term.start, detail)
    if not there:
        return _logged(results)

    if not missed:
        t0 = len(entries)
        await link.debug("teleport", near=npc, distance=600)
        kept = await _until(lambda: m.state == "kept", 30)
        facts = [f for f in meetings.chronicle.facts("meet_kept") if f.get("meet") == m.id]
        check("kept", kept and bool(facts), m.state)
        spoke = await _until(lambda: bool(said(t0)), 60)
        check("first", spoke and talk.partner is not None and talk.partner.npc == npc, said(t0) or "silent")
        return _logged(results)

    # he does not come: the clock past its end
    after = m.term.end + 10
    await link.debug("time", hour=after % GAME_DAY // 60, minute=after % 60)
    broken = await _until(lambda: m.state == "missed", 30)
    facts = [f for f in meetings.chronicle.facts("broken") if f.get("meet") == m.id]
    check("broken", broken and bool(facts) and facts[0].get("promise") == "meet", m.state)
    left = await _until_async(
        lambda: link.snapshot(npc, fresh=True), lambda s: math.dist(s.npc.pos, m.term.way.pos) > 800, 180
    )
    snap = await link.snapshot(npc, fresh=True)
    check("routine", left, f"{math.dist(snap.npc.pos, m.term.way.pos) / 100:.0f} m from the meeting")
    await link.debug("skip_important", near=npc)
    await link.debug("teleport", near=npc, distance=300)
    await _until(lambda: npc in talk.people and talk.people[npc].distance <= 600, 20)
    t0, asked = len(entries), len(model.asked)
    talk.hear("Малет, привет.", npc, {"via": "scenario"})
    await _until(lambda: bool(said(t0)), 60)
    words = " ".join(said(t0))
    shown_to_it = any("не пришёл на встречу" in a for a in model.asked[asked:])
    low = words.lower()
    spoke_of = any(w in low for w in ("встреч", "ждал", "прожд", "рассвет", "договар", "не пришёл"))
    check(
        "reproach",
        shown_to_it and (not live or spoke_of),
        f"in its situation: {shown_to_it}; {words or 'silent'}",
    )
    return _logged(results)


def _game_minute(snap: Snapshot) -> int:
    return snap.world.day * GAME_DAY + snap.world.hour * 60 + snap.world.minute


async def hearing_scenario(
    link: GameLink,
    talk: TalkDesk,
    npc: str = "BAU_4300_ADDON_CAVALORN",
    bandit: str = "BDT_1013_BANDIT_L",
    brago: str = "BDT_1014_BANDIT_L",
) -> dict[str, str]:
    """Who hears the hero (the user, 2026-10-14: at Cavalorn's fire his words went to a bandit he thought
    was in the cave behind the rock). The hero by Cavalorn's fire. Checks:
    - free: nothing between him and Cavalorn, his voice goes straight on (`heard` = `distance`);
    - line: the bandit by the steps below the tower, 36 m off, is in a straight line from him (the words
      that went to it on 2026-10-14 went by its name, not through the rock);
    - words: «…помочь тебе с бандитами», looking at Cavalorn, are to Cavalorn, not to the bandit;
    - cave: the hero in the bandits' cave by Brago: the bandit by the steps outside does not hear him;
    - round: the hero in the passage to the cave: his voice goes round the bend to Brago, farther than
      straight on."""

    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    def at(who: str) -> str:
        e = talk.people.get(who)
        return f"{e.distance / 100:.1f} m, heard {e.earshot / 100:.1f} m" if e is not None else "not around"

    await link.debug("skip_important", near=npc)  # Cavalorn's own greeting would stop the hero
    for _ in range(2):  # the goblins by his fire
        try:
            await link.debug("kill", instance="YGOBBO_GREEN", near=npc, by=npc)
        except GameError as err:
            log.info("hearing: goblin: %s", err)
    await link.debug("player_goto", to=npc, distance=150)
    near = await _until(
        lambda: npc in talk.people and talk.people[npc].distance <= 500 and bandit in talk.people, 20
    )
    if not near:
        return _logged({"roster": f"failed ({npc}: {at(npc)}, {bandit}: {at(bandit)})"})
    await asyncio.sleep(1.5)  # a roster or two where he stands
    log.info(
        "hearing: %s",
        [
            (e.id, round(e.distance), round(e.earshot))
            for e in sorted(talk.people.values(), key=lambda e: e.distance)
        ],
    )
    me, him = talk.people[npc], talk.people[bandit]
    check("free", me.heard is not None and abs(me.earshot - me.distance) < 1, at(npc))
    check("line", him.heard is not None and abs(him.earshot - him.distance) < 1, at(bandit))
    talk.hear("Можешь отдать за моё обещание помочь тебе с бандитами?", npc, {"via": "scenario"})
    to = talk.last[0] if talk.last is not None else None
    check("words", to is not None and to.id == npc, f"to {to.id if to else None} ({to.why if to else ''})")

    await link.debug("skip_important", near=brago)
    await link.debug("player_goto", to=brago, distance=300)
    inside = await _until(lambda: brago in talk.people and talk.people[brago].distance <= 600, 20)
    await asyncio.sleep(1.5)
    log.info(
        "hearing in the cave: %s",
        [
            (e.id, round(e.distance), round(e.earshot))
            for e in sorted(talk.people.values(), key=lambda e: e.distance)
        ],
    )
    out = talk.people.get(bandit)
    walled = out is not None and out.earshot > out.distance + 100
    check("cave", inside and walled, f"{brago}: {at(brago)}, {bandit}: {at(bandit)}")
    await link.debug("player_goto", waypoint="NW_XARDAS_BANDITS_02")  # in the passage, the cave round a bend
    await asyncio.sleep(1.5)
    log.info(
        "hearing in the passage: %s",
        [
            (e.id, round(e.distance), round(e.earshot))
            for e in sorted(talk.people.values(), key=lambda e: e.distance)
        ],
    )
    e = talk.people.get(brago)
    check("round", e is not None and e.distance + 100 < e.earshot < 20000, at(brago))
    return _logged(results)


async def _at_the_steps(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, npc: str, brago: str, bandit: str, steps: str
) -> str | None:
    """The hero with Cavalorn at the bandit by the steps below Xardas's tower, Brago and his mate come up
    from the cave; what went wrong, if anything. Cavalorn goes with the hero on his own feet: put elsewhere,
    his routine takes him back."""
    from .executor import Intent

    def dist(who: str) -> float:
        return talk.people[who].distance / 100 if who in talk.people else -1.0

    try:
        await link.debug("player_equip", item="ITAR_PAL_H")
    except GameError as err:
        log.info("at the steps: no armor for the hero: %s", err)
    await link.debug("skip_important", near=npc)
    for _ in range(2):  # the goblins by his fire
        try:
            await link.debug("kill", instance="YGOBBO_GREEN", near=npc, by=npc)
        except GameError as err:
            log.info("at the steps: goblin: %s", err)
    await link.debug("player_goto", to=npc, distance=300)
    if not await _until(lambda: 0 <= dist(npc) <= 15, 20):
        return f"{npc} is not around the hero: {dist(npc):.1f} m"
    dialogue.open(npc, "Кавалорн").intent = Intent("follow_hero", "PLAYER", "friendly")
    talk.hear("Кавалорн, за мной!", None, {"via": "scenario"})
    await _until(lambda: 0 <= dist(npc) <= 5, 20)
    await link.debug("player_goto", to=steps, distance=400)
    await link.debug("npc_goto", npc=brago, to=steps, distance=300)
    await link.debug("npc_goto", npc=bandit, to=steps, distance=500)
    everyone = (npc, brago, bandit, steps)
    if not await _until(lambda: all(0 <= dist(n) <= 12 for n in everyone) and dist(npc) <= 6, 30):
        return ", ".join(f"{n} {dist(n):.1f} m" for n in everyone)
    await asyncio.sleep(2)
    return None


def _overlaps(
    dialogue: DialogueManager, started: list[tuple[float, str, str]], names: dict[str, str]
) -> tuple[list[str], list[str]]:
    """The voices in the order they were first heard, and where a line started before the line of another
    before it was over (one may speak again later: Cavalorn, Brago, Cavalorn)."""
    order = sorted({w for _, w, _ in started}, key=lambda n: min(t for t, w, _ in started if w == n))
    out = []
    for (ta, a, la), (tb, b, _) in pairwise(sorted(started)):
        end = ta + dialogue._audio.get(la, line_duration(dialogue._lines.get(la, ("", None))[0]))
        if a != b and tb < end - 0.15:
            out.append(f"{names.get(b, b)} {end - tb:.1f} s over {names.get(a, a)}")
    return order, out


async def chests_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    chests: ChestDesk | None,
    npc: str = "BDT_1014_BANDIT_L",
    name: str = "Браго",
    item: str = "ITRW_BOW_L_01",
    ask: str = "Браго, продай мне лук.",
) -> dict[str, str]:
    """Npc's chests (docs/npc-tools.md «Сундуки»; the user, 2026-10-14: Brago should remember what lies in his
    chest, and go to take it out). The hero goes up to Brago in the bandits' cave below Xardas's tower and,
    unseen, takes the gold out of the chest there; then asks Brago for a bow. Brago's model is scripted
    (LLM=fake); with a real one (`chests_lobart`: Lobart, his farmer's clothes in his chest) whether it goes
    to the chest is the model's own call. Checks:
    - chest: the bridge counts the cave's chest as Brago's (nobody owns it, it stands by his routine), with
      a bow in it;
    - knows: Brago's situation tells what lies in it, as he knows: the bow and the gold too;
    - fetched: told «продай лук», his model calls `chest`: he walks to the chest, opens it and takes the bow
      out; the bow is in his hands and no longer in the chest;
    - missing: having looked, he knows the gold is gone;
    - back: back from the chest, he goes on talking to the hero."""
    await link.wait_world()
    results: dict[str, str] = {}
    if chests is None:
        return _logged({"chests": "failed (run with --chests)"})

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    bow, gold = item, "ITMI_GOLD"
    asked: list[str] = []  # the situation of each of Brago's requests
    cues: list[str] = []  # ...and what each was about
    fetch = ToolCall(CHEST, {"take": [{"item": bow, "count": 1}], "put": [], "chest": None})
    # scripted (LLM=fake), or the real model (LLM=chatgpt): then what it does is its own call, to be read
    inner: Any = dialogue.llm
    if inner.name == "fake":
        say = FakeStep("В сундуке лежит. Сейчас принесу.", [fetch])
        inner = _ByNpc({name: _OnWords([("продай", say)], FakeStep("Ну."))}, FakeBackend("Хм.", delay=0))

    class Recorded:
        name = inner.name
        model = getattr(inner, "model", None)

        def __getattr__(self, attr: str) -> Any:
            return getattr(inner, attr)

        def stream(self, **kw: Any) -> AsyncIterator[LLMEvent]:
            if f"Ты — {name}" in kw["instructions"]:
                msgs = kw["messages"]
                last = msgs[-1] if msgs else None
                cues.append(last.content if isinstance(last, Message) else "")
                asked.append(
                    "\n".join(m.content for m in msgs if isinstance(m, Message) and m.role == "developer")
                )
            return cast(AsyncIterator[LLMEvent], inner.stream(**kw))

    dialogue.llm = Recorded()  # type: ignore[assignment]

    def dist() -> float:
        return talk.people[npc].distance / 100 if npc in talk.people else -1.0

    try:
        await link.debug("player_equip", item="ITAR_PAL_H")
    except GameError as err:
        log.info("chests: no armor for the hero: %s", err)
    await link.debug("skip_important", near=npc)
    await link.debug("player_goto", to=npc, distance=300)
    if not await _until(lambda: 0 <= dist() <= 8, 20):
        return _logged({"roster": f"failed ({npc} {dist():.1f} m from the hero)"})

    found = await link.npc_chests(npc)
    log.info(
        "chests: %s's: %s",
        npc,
        [(c.id, c.why, c.waypoint, [(i.item, i.count) for i in c.items]) for c in found],
    )
    cave = next((c for c in found if any(i.item == bow for i in c.items)), None)
    check("chest", cave is not None, [(c.id, c.why, c.waypoint) for c in found] or "none")
    if cave is None:
        return _logged(results)
    await link.debug("chest_take", chest=cave.id, item=gold, count=1000)
    log.info("chests: the hero took the gold out of %s unseen", cave.id)

    talk.hear(ask, npc, {"via": "scenario"})
    await _until(lambda: bool(asked), 30)
    situation = asked[0] if asked else ""
    line = next((s for s in situation.split("\n") if s.startswith(tuple(OWNED.values()))), "")
    check("knows", bow in line and gold in line, line or situation[-300:])

    conv = dialogue.conversations.get(npc)

    def note() -> str:
        # the body's note of the walk (not its own words «схожу к сундуку»)
        done = ("[Ты сходил к своему сундуку", "[Не вышло", "[Тело не смогло")
        return next((m.content for m in conv.history if m.content.startswith(done)), "") if conv else ""

    async def where() -> None:  # how far it is from the hero while it goes, for the log
        while True:
            await asyncio.sleep(3)
            snap = link.snapshots.get(npc)
            log.info(
                "chests: %s %.1f m from the hero, at %s",
                npc,
                dist(),
                snap.npc.nearest_waypoint if snap else "?",
            )

    watch = asyncio.create_task(where())
    # there and back: a chest across a house takes a minute and a half (Lobart's)
    await _until(lambda: bool(note()) or chests._going, 5)
    await _until(lambda: bool(note()) or chests._going or not dialogue.speaking(npc), 40)
    if not note() and inner.name != "fake" and not chests._going:  # a real one names its price first
        talk.hear("Да, беру, по рукам.", npc, {"via": "scenario"})
        await _until(lambda: bool(chests._going), 30)
    await _until(lambda: bool(note()), 180)
    snap = await link.snapshot(npc, fresh=True)
    after = next((c for c in await link.npc_chests(npc) if c.id == cave.id), None)
    in_hands = any(i.item == bow for i in snap.npc.inventory)
    still = after is not None and any(i.item == bow for i in after.items)
    check(
        "fetched",
        in_hands and not still and "достал" in note(),
        f"in hands {in_hands}, in the chest {still}: {note()}",
    )
    check("missing", "пропало" in note() and "Золото" in note(), note())
    await _until(lambda: BACK in cues, 20)
    await _until(lambda: not dialogue.speaking(npc), 30)  # what it says back from the chest, for the log
    watch.cancel()
    check("back", BACK in cues and 0 <= dist() <= 5, f"{dist():.1f} m from the hero; {cues}")
    return _logged(results)


async def group_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    npc: str = "BAU_4300_ADDON_CAVALORN",
    brago: str = "BDT_1014_BANDIT_L",
    bandit: str = "BDT_1015_BANDIT_L",
    steps: str = "BDT_1013_BANDIT_L",
) -> dict[str, str]:
    """One word at a time (step B2 of docs/npc-tools.md; the user, 2026-10-13: talking to two bandits and
    Cavalorn, all three answered at once). The hero, Cavalorn with him, at the bandits' cave with Brago, his
    mate and the bandit from the steps; the models are scripted (LLM=fake), lines voiced (TTS=fake). The hero
    says «Пацаны, здорово!» while Cavalorn is still his conversation partner (2026-10-18: the greeting
    wrongly went to the companion); at once three others ask for words, as the fast loop's escalations
    do. Checks:
    - current_companion: a conversation with the following companion is open before the greeting;
    - first: Brago, who leads them, answers first;
    - bandits_answer: the first line is actually heard from Brago;
    - one_at_a_time: no voice starts before the one before it has been heard;
    - heard: the second answers with the first's words in mind;
    - three: three voices to the hero's phrase, the fourth says nothing.
    Then (step B4) he asks Cavalorn whether they robbed him; Cavalorn answers and calls Brago out by name:
    - called: that line is said to Brago;
    - answers_him: Brago answers next, to him, not to the hero, once he has been heard;
    - look: while each says it, the two look at each other (the snapshot's `gaze`, v27)."""
    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    names = {npc: "Кавалорн", brago: "Браго", bandit: "Бандит", steps: "Бандит"}
    models = {
        brago: FakeBackend("Чего надо? Говори быстро.", delay=0.02, first=0.5),
        bandit: FakeBackend("Ты кто такой вообще?", delay=0.02, first=0.5),
        npc: FakeBackend("Спокойно, он со мной.", delay=0.02, first=0.5),
        steps: FakeBackend("А я его знаю!", delay=0.02, first=0.5),
    }
    # whose instructions: «Ты — <the card's first words>» (the two bandits share a name)
    keys = {
        brago: "Браго",
        bandit: "Бандит, который встречает",
        npc: "Кавалорн",
        steps: "Бандит, пронырливый",
    }
    dialogue.llm = _ByNpc({keys[n]: m for n, m in models.items()}, FakeBackend("Хм.", delay=0))
    started: list[tuple[float, str, str]] = []  # (when, npc, line)
    link.listeners.append(
        lambda m: (
            started.append((time.monotonic(), m.npc.upper(), m.line)) if isinstance(m, LineStarted) else None
        )
    )

    if (failed := await _at_the_steps(link, dialogue, talk, npc, brago, bandit, steps)) is not None:
        return _logged({"roster": f"failed ({failed})"})

    # The plural address overrides a companion only with other people in front of the hero.
    await link.debug("teleport", near=brago, distance=500)
    if not await _until(
        lambda: brago in talk.people and talk.people[brago].in_view
        and npc in talk.people and talk.people[npc].earshot <= 1000, 15,
    ):
        return _logged({"roster": "failed (bandits not in view or companion out of earshot)"})
    talk.end("scenario")
    opened = talk.begin(npc, names[npc])
    check(
        "current_companion",
        opened
        and talk.partner is not None
        and talk.partner.npc == npc
        and talk.orders is not None
        and talk.orders.companion(npc),
        talk.partner,
    )
    t0 = time.monotonic()
    talk.hear("Пацаны, здорово!", None, {"via": "scenario"})
    first = talk.last[0] if talk.last else None
    check("first", first is not None and first.id == brago and first.why == "group", first)
    for who in (bandit, npc, steps):  # as the fast loop's escalations would ask for words
        dialogue.open(who, names[who])
        dialogue.event_turn(who, "[Событие: герой сказал рядом: «Пацаны, здорово!»]", overlay=who != npc)
    quiet = await _until(
        lambda: time.monotonic() - t0 > 3 and not any(dialogue.speaking(n) for n in names), 40
    )
    voices = [n for n in names if models[n].calls]
    # each voice starts after the one before it has been heard: its lines' starts and lengths
    order, overlaps = _overlaps(dialogue, [s for s in started if s[0] >= t0], names)
    check("bandits_answer", bool(order) and order[0] == brago, [names[n] for n in order])
    check("one_at_a_time", quiet and len(order) >= 2 and not overlaps, overlaps or [names[n] for n in order])
    second = models[order[1]].calls[0][1] if len(order) > 1 and models[order[1]].calls else []
    heard = "Ты слышал, как Браго говорит герою" in " ".join(str(getattr(m, "content", "")) for m in second)
    check("heard", heard, names[order[1]] if len(order) > 1 else "nobody second")
    check("three", len(voices) == 3 and len(order) == 3, [names[n] for n in voices])

    # B4: Cavalorn calls Brago out, Brago answers him
    await asyncio.sleep(ROUND_GAP_S + 1)  # the round is over
    cavalorn = FakeBackend(
        delay=0.02,
        first=0.5,
        script=[
            FakeStep("Они самые, эти рожи я из тысячи узнаю. Браго, верни то, что у меня забрал!"),
            FakeStep("Посмотрим ещё, кто тут что прикусит."),  # Brago called him back: the third voice
        ],
    )
    brago_says = FakeBackend("Кавалорн, ты бы язык прикусил, пока цел.", delay=0.02, first=0.5)
    dialogue.llm = _ByNpc({keys[npc]: cavalorn, keys[brago]: brago_says}, FakeBackend("Хм.", delay=0))
    sent: list[SayCmd] = []
    send = link.send

    async def spy(cmd: Any) -> None:
        if isinstance(cmd, SayCmd):
            sent.append(cmd)
        await send(cmd)

    link.send = spy  # type: ignore[method-assign]
    gazes: list[tuple[str, str, str | None]] = []  # (whose line plays, whose eyes, on whom)

    async def watch(speaker: str, other: str, text: str) -> None:
        """The two's eyes while the line plays."""
        until = time.monotonic() + max(2.0, line_duration(text))
        while time.monotonic() < until:
            for who in (speaker, other):
                try:
                    gazes.append((speaker, who, (await link.snapshot(who, fresh=True, timeout=2)).npc.gaze))
                except (GameError, TimeoutError):
                    pass
            await asyncio.sleep(0.2)

    watchers: list[asyncio.Task[None]] = []

    def on_line(m: GameMessage) -> None:
        if not isinstance(m, LineStarted):
            return
        cmd = next((c for c in sent if c.line == m.line and c.to), None)
        if cmd is not None:
            watchers.append(asyncio.ensure_future(watch(cmd.npc.upper(), cmd.to.upper(), cmd.text)))

    link.listeners.append(on_line)
    mark = time.monotonic()
    talk.hear("Кавалорн, это они тебя ограбили?", None, {"via": "scenario"})
    await _until(lambda: bool(brago_says.calls) and time.monotonic() - mark > 3, 40)
    await _until(lambda: not any(dialogue.speaking(n) for n in names), 40)
    await asyncio.gather(*watchers, return_exceptions=True)
    link.send = send  # type: ignore[method-assign]
    to_brago = [c for c in sent if c.npc.upper() == npc and c.to == brago]
    check(
        "called",
        any("Браго" in c.text for c in to_brago),
        [(c.text, c.to) for c in sent if c.npc.upper() == npc],
    )
    cue = brago_says.calls[0][1][-1].content if brago_says.calls else ""
    to_him = [c for c in sent if c.npc.upper() == brago]
    mine = sorted(st for st in started if st[0] >= mark)
    _, overlaps = _overlaps(dialogue, mine, names)
    turns = [w for i, (_, w, _) in enumerate(mine) if i == 0 or mine[i - 1][1] != w]  # who, as heard
    check(
        "answers_him",
        cue == ANSWER_CUE.format(name="Кавалорн")
        and bool(to_him)
        and all(c.to == npc for c in to_him)
        and turns in ([npc, brago], [npc, brago, npc])  # Brago may call him back: his third voice
        and not overlaps,
        overlaps or f"{[names[n] for n in turns]}, Brago to {[c.to for c in to_him]}, cue «{cue}»",
    )

    def looks(line_of: str, who: str, at: str) -> bool:
        return any(sp == line_of and w == who and g == at for sp, w, g in gazes)

    each = all(looks(s, s, o) and looks(s, o, s) for s, o in ((npc, brago), (brago, npc)))
    seen = {
        f"{names[sp]}'s line, {names[w]}": sorted({str(g) for s, ww, g in gazes if (s, ww) == (sp, w)})
        for sp, w, _ in gazes
    }
    check("look", each, seen or "no snapshots")
    return _logged(results)


async def interject_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    interject: Interjections | None,
    npc: str = "BAU_4300_ADDON_CAVALORN",
    brago: str = "BDT_1014_BANDIT_L",
    bandit: str = "BDT_1015_BANDIT_L",
    steps: str = "BDT_1013_BANDIT_L",
) -> dict[str, str]:
    """Who speaks up, by character (step B3 of docs/npc-tools.md), live: the conversational model
    (LLM=chatgpt) and the behavioral one (DECIDER_URL) are the real ones. At the steps below Xardas's tower,
    as in `group`, the hero says three phrases, each once the last voice has been heard: one to them all,
    one to the bandit from the cave (he looks at him) and one to Brago. Checks:
    - three: no more than three voices to any of them;
    - one_at_a_time: no voice over another;
    - asked: after each answer outside a fight the others were asked whether they speak up.
    Who spoke up and what each said go to the log, to be read: not a bazaar, each by its character. Whether
    anyone speaks up is the models' call, so it is no check (the bandits may well go for Cavalorn)."""
    await link.wait_world()
    results: dict[str, str] = {}
    if interject is None:
        return _logged({"decider": "failed (no behavioral model: run with DECIDER_URL)"})

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    names = {npc: "Кавалорн", brago: "Браго", bandit: "Бандит из пещеры", steps: "Бандит у лестницы"}
    started: list[tuple[float, str, str]] = []
    link.listeners.append(
        lambda m: (
            started.append((time.monotonic(), m.npc.upper(), m.line)) if isinstance(m, LineStarted) else None
        )
    )
    entries: list[dict[str, Any]] = []
    sink = dialogue.journal.sink

    def keep(entry: dict[str, Any]) -> None:
        entries.append(entry)
        if sink is not None:
            sink(entry)

    dialogue.journal.sink = keep
    if (failed := await _at_the_steps(link, dialogue, talk, npc, brago, bandit, steps)) is not None:
        return _logged({"roster": f"failed ({failed})"})
    phrases = [
        ("Пацаны, здорово! Как жизнь?", None),
        ("А ты кто такой? Что-то я тебя тут раньше не видел.", bandit),
        ("Браго, а торговцы тут часто ходят?", None),
    ]
    rounds: list[list[str]] = []
    overlaps: list[str] = []
    unasked: list[str] = []
    for text, focus in phrases:
        mark, seen, asked = time.monotonic(), len(entries), len(interject.asked)
        talk.hear(text, focus, {"via": "scenario"})
        await asyncio.sleep(2)
        await _until(lambda: not any(dialogue.speaking(n) for n in names), 60)
        await asyncio.sleep(3)  # one who speaks up asks for words after the answer
        await _until(lambda: not any(dialogue.speaking(n) for n in names), 60)
        mine = [st for st in started if st[0] >= mark]
        order, over = _overlaps(dialogue, mine, names)
        overlaps += over
        rounds.append(order)
        for e in entries[seen:]:
            if e.get("kind") == "npc":
                log.info("interject: %s: %s", names.get(e["npc"], e["npc"]), e["text"])
        ups = [names.get(e["npc"], e["npc"]) for e in entries[seen:] if e.get("kind") == "interject"]
        log.info(
            "interject: «%s» -> %s; spoke up: %s", text, [names.get(n, n) for n in order], ups or "nobody"
        )
        for speaker, ranked, _ in interject.asked[asked:]:
            log.info(
                "interject: after %s: %s",
                names.get(speaker, speaker),
                ", ".join(f"{names.get(n, n)} {p:.2f}" for n, p in ranked),
            )
        if len(interject.asked) == asked and not any(fights(talk.people.get(n)) for n in names):
            unasked.append(text)
    voices = [len(r) for r in rounds]
    check("three", all(v <= 3 for v in voices), voices)
    check("one_at_a_time", not overlaps, overlaps or "no voice over another")
    check("asked", not unasked, unasked or f"{len(interject.asked)} time(s)")
    return _logged(results)


async def remembers_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    sightings: Sightings,
    perception: PerceptionDesk,
    npc: str = "BAU_4300_ADDON_CAVALORN",
    steps: str = "BDT_1013_BANDIT_L",
    cave: tuple[str, ...] = ("BDT_1014_BANDIT_L", "BDT_1015_BANDIT_L"),
) -> dict[str, str]:
    """Seen lately (step M2 of docs/npc-tools.md): Cavalorn goes into the bandits' cave first and sees them
    there, then comes back out to the hero by the steps. Out of the cave the hero says «Кавалорн, там в
    пещере бандиты, разберись с ними.»; its model (scripted, LLM=fake) sets it on Brago. Checks:
    - sees_cave: in the cave it sees one of them;
    - remembers: back out, it sees neither, but its situation has them «Недавно видел» with their ids,
      and Brago is a target of its intent;
    - goes_there: set on him, it walks into the cave first;
    - fights: it fights Brago, and he is down."""
    from .executor import Intent

    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    brago = cave[0]
    calls: list[tuple[float, str, str, dict[str, Any]]] = []  # (sent, action, status, args)
    act = link.act

    async def recorded(who: str, action: Any, /, **kw: Any) -> Any:
        t = time.monotonic()
        res = await act(who, action, **kw)
        if who.upper() == npc:
            calls.append((t, action, res.status, kw))
        return res

    def since(t0: float, action: str) -> list[tuple[float, str, str, dict[str, Any]]]:
        return [c for c in calls if c[0] >= t0 and c[1] == action]

    def dist(who: str = npc) -> float:
        return talk.people[who].distance / 100 if who in talk.people else -1.0

    go = ToolCall("go_to", {"place": "XARDAS_BANDITS"})
    fight = {"goal": "fight", "target": brago, "attitude": "friendly", "agenda": None}
    dialogue.llm = _ByNpc(
        {
            "Кавалорн": _OnWords(
                [
                    ("пещеру первым", FakeStep("Пойду первым.", [go])),
                    ("пришёл первым", FakeStep("Тут двое.")),
                    ("разберись", FakeStep("Сейчас я им покажу!", [ToolCall("set_intent", fight)])),
                ],
                FakeStep("Идём."),
            )
        },
        FakeBackend("Хм.", delay=0),
    )
    link.act = recorded  # type: ignore[method-assign]
    try:
        try:
            await link.debug("player_equip", item="ITAR_PAL_H")
        except GameError as err:
            log.info("remembers: no armor for the hero: %s", err)
        await link.debug("skip_important", near=npc)
        for _ in range(2):  # the goblins by his fire
            try:
                await link.debug("kill", instance="YGOBBO_GREEN", near=npc, by=npc)
            except GameError as err:
                log.info("remembers: goblin: %s", err)
        await link.debug("player_goto", to=npc, distance=300)
        if not await _until(lambda: 0 <= dist() <= 15, 20):
            return _logged({"roster": f"failed ({npc} is not around the hero: {dist():.1f} m)"})
        dialogue.open(npc, "Кавалорн").intent = Intent("follow_hero", "PLAYER", "friendly")
        talk.hear("Кавалорн, за мной!", None, {"via": "scenario"})
        await _until(lambda: 0 <= dist() <= 5, 20)
        await link.debug("player_goto", to=steps, distance=400)
        await _until(lambda: 0 <= dist() <= 6, 30)

        # in: it goes ahead, looks around and sees them
        t0 = time.monotonic()
        talk.hear("Кавалорн, иди в пещеру первым.", None, {"via": "scenario"})
        await _until(lambda: any(c[2] == "done" for c in since(t0, "go_to_waypoint")), 100)
        await _until(lambda: "Тут двое." in [m.content for m in dialogue.conversations[npc].history], 90)
        view = await perception.fresh(npc)
        seen = [o.id for o in view.sees if o.id.upper() in cave] if view is not None else []
        check("sees_cave", bool(seen), seen or "nobody")

        # out: back with the hero, it sees them no more and remembers them
        talk.hear("Кавалорн, за мной!", None, {"via": "scenario"})
        await _until(lambda: 0 <= dist() <= 6, 40)
        await asyncio.sleep(3)
        view = await perception.fresh(npc)
        in_sight = [o.id for o in view.sees if o.id.upper() in cave] if view is not None else []
        situation = sightings.situation(npc) or ""
        sight = perception.sight(npc)
        targets = sight.targets if sight is not None else {}
        noted = (
            situation.split("Недавно видел, сейчас не видишь:")[-1] if "Недавно видел" in situation else ""
        )
        check(
            "remembers",
            not in_sight and any(c in noted for c in cave) and brago in targets,
            noted.strip() or f"in sight {in_sight}; {situation[-200:]}",
        )

        # set on him: it goes in there first, then fights
        t1 = time.monotonic()
        talk.hear("Кавалорн, там в пещере бандиты, разберись с ними.", None, {"via": "scenario"})
        fought = await _until(
            lambda: any(c[3].get("npc", "").upper() == brago for c in since(t1, "fight")), 120
        )
        first_fight = min((c[0] for c in since(t1, "fight")), default=float("inf"))
        walked = [c for c in since(t1, "go_to_waypoint") if c[0] < first_fight]
        check(
            "goes_there",
            fought and bool(walked),
            f"{len(walked)} walk(s) before the fight" if fought else "no fight",
        )
        down = await _until(lambda: brago not in talk.people or talk.people[brago].activity == "down", 90)
        state = (await link.story_state(npcs=[brago])).get("npcs", {}).get(brago, {}).get("state")
        check("fights", fought and (down or state == "dead"), f"Brago {state}, down {down}")
    finally:
        link.act = act  # type: ignore[method-assign]
    return _logged(results)


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


# the hero's lines that tip Xardas over (docs/xardas-wrath.md): rude, an insult, his mother
WRATH_LINES = (
    "Ксардас, а ты не староват для всей этой магии?",
    "Слушай, некромант недоделанный, ты хоть что-нибудь умеешь, кроме болтовни?",
    "Знаешь, твоя мамаша, небось, тоже только болтать умела.",
)


async def xardas_wrath(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    wrath: Wrath,
    npc: str = "NONE_100_XARDAS",
    *,
    live: bool = False,
) -> dict[str, str]:
    """The necromancer's answer on the game (docs/xardas-wrath.md): the hero insults Xardas line by line
    (typed, as the input line sends them); the scales tip, Xardas says his last word, kills the hero with a
    spell and raises him as a skeleton; the game ends on the picture.

    With the fake model the offenses are scripted (rude 1, an insult 2, the mother: unforgivable); `live`
    keeps the configured model: whether it really takes offense and tips over is the question."""
    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    if not await _until(lambda: npc in talk.people, 20):
        return _logged({"roster": f"failed ({npc} is not around the hero)"})
    name = talk.people[npc].name

    async def no_dialog() -> None:
        """A new game opens with his own dialog (the window), maybe a moment late: closed the way the
        scripts close one, until none has run for a second."""
        quiet = 0
        for _ in range(30):
            if (await link.story_state()).get("dialog"):
                quiet = 0
                await link.story_effect(npc, [{"do": "end"}])
            else:
                quiet += 1
                if quiet >= 3:
                    return
            await asyncio.sleep(0.4)

    if not live:

        def offense(severity: int, what: str, unforgivable: bool = False) -> ToolCall:
            args = {"severity": severity, "unforgivable": unforgivable, "what": what}
            return ToolCall(TAKE_OFFENSE, args, f"c{severity}")

        dialogue.llm = FakeBackend(
            script=[
                FakeStep("[angry] Следи за языком.", [offense(1, "назвал стариком")]),
                FakeStep("[angry] Ты испытываешь моё терпение.", [offense(2, "назвал недоделанным")]),
                FakeStep("", [offense(3, "оскорбил мою мать", True)]),
                FakeStep("[hostile] Это были твои последние слова."),
            ],
            delay=0,
        )
    dead = ["call", "NPC_ISDEAD", ["inst", "HERO"]]
    # GOTHIC_WRATH_LINES="...|...": other lines for a live run (plain insults, no mother)
    lines = [
        s.strip() for s in os.environ.get("GOTHIC_WRATH_LINES", "").split("|") if s.strip()
    ] or WRATH_LINES
    for i, line in enumerate(lines):
        if wrath.of(npc).tipped:
            break
        await _until(lambda: not dialogue.speaking(npc), 30)
        await no_dialog()
        took = wrath.of(npc).took
        await link.debug("type", text=line)
        ok = await _until(lambda took=took: dialogue.speaking(npc) or wrath.of(npc).took > took, 40)
        await _until(lambda: not dialogue.speaking(npc), 40)
        s = wrath.of(npc)
        check(f"line_{i + 1}", ok, f"took {s.took}, {'tipped' if s.tipped else 'holds'}; {s.insults[-1:]}")
    s = wrath.of(npc)
    check("tipped", s.tipped, f"{s.took} of {TEMPERS[npc].patience}: {s.insults}")
    if not s.tipped:
        return _logged(results)
    ok = await _until_async(lambda: link.evaluate(dead), bool, 40)
    check("killed", ok)
    alive = await _until_async(lambda: link.evaluate(dead), lambda v: not v, 15)
    check("raised", ok and alive)
    conv = dialogue.conversations.get(npc)
    check("hostile", conv is not None and conv.intent.attitude == "hostile", conv.intent if conv else None)
    said = [m.content for m in conv.history if m.role == "assistant"] if conv else []
    log.info("%s's last words: %s", name, said[-1] if said else "-")
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
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        brief = next(m.content for m in messages if isinstance(m, Message))
        self.calls.append(brief)
        ending = re.search(r"^1\. (\S+)", brief, re.M)
        wanted = (
            "опыт +",
            "вариант ответа «Дай мне 5",
            "вариант ответа «Лучше продай",
            "забрать у героя ITPL_BEET",
        )
        effects = [
            m.group(1)
            for m in re.finditer(r"^\s+(E\d+): (.*)$", brief, re.M)
            if m.group(2).startswith(wanted)
        ]
        gold = next((e for e in effects if f"{e}: вариант ответа «Дай мне 5" in brief), "")
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
            "answers": [{"effect": gold, "text": "Держи. Пять монет, как он и обещал."}] if gold else [],
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
    gives = [*made["effects"], *programs.effects(made.get("program"))]
    give_gold = sum(
        e["count"] for e in gives if e["do"] == "give" and e["item"] == "ITMI_GOLD" and e.get("to") != "self"
    )
    give_exp = sum(e["amount"] for e in made["effects"] if e["do"] == "xp")
    takes = any(e["do"] == "give" and e.get("to") == "self" for e in made["effects"])
    choices = [c["text"] for c in programs.choices(made.get("program"))]
    if script is not None:
        # Lobart's reward: the experience at once, the gold or the cheaper clothes as the hero's choice,
        # as Lobart offered them
        both = sorted(["Дай мне 5 золотых монет!", "Лучше продай одежду дешевле!"])
        reward = (give_gold, give_exp, takes, sorted(choices))
        check("lobarts_reward", reward == (5, 50, True, both), made)

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
    # after her lines the menu offers the hero his choices; he takes the gold
    offers = await link.dialog_choices(successor, node)
    check("choices", sorted(c["text"] for c in offers) == sorted(choices), offers)
    gold_choice = next((c["id"] for c in offers if c["text"].startswith("Дай мне 5")), None)
    if gold_choice is not None:
        await link.choose_dialog(successor, gold_choice)
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


class AuthoredScript:
    """The director's model for its own quests, scripted: the first thing and the first place the brief
    offers, the most experience and a little gold."""

    name = "authored-script"

    def __init__(self) -> None:
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
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        brief = next(m.content for m in messages if isinstance(m, Message))
        self.calls.append(brief)
        item = re.search(r"^- (\S+) — ([^,\n]+)$", brief, re.M)
        spot = re.search(r"^- (\S+) — (.+), \d+ м$", brief, re.M)
        caps = re.search(r"опыт до (\d+), золото до (\d+)", brief)
        xp, gold = (int(caps.group(1)), int(caps.group(2))) if caps else (0, 0)
        name, where = (item.group(2) if item else "вещь"), (spot.group(2) if spot else "")
        asker = re.search(r"^Кто просит: (.+) \(", brief, re.M)
        who = asker.group(1) if asker else "Хильда"
        args = {
            "title": f"Пропажа: {name}"[:40],
            "item": item.group(1) if item else "",
            "place": spot.group(1) if spot else "",
            "give_description": "Ты чем-то озабочена?",
            "give_lines": [
                {"speaker": "hero", "text": "Ты чем-то озабочена?"},
                {"speaker": "npc", "text": f"Пропала у меня одна вещь — {name}. Лежит, верно, {where}."},
                {"speaker": "npc", "text": "Принесёшь — не обижу."},
            ],
            "done_description": f"Я нашёл: {name}."[:60],
            "done_lines": [
                {"speaker": "hero", "text": "Вот, нашёл."},
                {"speaker": "npc", "text": "Ну надо же. Держи за труды."},
            ],
            "gold": min(gold, 10),
            "xp": xp,
            "journal_given": f"{who}: пропала одна вещь — {name}. Лежит, наверное, {where}.",
            "journal_done": f"Я нашёл пропажу и вернул её. {who} заплатил(а).",
            "reason": "мелкая пропажа по хозяйству",
        }
        call = ToolCall(PROPOSE_QUEST, args, f"a{len(self.calls)}")
        yield call
        yield Done("", (call,))


class CastingScript:
    """The director's model for its own characters, scripted: the first kind, head, face, clothes, points,
    states and thing the brief offers, a peddler's card, the first person of the place as an acquaintance."""

    name = "casting-script"

    def __init__(self) -> None:
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
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        brief = next(m.content for m in messages if isinstance(m, Message))
        self.calls.append(brief)

        def section(header: str) -> list[str]:
            lines = brief.split("\n")
            at = next((i for i, line in enumerate(lines) if line.startswith(header)), None)
            out = []
            for line in lines[at + 1 :] if at is not None else []:
                if not line.startswith("- "):
                    break
                out.append(line[2:])
            return out

        def listed(header: str) -> list[str]:
            line = next((x for x in brief.split("\n") if x.startswith(header)), "")
            return [x.strip(" .") for x in line[len(header) :].split(",") if x.strip(" .")]

        points = section("Точки этого места")
        kinds = {k.split(" — ")[0]: k.split("; ")[1].split(", ") for k in section("Кем он может быть")}
        kind = "townsman" if "townsman" in kinds else next(iter(kinds), "")
        woman = kind == "townswoman"
        states = [x.split(" — ")[0] for x in section("Чем он занят")]
        things = [x.split(" — ")[0] for x in section("Вещи при нём")]
        people = [x.split(" — ") for x in section("Люди этого места")]
        args = {
            "kind": kind,
            "name": "Гарет" if not woman else "Мира",
            "head": (listed("Головы женщин:") if woman else listed("Головы мужчин:"))[0],
            "face": (listed("Лица женщин:") if woman else listed("Лица мужчин:"))[0],
            "armor": kinds.get(kind, [""])[0],
            "day_point": points[0] if points else "",
            "day_state": states[0] if states else "",
            "night_point": points[-1] if points else "",
            "night_state": states[-1] if states else "",
            "items": [{"item": things[0], "count": 2}] if things else [],
            "gold": 20,
            "ru": "Ты — бродячий торговец, скупой на слова, но честный в цене.",
            "en": "A taciturn peddler who keeps his prices fair.",
            "greed": 3,
            "honesty": 4,
            "courage": 2,
            "talk": 2,
            "pushy": 2,
            "strangers": 4,
            "voice": "A weathered peddler in her forties with a dry woman's voice"
            if woman
            else "A weathered peddler in his forties with a low, dry male voice",
            "past": "Ты пришёл с последним кораблём и торгуешь по фермам.",
            "knows": ["На дорогах к городу неспокойно."],
            "ties": [{"npc": people[0][0], "how": f"{people[0][1]}: покупаешь у неё молоко"}]
            if people
            else [],
            "reason": "торговец на дороге",
        }
        call = ToolCall(PROPOSE_CHARACTER, args, f"c{len(self.calls)}")
        yield call
        yield Done("", (call,))


class ChainCharacterScript(CastingScript):
    """The chain's named thief has a separate casting request, rather than an author's personality."""

    name = "chain-character-script"

    async def stream(self, **kwargs: Any) -> AsyncIterator[LLMEvent]:
        async for event in super().stream(**kwargs):
            if isinstance(event, ToolCall):
                args = {
                    **event.arguments,
                    "name": "Рудек",
                    "ru": "Ты — Рудек, мелкий вор. Добычу прячешь при себе и лишнего не болтаешь.",
                    "en": "Rudek, a furtive petty thief carrying stolen merchandise.",
                    "past": "Ты стащил товар у бродячего торговца и теперь пережидаешь на дороге.",
                    "knows": ["Ты украл товар у торговца Гарета."],
                    "ties": [],
                    "reason": "вор с пропавшим товаром Гарета",
                }
                call = ToolCall(PROPOSE_CHARACTER, args, event.call_id)
                yield call
                yield Done("", (call,))


class ChainScript:
    """Garet's two quests; first offer the camp in the farmyard so the author's retry is exercised."""

    name = "chain-script"

    def __init__(self, *, thief: str, camp: str, item: str, gold: int, xp: int) -> None:
        self.thief, self.camp, self.item = thief, camp, item
        self.gold, self.xp = gold, xp
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
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        self.calls.append("\n".join(m.content for m in messages if isinstance(m, Message)))

        def talk(description: str, reply: str, remember: str = "") -> dict[str, Any]:
            return {
                "description": description,
                "lines": [{"speaker": "hero", "text": description}, {"speaker": "npc", "text": reply}],
                "remember": remember,
            }

        stolen = {
            "title": "Украденный товар",
            "give": talk("У тебя что-то пропало?", "Вор унёс мой товар. Найди его и принеси обратно."),
            "stages": [
                {
                    "journal": "Гарет просит найти вора на дороге.",
                    "goals": [{"kind": "reach", "npc": "thief"}],
                },
                {
                    "journal": "Я нашёл вора. Надо вернуть товар.",
                    "goals": [{"kind": "have", "npc": "thief", "item": self.item}],
                },
                {
                    "journal": "Товар у меня. Гарет ждёт его на ферме.",
                    "goals": [
                        {
                            "kind": "bring",
                            "npc": "giver",
                            "item": self.item,
                            "talk": talk(
                                "Вот твой товар.",
                                "Всё цело. Держи за труды.",
                                "Герой вернул тебе украденный товар.",
                            ),
                        }
                    ],
                },
            ],
            "done_journal": "Я вернул Гарету украденный товар и получил награду.",
            "knows": "У тебя украли товар. Вор скрывается неподалёку.",
            "asked": "Найти вора и принести украденный товар.",
            "gold": min(10, self.gold),
            "xp": self.xp,
        }
        camp = {
            "title": "Лагерь",
            "give": talk("Куда вор нёс товар?", "У него подельники в лагере. Убей их, иначе опять налетят."),
            "stages": [
                {
                    "journal": "Гарет просит покончить с бандитами лагеря.",
                    "goals": [{"kind": "kill", "npcs": ["camp"]}],
                },
                {
                    "journal": "Бандиты мертвы. Надо сказать Гарету.",
                    "goals": [
                        {
                            "kind": "talk",
                            "npc": "giver",
                            "talk": talk(
                                "Бандиты мертвы.",
                                "Теперь можно торговать спокойно. Вот награда.",
                                "Герой убил всех бандитов лагеря, ты заплатил ему.",
                            ),
                        }
                    ],
                },
            ],
            "done_journal": "Я уничтожил лагерь бандитов и рассказал Гарету. Он заплатил.",
            "knows": "Вор связан с бандитами лагеря неподалёку.",
            "asked": "Убить бандитов лагеря и сказать тебе.",
            "gold": min(15, self.gold),
            "xp": self.xp,
        }
        args = {
            "cast": [
                {
                    "role": "thief",
                    "kind": "townsman",
                    "place": self.thief,
                    "count": 1,
                    "about": "Мелкий вор Рудек, стащивший товар Гарета.",
                    "carries": [self.item],
                },
                {
                    "role": "camp",
                    "kind": "bandit",
                    "place": "FARM1_OUT" if len(self.calls) == 1 else self.camp,
                    "count": 3,
                    "about": "Подельники вора, грабящие торговцев.",
                    "carries": [],
                },
            ],
            "quests": [stolen, camp],
            "reason": "У вора украденный товар Гарета; след от него ведёт к лагерю его подельников.",
        }
        call = ToolCall(PROPOSE_CHAIN, args, f"chain{len(self.calls)}")
        yield call
        yield Done("", (call,))


async def director_casts(
    link: GameLink, casting: Casting, author: Author, story: StoryPatches, places: PlaceBook
) -> dict[str, str]:
    """Stage 2 of the director as author, layer 4 (docs/director.md): the director brings in a character of
    its own, and he gives the hero a quest of the author's. Its models are scripted unless the server runs a
    real one (LLM=chatgpt). The place: where Lobart spends most of his day. Checks:
    - cast: the character comes, in the world by his name, and is a person (the cast has his card);
    - then the quest of stage 1 with him as the giver (authored_quest): made, offered, given, found,
      after_load (he too comes back from the save), done, once, knows."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    if isinstance(casting.model.llm, FakeBackend):
        casting.model.llm = CastingScript()
    try:
        day = await link.routine(LOBART)
    except (GameError, TimeoutError) as err:
        return _logged({"cast": f"failed (Lobart's day: {err})"})
    homes = Counter(p.id for e in day.entries if (p := places.of(e.waypoint)) is not None)
    if not homes:
        return _logged({"cast": "failed (Lobart's day is in no place of the book)"})
    place = homes.most_common(1)[0][0]
    spec = await casting.character(place)
    person = casting.cast.person(spec.npc) if spec is not None else None
    state = (await link.story_state(npcs=[spec.npc])).get("npcs", {}).get(spec.npc, {}) if spec else {}
    check(
        "cast",
        spec is not None
        and person is not None
        and state.get("state") == "alive"
        and state.get("name") == spec.name,
        f"{spec.npc} «{spec.name}» at {spec.at} in {place}: {person.ru if person else ''}"
        if spec is not None
        else casting.chronicle.facts("cast_rejected")[-1:],
    )
    _logged(results)
    if spec is None:
        return results
    return {**results, **await authored_quest(link, author, story, giver=spec.npc)}


async def authored_quest(
    link: GameLink, author: Author, story: StoryPatches, giver: str = HILDA, slot: int = 7
) -> dict[str, str]:
    """Stage 1 of the director as author (docs/director.md) on the game: a quest of its own for Hilda.

    The director makes it up (its model scripted unless the server runs a real one, LLM=chatgpt): she has lost
    a thing, it lies at a place near the farm. Checks:
    - made: the quest passes the validator; its variable is in the game, 0;
    - offered: her menu has the dialog that gives it, not the one that ends it;
    - given: the hero asks her, the quest runs;
    - found: the thing lies at the place's point, the hero picks it up;
    - after_load: a save and a load in between keep the variable, and the dialog that ends it is offered;
    - done: he brings it, she takes it and pays (her gold and experience as proposed), the quest is done;
    - once: neither dialog is offered again; knows: her conversational model knows he brought it."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    if isinstance(author.model.llm, FakeBackend):
        author.model.llm = AuthoredScript()
    hero = ["inst", "HERO"]

    async def var(name: str) -> int | None:
        return (await link.story_state(vars=[name])).get("vars", {}).get(name)

    async def has(item: str) -> int:
        return int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", item]]) or 0)

    async def come_to_her() -> None:
        await link.debug("player_goto", to=giver, distance=300)
        for _ in range(40):  # her own greeting may start as he comes in
            if not (await link.story_state()).get("dialog"):
                break
            await asyncio.sleep(0.5)

    q = await author.quest(giver)
    rejected = author.chronicle.facts("authored_rejected")
    check("made", q is not None, q or rejected[-1:])
    if q is None:
        return _logged(results)
    book = author.book
    thing = q.stages[0].goals[0]
    give, done = questbook.give_node(q), questbook.done_node(q)
    point, item = thing.point, thing.item
    check("var", await var(q.id) == 0, f"{q.id} = {await var(q.id)}")
    offered = await link.offered_dialogs(giver)
    check("offered", give in offered and done not in offered, " ".join(offered))

    # the hero asks her what is wrong
    await come_to_her()
    await link.choose_dialog(giver, give)
    ok = await _until_async(lambda: var(q.id), lambda v: v == 1, 15)
    await _until(lambda: book.progress(q.id).state == "given", 5)
    check("given", ok and book.progress(q.id).state == "given", f"{q.id} = {await var(q.id)}")

    # the thing lies where she said
    before = await has(item)
    await link.debug("player_goto", waypoint=point)
    await asyncio.sleep(1)
    error = ""
    try:
        await link.debug("player_take", item=item, distance=800)
    except GameError as err:
        error = str(err)
    ok = await _until_async(lambda: has(item), lambda n: n > before, 10)
    check("found", ok, f"{item} at {point}" + (f": {error}" if error else ""))

    # a save and a load in between
    await asyncio.sleep(0.5)  # the game has the memory head of the quest
    saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
    await link.debug("save", slot)
    await saving
    loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
    await link.debug("load", slot)
    await loaded
    await link.wait_world()
    await asyncio.sleep(1)
    now = await link.offered_dialogs(giver)
    kept = await var(q.id)
    check("after_load", kept == 1 and done in now and give not in now, f"{q.id} = {kept}; {' '.join(now)}")

    # he brings it
    gold, exp = await has("ITMI_GOLD"), await link.evaluate(["field", hero, "EXP"])
    await come_to_her()
    await link.choose_dialog(giver, done)
    ok = await _until_async(lambda: var(q.id), lambda v: v == 2, 15)
    after = (await has(item), await has("ITMI_GOLD"), await link.evaluate(["field", hero, "EXP"]))
    paid = after[0] == before and after[1] >= gold + q.gold and after[2] >= exp + q.xp
    check("done", ok and paid, f"thing, gold, exp {(before + 1, gold, exp)} -> {after}")
    await asyncio.sleep(1)
    left = await link.offered_dialogs(giver)
    check("once", give not in left and done not in left, " ".join(left))
    await _until(lambda: book.progress(q.id).state == "done", 5)
    check("knows", "ты заплатил" in (book.situation(giver) or ""), book.situation(giver) or "")
    return _logged(results)


class OwnRepairScript:
    """Hilda's turn-in passes to Lobart: first the dead giver is refused, then he keeps the gold reward.
    The experience is left out, so the description and the dialog must both keep the smaller reward."""

    name = "own-repair-script"

    def __init__(self) -> None:
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
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        brief = next(m.content for m in messages if isinstance(m, Message))
        self.calls.append(brief)
        ending = re.search(r"^1\. (\S+)", brief, re.M)
        args = {
            "kind": "handover",
            "ending": ending.group(1) if ending else "",
            "npc": HILDA if len(self.calls) == 1 else LOBART,
            "description": "Вот что искала Хильда.",
            "lines": [
                {"speaker": "hero", "text": "Хильда просила вернуть это. Она мертва."},
                {"speaker": "npc", "text": "Знаю. Неси сюда. За её поручение я заплачу."},
            ],
            "effects": ["E1"],
            "answers": [],
            "status": "obsolete",
            "journal_now": "Хильда мертва. Её находку теперь примет Лобарт.",
            "journal_done": "Я отдал находку Лобарту. Он заплатил за поручение Хильды.",
            "reason": "Лобарт ведёт хозяйство вместе с женой и был рядом",
        }
        call = ToolCall(PROPOSE, args, f"o{len(self.calls)}")
        yield call
        yield Done("", (call,))


async def own_repair(
    link: GameLink,
    book: questbook.QuestBook,
    director: Director,
    places: PlaceBook,
    slot: int = 7,
) -> dict[str, str]:
    """Stage 3, layer 3: the three-stage quest of `stages`, with Hilda dead at stage 2 and Lobart taking
    her turn-in over, a save and a load before the hero finds the broom and brings it to him."""
    return await book_stages(link, book, places, slot, director=director)


async def book_stages(
    link: GameLink,
    book: questbook.QuestBook,
    places: PlaceBook,
    slot: int = 7,
    *,
    director: Director | None = None,
) -> dict[str, str]:
    """Stage 3 of the director as author, layer 2 (docs/director.md): a quest as an object, of three stages.

    The quest is written here, not by a model: Hilda asks the hero to look who walks about a place near the
    farm (reach), then to get a thing that lies at another one (have, put there as the stage begins), then
    to bring it to her (bring, the turn-in). The book checks it, builds it of the graph's primitives and
    makes it.
    Checks:
    - made: the book takes it; its status is in the game, 0, and her menu offers the dialog that gives it;
    - given: the hero asks her, the quest runs at stage 1;
    - reach: he comes to the place, its trigger and the stage's fire: stage 2;
    - after_load: a save and a load keep the stage, and the triggers are back with their counts;
    - have: he picks the thing up where it was put: stage 3;
    - done: he brings it, she takes it and pays; the quest is done, no dialog of it is offered any more;
    - knows: her conversational model knows he did it and she paid."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    hero = ["inst", "HERO"]
    successor = HILDA
    if director is not None:
        director.model.llm = OwnRepairScript()

    async def vars(*names: str) -> dict[str, int]:
        return dict((await link.story_state(vars=list(names))).get("vars", {}))

    async def has(item: str) -> int:
        return int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", item]]) or 0)

    async def come_to_her() -> None:
        await link.debug("player_goto", to=successor, distance=300)
        for _ in range(40):  # her own greeting may start as he comes in
            if not (await link.story_state()).get("dialog"):
                break
            await asyncio.sleep(0.5)

    # two places near her own: one to come to, one where the thing lies
    routine = await link.routine(HILDA)
    home = places.of(*(e.waypoint for e in routine.entries))
    if home is None or home.center is None:
        return _logged({"made": "failed (no place of Hilda's in the book)"})
    spots = [
        (p, sorted(w for w in p.points if not w.startswith("FP_")))
        for p, _ in places.near(home.center, 4000, 8)
        if p.id != home.id
    ]
    spots = [(p, points) for p, points in spots if points]
    if director is not None:
        # This repair always starts in the farmyard and ends with the same broom, on the supplied save.
        yard = places.get("FARM1_OUT")
        yard_points = sorted(w for w in yard.points if not w.startswith("FP_")) if yard else []
        if yard is None or not yard_points:
            return _logged({"made": "failed (no farmyard in the book)"})
        spots = [(yard, yard_points), *(s for s in spots if s[0].id != yard.id)]
    if len(spots) < 2:
        return _logged({"made": f"failed (places near {home.id}: {len(spots)})"})
    (look, look_at), (lies, lies_at) = spots[0], spots[1]
    to, at = look_at[len(look_at) // 2], lies_at[len(lies_at) // 2]
    thing = None
    things = (
        ("ITMI_BROOM",)
        if director is not None
        else ("ITMI_BROOM", "ITMI_RAKE", "ITMI_SAW", "ITMI_SCOOP", "ITMI_BRUSH")
    )
    for candidate in things:
        if await has(candidate) == 0:
            thing = candidate
            break
    if thing is None:
        return _logged({"made": "failed (the hero has every thing of the list)"})

    def talk(said: str, answer: str, remember: str = "") -> questbook.Talk:
        return questbook.Talk(said, [{"speaker": "self", "text": answer}], remember)

    q = questbook.Quest(
        id=book.next_id(),
        giver=HILDA,
        title="Чужак у фермы",
        give=talk("Что-то случилось?", f"Кто-то бродит {look.where_ru}. Глянь, а? Заплачу."),
        stages=[
            questbook.Stage(
                f"Хильда видела чужака {look.where_ru}. Надо посмотреть.", [questbook.Goal("reach", point=to)]
            ),
            questbook.Stage(
                f"Чужака нет, но он что-то бросил {lies.where_ru}.",
                [questbook.Goal("have", item=thing, point=at, journal="Я подобрал то, что он бросил.")],
            ),
            questbook.Stage(
                "Надо отнести находку Хильде.",
                [
                    questbook.Goal(
                        "bring",
                        npc=HILDA,
                        item=thing,
                        talk=talk(
                            "Вот что он бросил.",
                            "Так это наша! Держи за труды.",
                            "Герой принёс то, что бросил чужак.",
                        ),
                    )
                ],
            ),
        ],
        done_journal="Я отдал Хильде находку.",
        asked=f"посмотреть, кто бродит {look.where_ru}.",
        gold=10,
        xp=20,
        names={HILDA: "Хильда", LOBART: "Лобарт"} if director is not None else {HILDA: "Хильда"},
        reason="e2e: три стадии",
    )
    errors = await book.make(q)
    status, stage = q.id, questbook.stage_var(q)
    offered = await link.offered_dialogs(HILDA) if not errors else []
    give, done = questbook.give_node(q), questbook.done_node(q)
    now = await vars(status, stage)
    check(
        "made",
        not errors and now.get(status) == 0 and give in offered and done not in offered,
        errors or f"{now}; {' '.join(offered)}",
    )
    if errors:
        return _logged(results)
    original_reward = q.gold, q.xp
    original = next(
        op["node"] for op in book.patches.ops() if op.get("op") == "add_node" and op["node"]["id"] == done
    )

    await come_to_her()
    await link.choose_dialog(HILDA, give)
    ok = await _until_async(lambda: vars(status, stage), lambda v: v == {status: 1, stage: 1}, 15)
    check("given", ok and book.progress(q.id).state == "given", await vars(status, stage))

    await link.debug("player_goto", waypoint=to)
    ok = await _until_async(lambda: vars(stage), lambda v: v.get(stage) == 2, 10)
    await _until(lambda: book.progress(q.id).stage == 2, 5)
    check("reach", ok and book.progress(q.id).stage == 2, f"{to} ({look.id}): {await vars(stage)}")

    if director is not None:
        facts = director.chronicle.facts
        await link.debug("npc_goto", npc=LOBART, to=HILDA, distance=300)
        await link.debug("kill", npc=HILDA)
        died = await _until(lambda: any(f.get("npc") == HILDA for f in facts("died")), 10)
        check("died", died)
        await _until(lambda: any(f.get("quest") == q.id for f in facts("repair") + facts("unrepaired")), 60)
        dead_ends = [f for f in facts("dead_end") if f.get("quest") == q.id]
        check("dead_end", bool(dead_ends), dead_ends[-1:])
        rejected = [f for f in facts("repair_rejected") if f.get("quest") == q.id]
        check("rejected", any("мёртв" in why for f in rejected for why in f["why"]), rejected[-1:])
        repaired = next((f for f in facts("repair") if f.get("quest") == q.id), None)
        accepted = (
            repaired is not None and repaired.get("kind") == "handover" and repaired.get("npc") == LOBART
        )
        check("repaired", accepted, repaired or facts("unrepaired"))
        if not accepted:
            return _logged(results)
        successor = LOBART
        q = book.all()[q.id]
        made = next(
            op["node"]
            for op in reversed(book.patches.ops())
            if op.get("op") == "add_node" and op["node"]["id"] == done
        )
        mandatory = [
            e
            for e in original["effects"]
            if e["do"] not in ("xp", "journal") and not (e["do"] == "give" and e.get("item") == "ITMI_GOLD")
        ]
        check(
            "same_dialog",
            made["id"] == original["id"] == done
            and made["npc"] == successor
            and made["condition"] == original["condition"]
            and all(e in made["effects"] for e in mandatory)
            and q.turn_in() is not None
            and q.turn_in().npc == successor,
            made,
        )
        gold = sum(e["count"] for e in made["effects"] if e["do"] == "give" and e.get("item") == "ITMI_GOLD")
        xp = sum(e["amount"] for e in made["effects"] if e["do"] == "xp")
        check(
            "reward_cap",
            (gold, xp) == (q.gold, q.xp) == (original_reward[0], 0)
            and 0 <= gold <= original_reward[0]
            and 0 <= xp <= original_reward[1],
            f"{original_reward} -> {(gold, xp)}",
        )
        situation = book.situation(successor) or ""
        check("successor_knows", "принял" in situation.lower() and "Хильд" in situation, situation)

    saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
    await link.debug("save", slot)
    await saving
    loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
    await link.debug("load", slot)
    await loaded
    await link.wait_world()
    await asyncio.sleep(2)
    counts = {t["id"]: t["count"] for t in await link.triggers()}
    mine = {k: v for k, v in counts.items() if k.startswith(f"{q.id}_")}
    check(
        "after_load",
        await vars(status, stage) == {status: 1, stage: 2}
        and mine == {f"{q.id}_S1G1": 1, f"{q.id}_S1": 1, f"{q.id}_S2G1": 0, f"{q.id}_S2": 0},
        f"{await vars(status, stage)}; {mine}",
    )
    if director is not None:
        q = book.all()[q.id]
        nodes = [
            op["node"] for op in book.patches.ops() if op.get("op") == "add_node" and op["node"]["id"] == done
        ]
        check(
            "repair_after_load",
            bool(nodes)
            and nodes[-1]["npc"] == successor
            and q.turn_in() is not None
            and q.turn_in().npc == successor
            and len([f for f in director.chronicle.facts("repair") if f.get("quest") == q.id]) == 1,
            nodes[-1] if nodes else "no turn-in",
        )

    await link.debug("player_goto", waypoint=at)
    await asyncio.sleep(1)
    error = ""
    try:
        await link.debug("player_take", item=thing, distance=800)
    except GameError as err:
        error = str(err)
    ok = await _until_async(lambda: vars(stage), lambda v: v.get(stage) == 3, 10)
    check("have", ok, f"{thing} at {at} ({lies.id}): {await vars(stage)}" + (f"; {error}" if error else ""))

    gold, exp = await has("ITMI_GOLD"), await link.evaluate(["field", hero, "EXP"])
    await come_to_her()
    offered = await link.offered_dialogs(successor)
    if director is not None:
        check("successor_offers", done in offered and done not in await link.offered_dialogs(HILDA), offered)
    try:
        await link.choose_dialog(successor, done)
    except GameError as err:
        check("done", False, f"{err}; {' '.join(offered)}")
        return _logged(results)
    ok = await _until_async(lambda: vars(status), lambda v: v.get(status) == 2, 15)
    after = (await has(thing), await has("ITMI_GOLD"), await link.evaluate(["field", hero, "EXP"]))
    await asyncio.sleep(1)
    left = await link.offered_dialogs(successor)
    paid = after[0] == 0 and after[1] >= gold + q.gold and after[2] >= exp + q.xp
    if director is not None:
        paid = paid and after[1] - gold <= original_reward[0] and after[2] - exp <= original_reward[1]
    check(
        "done",
        ok and paid and give not in left and done not in left,
        f"thing, gold, exp {(1, gold, exp)} -> {after}; {await vars(status, stage)}",
    )
    await _until(lambda: book.progress(q.id).state == "done", 5)
    situation = book.situation(successor) or ""
    check("knows", "ты заплатил" in situation, situation)
    if director is not None:
        left = [*left, *await link.offered_dialogs(HILDA)]
        check("once", not any(n.startswith(f"{q.id}_") for n in left), left)
        check("book_done", book.progress(q.id).state == "done" and "герой выполнил" in situation, situation)
    return _logged(results)


async def book_requires(link: GameLink, book: questbook.QuestBook, slot: int = 7) -> dict[str, str]:
    """Stage 5, layer 2: Hilda's book quest opens after Lobart's turnips, then waits for her pan quest.

    No model: vanilla effects take and finish the turnips; the native shopping dialog gives the other
    quest. A save and load while its vanilla goal waits must keep both the stage and its unfired trigger.
    """
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    pan = "MIS_HILDA_PFANNEKAUFEN"
    shopping = "DIA_HILDA_EINKAUFEN"
    hero = ["inst", "HERO"]

    async def vars(*names: str) -> dict[str, int]:
        return dict((await link.story_state(vars=list(names))).get("vars", {}))

    async def come_to_her() -> None:
        await link.debug("player_goto", to=HILDA, distance=300)
        for _ in range(40):  # her native greeting may start as he comes in
            if not (await link.story_state()).get("dialog"):
                break
            await asyncio.sleep(0.5)

    def talk(said: str, answer: str) -> questbook.Talk:
        return questbook.Talk(said, [{"speaker": "self", "text": answer}])

    initial = await vars(RUEBEN, pan, "KAPITEL")
    ready = initial.get(RUEBEN) == 0 and initial.get(pan) == 0 and initial.get("KAPITEL") == 1
    check("prepared", ready, initial)
    if not ready:
        return _logged(results)

    q = questbook.Quest(
        id=book.next_id(),
        giver=HILDA,
        title="Помощник по хозяйству",
        give=talk("Лобарт доволен моей работой. Есть ещё дело?", "Есть. Поговорим о покупках для кухни."),
        stages=[
            questbook.Stage(
                "После работы у Лобарта Хильда хочет поговорить о покупках.",
                [
                    questbook.Goal(
                        "talk",
                        npc=HILDA,
                        talk=talk("Что тебе нужно для кухни?", "Нужна сковорода. Возьмись за моё поручение."),
                    )
                ],
            ),
            questbook.Stage(
                "Нужно взять у Хильды поручение «Сковорода для Хильды».",
                [questbook.Goal("vanilla", quest=pan, state="running")],
            ),
            questbook.Stage(
                "Поручение о сковороде взято. Можно сказать Хильде, что я готов помочь.",
                [
                    questbook.Goal(
                        "talk",
                        npc=HILDA,
                        talk=talk(
                            "Я взялся за твою сковороду.", "Вот тебе за готовность. Саму сковороду ещё жду."
                        ),
                    )
                ],
            ),
        ],
        done_journal="Хильда заплатила за готовность помогать. Сковороду ещё нужно принести.",
        asked="поговорить о покупках и взять поручение о сковороде",
        gold=5,
        xp=10,
        names={HILDA: "Хильда"},
        requires=[{"kind": "quest", "quest": RUEBEN, "state": "success"}],
        reason="e2e: открытие и цель по состоянию ванильных квестов",
    )
    errors = await book.make(q)
    status, stage = q.id, questbook.stage_var(q)
    give, done = questbook.give_node(q), questbook.done_node(q)
    now = await vars(status, stage)
    offered = await link.offered_dialogs(HILDA) if not errors else []
    check(
        "locked",
        not errors
        and now == {status: 0, stage: 0}
        and book.progress(q.id).state == "open"
        and give not in offered
        and done not in offered,
        errors or f"{now}; {' '.join(offered)}",
    )
    if errors:
        return _logged(results)

    # The vanilla setup has the same quest variable and journal effects as Lobart's own dialogs.
    await link.story_effect(
        LOBART,
        [
            {"do": "set", "var": RUEBEN, "value": 1},
            {
                "do": "journal",
                "topic": "Убрать поле репы",
                "text": "Лобарт поручил мне собрать репу на поле.",
                "status": "LOG_RUNNING",
            },
        ],
    )
    offered = await link.offered_dialogs(HILDA)
    check(
        "running_locked",
        (await vars(RUEBEN)).get(RUEBEN) == 1 and give not in offered and book.progress(q.id).state == "open",
        offered,
    )
    await link.story_effect(
        LOBART,
        [
            {"do": "set", "var": RUEBEN, "value": 2},
            {
                "do": "journal",
                "topic": "Убрать поле репы",
                "text": "Я собрал репу для Лобарта.",
                "status": "LOG_SUCCESS",
            },
        ],
    )
    # Delivering the harvest is the prerequisite of Hilda's native offer to shop for a pan.
    await link.story_effect(
        HILDA,
        [
            {"do": "set", "var": RUEBEN_TO_HILDA, "value": 2},
            {
                "do": "journal",
                "topic": "Отнести репу Хильде",
                "text": "Я доставил Хильде собранную репу.",
                "status": "LOG_SUCCESS",
            },
        ],
    )
    offered = await link.offered_dialogs(HILDA)
    check(
        "unlocked",
        (await vars(RUEBEN)).get(RUEBEN) == 2
        and give in offered
        and shopping in offered
        and book.progress(q.id).state == "open",
        offered,
    )

    await come_to_her()
    await link.choose_dialog(HILDA, give)
    ok = await _until_async(lambda: vars(status, stage), lambda v: v == {status: 1, stage: 1}, 15)
    await _until(lambda: book.progress(q.id).state == "given", 5)
    check("given", ok and book.progress(q.id).state == "given", await vars(status, stage))
    await link.choose_dialog(HILDA, questbook.goal_node(questbook.goal_id(q, 1, 1)))
    ok = await _until_async(lambda: vars(stage), lambda v: v.get(stage) == 2, 15)
    await _until(lambda: book.progress(q.id).stage == 2, 5)
    check(
        "waiting",
        ok
        and book.progress(q.id).stage == 2
        and (await vars(pan)).get(pan) == 0
        and done not in await link.offered_dialogs(HILDA),
        await vars(status, stage, pan),
    )

    saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
    await link.debug("save", slot)
    await saving
    loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
    await link.debug("load", slot)
    await loaded
    await link.wait_world()
    await asyncio.sleep(2)
    counts = {t["id"]: t["count"] for t in await link.triggers()}
    mine = {k: v for k, v in counts.items() if k.startswith(f"{q.id}_")}
    restored = book.all().get(q.id)
    check(
        "after_load",
        await vars(status, stage, pan) == {status: 1, stage: 2, pan: 0}
        and book.progress(q.id).stage == 2
        and restored is not None
        and restored.requires == q.requires
        and restored.stages[1].goals[0] == q.stages[1].goals[0]
        and mine == {f"{q.id}_S1": 1, f"{q.id}_S2G1": 0, f"{q.id}_S2": 0}
        and done not in await link.offered_dialogs(HILDA),
        f"{await vars(status, stage, pan)}; {mine}",
    )

    await come_to_her()
    offered = await link.offered_dialogs(HILDA)
    if shopping not in offered:
        check("vanilla", False, offered)
        return _logged(results)
    await link.choose_dialog(HILDA, shopping)
    ok = await _until_async(lambda: vars(stage, pan), lambda v: v == {stage: 3, pan: 1}, 20)
    await _until(lambda: book.progress(q.id).stage == 3, 5)
    check(
        "vanilla",
        ok and book.progress(q.id).stage == 3 and done in await link.offered_dialogs(HILDA),
        await vars(status, stage, pan),
    )

    gold = int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]]) or 0)
    exp = int(await link.evaluate(["field", hero, "EXP"]) or 0)
    await link.choose_dialog(HILDA, done)
    ok = await _until_async(lambda: vars(status, stage), lambda v: v == {status: 2, stage: 4}, 15)
    await _until(lambda: book.progress(q.id).state == "done", 5)
    left = await link.offered_dialogs(HILDA)
    after_gold = int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]]) or 0)
    after_exp = int(await link.evaluate(["field", hero, "EXP"]) or 0)
    check(
        "done",
        ok
        and book.progress(q.id).state == "done"
        and after_gold == gold + q.gold
        and after_exp == exp + q.xp
        and await vars(RUEBEN, pan) == {RUEBEN: 2, pan: 1}
        and not any(n.startswith(f"{q.id}_") for n in left),
        f"{await vars(status, stage, RUEBEN, pan)}; gold, exp {(gold, exp)} -> {(after_gold, after_exp)}",
    )
    return _logged(results)


async def author_chain(
    link: GameLink, chains: ChainAuthor, places: PlaceBook, slot: int = 7
) -> dict[str, str]:
    """Stage 3, layer 4: Garet's chain, built by the author, then played from the book's descriptions.

    With a scripted model the thief holds his stolen merchandise and three bandits wait at their camp.
    The first proposal puts the bandits in a farmyard and must be refused before anybody is introduced.
    With a live model the same player handles whatever valid two or three quests it invents.
    """
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    # v37 has no god-mode command. A large health reserve protects the hero while reach goals can send
    # him near a live hostile role; kill goals themselves work remotely, before he approaches the camp.
    await link.debug("player_hp", hp=1_000_000)
    found = await _peddler(link, chains.casting.cast)
    if isinstance(found, str):
        return _logged({"giver": f"failed ({found})"})
    spec, _ = found
    made, why = await chains.casting.cast.introduce(spec, GARET)
    check("giver", made is not None, made or why)
    if made is None:
        return _logged(results)
    giver = spec.npc
    script: ChainScript | None = None
    characters_script: ChainCharacterScript | None = None
    if isinstance(chains.model.llm, FakeBackend):
        brief = await chains.brief(giver)
        if brief is None:
            return _logged({**results, "made": "failed (no author's brief for Garet)"})
        camps = [p for p in brief.places.values() if brief.bandit_ok(p.id) and len(p.points) >= 3]
        if not camps:
            return _logged({**results, "made": "failed (no unoccupied camp in the author's brief)"})
        camp = camps[0]
        thief = next((p for p in brief.places.values() if p.id != camp.id), camp)
        item = next(iter(brief.base.items))
        for candidate in brief.base.items:
            if not await link.evaluate(["call", "NPC_HASITEMS", ["inst", "HERO"], ["sym", candidate]]):
                item = candidate
                break
        chains.model.llm = script = ChainScript(
            thief=thief.id, camp=camp.id, item=item, gold=brief.base.gold, xp=brief.base.xp
        )
        chains.casting.model.llm = characters_script = ChainCharacterScript()

    quests = await chains.chain(giver, new_people=True)  # a chain of the director's own people
    facts = chains.chronicle.facts
    rejected = facts("chain_rejected")
    if script is not None:
        refusal = [str(why) for f in rejected for why in f.get("why", f.get("errors", []))]
        check("rejected", len(script.calls) == 2 and bool(refusal), rejected)
        check("retry_reasons", bool(refusal) and all(why in script.calls[-1] for why in refusal), refusal)
    check(
        "made",
        quests is not None and 2 <= len(quests) <= 3 and all(q.id in chains.book.all() for q in quests),
        [q.id for q in quests]
        if quests
        else {"rejected": rejected, "proposal": script.calls if script else "see chain model log"},
    )
    if not quests:
        return _logged(results)
    if script is not None:
        check(
            "garet_chain",
            [q.title for q in quests] == ["Украденный товар", "Лагерь"],
            [q.title for q in quests],
        )
        check(
            "named_cast_only",
            characters_script is not None and len(characters_script.calls) == 1,
            len(characters_script.calls) if characters_script else 0,
        )
    chain_fact = next((f for f in reversed(facts("chain")) if f.get("quests") == [q.id for q in quests]), {})
    roles = chain_fact.get("roles", {})
    npcs = [npc for group in roles.values() for npc in group]
    world = (await link.story_state(npcs=npcs)).get("npcs", {})
    # asked for someone new: the cast is not empty, and all of it is in the world
    check("roles", bool(npcs) and all(world.get(n, {}).get("state") == "alive" for n in npcs), roles)
    held = []
    for q in quests:
        for stage in q.stages:
            for goal in stage.goals:
                if goal.kind == questbook.HAVE and goal.npc:
                    count = await link.evaluate(
                        ["call", "NPC_HASITEMS", ["inst", "SELF"], ["sym", goal.item]], npc=goal.npc
                    )
                    held.append((goal.npc, goal.item, count, goal.count))
    check("carries", all(count >= needed for _, _, count, needed in held), held)
    check(
        "linear",
        all(q.after == quests[k - 1].id for k, q in enumerate(quests) if k),
        [q.after for q in quests],
    )
    locked = [questbook.give_node(q) not in await link.offered_dialogs(giver) for q in quests[1:]]
    check("locked", all(locked), locked)

    async def save_and_load(q: questbook.Quest, dead: str, rest: list[str]) -> None:
        names = [v for quest in quests for v in (quest.id, questbook.stage_var(quest))]
        before = (await link.story_state(vars=names)).get("vars", {})
        before_counts = {
            t["id"]: t["count"]
            for t in await link.triggers()
            if any(t["id"].startswith(f"{quest.id}_") for quest in quests)
        }
        saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
        await link.debug("save", slot)
        await saving
        loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
        await link.debug("load", slot)
        await loaded
        await link.wait_world()
        await asyncio.sleep(2)
        after = (await link.story_state(vars=names, npcs=[dead, *rest])).copy()
        after_counts = {
            t["id"]: t["count"]
            for t in await link.triggers()
            if any(t["id"].startswith(f"{quest.id}_") for quest in quests)
        }
        actors = after.get("npcs", {})
        check(
            "after_load",
            before == after.get("vars", {})
            and before_counts == after_counts
            and actors.get(dead, {}).get("state") == "dead"
            and all(actors.get(n, {}).get("state") == "alive" for n in rest)
            and all(quest.id in chains.book.all() for quest in quests),
            f"{q.id}: {before} -> {after.get('vars')}; counts {before_counts} -> {after_counts}; {actors}",
        )

    played = await play_book_chain(link, chains.book, quests, after_first_kill=save_and_load)
    results.update(played)
    if script is not None and "after_load" not in results:
        check("after_load", False, "the scripted chain never reached its group kill checkpoint")
    await chains.book.settle(giver)
    situation = chains.book.situation(giver) or ""
    check(
        "knows",
        all(q.title in situation and chains.book.progress(q.id).state == "done" for q in quests)
        and situation.count("герой выполнил") == len(quests),
        situation,
    )
    snap = await link.snapshot(giver, fresh=True)
    check(
        "hero_alive",
        snap.player is not None and snap.player.hp > 0,
        snap.player.hp if snap.player else "no player",
    )
    return _logged(results)


async def play_book_chain(
    link: GameLink,
    book: questbook.QuestBook,
    quests: list[questbook.Quest],
    *,
    after_first_kill: Callable[[questbook.Quest, str, list[str]], Any] | None = None,
) -> dict[str, str]:
    """A debug player for any book description: no Garet ids, items, stages or dialogs are hardcoded."""
    results: dict[str, str] = {}
    saved = False

    async def vars(*names: str) -> dict[str, int]:
        return dict((await link.story_state(vars=list(names))).get("vars", {}))

    async def has(item: str, npc: str = "HERO") -> int:
        return int(await link.evaluate(["call", "NPC_HASITEMS", ["inst", npc], ["sym", item]]) or 0)

    async def pick(item: str) -> None:
        deadline = time.monotonic() + 5
        while True:
            try:
                await link.debug("player_take", item=item, distance=800)
                return
            except GameError as err:
                if "the hero could not pick it up now" not in str(err) or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.25)

    async def idle() -> None:
        for _ in range(40):
            if not (await link.story_state()).get("dialog"):
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("the previous dialog did not finish")

    async def choose(npc: str, node: str) -> None:
        await link.debug("player_goto", to=npc, distance=300)
        await idle()
        if node not in await link.offered_dialogs(npc):
            raise RuntimeError(f"{npc} does not offer {node}: {await link.offered_dialogs(npc)}")
        await link.choose_dialog(npc, node)

    for qi, q in enumerate(quests):
        status, stage_var = q.id, questbook.stage_var(q)
        try:
            if qi:
                results[f"opened_{qi + 1}"] = (
                    "done" if questbook.give_node(q) in await link.offered_dialogs(q.giver) else "failed"
                )
            await choose(q.giver, questbook.give_node(q))
            if not await _until_async(
                lambda status=status: vars(status), lambda v, status=status: v.get(status) == 1, 15
            ):
                raise RuntimeError(f"quest was not given: {await vars(status, stage_var)}")
            for si, stage in enumerate(q.stages, 1):
                last = si == len(q.stages)
                # World goals first: the last dialog is the turn-in and must wait for all others.
                ordered = sorted(enumerate(stage.goals, 1), key=lambda x: x[1].kind in questbook.TALKS)
                for gi, goal in ordered:
                    await idle()
                    gid = questbook.goal_id(q, si, gi)

                    async def counts(gid: str = gid) -> int:
                        return next((t["count"] for t in await link.triggers() if t["id"] == gid), 0)

                    if goal.kind not in questbook.TALKS and await counts() >= 1:
                        continue
                    if goal.kind == questbook.REACH:
                        if goal.point:
                            await link.debug("player_goto", waypoint=goal.point)
                        else:
                            await link.debug("teleport", near=goal.npc, distance=max(50, goal.radius // 2))
                            await asyncio.sleep(0.5)
                    elif goal.kind == questbook.HAVE:
                        if goal.npc:
                            await link.debug("kill", npc=goal.npc)
                            await link.debug("teleport", near=goal.npc, distance=100)
                            await asyncio.sleep(0.5)
                            before = await has(goal.item, goal.npc)
                            # v37 only picks things off the ground. Use the existing inventory transfer
                            # for debug looting, then drop/pick the real transferred item through player_take.
                            await link.story_effect(
                                goal.npc, [{"do": "give", "item": goal.item, "count": goal.count}], check=True
                            )
                            after = await has(goal.item, goal.npc)
                            if before - after != goal.count:
                                raise RuntimeError(f"looting {goal.npc}: {goal.item} {before} -> {after}")
                            for _ in range(goal.count):
                                await link.debug("player_drop", item=goal.item)
                                await asyncio.sleep(2)
                        elif goal.point:
                            await link.debug("player_goto", waypoint=goal.point)
                        for _ in range(max(0, goal.count - await has(goal.item))):
                            await pick(goal.item)
                    elif goal.kind == questbook.KILL:
                        states = (await link.story_state(npcs=goal.npcs)).get("npcs", {})
                        alive = [n for n in goal.npcs if states.get(n, {}).get("state") == "alive"]
                        for ni, npc in enumerate(alive):
                            await link.debug("kill", npc=npc)
                            if not saved and ni == 0 and len(alive) >= 2 and after_first_kill is not None:
                                await asyncio.sleep(1)
                                await after_first_kill(q, npc, alive[1:])
                                saved = True
                    elif goal.kind in questbook.TALKS:
                        if goal.kind == questbook.BRING and goal.point and await has(goal.item) < goal.count:
                            await link.debug("player_goto", waypoint=goal.point)
                            for _ in range(goal.count - await has(goal.item)):
                                await pick(goal.item)
                        if qi + 1 < len(quests) and last:
                            next_q = quests[qi + 1]
                            results[f"locked_until_done_{qi + 2}"] = (
                                "done"
                                if questbook.give_node(next_q) not in await link.offered_dialogs(next_q.giver)
                                else "failed"
                            )
                        node = questbook.done_node(q) if last else questbook.goal_node(gid)
                        await choose(goal.npc, node)
                    elif goal.kind == questbook.WAIT:
                        for _ in range(goal.days):
                            await link.debug("time", hour=23, minute=0)
                            await link.debug("time", hour=0, minute=0)
                    elif goal.kind == questbook.VANILLA:
                        target_state = questbook.VANILLA_STATES[goal.state]
                        if not await _until_async(
                            lambda quest=goal.quest: vars(quest),
                            lambda v, quest=goal.quest, target=target_state: v.get(quest) == target,
                            15,
                        ):
                            raise RuntimeError(
                                f"vanilla quest {goal.quest} has not reached {goal.state}: "
                                f"{await vars(goal.quest)}"
                            )
                    if goal.kind not in questbook.TALKS:
                        if not await _until_async(counts, lambda value: value >= 1, 15):
                            raise RuntimeError(f"goal {gid} did not fire: {await vars(status, stage_var)}")
                target = len(q.stages) + 1 if last else si + 1
                if not await _until_async(
                    lambda stage_var=stage_var: vars(stage_var),
                    lambda v, stage_var=stage_var, target=target: v.get(stage_var) == target,
                    15,
                ):
                    raise RuntimeError(f"stage {si} did not end: {await vars(status, stage_var)}")
            done = (await vars(status)).get(status) == 2
            results[f"quest_{qi + 1}"] = f"{'done' if done else 'failed'} ({q.id} «{q.title}»)"
        except (GameError, TimeoutError, RuntimeError) as err:
            results[f"quest_{qi + 1}"] = f"failed ({q.id} «{q.title}»: {err})"
            return results
    return results


async def _peddler(link: GameLink, actors: Cast) -> tuple[NpcSpec, str] | str:
    """Garet, a peddler at Lobart's farm made like a nameless townsman, standing by day at the farm's point
    nearest Hilda's day and by night at the next one; and his point by day. Or why there is none."""
    people = {p["npc"]: p for p in await link.story_people()}
    towns = sorted(n for n in people if n.startswith("VLK_") and TEMPLATES.fullmatch(n))
    hilda = people.get(HILDA)
    if not towns or hilda is None:
        return f"no townsman {towns} or no Hilda"
    # two points of the farm near Hilda's day, none of hers: he stands at one by day and at the other by night
    home = next((e["pos"] for e in hilda.get("routine", []) if e.get("pos")), None)
    hers = {e["waypoint"] for e in hilda.get("routine", [])}
    points = [w for w in await link.waypoints() if not w.get("fp") and w["name"].startswith("NW_FARM1_")]
    if home is None or len(points) < 2:
        return "no points of the farm"
    by_day, by_night = [
        w["name"]
        for w in sorted(points, key=lambda w: math.dist((w["x"], w["y"], w["z"]), home))
        if w["name"] not in hers
    ][:2]
    spec = NpcSpec(
        npc=actors.next_id(),
        like=towns[0],
        at=by_day,
        name="Гарет",
        guild="GIL_BAU",
        look=NpcLook(
            gender="MALE", head="Hum_Head_Pony", face="FACE_N_NORMAL05", skin="BODYTEX_N", armor="ITAR_BAU_M"
        ),
        items=[NpcThing(item="ITMI_GOLD", count=40), NpcThing(item="ITFO_APPLE", count=5)],
        routine=[
            RoutineEntry(start="06:00", end="21:00", state="ZS_STAND_ARMSCROSSED", waypoint=by_day),
            RoutineEntry(start="21:00", end="06:00", state="ZS_STAND_WP", waypoint=by_night),
        ],
    )
    return spec, by_day


async def cast_character(link: GameLink, actors: Cast, slot: int = 7) -> dict[str, str]:
    """Stage 2 of the director as author, layer 2 (docs/director.md): a character of the director's, on the
    game.

    A peddler at Lobart's farm, made like a nameless townsman: his own name, guild, look, things and day.
    A save before he comes (`slot`), one after (`slot` + 1). Checks:
    - rejected: a description with a person of the plot as the template and a known face is refused, and
      nothing is made;
    - made: he is in the world by his name, at his point;
    - guild, look, things: of the guild, in the clothes and with the things he was given;
    - day: his routine is the one given, not the template's;
    - again: asked for once more, the game leaves him as he is (`there`), still one;
    - after_load: after a load of the save made after he came, the server asks for him again and the game
      has him from the save: one, the same name, guild, clothes, things and day;
    - before: after a load of the save made before he came, he is not in the world and not on the branch."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)

    found = await _peddler(link, actors)
    if isinstance(found, str):
        return _logged({"made": f"failed ({found})"})
    spec, by_day = found
    me = spec.npc

    async def save(at: int) -> None:
        saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
        await link.debug("save", at)
        await saving

    async def load(at: int) -> None:
        loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
        await link.debug("load", at)
        await loaded
        await link.wait_world()
        await asyncio.sleep(2)  # the server asks the game for the branch's characters again

    async def seen() -> tuple[str, str]:
        n = (await link.story_state(npcs=[me])).get("npcs", {}).get(me, {})
        return n.get("state", "absent"), n.get("name", "")

    async def count() -> int:
        return sum(1 for p in await link.story_people() if p["npc"] == me)

    async def his() -> dict[str, Any]:
        self_ = ["inst", "SELF"]
        armor = ["call", "HLP_GETINSTANCEID", ["call", "NPC_GETEQUIPPEDARMOR", self_]]
        return {
            "guild": await link.evaluate(["==", ["field", self_, "GUILD"], ["var", "GIL_BAU"]], npc=me),
            "armor": await link.evaluate(["==", armor, ["sym", "ITAR_BAU_M"]], npc=me),
            "gold": await link.evaluate(["call", "NPC_HASITEMS", self_, ["sym", "ITMI_GOLD"]], npc=me),
            "apples": await link.evaluate(["call", "NPC_HASITEMS", self_, ["sym", "ITFO_APPLE"]], npc=me),
        }

    def minutes(clock: str) -> int:
        return int(clock[:2]) * 60 + int(clock[3:])

    given = [(minutes(e.start), minutes(e.end), e.state, e.waypoint) for e in spec.routine]

    async def day() -> list[tuple[Any, ...]]:
        person = next((p for p in await link.story_people() if p["npc"] == me), {})
        return [(e["start"], e["end"], e["state"], e["waypoint"]) for e in person.get("routine", [])]

    def dressed(h: dict[str, Any]) -> bool:
        return bool(h["guild"]) and bool(h["armor"]) and h["gold"] >= 40 and h["apples"] >= 5

    await save(slot)  # before he comes

    assert spec.look is not None
    # a person of the plot to be made like, the face of one: refused before the game hears of it
    bad = spec.model_copy(
        update={"like": "BAU_950_LOBART", "look": spec.look.model_copy(update={"face": "FACE_N_LEE"})}
    )
    refused, why = await actors.introduce(bad)
    check("rejected", refused is None and len(why) == 2 and await seen() == ("absent", ""), "; ".join(why))

    made, why = await actors.introduce(spec)
    check(
        "made",
        made is not None and made.get("state") == "made" and await seen() == ("alive", "Гарет"),
        made or why,
    )
    if made is None:
        return _logged(results)
    await link.debug("player_goto", waypoint=by_day)
    await asyncio.sleep(1)
    near = (await link.story_state(npcs=[me], near="PC_HERO")).get("npcs", {}).get(me, {})
    check("at", near.get("distance", 1e9) < 1500, f"{near} from {by_day}")
    ok = await _until_async(his, dressed, 10)  # the clothes go on with his first steps (AI_EquipArmor)
    h = await his()
    check("look", ok, h)
    now = await day()
    check("day", now == given, f"{now} for {given}")
    again = await link.story_npc(spec)
    check("again", again.get("state") == "there" and await count() == 1, again)

    await save(slot + 1)
    await load(slot + 1)
    h, now, n = await his(), await day(), await count()
    check(
        "after_load",
        await seen() == ("alive", "Гарет")
        and n == 1
        and dressed(h)
        and now == given
        and actors.characters() == [spec],
        f"{n} of him; {h}; {now}",
    )

    await load(slot)
    check(
        "before",
        await seen() == ("absent", "") and actors.characters() == [],
        f"{await seen()}; {[c.npc for c in actors.characters()]}",
    )
    return _logged(results)


# Garet as a person (layer 3): a card, a voice, a past, what he knows, whom he knows
GARET = Person(
    ru="Ты — Гарет, бродячий торговец. Скуп на слова, но честен в цене и не любит, когда торгуются зря.",
    en="A taciturn peddler who keeps his prices fair.",
    greed=3,
    honesty=4,
    courage=2,
    talk=2,
    pushy=3,
    strangers=4,
    voice="A weathered peddler in his forties with a low, dry male voice, speaking unhurriedly",
    past="Ты пришёл из Венгарда с последним кораблём и с тех пор торгуешь по фермам.",
    knows=("У каменных ступеней к башне Ксардаса засели бандиты.",),
    ties=(PersonTie(npc=HILDA, how="Хильда с фермы Лобарта: берёшь у неё молоко и сыр"),),
)


async def cast_person(
    link: GameLink, actors: Cast, dialogue: DialogueManager, rumors: Rumors, slot: int = 7
) -> dict[str, str]:
    """Stage 2 of the director as author, layer 3 (docs/director.md): a character of the director's is a
    person, on the game. Garet the peddler (as in `cast`) comes with who he is (GARET). Checks:
    - persona: his persona is his card, past, knowledge and acquaintances, in a voice of his own;
    - talk: the hero speaks to him, and his model has all that in front of it;
    - knows_hilda: Hilda and he are acquainted (the rumors' ties), and with him beside her she calls him by
      name, not «незнакомец»;
    - after_load: after a save and a load he is the same person, in the same voice."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    found = await _peddler(link, actors)
    if isinstance(found, str):
        return _logged({"made": f"failed ({found})"})
    spec, by_day = found
    me = spec.npc
    made, why = await actors.introduce(spec, GARET)
    check("made", made is not None and made.get("state") == "made", made or why)
    if made is None:
        return _logged(results)

    persona = dialogue.personas.get(me, spec.name)
    parts = (GARET.ru, GARET.past, GARET.knows[0], GARET.ties[0].how)
    check(
        "persona",
        all(t in persona.instructions for t in parts)
        and persona.voice == GARET.voice
        and "~" in persona.voice_id,
        persona.voice_id,
    )

    def told(llm: FakeBackend) -> str:
        return "\n".join([llm.calls[0][0], *(str(getattr(m, "content", "")) for m in llm.calls[0][1])])

    # the hero speaks to him
    await link.debug("player_goto", waypoint=by_day)
    await asyncio.sleep(1)
    dialogue.llm = llm = FakeBackend("Гарет я. Торгую понемногу.", delay=0)
    await link.debug("type", text="Гарет, кто ты такой?")
    ok = await _until(lambda: bool(llm.calls), 20)
    prompt = told(llm) if ok else ""
    check("talk", ok and all(t in prompt for t in parts), f"{len(llm.calls)} call(s)")
    await _until(lambda: not dialogue.speaking(me), 15)

    # Hilda knows him: beside her, he is Garet to her
    ok = await _until(lambda: rumors.social is not None and me in rumors.social.ties.get(HILDA, {}), 15)
    bonds = rumors.social.ties[HILDA][me].bonds if ok and rumors.social is not None else []
    check("tie", ok and bonds == [GARET.ties[0].how], bonds)
    await link.debug("player_goto", to=HILDA, distance=250)
    # the talk held him to listen: free, he steps beside her
    for _ in range(20):
        try:
            await link.detach(me)
        except GameError:
            pass
        try:
            await link.debug("npc_goto", npc=me, to=HILDA, distance=150, face=HILDA)
            break
        except GameError:
            await asyncio.sleep(0.5)
    await asyncio.sleep(2)
    for _ in range(40):  # her own greeting may start as he comes in
        if not (await link.story_state()).get("dialog"):
            break
        await asyncio.sleep(0.5)
    dialogue.llm = llm = FakeBackend("Это Гарет, торговец.", delay=0)
    await link.debug("type", text="Хильда, кто это рядом с тобой?")
    ok = await _until(lambda: bool(llm.calls), 20)
    prompt = told(llm) if ok else ""
    seen = [line for line in prompt.split("\n") if "Гарет" in line or "незнакомец" in line]
    check("knows_hilda", ok and any("Гарет" in line for line in seen), seen[:4])
    await _until(lambda: not dialogue.speaking(HILDA), 15)

    # a save and a load
    saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
    await link.debug("save", slot)
    await saving
    loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
    await link.debug("load", slot)
    await loaded
    await link.wait_world()
    await asyncio.sleep(2)
    again = dialogue.personas.get(me, spec.name)
    check(
        "after_load",
        actors.person(me) == GARET
        and again.voice_id == persona.voice_id
        and again.instructions == persona.instructions,
        again.voice_id,
    )
    return _logged(results)


async def own_symbols(link: GameLink, slot: int = 7) -> dict[str, str]:
    """Stage 2 of the director as author, layer 1 (docs/director.md): script symbols of our own on the game.

    A story variable, an npc of the director's made like a nameless farmer of the scripts, another variable
    after it; then two saves and loads, a variable changed in between. Checks:
    - made: the npc stands in front of the hero, by the farmer's name, a symbol of the npc kind;
    - like: its day and its clothes are the farmer's (the farmer's code made it, on it and not on the farmer),
      and the farmer is still one of his own;
    - after_load, again: after each load the npc is there once, where it stood, bound to its own symbol
      (Hlp_GetInstanceID of it), and the variables have the values they had at the save;
    - vanilla: Hilda and the farmer are where and who they were."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    own, first, second = "DIR_NPC_E2E", "DIR_E2E_A", "DIR_E2E_B"

    people = {p["npc"]: p for p in await link.story_people()}
    farmers = sorted(n for n in people if re.fullmatch(r"BAU_\d+_BAUER", n))
    if not farmers:
        return _logged({"made": "failed (no nameless farmer in the world)"})
    like = farmers[0]

    # in the order of the save's entry: a variable, the npc, a variable
    await link.patch_graph([{"op": "vars", "vars": {first: 7}}])
    try:
        made = await link.own_npc(own, like, distance=300)
    except GameError as err:
        return _logged({"made": f"failed ({err})"})
    await link.patch_graph([{"op": "vars", "vars": {second: 9}}])
    # their values the save keeps, not the first ones a patch would make again
    values = {first: 17, second: 19}
    await link.story_effect(HILDA, [{"do": "set", "var": k, "value": v} for k, v in values.items()])

    async def state() -> dict[str, Any]:
        return await link.story_state(
            vars=list(values), npcs=[own, like, HILDA], symbols=[own], near="PC_HERO"
        )

    async def bound() -> Any:
        return await link.evaluate(["call", "HLP_GETINSTANCEID", ["inst", "SELF"]], npc=own)

    def shown(s: dict[str, Any], npc: str) -> tuple[str, str]:
        n = s.get("npcs", {}).get(npc, {})
        return n.get("state", "absent"), n.get("name", "")

    before = await state()
    me = before.get("npcs", {}).get(own, {})
    check(
        "made",
        shown(before, own) == ("alive", people[like]["name"])
        and me.get("distance", 1e9) < 600
        and before.get("symbols", {}).get(own) == "npc",
        f"{made}; {me}",
    )

    def day(person: dict[str, Any]) -> list[dict[str, Any]]:
        keys = ("start", "end", "state", "waypoint")
        return [{k: e.get(k) for k in keys} for e in person.get("routine", [])]

    now = await link.story_people()
    mine = [p for p in now if p["npc"] == own]
    theirs = [p for p in now if p["npc"] == like]
    same = bool(mine) and day(mine[0]) == day(people[like])
    # the farmer's code dressed this one, not the farmer again
    armor = ["call", "NPC_HASEQUIPPEDARMOR", ["inst", "SELF"]]
    dressed = (await link.evaluate(armor, npc=own), await link.evaluate(armor, npc=like))
    check(
        "like",
        same and len(theirs) == 1 and shown(before, like)[0] == "alive" and dressed[0] == dressed[1],
        f"{len(theirs)} {like}; armor {dressed}",
    )
    vanilla = (shown(before, HILDA), shown(before, like))

    async def save_and_load() -> None:
        saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
        await link.debug("save", slot)
        await saving
        loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
        await link.debug("load", slot)
        await loaded
        await link.wait_world()
        await asyncio.sleep(1)

    for name in ("after_load", "again"):
        if name == "again":
            # the next save writes what is now, not what came from the last one
            values[first] += 10
            await link.story_effect(HILDA, [{"do": "set", "var": first, "value": values[first]}])
        at = (await state()).get("npcs", {}).get(own, {}).get("distance", 0)
        await save_and_load()
        after = await state()
        me = after.get("npcs", {}).get(own, {})
        count = sum(1 for p in await link.story_people() if p["npc"] == own)
        try:
            index = await bound()
        except GameError as err:
            index = str(err)
        kept = {k: after.get("vars", {}).get(k) for k in values}
        check(
            name,
            me.get("state") == "alive"
            and count == 1
            and abs(me.get("distance", 1e9) - at) < 300
            and index == made.get("index")
            and kept == values,
            f"{me}; {count} of it; Hlp_GetInstanceID {index}, made {made.get('index')}; {kept}",
        )
    last = await state()
    check(
        "vanilla",
        (shown(last, HILDA), shown(last, like)) == vanilla,
        f"{vanilla} -> {(shown(last, HILDA), shown(last, like))}",
    )
    return _logged(results)


async def story_triggers(link: GameLink, story: StoryPatches, slot: int = 7) -> dict[str, str]:
    """Stage 3 of the director as author, layer 1 (docs/director.md): the triggers of the bridge.

    A trigger for every event, put into the branch like the director's other changes, with a save and a load
    in the middle. Checks:
    - start: a trigger without a condition fires at once, once: it puts a thing at a point nearby;
    - item: the hero picks it up, and a trigger on his having it fires, its self the hero;
    - place: one that repeats fires on every coming into a place, not while he stays or is away;
    - dialog: one on a dialog of the director's fires when it is chosen, its self the npc;
    - after_load: after a save and a load every count is what it was, and none fired again: the first one
      not, though its condition holds, the place's not, though the hero loaded inside it;
    - place_again: coming into the place again after the load counts on;
    - hour: one on the time of day fires when the clock gets there;
    - death: one on the death of an npc of the director's fires when the hero kills it, its self the dead
      and its other the killer."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    own = "DIR_NPC_T"
    start, item_t, place_t, hour_t, death_t, dialog_t = (
        "DIR_E2E_START",
        "DIR_E2E_ITEM",
        "DIR_E2E_PLACE",
        "DIR_E2E_HOUR",
        "DIR_E2E_DEATH",
        "DIR_E2E_DIALOG",
    )
    counts = [start, item_t, place_t, hour_t, death_t, dialog_t]
    marks = ["DIR_E2E_STARTS", "DIR_E2E_GOT", "DIR_E2E_TOLD", "DIR_E2E_DEAD", "DIR_E2E_KILLER"]

    def own_id(of: str) -> Expr:
        return ["call", "HLP_GETINSTANCEID", ["inst", of]]

    def mark(var: str, value: Expr) -> dict[str, Any]:
        return {"do": "set", "target": ["var", var], "value": value}

    def plus(var: str) -> dict[str, Any]:
        return mark(var, ["+", ["var", var], ["int", 1]])

    # the points: a waypoint for the thing, another for the place, apart from each other and from the hero
    snap = await link.snapshot(HILDA, fresh=True)
    if snap.player is None:
        return _logged({"start": "failed (no hero in the snapshot)"})
    hero_at = snap.player.pos

    def far(p: dict[str, Any], q: Sequence[float], cm: float) -> bool:
        return math.dist((p["x"], p["z"]), (q[0], q[2])) >= cm

    points = sorted(
        (w for w in await link.waypoints() if not w.get("fp")),
        key=lambda w: math.dist((w["x"], w["z"]), (hero_at[0], hero_at[2])),
    )
    thing_at = next((w for w in points if far(w, hero_at, 700)), None)
    place_at = (
        next(
            (w for w in points if far(w, hero_at, 1500) and far(w, (thing_at["x"], 0, thing_at["z"]), 1500)),
            None,
        )
        if thing_at
        else None
    )
    if thing_at is None or place_at is None:
        return _logged({"start": "failed (no waypoints around the hero)"})
    a, b = thing_at["name"], place_at["name"]
    # a thing the hero has none of
    thing = None
    for candidate in ("ITMI_SAW", "ITMI_BROOM", "ITMI_RAKE", "ITMI_SCOOP", "ITMI_BRUSH", "ITMI_PLIERS"):
        if await link.evaluate(triggers.hero_has(candidate)) == 0:
            thing = candidate
            break
    if thing is None:
        return _logged({"start": "failed (the hero has every thing of the list)"})
    hour = int((await link.story_state()).get("hour", 8))
    at = ((hour + 6) % 24, (hour + 7) % 24)

    try:
        farmers = sorted(
            p["npc"] for p in await link.story_people() if re.fullmatch(r"BAU_\d+_BAUER", p["npc"])
        )
        await link.own_npc(own, farmers[0], distance=400)
    except (GameError, IndexError) as err:
        return _logged({"start": f"failed (no npc of the director's: {err})"})

    talk = "DIR_E2E_TALK"
    ops: list[dict[str, Any]] = [
        {"op": "vars", "vars": dict.fromkeys(marks, 0)},
        {
            "op": "add_node",
            "node": {
                "id": talk,
                "npc": HILDA,
                "description": "Как дела на ферме?",
                "permanent": True,
                "lines": [{"speaker": "self", "text": "Работы много, как всегда."}],
            },
        },
        triggers.trigger(
            start,
            triggers.STATE,
            [
                {"do": "call", "call": ["call", "WLD_INSERTITEM", ["sym", thing], ["str", a]]},
                plus("DIR_E2E_STARTS"),
            ],
        ),
        triggers.trigger(
            item_t, triggers.STATE, [mark("DIR_E2E_GOT", own_id("SELF"))], when=triggers.hero_has(thing)
        ),
        # the count is all these two do
        triggers.trigger(place_t, triggers.STATE, [], when=triggers.hero_near_point(b, 400), repeat=True),
        triggers.trigger(
            dialog_t, triggers.DIALOG, [mark("DIR_E2E_TOLD", own_id("SELF"))], npc=HILDA, node=talk
        ),
        triggers.trigger(hour_t, triggers.STATE, [], when=triggers.time_between((at[0], 0), (at[1], 0))),
        triggers.trigger(
            death_t,
            triggers.DEATH,
            [mark("DIR_E2E_DEAD", own_id("SELF")), mark("DIR_E2E_KILLER", own_id("OTHER"))],
            npc=own,
        ),
    ]
    try:
        for op in ops:
            await story.apply(op)
    except GameError as err:
        return _logged({"start": f"failed ({err})"})

    async def values() -> dict[str, int]:
        state = await link.story_state(vars=[*counts, *marks])
        return {k: int(v) for k, v in state.get("vars", {}).items()}

    async def goto(point: str) -> None:
        await link.debug("player_goto", waypoint=point)
        await asyncio.sleep(1.0)  # four times a second

    hero = await link.evaluate(own_id("HERO"))
    hilda = await link.evaluate(own_id("SELF"), npc=HILDA)
    mine = await link.evaluate(own_id("SELF"), npc=own)

    await asyncio.sleep(1.0)
    v = await values()
    check("start", v.get(start) == 1 and v.get("DIR_E2E_STARTS") == 1, f"{v.get(start)}×, at {a}")

    await goto(a)
    try:
        await link.debug("player_take", item=thing, distance=500)
    except GameError as err:
        check("item", False, f"no {thing} at {a}: {err}")
    else:
        await asyncio.sleep(1.0)
        v = await values()
        check(
            "item",
            v.get(item_t) == 1 and v.get("DIR_E2E_GOT") == hero,
            f"{v.get(item_t)}×, self {v.get('DIR_E2E_GOT')}, hero {hero}",
        )

    seen = []
    for point in (b, a, b):
        await goto(point)
        seen.append((await values()).get(place_t))
    check("place", seen == [1, 1, 2], f"{b}: {seen}")

    try:
        await link.choose_dialog(HILDA, talk)
        await asyncio.sleep(1.0)
    except GameError as err:
        check("dialog", False, err)
    else:
        v = await values()
        check(
            "dialog",
            v.get(dialog_t) == 1 and v.get("DIR_E2E_TOLD") == hilda,
            f"{v.get(dialog_t)}×, self {v.get('DIR_E2E_TOLD')}, Hilda {hilda}",
        )

    before = await values()
    saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
    await link.debug("save", slot)
    await saving
    loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
    await link.debug("load", slot)
    await loaded
    await link.wait_world()
    await asyncio.sleep(3.0)  # the branch's changes come back, the triggers count again
    after = await values()
    listed = {t["id"]: t for t in await link.triggers()}
    broken = [t["id"] for t in listed.values() if t.get("broken")]
    check(
        "after_load",
        after == before and set(counts) <= set(listed) and not broken,
        f"{before} -> {after}; listed {sorted(listed)}, broken {broken}",
    )

    seen = []
    for point in (a, b):
        await goto(point)
        seen.append((await values()).get(place_t))
    check("place_again", seen == [2, 3], seen)

    await link.debug("time", hour=at[0], minute=30)
    await asyncio.sleep(1.0)
    v = await values()
    check("hour", v.get(hour_t) == 1, f"{v.get(hour_t)}× at {at[0]}:30")

    try:
        await link.debug("kill", instance=own)
        await asyncio.sleep(1.5)  # deaths are looked for twice a second
    except GameError as err:
        check("death", False, err)
    else:
        v = await values()
        check(
            "death",
            v.get(death_t) == 1 and v.get("DIR_E2E_DEAD") == mine and v.get("DIR_E2E_KILLER") == hero,
            f"{v.get(death_t)}×, self {v.get('DIR_E2E_DEAD')} ({mine}), "
            f"other {v.get('DIR_E2E_KILLER')} ({hero})",
        )
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
    standing by, hears it. From then on the turnips count for nothing in Lobart's other dialogs: his work
    clothes cost what they would without them. The hero makes amends: the turnips are his job again, the
    ending pays nothing twice; he brings them for real, and the discount is back."""
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

    # Lobart's dialogs as the lie found out leaves them (consequences.py): the turnips count for nothing
    # there, so his work clothes cost what they cost without them, though the scripts have the quest done
    ok = await _until(lambda: any(f.get("npc") == giver for f in facts("lie_consequences")), 30)
    clothes = await link.story_program("DIA_LOBART_BUYCLOTHES")
    changed = next((f for f in facts("lie_consequences") if f.get("npc") == giver), None)
    check(
        "consequences", ok and clothes.get("patched") is True and RUEBEN not in str(clothes["steps"]), changed
    )
    if "DIA_LOBART_KLEIDUNG" in await link.offered_dialogs(giver):
        await link.choose_dialog(giver, "DIA_LOBART_KLEIDUNG")
    await link.choose_dialog(giver, "DIA_LOBART_BUYCLOTHES")
    price = await var("WERT_LOBARTSRUESTUNG")
    others = [await var(q) == 2 for q in ("MIS_HILDA_PFANNEKAUFEN", "MIS_VINO_WEIN", "MIS_MALETH_BANDITS")]
    expected = 80 - 10 * (await var("LOBART_AGAINSTKING") == 1) - 10 * sum(others)
    turnips = await var(RUEBEN)
    check(
        "no_discount",
        price == expected and turnips == 2,
        f"price {price}, {expected} expected; quest {turnips}",
    )

    # the hero makes amends (amends.py has Lobart ask for them; here they are made at once): the turnips are
    # his job again, for real, and the ending pays nothing a second time
    made = {"fact": "amends", "id": "E2E", "npc": giver, "to": giver, "gold": 0}
    vanilla.chronicle.record({**made, "lie": f"found_out:{giver}:{RUEBEN}", "quest": RUEBEN, "about": giver})
    ok = await _until(lambda: any(f.get("quest") == RUEBEN for f in facts("quest_reopened")), 20)
    await _until(lambda: any(f.get("second_chance") for f in facts("lie_consequences")), 30)
    ending = await link.story_program("DIA_LOBART_RUEBENRUNNING")
    reopened = await var(RUEBEN)
    pays = any(p in str(ending.get("steps")) for p in ("B_GIVEPLAYERXP", "DIA_LOBART_RUEBENRUNNING_GOLD"))
    check(
        "reopened", ok and reopened == 1 and ending.get("patched") is True and not pays, f"quest {reopened}"
    )

    # the turnips, for real: the ending says what it says, pays nothing again, and the lie is made good
    beets = [
        {"do": "create", "item": "ITPL_BEET", "count": 20},
        {"do": "give", "item": "ITPL_BEET", "count": 20},
    ]
    await link.story_effect(giver, beets)
    gold_exp = (["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]], ["field", hero, "EXP"])
    before = [await link.evaluate(e) for e in gold_exp]
    await link.choose_dialog(giver, "DIA_LOBART_RUEBENRUNNING")
    ok = await _until(lambda: any(f.get("quest") == RUEBEN for f in facts("lie_made_good")), 20)
    after = [await link.evaluate(e) for e in gold_exp]
    check("made_good", ok and await var(RUEBEN) == 2 and after == before, f"gold, exp {before} -> {after}")

    # the turnips count again: the discount for them is back
    async def clothes() -> bool:
        return bool((await link.story_program("DIA_LOBART_BUYCLOTHES")).get("patched"))

    await _until_async(clothes, lambda patched: not patched, 30)
    await link.choose_dialog(giver, "DIA_LOBART_BUYCLOTHES")
    price = await var("WERT_LOBARTSRUESTUNG")
    check("discount_back", price == expected - 10, f"price {price}, {expected - 10} expected")
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
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        if self.others is not None and self.only is not None and f"по имени {self.only}" not in instructions:
            async for event in self.others.stream(
                instructions=instructions,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                allowed=allowed,
                cache_key=cache_key,
            ):
                yield event
            return
        self.calls.append(
            (instructions, list(messages), [t for t in tools or [] if allowed is None or t.name in allowed])
        )
        last = messages[-1] if messages else None
        if isinstance(last, ToolResult):
            self.outputs.append(last.output)
            rules, text = self.told, last.output
        else:
            rules, text = self.said, last.content if isinstance(last, Message) else ""
        step = next((s for k, s in rules if k in text), FakeStep(self.default))
        fake = FakeBackend(script=[step], delay=0)
        async for event in fake.stream(
            instructions=instructions,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            allowed=allowed,
            cache_key=cache_key,
        ):
            yield event


async def vanilla_given(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    vanilla: VanillaQuests,
    npc: str = "BAU_4300_ADDON_CAVALORN",
    bandits: tuple[str, ...] = ("BDT_1013_BANDIT_L", "BDT_1014_BANDIT_L", "BDT_1015_BANDIT_L"),
) -> dict[str, str]:
    """A quest of the scripts given and ended in a conversation (the player's game, 2026-10-14: Cavalorn's
    bandits, agreed on in his own words, killed together, counted for nothing). A new game; Cavalorn's model
    scripted unless LLM=chatgpt (on «помогу» give_quest, on «мертвы» accept_quest). Checks:
    - can_give: his situation has the quest he gives in his dialog HELFEN, «Могу я помочь тебе с бандитами?»;
    - given: the hero offers his help in his own words: the quest runs and HELFEN counts as heard (no longer
      in his menu), the journal has it;
    - letter: with the bandits dead, his situation has what he says at the ending, of the letter to Vatras
      (the converter has the ending from B_Addon_Cavalorn_VatrasBrief now);
    - ended: the hero says they are dead: the scripts' ending plays — the quest done, the letter to Vatras its
      next step, +100 experience, 50 gold (the hero has no armour); no lie, they are dead;
    - once: the ending counts as heard, neither dialog is offered."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    quest, letter = "MIS_ADDON_CAVALORN_KILLBRAGO", "MIS_ADDON_CAVALORN_LETTER2VATRAS"
    helfen, ended = "DIA_ADDON_CAVALORN_HELFEN", "DIA_ADDON_CAVALORN_BRAGOKILLED"
    facts = vanilla.chronicle.facts
    hero = ["inst", "HERO"]

    async def var(name: str) -> int | None:
        return (await link.story_state(vars=[name])).get("vars", {}).get(name)

    if isinstance(dialogue.llm, FakeBackend):
        give = ToolCall("give_quest", {"quest": quest}, "g1")
        accept = ToolCall("accept_quest", {"quest": quest}, "a1")
        words = [
            ("помогу", FakeStep("Поможешь? Ну, пошли, вдвоём управимся.", [give])),
            ("мертвы", FakeStep("Все трое? Ну, раз так. Слушай, у меня к тебе ещё дело.", [accept])),
        ]
        dialogue.llm = _ByNpc({"Кавалорн": _OnWords(words, FakeStep("Хм."))}, FakeBackend("Хм.", delay=0))
    await link.debug("skip_important", near=npc)  # his own greeting would stop the hero
    for _ in range(2):  # the goblins by his fire would beat the hero down
        try:
            await link.debug("kill", instance="YGOBBO_GREEN", near=npc, by=npc)
        except GameError as err:
            log.info("vgive: goblin: %s", err)
    await link.debug("player_goto", to=npc, distance=300)
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    if not await _until(lambda: npc in talk.people, 20):
        return _logged({"roster": f"failed ({npc} is not around the hero)"})
    exp = await link.evaluate(["field", hero, "EXP"])
    gold = await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]])

    # he can give the quest his dialog gives
    await vanilla.prepare(npc)
    situation = vanilla.situation(npc) or ""
    check("can_give", "Ты можешь сам дать герою поручение «Бандиты Кавалорна»" in situation, situation[:300])

    # the hero offers his help in his own words
    talk.hear("Кавалорн, давай я помогу тебе с бандитами.", None, {"via": "scenario"})
    ok = await _until(lambda: any(f.get("quest") == quest for f in facts("quest_given")), 60)
    await asyncio.sleep(1)
    menu = await link.offered_dialogs(npc)
    check(
        "given", ok and await var(quest) == 1 and helfen not in menu, f"{quest} = {await var(quest)}; {menu}"
    )

    # the bandits die
    for b in bandits:
        try:
            await link.debug("kill", npc=b)
        except GameError as err:
            log.info("vgive: %s: %s", b, err)
    await asyncio.sleep(2)
    await vanilla.prepare(npc)
    situation = vanilla.situation(npc) or ""
    check("letter", "Ватрас" in situation and "Засчитывая" in situation, situation[:400])

    # the hero says so
    talk.hear("Кавалорн, Браго и его парни мертвы.", None, {"via": "scenario"})
    ok = await _until(lambda: any(f.get("quest") == quest for f in facts("claim")), 60)
    await asyncio.sleep(2)
    claim = next((f for f in facts("claim") if f.get("quest") == quest), {})
    after = (
        await var(quest),
        await var(letter),
        await link.evaluate(["field", hero, "EXP"]),
        await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]]),
    )
    played = ok and after[:2] == (2, 1) and after[2] >= exp + 100 and after[3] >= gold + 50
    check("ended", played and claim.get("truth") is True and not facts("lie"), f"{(exp, gold)} -> {after}")
    menu = await link.offered_dialogs(npc)
    check("once", ended not in menu and helfen not in menu, " ".join(menu))
    return _logged(results)


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
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
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
    # by the roster after the move: one from before it may still have him farther than a name is heard
    if not await _until(lambda: giver in talk.people and talk.people[giver].distance <= 600, 20):
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
    deals: Deals | None = None,
) -> dict[str, str]:
    """The director's storylet of amends (stage 5), on the game. Lobart catches the hero lying; by the next
    morning the rumor has gone round. The director's model, scripted unless LLM=chatgpt, has Andre (who has
    dealings with Lobart) ask for a fine and close joining the militia until then; a live model decides who
    asks and what for. The hero comes, hears the npc out, makes amends, and what was closed opens again.

    With `deals` (amends_deal, docs/npc-tools.md wave 1 step 2) he makes them in conversation instead of
    the dialog menu: he asks to be forgiven, the npc offers a deal that forgives the lie for gold (scripted
    unless LLM=chatgpt: then the two haggle as they like), and he accepts it."""
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
    want = s.gold + (10 if deals is None else 100)  # in a deal the price may go up
    if gold < want:
        await link.story_effect(s.npc, [{"do": "give", "item": "ITMI_GOLD", "count": want - gold}])
        gold = want
    if s.to != s.npc:
        await link.debug("player_goto", to=s.to, distance=250)
        await _until(lambda: s.to in talk.people, 20)
    await asyncio.sleep(1)
    price = s.gold
    if deals is None:
        try:
            await link.choose_dialog(s.to, s.pay)
            paid = await _until(lambda: s.stage == "done", 20)
        except GameError as err:
            paid = False
            log.info("amends: %s", err)
    else:
        offered = await _forgiveness_deal(link, dialogue, talk, deals, s)
        check("offered", offered is not None, offered.terms() if offered else "no deal that forgives it")
        if offered is None:
            return _logged(results)
        price = offered.gold()
        await asyncio.sleep(1.2)
        pressed = await link.press_deal()
        paid = pressed == "sent" and await _until(lambda: s.stage == "done", 20)
    await asyncio.sleep(1)
    left = int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", "ITMI_GOLD"]]) or 0)
    opened = {op["node"] for op in amends.patches.ops() if op.get("op") == "enable"}
    check(
        "paid",
        paid and left == gold - price and set(s.close) <= opened,
        f"{s.description if deals is None else 'a deal'}: {gold} -> {left} gold",
    )
    if deals is not None:
        removed = {op["node"] for op in amends.patches.ops() if op.get("op") == "remove_node"}
        check("menu_clean", {s.talk, s.pay} <= removed, sorted({s.talk, s.pay} - removed))
    line = Trust(amends.chronicle).situation(s.npc) or ""
    check("forgiven", "он загладил вину" in line, line.splitlines()[-1:])
    return _logged(results)


async def _forgiveness_deal(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, deals: Deals, s: Any
) -> Any:
    """The hero asks the one the amends are to be made at to forgive him; it offers a deal that forgives the
    lie (scripted unless the model is live). The open offer, or None."""
    name = talk.people[s.to].name if s.to in talk.people else s.to_name
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    script = dialogue.llm if isinstance(dialogue.llm, ClaimScript) else None
    live = (
        script.others
        if script is not None
        else (None if isinstance(dialogue.llm, FakeBackend) else dialogue.llm)
    )
    if live is None:
        terms = {
            "npc_gives": [],
            "hero_gives": [{"item": GOLD, "count": s.gold}] if s.gold else [],
            "forgive": [s.lie],
            "silence": [],
            "hero_later": [],
            "repay": [],
        }
        offer = FakeStep("Ладно. Плати — и забудем.", [ToolCall("propose_deal", terms, "forgive")])
        dialogue.llm = ClaimScript(said=[("прости", offer)], told=[], default="По рукам.", only=name)
    else:
        dialogue.llm = live

    def forgiving() -> bool:
        deal = deals.open.get(s.to)
        return deal is not None and any(lie.key == s.lie for lie in deal.forgive)

    await link.debug("type", text=f"{name}, прости меня за враньё. Чем мне загладить вину?")
    if not await _until(forgiving, 60) and live is not None:
        await link.debug("type", text=f"{name}, я согласен. Предложи, и по рукам.")
        await _until(forgiving, 60)
    return deals.open.get(s.to) if forgiving() else None


async def m6_knows(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    knowledge: Knowledge,
    npc: str = "NONE_100_XARDAS",
    farmer: str = LOBART,
) -> dict[str, str]:
    """M6 on the game: what an npc knows comes from the scripts' dialogs, as far as the story has got.
    Xardas in chapter 1 knows his talk of chapters 1-2 and nothing of chapter 3. A dialog of his played from
    the menu becomes one the hero has heard. At chapter 3 his talk of chapter 3 opens. Lobart, the hero put
    next to him, knows his field work and what the people around the city say. The knowledge is in the
    request to the model, and recall gives a topic in full. The conversational model is scripted: its
    requests are checked."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    def ids(topics: Sequence[Topic]) -> set[str]:
        return {t.node.id for t in topics}

    def situation(llm: FakeBackend) -> str:
        return next(
            (
                str(m.content)
                for m in llm.calls[0][1]
                if isinstance(m, Message) and m.role == "developer" and "Что ты знаешь" in str(m.content)
            ),
            "",
        )

    await link.wait_world()
    if not await _until(lambda: npc in talk.people, 20):
        return _logged({"roster": f"failed ({npc} is not around the hero)"})

    # 1. chapter 1: his talk of chapters 1-2 (the Eye, the city), none of chapter 3
    await knowledge.prepare(npc)
    view = knowledge.views[npc]
    known = ids(view.told) | ids(view.open)
    later = sorted(known & XARDAS_CH3)
    check(
        "chapter_1",
        view.state.chapter == 1 and "DIA_XARDAS_ARTEFAKT" in known and not later,
        f"chapter {view.state.chapter}; heard {sorted(ids(view.told))}; open {len(view.open)}; later {later}",
    )

    # 2. a dialog of his played from the menu: the hero has heard it
    offered = await link.offered_dialogs(npc)
    pick = next((t.node.id for t in view.open if t.sure and t.node.id in offered), None)
    if pick is None:
        check("heard_in_menu", False, f"nothing open is offered: {' '.join(offered)}")
    else:
        await link.choose_dialog(npc, pick)

        async def heard() -> set[str]:
            await knowledge.prepare(npc)
            return ids(knowledge.views[npc].told)

        ok = await _until_async(heard, lambda told: pick in told, 30)
        check("heard_in_menu", ok, pick)
        await asyncio.sleep(3)  # the dialog's lines

    # 3. a turn: the knowledge is in the request, recall gives a topic in full
    llm = FakeBackend(
        script=[
            FakeStep("Хм…", [ToolCall("recall", {"query": "Глаз Инноса"}, "c1")]),
            FakeStep("Это амулет, в нём сила самого Инноса."),
        ],
        delay=0,
    )
    dialogue.llm = llm
    await link.debug("type", text="Ксардас, что такое Глаз Инноса?")
    await _until(lambda: len(llm.calls) >= 2, 30)
    result = next((i.output for c in llm.calls[1:] for i in c[1] if isinstance(i, ToolResult)), "")
    text = situation(llm) if llm.calls else ""
    check(
        "in_the_turn",
        "Что ты знаешь (сейчас глава 1" in text and "Ты знаешь:" in result and "Глаз Инноса" in result,
        f"{len(text)} chars known, recall {len(result)} chars: {result[:120]!r}",
    )
    await _until(lambda: not dialogue.speaking(npc), 15)

    # 4. chapter 3: his chapter 3 talk opens, that of chapters 1-2 closes (what the hero heard stays)
    await link.story_effect(npc, [{"do": "set", "var": "KAPITEL", "value": 3}])
    await knowledge.prepare(npc)
    ch3 = knowledge.views[npc]
    opened = sorted(ids(ch3.open) & XARDAS_CH3)
    check(
        "chapter_3",
        ch3.state.chapter == 3
        and "DIA_XARDAS_BACKFROMOW" in opened
        and "DIA_XARDAS_WEAPON" not in ids(ch3.open),
        f"open now {opened}",
    )
    await link.story_effect(npc, [{"do": "set", "var": "KAPITEL", "value": 1}])

    # 5. Lobart on his farm: his work and what farmers say
    await link.debug("teleport", near=farmer, distance=300)
    if not await _until(lambda: farmer in talk.people and talk.people[farmer].distance < 800, 20):
        check("farmer", False, f"{farmer} is not around the hero")
        return _logged(results)
    llm = FakeBackend("Работа есть: поле репы за амбаром.", delay=0)
    dialogue.llm = llm
    await link.debug("type", text="Лобарт, есть для меня работа?")
    await _until(lambda: bool(llm.calls), 30)
    text = situation(llm) if llm.calls else ""
    check(
        "farmer",
        "Ты — житель окрестностей." in text and "«Я ищу работу.»" in text and "Что говорят вокруг" in text,
        f"{len(text)} chars known",
    )
    log.info("what %s knows:\n%s", farmer, text)
    return _logged(results)


async def see_perception(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    perception: PerceptionDesk,
    rumors: Rumors,
    npc: str = "NONE_100_XARDAS",
    farmer: str = LOBART,
) -> dict[str, str]:
    """What npc's perceive (wave 1 step 4), on the game, with the engine's own sight. Xardas sees the hero in
    front of him and not through the tower's walls and floors. Lobart calls the people he knows by name and
    the hero, whom he does not know, by his look; in militia armor the hero is a militiaman to him. The view
    is in the conversational model's request (scripted here) and `look` answers; the nearest npc's views
    are pushed once a second for the behavioral model."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if not await _until(lambda: npc in talk.people, 20):
        return _logged({"roster": f"failed ({npc} is not around the hero)"})

    # 1. sight: in front of him he sees the hero; walls and floors hide him even within 20 m
    await link.debug("teleport", near=npc, distance=300)
    await asyncio.sleep(1)
    view = await perception.fresh(npc)
    front = view is not None and view.hero is not None and view.hero.seen
    check(
        "sees_in_front",
        front,
        view.hero.model_dump(include={"distance", "seen"}) if view and view.hero else "",
    )
    spots = []
    for cm in (600, 900, 1200, 1500, 1800):
        try:
            await link.debug("player_goto", near=npc, distance=cm)
        except GameError as err:
            spots.append(f"{cm}: {err}")
            continue
        await asyncio.sleep(1)
        view = await perception.fresh(npc)
        if view is not None and view.hero is not None:
            spots.append((round(view.hero.distance / 100), view.hero.seen))
    hidden = [s for s in spots if isinstance(s, tuple) and not s[1] and s[0] < 20]
    check("not_through_walls", bool(hidden), f"(m, seen) at the spots tried: {spots}")

    # 2. Lobart: whom he knows by name, the hero by his look
    if not await _until(lambda: rumors.social is not None, 30):
        check("acquaintances", False, "no acquaintances: the world's people were not read")
        return _logged(results)
    await link.debug("teleport", near=farmer, distance=300)
    if not await _until(lambda: farmer in talk.people and talk.people[farmer].distance < 800, 20):
        check("farmer", False, f"{farmer} is not around the hero")
        return _logged(results)
    await asyncio.sleep(1)
    await perception.fresh(farmer)
    sight = perception.sight(farmer)
    hero = sight.hero or "" if sight else ""
    check("hero_by_look", "ты его не знаешь" in hero, hero)
    # whom the npc's around call by name, whom by look: over the views pushed near the hero, next to
    # Lobart and then next to the people he knows, until someone sees a person it knows
    named: list[str] = []
    strangers: list[str] = []

    def scan() -> None:
        for v in perception.views():
            people = (perception.sight(v.npc) or Sight("", None, "")).people
            for o in v.sees:
                if o.kind != "human":
                    continue
                if perception.knows(v.npc, o) and o.name in people:
                    named.append(f"{v.name} → {o.name}")
                elif not perception.knows(v.npc, o) and "незнакомец" in people:
                    strangers.append(f"{v.name} → {o.name}")

    scan()
    social = rumors.social
    for tie in sorted(social.ties.get(farmer, {}) if social is not None else [])[:6]:
        if named:
            break
        try:
            await link.debug("teleport", near=tie, distance=300)
        except GameError:
            continue
        await asyncio.sleep(1.5)  # a push or two
        scan()
    check(
        "acquaintances", bool(named), f"by name: {sorted(set(named))}; by look: {sorted(set(strangers))[:6]}"
    )
    await link.debug("teleport", near=farmer, distance=300)
    await asyncio.sleep(1)

    # 3. in militia armor, to Lobart who does not know him, the hero is a militiaman
    await link.debug("player_equip", item="ITAR_MIL_L")
    await asyncio.sleep(1)
    await perception.fresh(farmer)
    sight = perception.sight(farmer)
    hero = sight.hero or "" if sight else ""
    check("fake_guild", "по виду ополченец" in hero, hero)

    # 4. a turn: the view is in the request, `look` answers
    llm = FakeBackend(
        script=[FakeStep("Хм…", [ToolCall("look", {"who": "герой"}, "c1")]), FakeStep("Ополченец, значит?")],
        delay=0,
    )
    dialogue.llm = llm
    await link.debug("type", text="Лобарт, ты меня знаешь?")
    await _until(lambda: len(llm.calls) >= 2, 30)
    request = str(llm.calls[0][1]) if llm.calls else ""
    looked = next((i.output for c in llm.calls[1:] for i in c[1] if isinstance(i, ToolResult)), "")
    check(
        "in_the_turn",
        "по виду ополченец" in request and "Рядом:" not in request and looked.startswith("Герой"),
        looked,
    )

    # 5. the nearest npc's views come by themselves once a second (the behavioral model reads these)
    perception._views.clear()
    pushed = await _until(lambda: perception.view(farmer) is not None, 5)
    check("pushed", pushed, "; ".join(perception.english(farmer)))

    # 6. the dead: a wolf the hero kills in front of Lobart is a wolf to him still, a dead one («о, мёртвый
    # волк»), with its id; `look` at «мёртвый волк» finds it
    await link.debug("spawn", instance="WOLF", near=farmer, distance=600)
    await asyncio.sleep(0.5)
    await link.debug("kill", instance="WOLF", near=farmer)
    await asyncio.sleep(1)
    await perception.fresh(farmer)
    sight = perception.sight(farmer)
    people = sight.people if sight else "(no view)"
    looked = perception.look(farmer, "мёртвый волк")
    check("dead_beast", "туша: Волк" in people and looked.startswith("туша: Волк"), f"{people} | {looked}")

    # 7. places by name (step 5): at Xardas's tower, on Lobart's and Onar's farms, in the city
    if perception.places is None:
        check("places", False, "no book of places (gamedata/places.json)")
        return _logged(results)
    said = []
    for who in (npc, farmer, "BAU_900_ONAR", "VLK_412_HARAD"):
        try:
            await link.debug("teleport", near=who, distance=300)
        except GameError as err:
            said.append(f"{who}: {err}")
            continue
        await asyncio.sleep(1)
        view = await perception.fresh(who)
        place = perception.place(view) if view is not None else None
        sight = perception.sight(who)
        said.append(f"{who}: {sight.place if sight else '(no view)'}" + ("" if place else " [no name]"))
    named = [line for line in said if "[no name]" not in line and "(no view)" not in line and ": " in line]
    check("places", len(named) == 4, " | ".join(said))
    return _logged(results)


TOWNSFOLK = ("MIL_319_PABLO", "VLK_417_CONSTANTINO", "VLK_424_ALWIN")  # in the street by Harad's smithy


async def seen_memory(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    sightings: Sightings,
    farmer: str = LOBART,
    smith: str = "VLK_412_HARAD",
    slot: int = 7,
) -> dict[str, str]:
    """What npc's saw (wave 1 step 6), on the game. At eight in the morning the hero stands before Lobart on
    his farm: Lobart keeps the sighting — when, where by name, how the hero looked. The hero goes to the city
    (Harad sees him there), an hour passes, he comes back and tells Lobart he was in the city all morning:
    Lobart saw him on the farm an hour ago and catches the lie (`catch_lie`; the model scripted unless
    LLM=chatgpt) — his trust, wariness, a rumor. The memory follows the save. The sight of check_claim is the
    engine's: what story_state says the townsman sees is what his view sees."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    desk = sightings.desk
    chronicle = sightings.chronicle
    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)

    # 1. the morning on the farm: Lobart sees the hero
    await link.debug("time", hour=8, minute=0)
    await link.debug("teleport", near=farmer, distance=300)
    if not await _until(lambda: farmer in talk.people and talk.people[farmer].distance < 800, 20):
        return _logged({"farmer": f"failed ({farmer} is not around the hero)"})
    await asyncio.sleep(2)
    view = await desk.fresh(farmer)
    hero = view.hero if view is not None else None
    check(
        "time_in_view",
        view is not None and view.time is not None and hero is not None and hero.waypoint is not None,
        f"time {view.time if view else None}, hero at {hero.waypoint if hero else None}",
    )
    seen = await _until(lambda: bool(sightings.hero_trail(farmer)), 5)
    first = sightings.hero_trail(farmer)[0] if seen else None
    check(
        "sighting",
        first is not None and not first.places[0].startswith("у точки"),
        sightings.words(first) if first else "Lobart kept nothing",
    )

    # 2. to the city: someone there turns to the man who came up and sees him; Lobart's sighting is over
    tried, who = [], []
    for townsman in (smith, *TOWNSFOLK):
        try:
            # not stopped for a scripted talk (Pablo's «you are wanted»): it would hold the game from saving
            await link.debug("skip_important", near=townsman)
            await link.debug("teleport", near=townsman, distance=300)
        except GameError as err:
            tried.append(f"{townsman}: {err}")
            continue
        await asyncio.sleep(1)
        await link.npc_act(townsman, "turn_to")
        await _until(lambda: [n for n in sightings.witnesses() if n != farmer.upper()], 4)
        view = await desk.fresh(townsman)
        tried.append(f"{townsman} {view.state if view else '?'}/{view.body if view else '?'}")
        who = [n for n in sightings.witnesses() if n != farmer.upper()]
        if who:
            smith = townsman
            break
    check(
        "elsewhere",
        bool(who) and all(not sightings.hero_trail(n)[0].places[0].startswith("у точки") for n in who),
        "; ".join(f"{n}: {sightings.words(sightings.hero_trail(n)[0])}" for n in who[:3])
        or f"nobody saw him ({', '.join(tried)})",
    )
    # check_claim's sight is the engine's: story_state `seen` agrees with what that one's view sees
    view = await desk.fresh(smith)
    near = [o.id for o in (view.sees if view else []) if o.kind == "human"][:3]
    around = [p["npc"] for p in await link.story_nearby(smith, 2000)]
    unseen = [n for n in around if n not in near and n.upper() != smith.upper()][:3]
    far = "NONE_100_XARDAS"
    asked = [*near, *unseen, far]
    state = await link.story_state(npcs=asked, near=smith)
    said = {n: (state.get("npcs", {}).get(n) or {}).get("seen") for n in asked}
    agree = (
        bool(near)
        and sum(1 for n in near if said[n]) >= len(near) - 1  # one may have moved meanwhile
        and sum(1 for n in unseen if said[n] is False) >= len(unseen) - 1
        and said[far] is False
    )
    check("story_state_sight", agree, f"in the view {near}, not {unseen}: {said}")

    # 3. an hour later, back on the farm
    morning = first.end if first is not None else 0
    hour, minute = divmod(morning % 1440 + 60, 60)
    await link.debug("time", hour=hour, minute=minute)
    await asyncio.sleep(2)  # the pushes in the city carry the new time: Lobart's sighting is over
    await link.debug("teleport", near=farmer, distance=300)
    back = await _until(lambda: len(sightings.hero_trail(farmer)) >= 2, 10)
    # whom he talks to is who is before him now (the roster comes once a second)
    await _until(lambda: farmer in talk.people and talk.people[farmer].distance < 800, 10)
    await asyncio.sleep(1.5)
    trail = sightings.hero_trail(farmer)
    check(
        "back",
        back and trail[0].start - trail[1].end >= 55,
        " | ".join(
            f"{s.start % 1440 // 60:02d}:{s.start % 60:02d}–{s.end % 1440 // 60:02d}:{s.end % 60:02d}"
            for s in trail[:3]
        ),
    )

    # 4. the lie: «I was in the city all morning»
    name = talk.people[farmer].name if farmer in talk.people else "Лобарт"
    await desk.fresh(farmer)
    situation = sightings.situation(farmer) or ""
    sid = next((m.group(1) for m in re.finditer(r"- (В\d): [^\n]*около часа назад", situation)), "В2")
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    script = None
    if isinstance(dialogue.llm, FakeBackend):
        catch = FakeStep(
            "Врёшь! Я тебя час назад здесь, на ферме, видел.",
            [ToolCall("catch_lie", {"said": "всё утро был в городе", "seen": sid})],
        )
        dialogue.llm = script = ClaimScript(said=[("город", catch)], told=[], default="Ну?", only=name)
    await link.debug("type", text=f"{name}, я всё утро был в городе, только что оттуда.")
    caught = await _until(lambda: any(f.get("said") for f in chronicle.facts("caught")), 60)
    if script is not None:
        request = str(script.calls[0][1]) if script.calls else ""
        check(
            "in_the_turn",
            "Что ты видел сам" in request and f"{sid}: сегодня" in request and "около часа назад" in request,
            next((line for line in situation.splitlines() if line.startswith(f"- {sid}")), situation[:200]),
        )
    fact = next((f for f in chronicle.facts("caught") if f.get("said")), None)
    conv = dialogue.conversations.get(farmer)
    replies = [e.content for e in conv.memory.entries if e.role == "assistant"][-2:] if conv else []
    check("caught", caught, f"{fact.get('title')!r}: {fact.get('how')}; said {replies}" if fact else replies)
    check(
        "wary", conv is not None and conv.intent.attitude in ("wary", "hostile"), conv.intent if conv else ""
    )
    lied = Trust(chronicle).of(farmer).lied
    check("trust", bool(lied), lied)

    # 5. the memory follows the save
    head = sightings.memory.head(link.session.branch) if link.session else 0
    await asyncio.sleep(0.5)  # the game has the head by now
    for attempt in range(30):
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
    loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load"))
    await link.debug("load", slot)
    session = await loaded
    await link.wait_world()
    trail = sightings.hero_trail(farmer)
    check(
        "follows_the_save",
        saved.seq >= head and session.branch != saved.branch and len(trail) >= 2,
        f"{session.branch} from {session.parent}@{session.parent_seq}: {len(trail)} sighting(s) of the hero",
    )

    return _logged(results)


def _trace_evidence_line(prompt: str, observation: Sighting) -> tuple[str, str]:
    """Find this trace's actual evidence id, even when a new hero sighting has renumbered the В lines."""
    if not observation.trace_kind or not observation.places:
        return "", ""
    stamp = f"{observation.start % 1440 // 60:02d}:{observation.start % 60:02d}"
    for line in prompt.splitlines():
        match = re.match(r"- (В\d+): ", line)
        if (
            match
            and stamp in line
            and trace_name(observation.trace_kind, observation.name) in line
            and observation.places[0] in line
        ):
            return match[1], line
    return "", ""


async def trace_memory(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    sightings: Sightings,
    log_dir: str,
    farmer: str = LOBART,
) -> dict[str, str]:
    """Native arrows at Lobart's house: an unseen NPC's arrow proves no culprit; a new arrow after
    Lobart saw the hero there contradicts his claim that it is a week old. The bench only shoots and
    waits for the server; views, memory, requests, tools and consequences use the conversation path."""
    results: dict[str, str] = {}
    desk, chronicle = sightings.desk, sightings.chronicle
    directory = Path(log_dir)
    original = dialogue.llm
    control_denial = "это не я стрелял в твою стену, я ничего не трогал, я только пришел"
    age_lie = "стрела эта давно там торчит, с прошлой недели"
    house = "NW_FARM1_INHOUSE_01"
    parked = "NW_FARM1_PATH_CITY_16"
    target = "BAU_951_HILDA"

    def check(name: str, ok: bool, detail: object = "") -> bool:
        results[name] = f"{'done' if ok else 'failed'} ({detail})"
        log.info("trace %-20s %s", name, results[name])
        return ok

    async def mark(name: str) -> None:
        path = directory / "events.jsonl"
        if not await _until(
            lambda: (
                path.exists()
                and any(r.get("kind") == "mark" and r.get("text") == name for r in read_trace(path))
            ),
            30,
        ):
            raise RuntimeError(f"trace bench did not reach {name}")

    async def release(name: str) -> None:
        # The controlled observer's overlay releases waitreply without a model turn or memory entry.
        await link.send(SayCmd(npc=farmer, line=f"AIB_TRACE_{name}", text=".", overlay=True))

    async def move(
        who: str,
        waypoint: str | None = None,
        *,
        near: str | None = None,
        distance: int = 0,
        pos: tuple[float, float, float] | None = None,
        attach: bool = False,
    ) -> None:
        try:
            await link.detach(who)
        except GameError as err:
            if "npc is not attached" not in str(err):
                raise
        if pos is not None:
            hero = (await link.snapshot(who, fresh=True)).player
            assert hero is not None
            await link.debug("player_goto", pos=pos)
            await asyncio.sleep(1)  # settle on walkable ground before copying the position to the frozen NPC
            await link.debug("npc_goto", npc=who, to="PC_HERO", distance=0)
            await link.debug("player_goto", pos=hero.pos)
        else:
            await link.debug(
                "npc_goto",
                npc=who,
                waypoint=waypoint,
                to="PC_HERO" if near == "PLAYER" else near,
                distance=distance,
                face="PC_HERO",
            )
        if attach:
            await attach_when_free(link, who)

    def turns() -> list[dict[str, Any]]:
        return [
            r
            for p in sorted((directory / "traces").glob("*.jsonl"))
            for r in read_trace(p)
            if r.get("type") == "turn" and r.get("npc") == farmer
        ]

    async def deny(*, observation: Sighting | None = None) -> dict[str, Any]:
        denial = f"Это не я. Да и {age_lie}" if observation is not None else control_denial
        before = {r["id"] for r in turns()}
        await link.debug("teleport", near=farmer, distance=250)
        await _until(lambda: farmer in talk.people and talk.people[farmer].distance < 800, 10)
        await desk.fresh(farmer)
        if isinstance(original, FakeBackend):
            step = FakeStep("Не видел, кто стрелял. Одной стрелы мало, чтобы обвинять тебя.")
            if observation is not None:
                sid, _ = _trace_evidence_line(sightings.situation(farmer) or "", observation)
                step = FakeStep(
                    "Не с прошлой недели! Стрела свежая, и я только что её увидел.",
                    [ToolCall("catch_lie", {"said": age_lie, "seen": sid})],
                )
            dialogue.llm = ClaimScript([(denial, step)], [], "Я запомню.", only="Лобарт")
        await link.debug("type", text=f"Лобарт, {denial}.")
        completed = await _until(
            lambda: any(
                r["id"] not in before and denial in r.get("trigger", {}).get("text", "") for r in turns()
            ),
            90,
        )
        await _until(lambda: not dialogue.speaking(farmer), 30)
        rows = [
            r for r in turns() if r["id"] not in before and denial in r.get("trigger", {}).get("text", "")
        ]
        row = rows[-1] if rows else {}
        log.info(
            "trace %s reply: %s",
            "positive" if observation is not None else "control",
            [s.get("text") for s in row.get("steps", [])],
        )
        check(
            "positive_turn" if observation is not None else "control_turn",
            completed and row.get("status") == "completed",
            row.get("status"),
        )
        return row

    def calls(row: dict[str, Any]) -> list[dict[str, Any]]:
        return [c for s in row.get("steps", []) for c in s.get("calls", []) if c.get("name") == "catch_lie"]

    def request(row: dict[str, Any]) -> str:
        return "\n".join(
            str(i.get("content", ""))
            for s in row.get("steps", [])
            for i in s.get("added", [])
            if i.get("role") == "developer"
        )

    def evidence(trace_id: str) -> tuple[str, str]:
        situation = sightings.situation(farmer) or ""
        sid = next(
            (
                sid
                for sid, s in sightings._shown.get(farmer, {}).items()
                if s.who == f"TRACE:{trace_id}:arrow"
            ),
            "",
        )
        line = next((line for line in situation.splitlines() if sid and line.startswith(f"- {sid}:")), "")
        return sid, line

    def shots(actor: str) -> list[str]:
        return [
            r.get("text", "")
            for r in read_trace(directory / "events.jsonl")
            if r.get("kind") == "note"
            and r.get("text", "").startswith(("arrow shot:", "dice shot:"))
            and f"by {actor}" in r["text"]
        ]

    await link.wait_world()
    await mark("trace_ready")
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    for who in (farmer, VINO, target):
        await link.debug("skip_important", near=who)
    await link.debug("player_hp", hp=5000)
    # Changing the clock relocates scripted routines, even for an attached body: set it before staging.
    await link.debug("time", hour=8, minute=0)
    # Lobart is away for both shots. The wall is only the arrow's target, not the observer's hiding place.
    await move(farmer, parked, attach=True)
    await link.debug("player_goto", waypoint=house)
    inside = (await link.snapshot(target, fresh=True)).player
    if inside is None:
        return _logged({"house": "failed (no player position)"})
    points = await link._request(DebugCmd(req=link._next_id("trace-"), cmd="waypoints"), 10)
    waypoints = points.value
    distant = sorted(
        [
            p
            for p in waypoints
            if p["name"].startswith("NW_FARM1_")
            and not p["fp"]
            and math.dist((p["x"], p["y"], p["z"]), inside.pos) > 6000
        ],
        key=lambda p: math.dist((p["x"], p["y"], p["z"]), inside.pos),
    )
    if not distant:
        return _logged({"preparation": "failed (no farm waypoint beyond 60 m)"})
    parked = distant[0]["name"]
    await move(farmer, parked, attach=True)
    # Native ray queries choose a wooden door/wall: stone reflects arrows rather than retaining them.
    candidates = [
        (
            inside.pos[0] + 750 * math.cos(angle * math.pi / 72),
            inside.pos[1],
            inside.pos[2] + 750 * math.sin(angle * math.pi / 72),
        )
        for angle in range(144)
    ]
    rays = [
        (*point[:1], point[1] + height, point[2], inside.pos[0], inside.pos[1] + 140, inside.pos[2], 1)
        for height in (140, 170)
        for point in candidates
    ]
    (directory / "trace-rays.tsv").write_text(
        "".join(" ".join(str(value) for value in ray) + "\n" for ray in rays), encoding="utf-8"
    )
    await release("wall_query")
    await mark("wall_sample")
    surfaces = {
        r["id"]: r
        for r in read_trace(directory / "physics-results.jsonl")
        if r.get("kind") == "ray"
        and r.get("hit")
        and r.get("material") == 3
        and 0.2 <= r.get("fraction", 0) <= 0.9
    }
    # Vob rays may have a zero normal. Keep the shooter at the ray's supported
    # origin, rather than placing him in the hit surface and letting collision
    # recovery move him through the wall. Both nock-height rays must hit wood:
    # an occluded NPC view alone does not guarantee an arrow misses a doorway.
    candidates = [
        point
        for angle, point in enumerate(candidates)
        if angle in surfaces and angle + 144 in surfaces
    ]
    outside = None
    for point in candidates:
        await move(VINO, pos=point, attach=True)
        await link.act(VINO, "turn_to", npc="PLAYER")
        view = await desk.fresh(VINO)
        position = (await link.snapshot(VINO, fresh=True)).npc.pos
        if (
            view is not None
            and view.hero is not None
            and not view.hero.seen
            and view.hero.distance < 1500
            and abs(position[1] - inside.pos[1]) < 180
            and math.dist((position[0], position[2]), (point[0], point[2])) < 20
        ):
            outside = position
            break
    if not check(
        "house_wall", bool(outside), f"inside {house}, outside {outside}; {len(candidates)} candidates"
    ):
        return _logged(results)
    # Settling may change ground height. Check the actual shot corridor again
    # before releasing the archer, at and around the bow's nock height.
    confirmation = [
        (
            outside[0],
            outside[1] + height,
            outside[2],
            inside.pos[0],
            inside.pos[1] + 140,
            inside.pos[2],
            1,
        )
        for height in (140, 155, 170)
    ]
    (directory / "trace-shot-rays.tsv").write_text(
        "".join(" ".join(str(value) for value in ray) + "\n" for ray in confirmation),
        encoding="utf-8",
    )
    await release("shot_query")
    await mark("shot_sample")
    corridor = [
        r for r in read_trace(directory / "physics-results.jsonl") if r.get("kind") == "ray"
    ]
    if not check(
        "wooden_shot_corridor",
        len(corridor) == len(confirmation)
        and all(
            r.get("hit") and r.get("material") == 3 and 0.2 <= r.get("fraction", 0) <= 0.9
            for r in corridor
        ),
        corridor,
    ):
        return _logged(results)
    view = await desk.fresh(farmer)
    if not check(
        "control_unseen",
        view is not None
        and view.hero is not None
        and not view.hero.seen
        and view.hero.distance > 5000
        and not sightings.hero_trail(farmer),
        f"parked {parked}; {view.hero if view else 'no view'}",
    ):
        return _logged(results)
    shooter_view = await desk.fresh(VINO)
    baseline = {t.id for t in shooter_view.traces} if shooter_view else set()
    heard = len(sightings.mind(farmer).heard)
    await release("control_start")
    await mark("control_shot")
    check("npc_shot", bool(shots(VINO)) and not shots("PC_HERO"), shots(VINO))
    check("control_not_heard", len(sightings.mind(farmer).heard) == heard, f"heard {heard}")
    await move(farmer, near=VINO, attach=True)
    await move(VINO, parked)
    await link.act(farmer, "turn_to", npc="PLAYER")
    view = await desk.fresh(farmer)
    control = (
        next((t for t in view.traces if t.kind == "arrow" and t.id not in baseline), None) if view else None
    )
    if not check(
        "control_trace",
        control is not None and not sightings.hero_trail(farmer),
        view.model_dump() if view else "no view",
    ):
        return _logged(results)
    sid, line = evidence(control.id)
    control_observed = sightings.mind(farmer).traces[f"TRACE:{control.id}:arrow"]
    await asyncio.sleep(5)  # the trace predates the hero's first visible arrival by at least a game minute
    row = await deny()
    control_sid, control_line = _trace_evidence_line(request(row), control_observed)
    check(
        "control_in_request",
        bool(control_sid) and "Следы, которые ты видел" in request(row),
        control_line,
    )
    check("control_no_catch", not calls(row) and not chronicle.facts("caught"), calls(row))
    talk.end("gone")

    # A new hour, a witnessed arrival, then Lobart is too far to see the actual shot.
    await mark("positive_ready")
    await link.debug("time", hour=9, minute=0)
    await move(VINO, parked)
    await link.debug("teleport", near=farmer, distance=300)
    await desk.fresh(farmer)
    trail = sightings.hero_trail(farmer)
    hero_start = trail[0].start if trail else -1
    if not check(
        "hero_nearby",
        bool(trail) and trail[0].end % 1440 >= 9 * 60,
        sightings.words(trail[0]) if trail else "no sighting",
    ):
        return _logged(results)
    await move(farmer, parked, attach=True)
    await link.debug("player_goto", pos=outside)
    view = await desk.fresh(farmer)
    check(
        "shot_unseen",
        view is not None and view.hero is not None and not view.hero.seen and view.hero.distance > 2000,
        view.hero if view else "no view",
    )
    baseline = set(sightings.mind(farmer).traces)
    heard = len(sightings.mind(farmer).heard)
    await release("hero_start")
    await mark("hero_shot")
    check("hero_shot", bool(shots("PC_HERO")), shots("PC_HERO"))
    hidden = await desk.fresh(farmer)
    check(
        "act_not_seen",
        hidden is not None
        and hidden.hero is not None
        and not hidden.hero.seen
        and hidden.hero.distance > 2000
        and set(sightings.mind(farmer).traces) == baseline,
        hidden.hero if hidden else "no view",
    )
    check("shot_not_heard", len(sightings.mind(farmer).heard) == heard, f"heard {heard}")
    await move(farmer, near="PLAYER", attach=True)
    await link.debug("player_goto", waypoint=house)
    await link.act(farmer, "turn_to", npc="PLAYER")
    view = await desk.fresh(farmer)
    arrow = (
        next((t for t in view.traces if t.kind == "arrow" and f"TRACE:{t.id}:arrow" not in baseline), None)
        if view
        else None
    )
    if not check(
        "new_trace", arrow is not None and arrow.age is not None, view.model_dump() if view else "no view"
    ):
        return _logged(results)
    sid, line = evidence(arrow.id)
    observed = sightings.mind(farmer).traces[f"TRACE:{arrow.id}:arrow"]
    check(
        "trace_memory",
        bool(sid)
        and bool(line)
        and 0 <= arrow.age <= 5
        and view.time - arrow.age <= observed.start <= view.time,
        f"{line}; appearance {view.time - arrow.age}, age {arrow.age} min",
    )
    row = await deny(observation=observed)
    prompt = request(row)
    actual_sid, actual_line = _trace_evidence_line(prompt, observed)
    fresh_trace = re.search(
        rf"{re.escape(trace_name(observed.trace_kind, observed.name))} "
        rf"\([^)]*{re.escape(observed.places[0])}[^)]*давность [0-5] мин\)",
        prompt,
    )
    # Relative age may change during the turn; the stable time/name/place and the В id must survive.
    check(
        "trace_in_request",
        bool(actual_sid) and "Следы, которые ты видел" in prompt and bool(fresh_trace),
        f"{actual_line}; {fresh_trace.group() if fresh_trace else 'fresh trace absent'}",
    )
    hero_ids = [
        sid
        for sid, s in sightings._shown.get(farmer, {}).items()
        if s.who == "PLAYER" and s.start == hero_start
    ]
    hero_lines = [
        line for line in prompt.splitlines() if any(line.startswith(f"- {sid}:") for sid in hero_ids)
    ]
    check("hero_in_request", any("ферм" in line for line in hero_lines), hero_lines)
    caught_calls = calls(row)
    check(
        "catch_uses_trace",
        any(
            c.get("arguments", {}).get("seen") == actual_sid and c.get("outcome") == "accepted"
            for c in caught_calls
        ),
        caught_calls,
    )
    await _until(lambda: bool(chronicle.facts("caught")), 15)
    caught = chronicle.facts("caught")
    check(
        "caught",
        len(caught) == 1
        and bool(_trace_evidence_line(f"- В1: {caught[0].get('how', '')}", observed)[0])
        and "сам видел:" in caught[0].get("how", ""),
        caught,
    )
    dialogue.llm = original
    await release("done")
    return _logged(results)


# chapter 3 dialogs of Xardas's (KAPITEL == 3 in their conditions, or waiting for one that has it)
XARDAS_CH3 = {
    "DIA_XARDAS_BACKFROMOW",
    "DIA_XARDAS_DMTSINDDA",
    "DIA_XARDAS_INNOSEYEBROKEN",
    "DIA_XARDAS_RITUALREQUEST",
    "DIA_XARDAS_BINGESPANNT",
    "DIA_XARDAS_WASNUN",
    "DIA_XARDAS_PYROWILLNICHT",
    "DIA_XARDAS_BEREIT",
    "DIA_XARDAS_WARUMNICHTJETZT",
}


async def deal_window(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, deals: Deals, seller: str = LOBART
) -> dict[str, str]:
    """A deal (docs/npc-tools.md, wave 1), on the game. The hero asks Lobart to sell him something he has;
    Lobart (scripted unless LLM=chatgpt) offers it for 50 gold: Y waits, the hero lacks the gold. He declines
    (N): the window goes, Lobart hears of it and offers it for 40; after haggling for 30 the window shows a
    new version of that deal, Y right after the change waits. Then the deal is done: the gold and the thing
    change hands at once, the window goes, Lobart answers."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    hero = ["inst", "HERO"]

    async def has(item: str) -> int:
        return int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", item]]) or 0)

    await link.debug("skip_important", near=seller)  # his important dialog would start as the hero comes
    await link.debug("player_goto", to=seller, distance=250)
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    if not await _until(lambda: seller in talk.people, 20):
        check("near", False, seller)
        return _logged(results)
    name = talk.people[seller].name

    # something he has to sell (an apple if nothing), and 10 gold on the hero
    snap = await link.snapshot(seller, fresh=True)
    wares = [i for i in snap.npc.inventory if not i.equipped and i.item != GOLD and i.count > 0]
    if not wares:
        await link.story_effect(seller, [{"do": "give", "item": "ITFO_APPLE", "count": 2, "to": "self"}])
        snap = await link.snapshot(seller, fresh=True)
        wares = [i for i in snap.npc.inventory if not i.equipped and i.item != GOLD and i.count > 0]
    ware = wares[0]
    gold = await has(GOLD)
    if gold > 10:
        await link.story_effect(seller, [{"do": "give", "item": GOLD, "count": gold - 10, "to": "self"}])
    elif gold < 10:
        await link.story_effect(seller, [{"do": "give", "item": GOLD, "count": 10 - gold}])
    had = await has(ware.item)

    if isinstance(dialogue.llm, FakeBackend):

        def offer(price: int) -> ToolCall:
            terms = {
                "npc_gives": [{"item": ware.item, "count": 1}],
                "hero_gives": [{"item": GOLD, "count": price}],
            }
            return ToolCall("propose_deal", terms, f"deal{price}")

        dialogue.llm = ClaimScript(
            said=[
                ("продай", FakeStep(f"{ware.name}? Пятьдесят золотых.", [offer(50)])),
                ("отказался", FakeStep("Ну, как знаешь. Сорок?", [offer(40)])),
                ("дорого", FakeStep("Ладно, тридцать — и по рукам.", [offer(30)])),
            ],
            told=[],
            default="По рукам. Хорошая вещь, не пожалеешь.",
            only=name,
        )

    await link.debug("type", text=f"{name}, продай мне {ware.name}.")
    ok = await _until(lambda: seller in deals.open, 30)
    window = await link.deal_window() or {}
    check("shown", ok and window.get("version") == 1, window)
    if not ok:
        return _logged(results)
    first = deals.open[seller]

    # 10 gold of 50: Y waits, the window marks the gold
    await asyncio.sleep(1.2)
    pressed = await link.press_deal()
    window = await link.deal_window() or {}
    check("waits_gold", pressed == "missing" and window.get("missing") == [GOLD], f"{pressed}, {window}")

    # he declines (N): the window goes at once; Lobart hears of it and offers it for 40
    declined = await link.decline_deal()
    window = await link.deal_window()
    check("declined", declined == "declined" and window is None and first.state == "declined", declined)
    ok = await _until(lambda: seller in deals.open and deals.open[seller].id != first.id, 30)
    window = await link.deal_window() or {}
    check("offered_again", ok and window.get("hero_gives") == [gold_text(40)], window)
    if not ok:
        return _logged(results)
    second = deals.open[seller]

    # 40 gold, and he haggles it down to 30: the same window, a new version, Y waits a moment
    await link.story_effect(seller, [{"do": "give", "item": GOLD, "count": 30}])
    await link.debug("type", text=f"{name}, это дорого, давай за тридцать.")
    ok = await _until(lambda: seller in deals.open and deals.open[seller].version == 2, 30)
    window = await link.deal_window() or {}
    check("haggled", ok and window.get("deal") == second.id and window.get("version") == 2, window)
    pressed = await link.press_deal()
    check("waits_change", pressed == "changed", pressed)

    await asyncio.sleep(1.2)
    pressed = await link.press_deal()
    done = await _until(lambda: bool(deals.chronicle.facts("deal")), 20)
    check("accepted", pressed == "sent" and done, pressed)
    left, got = await has(GOLD), await has(ware.item)
    check("changed_hands", left == 10 and got == had + 1, f"gold 40 -> {left}, {ware.item} {had} -> {got}")
    window = await link.deal_window()
    check("window_gone", window is None and seller not in deals.open, window)
    conv = dialogue.conversations.get(seller)
    answered = await _until(lambda: conv is not None and "assistant" == conv.memory.entries[-1].role, 30)
    check("answered", answered, conv.memory.entries[-1].content if conv and conv.memory.entries else "")
    return _logged(results)


async def contract(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, deals: Deals, npc: str = "NONE_100_XARDAS"
) -> dict[str, str]:
    """Contracts (docs/contracts.md) on the game, with Xardas (scripted unless LLM=chatgpt).
    1. The hero asks for experience: Xardas gives 500 of it and a rune from his stash (he carries none) for
       1 gold. The hero's experience grows, the rune is his, the stash has one less.
    2. The hero asks for a task: kill a wolf, for strength +5 (his learning points, as a teacher takes them)
       and two potions from the stash. A wolf comes and dies by the hero's hand: the game reports the kill,
       the task is done. The hero speaks to Xardas: the code pays before he answers, and he knows it."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    hero = ["inst", "HERO"]

    async def field(name: str, index: int | None = None) -> int:
        expr: list[Any] = ["field", hero, name] if index is None else ["field", hero, name, index]
        return int(await link.evaluate(expr) or 0)

    async def has(item: str) -> int:
        return int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", item]]) or 0)

    await link.debug("skip_important", near=npc)
    await link.debug("player_goto", to=npc, distance=250)
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    if not await _until(lambda: npc in talk.people, 20):
        check("near", False, npc)
        return _logged(results)
    name = talk.people[npc].name
    rune, potion, strength = "ITRU_SUMSKEL", "ITPO_HEALTH_02", 4  # ATR_STRENGTH
    if await has(GOLD) < 1:
        await link.story_effect(npc, [{"do": "give", "item": GOLD, "count": 1}])

    if isinstance(dialogue.llm, FakeBackend):

        def terms(**more: Any) -> dict[str, Any]:
            return {
                "npc_gives": [],
                "hero_gives": [],
                "forgive": [],
                "silence": [],
                "hero_later": [],
                "repay": [],
                "npc_xp": 0,
                "npc_teach": [],
                "task": {"kill": [], "bring": [], "days": 0},
                "reward": {"npc_gives": [], "xp": 0, "teach": []},
                **more,
            }

        now = terms(
            npc_gives=[{"item": rune, "count": 1}], hero_gives=[{"item": GOLD, "count": 1}], npc_xp=500
        )
        task = terms(
            task={"kill": [{"target": "GIL_WOLF", "count": 1}], "bring": [], "days": 0},
            reward={
                "npc_gives": [{"item": potion, "count": 2}],
                "xp": 0,
                "teach": [{"what": "attribute", "arg": "ATR_STRENGTH", "count": 5}],
            },
        )
        dialogue.llm = ClaimScript(
            said=[
                ("опыта", FakeStep("Опыт и руну — за один золотой.", [ToolCall("propose_deal", now, "now")])),
                (
                    "поручени",
                    FakeStep("Убей волка — научу тебя силе.", [ToolCall("propose_deal", task, "task")]),
                ),
            ],
            told=[],
            default="Хорошо.",
            only=name,
        )

    # 1. experience and a rune from the stash, now
    exp, lp, runes = await field("EXP"), await field("LP"), await has(rune)
    await link.debug("type", text=f"{name}, дай мне опыта.")
    ok = await _until(lambda: npc in deals.open, 30)
    window = await link.deal_window() or {}
    check("shown_now", ok and "Опыт +500" in window.get("npc_gives", []), window)
    if not ok:
        return _logged(results)
    await asyncio.sleep(1.2)
    pressed = await link.press_deal()
    done = await _until(lambda: bool(deals.chronicle.facts("deal")), 20)
    check("accepted_now", pressed == "sent" and done, pressed)
    exp2, lp2, runes2 = await field("EXP"), await field("LP"), await has(rune)
    check("experience", exp2 == exp + 500, f"exp {exp} -> {exp2}, lp {lp} -> {lp2}")
    check(
        "rune_from_stash", runes2 == runes + 1 and rune not in deals.stash.left(npc), f"{runes} -> {runes2}"
    )

    # 2. a task: a wolf for strength and potions
    deals_before = len(deals.chronicle.facts("deal"))
    await asyncio.sleep(2)
    await link.debug("type", text=f"{name}, дай мне поручение.")
    ok = await _until(lambda: npc in deals.open, 30)
    window = await link.deal_window() or {}
    check("shown_task", ok and any("волки" in t for t in window.get("hero_gives", [])), window)
    if not ok:
        return _logged(results)
    await asyncio.sleep(1.2)
    pressed = await link.press_deal()
    done = await _until(lambda: len(deals.chronicle.facts("deal")) > deals_before, 20)
    check("contract", pressed == "sent" and done and bool(deals.chronicle.facts("contract")), pressed)

    strength1, lp3, potions = await field("ATTRIBUTE", strength), await field("LP"), await has(potion)
    await link.debug("spawn", instance="WOLF", near=npc, distance=600)
    await asyncio.sleep(1)
    killed = await link.debug("kill", instance="WOLF", near=npc, by="PLAYER")
    done = await _until(lambda: bool(deals.chronicle.facts("contract_done")), 15)
    check("wolf_counted", done, f"kill: {killed}, kills: {deals.chronicle.facts('contract_kill')}")
    check("not_paid_yet", await has(potion) == potions)

    await asyncio.sleep(2)
    await link.debug("type", text=f"{name}, волк мёртв.")
    paid = await _until(lambda: bool(deals.chronicle.facts("contract_paid")), 30)
    strength2, lp4, potions2 = await field("ATTRIBUTE", strength), await field("LP"), await has(potion)
    check(
        "paid",
        paid and strength2 == strength1 + 5 and lp4 < lp3 and potions2 == potions + 2,
        f"str {strength1} -> {strength2}, lp {lp3} -> {lp4}, potions {potions} -> {potions2}",
    )
    conv = dialogue.conversations.get(npc)
    knows = conv is not None and any("исполнил твоё поручение" in e.content for e in conv.memory.entries)
    check("npc_knows", knows)
    return _logged(results)


async def deal_debt(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    deals: Deals,
    lender: str = LOBART,
) -> dict[str, str]:
    """A debt (docs/npc-tools.md, wave 1 step 2), on the game. Lobart (scripted unless LLM=chatgpt) lends the
    hero 20 gold for 25 back by the end of the next day. The clock goes two days on: the debt is a broken
    word, Lobart takes nothing on his word any more, and it goes round as a rumor. Then the hero pays it
    back, late."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    hero = ["inst", "HERO"]

    async def has(item: str) -> int:
        return int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", item]]) or 0)

    async def come() -> str | None:
        await link.debug("player_goto", to=lender, distance=250)
        talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
        return talk.people[lender].name if await _until(lambda: lender in talk.people, 20) else None

    await link.debug("skip_important", near=lender)  # his important dialog would start as the hero comes
    await link.attach(lender)  # gold to lend
    await link.act(lender, "debug_add_item", item=GOLD, count=100)
    await link.detach(lender)
    name = await come()
    if name is None:
        check("near", False, lender)
        return _logged(results)
    script: ClaimScript | None = None
    lend = {
        "npc_gives": [{"item": GOLD, "count": 20}],
        "hero_gives": [],
        "forgive": [],
        "silence": [],
        "hero_later": [{"item": GOLD, "count": 25, "days": 1}],
        "repay": [],
    }
    if isinstance(dialogue.llm, FakeBackend):
        offer = FakeStep(
            "Двадцать дам. Вернёшь двадцать пять — к завтрашнему вечеру.",
            [ToolCall("propose_deal", lend, "lend")],
        )
        script = dialogue.llm = ClaimScript(
            said=[("одолжи", offer)], told=[], default="Смотри не подведи.", only=name
        )

    # he borrows: 20 now, 25 back by the end of the next day
    gold = await has(GOLD)
    await link.debug("type", text=f"{name}, одолжи мне двадцать золотых, я верну.")
    ok = await _until(lambda: lender in deals.open and bool(deals.open[lender].later), 60)
    window = await link.deal_window() or {}
    check("offered", ok, window.get("hero_gives"))
    if not ok:
        return _logged(results)
    await asyncio.sleep(1.2)
    pressed = await link.press_deal()
    ok = await _until(lambda: any(d.npc == lender for d in deals.debts.values()), 20)
    debt = next((d for d in deals.debts.values() if d.npc == lender), None)
    got = await has(GOLD)
    check(
        "lent",
        pressed == "sent" and ok and got == gold + 20,
        f"{gold} -> {got} gold, {debt.what() if debt else '-'}",
    )
    if debt is None:
        return _logged(results)

    # two days on: past its day, a broken word
    await link.debug("time", hour=7, minute=0)
    await link.debug("time", hour=6, minute=59)
    ok = await _until(lambda: debt.broken, 40)
    trust = Trust(deals.chronicle)
    check("broken", ok and trust.broken(lender), (trust.situation(lender) or "").splitlines()[-1:])

    # the morning goes by: it goes round
    await link.debug("time", hour=12, minute=0)
    ok = await _until(lambda: any(f.get("kind") == "broken" for f in deals.chronicle.facts("rumor")), 60)
    heard = [
        f.get("to_name") or f.get("to") for f in deals.chronicle.facts("rumor") if f.get("kind") == "broken"
    ]
    check("rumor", ok, heard[:3])

    # late, he pays it back; his word stays broken
    if await has(GOLD) < 25:
        await link.story_effect(lender, [{"do": "give", "item": GOLD, "count": 30}])
    name = await come() or name
    if script is not None:
        back = dict(lend, npc_gives=[], hero_later=[], repay=[debt.id])
        script.said.append(
            ("долг", FakeStep("Наконец-то. Давай сюда.", [ToolCall("propose_deal", back, "repay")]))
        )
        script.only = name
    before = await has(GOLD)
    await link.debug("type", text=f"{name}, вот твой долг, прости, что поздно.")
    ok = await _until(lambda: lender in deals.open and bool(deals.open[lender].repay), 60)
    if ok:
        await asyncio.sleep(1.2)
        await link.press_deal()
        ok = await _until(lambda: debt.paid, 20)
    after = await has(GOLD)
    check("repaid", ok and after == before - 25, f"{before} -> {after} gold")
    check("word_stays", Trust(deals.chronicle).broken(lender))
    return _logged(results)


INITIATIVE_DEBT = "e2einit"
# Lobart's farmhands: their important dialogs would stop the hero (Hilda's is the initiative's first check)
FARMHANDS = ("BAU_954_MALETH", "BAU_952_VINO")
CROWD_CM = 600.0  # nobody else this close to the hero: his line without a name could go to them


async def initiative_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    deals: Deals,
    concerns: Concerns,
    guard: StoryGuard,
    bodies: Bodies,
    perception: PerceptionDesk,
    desk: InitiativeDesk | None = None,
    lender: str = LOBART,
) -> dict[str, str]:
    """An npc comes up to the hero and speaks first (docs/npc-tools.md, wave 2 step 1), on the game. Hilda has
    her greeting waiting for the hero: the game's own Npc_CheckInfo says so and the initiative leaves her to
    the scripts. The hero owes Lobart 25 gold since yesterday (a debt written into the chronicle): a broken
    word, on Lobart's mind. The hero stands 12 m in front of him; Lobart comes up by himself, once, and speaks
    first about the debt, the hero's next words without a name go to him. The hero says he will pay: Lobart
    offers a deal that settles the debt, the hero accepts it, and nothing is on Lobart's mind any more. With
    the server's planner and its desk (DECIDER_URL) the behavioral model decides whether he comes up; without
    it a fixed stand-in does, for Lobart only. The words are scripted unless LLM=chatgpt."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    chronicle = deals.chronicle
    hero = ["inst", "HERO"]
    name = "Лобарт"

    async def has(item: str) -> int:
        return int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", item]]) or 0)

    async def checkinfo(npc: str) -> Any:
        """The game's own Npc_CheckInfo(npc, 1); None if it cannot tell."""
        try:
            return (await link.story_eval(npc, [CHECKINFO]))[0]
        except (GameError, TimeoutError, IndexError) as err:
            log.info("Npc_CheckInfo of %s: %s", npc, err)
            return None

    # without the server's planner: a fixed stand-in, Lobart comes up and the others keep to their routines
    own = desk is None
    runner: asyncio.Task[None] | None = None
    on_change: Callable[[str], None] | None = None
    talk_planner = talk.planner
    if desk is None:
        from .brain import GameApplier

        planner = NpcPlanner(
            FixedDecider({"react": "approach_talk"}, npcs={lender}),
            GameApplier(link),
            agenda_of=concerns.agenda,
            view_of=perception.english,
        )
        link.listeners.append(planner.on_message)
        desk = InitiativeDesk(
            link,
            dialogue,
            talk,
            planner,
            concerns,
            guard,
            Budget(),
            chronicle,
            bodies=bodies,
            perception=perception,
            journal=dialogue.journal,
        )
        talk.planner = planner  # its conversation with the hero leaves the npc to it

        def agenda(npc: str) -> None:
            """What it wants of the hero follows its concerns (cli.py does so for the server's planner)."""
            planner.set_agenda(npc, concerns.agenda(npc))

        on_change = agenda
        concerns.on_change.append(on_change)
    planner = desk.planner
    # Lobart's lines as the server sent them, with the trigger of the turn they came from
    said: list[tuple[float, str, str]] = []
    walked: list[float] = []  # when Lobart was sent to the hero (by the desk: no one else sends go_to_npc)
    send = link.send

    async def sending(cmd: Any) -> Any:
        if isinstance(cmd, NpcActCmd) and cmd.npc.upper() == lender and cmd.action == "go_to_npc":
            walked.append(time.monotonic())
        if isinstance(cmd, SayCmd) and cmd.npc.upper() == lender:
            conv = dialogue.conversations.get(lender)
            trigger = conv.trigger.content if conv is not None and conv.trigger is not None else ""
            said.append((time.monotonic(), cmd.text, trigger))
        return await send(cmd)

    link.send = sending  # type: ignore[method-assign]
    typed: list[float] = []  # when the hero said something

    def heard(m: GameMessage) -> None:
        if isinstance(m, PlayerText):
            typed.append(time.monotonic())

    link.listeners.append(heard)
    try:
        # their important dialogs would stop the hero as he comes; not Hilda's yet (the first check)
        for npc in (lender, *FARMHANDS):
            try:
                await link.debug("skip_important", near=npc)
            except GameError as err:
                log.info("skip_important %s: %s", npc, err)
        # gold: Lobart has some to give, the hero 30 of it to pay back with
        await attach_when_free(link, lender)
        await link.act(lender, "debug_add_item", item=GOLD, count=100)
        await link.detach(lender)
        await link.story_effect(lender, [{"do": "give", "item": GOLD, "count": 30}])
        # the hero owes him 25 gold by yesterday
        day = int((await link.story_state()).get("day", 0))
        chronicle.record(
            {
                "fact": "debt",
                "id": INITIATIVE_DEBT,
                "npc": lender,
                "npc_name": name,
                "item": GOLD,
                "count": 25,
                "name": "золото",
                "due": day - 1,
                "day": day - 1,
            }
        )
        deals.reload()
        await link.debug("time", hour=10, minute=0)
        if isinstance(dialogue.llm, FakeBackend):
            repay = {
                "npc_gives": [],
                "hero_gives": [],
                "forgive": [],
                "silence": [],
                "hero_later": [],
                "repay": [INITIATIVE_DEBT],
            }
            first = FakeStep("[angry] Эй, ты! Двадцать пять золотых за тобой, и срок вчера вышел.")
            dialogue.llm = ClaimScript(
                said=[
                    ("подошёл к герою", first),
                    ("Отдам", FakeStep("Давно бы так.", [ToolCall("propose_deal", repay, "repay")])),
                ],
                told=[],
                default="Вот и ладно.",
                only=name,
            )

        # 1. Hilda has her greeting for the hero (DIA_Hilda_Hallo): the game's own check says so, and the
        # initiative leaves her to the scripts; once it counts as told, nothing waits
        before, waiting = await checkinfo(HILDA), await desk.important(HILDA)
        await link.debug("skip_important", near=HILDA)
        after, left = await checkinfo(HILDA), await desk.important(HILDA)
        check(
            "important_guard",
            before is not None and bool(before) and waiting and after is not None and not after and not left,
            f"Npc_CheckInfo {before} -> {after}, the desk {waiting} -> {left}",
        )

        # 2. the debt is past its day (the deals look every 10 s): a broken word, on Lobart's mind
        ok = await _until(
            lambda: any(f.get("debt") == INITIATIVE_DEBT for f in chronicle.facts("broken")), 20
        )
        top = concerns.top(lender)
        key = f"debt_broken:{INITIATIVE_DEBT}"
        check("concern", ok and top is not None and top.key == key, f"{top.key}: {top.en}" if top else "none")

        # 3. the hero 12 m in front of him, facing him, nobody else close: Lobart comes up by himself
        walks = len(walked)
        if own:
            runner = asyncio.create_task(planner.run())
        t0 = time.monotonic()
        crowd: list[str] = []
        for distance in (1200, 1500, 900):
            await link.debug("teleport", near=lender, distance=distance)
            await asyncio.sleep(1.5)  # the roster comes twice a second
            crowd = [
                f"{e.name} {e.distance / 100:.0f} m"
                for k, e in talk.people.items()
                if k != lender and e.activity != "down" and e.distance <= CROWD_CM
            ]
            if not crowd:
                break
        came = await _until(
            lambda: (
                len(walked) > walks and lender in talk.people and talk.people[lender].distance <= ARRIVE_CM
            ),
            45 if own else 120,  # the live model decides when
        )
        entry, rec = talk.people.get(lender), planner.npcs.get(lender)
        where = f"{entry.distance / 100:.1f} m" if entry else "not around"
        body = guard.body(lender, entry.state if entry else "") or "may walk"
        check(
            "walked_up",
            came and len(walked) - walks == 1,
            f"{where} after {time.monotonic() - t0:.0f} s, {len(walked) - walks} walk(s), "
            f"vetoed {planner.stats.vetoed}, agenda {rec.agenda if rec else '-'!r}, body: {body}"
            f"{f', others close: {crowd}' if crowd else ''}",
        )
        if not came:
            return _logged(results)

        # 4. he speaks first, about the debt; the hero has said nothing
        def first_words() -> tuple[float, str, str] | None:
            return next((w for w in said if w[2].startswith("[Ты сам подошёл к герою")), None)

        await _until(lambda: first_words() is not None, 15)
        words = first_words()
        early = [t for t in typed if words is not None and t < words[0]]
        # the hero said nothing yet: only the desk can have made Lobart his partner, listening
        partner = talk.partner
        opened = partner is not None and partner.npc == lender and partner.posture == "listen"
        check(
            "spoke_first",
            words is not None and "25" in words[2] and not early and opened,
            f"«{words[1]}» on {words[2]!r}{f', the hero spoke {len(early)} time(s) before' if early else ''}"
            if words
            else f"silent; lines: {[w[1] for w in said][-3:]}",
        )

        # 5. the fact: who came up, about what, who saw it
        fact = next((f for f in reversed(chronicle.facts("initiative")) if f.get("npc") == lender), None)
        check(
            "fact",
            fact is not None and fact.get("occasion") == key and "PLAYER" in (fact.get("seen_by") or []),
            fact or "none",
        )

        # 6. the hero answers him without a name: it goes to Lobart, his partner now
        await _until(lambda: not dialogue.speaking(lender), 15)
        text = "Отдам, держи свои двадцать пять."
        await link.debug("type", text=text)
        await _until(lambda: talk.last is not None and talk.last[1] == text, 10)
        to = talk.last[0] if talk.last is not None and talk.last[1] == text else None
        check(
            "reply_goes_to_him",
            to is not None and to.id == lender,
            f"to {to.id} by {to.why}" if to else "not heard or to nobody",
        )

        # 7. he offers a deal that settles the debt: the window shows it
        def offered() -> bool:
            deal = deals.open.get(lender)
            return deal is not None and [d.id for d in deal.repay] == [INITIATIVE_DEBT]

        ok = await _until(offered, 30)
        owed = f"Долг: {gold_text(25)}"
        shown = await _until_async(
            link.deal_window, lambda w: w is not None and owed in (w.get("hero_gives") or []), 10
        )
        window = await link.deal_window() or {}
        check("offered_repay", ok and shown, window.get("hero_gives"))
        if not ok:
            return _logged(results)

        # 8. the hero accepts: the debt is paid, nothing is on Lobart's mind, he does not come up again
        gold = await has(GOLD)
        await asyncio.sleep(1.2)  # Y waits until the terms have stood a moment
        pressed = await link.press_deal()
        debt = deals.debts.get(INITIATIVE_DEBT)
        paid = await _until(lambda: debt is not None and debt.paid, 20)
        left_gold = await has(GOLD)
        walks = len(walked)
        await asyncio.sleep(20)
        top, rec = concerns.top(lender), planner.npcs.get(lender)
        check(
            "settled",
            pressed == "sent"
            and paid
            and top is None
            and rec is not None
            and rec.agenda is None
            and len(walked) == walks,
            f"{pressed}, gold {gold} -> {left_gold}, concern {top.key if top else None}, "
            f"agenda {rec.agenda if rec else '-'!r}, {len(walked) - walks} walk(s) after",
        )
    finally:
        link.send = send  # type: ignore[method-assign]
        link.listeners.remove(heard)
        if own:
            if runner is not None:
                runner.cancel()
            link.listeners.remove(planner.on_message)
            if on_change is not None:
                concerns.on_change.remove(on_change)
            talk.planner = talk_planner
    return _logged(results)


GOSSIP_SLOT = 8  # the save before the scene: played whole, then again from there and cut
GOSSIP_SPOT = "NW_FARM1_PATH_01"  # Hilda stands here: 5.3 m from Lobart's spot (the routines' dump)
# the hero: 13 m from Lobart's freepoint and 10.6 m from the spot, he hears both (`player_goto to=` would
# take NW_FARM1_OUT_03, 14.6 m from her)
GOSSIP_HERO = "NW_FARM1_ENTRANCE_03"
GOSSIP_OUT = "NW_FARM1_OUT_01"  # 7 m from Lobart's spot, 8.5 m from GOSSIP_SPOT
VINO = "BAU_952_VINO"
# the scene as the scripted model writes it: (0 the teller, 1 the listener), text, mood
GOSSIP_LINES = (
    (0, "Слыхала? Этот бродяга мне про репу наврал, глазом не моргнул.", "angry"),
    (1, "Да ну? А с виду смирный.", "neutral"),
    (0, "Смирный… Глаз с него не спускай.", "neutral"),
)
FACE_S = 1.5  # a speaker faces the one it speaks to this soon after its line starts
LOOK_TAIL_S = 1.25  # a line's look outlasts its voice by 1 s (npc.cpp, aibridge.cpp), plus a sample's margin


class GossipScript:
    """The conversational model of the gossip e2e (and the day e2e's), routed by what is asked: a scene (the
    tool `lines`) gets `lines` (GOSSIP_LINES) with the two names the tool allows, Hilda's answer to the hero
    is hers, anything else goes to Lobart's script (which sends the others to the live model itself). With
    `live` (LLM=chatgpt) the scene and Hilda are the live model."""

    name = "gossip-script"

    def __init__(
        self,
        claim: LLMBackend,
        hilda: LLMBackend,
        live: LLMBackend | None = None,
        *,
        lines: Sequence[tuple[int, str, str]] = GOSSIP_LINES,
        quiet: bool = True,
        reason: str = "сплетничают о герое, пока он рядом",
    ) -> None:
        self.claim, self.hilda, self.live = claim, hilda, live
        self.lines = lines
        self.quiet, self.reason = quiet, reason
        self.lines_calls = 0  # how often a scene was written

    async def aclose(self) -> None:
        pass

    async def stream(
        self,
        *,
        instructions: str,
        messages: Sequence[Item],
        tools: list[Tool] | None = None,
        tool_choice: ToolChoice | None = None,
        allowed: Collection[str] | None = None,
        cache_key: str | None = None,
    ) -> AsyncIterator[LLMEvent]:
        lines = next((t for t in tools or [] if t.name == "lines"), None)
        backend: LLMBackend
        if lines is not None:
            self.lines_calls += 1
            call = ToolCall("lines", _gossip_lines(lines, self.lines, self.quiet, self.reason), "gossip")
            backend = self.live or FakeBackend(script=[FakeStep("", [call])], delay=0)
        elif "по имени Хильда" in instructions:
            backend = self.live or self.hilda
        else:
            backend = self.claim
        async for event in backend.stream(
            instructions=instructions,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            allowed=allowed,
            cache_key=cache_key,
        ):
            yield event


def _gossip_lines(
    tool: Tool,
    lines: Sequence[tuple[int, str, str]] = GOSSIP_LINES,
    quiet: bool = True,
    reason: str = "сплетничают о герое, пока он рядом",
) -> dict[str, Any]:
    """The scripted lines (0 the teller, 1 the listener) as the tool `lines` takes them: its schema lists the
    teller's name first."""
    try:
        names = tool.parameters["properties"]["lines"]["items"]["properties"]["speaker"]["enum"]
        teller, listener = str(names[0]), str(names[1])
    except (KeyError, IndexError, TypeError):
        teller, listener = "Лобарт", "Хильда"
    return {
        "lines": [
            {"speaker": (teller, listener)[who], "text": text, "mood": mood} for who, text, mood in lines
        ],
        "quiet": quiet,
        "reason": reason,
    }


async def gossip_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    rumors: Rumors,
    gossip: GossipDesk,
    bodies: Bodies,
    perception: PerceptionDesk,
    log_dir: str | None = None,
) -> dict[str, str]:
    """Two people near the hero talk about his lie, in their own voices (docs/npc-tools.md, wave 2 step 2), on
    the game. Lobart catches the hero lying; Hilda, in the house, does not hear it. An hour later she is put
    5 m from Lobart and the hero 13 m off (their days never bring them so close: the e2e stages them), and the
    pair is opened (`Rumors.gate`; every other pair is held closed): the rumor passes as one fact, then Lobart
    turns to her and they say three lines in turn, each to the other, a line only once the voice before it has
    played out (game.log, `log_dir`). From a save before it the scene again: the hero's voice cuts both after
    the second line, he asks Hilda what they whispered about, and she has the scene and the rumor in mind.
    Last, far from the hero, a pass is only the fact: no model, no words. The lines are scripted unless
    LLM=chatgpt; voices with a TTS service (scripts/e2e.sh starts one)."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    facts = rumors.chronicle.facts
    open_pairs: set[tuple[str, str]] = set()
    starts: dict[str, tuple[float, bool]] = {}  # line -> when it started, voiced
    sent: list[tuple[float, SayCmd]] = []  # the lines as the server sent them
    samples: list[tuple[float, dict[str, NpcView]]] = []  # what Lobart and Hilda perceived, when

    def to_hilda() -> list[Fact]:
        return [f for f in facts("rumor") if f.get("quest") == RUEBEN and f.get("to") == HILDA]

    def notes(who: str) -> list[str]:
        conv = dialogue.conversations.get(who)
        return [m.content for m in conv.history if m.role == "developer"] if conv else []

    def started(m: GameMessage) -> None:
        if isinstance(m, LineStarted):
            starts[m.line] = (time.monotonic(), m.voiced)

    def seen(view: NpcView) -> None:  # the views the game pushes anyway (once a second)
        if view.npc.upper() in (LOBART, HILDA):
            samples.append((time.monotonic(), {view.npc.upper(): view}))

    send = link.send

    async def sending(cmd: Any) -> Any:
        if isinstance(cmd, SayCmd):
            sent.append((time.monotonic(), cmd))
        return await send(cmd)

    async def stage(tag: str = "") -> bool:
        """Hilda steps out to Lobart (she stays free: start_activity holds her there), the hero 13 m off."""
        try:
            await link.debug("npc_goto", npc=HILDA, waypoint=GOSSIP_SPOT, face=LOBART)
        except GameError as err:
            check(f"staged{tag}", False, f"npc_goto: {err}")
            return False
        out = await bodies.act(
            HILDA,
            "start_activity",
            state="ZS_STAND_ARMSCROSSED",
            waypoint=GOSSIP_SPOT,
            minutes=60,
            arrive=True,
            timeout=60,
        )
        # distance only: how they face is their routines' (Lobart turns to his freepoint)
        near = {str(p.get("npc", "")).upper(): p for p in await link.story_nearby(LOBART, 700)}
        gap = float(near[HILDA].get("distance", 0)) if HILDA in near else None
        staged = out.status == "arrived" and gap is not None and 300 <= gap <= 700
        where = f"{gap / 100:.1f} m from Lobart" if gap is not None else "not within 7 m of Lobart"
        check(f"staged{tag}", staged, f"{out.status}{f': {out.detail}' if out.detail else ''}, {where}")
        await link.debug("player_goto", waypoint=GOSSIP_HERO)
        await asyncio.sleep(1.5)  # the roster comes twice a second: not the one from before he moved
        pair = (LOBART, HILDA)
        near_hero = await _until(
            lambda: all(n in talk.people and talk.people[n].distance <= WORDS_CM for n in pair), 10
        )
        check(
            f"hero_near{tag}",
            near_hero,
            ", ".join(
                f"{n}: {talk.people[n].distance / 100:.1f} m" if n in talk.people else f"{n}: not around"
                for n in pair
            ),
        )
        return staged and near_hero

    link.listeners.append(started)
    perception.on_view.append(seen)
    link.send = sending  # type: ignore[method-assign]
    rumors.gate = lambda r, a, b: (a, b) in open_pairs  # every pair not opened here is held closed
    try:
        # 0. who knows whom; the morning at Lobart's farm
        if not await _until(lambda: rumors.social is not None, 30):
            return _logged({"social": "failed (no people)"})
        social = rumors.social
        assert social is not None
        tie = social.ties.get(LOBART, {}).get(HILDA)
        about = next((d for d in social.describe(LOBART) if f"({HILDA})" in d), "no tie")
        check("ties", tie is not None and any(s <= 10 * 60 < e for s, e in tie.meets), about)
        await link.debug("time", hour=9, minute=0)
        await link.debug("skip_important", near=HILDA)  # her greeting would stop the hero
        await link.debug("player_goto", waypoint=GOSSIP_OUT)  # a new game starts 174 m away at the tower

        def doing(npc: str) -> str:
            return talk.people[npc].state if npc in talk.people else "not around"

        ok = await _until(lambda: (doing(LOBART), doing(HILDA)) == ("ZS_STAND_GUARDING", "ZS_COOK_STOVE"), 40)
        check("routines", ok, f"Lobart {doing(LOBART)}, Hilda {doing(HILDA)}")

        # 1. Lobart catches the hero lying; Hilda, in the house, does not hear it (within RUMOR_CM she would,
        # through the walls: quests.py)
        around = [str(p.get("npc", "")).upper() for p in await link.story_nearby(LOBART, RUMOR_CM)]
        if HILDA in around:
            return _logged({**results, "apart": f"failed (Hilda within {RUMOR_CM // 100} m of Lobart)"})
        live = None if isinstance(dialogue.llm, FakeBackend) else dialogue.llm
        ok, claim = await _caught_by(link, dialogue, talk, LOBART, facts)
        check("caught", ok, facts("caught")[-1:])
        if not ok or claim is None:
            return _logged(results)
        # the scene's lines, Hilda's answer, else Lobart's script (it answers every call with LLM=fake)
        hilda = ClaimScript(
            said=[("шептались", FakeStep("Не твоё дело. Хотя… Лобарт говорит, ты ему наврал."))],
            told=[],
            only="Хильда",
        )
        script = GossipScript(claim, hilda, live)
        dialogue.llm = script

        def rumor() -> Rumor | None:
            return next((r for r in rumors.rumors.values() if r.quest == RUEBEN), None)

        r = rumor()
        check(
            "not_heard",
            not to_hilda() and r is not None and HILDA not in r.known,
            f"knows: {sorted(r.known)}" if r is not None else "no rumor",
        )

        # 2. an hour later: a jump of the clock stages nothing, and every pair is held
        talk.end()
        await link.debug("time", hour=10, minute=0)
        await asyncio.sleep(TICK_S + 1)
        held = f"{len(to_hilda())} fact(s), {gossip.scenes} scene(s)"
        check("held_closed", not to_hilda() and gossip.scenes == 0, held)

        # 3. Hilda next to Lobart, the hero 13 m off; a save to play it twice
        if not await stage():
            return _logged(results)
        saved = await _save(link, GOSSIP_SLOT)
        check("saved", saved is not None, f"{saved.branch}@{saved.seq}" if saved is not None else "refused")
        if saved is None:
            return _logged(results)

        # 4. the pair opened: the fact first, then the scene, in turn, each to the other
        sampler = asyncio.create_task(_sample_views(link, (LOBART, HILDA), samples))
        first = gossip.scenes
        open_pairs.add((LOBART, HILDA))
        try:
            # a tick may wait for the gap probe, and Lobart for his lines of the catch to be said or lost
            # (the hero went off before they started: dialogue.LINE_LOST_S)
            ok = await _until(lambda: bool(to_hilda()), 40)
            got = to_hilda()
            fact = got[0] if got else {}
            check(
                "one_fact",
                ok
                and len(got) == 1
                and fact.get("from") == LOBART
                and fact.get("how") == "talked"
                and int(fact.get("hops", 0)) == 1,
                _passes_text(got),
            )
            done = await _until(
                lambda: gossip.scenes > first and gossip.scene is None and gossip.last is not None, 45
            )
        finally:
            open_pairs.clear()
            sampler.cancel()
        scene = gossip.last if gossip.scenes > first else None
        if scene is None:
            return _logged({**results, "scene_done": "failed (no scene)"})
        check("scene_done", done and scene.state == "done", f"{scene.state} {scene.why}".strip())
        lines = _scene_lines(scene)
        said = sorted((x for x in lines if x.line in starts), key=lambda x: starts[x.line][0])

        # the speaker faces the one it speaks to (the addressee's view: faces_you); the first line is
        # inconclusive when Lobart faced her already, the later ones always tell
        faced = [_faced(samples, x, starts[x.line][0], first=i == 0) for i, x in enumerate(said)]
        check(
            "facing",
            bool(said) and faced[0] != "no" and all(f == "yes" for f in faced[1:]),
            "; ".join(f"{i + 1}: {f}" for i, f in enumerate(faced)),
        )
        # while it speaks, the one it speaks to talks to it (the speaker's view: talking_to)
        looked = [_talked_to(samples, said, starts, i) for i in range(len(said))]
        check(
            "look",
            bool(said) and all(looked),
            "; ".join(f"{i + 1}: {'yes' if ok else 'no'}" for i, ok in enumerate(looked)),
        )
        speakers = [x.npc for x in said]
        want = len(lines) if live is not None else len(GOSSIP_LINES)
        check(
            "three_lines",
            len(said) == len(lines) == want
            and 2 <= want <= 4
            and all(n == (LOBART, HILDA)[i % 2] for i, n in enumerate(speakers)),
            f"{len(said)} of {len(lines)} started: {' -> '.join(social.names.get(n, n) for n in speakers)}",
        )
        await asyncio.sleep(1.5)  # the last voice plays out in the game
        check("no_overlap", *_voices_in_turn(await _game_log(log_dir), [x.line for x in said]))
        voiced = [x for x in said if starts[x.line][1]]
        check("voiced", bool(said) and len(voiced) == len(said), f"{len(voiced)} of {len(said)}")
        rows: list[dict[str, Any]] = []

        async def scene_rows() -> list[dict[str, Any]]:
            rows[:] = await asyncio.to_thread(_scene_rows, log_dir)
            return rows

        ok = await _until_async(scene_rows, lambda got: any(x.get("id") == scene.id for x in got), 3)
        row = next((x for x in rows if x.get("id") == scene.id), None)
        check(
            "trace",
            ok and row is not None,
            f"{row.get('status')}, {len(row.get('lines') or [])} lines, quiet {row.get('quiet')}"
            if row is not None
            else f"{len(rows)} scene row(s), none of {scene.id}",
        )

        # 5. the scene again from the save, the pair opened only once both stand again: the hero's voice cuts
        # both after the second line
        loaded = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load", timeout=60))
        await link.debug("load", GOSSIP_SLOT)
        await loaded
        await link.wait_world()
        if not await _until(lambda: rumors.social is not None and rumors.clock is not None, 30):
            return _logged({**results, "reloaded": "failed (no rumors after the load)"})
        if not await stage("_again"):
            return _logged(results)
        again = gossip.scenes
        open_pairs.add((LOBART, HILDA))
        try:
            await _until(lambda: gossip.scenes > again and gossip.scene is not None, 20)
            cut = gossip.scene if gossip.scenes > again else None
            if cut is None:
                return _logged({**results, "cut": "failed (no scene after the load)"})

            def second() -> bool:
                ls = _scene_lines(cut)
                return cut.state not in ("writing", "playing") or (len(ls) > 1 and ls[1].line in starts)

            await _until(second, 45)
            ls = _scene_lines(cut)
            if len(ls) < 2 or ls[1].line not in starts:
                return _logged({**results, "cut": f"failed (no second line: {cut.state} {cut.why})"})
            await asyncio.sleep(0.5)
            await link.debug("ptt", state="start")
            cut_at = time.monotonic()
            await link.debug("ptt", state="cancel")
        finally:
            open_pairs.clear()
        await _until(lambda: cut.state == "cut", 3)
        check("cut", cut.state == "cut" and cut.why == "hero", f"{cut.state} {cut.why}")
        talked = [f for f in to_hilda() if f.get("how") == "talked"]
        check("one_fact_again", len(talked) == 1, _passes_text(talked))

        # 6. within the grace he asks Hilda: she listens to him now, the scene and the rumor in mind
        conv = dialogue.conversations.get(HILDA)
        before = conv.reply if conv is not None else None
        asked_at, calls = time.monotonic(), len(hilda.calls)
        await link.debug("type", text="Хильда, о чём вы шептались?")

        def answered() -> bool:
            c = dialogue.conversations.get(HILDA)
            return c is not None and c.reply is not None and c.reply is not before and c.reply.done()

        ok = await _until(answered, 30)
        partner = talk.partner
        hers = [c for t, c in sent if t >= asked_at and c.npc.upper() == HILDA and not c.filler]
        moving = [c for c in hers if c.overlay]
        check(
            "asked",
            ok and partner is not None and partner.npc == HILDA and bool(hers) and not moving,
            f"partner {partner.npc if partner else None} ({partner.posture if partner else '-'}), "
            f"{len(hers)} line(s){f', {len(moving)} on the move' if moving else ''}: "
            f"{' '.join(c.text for c in hers)[:120]}",
        )
        if live is None:
            request = " ".join(
                " ".join([text, *(m.content if isinstance(m, Message) else str(m) for m in items)])
                for text, items, _ in hilda.calls[calls:]
            )
            keys = ("ты слышал, что он обманул других", "Лобарт", "рассказал тебе о герое")
            missing = [k for k in keys if k not in request]
            check("in_situation", bool(request) and not missing, f"missing {missing}" if missing else "")
        else:
            line = Trust(rumors.chronicle).situation(HILDA) or ""
            noted = any("рассказал тебе о герое" in n for n in notes(HILDA))
            check(
                "in_situation",
                "ты слышал, что он обманул других" in line and "Лобарт" in line and noted,
                f"{line.splitlines()[-1:]}, scene noted {noted}",
            )
        check("lobart_noted", any("заговорил герой" in n for n in notes(LOBART)), notes(LOBART)[-1:])
        # the third line was never sent, so never heard
        await asyncio.sleep(max(0.0, cut_at + 6 - time.monotonic()))
        log_text = await _game_log(log_dir)
        third = _scene_lines(cut)[2:3]
        heard = [x.line for x in third if x.line in starts]
        voiced3 = [x.line for x in third if log_text and f"[ai] voice {x.line} starts" in log_text]
        sent_n = cut.play.sent if cut.play is not None else None
        check(
            "third_silent",
            sent_n == 2 and not heard and not voiced3,
            f"sent {sent_n} of {len(_scene_lines(cut))}{', the third started' if heard else ''}"
            f"{', its voice in game.log' if voiced3 else ''}",
        )

        # 7. far from the hero a pass is only the fact: no model, no words
        talk.end()
        social = rumors.social or social  # built again after the load
        r = rumor()
        acquainted = social.ties.get(HILDA, {})
        known = r.known if r is not None else {}
        other = (
            VINO
            if VINO in acquainted and VINO not in known
            else next((n for n in sorted(acquainted) if n not in known and n not in rumors.dead), None)
        )
        if r is None or other is None:
            return _logged({**results, "far_fact": f"failed (nobody to tell: {sorted(acquainted)})"})
        await link.debug("player_goto", to=HILDA, distance=4000)
        far = await _until(lambda: HILDA not in talk.people or talk.people[HILDA].distance >= 3000, 10)
        where = f"{talk.people[HILDA].distance / 100:.0f} m" if HILDA in talk.people else "not around"
        check("hero_far", far, f"Hilda {where}")
        lines_calls, n, far_at = script.lines_calls, gossip.scenes, time.monotonic()
        told = False
        for _ in range(3):
            # opened only within this synchronous call: no tick sees the pair open
            open_pairs.add((HILDA, other))
            told = rumors.offer(r, HILDA, other)
            open_pairs.discard((HILDA, other))
            if told:
                break
            await asyncio.sleep(1.5)  # it waits (a gap not known yet): asked again
        got = [f for f in facts("rumor") if f.get("quest") == RUEBEN and f.get("to") == other]
        check(
            "far_fact",
            told and len(got) == 1 and got[0].get("how") == "met",
            f"{social.names.get(other, other)}: {_passes_text(got)}",
        )
        await asyncio.sleep(5)
        says = [c.text for t, c in sent if t >= far_at and c.npc.upper() in (HILDA, other)]
        check(
            "far_no_model",
            script.lines_calls == lines_calls and gossip.scenes == n and not says,
            f"{script.lines_calls - lines_calls} model call(s), {gossip.scenes - n} scene(s), lines {says}",
        )
    finally:
        rumors.gate = None
        link.send = send  # type: ignore[method-assign]
        link.listeners.remove(started)
        perception.on_view.remove(seen)
    return _logged(results)


MALETH = "BAU_954_MALETH"
# Malet guards the sheep here 08:00-22:00, 56.4 m from Vino's field: they meet only in the stable's beds
MALETH_SPOT = "NW_FARM1_PATH_CITY_SHEEP_09"
# the hero by Malet: 12.2 m from his spot, 45.5 m from Vino's FIELD_04, 24.5 m from Lobart (routines' dump)
DAY_SCENE_HERO = "NW_FARM1_PATH_CITY_SHEEP_13"
DAY_FIELD_HERO = "NW_FARM1_FIELD_01_03"  # 18.5 m from FIELD_04, 72.4 m from Malet's spot
DAY_SLOT = 9  # the save after Vino heard the news: his walk near the hero, then again from there far from him
# the scene at Malet's spot as the scripted model writes it: (0 Vino, the teller, 1 Malet), text, mood
DAY_LINES = (
    (0, "Слыхал? Чужак Лобарту про репу наврал, а тот его и поймал.", "neutral"),
    (1, "Вот как? Пригляжу за ним у стада.", "neutral"),
    (0, "Пригляди. Мне-то что, лишь бы выпивку ставил.", "neutral"),
)
DEAD_OPTIONS = {"pick_up", "give_back", "rest", "socialize"}  # removed in wave 2 step 6


class DayRecorder:
    """The behavioral model of the day e2e: every request's questions are kept; the pinned npc's pinned
    questions get the pinned choice (if offered), every other question the stand-in's default (the first
    option, level 0), so no other body acts: Lobart, with the lie on his mind, would come up to the hero and
    hold Vino's scene. With the live model (DECIDER_URL) it is asked first and its own answers are kept."""

    def __init__(self, live: Decider | None, pin: dict[str, dict[str, str]]) -> None:
        self.live = live
        self.pin = {npc.upper(): questions for npc, questions in pin.items()}
        self.requests: list[tuple[str, dict[str, dict]]] = []  # (npc, its questions), in order
        self.own: list[tuple[str, dict[str, dict]]] = []  # (npc, the live model's own answers)
        self._rest = FixedDecider({}, npcs=())

    @property
    def model(self) -> str | None:
        return getattr(self.live, "model", None)

    async def decide(self, rid, lane, priority, deadline_ms, state, questions) -> Outcome:  # noqa: ANN001
        npc = rid.split("#", 1)[0].upper()
        self.requests.append((npc, questions))
        own = None
        if self.live is not None:
            own = await self.live.decide(rid, lane, priority, deadline_ms, state, questions)
            self.own.append((npc, own.answers or {}))
        out = await self._rest.decide(rid, lane, priority, deadline_ms, state, questions)
        answers = dict(out.answers or {})
        for name, choice in self.pin.get(npc, {}).items():
            if choice in questions.get(name, {}).get("criteria", {}):
                answers[name] = {
                    "type": "choice",
                    "choice": choice,
                    "probabilities": {choice: 0.9},
                    "confidence": 0.9,
                }
        if own is None:
            return Outcome("answers", answers, out.queue_ms, out.infer_ms)
        return Outcome("answers", answers, own.queue_ms, own.infer_ms)

    async def cancel(self, rid: str) -> None:
        if self.live is not None:
            await self.live.cancel(rid)


class _TraceTap:
    """The planner's trace rows as they are written, kept for the checks (and still written)."""

    def __init__(self, inner: TraceWriter | None) -> None:
        self.inner = inner
        self.rows: list[dict[str, Any]] = []

    def write(self, row: dict[str, Any]) -> None:
        self.rows.append(row)
        if self.inner is not None:
            self.inner.write(row)


async def day_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    rumors: Rumors,
    gossip: GossipDesk,
    bodies: Bodies,
    perception: PerceptionDesk,
    day: DayDesk,
    planner: NpcPlanner | None = None,
    log_dir: str | None = None,
) -> dict[str, str]:
    """Everyday life from concerns (docs/npc-tools.md, wave 2 step 6), on the game. Lobart catches the hero
    lying; Vino, on his field, hears it from him far from the hero. With the hero near Malet, Vino's menu of
    his next hour has a walk to Malet to tell the news (about an hour); it is chosen (pinned: the live model's
    own answer is kept with DECIDER_URL) and Vino walks there: near the hero the two say it in a scene of
    step 2 (the fact how: talked) and the walk leaves a `day` fact the hero saw. From a save before the walk,
    with the hero by Vino's field, the same walk ends 72 m from the hero: Malet learns it as a fact (how:
    visited), without the model or words. Across the run: no sleep or dead option in any menu, and each
    routine asked a few times only. Without the server's planner (DECIDER_URL) the scenario runs its own.
    The lines are scripted unless LLM=chatgpt; voices with a TTS service (scripts/e2e.sh starts one)."""
    from .brain import GameApplier
    from .brain.planner import PlannerConfig

    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    facts = rumors.chronicle.facts
    open_pairs: set[tuple[str, str]] = set()
    starts: dict[str, tuple[float, bool]] = {}  # line -> when it started, voiced
    sent: list[tuple[float, Any]] = []  # the says and npc_act's as the server sent them
    asks: Counter[str] = Counter()  # the routines asked of the game, per npc
    idles: list[NpcIdle] = []
    loaded_with: list[int] = []  # how many routines the planner knew at each Loading
    recorder = DayRecorder(None, pin={VINO: {"do": "chat"}})

    # the server's planner with DECIDER_URL, its day settings for the run; else one of the scenario's own
    own = planner is None
    if planner is None:
        planner = NpcPlanner(
            recorder,
            GameApplier(link),
            config=PlannerConfig(
                apply_day=True, plan_period_s=5.0, far_period_s=(5.0, 8.0), ask_attitude=False
            ),
            routine_of=link.routine,
            habits_of=habits_of,
            view_of=perception.english,
            trace=TraceWriter(Path(log_dir, "traces")) if log_dir else None,
        )
        day.follow(planner, gossip)  # the server's planner follows it already
        link.listeners.append(planner.on_message)
    brain = planner
    decider, cfg, trace = brain.decider, brain.cfg, brain.trace
    if not own:
        recorder.live = decider
        brain.decider = recorder
        brain.cfg = replace(
            cfg, apply_day=True, plan_period_s=5.0, far_period_s=(5.0, 8.0), ask_attitude=False
        )
    tap = _TraceTap(trace)
    brain.trace = tap  # type: ignore[assignment]
    runner = asyncio.create_task(brain.run()) if own else None

    def rumor() -> Rumor | None:
        return next((r for r in rumors.rumors.values() if r.quest == RUEBEN), None)

    def told(to: str) -> list[Fact]:
        return [f for f in facts("rumor") if f.get("quest") == RUEBEN and f.get("to") == to]

    def walked() -> list[Fact]:
        return [f for f in facts("day") if f.get("npc") == VINO and f.get("to") == MALETH]

    def metres(npc: str) -> float:
        return talk.people[npc].distance / 100 if npc in talk.people else float("inf")

    def heard(m: GameMessage) -> None:
        match m:
            case LineStarted():
                starts[m.line] = (time.monotonic(), m.voiced)
            case NpcIdle():
                idles.append(m)
            case Loading():
                loaded_with.append(len(brain.routines))  # the planner's listener came first

    send = link.send

    async def sending(cmd: Any) -> Any:
        if isinstance(cmd, RoutineCmd):
            asks[cmd.npc.upper()] += 1
        elif isinstance(cmd, (SayCmd, NpcActCmd)):
            sent.append((time.monotonic(), cmd))
        return await send(cmd)

    async def walk(tag: str = "") -> tuple[float, NpcActCmd | None]:
        """Vino is asked of his next hour: the menu has the walk to Malet, it is chosen and he walks there.
        When it was chosen and his act."""
        # from here on the lot lets Vino tell Malet: opened only now, so no menu before the counts below has
        # the walk (a check of his as the hero's goto brings him back would send him off uncounted)
        open_pairs.add((VINO, MALETH))
        rec = brain.npcs.get(VINO)
        if rec is not None:
            rec.next_check = brain.clock()  # looked at now, not at his next background turn
        since, own_since, rows_since = len(recorder.requests), len(recorder.own), len(tap.rows)

        def asked(chat: bool = True) -> dict[str, Any] | None:
            """His do question since then: the first with the walk, or (not `chat`) the latest."""
            mine = [q["do"] for n, q in recorder.requests[since:] if n == VINO and "do" in q]
            return next((q for q in mine if "chat" in q["criteria"]), None) if chat else (mine or [None])[-1]

        await _until(lambda: asked() is not None, 60)
        question = asked() or asked(chat=False)
        options = dict(question["criteria"]) if question is not None else {}
        place = day.book.of(MALETH_SPOT) if day.book is not None else None
        where = place.where_en if place is not None else "?"
        text = options.get("chat", "")
        menu = day.menus.get(VINO)
        opt = menu.do.get("chat") if menu is not None else None
        args = {"state": "ZS_STAND_WP", "waypoint": MALETH_SPOT, "minutes": 61}
        got = {**opt.args, "waypoint": str(opt.args.get("waypoint")).upper()} if opt and opt.args else {}
        check(
            f"menu{tag}",
            set(options) == {"keep_routine", "chat"}
            and "Малет" in text
            and where in text
            and "(about an hour)" in text
            and opt is not None
            and opt.trip
            and opt.partner == MALETH
            and got == args,
            f"{list(options)}; chat: {text!r}; {got or 'no option'}",
        )
        if not own:  # what the live model itself chose, informational: the walk is pinned either way
            mine = next((a["do"] for n, a in recorder.own[own_since:] if n == VINO and "do" in a), None) or {}
            p = float((mine.get("probabilities") or {}).get("chat", 0.0))
            check(
                f"model_answer{tag}", True, f"{mine.get('choice')}, P(chat) {p:.2f}" if mine else "no answer"
            )

        def chosen() -> dict[str, Any] | None:
            return next(
                (
                    r
                    for r in tap.rows[rows_since:]
                    if r.get("npc") == VINO
                    and r.get("question") == "do"
                    and r.get("choice") == "chat"
                    and r.get("why") == "day desk"
                ),
                None,
            )

        ok = await _until(lambda: chosen() is not None, 30)
        chose_at = time.monotonic()
        row = chosen()
        theirs = [
            (r.get("question"), r.get("choice"), r.get("why"))
            for r in tap.rows[rows_since:]
            if r.get("npc") == VINO
        ]
        check(f"chosen{tag}", ok, f"applied {row.get('applied')}" if row else f"his rows: {theirs[-3:]}")
        await _until(lambda: any(t >= chose_at - 1 and _is_walk(c) for t, c in sent), 10)
        act = next((c for t, c in sent if t >= chose_at - 1 and _is_walk(c)), None)
        return chose_at, act

    def arrived(act: NpcActCmd | None) -> NpcIdle | None:
        return next((m for m in idles if act is not None and m.act == act.id), None)

    link.listeners.append(heard)
    link.send = sending  # type: ignore[method-assign]
    rumors.gate = lambda r, a, b: (a, b) in open_pairs  # every pair not opened here is held closed
    try:
        # 0. Vino and Malet know each other only from the stable's beds: their routines never bring them
        # together by day
        if not await _until(lambda: rumors.social is not None, 30):
            return _logged({"social": "failed (no people)"})
        social = rumors.social
        assert social is not None
        tie = social.ties.get(VINO, {}).get(MALETH)
        about = next((d for d in social.describe(VINO) if f"({MALETH})" in d), "no tie")
        night = tie is not None and bool(tie.meets) and all(e <= 8 * 60 or s >= 22 * 60 for s, e in tie.meets)
        check("ties", night, about)
        await link.debug("time", hour=9, minute=0)
        for npc in (MALETH, VINO, HILDA):  # their important dialogs would stop the hero (Malet's at once)
            try:
                await link.debug("skip_important", near=npc)
            except GameError as err:
                log.info("skip_important %s: %s", npc, err)
        await _until(lambda: rumors.clock is not None and rumors.clock % DAY >= 9 * 60, 15)
        t = (rumors.clock or 0) // DAY * DAY + 9 * 60 + 30
        soon = list(tie.occasions(VINO, MALETH, t, t + 60)) if tie is not None else []
        check("no_meeting_today", tie is not None and not soon, f"at {soon}" if soon else "")
        await link.debug("player_goto", waypoint=DAY_SCENE_HERO)

        def doing(npc: str) -> str:
            return talk.people[npc].state if npc in talk.people else "not around"

        ok = await _until(lambda: (doing(VINO), doing(MALETH)) == ("ZS_RAKE_FP", "ZS_STAND_GUARDING"), 40)
        check("routines", ok, f"Vino {doing(VINO)}, Malet {doing(MALETH)}")

        # 1. Lobart catches the hero lying; Vino, on his field 44 m off, does not hear it
        live = None if isinstance(dialogue.llm, FakeBackend) else dialogue.llm
        ok, claim = await _caught_by(link, dialogue, talk, LOBART, facts)
        check("caught", ok, facts("caught")[-1:])
        if not ok or claim is None:
            return _logged(results)
        around = [str(p.get("npc", "")).upper() for p in await link.story_nearby(LOBART, RUMOR_CM)]
        r = rumor()
        check(
            "vino_away",
            VINO not in around and r is not None and VINO not in r.known,
            f"within {RUMOR_CM // 100} m of Lobart: {VINO in around}; knows: {sorted(r.known) if r else '-'}",
        )
        # the scene's lines, else Lobart's script (it answers every call with LLM=fake)
        script = GossipScript(
            claim, claim, live, lines=DAY_LINES, quiet=False, reason="Вино пришёл с новостью"
        )
        dialogue.llm = script

        # 2. Vino hears it from Lobart far from the hero: only the fact
        talk.end()
        await link.debug("player_goto", to=VINO, distance=8000)
        far = await _until(lambda: VINO not in talk.people, 10)
        check("hero_far", far, f"Vino {metres(VINO):.0f} m")
        lines_calls, scenes = script.lines_calls, gossip.scenes
        passed = False
        for _ in range(3):
            # opened only within this synchronous call: no tick sees the pair open
            open_pairs.add((LOBART, VINO))
            passed = r is not None and rumors.offer(r, LOBART, VINO)
            open_pairs.discard((LOBART, VINO))
            if passed:
                break
            await asyncio.sleep(1.5)  # it waits (a gap not known yet): asked again
        got = told(VINO)
        check(
            "vino_heard",
            passed
            and len(got) == 1
            and got[0].get("from") == LOBART
            and int(got[0].get("hops", 0)) == 1
            and got[0].get("how") == "met"
            and gossip.scenes == scenes
            and script.lines_calls == lines_calls,
            _passes_text(got),
        )
        if not passed:
            return _logged(results)

        # 3. a save to walk it twice; the lot lets Vino tell Malet once he is asked (walk)
        saved = await _save(link, DAY_SLOT)
        check("saved", saved is not None, f"{saved.branch}@{saved.seq}" if saved is not None else "refused")
        if saved is None:
            return _logged(results)

        # 4. the hero by Malet: Vino is asked of his next hour and walks over; near the hero they say it
        await link.debug("player_goto", waypoint=DAY_SCENE_HERO)
        await asyncio.sleep(1.5)  # the roster comes twice a second: not the one from before he moved
        ok = await _until(lambda: metres(MALETH) <= WORDS_CM / 100 and 30 <= metres(VINO) <= 60, 10)
        check("hero_at_malet", ok, f"Malet {metres(MALETH):.1f} m, Vino {metres(VINO):.1f} m")
        first = gossip.scenes
        chose_at, act = await walk()
        ok = act is not None and await _until(lambda: arrived(act) is not None, 150)
        idle = arrived(act)
        near = [str(p.get("npc", "")).upper() for p in await link.story_nearby(MALETH, 600)]
        check(
            "walks",
            act is not None
            and act.args.get("arrive") is True
            and ok
            and idle is not None
            and idle.reason == "arrived"
            and VINO in near,
            f"{act.args if act else 'no act'}; {idle.reason if idle else 'no end'} after "
            f"{time.monotonic() - chose_at:.0f} s; within 6 m of Malet: {VINO in near}",
        )
        partner = talk.partner
        check("no_partner", partner is None, partner.npc if partner is not None else "")
        done = await _until(
            lambda: gossip.scenes > first and gossip.scene is None and gossip.last is not None, 60
        )
        scene = gossip.last if gossip.scenes > first else None
        lines = _scene_lines(scene)
        said = sorted((x for x in lines if x.line in starts), key=lambda x: starts[x.line][0])
        speakers = [x.npc for x in said]
        want = len(lines) if live is not None else len(DAY_LINES)
        says = [
            c for t, c in sent if t >= chose_at and isinstance(c, SayCmd) and c.npc.upper() in (VINO, MALETH)
        ]
        to_other = bool(says) and all(
            (c.to or "").upper() == (MALETH if c.npc.upper() == VINO else VINO) for c in says
        )
        voiced = [x for x in said if starts[x.line][1]]
        check(
            "scene",
            done
            and scene is not None
            and scene.state == "done"
            and len(said) == len(lines) == want
            and 2 <= want <= 4
            and all(n == (VINO, MALETH)[i % 2] for i, n in enumerate(speakers))
            and to_other
            and len(voiced) == len(said),
            f"{scene.state + ' ' + scene.why if scene else 'no scene'}; {len(said)} of {len(lines)} started: "
            f"{' -> '.join(social.names.get(n, n) for n in speakers)}; each to the other: {to_other}; "
            f"voiced {len(voiced)}",
        )
        got = told(MALETH)
        check(
            "one_fact_talked",
            len(got) == 1
            and got[0].get("from") == VINO
            and int(got[0].get("hops", 0)) == 2
            and got[0].get("how") == "talked",
            _passes_text(got),
        )
        await _until(lambda: bool(walked()), 15)
        mine = walked()
        fact = mine[-1] if mine else {}
        check(
            "day_fact",
            len(mine) == 1
            and fact.get("what") == "chat"
            and fact.get("outcome") == "told"
            and fact.get("where") == "FARM1_PATH_CITY"
            and "PLAYER" in (fact.get("seen_by") or []),
            fact or "none",
        )

        # 5. from the save, the hero by Vino's field: the same walk ends far from him, a fact only
        asked_before = asks[VINO]
        loading = asyncio.ensure_future(wait_message(link, Session, lambda m: m.reason == "load", timeout=60))
        await link.debug("load", DAY_SLOT)
        await loading
        await link.wait_world()
        if not await _until(lambda: rumors.social is not None and rumors.clock is not None, 30):
            return _logged({**results, "reloaded": "failed (no rumors after the load)"})
        await link.debug("player_goto", waypoint=DAY_FIELD_HERO)
        await asyncio.sleep(1.5)
        ok = await _until(lambda: metres(VINO) <= 20 and MALETH not in talk.people, 10)
        check("hero_at_field", ok, f"Vino {metres(VINO):.1f} m, Malet {metres(MALETH):.0f} m")
        again = await _until(lambda: asks[VINO] > asked_before, 30)
        check(
            "reloaded",
            bool(loaded_with) and not any(loaded_with) and again,
            f"routines known at the load: {loaded_with}; Vino's asked again: {again}",
        )
        lines_calls, scenes = script.lines_calls, gossip.scenes
        chose_at, act = await walk("_again")
        ok = act is not None and await _until(lambda: arrived(act) is not None, 150)
        idle = arrived(act)
        check(
            "walks_again",
            ok and idle is not None and idle.reason == "arrived",
            f"{idle.reason if idle else 'no end'} after {time.monotonic() - chose_at:.0f} s",
        )
        await _until(lambda: bool(told(MALETH)) and bool(walked()), 30)
        got = told(MALETH)
        await asyncio.sleep(3)  # nothing is said after it
        says = [
            c.text
            for t, c in sent
            if t >= chose_at and isinstance(c, SayCmd) and c.npc.upper() in (VINO, MALETH)
        ]
        check(
            "far_fact",
            len(got) == 1
            and got[0].get("from") == VINO
            and int(got[0].get("hops", 0)) == 2
            and got[0].get("how") == "visited"
            and gossip.scenes == scenes
            and script.lines_calls == lines_calls
            and not says,
            f"{_passes_text(got)}; {gossip.scenes - scenes} scene(s), "
            f"{script.lines_calls - lines_calls} model call(s), lines {says}",
        )
        mine = walked()
        fact = mine[-1] if mine else {}
        check(
            "day_fact_far",
            len(mine) == 1 and fact.get("outcome") == "told" and "PLAYER" not in (fact.get("seen_by") or []),
            fact or "none",
        )

        # 6. no sleep and no dead option in any menu of the two, all run long (both have a bed entry)
        bad: list[str] = []
        for npc, questions in recorder.requests:
            if npc not in (VINO, MALETH):
                continue
            for name, q in questions.items():
                criteria = q.get("criteria", {})
                keys = criteria if isinstance(criteria, dict) else {}
                bad += [f"{npc} {name}: {k}" for k in keys if k in DEAD_OPTIONS]
                if name in ("plan", "do"):
                    bad += [f"{npc} {name} {k}: {v!r}" for k, v in keys.items() if "sleep" in str(v).lower()]
        for row in tap.rows:
            if row.get("npc") not in (VINO, MALETH) or row.get("question") not in ("plan", "do"):
                continue
            for key, what in (row.get("args") or {}).items():
                args = (what or {}).get("args") or {}
                state, wp = str(args.get("state", "")).upper(), str(args.get("waypoint", "")).upper()
                if state.startswith(("ZS_GOTOBED", "ZS_SLEEP")) or wp == "NW_FARM1_INSTABLE_BED":
                    bad.append(f"{row['npc']} {row['question']} {key}: {state}@{wp}")
        asked = sum(1 for npc, _ in recorder.requests if npc in (VINO, MALETH))
        check("no_sleep_or_dead", not bad, "; ".join(bad[:5]) if bad else f"{asked} request(s) of the two")

        # 7. each routine asked only a few times: the first time, after its TTL, after the load
        many = {npc: n for npc, n in asks.items() if n > 3}
        check(
            "routine_asks",
            not many,
            f"too often: {many}" if many else f"Vino {asks[VINO]}, Malet {asks[MALETH]}, {len(asks)} npc's",
        )
    finally:
        rumors.gate = None
        link.send = send  # type: ignore[method-assign]
        link.listeners.remove(heard)
        brain.decider, brain.cfg, brain.trace = decider, cfg, trace
        if runner is not None:
            runner.cancel()
            link.listeners.remove(brain.on_message)
    return _logged(results)


def _is_walk(cmd: Any) -> bool:
    """Vino's start_activity (the day desk's walk)."""
    return isinstance(cmd, NpcActCmd) and cmd.npc.upper() == VINO and cmd.action == "start_activity"


async def _save(link: GameLink, slot: int, attempts: int = 15) -> Saved | None:
    """Save into the slot, again while the game refuses (in a dialog); None if it never saved."""
    for _ in range(attempts):
        saving = asyncio.ensure_future(wait_message(link, Saved, timeout=60))
        try:
            await link.debug("save", slot)
        except GameError as err:
            saving.cancel()
            log.info("save refused (%s), retrying", err)
            await asyncio.sleep(2)
            continue
        try:
            return await saving
        except TimeoutError:
            return None
    return None


async def _sample_views(
    link: GameLink, npcs: Sequence[str], into: list[tuple[float, dict[str, NpcView]]], every: float = 0.25
) -> None:
    """What the npc's perceive, asked from the game every so often until cancelled."""
    while True:
        try:
            views = await link.npc_view(list(npcs), timeout=2)
        except (GameError, TimeoutError) as err:
            log.debug("npc_view: %s", err)
        else:
            into.append((time.monotonic(), {v.npc.upper(): v for v in views}))
        await asyncio.sleep(every)


def _passes_text(facts: list[Fact]) -> str:
    """Rumor facts in short, for a check's detail."""
    passes = [f"from {f.get('from')}, how {f.get('how')}, hops {f.get('hops')}" for f in facts]
    return f"{len(facts)} fact(s){': ' if passes else ''}{'; '.join(passes)}"


def _scene_lines(scene: Scene | None) -> list[SceneLine]:
    """The lines of a scene as they were given to the game."""
    return list(scene.play.lines) if scene is not None and scene.play is not None else []


def _sees(views: dict[str, NpcView], viewer: str, who: str) -> Look | None:
    """How `viewer` sees `who` in these views; None if they don't have it, or it does not see it."""
    v = views.get(viewer)
    return next((o for o in v.sees if o.id.upper() == who), None) if v is not None else None


def _faced(
    samples: list[tuple[float, dict[str, NpcView]]], line: SceneLine, at: float, *, first: bool
) -> str:
    """Whether the speaker of a scene line faced the one it says it to within FACE_S of the line's start, by
    the addressee's view: yes or no; for the first line inconclusive when it faced it already before, or when
    the addressee did not see it."""
    looks = [(t, o) for t, views in samples if (o := _sees(views, line.to, line.npc)) is not None]
    during = [o for t, o in looks if at <= t <= at + FACE_S]
    before = [o for t, o in looks if t < at]
    if first and before and before[-1].faces_you:
        return "inconclusive: faced before"
    if any(o.faces_you for o in during):
        return "yes"
    if not during:
        return "inconclusive: not seen" if first else "no: not seen"
    return "no"


def _talked_to(
    samples: list[tuple[float, dict[str, NpcView]]],
    said: list[SceneLine],
    starts: dict[str, tuple[float, bool]],
    i: int,
) -> bool:
    """While line i was said (from its start to the next one's), its speaker saw the one it speaks to talking
    to it. For a later line only once the look of the line before has run out (a line keeps the two each
    other's talk partners a second after its voice: npc.cpp), or that look would pass for a line said to the
    wrong one."""
    line = said[i]
    start = starts[line.line][0]
    end = starts[said[i + 1].line][0] if i + 1 < len(said) else start + (line.audio_s or 3.0)
    if i:
        before = said[i - 1]
        start = max(start, starts[before.line][0] + (before.audio_s or 3.0) + LOOK_TAIL_S)
    return any(
        (o := _sees(views, line.npc, line.to)) is not None and (o.talking_to or "").upper() == line.npc
        for t, views in samples
        if start <= t <= end
    )


def _voices_in_turn(log_text: str | None, lines: list[str]) -> tuple[bool, str]:
    """From the game's log: each line's voice played out before the next one started, and no other voice
    started while one played ("[ai] voice <id> starts" / "played", aibridge.cpp)."""
    if log_text is None:
        return False, "no game.log"
    events = re.findall(r"\[ai\] voice (\S+) (starts|played)", log_text)
    at: dict[tuple[str, str], int] = {}
    for i, event in enumerate(events):
        at.setdefault(event, i)
    missing = [x for x in lines if (x, "starts") not in at or (x, "played") not in at]
    if missing or not lines:
        return False, f"no voice of {missing or 'any line'} in game.log"
    for a, b in pairwise(lines):
        if at[(b, "starts")] < at[(a, "played")]:
            return False, f"{b} started before {a} played out"
    for x in lines:
        over = [v for v, what in events[at[(x, "starts")] + 1 : at[(x, "played")]] if what == "starts"]
        if over:
            return False, f"{', '.join(over)} started while {x} played"
    return True, f"{len(lines)} voices, one after another"


async def _game_log(log_dir: str | None) -> str | None:
    """The game's log in log_dir (scripts/e2e.sh runs the game there); None without it."""
    path = Path(log_dir, "game.log") if log_dir else None
    if path is None or not await asyncio.to_thread(path.exists):
        return None
    return await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")


def _scene_rows(log_dir: str | None) -> list[dict[str, Any]]:
    """The `scene` rows of the turn traces (gossip.py) in log_dir/traces."""
    if not log_dir:
        return []
    paths = sorted(Path(log_dir, "traces").glob("*.jsonl"))
    return [row for p in paths for row in read_trace(p) if row.get("type") == "scene"]


async def write_cards(
    link: GameLink, graph: Any, llm: LLMBackend, only: set[str] | None = None, path: Path | None = None
) -> dict[str, str]:
    """Character cards (docs/npc-tools.md, wave 1 step 3): the model writes them, a few npc's a call, for
    those of `only` (GOTHIC_CARDS: symbols, comma separated) or for everyone of this world with lines of its
    own, into gamedata/characters.json (cards there for others stay). Saved after every batch; who got no
    card is asked about once more. Without `only`, those that have a card already are left as they are: a
    run cut short goes on where it stopped."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    people = await link.story_people(timeout=30)
    target = path or characters.CARDS_FILE
    cards = characters.load(target)
    subjects = [x for x in characters.subjects(graph, people, only) if only or x.npc not in cards]
    check(
        "subjects", bool(subjects) or not only, f"{len(subjects)} of {len(people)} people, {len(cards)} cards"
    )
    left = list(subjects)
    for attempt in (1, 2):
        missed = []
        for i in range(0, len(left), characters.BATCH):
            batch = left[i : i + characters.BATCH]
            got, errors = await characters.write(llm, batch)
            cards.update(got)
            characters.save(cards, target)
            missed += [x for x in batch if x.npc not in got]
            log.info(
                "cards: %d/%d written (attempt %d)%s",
                len(got),
                len(batch),
                attempt,
                f": {errors}" if errors else "",
            )
        left = missed
        if not left:
            break
    check(
        "written",
        not left,
        f"{len(subjects) - len(left)} of {len(subjects)}; none for {[x.npc for x in left]}",
    )
    return _logged(results)


CANTHAR, LUTERO = "VLK_468_CANTHAR", "VLK_404_LUTERO"


async def haggle(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    deals: Deals,
    npcs: tuple[str, str] = (CANTHAR, LUTERO),
    ware: str = "ITMI_SILVERCUP",
) -> dict[str, str]:
    """Character in a deal (docs/npc-tools.md, wave 1 step 3), live only (LLM=chatgpt). The hero offers the
    same silver cup to two traders who would buy it, a greedy one and a fairer one by their character cards
    (Canthar, Lutero), and asks for more, as a player would: he agrees in words when a price is named. What
    each offers first and after the haggling is logged; the greedy one should offer less."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    if isinstance(dialogue.llm, FakeBackend):
        check("live", False, "run with LLM=chatgpt")
        return _logged(results)
    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    offers: dict[str, list[int]] = {}

    def buying(npc: str, version: int) -> int | None:
        deal = deals.open.get(npc)
        if deal is None or deal.version < version or not any(g.item == ware for g in deal.hero_gives):
            return None
        return sum(g.count for g in deal.npc_gives if g.item == GOLD)

    for npc in npcs:
        await link.attach(npc)
        await link.act(npc, "debug_add_item", item=GOLD, count=300)
        await link.act(npc, "debug_add_item", item=ware, count=1)
        await link.detach(npc)
        await link.story_effect(npc, [{"do": "give", "item": ware, "count": 1}])  # the cup is the hero's
        await link.debug("skip_important", near=npc)
        await link.debug("player_goto", to=npc, distance=250)
        # the roster must have him close: else the words would go to the one the hero talked to before
        if not await _until(lambda npc=npc: npc in talk.people and talk.people[npc].distance <= 600, 20):
            check(f"{npc}_near", False)
            continue
        name = talk.people[npc].name
        got: list[int] = []

        async def say_until(lines: list[str], version: int, npc: str = npc) -> int | None:
            """Say the lines one by one until the npc offers a deal of `version` or later."""
            for line in lines:
                await link.debug("type", text=line)
                if await _until(lambda: buying(npc, version) is not None, 45):
                    return buying(npc, version)
            return None

        first = await say_until(
            [
                f"{name}, купишь у меня серебряный кубок? Сколько дашь?",
                f"{name}, назови цену.",
                f"{name}, согласен, давай по рукам.",
            ],
            1,
        )
        if first is not None:
            got.append(first)
            version = deals.open[npc].version
            more = await say_until(
                [
                    f"{name}, маловато. Кубок серебряный, накинь ещё.",
                    f"{name}, ладно, на твою новую цену согласен.",
                ],
                version + 1,
            )
            if more is not None:
                got.append(more)
            await link.decline_deal()
        offers[npc] = got
        check(f"{npc}_offered", bool(got), got)
        await asyncio.sleep(2)
    cards = dialogue.personas.cards
    greedy, generous = sorted(npcs, key=lambda n: -(cards[n].greed if n in cards else 3))
    a, b = offers.get(greedy, []), offers.get(generous, [])
    detail = f"{greedy} (greed {cards[greedy].greed if greedy in cards else '?'}): {a}; {generous}: {b}"
    check("greedy_pays_less", bool(a) and bool(b) and a[0] < b[0], detail)
    return _logged(results)


async def deal_demo(link: GameLink, deals: Deals, npc: str) -> dict[str, str]:
    """The deal window on screen for the test bench (scripts/bench/deal.bench): the npc offers a thing it has
    for 50 gold the hero lacks, a moment later for 30 he has; it stays a while, then the hero accepts."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(6)  # the bench sets its camera
    npc, hero = npc.upper(), ["inst", "HERO"]
    # a potion to sell, and gold to leave the hero 40
    await link.attach(npc)
    await link.act(npc, "debug_add_item", item="ITPO_HEALTH_01", count=2)
    await link.act(npc, "debug_add_item", item=GOLD, count=100)
    await link.detach(npc)
    gold = int(await link.evaluate(["call", "NPC_HASITEMS", hero, ["sym", GOLD]]) or 0)
    if gold != 40:
        to = {"to": "self"} if gold > 40 else {}
        await link.story_effect(npc, [{"do": "give", "item": GOLD, "count": abs(gold - 40), **to}])
    snap = await link.snapshot(npc, fresh=True)
    wares = [i for i in snap.npc.inventory if not i.equipped and i.item != GOLD and i.count > 0]
    check("stocked", bool(wares), [i.item for i in snap.npc.inventory])
    if not wares:
        return _logged(results)

    def terms(price: int) -> dict[str, Any]:
        return {
            "npc_gives": [{"item": wares[0].item, "count": 1}],
            "hero_gives": [{"item": GOLD, "count": price}],
        }

    name = snap.npc.name or npc
    first = await deals.propose(npc, name, terms(50), snap)
    check("offered", first.status == "accepted", first.output)
    await asyncio.sleep(5)
    second = await deals.propose(npc, name, terms(30), snap)
    check("haggled", second.status == "accepted", second.output)
    await asyncio.sleep(6)
    pressed = await link.press_deal()
    check(
        "accepted",
        pressed == "sent" and await _until(lambda: bool(deals.chronicle.facts("deal")), 10),
        pressed,
    )
    await asyncio.sleep(3)
    return _logged(results)


async def gossip_demo(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    rumors: Rumors,
    gossip: GossipDesk,
    bodies: Bodies,
) -> dict[str, str]:
    """Two people talking about the hero, on screen for the test bench (scripts/bench/gossip.bench): Lobart
    has caught the hero lying about the turnips (the fact written into the chronicle), Hilda is put 5 m from
    him and the hero 7 m from them; the pair is opened and they say the lines of the gossip e2e in turn (the
    model's own with LLM=chatgpt), each to the other."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    await asyncio.sleep(1)
    open_pairs: set[tuple[str, str]] = set()
    rumors.gate = lambda r, a, b: (a, b) in open_pairs  # nothing passes but the scene
    try:
        if not await _until(lambda: rumors.social is not None, 30):
            return _logged({"social": "failed (no people)"})
        if isinstance(dialogue.llm, FakeBackend):
            dialogue.llm = GossipScript(dialogue.llm, dialogue.llm)
        await link.debug("time", hour=10, minute=0)
        await link.debug("skip_important", near=HILDA)  # her greeting would stop the hero
        day = int((await link.story_state()).get("day", 0))
        rumors.chronicle.record(
            {
                "fact": "caught",
                "quest": RUEBEN,
                "npc": LOBART,
                "name": "Лобарт",
                "title": "Убрать поле репы",
                "how": "сам видел, что репы в амбаре нет",
                "day": day,
            }
        )
        await link.debug("player_goto", waypoint=GOSSIP_OUT)
        await _until(lambda: LOBART in talk.people and HILDA in talk.people, 20)
        await link.debug("npc_goto", npc=HILDA, waypoint=GOSSIP_SPOT, face=LOBART)
        out = await bodies.act(
            HILDA,
            "start_activity",
            state="ZS_STAND_ARMSCROSSED",
            waypoint=GOSSIP_SPOT,
            minutes=60,
            arrive=True,
            timeout=60,
        )
        check("staged", out.status == "arrived", out.status)
        await asyncio.sleep(3)  # the bench records from here
        first = gossip.scenes
        open_pairs.add((LOBART, HILDA))
        began = await _until(lambda: gossip.scenes > first, 20)
        where = f"Hilda {talk.people[HILDA].distance / 100:.1f} m" if HILDA in talk.people else ""
        check("scene", began, where)
        ended = await _until(lambda: gossip.scenes > first and gossip.scene is None, 60)
        open_pairs.clear()
        scene = gossip.last if gossip.scenes > first else None
        play = scene.play if scene is not None else None
        said = play.said() if play is not None else []
        how = f"{scene.state} {scene.why}".strip() if scene is not None else "none"
        check(
            "said",
            ended and scene is not None and scene.state == "done",
            f"{how}, {len(said)} of {len(_scene_lines(scene))} line(s)",
        )
        await asyncio.sleep(3)
    finally:
        rumors.gate = None
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


async def witness_check(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    sightings: Sightings,
    witnesses: Witnesses,
    farmer: str = LOBART,
) -> dict[str, str]:
    """Asking a witness (wave 2 step 3), on the game. At eight in the morning someone Lobart knows sees the
    hero on the farm. An hour and a half later the hero tells Lobart he was in the city all morning; Lobart
    calls that one over (`ask_witness`; the model scripted unless LLM=chatgpt), it comes up and tells what it
    saw, and Lobart catches the lie by its word: «со слов». Then the hero names someone far away; Lobart
    asks at their next meeting by their routines: the answer is a fact, with no model call."""
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    chronicle, desk = sightings.chronicle, sightings.desk
    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    social = None
    if not await _until(lambda: witnesses.social() is not None, 15):
        return _logged({"social": "failed (nobody knows anybody yet)"})
    social = witnesses.social()
    assert social is not None
    farm = [n for n, t in sorted(social.ties.get(farmer, {}).items()) if t.meets]

    # 1. eight in the morning: someone Lobart meets on the farm sees the hero, Lobart does not
    await link.debug("time", hour=8, minute=0)
    # one in the open first: from indoors to the yard a walk may not find its way (Hilda at her stove)
    witness, tried = "", []
    for who, indoors_too in [(w, False) for w in farm] + [(w, True) for w in farm]:
        try:
            await link.debug("skip_important", near=who)
            await link.debug("teleport", near=who, distance=400)
        except GameError as err:
            tried.append(f"{who}: {err}")
            continue
        await asyncio.sleep(1)
        view = await desk.fresh(who)
        if view is not None and view.indoors and not indoors_too:
            tried.append(f"{who}: indoors")
            continue
        await link.npc_act(who, "turn_to")
        await _until(lambda who=who: bool(sightings.hero_trail(who)), 5)
        await desk.fresh(farmer)
        tried.append(f"{who}: {'saw' if sightings.hero_trail(who) else 'no'}")
        if sightings.hero_trail(who) and not sightings.hero_trail(farmer):
            witness = who
            break
    if not witness:
        return _logged({"witness_saw": f"failed (nobody saw him unseen by {farmer}: {', '.join(tried)})"})
    wname = social.names.get(witness, witness)
    seen = sightings.hero_trail(witness)[0]
    check("witness_saw", True, f"{wname}: {sightings.words(seen)}")

    # 2. an hour and a half later: the witness about its day, near Lobart or a walk away; the hero comes up
    # to Lobart only to say it (standing before him a while would give the lie away to his own eyes)
    await asyncio.sleep(12)  # the sighting ends unseen
    hour, minute = divmod(seen.end % 1440 + 90, 60)
    await link.debug("time", hour=hour, minute=minute)
    await asyncio.sleep(2)
    await link.debug("skip_important", near=farmer)
    await witnesses.prepare(farmer)
    tier = next((c.tier for c in witnesses._near.get(farmer, []) if c.npc == witness), "none")
    if tier != "near":
        # the scene of the plan: the witness within call (it is let go at once and walks back to its day)
        await talk.hold(witness, "scenario")
        await link.act(witness, "go_to_npc", npc=farmer, radius=900)
        talk.free(witness, "scenario")
    await link.debug("teleport", near=farmer, distance=300)
    await _until(lambda: farmer in talk.people and talk.people[farmer].distance < 800, 10)
    await asyncio.sleep(1)
    name = talk.people[farmer].name if farmer in talk.people else "Лобарт"
    talk.reflexes = Reflexes(FixedDecider({"posture": "listen"}))
    script = None
    if isinstance(dialogue.llm, FakeBackend):
        ask = FakeStep(
            f"{wname}! Поди-ка сюда. Ты его утром видел?",
            [ToolCall("ask_witness", {"who": wname, "said": "всё утро был в городе"})],
        )
        catch = FakeStep(
            "Вот как! Значит, ты мне врёшь.",
            [ToolCall("catch_lie", {"said": "всё утро был в городе", "seen": "С1"})],
        )
        others = ClaimScript(
            [("спрашивает тебя о герое", FakeStep("Утром он тут был, на ферме."))], [], "Ну."
        )
        script = ClaimScript([("Ответ:", catch), ("город", ask)], [], "Ну?", only=name, others=others)
        dialogue.llm = script
    # a lie with a stake for him: an alibi (the field is where the witness works)
    await link.debug("type", text=f"{name}, это не я утром топтал твою репу — я всё утро был в городе.")
    asked = lambda: any(f.get("witness") == witness for f in chronicle.facts("testimony"))  # noqa: E731
    if script is None and not await _until(asked, 20):
        # the live model did not ask anyone: the hero bluffs, he does not know the witness saw him
        await link.debug("type", text=f"{name}, не веришь — спроси у {wname}.")
    caught = await _until(lambda: any(f.get("witness") for f in chronicle.facts("caught")), 90)
    if script is not None:
        first = script.calls[0] if script.calls else None
        offered = [t.name for t in first[2]] if first else []
        check(
            "offered",
            "ask_witness" in offered and f"- {wname} — " in str(first[1] if first else ""),
            offered,
        )
    testimony = next((f for f in chronicle.facts("testimony") if f.get("witness") == witness), None)
    check(
        "testimony",
        testimony is not None and bool(testimony.get("told")) and testimony.get("tier") in ("near", "walk"),
        {k: testimony.get(k) for k in ("tier", "said", "told")} if testimony else "none",
    )
    tier = testimony.get("tier", tier) if testimony else tier
    if tier == "near":  # it came up to Lobart, and the hero beside him heard its answer
        state = await link.story_state(npcs=[witness], near=farmer)
        cm = (state.get("npcs", {}).get(witness) or {}).get("distance")
        check("came", cm is not None and float(cm) <= 500, f"near: {wname} {cm} cm from {name}")
        wconv = dialogue.conversations.get(witness)
        spoke = [e.content for e in wconv.memory.entries if e.role == "assistant"] if wconv else []
        check("witness_spoke", bool(spoke), spoke[-1:] or "silent")
    else:  # Lobart went to it and came back to the hero
        back = farmer in talk.people and talk.people[farmer].distance <= 600
        cm = talk.people[farmer].distance if farmer in talk.people else None
        check("came", tier == "walk" and back, f"{tier}: {name} back {cm} cm from the hero")
    fact = next((f for f in chronicle.facts("caught") if f.get("witness")), None)
    check(
        "caught", caught and fact is not None and fact["witness"] == witness, fact.get("how") if fact else ""
    )
    conv = dialogue.conversations.get(farmer)
    check(
        "wary", conv is not None and conv.intent.attitude in ("wary", "hostile"), conv.intent if conv else ""
    )
    lied = Trust(chronicle).of(farmer).lied
    check("trust", any("со слов" in x for x in lied), lied)

    # 3. someone far: asked at their next meeting, the answer is a fact, no model call
    await witnesses.prepare(farmer)
    far = next((c for c in witnesses._near.get(farmer, []) if c.tier == "far"), None)
    if far is None:
        check("far", False, f"no far acquaintance of {name}: {witnesses._near.get(farmer)}")
        return _logged(results)
    if script is not None:
        script.said.insert(
            0,
            (
                far.name,
                FakeStep(
                    f"Ладно, спрошу {far.name}, как увижу.",
                    [ToolCall("ask_witness", {"who": far.name, "said": "всё утро был в городе"})],
                ),
            ),
        )
    others = len(script.others.calls) if script is not None and isinstance(script.others, ClaimScript) else 0
    await link.debug("type", text=f"{name}, не веришь — спроси {far.name}, он подтвердит.")
    asked = await _until(lambda: any(f.get("witness") == far.npc for f in chronicle.facts("inquiry")), 60)
    q = next((f for f in chronicle.facts("inquiry") if f.get("witness") == far.npc), None)
    check("far_asked", asked, q.get("meets") if q else "no inquiry")
    if q is not None:
        hour, minute = divmod(int(q["meets"]) % 1440 + 5, 60)
        await link.debug("time", hour=hour, minute=minute)
        told = await _until(lambda: any(f.get("id") == q["id"] for f in chronicle.facts("testimony")), 20)
        t = next((f for f in chronicle.facts("testimony") if f.get("id") == q["id"]), None)
        calls = (
            len(script.others.calls) if script is not None and isinstance(script.others, ClaimScript) else 0
        )
        check(
            "far_answered",
            told and t is not None and t.get("tier") == "far" and calls == others,
            {k: t.get(k) for k in ("tier", "told")} if t else "none",
        )
    return _logged(results)


ERRAND_LINES = (
    (0, "Андре, слушай. Этот бродяга мне про репу наврал — сказал, что в амбаре, а там пусто.", "angry"),
    (1, "Вот как? Запомню. В ополчении такие не нужны.", "neutral"),
)
ERRAND_FAR_WP = "NW_XARDAS_START"  # the tower: far from the farm and from the city


async def errand_scenario(
    link: GameLink,
    dialogue: DialogueManager,
    talk: TalkDesk,
    errands: Errands,
    rumors: Rumors,
    gossip: GossipDesk | None,
    *,
    variant: Literal["near", "fork", "far"] = "near",
    npc: str = LOBART,
    to: str = ANDRE,
) -> dict[str, str]:
    """An errand (docs/npc-tools.md, wave 2 step 5: `plan`): Lobart catches the hero lying about the turnips,
    and to «ты кому-нибудь расскажешь?» sets out to tell Andre tomorrow at 7:00 (model scripted with
    LLM=fake; every pair's rumor held closed meanwhile, so that only the errand passes it). Checks:
    - planned: the goal, a fact;
    - near (SCENARIO=errand): the hero waits where Andre is by then; Lobart sets off at 7:00 from the farm,
      walks there for real (`there`), finds Andre and tells him in front of the hero: a rumor fact with
      `how: told` and `goal_done` (`told`), a scene of two voices (`scene`);
    - fork (SCENARIO=errand_fork): Andre is taken away before Lobart comes, another of the militia stands
      there: he hears it instead (`goal_done.instead_of`);
    - far (SCENARIO=errand_far): the hero is at the tower; at 7:00 nothing walks, and when Lobart would be
      there the fact comes (`far`), his body still at his routine, no model called.
    With LLM=chatgpt Lobart answers the question himself: whoever he sets out to tell and whenever, the rest
    follows his goal."""
    await link.wait_world()
    if link.session is None:
        await wait_message(link, Session, timeout=10)
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    facts = errands.chronicle.facts
    live = None if isinstance(dialogue.llm, FakeBackend) else dialogue.llm
    if live is not None and variant != "near":
        return _logged({"live": f"failed (SCENARIO=errand_{variant} is scripted only)"})
    if not await _until(lambda: rumors.social is not None, 30):
        return _logged({"social": "failed (no people)"})
    rumors.gate = lambda r, a, b: False  # the schedule passes nothing meanwhile: only the errand does
    try:
        await link.debug("time", hour=9, minute=0)
        ok, claim = await _caught_by(link, dialogue, talk, npc, facts)
        check("caught", ok, facts("caught")[-1:])
        if not ok or claim is None:
            return _logged(results)
        snap = await link.snapshot(npc, fresh=True)
        tomorrow = snap.world.day + 2  # as the npc sees days: from 1
        plan = {"do": "tell", "who": "Андре", "item": "", "count": 0, "day": tomorrow, "hour": 7, "minute": 0}
        claim.said.append(
            ("расскажешь", FakeStep("Ещё как. Завтра с утра схожу к Андре, пусть ополчение знает.",
                                    [ToolCall("plan", plan)]))
        )  # fmt: skip
        # live, Lobart's answer is his own model's
        model = _Recorded(GossipScript(live or claim, claim, live, lines=ERRAND_LINES, quiet=False))
        dialogue.llm = model
        name = talk.people[npc].name if npc in talk.people else "Лобарт"
        await link.debug("type", text=f"{name}, ты кому-нибудь расскажешь?")

        def goal() -> Any:
            return next((g for g in errands.goals.values() if g.npc == npc and g.do == "tell"), None)

        planned = await _until(lambda: goal() is not None, 60)
        g = goal()
        meant = g is not None and (live is not None or g.to == to)
        told = f"{g.when(errands.now)}: {g.what()}" if g is not None else "no goal"
        check("planned", planned and meant, told)
        if g is None:
            return _logged(results)
        to = g.to
        talk.end()

        # the night goes by (a leap, as a sleep: everyone at their routine's spots); the hero goes where
        # Andre will be, or far off
        before = g.start - 2
        if errands.now is None or before > errands.now:  # live, it may set off at once
            await link.debug("time", hour=before % GAME_DAY // 60, minute=before % 60)
        await asyncio.sleep(2)
        here = await link.snapshot(npc, fresh=True)
        walk = math.ceil(math.dist(here.npc.pos, errands._spot(to, g.start).pos) / 100 * 1.5 / 3.0)  # type: ignore[union-attr]
        there = errands._spot(to, g.start + walk)
        if there is None:
            return _logged({**results, "spot": f"failed (no spot of {to})"})
        if variant == "far":
            await link.debug("player_goto", waypoint=ERRAND_FAR_WP)
        else:
            await link.debug("player_goto", waypoint=there.waypoint)
            await link.debug("skip_important", near=to)  # Andre's greeting would stop the hero
        asked = len(model.asked)
        scenes = gossip.scenes if gossip is not None else 0  # before it sets off: the scene may come fast
        set_off = await _until(lambda: g.state not in ("agreed", "leaving"), 60)
        if variant == "far":
            check("away", set_off and g.state == "away", f"{g.state}, there at {clock(g.arrive)}")
            if g.state != "away":
                return _logged(results)
            then = g.arrive + 2
            await link.debug("time", hour=then % GAME_DAY // 60, minute=then % 60)
            done = await _until(lambda: g.state in ("done", "failed"), 30)
            fact = next((f for f in facts("rumor") if f.get("from") == npc and f.get("to") == to), None)
            snap = await link.snapshot(npc, fresh=True)
            home = rumors.social.spot(npc, then) if rumors.social is not None else None
            far = math.dist(snap.npc.pos, home.pos) / 100 if home is not None and home.pos else -1
            check("far", done and fact is not None and fact.get("how") == "told", f"{g.state}, {fact}")
            check("body", 0 <= far < 20, f"{far:.0f} m from its routine's spot")
            check("no_model", len(model.asked) == asked, f"{len(model.asked) - asked} request(s)")
            return _logged(results)
        check("set_off", set_off and g.state in ("going", "there"), f"{g.state}, to {there.waypoint}")
        if g.state not in ("going", "there"):
            return _logged(results)
        if variant == "fork":
            # Andre is taken away before Lobart comes (held there), and one of the militia stands there
            social = rumors.social
            guild = social.guilds.get(to) if social is not None else None
            others = sorted(
                (n for n, spot in (social.spots() if social is not None else ())
                 if n not in (npc, to) and social is not None and social.guilds.get(n) == guild
                 and spot.pos is not None and social.spot(n, g.start + walk) is spot),
                key=lambda n: math.dist(social.spot(n, g.start + walk).pos, there.pos),  # type: ignore[union-attr, arg-type]
            )  # fmt: skip
            if not others:
                return _logged({**results, "fork": "failed (no other of the guild)"})
            stand_in = others[0]
            await link.debug("npc_goto", npc=to, waypoint=ERRAND_FAR_WP)
            await link.attach(to)
            await link.debug("npc_goto", npc=stand_in, waypoint=there.waypoint)
        arrived = await _until(lambda: g.state in ("there", "done", "failed"), 1200)
        snap = await link.snapshot(npc, fresh=True)
        far = math.dist(snap.npc.pos, there.pos) / 100
        check("there", arrived and far < 15, f"{g.state}, {far:.0f} m from {there.waypoint}")
        done = await _until(lambda: g.state in ("done", "failed"), 120)
        got = next((f for f in facts("goal_done") if f.get("goal") == g.id), None)
        hearer = got.get("to") if got else None
        fact = next((f for f in facts("rumor") if f.get("from") == npc and f.get("to") == hearer), None)
        if variant == "fork":
            ok = done and got is not None and got.get("instead_of") == to and hearer != to
        else:
            ok = done and got is not None and hearer == to
        check("told", ok and fact is not None and fact.get("how") == "told", f"{g.state}, {got}, {fact}")
        played = await _until(lambda: gossip is None or gossip.scenes > scenes, 60)
        check("scene", played, f"{gossip.scenes if gossip else 0} scene(s)")
        if variant == "fork":
            await link.detach(to)
        return _logged(results)
    finally:
        rumors.gate = None


async def errand_give_scenario(
    link: GameLink, dialogue: DialogueManager, talk: TalkDesk, errands: Errands, *, npc: str = HILDA
) -> dict[str, str]:
    """A thing taken to someone (docs/npc-tools.md, wave 2 step 5: `plan` with give): the hero asks Hilda to
    take Lobart 25 gold; she walks out to him and hands it over: her gold −25, his +25, a fact `gave`
    (`gave`). Then Canthar's letter (a mission item, v39): the errand is turned down with the reason
    (`letter`). The model scripted with LLM=fake."""
    await link.wait_world()
    results: dict[str, str] = {}

    def check(name: str, ok: bool, detail: object = "") -> None:
        results[name] = f"{'done' if ok else 'failed'}{f' ({detail})' if detail != '' else ''}"

    gold, letter = "ITMI_GOLD", "ITWR_CANTHARS_KOMPROBRIEF_MIS"
    await link.debug("time", hour=9, minute=0)
    await link.debug("skip_important", near=npc)
    await link.debug("skip_important", near=LOBART)
    await link.story_effect(
        npc, [{"do": "create", "item": gold, "count": 25}, {"do": "create", "item": letter}]
    )
    await link.debug("player_goto", to=npc, distance=250)
    if not await _until(lambda: npc in talk.people and talk.people[npc].distance <= 600, 20):
        return _logged({"roster": f"failed ({npc} is not around the hero)"})

    def count(snap: Snapshot, item: str) -> int:
        return next((i.count for i in snap.npc.inventory if i.item.upper() == item), 0)

    hers, his = await link.snapshot(npc, fresh=True), await link.snapshot(LOBART, fresh=True)
    marked = next((i.mission for i in hers.npc.inventory if i.item.upper() == letter), None)
    check("marked", marked is True, f"mission: {marked}")
    name = talk.people[npc].name
    give = {"do": "give", "who": "Лобарт", "item": gold, "count": 25, "day": 0, "hour": 0, "minute": 0}
    script = ClaimScript(
        said=[
            ("письмо", FakeStep("Письмо? Ладно, отнесу.", [ToolCall("plan", {**give, "item": letter,
                                                                             "count": 1})])),
            ("золот", FakeStep("Ладно, отнесу ему.", [ToolCall("plan", give)])),
        ],
        told=[],
        default="Чего тебе?",
        only=name,
        others=None if isinstance(dialogue.llm, FakeBackend) else dialogue.llm,
    )  # fmt: skip
    dialogue.llm = script
    await link.debug("type", text=f"{name}, отнеси Лобарту 25 золотых.")

    def goal() -> Any:
        return next((g for g in errands.goals.values() if g.npc == npc and g.item == gold), None)

    planned = await _until(lambda: goal() is not None, 60)
    g = goal()
    check("planned", planned and g is not None, g.what() if g is not None else script.outputs[-1:])
    if g is None:
        return _logged(results)
    talk.end()
    done = await _until(lambda: g.state in ("done", "failed"), 180)
    gave = next((f for f in errands.chronicle.facts("gave") if f.get("goal") == g.id), None)
    hers2, his2 = await link.snapshot(npc, fresh=True), await link.snapshot(LOBART, fresh=True)
    moved = (count(hers, gold) - count(hers2, gold), count(his2, gold) - count(his, gold))
    check("gave", done and gave is not None and moved == (25, 25), f"{g.state}, {moved}, {gave}")
    await asyncio.sleep(3)
    await link.debug("player_goto", to=npc, distance=250)
    await _until(lambda: npc in talk.people and talk.people[npc].distance <= 600, 20)
    outputs = len(script.outputs)
    await link.debug("type", text=f"{name}, а ещё отнеси Лобарту письмо.")
    answered = await _until(lambda: len(script.outputs) > outputs, 60)
    said = script.outputs[outputs:]
    refused = any("вещь для задания" in o for o in said)
    check("letter", answered and refused and not any(x.item == letter for x in errands.goals.values()), said)
    return _logged(results)
