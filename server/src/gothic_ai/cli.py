"""`gothic-ai`: AI server for the game, ChatGPT sign-in and diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import shlex
import sys
import time
from array import array
from pathlib import Path
from typing import Any

from . import npc_tools, places, scenarios
from .amends import Amends
from .bodies import Bodies
from .brain import DeciderClient, GameApplier, NpcPlanner, PlannerConfig
from .brain.reflex import Reflexes
from .brain.situation import SituationBuilder
from .budget import Budget
from .chronicle import Chronicle
from .concerns import Concerns
from .daylife import DayDesk
from .deals import Deals
from .dialogue import DialogueManager
from .director import Director
from .executor import Intent
from .fillers import Fillers
from .game import GameError, GameLink, GameServer
from .gamedata import storygraph
from .gamedata.ou import load_npc_lines
from .gossip import GossipDesk
from .incidents import IncidentDesk
from .initiative import InitiativeDesk
from .journal import Journal, print_sink
from .knowledge import Knowledge
from .llm import (
    BACKENDS,
    Done,
    Item,
    LLMBackend,
    LLMError,
    Message,
    Reasoning,
    TextDelta,
    ToolCall,
    ToolResult,
    make_backend,
)
from .llm.siwc import AuthError, TokenStore, login
from .memory import DEFAULT_PATH as DEFAULT_MEMORY_DB
from .memory import MemoryStore
from .perception import PerceptionDesk
from .persona import MOOD_DELIVERY, PersonaProvider, habits_of
from .protocol import (
    MOODS,
    Attached,
    Detached,
    GameMessage,
    Perception,
    PlayerText,
    Saved,
    Session,
    ThreatControlCmd,
)
from .quests import QuestBoard
from .replay import replay as replay_turn
from .sightings import Sightings
from .social import Rumors
from .story import StoryPatches
from .storyguard import StoryGuard
from .talk import TalkDesk
from .trace import TraceWriter, find_turn, latest
from .trust import Trust
from .tts import DEFAULT_TTS_URL, TtsClient, TtsError
from .vanilla import VanillaQuests
from .voice import (
    DEFAULT_ASR_URL,
    MIC_DENIED,
    AsrClient,
    AudioSource,
    Microphone,
    VoiceError,
    VoiceInput,
    WavSource,
)
from .witnesses import Witnesses
from .wrath import Wrath

log = logging.getLogger("gothic_ai")

DEFAULT_TRACE_DIR = DEFAULT_MEMORY_DB.parent / "traces"

DEFAULT_GAME_DIR = "/Volumes/shared/projects/Gothic II"

HELP = """commands (npc defaults to the last attached one):
  attach <npc> | detach [npc] | snap [npc] | ping
  goto <waypoint> | come | turn | ani <name> | wait <ms> | stop | routine
  schedule [npc]    the npc's daily routine, its variants and current script state
  talk <text>       say it as the hero, looking at the npc (talk.py decides whom it is said to)
  additem <item> [n] put an item into the npc's inventory (game needs -devmode)
  memory [npc]      the current save branch and what the npc remembers
  brain             what the npc planner is doing (tiers, requests, decisions)
  save <slot> | load <slot>   save_slot_<slot>.sav (game needs -devmode)
  graph             story graph changes on this save branch (docs/story-graph.md)
  graph hide|show <node>      switch a dialog or a choice off / on again
  graph cond <node> <json>    add a condition (story graph expression) to a dialog; "-" drops them
  graph add <json>            add a dialog: {"id", "npc", "description", "lines", "effects", ...}
  graph remove <id>           remove an added dialog
  quest <instance> <n> <item> <n> <title>   the npc gives the hero a quest: kill n creatures
                    (they appear 30 m away), the reward is an item it has
  quests            the director's quests: paid, lied about, found out
  director          quests left without an ending and the director's repairs of them
  director look     look for such quests now and repair them
  claims            quests of the scripts the hero said were done: believed, lies, found out
  know [npc] [query]  what the npc knows of the world now (M6), and what recall finds for the query
  see [npc]         what the npc perceives now: in Russian for its words, in English for its body
  seen [npc] [query]  what the npc saw itself: the hero's sightings, the people it knows (and recall)
  witness [npc]     whom the npc may ask about the hero, its questions under way and what it was told
  trust [npc]       what the npc knows of the hero's word: lies, rumors, claims that were true
  guard [npc]       whether the npc may leave its spot (plot, post), its concerns about the hero
  rumors            rumors of the hero's lies and who knows them, by retellings
  ties [npc]        whom the npc meets by its routine (and when) and has dealings with
  gossip            the latest scene of two talking about the hero near him: its lines, how it ended
  day [npc]         the npc's last menus of its next hour (the old plan, do) and the trip it is on
  amends            the director's storylets of amends for the hero's lies, and where they stand
  deal              offers open to the hero, and what the game's deal window shows (needs -devmode)
  deal <npc gives> / <hero gives>   the npc offers a deal, e.g. deal ITPO_HEALTH_01:2 / ITMI_GOLD:30
  deal accept       press Y on the deal window as the player does (game needs -devmode)
  dialogs           what the npc offers in the dialog menu now (game needs -devmode)
  choose <node>     play a dialog of the npc as if chosen in the menu (game needs -devmode)
  eval <json>       compute a story graph expression in the game, self = the npc (game needs -devmode)
  quit"""


def print_event(msg: GameMessage) -> None:
    match msg:
        case Attached():
            log.info("attached %s (%s)", msg.npc, msg.name)
        case Detached():
            log.info("detached %s: %s", msg.npc, msg.reason)
        case Perception():
            log.debug("perception %s: %s from %s at %.0fcm", msg.npc, msg.perc, msg.other, msg.distance)
        case PlayerText():
            log.info("player typed (focus %s): %s", msg.focus or "-", msg.text)
        case Saved():
            log.info("game saved at memory %s@%d", msg.branch, msg.seq)
        case Session():
            pass  # logged by the dialogue manager


class ServerState:
    def __init__(self) -> None:
        self.dialogue: DialogueManager | None = None
        self.voice: VoiceInput | None = None
        self.brain: NpcPlanner | None = None
        self.incidents: IncidentDesk | None = None
        self.talk: TalkDesk | None = None
        self.story: StoryPatches | None = None
        self.chronicle: Chronicle | None = None
        self.quests: QuestBoard | None = None
        self.director: Director | None = None
        self.vanilla: VanillaQuests | None = None
        self.trust: Trust | None = None
        self.rumors: Rumors | None = None
        self.amends: Amends | None = None
        self.knowledge: Knowledge | None = None
        self.deals: Deals | None = None
        self.bodies: Bodies | None = None
        self.concerns: Concerns | None = None
        self.guard: StoryGuard | None = None
        self.initiative: InitiativeDesk | None = None
        self.gossip: GossipDesk | None = None
        self.day: DayDesk | None = None
        self.perception: PerceptionDesk | None = None
        self.sightings: Sightings | None = None
        self.witnesses: Witnesses | None = None
        self.wrath: Wrath | None = None


class Console:
    """Line commands from stdin, executed against the current game link."""

    def __init__(self, server: GameServer, state: ServerState, npc: str) -> None:
        self.server = server
        self.state = state
        self.npc = npc

    async def run(self) -> None:
        reader = asyncio.StreamReader()
        loop = asyncio.get_running_loop()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        print(HELP, flush=True)
        while line := (await reader.readline()).decode():
            args = shlex.split(line)
            if not args:
                continue
            if args[0] in ("quit", "exit"):
                return
            link = self.server.link
            if link is None:
                print("game is not connected", flush=True)
                continue
            try:
                await self.execute(link, args)
            except (GameError, TimeoutError, KeyError) as err:
                print(f"error: {err}", flush=True)

    async def execute(self, link: GameLink, args: list[str]) -> None:
        cmd, rest = args[0], args[1:]
        npc = self.npc
        match cmd:
            case "attach":
                self.npc = (rest[0] if rest else npc).upper()
                await link.attach(self.npc)
            case "detach":
                await link.detach(rest[0] if rest else npc)
            case "snap":
                print((await link.snapshot(rest[0] if rest else npc, fresh=True)).model_dump_json(indent=2))
            case "schedule":
                info = await link.routine(rest[0] if rest else npc)
                print(f"now: {info.current}; variants: {', '.join(info.routines) or '-'}")
                for e in info.entries:
                    print(f"  {e.start}-{e.end} {e.state} at {e.waypoint}")
            case "ping":
                print(f"pong in {await link.ping() * 1000:.1f} ms")
            case "goto" if rest:
                print(await link.act(npc, "go_to_waypoint", waypoint=rest[0]))
            case "come":
                print(await link.act(npc, "go_to_npc", npc="PLAYER"))
            case "turn":
                print(await link.act(npc, "turn_to", npc="PLAYER"))
            case "ani" if rest:
                print(await link.act(npc, "play_ani", name=rest[0]))
            case "wait" if rest:
                print(await link.act(npc, "wait", ms=int(rest[0])))
            case "stop":
                print(await link.act(npc, "stop"))
            case "routine":
                print(await link.act(npc, "return_to_routine"))
            case "additem" if rest:
                count = int(rest[1]) if len(rest) > 1 else 1
                print(await link.act(npc, "debug_add_item", item=rest[0].upper(), count=count))
            case "memory" if self.state.dialogue is not None:
                d = self.state.dialogue
                branch = d.branch or "-"
                print(f"branch {branch}, head {d.memory.head(branch)}")
                mem = d.memory.load(branch, (rest[0] if rest else npc).upper())
                if mem.summary:
                    print(f"summary: {mem.summary}")
                for e in mem.entries:
                    print(f"  {e.seq:4d} {e.role:9s} {e.content}")
            case "save" | "load" if rest:
                await link.debug(cmd, int(rest[0]))
            case "brain" if self.state.brain is not None:
                print(self.state.brain.snapshot())
                for d in list(self.state.brain.applier.recent)[-10:]:
                    print(f"  {d.npc} {d.question}={d.choice} ({d.confidence:.2f}) {','.join(d.stimuli)}")
            case "graph" if self.state.story is not None:
                await self.graph(rest)
            case "quest" if len(rest) >= 5 and self.state.quests is not None:
                await self.quest(link, npc, rest)
            case "quests" if self.state.quests is not None:
                board = self.state.quests
                for q in board.quests.values():
                    state = "found out" if q.found_out else "lied" if q.lie else "paid" if q.paid else "open"
                    print(f"{q.id} {q.giver}: {q.title}; {q.reward()}; {state}; done {board.done(q)}")
            case "director" if self.state.director is not None:
                await self.director(rest)
            case "claims" if self.state.vanilla is not None:
                for t in self.state.vanilla.told.values():
                    state = "found out" if t.found_out else "lie" if t.lie else "true"
                    print(f"{t.quest} {t.npc}: {t.title}; {t.reward}; {state} ({t.kind or '-'})")
            case "see" if self.state.perception is not None:
                who = (rest[0] if rest else npc).upper()
                view = await self.state.perception.fresh(who)
                if view is None:
                    print("(no view)")
                else:
                    sight = self.state.perception.sight(who, link.snapshots.get(who))
                    assert sight is not None
                    print("\n".join(line for line in (sight.place, sight.hero, sight.people) if line))
                    print("\n".join(self.state.perception.english(who)))
            case "seen" if self.state.sightings is not None:
                who = (rest[0] if rest else npc).upper()
                print(self.state.sightings.situation(who) or "(saw nothing)")
                if len(rest) > 1:
                    print(self.state.sightings.recall(who, " ".join(rest[1:])) or "(recall: nothing)")
            case "witness" if self.state.witnesses is not None:
                who = (rest[0] if rest else npc).upper()
                await self.state.witnesses.prepare(who)
                print(self.state.witnesses.situation(who) or "(nobody to ask, nothing told)")
            case "know" if self.state.knowledge is not None:
                who = (rest[0] if rest else npc).upper()
                await self.state.knowledge.prepare(who)
                print(self.state.knowledge.situation(who))
                if len(rest) > 1:
                    print(self.state.knowledge.recall(who, " ".join(rest[1:])) or "(recall: nothing)")
            case "guard" if self.state.guard is not None:
                who = (rest[0] if rest else npc).upper()
                guard = self.state.guard
                state = (
                    self.state.talk.people[who].state
                    if self.state.talk and who in self.state.talk.people
                    else ""
                )
                body = guard.body(who, state) or "may walk"
                print(f"plot {guard.plot(who)}, post {who in guard.posts}, body: {body}")
                if self.state.concerns is not None:
                    print("concerns:", [c.key for c in self.state.concerns.of(who)] or "none")
                print("reads where it stands:", guard.placed().get(who, []) or "no")
            case "trust" if self.state.trust is not None:
                print(self.state.trust.situation(rest[0] if rest else npc) or "(nothing)")
            case "amends" if self.state.amends is not None:
                for s in self.state.amends.storylets.values():
                    closed = ", ".join(s.close)
                    print(f"{s.id} {s.name}: {s.stage}; closed {closed}; {s.gold} gold to {s.to_name}")
            case "deal" if self.state.deals is not None:
                await self.deal(link, npc, rest)
            case "rumors" if self.state.rumors is not None:
                print("\n".join(self.state.rumors.describe()) or "(none)")
            case "ties" if self.state.rumors is not None and self.state.rumors.social is not None:
                print("\n".join(self.state.rumors.social.describe(rest[0] if rest else npc)) or "(none)")
            case "gossip" if self.state.gossip is not None:
                gossip = self.state.gossip
                s = gossip.scene or gossip.last
                if s is None:
                    print(f"(no scene yet; {gossip.scenes} begun)")
                else:
                    print(f"{s.id} {s.a_name} -> {s.b_name}: {s.state}{f' ({s.why})' if s.why else ''}")
                    names = {s.a: s.a_name, s.b: s.b_name}
                    said = [x.text for x in s.play.said()] if s.play is not None else []
                    for n, text, _ in s.lines:  # + said, - written and not heard
                        print(f"  {'+' if text in said else '-'} {names[n]}: {text}")
                    for e in s.errors:
                        print(f"  rejected: {e}")
            case "day" if self.state.day is not None:
                self.day((rest[0] if rest else npc).upper())
            case "dialogs":
                print(" ".join(await link.offered_dialogs(rest[0] if rest else npc)) or "(none)")
            case "choose" if rest:
                await link.choose_dialog(rest[1] if len(rest) > 1 else npc, rest[0])
            case "eval" if rest:
                print(json.dumps(await link.evaluate(json.loads(rest[0]), rest[1] if len(rest) > 1 else npc)))
            case "talk" if rest and self.state.talk is not None:
                self.state.talk.hear(" ".join(rest), npc, {"via": "console"})
            case _:
                print(HELP)

    def day(self, npc: str) -> None:
        """day: the npc's last menus of its next hour, as the planner asked them, and the trip it is on."""
        desk = self.state.day
        assert desk is not None
        menu = desk.menus.get(npc)
        if menu is None:
            print("(not asked for its next hour yet)")
        elif menu.stay is not None:
            print(f"not asked: {menu.stay}")
        else:
            for key, text in menu.plan[0].items():
                print(f"  plan {key}: {text}")
            for o in menu.do.values():
                print(f"  do {o.key} [{o.id}]: {o.text}")
        if (trip := desk.walking.get(npc)) is not None:
            print(f"on its way: {trip.id}")

    async def deal(self, link: GameLink, npc: str, rest: list[str]) -> None:
        """deal: the open offers; deal accept: Y on the window; deal <npc gives> / <hero gives>: an offer."""
        deals = self.state.deals
        assert deals is not None
        if not rest:
            for d in deals.open.values():
                print(f"{d.id} v{d.version} {d.name}: {d.state}; {d.terms()}")
            print(f"window: {json.dumps(await link.deal_window(), ensure_ascii=False)}")
            return
        if rest == ["accept"]:
            print(await link.press_deal())
            return
        text = " ".join(rest)
        mine, _, theirs = text.partition("/")

        def gives(part: str) -> list[dict[str, object]]:
            out = []
            for word in part.split():
                item, _, count = word.partition(":")
                out.append({"item": item, "count": int(count or 1)})
            return out

        snap = await link.snapshot(npc, fresh=True)
        name = snap.npc.name or npc
        outcome = await deals.propose(
            npc, name, {"npc_gives": gives(mine), "hero_gives": gives(theirs)}, snap
        )
        print(f"{outcome.status}: {outcome.output}")

    async def quest(self, link: GameLink, npc: str, rest: list[str]) -> None:
        """quest <instance> <count> <reward item> <reward count> <title...>: the npc gives a quest."""
        assert self.state.quests is not None
        instance, count, item, pay = rest[0], int(rest[1]), rest[2].upper(), int(rest[3])
        snap = await link.snapshot(npc, fresh=True)
        reward = next((i.name for i in snap.npc.inventory if i.item == item), item)
        title = " ".join(rest[4:])
        q = await self.state.quests.give(npc, snap.npc.name, title, instance, count, item, reward, pay)
        print(f"{q.id}: {q.title} ({q.count} {q.instance} as {q.tag}), reward {q.reward()}")

    async def director(self, rest: list[str]) -> None:
        director = self.state.director
        assert director is not None
        if rest == ["look"]:
            print("repaired:", " ".join(await director.look()) or "nothing to repair")
            return
        story = await director.story()
        print("without an ending:", " ".join(story.dead_ends()) or "none")
        for r in director.repairs:
            print(f"  {r.quest}: {r.kind} {r.npc} {r.node}".rstrip())
        if director.given_up:
            print("no repair found:", " ".join(sorted(director.given_up)))

    async def graph(self, rest: list[str]) -> None:
        story = self.state.story
        assert story is not None
        match rest:
            case []:
                for op in story.ops():
                    print(json.dumps(op, ensure_ascii=False))
                return
            case ["hide", node]:
                op: dict[str, Any] = {"op": "disable", "node": node.upper()}
            case ["show", node]:
                op = {"op": "enable", "node": node.upper()}
            case ["cond", node, "-"]:
                op = {"op": "condition", "node": node.upper(), "expr": None}
            case ["cond", node, expr]:
                op = {"op": "condition", "node": node.upper(), "expr": json.loads(expr)}
            case ["add", node]:
                op = {"op": "add_node", "node": json.loads(node)}
            case ["remove", node]:
                op = {"op": "remove_node", "node": node.upper()}
            case _:
                print(HELP)
                return
        await story.apply(op)
        print("ok")


