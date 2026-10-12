"""Minimal stand-in for OpenGothic's ai_bridge, for tests."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from gothic_ai.game import GameLink, GameServer

KNOWN_ANIMS = {
    "T_GREETNOV",
    "T_YES",
    "T_NO",
    "T_DONTKNOW",
    "T_FORGETIT",
}  # not T_COMEOVERHERE: m0 tries it first
WAYPOINTS = {"NW_XARDAS_TOWER_IN1_31": 0.0, "NW_XARDAS_TOWER_04": 850.0, "NW_XARDAS_STAIRS_01": 1200.0}


class FakeGame:
    def __init__(self, busy_attaches: int = 0, branch: str | None = "test-branch") -> None:
        self.attached: set[str] = set()
        self.received: list[dict[str, Any]] = []
        self.busy_attaches = busy_attaches  # first N attach requests fail as "npc is in dialog"
        self.ws: ClientConnection | None = None
        self.said: list[str] = []
        self.voiced: list[bool] = []  # said with audio
        self.statuses: list[str] = []
        self.branch = branch  # reported as the session on connect (None: an old game without sessions)
        self.heads: list[tuple[str, int]] = []  # memory heads the server reported
        self.transcripts: list[str] = []  # final ones
        self.partials: list[str] = []
        self.graph: list[dict[str, Any]] = []  # graph_patch messages; ops on node "BAD" are rejected
        self.story: list[dict[str, Any]] = []  # story_spawn / story_watch / story_effect messages
        self.people_near: list[dict[str, Any]] = []  # what story_nearby answers
        # what story_state answers: globals, npc's ({"state", "name", "distance" from `near`}), dialogs the
        # hero heard, script names
        self.vars: dict[str, int] = {}
        self.npc_states: dict[str, dict[str, Any]] = {}
        self.known: set[str] = set()
        self.symbols: dict[str, str] = {}
        self.in_dialog = False
        self.day = 1
        self.item_names: dict[str, str] = {}
        # story_eval: expressions over vars, npc_states, the hero's items (instances: global -> npc symbol)
        self.hero_items: dict[str, int] = {}
        self.instances: dict[str, str] = {}
        self.nearby = [{"id": "PC_THIEF", "name": "Диего", "distance": 650.0, "guild": 0, "dead": False}]
        self.hp = 999
        self.soft: list[tuple[str, str]] = []  # npc_act's: (npc, action)
        self.hushed: list[dict[str, Any]] = []
        self.heard: list[dict[str, Any]] = []  # final transcripts with whom they were said to

    async def perception(
        self, npc: str, perc: str, other: str, distance: float = 300.0, victim: str | None = None
    ):
        """What a controlled npc noticed (the game sends these for attached npc's only)."""
        extra = {"victim": victim} if victim else {}
        await self.send(type="perception", npc=npc, perc=perc, other=other, distance=distance, **extra)

    async def roster(self, *people: dict[str, Any]) -> None:
        """The people around the player (npc_roster): id, name, distance, in_view, state, moving..."""
        npcs = [{"guild": 0, "in_view": False, "activity": "routine", **p} for p in people]
        await self.send(type="npc_roster", t=1000, hour=8, minute=0, full=True, npcs=npcs)

    async def send(self, **msg: Any) -> None:
        assert self.ws is not None
        await self.ws.send(json.dumps(msg))

    def snapshot(self, npc: str) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "t": 1000,
            "world": {"name": "NEWWORLD.ZEN", "day": 0, "hour": 8, "minute": 0},
            "npc": {
                "id": npc,
                "name": "Ксардас",
                "pos": [1.0, 2.0, 3.0],
                "rot": 90.0,
                "hp": self.hp,
                "hp_max": 999,
                "busy": False,
                "action": None,
                "waypoint": "NW_XARDAS_TOWER_IN1_31",
                "nearest_waypoint": "NW_XARDAS_TOWER_IN1_31",
                "following": False,
                "inventory": [
                    {"item": "ITPO_HEALTH_01", "name": "Эликсир лечения", "count": 3, "equipped": False},
                    {"item": "ITMW_ZWEIHAENDER1", "name": "Двуручник", "count": 1, "equipped": True},
                ],
            },
            "player": {
                "name": "Я",
                "pos": [100.0, 2.0, 3.0],
                "distance": 99.0,
                "guild": 0,
                "hp": 40,
                "hp_max": 40,
                "armor": None,
                "melee": "Палка",
                "ranged": None,
            },
            "nearby_npcs": self.nearby,
            "nearby_waypoints": [{"name": n, "distance": d} for n, d in WAYPOINTS.items()],
        }

    async def run(self, url: str) -> None:
        async with connect(url) as ws:
            self.ws = ws
            await self.send(type="hello", protocol=4, client="fake", codepage="cp1251")
            if self.branch is not None:
                await self.send(type="session", branch=self.branch, reason="new_game")
            await self.send(type="world_loaded", world="NEWWORLD.ZEN")
            async for raw in ws:
                msg = json.loads(raw)
                self.received.append(msg)
                await self.handle(msg)

    async def handle(self, msg: dict[str, Any]) -> None:
        match msg["type"]:
            case "detach":
                self.attached.discard(msg["npc"])
                await self.send(type="result", req=msg["req"], ok=True)
                await self.send(type="detached", npc=msg["npc"], reason="server")
            case "npc_act":
                self.soft.append((msg["npc"], msg["action"]))
                await self.send(type="npc_act_result", id=msg["id"], npc=msg["npc"], status="queued")
            case "hush":
                self.hushed.append(msg)
            case "attach":
                if self.busy_attaches > 0:
                    self.busy_attaches -= 1
                    await self.send(type="result", req=msg["req"], ok=False, error="npc is in dialog")
                    return
                self.attached.add(msg["npc"])
                await self.send(type="result", req=msg["req"], ok=True)
                await self.send(type="attached", npc=msg["npc"], name="Ксардас")
                await self.send(**self.snapshot(msg["npc"]))
            case "snapshot":
                await self.send(**self.snapshot(msg["npc"]))
            case "action":
                await self.handle_action(msg)
            case "say":
                self.said.append(msg["text"])
                self.voiced.append(bool(msg.get("audio") or msg.get("stream")))
                await self.send(type="line_started", npc=msg["npc"], line=msg["line"])
            case "chat_status":
                self.statuses.append(msg["status"])
            case "memory":
                self.heads.append((msg["branch"], msg["seq"]))
            case "transcript":
                (self.transcripts if msg.get("final", True) else self.partials).append(msg["text"])
                if msg.get("final", True):
                    self.heard.append(msg)
            case "story_spawn":
                self.story.append(msg)
                await self.send(type="result", req=msg["req"], ok=True, value=msg.get("count", 1))
            case "story_watch":
                self.story.append(msg)
            case "story_effect":
                self.story.append(msg)
                await self.send(type="result", req=msg["req"], ok=True)
            case "story_nearby":
                await self.send(type="result", req=msg["req"], ok=True, value=self.people_near)
            case "story_state":
                value = {
                    "vars": {v: self.vars[v] for v in msg.get("vars", []) if v in self.vars},
                    "npcs": {
                        n: {
                            k: v
                            for k, v in self.npc_states.get(n, {"state": "absent"}).items()
                            if k != "distance" or msg.get("near")
                        }
                        for n in msg.get("npcs", [])
                    },
                    "knows": {k: k in self.known for k in msg.get("knows", [])},
                    "symbols": {n: self.symbols[n] for n in msg.get("symbols", []) if n in self.symbols},
                    "dialog": self.in_dialog,
                    "cutscene": False,
                    "items": {i: self.item_names[i] for i in msg.get("items", []) if i in self.item_names},
                    "day": self.day,
                    "hour": 8,
                    "minute": 0,
                }
                await self.send(type="result", req=msg["req"], ok=True, value=value)
            case "story_eval":
                self.story.append(msg)
                values = [self.evaluate(e) for e in msg["exprs"]]
                await self.send(type="result", req=msg["req"], ok=True, value=values)
            case "story_tag":
                self.story.append(msg)
                await self.send(type="result", req=msg["req"], ok=True, value=len(msg["npcs"]))
            case "graph_patch":
                self.graph.append(msg)
                if msg.get("req"):
                    bad = [i for i, op in enumerate(msg["ops"]) if op.get("node") == "BAD"]
                    errors = {"error": "; ".join(f"op {i}: no dialog BAD" for i in bad)} if bad else {}
                    await self.send(type="result", req=msg["req"], ok=not bad, **errors)

    def evaluate(self, e: Any) -> Any:
        """A story graph expression, as far as the fake world knows: ints, vars, comparisons, logic, the
        hero's items and who is dead; fields read 0, anything else is null (can't be computed)."""
        ops = {
            "==": lambda a, b: a == b,
            "!=": lambda a, b: a != b,
            "<": lambda a, b: a < b,
            ">": lambda a, b: a > b,
            "<=": lambda a, b: a <= b,
            ">=": lambda a, b: a >= b,
        }
        match e:
            case ["int", v, *_]:
                return v
            case ["var", name]:
                return self.vars.get(name, 0)
            case ["field", *_]:
                return 0
            case ["!", a]:
                v = self.evaluate(a)
                return None if v is None else int(not v)
            case ["&&", *args]:
                vs = [self.evaluate(a) for a in args]
                return None if None in vs else int(all(vs))
            case ["||", *args]:
                vs = [self.evaluate(a) for a in args]
                return None if None in vs else int(any(vs))
            case [op, a, b] if op in ops:
                x, y = self.evaluate(a), self.evaluate(b)
                return None if x is None or y is None else int(ops[op](x, y))
            case ["call", "NPC_HASITEMS", ["inst", "OTHER" | "HERO"], ["sym", item]]:
                return self.hero_items.get(item, 0)
            case ["call", "NPC_ISDEAD", ["inst", name]]:
                npc = self.instances.get(name, name)
                return int(self.npc_states.get(npc, {}).get("state") == "dead")
        return None

    async def handle_action(self, msg: dict[str, Any]) -> None:
        npc, action, args = msg["npc"], msg["action"], msg.get("args", {})
        status, detail = "done", None
        if npc not in self.attached:
            status, detail = "rejected", "npc is not attached"
        elif action == "play_ani" and args.get("name") not in KNOWN_ANIMS:
            status, detail = "rejected", "unknown animation"
        elif action == "go_to_waypoint" and args.get("waypoint") not in WAYPOINTS:
            status, detail = "rejected", "unknown waypoint"
        elif action == "go_to_npc" and args.get("npc") in self.npc_states:
            # it gets there: the target is as far from it as it was to stop
            self.npc_states[args["npc"]]["distance"] = args.get("radius", 400)
        await self.send(
            type="action_result",
            id=msg["id"],
            npc=npc,
            status=status,
            **({"detail": detail} if detail else {}),
        )
        if action == "return_to_routine" and status == "done":
            self.attached.discard(npc)
            await self.send(type="detached", npc=npc, reason="routine")


@asynccontextmanager
async def connected(fake: FakeGame):
    """Server on a free port with the fake game connected; yields the game link."""
    server = GameServer(port=0)
    serving = asyncio.create_task(server.serve_forever())
    await server.started.wait()
    game = asyncio.create_task(fake.run(f"ws://127.0.0.1:{server.port}"))
    try:
        link: GameLink = await asyncio.wait_for(server.wait_link(), 5)
        await link.wait_world(5)
        yield link
    finally:
        game.cancel()
        serving.cancel()
        await asyncio.gather(game, serving, return_exceptions=True)
