"""The native-AI control traverses waves and loads without constructing a pilot."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from gothic_ai.bodypilot import BodyFight, PolicyClient
from gothic_ai.protocol import LineStarted, Result, Saved, Session
from gothic_ai.scenarios import neural_soak as soak
from gothic_ai.scenarios.neural_soak_metrics import SoakMetrics


class NativeWorld:
    """A live native fight, one hero death, and saves that restore a new world."""

    def __init__(self):
        self.now = 0.0
        self.hello = SimpleNamespace(protocol=45)
        self.raw_body_listeners = []
        self.listeners = []
        self.body_latest = {}
        self.body_session_epoch = 1
        self.body_generation = 1
        self.branch = "initial"
        self.saves = {}
        self.save_slots = []
        self.loads = []
        self.wave_sizes = []
        self.wave_started = None
        self.dead = False
        self.death_used = False
        self.watched = []
        self.watches = []
        self.trigger_counts = []
        self.heals = 0
        self.conversations = 0
        self.weapons = []

    def emit(self, message):
        for listener in tuple(self.listeners):
            listener(message)

    def reply(self):
        self.emit(Result(type="result", req="fake", ok=True))

    async def sleep(self, seconds):
        # Sampling is a concurrent observer, not an extra world clock. Only
        # waits in the scenario advance this deterministic native world.
        if asyncio.current_task().get_coro().__name__ != "sample_loop":
            self.now += seconds
            if self.watched:
                packet = {"observations": [{
                    "session_epoch": self.body_session_epoch,
                    "world_epoch": self.body_generation,
                    "entity_id": (0 if npc == "PLAYER" else soak.BANDITS.index(npc) + 1),
                    "control_generation": 0,
                    "t_game_ms": int(self.now * 1000),
                } for npc in self.watched]}
                for listener in tuple(self.raw_body_listeners):
                    listener(packet)
        await asyncio.sleep(0)

    async def wait_world(self):
        pass

    async def own_npc(self, *args, **kwargs):
        pass

    async def story_npc(self, spec):
        pass

    async def story_effect(self, *args):
        self.reply()

    async def snapshot(self, *args, **kwargs):
        return SimpleNamespace(npc=SimpleNamespace(hp=900, hp_max=900, pos=(0, 0, 0)))

    async def body_watch(self, npcs, **kwargs):
        self.watched = list(npcs)
        self.watches.append(self.watched)
        self.reply()

    async def story_eval(self, npc, expressions):
        self.reply()
        if npc in soak.BANDITS:
            return [soak.BANDITS.index(npc) + 100, True, True]
        if expressions[0] == ["call", "NPC_ISDEAD", ["inst", soak.COMPANION]]:
            return [False]
        assert expressions[0] == ["call", "NPC_ISDEAD", ["inst", "HERO"]]
        if (not self.death_used and self.wave_started is not None
                and self.now - self.wave_started >= 8):
            self.dead = self.death_used = True
        # Native attacks stay live until a load or the escape deadline. No
        # lease, policy reset or body-control method exists on this fake link.
        return [self.dead, *[True for _ in expressions[1:]]]

    async def patch_graph(self, operations):
        for operation in operations:
            if operation["steps"] is None:
                continue
            self.trigger_counts.append({"id": operation["id"], "count": 1})
            attacks = [step["call"] for step in operation["steps"]
                       if step["call"][:2] == ["fn", "B_ATTACK"]]
            if attacks:
                self.wave_sizes.append(len(attacks))
                self.wave_started = self.now
        self.reply()

    async def triggers(self):
        return self.trigger_counts

    async def debug(self, command, *args, **kwargs):
        if command == "player_hp":
            self.heals += 1
        elif command == "load":
            saved = self.saves[args[0]]
            self.loads.append(saved)
            self.body_generation += 1
            self.branch = f"loaded-{self.body_generation}"
            self.dead = False
            self.wave_started = None
            self.emit(Session(type="session", branch=self.branch, parent=saved.branch,
                              parent_seq=saved.seq, reason="load"))
        elif command == "type":
            self.conversations += 1
            self.emit(LineStarted(type="line_started", npc=soak.TALKER,
                                  line=f"answer-{self.conversations}"))
        self.reply()

    async def attach(self, npc):
        pass

    async def detach(self, npc):
        pass

    async def save(self, slot):
        saved = Saved(type="saved", branch=self.branch, seq=len(self.save_slots) + 1)
        self.saves[slot] = saved
        self.save_slots.append(slot)
        return saved


class ReadyBodies:
    def __init__(self, link):
        pass

    async def act(self, *args, **kwargs):
        return SimpleNamespace(status="arrived")


@pytest.mark.parametrize("duration", [90, 180])
async def test_control_runs_native_waves_death_and_scheduled_loads(
    tmp_path, monkeypatch, duration,
):
    world = NativeWorld()
    dialogue = SimpleNamespace(llm=SimpleNamespace(name="fake"), speaking=lambda npc: False)
    original_llm = dialogue.llm
    original_finish = BodyFight._finish

    def forbidden(*args, **kwargs):
        pytest.fail("the native-AI control entered the policy/capture path")

    async def weapon(link, kind, **kwargs):
        link.weapons.append(kind)

    monkeypatch.setenv("GOTHIC_SOAK_CONTROL", "1")
    monkeypatch.setenv("GOTHIC_SOAK_SECONDS", str(duration))
    monkeypatch.setattr(soak, "time", SimpleNamespace(monotonic=lambda: world.now))
    monkeypatch.setattr(soak, "asyncio", SimpleNamespace(
        sleep=world.sleep, create_task=asyncio.create_task, CancelledError=asyncio.CancelledError,
    ))
    monkeypatch.setattr(soak, "Bodies", ReadyBodies)
    monkeypatch.setattr(soak, "_m5b_player_weapon", weapon)
    monkeypatch.setattr(soak, "_save_or_fail", lambda link, slot: link.save(slot))
    monkeypatch.setattr(soak, "CaptureTracker", forbidden)
    monkeypatch.setattr(soak, "_policy_resets", forbidden)
    monkeypatch.setattr(PolicyClient, "call", forbidden)
    monkeypatch.setattr("gothic_ai.scenarios.neural_soak_metrics.rss_bytes", lambda pid: 1000)
    monkeypatch.setattr(soak, "SoakMetrics", lambda link, pilot, log_dir: SoakMetrics(
        link, pilot, log_dir, clock=lambda: world.now,
    ))
    (tmp_path / "game.pid").write_text("123")
    (tmp_path / "server.log").write_text("INFO native control ready\n")

    checks = await soak.neural_soak_scenario(world, dialogue, None, log_dir=tmp_path)

    assert checks and all(value.startswith("done") for value in checks.values())
    assert not {"capture_rate", "accepted_commands", "release_clean",
                "fault_fallbacks", "service_latency"} & checks.keys()
    result = json.loads((tmp_path / "neural_soak_result.json").read_text())
    assert result["mode"] == "control"
    assert result["status"] == "done"
    assert "fights" not in result
    assert "policy_decisions" not in result["metrics"]
    loads = result["extra"]["loads"]
    assert [entry["cause"] for entry in loads] == [
        "hero_death", "scheduled_10m", "scheduled_20m",
    ]
    assert all(entry["ok"] for entry in loads)
    assert all(entry["mid_fight"] for entry in loads)
    assert all("reacquired" not in entry and "leases" not in entry for entry in loads)
    assert result["extra"]["scheduled_loads"] == [10, 20]
    assert len(world.loads) == 3
    assert world.save_slots.count(8) == 2
    assert world.wave_sizes == [3, 4, 3, 4]
    assert len(result["extra"]["waves"]) == 4
    assert result["extra"]["waves"][-1]["end"] == "escape_duration"
    assert len({entry["place"] for entry in result["extra"]["waves"]}) == 2
    assert world.conversations == len(world.wave_sizes)
    assert world.heals >= len(world.wave_sizes) * 2
    assert world.weapons.count("melee") == len(world.wave_sizes)
    assert world.weapons.count("none") == world.conversations
    assert ["PLAYER"] in world.watches and list(soak.BANDITS) in world.watches
    assert dialogue.llm is original_llm
    assert PolicyClient.call is forbidden
    assert BodyFight._finish is original_finish
    assert not world.raw_body_listeners and not world.listeners
