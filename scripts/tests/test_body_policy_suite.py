"""Native handoff accounting uses recorded rows, without an engine or model."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_arena_env import ArenaInfrastructureError, EpisodeIdentity
from body_policy_suite import PolicyEpisodeEnv, native_fallback_boundary


INITIAL = {"primary_entity_id": 4}
FINAL = {"session_epoch": 1, "world_epoch": 2, "game_ms": 100}
MAPPING = {"phase": "arena_actor_mapping", "observation_entity_id": 4, "actor_id": 10}


def ownership(reason, time=100):
    return {"entity_id": 4, "session_epoch": 1, "world_epoch": 2, "phase": "ownership",
            "reason": reason, "t_game_ms": time}


def test_terminal_pending_release_and_historical_idle_attempt_do_not_invent_native_execution():
    cleanup = {"phase": "native_attempt", "actor_id": 10, "t_game": 100,
               "hook": "set_anim", "choice_id": 17, "accepted": True}
    result = native_fallback_boundary(INITIAL, FINAL,
              [ownership("release_pending"), ownership("native_restored")], [MAPPING, cleanup])
    assert not any(result.values())


@pytest.mark.parametrize("phase", ["native_choice", "native_consume"])
def test_real_native_executor_after_same_frame_restoration_loses_model_credit(phase):
    primary = {"phase": phase, "actor_id": 10, "t_game": 100, "choice_id": 1}
    opponent = {**primary, "actor_id": 11}
    result = native_fallback_boundary(INITIAL, FINAL, [ownership("native_restored")],
                                      [MAPPING, primary, opponent])
    assert result["same_final_native_continuations"] == [primary]


def test_pre_final_native_restoration_always_loses_credit_without_a_new_animation():
    restored = ownership("native_restored", 99)
    assert native_fallback_boundary(INITIAL, FINAL, [restored], [MAPPING])["restorations_before_final"] == [restored]


class PendingTransport:
    """Native protocol receipts only; no movement, damage, physics or model."""
    def __init__(self, *, release_pending=True, reason="release_pending"):
        self.release_pending, self.reason, self.requests = release_pending, reason, []

    def call(self, request):
        self.requests.append(request)
        command = request["cmd"]
        if command == "body_heartbeat":
            return {"accepted": False, "reason": self.reason}
        if command == "body_control_status":
            return {"actors": [{"entity_id": 4, "control_generation": 7,
                                "release_pending": self.release_pending}]}
        if command == "arena_step":
            return {"arena": {"game_ms": 133, "terminated": False, "truncated": False,
                               "infrastructure": False}, "advanced_substeps": 1}
        if command == "body_observations":
            return {"observations": []}
        if command == "body_events":
            return {"events": []}
        raise AssertionError(command)


def pending_env(transport):
    env = PolicyEpisodeEnv(transport, Path("arena.bench"), owner_provider="stage7-policy-gru")
    env.identity = EpisodeIdentity(1, 2, 3, 4)
    env.arena = {"game_ms": 100, "terminated": False, "truncated": False}
    env.leases = {4: {"session_epoch": 1, "world_epoch": 2, "entity_id": 4, "control_generation": 7}}
    return env


def test_same_generation_pending_native_guard_can_advance_without_dropping_owned_history():
    transport = PendingTransport()
    env = pending_env(transport)
    step = env.step()
    assert step.advanced_substeps == 1 and not step.done
    assert env.leases[4]["control_generation"] == 7 and not env.fallbacks
    assert any(request["cmd"] == "arena_step" for request in transport.requests)


@pytest.mark.parametrize("changes", [{"release_pending": False}, {"reason": "owner_mismatch"}])
def test_heartbeat_reject_without_matching_native_pending_release_stays_infrastructure(changes):
    transport = PendingTransport(**changes)
    with pytest.raises(ArenaInfrastructureError, match="heartbeat rejected"):
        pending_env(transport).step()
    assert not any(request["cmd"] == "arena_step" for request in transport.requests)
