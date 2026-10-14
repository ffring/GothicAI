"""Scripted checks that drive an NPC without an LLM."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Collection, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import characters, programs
from .amends import Amends
from .beasts import BeastDesk
from .bodies import Bodies
from .brain import Decider, NpcPlanner, Outcome
from .brain.reflex import Reflexes
from .budget import Budget
from .chronicle import Fact
from .concerns import Concerns
from .contracts import gold_text
from .daylife import DayDesk
from .deals import GOLD, Deals
from .dialogue import DialogueManager, SceneLine, line_duration
from .director import PROPOSE, Director
from .game import GameError, GameLink
from .gossip import WORDS_CM, GossipDesk, Scene
from .incidents import IncidentDesk
from .initiative import ARRIVE_CM, CHECKINFO, InitiativeDesk
from .interject import Interjections
from .journal import Journal
from .knowledge import Knowledge, Topic
from .llm import Done, Item, LLMBackend, LLMEvent, Message, Tool, ToolCall, ToolChoice, ToolResult
from .llm.fake import FakeBackend, FakeStep
from .npc_tools import GESTURES as NPC_GESTURES
from .perception import PerceptionDesk, Sight
from .persona import PersonaProvider, habits_of
from .protocol import (
    ActionResult,
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
    NpcEvent,
    NpcIdle,
    NpcRoster,
    NpcView,
    Perception,
    PerceptionHeld,
    PlayerText,
    RoutineCmd,
    Saved,
    SayCmd,
    Session,
    Snapshot,
    ThreatControlCmd,
)
from .quests import RUMOR_CM, SEE_CM, QuestBoard
from .sightings import Sightings
from .social import DAY, TICK_S, Rumor, Rumors
from .story import StoryPatches
from .storyguard import StoryGuard
from .talk import TalkDesk, fights
from .trace import TraceWriter
from .trace import read as read_trace
from .trust import Trust
from .vanilla import CHECK_CM, VanillaQuests
from .voice import VoiceInput
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
    link: GameLink, story: StoryPatches, npc: str = "NONE_100_XARDAS", slot: int = 8
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
        check("said", True, (await said).line)
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
        return {"roster": f"failed ({npc} is not around the hero)"}
    dialogue.open(npc, "Ксардас").intent = Intent("follow_hero", "PLAYER", "friendly")

    def dist() -> float:
        return talk.people[npc].distance / 100 if npc in talk.people else -1.0

    async def order(
        text: str, action: str, focus: str | None = None, timeout: float = 15
    ) -> tuple[bool, str]:
        t0 = time.monotonic()
        if focus is None:
            await link.debug("type", text=text)
        else:
            talk.hear(text, focus, {"via": "scenario"})
        ok = await _until(lambda: any(a == action and t >= t0 for t, _, a, _ in calls), timeout)
        first = next(((t1, a, st) for t, t1, a, st in calls if t >= t0), None)
        took = f"{(first[0] - t0) * 1000:.0f} ms to {first[1]} {first[2]}" if first else "no action"
        done = ok and all(st == "done" for t, _, a, st in calls if t >= t0 and a == action)
        return done, took

    # the tower's walls stop a straight walk: every walk here starts where Xardas can walk from
    await link.debug("player_goto", near=npc, distance=900)
    await asyncio.sleep(1)
    ok, took = await order("Ксардас, ко мне!", "follow")
    came = await _until(lambda: 0 <= dist() <= 5, 20)
    results["come"] = f"{'done' if ok and came else 'failed'} (in {took}; {dist():.1f} m from the hero)"

    ok, took = await order("Стой!", "stop")
    await link.debug("player_goto", near=npc, distance=900)
    await asyncio.sleep(4)
    stood = dist()
    results["halt"] = f"{'done' if ok and stood >= 7.5 else 'failed'} (in {took}; stays {stood:.1f} m behind)"

    ok, took = await order("За мной!", "follow")
    closer = await _until(lambda: 0 <= dist() <= stood - 2, 15)
    results["follow"] = f"{'done' if ok and closer else 'failed'} (in {took}; {stood:.1f} -> {dist():.1f} m)"

    ok, took = await order("К оружию!", "draw_weapon")
    results["draw"] = f"{'done' if ok else 'failed'} (in {took})"

    await link.debug("spawn", instance="WOLF", near=npc, distance=500)
    await asyncio.sleep(1)
    ok, took = await order("В атаку!", "fight", focus="WOLF", timeout=60)
    over = await _until(lambda: any(a == "fight" for _, _, a, _ in calls) and calls[-1][2] == "follow", 60)
    fight = next((st for _, _, a, st in reversed(calls) if a == "fight"), "-")
    results["attack"] = (
        f"{'done' if ok and over and fight == 'done' else 'failed'} "
        f"(in {took}; fight {fight}, follows after {over})"
    )

    ok, took = await order("Убери оружие.", "remove_weapon")
    results["sheathe"] = f"{'done' if ok else 'failed'} (in {took})"

    ok, took = await order("Свободен.", "stop")
    free = await _until(lambda: npc not in talk.attached, 10)
    goal = dialogue.conversations[npc].intent.goal
    results["dismiss"] = (
        f"{'done' if ok and free and goal == 'keep_routine' else 'failed'} "
        f"(in {took}; detached {free}, goal {goal})"
    )

    results["no_model"] = f"{'done' if not llm.calls else 'failed'} ({len(llm.calls)} model call(s))"
    link.act = act  # type: ignore[method-assign]
    for name, outcome in results.items():
        log.info("%-12s %s", name, outcome)
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
    - defend: the companion fights him at once, with no order, and follows the hero again after;
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
    t0 = time.monotonic()
    talk.hear("Эй, ты! А ну отойди с дороги.", bandit, {"via": "scenario"})
    attacked = await _until(lambda: any(e["kind"] == "attack" for e in entries), 20)
    check("attack", attacked, "the scripts' attack" if attacked else "no attack")
    defended = await _until(
        lambda: any(c[3].get("npc", "").upper() == bandit for c in since(t0, "fight")), 30
    )
    started = since(t0, "fight")
    check("defend", defended, f"{started[0][0] - t0:.1f} s after the words" if started else "no fight")
    down = await _until(lambda: bandit not in talk.people or talk.people[bandit].activity == "down", 90)
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
    """The voices in the order they were heard, and where one started before the one before it was over."""
    order = sorted({w for _, w, _ in started}, key=lambda n: min(t for t, w, _ in started if w == n))
    out = []
    for a, b in zip(order, order[1:], strict=False):
        ends = [
            t + dialogue._audio.get(line, line_duration(dialogue._lines.get(line, ("", None))[0]))
            for t, w, line in started
            if w == a
        ]
        b_first = min(t for t, w, _ in started if w == b)
        if b_first < max(ends) - 0.15:
            out.append(f"{names.get(b, b)} {max(ends) - b_first:.1f} s over {names.get(a, a)}")
    return order, out


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
    says «Пацаны, здорово!» to nobody in particular; at once three others ask for words, as the fast loop's
    escalations do. Checks:
    - first: Brago, who leads them, answers first;
    - one_at_a_time: no voice starts before the one before it has been heard;
    - heard: the second answers with the first's words in mind;
    - three: three voices to the hero's phrase, the fourth says nothing."""
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
    order, overlaps = _overlaps(dialogue, started, names)
    check("one_at_a_time", quiet and len(order) >= 2 and not overlaps, overlaps or [names[n] for n in order])
    second = models[order[1]].calls[0][1] if len(order) > 1 and models[order[1]].calls else []
    heard = "Ты слышал, как Браго говорит герою" in " ".join(str(getattr(m, "content", "")) for m in second)
    check("heard", heard, names[order[1]] if len(order) > 1 else "nobody second")
    check("three", len(voices) == 3 and len(order) == 3, [names[n] for n in voices])
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
