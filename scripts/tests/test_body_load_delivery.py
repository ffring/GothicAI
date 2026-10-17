"""Four real runtime slots use shared delivery hooks; fake receipts/models only."""
from collections import Counter, OrderedDict
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import body_policy_load as load
from body_policy_diagnostics import DiagnosticRunner


def fake_pools(monkeypatch):
    trace = []
    class Runtime:
        def __init__(self, checkpoint, *, device, max_actors, inference_cpu_threads=2):
            self.slot = len(trace)
            self.checkpoint_sha256 = load.sha256(checkpoint)
            trace.append(("construct", checkpoint.name, device, max_actors, self))
    def warmup(runtime, steps):
        trace.append(("warmup", runtime, steps))
        return {"kind": "fake_backend_receipt", "steps": steps,
                "checkpoint_sha256": runtime.checkpoint_sha256, "runtime_before": {"actors": 0},
                "runtime_after": {"actors": 0}, "commands": 0, "quality_credit": False}
    monkeypatch.setattr(load, "PolicyRuntime", Runtime)
    monkeypatch.setattr(load, "startup_warmup", warmup)
    return trace


def arguments(tmp_path):
    gru, mlp = tmp_path/"gru.pt", tmp_path/"mlp.pt"
    gru.write_bytes(b"fixed-gru"); mlp.write_bytes(b"fixed-mlp")
    return SimpleNamespace(device="cpu", gru_checkpoint=gru, mlp_checkpoint=mlp,
                           output=tmp_path, no_service_load=True, config=tmp_path/"config.json")


def test_all_eight_distinct_runtime_objects_are_warmed_without_episode_state_before_pool_use(tmp_path, monkeypatch):
    trace = fake_pools(monkeypatch)
    args = arguments(tmp_path)
    expected = {name: load.sha256(getattr(args, name+"_checkpoint")) for name in ("gru", "mlp")}
    pools, proofs = load.prepare_policy_pools(args, {"policies": ["native", "gru", "mlp"]},
                                               {"startup_warmup_steps": 2}, expected)
    assert set(pools) == set(proofs) == {"gru", "mlp"}
    assert len({id(runtime) for pool in pools.values() for runtime in pool}) == 8
    assert len([row for row in trace if row[0] == "warmup"]) == 8
    for name in ("gru", "mlp"):
        assert [proof["runtime_pool_slot"] for proof in proofs[name]] == [0, 1, 2, 3]
        assert all(proof["steps"] == 2 and proof["checkpoint_sha256"] == expected[name] and
                   proof["runtime_before"] == proof["runtime_after"] and proof["commands"] == 0
                   for proof in proofs[name])


def test_compact_flag_and_all_eight_startup_proofs_are_bound_before_runner_or_any_reset(tmp_path, monkeypatch):
    trace = fake_pools(monkeypatch)
    args = arguments(tmp_path)
    monkeypatch.setattr(load, "provenance", lambda _: {})
    def runner(actual_args):
        assert actual_args.compact_replies is True
        warm = json.loads((tmp_path/"startup-warmup.json").read_text())
        manifest = json.loads((tmp_path/"manifest.json").read_text())
        assert manifest["startup_warmup"] == warm
        assert manifest["startup_warmup_before_any_arena"] is True
        assert manifest["startup_runtime_counts"] == {"gru": 4, "mlp": 4}
        assert len(warm["gru"]) == len(warm["mlp"]) == 4
        assert len([row for row in trace if row[0] == "warmup"]) == 8
        raise RuntimeError("test stops before native construction or any reset")
    monkeypatch.setattr(load, "DiagnosticRunner", runner)
    config = {"runtime": {"startup_warmup_steps": 2, "compact_replies": True},
              "load": {"policies": ["native", "gru", "mlp"]}, "services": {}}
    assert load.run_suite(args, config) == 2
    assert json.loads((tmp_path/"suite.json").read_text())["cases"] == []


def buffered_runner():
    runner = DiagnosticRunner.__new__(DiagnosticRunner)
    runner.ipc, runner.timing = io.StringIO(), io.StringIO()
    runner.received = OrderedDict()
    runner._evidence_pending, runner._evidence_pending_bytes, runner._dispatch_window = [], 0, False
    runner.last_request_ns = runner.last_response_ns = 0
    return runner


def test_four_actor_arbitration_keeps_all_receipts_in_buffer_until_batch_is_dispatched():
    runner = buffered_runner()
    actors = (11, 15, 20, 21)
    rows = {entity: {"entity_id": entity, "session_epoch": 1, "world_epoch": 2,
                     "control_generation": entity+100, "observation_seq": index+1,
                     "periodic": True, "t_game_ms": 100} for index, entity in enumerate(actors)}
    env = SimpleNamespace(latest=rows, runner=runner, eligible=lambda _: True, command_responses=[],
                          fresh_observations_required=False, persist=runner.persist_json)
    calls = []
    class Policy:
        def arbitrate(self, row, *, stream_seq):
            calls.append(row["entity_id"])
            return {"status": "decision", "action_id": 6}
    def accept(entity, action):
        assert not runner.ipc.getvalue() and not journal.getvalue() and not latency.getvalue()
        request = {"cmd": "body_action", "entity_id": entity, "action_id": action}
        response = {"accepted": True, "execution_id": entity}
        runner.record("request", request); runner.record("response", response)
        env.command_responses.append({"request": request, "response": response,
                                      "sent_wall_monotonic_ns": runner.last_request_ns})
        return entity
    env.try_act = accept
    journal, latency, stats = io.StringIO(), io.StringIO(), Counter()
    runner.begin_dispatch_window()
    load.arbitrate_physical_actors(env, {entity: Policy() for entity in actors}, Counter(),
                                  journal, latency, stats, set())
    assert calls == list(actors) and stats["accepted_model_commands"] == 4
    assert not journal.getvalue() and not runner.ipc.getvalue()
    runner.end_dispatch_window()
    assert len(journal.getvalue().splitlines()) == len(latency.getvalue().splitlines()) == 4
    assert len(runner.ipc.getvalue().splitlines()) == 8 and runner._evidence_pending_bytes == 0


def test_load_finally_flushes_captured_evidence_before_journals_close_on_failure(monkeypatch):
    runner = buffered_runner()
    journal = io.StringIO()
    env = SimpleNamespace(runner=runner)
    def failure(*args, **kwargs):
        runner.begin_dispatch_window()
        runner.persist_json(journal, {"kind": "actual_refusal", "accepted": False})
        raise RuntimeError("actual failure")
    monkeypatch.setattr(load, "_load_rollout", failure)
    with pytest.raises(RuntimeError, match="actual failure"):
        load.load_rollout(env, {}, {}, journal, io.StringIO())
    assert json.loads(journal.getvalue())["accepted"] is False
    assert not runner._dispatch_window and not runner._evidence_pending


def test_pending_refresh_after_empty_drain_cannot_dispatch_another_old_actor():
    env = SimpleNamespace(fresh_observations_required=True)
    class Policy:
        def arbitrate(self, *args, **kwargs):
            raise AssertionError("an old batch cannot run during pending fresh delivery")
    stats, journal = Counter(), io.StringIO()
    load.arbitrate_physical_actors(env, {11: Policy()}, Counter(), journal, io.StringIO(), stats, set())
    assert not stats and not journal.getvalue()
