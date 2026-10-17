"""Adapter identity/freshness tests; these do not launch or emulate an engine."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_arena_env import ArenaInfrastructureError, EpisodeEnv, ScriptedProvider


class FakeTransport:
    """Protocol responses only; no combat, animations, geometry or damage."""
    def __init__(self):
        self.requests = []
        self.generation = 0
        self.leases = {1: 0, 2: 0}
        self.sequence = 0
        self.rows = []
        self.arena = {}
        self.fail_reset = False
        self.fail_heartbeat = False
        self.next_step_arena = None

    def episode_log(self, output):
        self.output = output

    def sample(self):
        return {}

    def row(self, entity):
        return {"session_epoch": 7, "world_epoch": self.generation, "entity_id": entity,
                "control_generation": self.leases[entity], "observation_seq": self.sequence,
                "t_game_ms": self.sequence*50, "can_decide": True, "action_mask_valid": True,
                "action_mask": [True]*12, "target": {"entity_id": 3-entity, "current_valid": True,
                                                          "attack_anim": False, "prehit": False},
                "goal": {"version": 1}}

    def call(self, request):
        self.requests.append(deepcopy(request))
        command = request["cmd"]
        if command == "reset":
            if self.fail_reset:
                raise ArenaInfrastructureError("fixture transport reset failure")
            self.generation += 1
            self.leases = {1: 0, 2: 0}
            self.sequence = 1
            self.arena = {"session_epoch": 7, "world_epoch": self.generation, "runner_generation": self.generation,
                          "episode_id": self.generation, "ready": True, "started": False,
                          "primary_entity_id": 1, "opponent_entity_id": 2,
                          "actors": [{"entity_id": 1}, {"entity_id": 2}],
                          "terminated": False, "truncated": False, "infrastructure": False}
            self.rows = [self.row(1), self.row(2)]
            return {"arena": deepcopy(self.arena)}
        if command in ("arena_status", "arena_start"):
            if command == "arena_start":
                self.arena["started"] = True
            return {"arena": deepcopy(self.arena)}
        if command == "arena_step":
            if self.next_step_arena is not None:
                self.arena.update(self.next_step_arena)
                self.next_step_arena = None
                self.leases[1] = 0
            self.sequence += 1
            self.rows = [self.row(1), self.row(2)]
            return {"arena": deepcopy(self.arena), "advanced_substeps": request["substeps"]}
        if command == "body_observations":
            rows, self.rows = self.rows, []
            return {"observations": rows}
        if command == "body_control_status":
            return {"actors": [{"entity_id": entity, "session_epoch": 7, "world_epoch": self.generation,
                                "control_generation": self.leases[entity], "target_entity_id": 3-entity}
                               for entity in (1, 2)]}
        if command == "body_acquire":
            entity = request["entity_id"]
            self.leases[entity] = self.generation*10+entity
            return {"accepted": True, "entity_id": entity, "session_epoch": 7,
                    "world_epoch": self.generation, "control_generation": self.leases[entity]}
        if command == "body_heartbeat":
            return {"accepted": not self.fail_heartbeat, "reason": "not_owned" if self.fail_heartbeat else "heartbeat"}
        if command == "body_action":
            return {"accepted": True, "execution_id": request["command_id"]}
        if command == "body_events":
            return {"events": []}
        raise AssertionError(command)


def reset(env, output, provider="scripted"):
    return env.reset(output=output, task="reset_stress", seed=17, side=0,
                     distance_cm=150, timeout_ms=10000, provider=provider)


def test_reset_invalidates_recurrent_memory_commands_and_old_lease(tmp_path):
    transport = FakeTransport()
    env = EpisodeEnv(transport, Path("body-arena.bench"))
    reset(env, tmp_path / "first")
    env.start()
    env.act(1, 6)
    token, lease = env.recurrent_token, deepcopy(env.leases[1])
    env.processed_sequences[99] = 999999
    env.latest[99] = {"old": True}
    reset(env, tmp_path / "second")
    assert env.recurrent_token != token
    assert env.identity.world_epoch != lease["world_epoch"]
    assert env.leases[1] != lease
    assert env.command_ids == {1: 0, 2: 0}
    assert env.decisions == []
    assert env.events == []
    assert env.latest == {}
    assert env.processed_sequences == {1: 1, 2: 1}
    assert not env.eligible(1)


def test_failed_reset_clears_memory_before_transport_failure(tmp_path):
    transport = FakeTransport()
    env = EpisodeEnv(transport, Path("body-arena.bench"))
    reset(env, tmp_path / "first")
    env.start()
    env.act(1, 6)
    transport.fail_reset = True
    with pytest.raises(ArenaInfrastructureError, match="reset failure"):
        reset(env, tmp_path / "failed")
    assert env.identity is None and env.recurrent_token is None
    assert not env.latest and not env.processed_sequences and not env.leases and not env.command_ids
    assert not env.decisions and not env.events


def test_start_barrier_defaults_to_two_substeps_and_accepts_realtime_one(tmp_path):
    for name, substeps in (("default", None), ("realtime", 1)):
        transport = FakeTransport()
        env = EpisodeEnv(transport, Path("body-arena.bench"))
        reset(env, tmp_path / name)
        env.start() if substeps is None else env.start(substeps)
        assert [request["substeps"] for request in transport.requests if request["cmd"] == "arena_step"] == [substeps or 2]
        assert env.eligible(1)


def test_post_acquire_freshness_and_single_sample_decision(tmp_path):
    transport = FakeTransport()
    env = EpisodeEnv(transport, Path("body-arena.bench"))
    reset(env, tmp_path / "episode")
    with pytest.raises(ArenaInfrastructureError, match="unavailable"):
        env.act(1, 6)
    env.start()
    row = deepcopy(env.latest[1])
    assert env.act(1, 6) == 1
    request = transport.requests[-1]
    assert request["observation_seq"] == row["observation_seq"]
    assert request["issued_game_ms"] == row["t_game_ms"]
    assert request["control_generation"] == row["control_generation"]
    with pytest.raises(ArenaInfrastructureError, match="unavailable"):
        env.act(1, 6)
    assert len([request for request in transport.requests if request["cmd"] == "body_action"]) == 1


@pytest.mark.parametrize("flag", ["terminated", "truncated", "infrastructure"])
def test_terminal_episode_rejects_new_decision(tmp_path, flag):
    env = EpisodeEnv(FakeTransport(), Path("body-arena.bench"))
    reset(env, tmp_path / "episode")
    env.start()
    env.arena[flag] = True
    with pytest.raises(ArenaInfrastructureError, match="terminal episode"):
        env.act(1, 6)


def test_lost_provider_is_infrastructure_not_duel_timeout(tmp_path):
    transport = FakeTransport()
    env = EpisodeEnv(transport, Path("body-arena.bench"))
    reset(env, tmp_path / "episode")
    env.start()
    transport.fail_heartbeat = True
    with pytest.raises(ArenaInfrastructureError, match="lost ownership"):
        env.step()
    assert env.arena["truncated"] is False


def test_native_control_lost_truncation_freezes_decisions_and_caches_final_row(tmp_path):
    transport = FakeTransport()
    env = EpisodeEnv(transport, Path("body-arena.bench"))
    reset(env, tmp_path / "episode")
    env.start()
    old_sequence = env.latest[1]["observation_seq"]
    transport.next_step_arena = {"truncated": True, "terminated": False, "infrastructure": False,
                                 "outcome": "control_lost", "reason": "target_visibility_lost"}
    step = env.step()
    assert step.done and step.arena["outcome"] == "control_lost"
    assert env.latest[1]["observation_seq"] > old_sequence
    assert env.latest[1]["control_generation"] == 0
    requests = len(transport.requests)
    assert env.step().done
    assert len(transport.requests) == requests
    with pytest.raises(ArenaInfrastructureError, match="terminal episode"):
        env.act(1, 6)
    assert len(transport.requests) == requests


def test_stale_epoch_observation_cannot_update_recurrent_memory(tmp_path):
    transport = FakeTransport()
    env = EpisodeEnv(transport, Path("body-arena.bench"))
    reset(env, tmp_path / "episode")
    old = transport.row(1)
    old["world_epoch"] -= 1
    transport.rows = [old]
    with pytest.raises(ArenaInfrastructureError, match="another episode epoch"):
        env.pull()
    assert env.processed_sequences == {1: 1, 2: 1}


def test_provider_obeys_observation_mask_and_never_reuses_row():
    transport = FakeTransport()
    row = transport.row(1)
    row["observation_seq"] = 1
    row["action_mask"] = [False]*12
    row["action_mask"][11] = True
    provider = ScriptedProvider()
    provider.reset((7, 1, 1))
    assert provider.choose(row, "attack") == 11
    assert provider.choose(row, "attack") is None
    provider.reset((7, 2, 2))
    assert provider.choose(row, "attack") == 11


def test_block_provider_keeps_body_idle_until_visible_attack():
    row = FakeTransport().row(1)
    row["observation_seq"] = 1
    row["action_mask"] = [False]*12
    row["action_mask"][9] = row["action_mask"][11] = True
    provider = ScriptedProvider()
    provider.reset((7, 1, 1))
    assert provider.choose(row, "block") is None
    row["observation_seq"] = 2
    row["target"]["prehit"] = True
    assert provider.choose(row, "block") == 9
