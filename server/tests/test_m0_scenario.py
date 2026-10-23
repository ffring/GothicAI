import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gothic_ai.game import GameDisconnected, GameError
from gothic_ai.protocol import LineStarted, SvmLine
from gothic_ai.scenarios import basics as scenarios
from gothic_ai.trace import TraceWriter
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


async def test_svm_scenario_waits_for_voice_trace_and_keeps_phrase_and_voice_times(
    monkeypatch, tmp_path
):
    trace = TraceWriter(tmp_path / "traces")
    trace.directory.mkdir()
    trace.path().touch()
    game_log = tmp_path / "game.log"
    game_log.touch()
    dialogue = SimpleNamespace(svm_lines=True, quick=object(), _svm_available=True,
                               trace=trace, _lines={})
    link = SimpleNamespace(listeners=[], wait_world=AsyncMock(), debug=AsyncMock())
    pending = None
    trace_waits = []

    async def act(who, action, **kwargs):
        nonlocal pending
        if action == "say_svm":
            number = len(dialogue._lines)
            line, answer = f"line-{number}", f"Убирайся {number}!"
            event = SvmLine(type="svm_line", id=f"svm-{number}", npc=who, svm=kwargs["svm"],
                            text="Уходи!", target="PC_HERO", overlay=kwargs["overlay"])
            dialogue._lines[line] = (answer,)
            for listener in link.listeners:
                listener(event)
            pending = line, {"type": "svm_line", "id": event.id, "outcome": "said",
                             "answer": answer, "elapsed_ms": 4750.0,
                             "first_phrase_ms": 125.0, "voice_ms": 4500.0}
        return SimpleNamespace(status="done")

    async def until(check, timeout):
        nonlocal pending
        if not check():
            # The trace completes only with the voice, 4.5 s after the SVM arrived.
            trace_waits.append(timeout)
            if timeout < 4.5:
                return False
            line, row = pending
            pending = None
            game_log.write_text(f"[ai] voice {line} starts\n", encoding="utf-8")
            for listener in link.listeners:
                listener(LineStarted(type="line_started", npc=XARDAS, line=line, voiced=True))
            trace.write(row)
        return bool(check())

    link.act = act
    monkeypatch.setattr(scenarios, "attach_when_free", AsyncMock())
    monkeypatch.setattr(scenarios, "_until", until)
    monkeypatch.setattr(scenarios.asyncio, "sleep", AsyncMock())
    results = await scenarios.svm_lines(link, dialogue, log_dir=tmp_path)

    assert trace_waits == [8] * 10
    assert all(result.startswith("done") for result in results.values()), results
    assert "first_phrase_ms=125.0, voice_ms=4500.0" in results["gemma_01"]
    assert "first_phrase_ms=4750.0" not in results["gemma_01"]


async def _never_answer(msg):
    return None
