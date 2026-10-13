import asyncio
import json

from gothic_ai.bodies import KEEP, Bodies, BodyOutcome
from gothic_ai.protocol import parse_game_message
from tests.fake_game import FakeGame, connected
from tests.test_dialogue import _until

LOBART, HILDA = "BAU_950_LOBART", "BAU_951_HILDA"


def idle(npc: str, act: str | None = None, reason: str | None = None, detail: str | None = None):
    msg = {"type": "npc_idle", "npc": npc, "act": act, "reason": reason, "detail": detail}
    return parse_game_message(json.dumps(msg))


async def test_done_rejected_timeout_lost():
    fake = FakeGame()
    async with connected(fake) as link:
        bodies = Bodies(link)
        fake.auto_idle = True
        done = await bodies.act(LOBART, "go_to_npc", npc="PLAYER")
        assert done == BodyOutcome(fake.acts[-1]["id"], LOBART, "done")
        assert fake.acts[-1]["args"] == {"npc": "PLAYER"}
        fake.idle_reason = "gone"
        assert (await bodies.act(LOBART, "turn_to")).status == "gone"
        fake.idle_reason = "timeout"  # the bridge gave it up after its 30 s
        assert (await bodies.act(LOBART, "turn_to")).status == "timeout"

        fake.busy_npcs.add(LOBART)
        rejected = await bodies.act(LOBART, "go_to_npc")
        assert (rejected.status, rejected.detail) == ("rejected", "npc is busy (attached or in a dialog)")
        fake.busy_npcs.clear()

        fake.auto_idle = False  # it never says it is done
        late = await bodies.act(LOBART, "go_to_npc", timeout=0.1)
        assert (late.act, late.status, late.detail) == (fake.acts[-1]["id"], "timeout", "no npc_idle")

        walking = asyncio.create_task(bodies.act(LOBART, "go_to_npc"))
        await _until(lambda: bool(bodies._waiting))
        await fake.send(type="loading")  # the npc's are released: nobody answers that act
        lost = await asyncio.wait_for(walking, 2)
        assert (lost.act, lost.npc, lost.status) == (fake.acts[-1]["id"], LOBART, "lost")
        assert not bodies._waiting

        assert fake.ws is not None
        await fake.ws.close()
        await asyncio.wait_for(link.closed.wait(), 2)
        assert await bodies.act(LOBART, "go_to_npc") == BodyOutcome("", LOBART, "lost", "game disconnected")
        assert fake.soft[-1] == (LOBART, "go_to_npc") and len(fake.acts) == 6


async def test_answer_before_waiting_is_kept(monkeypatch):
    fake = FakeGame()
    fake.auto_idle = True
    async with connected(fake) as link:
        bodies = Bodies(link)
        answered = asyncio.Event()
        link.listeners.append(lambda m: answered.set() if m.type == "npc_idle" else None)
        send = link.npc_act

        async def slow(npc, action, /, **args):  # the game answers before anyone waits
            act = await send(npc, action, **args)
            await answered.wait()
            return act

        monkeypatch.setattr(link, "npc_act", slow)
        outcome = await bodies.act(LOBART, "go_to_npc", timeout=0.01)
        assert outcome == BodyOutcome(fake.acts[-1]["id"], LOBART, "done")
        assert not bodies._ended  # taken once

    # the planner's own acts end here too, nobody waits for them: only the latest are kept
    for i in range(KEEP + 10):
        bodies._on_message(idle("A", f"x{i}", "done"))
    bodies._on_message(idle("A"))  # an old bridge: no act id, nothing to end
    assert len(bodies._ended) == KEEP and "x10" in bodies._ended  # the one without an id pushed none out
    assert "x9" not in bodies._ended and bodies._ended[f"x{KEEP + 9}"].status == "done"
    bodies._on_message(idle("A", "odd", "stuck"))  # a reason it doesn't know still ends the act
    assert bodies._ended["odd"] == BodyOutcome("odd", "A", "done", "stuck")


