"""`gothic-ai serve`: wait for the game, optionally run a scenario, then accept console commands."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shlex
import sys

from . import scenarios
from .game import GameError, GameLink, GameServer
from .protocol import Attached, Detached, GameMessage, Perception

log = logging.getLogger("gothic_ai")

HELP = """commands (npc defaults to the last attached one):
  attach <npc> | detach [npc] | snap [npc] | ping
  goto <waypoint> | come | turn | ani <name> | wait <ms> | stop | routine
  quit"""


def print_event(msg: GameMessage) -> None:
    match msg:
        case Attached():
            log.info("attached %s (%s)", msg.npc, msg.name)
        case Detached():
            log.info("detached %s: %s", msg.npc, msg.reason)
        case Perception():
            log.info("perception %s: %s from %s at %.0fcm", msg.npc, msg.perc, msg.other, msg.distance)


class Console:
    """Line commands from stdin, executed against the current game link."""

    def __init__(self, server: GameServer, npc: str) -> None:
        self.server = server
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
            except (GameError, TimeoutError) as err:
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
            case _:
                print(HELP)


async def serve(args: argparse.Namespace) -> int:
    done = asyncio.Event()
    exit_code = 0

    async def on_link(link: GameLink) -> None:
        nonlocal exit_code
        link.listeners.append(print_event)
        if args.scenario != "m0":
            return
        try:
            steps = await scenarios.m0_smoke(link, args.npc)
            failed = [s for s in steps if s.result.status != "done"]
            log.info("scenario m0: %d steps, %d not done", len(steps), len(failed))
            exit_code = 1 if any(s.result.status in ("failed", "timeout") for s in steps) else 0
        except Exception:
            log.exception("scenario m0 failed")
            exit_code = 1
        if args.exit_after:
            done.set()

    server = GameServer(args.host, args.port, on_link)
    tasks = [asyncio.create_task(server.serve_forever()), asyncio.create_task(done.wait())]
    if not args.no_console:
        tasks.append(asyncio.create_task(Console(server, args.npc).run()))
    finished, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    for task in finished:
        if not task.cancelled() and (err := task.exception()) is not None:
            log.error("%s", err)
            return 1
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(prog="gothic-ai")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve", help="wait for the game and drive npc's")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=47817)
    p.add_argument("--npc", default="NONE_100_XARDAS", help="default npc for commands and scenarios")
    p.add_argument("--scenario", choices=["m0"], help="run a scripted check when the game connects")
    p.add_argument("--exit-after", action="store_true", help="exit when the scenario finishes")
    p.add_argument("--no-console", action="store_true", help="do not read commands from stdin")
    p.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        sys.exit(asyncio.run(serve(args)))
    except KeyboardInterrupt:
        pass
