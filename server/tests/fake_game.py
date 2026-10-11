"""Minimal stand-in for OpenGothic's ai_bridge, for tests."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from gothic_ai.game import GameLink, GameServer

KNOWN_ANIMS = {"T_GREETNOV"}
WAYPOINTS = {"NW_XARDAS_TOWER_IN1_31": 0.0, "NW_XARDAS_TOWER_04": 850.0, "NW_XARDAS_STAIRS_01": 1200.0}


class FakeGame:
    def __init__(self, busy_attaches: int = 0) -> None:
        self.attached: set[str] = set()
        self.received: list[dict[str, Any]] = []
        self.busy_attaches = busy_attaches  # first N attach requests fail as "npc is in dialog"
        self.ws: ClientConnection | None = None
        self.said: list[str] = []
        self.statuses: list[str] = []

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
                "hp": 999,
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
            "nearby_npcs": [
                {"id": "PC_THIEF", "name": "Диего", "distance": 650.0, "guild": 0, "dead": False}
            ],
            "nearby_waypoints": [{"name": n, "distance": d} for n, d in WAYPOINTS.items()],
        }

    async def run(self, url: str) -> None:
        async with connect(url) as ws:
            self.ws = ws
            await self.send(type="hello", protocol=3, client="fake", codepage="cp1251")
            await self.send(type="world_loaded", world="NEWWORLD.ZEN")
            async for raw in ws:
                msg = json.loads(raw)
                self.received.append(msg)
                await self.handle(msg)

    async def handle(self, msg: dict[str, Any]) -> None:
        match msg["type"]:
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
                await self.send(type="line_started", npc=msg["npc"], line=msg["line"])
            case "chat_status":
                self.statuses.append(msg["status"])

    async def handle_action(self, msg: dict[str, Any]) -> None:
        npc, action, args = msg["npc"], msg["action"], msg.get("args", {})
        status, detail = "done", None
        if npc not in self.attached:
            status, detail = "rejected", "npc is not attached"
        elif action == "play_ani" and args.get("name") not in KNOWN_ANIMS:
            status, detail = "rejected", "unknown animation"
        elif action == "go_to_waypoint" and args.get("waypoint") not in WAYPOINTS:
            status, detail = "rejected", "unknown waypoint"
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