async def test_arrived_failed_and_busy(monkeypatch):
    fake = FakeGame()  # it never says it is done by itself: the test answers
    async with connected(fake) as link:
        bodies = Bodies(link)
        assert not bodies.busy(LOBART)
        here = {"state": "ZS_STAND_ARMSCROSSED", "waypoint": "NW_FARM1_PATH_01", "minutes": 60}
        walking = asyncio.create_task(bodies.act(LOBART, "start_activity", arrive=True, **here))
        await _until(lambda: bool(bodies._waiting) and len(fake.acts) == 1)  # the fake has it too
        assert bodies.busy(LOBART) and not bodies.busy(HILDA)
        await fake.send(type="npc_idle", npc=LOBART, act=fake.acts[-1]["id"], reason="arrived")
        assert await asyncio.wait_for(walking, 2) == BodyOutcome(fake.acts[-1]["id"], LOBART, "arrived")
        assert not bodies.busy(LOBART)

        giving = asyncio.create_task(bodies.act(LOBART, "give", npc=HILDA, item="ITFO_BREAD", count=2))
        await _until(lambda: bool(bodies._waiting) and len(fake.acts) == 2)
        assert fake.acts[-1]["args"] == {"npc": HILDA, "item": "ITFO_BREAD", "count": 2}
        why = {"reason": "failed", "detail": "could not reach the target"}
        await fake.send(type="npc_idle", npc=LOBART, act=fake.acts[-1]["id"], **why)
        failed = await asyncio.wait_for(giving, 2)
        assert (failed.status, failed.detail) == ("failed", "could not reach the target")
        assert not bodies.busy(LOBART)

        # busy from the send on (before the game queued it), and while any of two acts on it goes on
        sent, go = asyncio.Event(), asyncio.Event()
        send = link.npc_act

        async def slow(npc, action, /, **args):
            sent.set()
            await go.wait()
            return await send(npc, action, **args)

        monkeypatch.setattr(link, "npc_act", slow)
        first = asyncio.create_task(bodies.act(LOBART, "turn_to", timeout=5))
        await sent.wait()
        assert bodies.busy(LOBART) and not bodies._waiting
        go.set()
        await _until(lambda: bool(bodies._waiting) and fake.acts[-1]["action"] == "turn_to")
        monkeypatch.setattr(link, "npc_act", send)
        second = asyncio.create_task(bodies.act(LOBART, "look_at", timeout=5))
        replaced = await asyncio.wait_for(first, 2)  # the newer act took the tracking: the older one ends
        look = next(a["id"] for a in fake.acts if a["action"] == "look_at")
        assert (replaced.status, replaced.detail) == ("gone", f"replaced by {look}")
        assert bodies.busy(LOBART)
        await fake.send(type="npc_idle", npc=LOBART, act=look, reason="done")
        assert (await asyncio.wait_for(second, 2)).status == "done"
        assert not bodies.busy(LOBART) and not bodies._acting

    # what the bridge says of an act nobody waits for is kept with its detail
    bodies._on_message(idle(LOBART, "g1", "failed", "npc has no item ITFO_BREAD"))
    bodies._on_message(idle(LOBART, "a1", "arrived"))
    assert bodies._ended["g1"] == BodyOutcome("g1", LOBART, "failed", "npc has no item ITFO_BREAD")
    assert bodies._ended["a1"] == BodyOutcome("a1", LOBART, "arrived")


async def test_a_newer_act_on_the_npc_ends_the_older_one():
    """The bridge tracks one act per npc: a newer one queued by anyone (the planner's applier, a reflex) ends
    the wait for ours at once, as gone. A rejected one, or another npc's, leaves it be."""
    fake = FakeGame()
    async with connected(fake) as link:
        bodies = Bodies(link)
        here = {"state": "ZS_STAND_ARMSCROSSED", "waypoint": "NW_FARM1_PATH_01", "minutes": 60}
        walking = asyncio.create_task(bodies.act(LOBART, "start_activity", arrive=True, **here))
        await _until(lambda: bool(bodies._waiting) and bodies._queued.get(LOBART) == fake.acts[0]["id"])
        walk = fake.acts[0]["id"]
        fake.busy_npcs.add(LOBART)
        refused = await link.npc_act(LOBART, "fight", npc="PLAYER")
        await _until(lambda: refused in bodies._ended)
        fake.busy_npcs.clear()
        flee = await link.npc_act(HILDA, "flee", npc="PLAYER")
        await _until(lambda: bodies._queued.get(HILDA) == flee)
        assert not walking.done() and bodies._queued[LOBART] == walk

        fight = await link.npc_act(LOBART, "fight", npc="PLAYER")  # the planner's, past Bodies
        assert await asyncio.wait_for(walking, 2) == BodyOutcome(walk, LOBART, "gone", f"replaced by {fight}")
        assert not bodies.busy(LOBART) and bodies._queued[LOBART] == fight
        await fake.send(type="npc_idle", npc=HILDA, act=flee, reason="done")
        await _until(lambda: HILDA not in bodies._queued)  # ended: no act of hers is tracked
        await fake.send(type="loading")
        await _until(lambda: not bodies._queued)  # the npc's are released
