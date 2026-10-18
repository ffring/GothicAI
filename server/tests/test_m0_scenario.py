import asyncio

import pytest

from gothic_ai.game import GameDisconnected, GameError
from gothic_ai.scenarios import basics as scenarios
from tests.fake_game import FakeGame, connected

XARDAS = "NONE_100_XARDAS"


async def test_m0_scenario_drives_npc_and_returns_it_to_routine():
    fake = FakeGame()
    async with connected(fake) as link:
        steps = await asyncio.wait_for(scenarios.m0_smoke(link), 5)

    names = [s.name for s in steps]
    assert names[0] == "go_to_waypoint NW_XARDAS_STAIRS_01"  # farthest nearby waypoint
    assert names[-2] == "go_to_waypoint NW_XARDAS_TOWER_IN1_31"  # back home
    assert names[-1] == "return_to_routine"
    # unknown gestures are rejected by the game, the scenario falls back to the next one
    gestures = [s for s in steps if s.name.startswith("play_ani")]
    assert [g.result.status for g in gestures] == ["rejected", "done"]
    assert all(s.result.status == "done" for s in steps if not s.name.startswith("play_ani"))
    assert fake.attached == set()


async def test_action_args_reach_the_game():
    fake = FakeGame()
    async with connected(fake) as link:
        await link.attach(XARDAS)
        await link.act(XARDAS, "turn_to", npc="PLAYER")

    action = next(m for m in fake.received if m["type"] == "action")
    assert action["npc"] == XARDAS
    assert action["args"] == {"npc": "PLAYER"}


async def test_attach_retries_while_npc_is_in_dialog():
    fake = FakeGame(busy_attaches=2)
    async with connected(fake) as link:
        await scenarios.attach_when_free(link, XARDAS, delay=0.01)

    assert len([m for m in fake.received if m["type"] == "attach"]) == 3
    assert fake.attached == {XARDAS}


async def test_failed_attach_raises_game_error():
    async with connected(FakeGame(busy_attaches=1)) as link:
        with pytest.raises(GameError, match="in dialog"):
            await link.attach(XARDAS)


async def test_pending_action_fails_when_game_disconnects():
    fake = FakeGame()
    async with connected(fake) as link:
        await link.attach(XARDAS)
        fake.handle_action = _never_answer  # type: ignore[method-assign]
        action = asyncio.create_task(link.act(XARDAS, "wait", ms=100))
        await asyncio.sleep(0.05)
        assert fake.ws is not None
        await fake.ws.close()
        with pytest.raises(GameDisconnected):
            await asyncio.wait_for(action, 5)


async def _never_answer(msg):
    return None