def _story_graph(args: argparse.Namespace, game_dir: Path | None) -> storygraph.StoryGraph | None:
    """The scripts' story graph (storygraph's JSON): a given file, or made from the game's scripts."""
    path = Path(args.story_graph) if args.story_graph else None
    if path is None and game_dir is not None:
        path = storygraph.ensure(game_dir, DEFAULT_MEMORY_DB.parent / "story.json")
    if path is None or not path.exists():
        log.warning(
            "no story graph, so no director and no npc knowledge "
            "(build OpenGothic/ai_bridge/storygraph or --story-graph)"
        )
        return None
    return storygraph.load(path)


def _backend(args: argparse.Namespace) -> LLMBackend:
    return make_backend(args.llm, args.model, base_url=args.base_url, reasoning_effort=args.reasoning_effort)


def _audio_source(args: argparse.Namespace) -> AudioSource:
    if args.voice_file:
        return WavSource(args.voice_file)
    device = int(args.mic_device) if args.mic_device and args.mic_device.isdigit() else args.mic_device
    return Microphone(device)


async def serve(args: argparse.Namespace) -> int:
    done = asyncio.Event()
    exit_code = 0
    state = ServerState()
    llm = _backend(args)
    game_dir = Path(args.game_dir) if args.game_dir else None
    personas = PersonaProvider(game_dir if game_dir and game_dir.is_dir() else None)
    memory = MemoryStore(args.memory_db)
    asr = None if args.no_voice else AsrClient(args.asr_url)
    decider = None if args.no_brain or not args.decider_url else DeciderClient(args.decider_url)
    source = None if args.no_voice else _audio_source(args)
    tts = None if args.no_tts else TtsClient(args.tts_url)
    # "Хм..." while the model thinks: made with the npc's voices, kept across reconnections of the game
    fillers = None if args.no_fillers else Fillers(None if tts is None else args.tts_url)
    journal = Journal(print_sink if args.journal else None)
    trace = None if args.no_trace else TraceWriter(Path(args.trace_dir))
    budget = Budget(per_npc_per_min=args.llm_per_npc_min, total_per_min=args.llm_total_min)
    log.info("LLM backend: %s (%s)", llm.name, getattr(llm, "model", "-"))
    log.info("memory: %s", args.memory_db)
    log.info("turn traces: %s", "off" if trace is None else args.trace_dir)
    log.info("voice: %s", "off" if asr is None else f"{args.voice_file or 'microphone'} -> {args.asr_url}")
    log.info("npc voices: %s", "off" if tts is None else args.tts_url)
    log.info("fillers: %s", "off" if fillers is None else ("voiced" if tts is not None else "subtitles"))
    log.info("npc planner: %s", "off" if decider is None else args.decider_url)
    graph = None if args.no_director and args.no_knowledge else _story_graph(args, game_dir)
    place_book = places.PlaceBook.load()
    log.info("places: %s", f"{len(place_book.places)} named" if place_book else "no book, waypoints only")
    log.info(
        "story graph: %s; director %s, npc knowledge %s",
        "none" if graph is None else f"{len(graph.nodes)} nodes, {len(graph.quests)} quests",
        "off" if graph is None or args.no_director else "on",
        "off" if graph is None or args.no_knowledge else "on",
    )

    async def on_link(link: GameLink) -> None:
        link.listeners.append(print_event)
        state.dialogue = DialogueManager(
            link,
            llm,
            personas,
            memory=memory,
            tts=tts,
            fillers=fillers,
            journal=journal,
            trace=trace,
            budget=budget,
        )
        state.story = StoryPatches(link, memory)
        # npc's we control: the body's instant reactions (behavioral model) and words for incidents
        reflexes = Reflexes(decider, apply_threats=args.apply_threats) if decider is not None else None
        state.incidents = IncidentDesk(link, state.dialogue, reflexes, budget, trace)
        # the hero talks to the world: whom he speaks to, the conversation, the body meanwhile (M5e)
        state.talk = TalkDesk(link, state.dialogue, reflexes, journal=journal, trace=trace)
        if asr is not None and source is not None:
            state.voice = VoiceInput(
                link, state.talk.hear, source, asr, journal=journal, files=args.voice_files
            )
        if graph is not None and not args.no_knowledge:
            # what each npc knows of the world as far as the story has got: its dialogs, quests, talk (M6)
            state.knowledge = Knowledge(link, graph)
            state.dialogue.extensions.append(state.knowledge)

        def knows_hero(npc: str, dialogue: DialogueManager = state.dialogue) -> bool:
            """It has met the hero: it remembers talks with him, or he has heard its dialogs (knowledge)."""
            conv = dialogue.conversations.get(npc)
            if conv is not None and (conv.memory.entries or conv.memory.summary):
                return True
            if dialogue.branch is not None:
                mem = dialogue.memory.load(dialogue.branch, npc)
                if mem.entries or mem.summary:
                    return True
            told = state.knowledge.views.get(npc) if state.knowledge is not None else None
            return told is not None and bool(told.told)

        # what each npc sees: names for those it knows, a look for strangers (wave 1 step 4)
        perception = state.perception = PerceptionDesk(
            link,
            social=lambda: state.rumors.social if state.rumors is not None else None,
            knows_hero=knows_hero,
            places=place_book,
        )
        state.dialogue.extensions.append(perception)
        state.dialogue.sight = perception.sight
        state.talk.view_of = state.incidents.view_of = perception.english
        # what really happened, the director's quests and the hero's lies about them (story graph, stage 4)
        state.chronicle = Chronicle(link, memory)
        talk = state.talk
        # what each npc saw itself: where and how it saw the hero and the people it knows (wave 1 step 6)
        state.sightings = Sightings(
            link, memory, perception, state.chronicle, knows_hero=knows_hero, journal=journal
        )
        state.dialogue.extensions.append(state.sightings)
        # what each npc knows of the hero's word: its lies found out, rumors, claims that were true
        trust = state.trust = Trust(state.chronicle)
        state.dialogue.extensions.append(trust)
        # deals the npc's offer the hero in conversation: a window, the player accepts (docs/npc-tools.md)
        state.deals = Deals(link, state.dialogue, state.chronicle, journal=journal)
        state.dialogue.extensions.append(state.deals)
        talk.on_talk_end.append(state.deals.end)
        if args.wrath or args.scenario == "wrath":
            # an ending off the main game: insult Xardas enough and he raises you as a skeleton
            state.wrath = Wrath(link, state.dialogue, journal=journal)
            state.dialogue.extensions.append(state.wrath)
        debts = asyncio.create_task(state.deals.run())  # a debt not paid back by its day is a broken word
        # what each npc has on its mind about the hero, and what may come of it (wave 2 step 1)
        state.bodies = Bodies(link)
        state.concerns = Concerns(link, state.chronicle, state.deals)
        state.guard = StoryGuard(graph)
        # the npc checks the hero's words by asking a witness it knows (wave 2 step 3)
        witnesses = state.witnesses = Witnesses(
            link,
            state.dialogue,
            state.sightings,
            perception,
            state.chronicle,
            state.guard,
            social=lambda: state.rumors.social if state.rumors is not None else None,
            budget=budget,
            hero_cm=lambda npc: talk.people[npc].distance if npc in talk.people else None,
            state_of=lambda npc: talk.people[npc].state if npc in talk.people else "",
            controlled=lambda npc: npc.upper() in talk.attached,
            partner=lambda: talk.partner.npc if talk.partner is not None else None,
            talking=lambda npc, on: talk.planner.talking(npc, on) if talk.planner is not None else None,
            journal=journal,
        )
        # its lines right after what the npc saw itself
        state.dialogue.extensions.insert(state.dialogue.extensions.index(state.sightings) + 1, witnesses)
        state.concerns.extra.append(witnesses.concerns)
        witnesses.on_change.append(state.concerns.changed)  # a far answer's time on its mind is over
        asks = asyncio.create_task(witnesses.run())  # far witnesses are asked at their meeting
        state.quests = QuestBoard(
            link,
            state.dialogue,
            state.chronicle,
            hero_distance=lambda npc: talk.people[npc].distance if npc in talk.people else None,
            journal=journal,
            trust=trust,
        )
        lies = asyncio.create_task(state.quests.run())
        directing: list[asyncio.Task[None]] = []
        if graph is not None and not args.no_director:
            # quests the hero can no longer finish get repaired (story graph, stage 5)
            state.director = Director(
                link, graph, state.story, state.chronicle, llm, dialogue=state.dialogue, journal=journal
            )
            # the scripts' quests in their givers' conversations, and the hero's lies about them
            state.vanilla = VanillaQuests(
                link,
                state.dialogue,
                state.chronicle,
                graph,
                hero_distance=lambda npc: talk.people[npc].distance if npc in talk.people else None,
                journal=journal,
                trust=trust,
            )
            # those who learn of a lie of the hero's ask him to make amends: the director's storylets
            state.amends = Amends(
                link,
                graph,
                state.story,
                state.chronicle,
                state.director,
                llm,
                dialogue=state.dialogue,
                journal=journal,
            )
            directing = [
                asyncio.create_task(state.director.run()),
                asyncio.create_task(state.vanilla.run()),
                asyncio.create_task(state.amends.run()),
            ]
        # rumors of the hero's lies go round the people that know each other (story graph, stage 5)
        state.rumors = Rumors(link, state.dialogue, state.chronicle, graph)
        directing.append(asyncio.create_task(state.rumors.run()))
        # near the hero a retelling is said aloud, in the two's own voices, or seen (wave 2 step 2)
        state.gossip = GossipDesk(
            link,
            state.dialogue,
            talk,
            state.rumors,
            perception,
            budget,
            state.chronicle,
            trust,
            state.guard,
            state.bodies,
            trace=trace,
            journal=journal,
        )
        # what each npc does in its next hour: trips from its everyday concerns (wave 2 step 6); always
        # there, so the deaths the npc's see are written down without the planner too
        state.day = DayDesk(
            link,
            state.rumors,
            state.chronicle,
            state.bodies,
            state.guard,
            perception,
            talk,
            place_book,
            journal=journal,
        )
        brain = None
        if decider is not None:
            # npc's outside conversations: the bridge reports them (npc_roster, npc_event, protocol v5)
            concerns = state.concerns

            def agenda_of(npc: str, dialogue: DialogueManager = state.dialogue) -> str | None:
                """What it wants of the hero: the conversation's agenda, else its most pressing concern."""
                saved = memory.intent(dialogue.branch, npc) if dialogue.branch else None
                agenda = Intent.from_json(saved).agenda if saved else None
                return concerns.current(npc, agenda) or concerns.agenda(npc)

            planner = state.brain = NpcPlanner(
                decider,
                GameApplier(link),
                agenda_of=agenda_of,
                routine_of=link.routine,
                habits_of=habits_of,
                view_of=perception.english,
                # each npc's character, from its card (characters.py)
                builder=SituationBuilder(traits={npc: card.en for npc, card in personas.cards.items()}),
                # the new day menu applied, or still asked in shadow beside the old one
                config=PlannerConfig(apply_day=args.apply_day),
                trace=trace,  # its decisions: data for fine-tuning (M5d)
            )
            state.incidents.follow(planner)  # free npc's speak when the moment calls for it
            state.talk.planner = planner  # npc's in a conversation are left to it; others overhear
            if not args.no_hold_threats:
                # threats to free npc's: the behavioral model first, the scripts if it does not take them
                await link.send(ThreatControlCmd(hold=True))
            link.listeners.append(planner.on_message)
            # what the npc wants from the hero, from the conversation, steers it afterwards
            state.dialogue.on_intent = lambda npc, intent: planner.set_agenda(
                npc, concerns.current(npc, intent.agenda or None) or concerns.agenda(npc)
            )
            concerns.on_change.append(lambda npc: planner.set_agenda(npc, agenda_of(npc)))
            # an npc with something on its mind comes up to the hero and speaks first (wave 2 step 1)
            state.initiative = InitiativeDesk(
                link,
                state.dialogue,
                state.talk,
                planner,
                concerns,
                state.guard,
                budget,
                state.chronicle,
                bodies=state.bodies,
                perception=perception,
                journal=journal,
            )
            # two talking near the hero: their veto before the initiative's, whose first words wait for them
            state.gossip.follow(planner, state.initiative)
            # the day's menus and trips: its veto before the two talking's
            state.day.follow(planner, state.gossip, state.initiative)
            brain = asyncio.create_task(planner.run())
        try:
            await _run_scenario(link)
            await link.closed.wait()  # the session lives while the game is connected
        finally:
            lies.cancel()
            debts.cancel()
            asks.cancel()
            for task in directing:
                task.cancel()
            if brain is not None:
                brain.cancel()

    async def _run_scenario(link: GameLink) -> None:
        nonlocal exit_code
        if args.scenario is None:
            return
        try:
            if args.scenario == "m0":
                steps = await scenarios.m0_smoke(link, args.npc)
                failed = [s for s in steps if s.result.status in ("failed", "timeout")]
                log.info("scenario m0: %d steps, %d failed", len(steps), len(failed))
                exit_code = 1 if failed else 0
            elif args.scenario == "m2":
                results = await scenarios.m2_actions(link, args.npc)
                bad = {
                    k: v
                    for k, v in results.items()
                    if not k.startswith("gesture") and k != "give_item_missing" and not v.startswith("done")
                }
                log.info("scenario m2: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "m1":
                lines = await scenarios.m1_chat(link, state.dialogue, args.npc)
                log.info("scenario m1: npc spoke %d line(s)", len(lines))
            elif args.scenario == "m4":
                results = await scenarios.m4_planner_bridge(link, args.npc, state.brain)
                expected_rejects = ("unknown_npc", "attached_soft_act")
                bad = {
                    k: v
                    for k, v in results.items()
                    if not v.startswith("rejected" if k in expected_rejects else "done")
                }
                log.info("scenario m4: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "m5":
                results = await scenarios.m5_turn_loop(link, args.npc, decider, trace)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario m5: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "m5b":
                results = await scenarios.m5b_bridge(link, args.npc)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario m5b: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "m5c":
                results = await scenarios.m5c_free_npc(link, args.npc)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario m5c: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario in ("rescue", "rescue_self"):
                killer = "hero" if args.scenario == "rescue" else "npc"
                results = await scenarios.rescue(
                    link, planner=state.brain, killer=killer, dialogue=state.dialogue if state.brain else None
                )
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario %s: %d checks, %d failed", args.scenario, len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "m5e":
                assert state.dialogue is not None and state.talk is not None
                results = await scenarios.m5e_talk(link, state.dialogue, state.talk, state.voice, args.npc)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario m5e: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "m6":
                dialogue, talk, knowledge = state.dialogue, state.talk, state.knowledge
                assert dialogue is not None and talk is not None and knowledge is not None
                results = await scenarios.m6_knows(link, dialogue, talk, knowledge, args.npc)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario m6: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "see":
                dialogue, talk, perception, rumors = (
                    state.dialogue,
                    state.talk,
                    state.perception,
                    state.rumors,
                )
                assert (
                    dialogue is not None
                    and talk is not None
                    and perception is not None
                    and rumors is not None
                )
                results = await scenarios.see_perception(link, dialogue, talk, perception, rumors, args.npc)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario see: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "seen":
                dialogue, talk, sightings = state.dialogue, state.talk, state.sightings
                assert dialogue is not None and talk is not None and sightings is not None
                results = await scenarios.seen_memory(link, dialogue, talk, sightings)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario seen: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "witness":
                dialogue, talk, sightings = state.dialogue, state.talk, state.sightings
                assert dialogue is not None and talk is not None and sightings is not None
                assert state.witnesses is not None
                results = await scenarios.witness_check(link, dialogue, talk, sightings, state.witnesses)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario witness: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "places":
                # not a check: the world's points and people for the book of places (places.py)
                await link.wait_world()
                where = Path(args.trace_dir).parent if args.trace_dir else Path(".")
                data = await places.dump(link, where / "world_points.json")
                log.info(
                    "places: %d points, %d people -> %s", len(data["points"]), len(data["people"]), where
                )
                exit_code = 0
            elif args.scenario == "wrath":
                dialogue, talk, wrath = state.dialogue, state.talk, state.wrath
                assert dialogue is not None and talk is not None and wrath is not None
                live = dialogue.llm.name != "fake"
                results = await scenarios.xardas_wrath(link, dialogue, talk, wrath, args.npc, live=live)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info(
                    "scenario wrath (%s): %d checks, %d failed",
                    "live" if live else "fake",
                    len(results),
                    len(bad),
                )
                exit_code = 1 if bad else 0
            elif args.scenario == "lie":
                dialogue, talk, quests = state.dialogue, state.talk, state.quests
                assert dialogue is not None and talk is not None and quests is not None
                results = await scenarios.lie_found_out(link, dialogue, talk, quests, args.npc)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario lie: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "repair":
                assert state.director is not None and state.story is not None
                results = await scenarios.repair_after_death(link, state.director, state.story)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario repair: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario in ("amends", "amends_deal"):
                dialogue, talk, amends = state.dialogue, state.talk, state.amends
                assert dialogue is not None and talk is not None and amends is not None
                deals = state.deals if args.scenario == "amends_deal" else None
                results = await scenarios.amends_storylet(link, dialogue, talk, amends, deals=deals)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario %s: %d checks, %d failed", args.scenario, len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "deal":
                dialogue, talk, deals = state.dialogue, state.talk, state.deals
                assert dialogue is not None and talk is not None and deals is not None
                results = await scenarios.deal_window(link, dialogue, talk, deals)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario deal: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "cards":
                if graph is None:
                    raise SystemExit("cards need the story graph")
                only = {n.strip().upper() for n in os.environ.get("GOTHIC_CARDS", "").split(",") if n.strip()}
                results = await scenarios.write_cards(link, graph, llm, only or None)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario cards: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "haggle":
                dialogue, talk, deals = state.dialogue, state.talk, state.deals
                assert dialogue is not None and talk is not None and deals is not None
                results = await scenarios.haggle(link, dialogue, talk, deals)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario haggle: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "debt":
                dialogue, talk, deals = state.dialogue, state.talk, state.deals
                assert dialogue is not None and talk is not None and deals is not None
                results = await scenarios.deal_debt(link, dialogue, talk, deals)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario debt: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "initiative":
                dialogue, talk, deals, concerns = state.dialogue, state.talk, state.deals, state.concerns
                guard, bodies, perception = state.guard, state.bodies, state.perception
                assert dialogue is not None and talk is not None and deals is not None
                assert concerns is not None and guard is not None and bodies is not None
                assert perception is not None
                # with DECIDER_URL the server's planner and its desk; else the scenario's fixed stand-in
                results = await scenarios.initiative_scenario(
                    link, dialogue, talk, deals, concerns, guard, bodies, perception, desk=state.initiative
                )
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario initiative: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "deal_demo":
                assert state.deals is not None
                results = await scenarios.deal_demo(link, state.deals, args.npc)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario deal_demo: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario in ("gossip", "gossip_demo"):
                dialogue, talk, rumors = state.dialogue, state.talk, state.rumors
                gossip, bodies, perception = getattr(state, "gossip", None), state.bodies, state.perception
                assert dialogue is not None and talk is not None and rumors is not None
                assert gossip is not None and bodies is not None and perception is not None
                if args.scenario == "gossip":
                    where = str(Path(args.trace_dir).parent) if args.trace_dir else None
                    results = await scenarios.gossip_scenario(
                        link, dialogue, talk, rumors, gossip, bodies, perception, log_dir=where
                    )
                else:
                    results = await scenarios.gossip_demo(link, dialogue, talk, rumors, gossip, bodies)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario %s: %d checks, %d failed", args.scenario, len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "day":
                dialogue, talk, rumors, gossip = state.dialogue, state.talk, state.rumors, state.gossip
                bodies, perception, day = state.bodies, state.perception, state.day
                assert dialogue is not None and talk is not None and rumors is not None
                assert gossip is not None and bodies is not None and perception is not None
                assert day is not None
                where = str(Path(args.trace_dir).parent) if args.trace_dir else None
                # with DECIDER_URL the server's planner (day settings for the run); else the scenario's own
                results = await scenarios.day_scenario(
                    link, dialogue, talk, rumors, gossip, bodies, perception, day, state.brain, log_dir=where
                )
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario day: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "rumor":
                dialogue, talk, rumors = state.dialogue, state.talk, state.rumors
                assert dialogue is not None and talk is not None and rumors is not None
                where = str(Path(args.trace_dir).parent) if args.trace_dir else None
                results = await scenarios.rumor_spread(link, dialogue, talk, rumors, log_dir=where)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario rumor: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "vcheck":
                dialogue, talk, vanilla = state.dialogue, state.talk, state.vanilla
                assert dialogue is not None and talk is not None and vanilla is not None
                results = await scenarios.vanilla_check(link, dialogue, talk, vanilla)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario vcheck: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "vlie":
                dialogue, talk, vanilla = state.dialogue, state.talk, state.vanilla
                assert dialogue is not None and talk is not None and vanilla is not None
                results = await scenarios.vanilla_lie(link, dialogue, talk, vanilla)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario vlie: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "graph":
                assert state.story is not None
                results = await scenarios.graph_dialogs(link, state.story, args.npc)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario graph: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "tts":
                results = await scenarios.tts_lines(link, state.dialogue, args.npc)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario tts: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
            elif args.scenario == "m3":
                results = await scenarios.m3_memory_voice(link, state.dialogue, state.voice, args.npc)
                bad = {k: v for k, v in results.items() if not v.startswith("done")}
                log.info("scenario m3: %d checks, %d failed", len(results), len(bad))
                exit_code = 1 if bad else 0
        except Exception:
            log.exception("scenario %s failed", args.scenario)
            exit_code = 1
        if args.exit_after:
            done.set()

    server = GameServer(args.host, args.port, on_link)
    tasks = [asyncio.create_task(server.serve_forever()), asyncio.create_task(done.wait())]
    if not args.no_console:
        tasks.append(asyncio.create_task(Console(server, state, args.npc).run()))
    finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await llm.aclose()
    if asr is not None:
        await asr.aclose()
    if tts is not None:
        await tts.aclose()
    if decider is not None:
        await decider.aclose()
    memory.close()
    for task in finished:
        if not task.cancelled() and (err := task.exception()) is not None:
            log.error("%s", err)
            return 1
    return exit_code


PROBE_SITUATION = "[Обстановка: твой инвентарь — зелье лечения (ITPO_HEALTH_01) ×2.]"
PROBE_TEXT = "Дай мне зелье лечения, пожалуйста."  # the npc has one: it calls give_item
PROBE_REJECTION = "Отклонено: зелья у тебя больше нет, ты отдал последнее раньше."


async def probe_tools(llm: LLMBackend, instructions: str, text: str) -> dict[str, Any]:
    """Can a turn go on after a tool call? Step 1 offers the tools; every call is answered as rejected and
    step 2 goes on with the response's own items (reasoning included) and tool_choice="none"."""
    messages: list[Item] = [Message("developer", PROBE_SITUATION), Message("user", text)]
    steps: list[dict[str, Any]] = []
    for n in (1, 2):
        started = time.monotonic()
        first: float | None = None
        done: Done | None = None
        async for event in llm.stream(
            instructions=instructions,
            messages=messages,
            tools=npc_tools.TOOLS,
            tool_choice="none" if n == 2 else None,
        ):
            if isinstance(event, TextDelta) and first is None:
                first = time.monotonic() - started
            elif isinstance(event, Done):
                done = event
        assert done is not None
        calls = [i for i in done.items if isinstance(i, ToolCall)]
        steps.append(
            {
                "text": done.text.strip(),
                "calls": [f"{c.name}({c.raw})" for c in calls],
                "reasoning_items": sum(isinstance(i, Reasoning) for i in done.items),
                "first_token_s": round(first or 0.0, 2),
                "total_s": round(time.monotonic() - started, 2),
                "usage": vars(done.usage) if done.usage else None,
            }
        )
        if not calls:
            break
        messages += [*done.items, *(ToolResult(c.call_id, PROBE_REJECTION) for c in calls)]
    ok = len(steps) == 2 and not steps[1]["calls"] and bool(steps[1]["text"])
    return {"ok": ok, "llm": llm.name, "model": getattr(llm, "model", None), "steps": steps}


async def llm_test(args: argparse.Namespace) -> int:
    """One request, prints the reply and latency: compare backends and models. With --json one object
    on stdout ({"text", "llm", "model", "first_token_s", "total_s"} or {"error"}), for the launcher."""
    llm = _backend(args)
    personas = PersonaProvider(Path(args.game_dir) if args.game_dir else None)
    persona = personas.get(args.npc, args.name)
    if args.probe_tools:
        try:
            result = await probe_tools(llm, persona.instructions, args.text or PROBE_TEXT)
        except LLMError as err:
            result = {"ok": False, "error": str(err)}
        finally:
            await llm.aclose()
        print(json.dumps(result, ensure_ascii=False, indent=None if args.json else 2))
        return 0 if result["ok"] else 1
    if not args.text:
        await llm.aclose()
        print("llm-test: the text to send is required", file=sys.stderr)
        return 2
    messages = [Message("user", args.text)]
    if args.situation:
        messages.insert(0, Message("developer", args.situation))
    tools = npc_tools.TOOLS if args.tools else None
    started = time.monotonic()
    first = None
    text = ""
    try:
        async for event in llm.stream(instructions=persona.instructions, messages=messages, tools=tools):
            if isinstance(event, TextDelta):
                if first is None:
                    first = time.monotonic() - started
                text += event.text
                if not args.json:
                    print(event.text, end="", flush=True)
            elif isinstance(event, ToolCall) and not args.json:
                at = time.monotonic() - started
                print(f"\n  -> {event.name}({event.arguments}) at {at:.2f}s", flush=True)
    except LLMError as err:
        if args.json:
            print(json.dumps({"error": str(err)}, ensure_ascii=False))
        else:
            print(f"\nerror: {err}", file=sys.stderr)
        return 1
    finally:
        await llm.aclose()
    total = time.monotonic() - started
    if args.json:
        result = {
            "text": text.strip(),
            "llm": llm.name,
            "model": getattr(llm, "model", None),
            "first_token_s": round(first or 0.0, 2),
            "total_s": round(total, 2),
        }
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"\n\n[{llm.name}] first token {first or 0:.2f}s, total {total:.2f}s", file=sys.stderr)
    return 0


async def places_cmd(args: argparse.Namespace) -> int:
    """The book of places from a dump of the world (SCENARIO=places): grouped, named by the model, written."""
    data = json.loads(await asyncio.to_thread(Path(args.dump).read_text, encoding="utf-8"))
    groups = places.group(data["points"], data.get("people", []))
    if args.area:
        groups = [g for g in groups if g.area in {a.upper() for a in args.area}]
    print(f"{len(data['points'])} points -> {len(groups)} places", flush=True)
    if args.dry_run:
        for g in sorted(groups, key=lambda g: g.id):
            print(f"  {g.id:40s} {len(g.points):4d} points  {'; '.join(g.residents[:3])}")
        return 0
    llm = _backend(args)
    try:
        names = await places.label(groups, llm)
    finally:
        await llm.aclose()
    book = places.book(groups, names, data.get("world"))
    text = json.dumps(book, ensure_ascii=False, indent=1)
    await asyncio.to_thread(Path(args.out).write_text, text, encoding="utf-8")
    print(f"{len(book['places'])} of {len(groups)} places named -> {args.out}")
    return 0 if len(book["places"]) == len(groups) else 1


async def replay(args: argparse.Namespace) -> int:
    """A recorded turn, run again: compare models and prompts on real situations from the game."""
    path = Path(args.file) if args.file else latest(Path(args.trace_dir))
    if path is None:
        print(f"no traces in {args.trace_dir}", file=sys.stderr)
        return 2
    try:
        record, instructions = find_turn(path, args.turn)
    except LookupError as err:
        print(err, file=sys.stderr)
        return 2
    if args.persona_now or instructions is None:
        game_dir = Path(args.game_dir) if args.game_dir else None
        personas = PersonaProvider(game_dir if game_dir and game_dir.is_dir() else None)
        instructions = personas.get(record["npc"], args.name).instructions
    llm = _backend(args)
    try:
        result = await replay_turn(llm, record, instructions)
    finally:
        await llm.aclose()
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print(
        f"turn {result['turn']} ({result['npc']}), {result['trigger']['kind']}: {result['trigger']['text']}"
    )
    for title, steps in (("recorded", result["original"]), (f"{llm.name}", result["replay"])):
        print(f"\n[{title}]")
        for n, st in enumerate(steps, 1):
            calls = ", ".join(
                c if isinstance(c, str) else f"{c['name']}{c['arguments']}" for c in st["calls"]
            )
            timing = f" ({st['first_token_s']:.2f}s / {st['total_s']:.2f}s)" if "total_s" in st else ""
            print(f"  {n}. {st['text'] or '-'}{f'  -> {calls}' if calls else ''}{timing}")
    if result["diverged"]:
        print("\n(the replay made calls the recorded turn did not: their results are not known)")
    return 0


async def login_chatgpt() -> int:
    store = TokenStore()
    try:
        creds = await login(store)
    except (AuthError, TimeoutError) as err:
        print(f"sign-in failed: {err}", file=sys.stderr)
        return 1
    print(f"signed in{f' as {creds.email}' if creds.email else ''}; credentials saved to {store.path}")
    return 0


async def voice_test(args: argparse.Namespace) -> int:
    """Record from the microphone (or play a wav), streaming it to the ASR service like push-to-talk;
    prints partial text as it comes and the final one with the latency after the end of speech.
    With --json, JSON lines on stdout for the launcher: {"level": 0..1} while recording (~10 a second),
    {"partial": text}, and last {"text", "provider", "audio_s", "latency_s"} or {"error"}."""
    source = _audio_source(args)
    asr = AsrClient(args.asr_url)
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    seconds = getattr(source, "duration", args.seconds)

    def out(msg: dict[str, object]) -> None:
        print(json.dumps(msg, ensure_ascii=False), flush=True)

    def partial(text: str) -> None:
        if args.json:
            out({"partial": text})
        else:
            print(f"  … {text}", file=sys.stderr)

    def fail(detail: str) -> int:
        if args.json:
            out({"error": detail})
        else:
            print(f"error: {detail}", file=sys.stderr)
        return 1

    try:
        session = await asr.open(source.rate, on_partial=partial)
        source.start(lambda data: loop.call_soon_threadsafe(queue.put_nowait, data))
        if not args.voice_file and not args.json:
            print(f"говорите {seconds:g} с...", file=sys.stderr, flush=True)
        loop.call_later(seconds + 0.1, lambda: (source.stop(), queue.put_nowait(None)))
        size, heard, peak, last_level = 0, False, 0.0, 0.0
        while (chunk := await queue.get()) is not None:
            size += len(chunk)
            heard = heard or chunk.count(0) != len(chunk)
            if args.json:
                peak = max(peak, _level(chunk))
                if (now := time.monotonic()) - last_level >= 0.1:
                    out({"level": round(peak, 3)})
                    peak, last_level = 0.0, now
            await session.feed(chunk)
        if size and not heard:
            await session.cancel()
            return fail(MIC_DENIED)
        result = await session.finish()
    except VoiceError as err:
        return fail(str(err))
    finally:
        await asr.aclose()
    audio_s = size / 2 / source.rate
    if args.json:
        out(
            {
                "text": result.text,
                "provider": result.provider,
                "audio_s": round(audio_s, 2),
                "latency_s": round(result.latency_s, 2),
            }
        )
        return 0
    print(result.text or "(ничего не распознано)")
    took = f"{audio_s:.1f}s audio, {result.latency_s:.2f}s after it ended"
    print(f"[{result.provider}] {took}", file=sys.stderr)
    return 0


def _level(chunk: bytes) -> float:
    """Loudness of 16-bit PCM as 0..1: -60 dBFS and below is 0, full scale is 1."""
    samples = array("h", chunk[: len(chunk) // 2 * 2])
    if not samples:
        return 0.0
    rms = math.sqrt(sum(x * x for x in samples) / len(samples))
    if rms < 1:
        return 0.0
    return max(0.0, min(1.0, (20 * math.log10(rms / 32768) + 60) / 60))


def mics(args: argparse.Namespace) -> int:
    """Input devices, for the launcher's microphone setting."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        default = sd.default.device[0]
    except Exception as err:  # PortAudio missing or broken
        print(json.dumps({"error": f"нет доступа к аудио: {err}"}, ensure_ascii=False))
        return 1
    found = [
        {"index": i, "name": d["name"], "default": i == default}
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]
    print(json.dumps(found, ensure_ascii=False))
    return 0


async def speech_test(args: argparse.Namespace) -> int:
    """One line in an npc's voice through the TTS service, played as it arrives, the way the game gets
    it (same voice id and description, so the provider reuses the npc's voice). With --json one object:
    {"text", "provider", "first_chunk_s", "audio_s", "latency_s"} or {"error"}."""
    personas = PersonaProvider(Path(args.game_dir) if args.game_dir else None)
    persona = personas.get(args.npc, args.name)
    tts = TtsClient(args.tts_url)
    player: Any = None
    try:
        import sounddevice as sd

        async def start(rate: int, _provider: str) -> None:
            nonlocal player
            player = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16")
            player.start()

        async def chunk(data: bytes) -> None:
            await asyncio.to_thread(player.write, data[: len(data) // 2 * 2])

        style = MOOD_DELIVERY[args.mood] if args.mood else ""
        speech = await tts.stream(args.text, args.npc.upper(), persona.voice, start, chunk, style)
        if player is not None:
            await asyncio.to_thread(player.stop)  # waits until the last chunk is heard
    except Exception as err:  # TtsError, or sounddevice's own errors when playing
        detail = str(err) if isinstance(err, TtsError) else f"звук не проигрался: {err}"
        print(json.dumps({"error": detail}, ensure_ascii=False) if args.json else f"error: {detail}")
        return 1
    finally:
        if player is not None:
            player.close()
        await tts.aclose()
    result = {
        "text": args.text,
        "provider": speech.provider,
        "first_chunk_s": round(speech.first_chunk_s, 2),
        "audio_s": round(speech.audio_s, 2),
        "latency_s": round(speech.latency_s, 2),
    }
    print(json.dumps(result, ensure_ascii=False) if args.json else result)
    return 0


def show_memory(args: argparse.Namespace) -> int:
    store = MemoryStore(args.memory_db)
    if args.branch is None:
        for b in store.branches():
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.created))
            parent = f" from {b.parent}@{b.parent_seq}" if b.parent else ""
            print(f"{b.id}  {when}  head {b.head}, {b.npcs} npc(s){parent}")
        return 0
    mem = store.load(args.branch, args.npc.upper())
    if mem.summary:
        print(f"summary (up to {mem.summary_seq}): {mem.summary}")
    for e in mem.entries:
        print(f"{e.seq:5d} {e.role:9s} {e.content}")
    return 0


def lines(args: argparse.Namespace) -> int:
    found = load_npc_lines(Path(args.game_dir), args.npc)
    for name, text in found:
        print(f"{name}: {text}")
    print(f"{len(found)} line(s)", file=sys.stderr)
    return 0


def _add_voice_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--asr-url", default=os.environ.get("GOTHIC_ASR_URL", DEFAULT_ASR_URL))
    p.add_argument("--mic-device", help="input device name or index (default: system input)")
    p.add_argument("--voice-file", help="use this 16-bit mono wav instead of the microphone (tests)")
    p.add_argument(
        "--voice-files",
        action="store_true",
        help="the game may name a wav the hero says instead of the microphone (the test bench, shorts/)",
    )


def _add_llm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--llm", choices=BACKENDS, default=os.environ.get("GOTHIC_AI_LLM", "chatgpt"))
    p.add_argument("--model", help="model id (default depends on the backend)")
    p.add_argument("--base-url", help="API base url (openai/local backends)")
    p.add_argument("--reasoning-effort", default="low", help="reasoning effort for Responses models")
    p.add_argument("--game-dir", default=os.environ.get("GOTHIC_DIR", DEFAULT_GAME_DIR))
    p.add_argument("--npc", default="NONE_100_XARDAS", help="default npc for commands and scenarios")


def main() -> None:
    parser = argparse.ArgumentParser(prog="gothic-ai")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="wait for the game and drive npc's")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=47817)
    p.add_argument(
        "--scenario",
        choices=[
            "m0",
            "m1",
            "m2",
            "m3",
            "m4",
            "m5",
            "m5b",
            "m5c",
            "m5e",
            "m6",
            "see",
            "seen",
            "witness",
            "places",
            "graph",
            "lie",
            "wrath",
            "repair",
            "vlie",
            "vcheck",
            "rumor",
            "amends",
            "deal",
            "deal_demo",
            "debt",
            "initiative",
            "gossip",
            "gossip_demo",
            "day",
            "amends_deal",
            "cards",
            "haggle",
            "rescue",
            "rescue_self",
            "tts",
        ],
        help="run a scripted check when the game connects",
    )
    p.add_argument("--exit-after", action="store_true", help="exit when the scenario finishes")
    p.add_argument("--no-console", action="store_true", help="do not read commands from stdin")
    p.add_argument("--memory-db", default=str(DEFAULT_MEMORY_DB), help="SQLite file with npc memories")
    p.add_argument("--no-voice", action="store_true", help="disable push-to-talk")
    p.add_argument("--tts-url", default=os.environ.get("GOTHIC_TTS_URL", DEFAULT_TTS_URL))
    p.add_argument("--no-tts", action="store_true", help="npc's do not speak aloud (subtitles only)")
    p.add_argument(
        "--no-fillers", action="store_true", help='npc\'s say no "Хм..." while the model thinks of an answer'
    )
    p.add_argument("--journal", action="store_true", help="print lines and actions as JOURNAL {json} lines")
    p.add_argument("--trace-dir", default=str(DEFAULT_TRACE_DIR), help="where turn traces go (a file a day)")
    p.add_argument("--no-trace", action="store_true", help="do not write turn traces")
    p.add_argument(
        "--llm-per-npc-min",
        type=float,
        default=6.0,
        help="calls of the conversational model per npc and minute nobody asked for (incidents, summaries)",
    )
    p.add_argument("--llm-total-min", type=float, default=20.0, help="the same for all npc's together")
    p.add_argument(
        "--decider-url",
        default=os.environ.get("GOTHIC_DECIDER_URL"),
        help="gothic-decider service for npc decisions outside conversations (without it the planner is off)",
    )
    p.add_argument("--no-brain", action="store_true", help="do not plan npc decisions outside conversations")
    p.add_argument("--no-director", action="store_true", help="do not repair quests left without an ending")
    p.add_argument(
        "--no-knowledge", action="store_true", help="npc's do not know the world from the scripts (M6)"
    )
    p.add_argument("--story-graph", help="storygraph JSON (default: made from the game's scripts and cached)")
    p.add_argument(
        "--no-hold-threats",
        action="store_true",
        help="threats to free npc's go straight to the game's scripts, not to the behavioral model first",
    )
    p.add_argument(
        "--wrath",
        action="store_true",
        help="npc's take offense and answer insolence with force (docs/xardas-wrath.md; off the main game)",
    )
    p.add_argument(
        "--apply-threats",
        action="store_true",
        help="carry out the behavioral model's fights and flights (default: traced only until trained)",
    )
    p.add_argument(
        "--apply-day",
        action="store_true",
        help="carry out the behavioral model's day plans from the new menu (default: traced in shadow until "
        "measured)",
    )
    _add_voice_args(p)
    _add_llm_args(p)

    p = sub.add_parser("llm-test", help="send one message to the LLM and measure latency")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--name", default="Ксардас", help="npc display name for the persona")
    p.add_argument("--tools", action="store_true", help="offer the npc action tools")
    p.add_argument("--situation", help="situation note sent before the message")
    p.add_argument("--json", action="store_true", help="one JSON object on stdout instead of the stream")
    p.add_argument(
        "--probe-tools",
        action="store_true",
        help="check that a turn goes on after a tool result (reasoning carried, tool_choice none, cache)",
    )
    _add_llm_args(p)

    p = sub.add_parser("replay", help="run a recorded npc turn again, with another model or prompt")
    p.add_argument("file", nargs="?", help="trace file (default: the newest in --trace-dir)")
    p.add_argument("--turn", help="turn id or its prefix (default: the last turn in the file)")
    p.add_argument("--trace-dir", default=str(DEFAULT_TRACE_DIR))
    p.add_argument("--name", default="Ксардас", help="npc display name for --persona-now")
    p.add_argument("--persona-now", action="store_true", help="today's persona instead of the recorded one")
    p.add_argument("--json", action="store_true", help="one JSON object on stdout")
    _add_llm_args(p)

    p = sub.add_parser(
        "places", help="the book of places: group a world dump (SCENARIO=places) and name them"
    )
    p.add_argument("dump", help="world_points.json from SCENARIO=places")
    p.add_argument("--out", default=str(places.BOOK), help="the book to write")
    p.add_argument("--area", action="append", help="only these parts of the world (FARM1, CITY...)")
    p.add_argument("--dry-run", action="store_true", help="only show the groups, no model calls")
    _add_llm_args(p)

    p = sub.add_parser("login", help="sign in to a provider")
    p.add_argument("provider", choices=["chatgpt"])

    p = sub.add_parser("logout", help="forget stored credentials")
    p.add_argument("provider", choices=["chatgpt"])

    p = sub.add_parser("voice-test", help="record from the microphone and print what ASR heard")
    p.add_argument("--seconds", type=float, default=4.0)
    p.add_argument("--json", action="store_true", help="JSON lines: levels, partials, the result")
    _add_voice_args(p)

    sub.add_parser("mics", help="input devices as JSON")

    p = sub.add_parser("speech-test", help="say a line in an npc's voice through the TTS service, aloud")
    p.add_argument("--text", default="Я ждал тебя. Нам нужно поговорить.")
    p.add_argument("--npc", default="NONE_100_XARDAS")
    p.add_argument("--name", default="Ксардас", help="npc display name for the persona")
    p.add_argument("--mood", choices=MOODS, help="say it in this mood (as a line with the model's tag)")
    p.add_argument("--tts-url", default=os.environ.get("GOTHIC_TTS_URL", DEFAULT_TTS_URL))
    p.add_argument("--game-dir", default=os.environ.get("GOTHIC_DIR", DEFAULT_GAME_DIR))
    p.add_argument("--json", action="store_true", help="one JSON object on stdout")

    p = sub.add_parser("memory", help="list save branches, or show what an npc remembers in one")
    p.add_argument("branch", nargs="?")
    p.add_argument("--npc", default="NONE_100_XARDAS")
    p.add_argument("--memory-db", default=str(DEFAULT_MEMORY_DB))

    p = sub.add_parser("lines", help="print an npc's original dialog lines")
    p.add_argument("npc")
    p.add_argument("--game-dir", default=os.environ.get("GOTHIC_DIR", DEFAULT_GAME_DIR))

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpx2", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        match args.command:
            case "serve":
                code = asyncio.run(serve(args))
            case "llm-test":
                code = asyncio.run(llm_test(args))
            case "replay":
                code = asyncio.run(replay(args))
            case "places":
                code = asyncio.run(places_cmd(args))
            case "login":
                code = asyncio.run(login_chatgpt())
            case "logout":
                TokenStore().delete()
                print("ChatGPT credentials removed")
                code = 0
            case "lines":
                code = lines(args)
            case "voice-test":
                code = asyncio.run(voice_test(args))
            case "mics":
                code = mics(args)
            case "speech-test":
                code = asyncio.run(speech_test(args))
            case "memory":
                code = show_memory(args)
            case _:
                code = 2
    except LLMError as err:
        print(f"error: {err}", file=sys.stderr)
        code = 1
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)
