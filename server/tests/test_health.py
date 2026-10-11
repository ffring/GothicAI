import asyncio

import httpx

from gothic_ai.game import GameServer
from tests.fake_game import FakeGame


async def _health(port: int) -> dict:
    async with httpx.AsyncClient() as http:
        r = await http.get(f"http://127.0.0.1:{port}/health")
    assert r.headers["content-type"] == "application/json"
    return r.json()


async def test_health_tells_whether_the_game_is_connected():
    server = GameServer(port=0)
    serving = asyncio.create_task(server.serve_forever())
    await server.started.wait()
    game = None
    try:
        assert await _health(server.port) == {"ok": True, "service": "gothic-ai", "game": False}
        game = asyncio.create_task(FakeGame().run(f"ws://127.0.0.1:{server.port}"))
        await asyncio.wait_for(server.wait_link(), 5)
        assert (await _health(server.port))["game"] is True
    finally:
        for task in (game, serving):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in (game, serving) if t is not None), return_exceptions=True)
