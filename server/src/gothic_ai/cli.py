"""`gothic-ai`: AI server for the game, ChatGPT sign-in and diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shlex
import sys
import time
from pathlib import Path

from . import npc_tools, scenarios
from .dialogue import DialogueManager
from .game import GameError, GameLink, GameServer
from .gamedata.ou import load_npc_lines
from .llm import BACKENDS, LLMBackend, LLMError, Message, TextDelta, ToolCall, make_backend
from .llm.siwc import AuthError, TokenStore, login
from .memory import DEFAULT_PATH as DEFAULT_MEMORY_DB
from .memory import MemoryStore
from .persona import PersonaProvider
from .protocol import (
    Attached,
    ChatClose,
    ChatOpen,
    Detached,
    GameMessage,
    Perception,
    PlayerText,
    Saved,
    Session,
)
from .voice import DEFAULT_ASR_URL, AsrClient, AudioSource, Microphone, VoiceError, VoiceInput, WavSource

log = logging.getLogger("gothic_ai")

DEFAULT_GAME_DIR = "/Volumes/shared/projects/Gothic II"

HELP = """commands (npc defaults to the last attached one):
  attach <npc> | detach [npc] | snap [npc] | ping
  goto <waypoint> | come | turn | ani <name> | wait <ms> | stop | routine
  talk <text>       speak to the npc as the player (opens a conversation)
  additem <item> [n] put an item into the npc's inventory (game needs -devmode)
  memory [npc]      the current save branch and what the npc remembers
  save <slot> | load <slot>   save_slot_<slot>.sav (game needs -devmode)
  quit"""


def print_event(msg: GameMessage) -> None:
    match msg:
        case Attached():
            log.info("attached %s (%s)", msg.npc, msg.name)
        case Detached():
            log.info("detached %s: %s", msg.npc, msg.reason)
        case Perception():
            log.debug("perception %s: %s from %s at %.0fcm", msg.npc, msg.perc, msg.other, msg.distance)
        case ChatOpen():
            log.info("chat opened with %s", msg.npc)
        case PlayerText():
            log.info("player -> %s: %s", msg.npc, msg.text)
        case ChatClose():
            log.info("chat closed with %s", msg.npc)
        case Saved():
            log.info("game saved at memory %s@%d", msg.branch, msg.seq)
        case Session():
            pass  # logged by the dialogue manager


class ServerState:
    def __init__(self) -> None:
        self.dialogue: DialogueManager | None = None
        self.voice: VoiceInput | None = None


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
            case "talk" if rest and self.state.dialogue is not None:
                snap = await link.snapshot(npc)
                self.state.dialogue.open(npc, snap.npc.name)
                await self.state.dialogue.say_as_player(npc, " ".join(rest))
            case _:
                print(HELP)


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
    source = None if args.no_voice else _audio_source(args)
    log.info("LLM backend: %s (%s)", llm.name, getattr(llm, "model", "-"))
    log.info("memory: %s", args.memory_db)
    log.info("voice: %s", "off" if asr is None else f"{args.voice_file or 'microphone'} -> {args.asr_url}")

    async def on_link(link: GameLink) -> None:
        nonlocal exit_code
        link.listeners.append(print_event)
        state.dialogue = DialogueManager(link, llm, personas, memory=memory, greet=not args.no_greet)
        if asr is not None and source is not None:
            state.voice = VoiceInput(link, state.dialogue, source, asr)
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
    memory.close()
    for task in finished:
        if not task.cancelled() and (err := task.exception()) is not None:
            log.error("%s", err)
            return 1
    return exit_code


async def llm_test(args: argparse.Namespace) -> int:
    """One request, prints the reply and latency: compare backends and models."""
    llm = _backend(args)
    personas = PersonaProvider(Path(args.game_dir) if args.game_dir else None)
    persona = personas.get(args.npc, args.name)
    messages = [Message("user", args.text)]
    if args.situation:
        messages.insert(0, Message("developer", args.situation))
    tools = npc_tools.TOOLS if args.tools else None
    started = time.monotonic()
    first = None
    try:
        async for event in llm.stream(instructions=persona.instructions, messages=messages, tools=tools):
            if isinstance(event, TextDelta):
                if first is None:
                    first = time.monotonic() - started
                print(event.text, end="", flush=True)
            elif isinstance(event, ToolCall):
                at = time.monotonic() - started
                print(f"\n  -> {event.name}({event.arguments}) at {at:.2f}s", flush=True)
    except LLMError as err:
        print(f"\nerror: {err}", file=sys.stderr)
        return 1
    finally:
        await llm.aclose()
    total = time.monotonic() - started
    print(f"\n\n[{llm.name}] first token {first or 0:.2f}s, total {total:.2f}s", file=sys.stderr)
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
    prints partial text as it comes and the final one with the latency after the end of speech."""
    source = _audio_source(args)
    asr = AsrClient(args.asr_url)
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    seconds = getattr(source, "duration", args.seconds)
    try:
        session = await asr.open(source.rate, on_partial=lambda text: print(f"  … {text}", file=sys.stderr))
        source.start(lambda data: loop.call_soon_threadsafe(queue.put_nowait, data))
        if not args.voice_file:
            print(f"говорите {seconds:g} с...", file=sys.stderr, flush=True)
        loop.call_later(seconds + 0.1, lambda: (source.stop(), queue.put_nowait(None)))
        size, heard = 0, False
        while (chunk := await queue.get()) is not None:
            size += len(chunk)
            heard = heard or chunk.count(0) != len(chunk)
            await session.feed(chunk)
        if size and not heard:
            await session.cancel()
            print("микрофон записал тишину: проверьте доступ к микрофону", file=sys.stderr)
            return 1
        result = await session.finish()
    except VoiceError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    finally:
        await asr.aclose()
    print(result.text or "(ничего не распознано)")
    print(
        f"[{result.provider}] {size / 2 / source.rate:.1f}s audio, {result.latency_s:.2f}s after it ended",
        file=sys.stderr,
    )
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
        "--scenario", choices=["m0", "m1", "m2", "m3"], help="run a scripted check when the game connects"
    )
    p.add_argument("--exit-after", action="store_true", help="exit when the scenario finishes")
    p.add_argument("--no-console", action="store_true", help="do not read commands from stdin")
    p.add_argument("--no-greet", action="store_true", help="npc does not greet when the chat opens")
    p.add_argument("--memory-db", default=str(DEFAULT_MEMORY_DB), help="SQLite file with npc memories")
    p.add_argument("--no-voice", action="store_true", help="disable push-to-talk")
    _add_voice_args(p)
    _add_llm_args(p)

    p = sub.add_parser("llm-test", help="send one message to the LLM and measure latency")
    p.add_argument("text")
    p.add_argument("--name", default="Ксардас", help="npc display name for the persona")
    p.add_argument("--tools", action="store_true", help="offer the npc action tools")
    p.add_argument("--situation", help="situation note sent before the message")
    _add_llm_args(p)

    p = sub.add_parser("login", help="sign in to a provider")
    p.add_argument("provider", choices=["chatgpt"])

    p = sub.add_parser("logout", help="forget stored credentials")
    p.add_argument("provider", choices=["chatgpt"])

    p = sub.add_parser("voice-test", help="record from the microphone and print what ASR heard")
    p.add_argument("--seconds", type=float, default=4.0)
    _add_voice_args(p)

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
