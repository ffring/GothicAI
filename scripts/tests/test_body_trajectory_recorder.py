"""Live writer/link tests using protocol fixtures only; no engine or model."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_trajectory_recorder import RecorderError, TrajectoryRecorder


def initial():
    return {"session_epoch": 7, "world_epoch": 8, "game_ms": 1000,
            "actors": [{"entity_id": 1}, {"entity_id": 2}]}


def final(time=1050):
    return {"game_ms": time, "terminated": True, "truncated": False, "infrastructure": False,
            "outcome": "death_or_down", "reason": "terminal_actor"}


def common(sequence, kind, time=1010):
    return {"schema_version": 1, "record_seq": sequence, "capture_generation": 1,
            "kind": kind, "source": "native", "session_epoch": 7, "world_epoch": 8,
            "entity_id": 1, "control_generation": 0, "t_game_ms": time, "sim_step": 1}


def observation(sequence, source="native_capture", time=1010, raw_seq=99, dt=10, control=0):
    row = common(sequence, "observation", time)
    row["source"] = source
    row["observation"] = {"session_epoch": 7, "world_epoch": 8, "entity_id": 1,
                          "control_generation": control, "t_game_ms": time, "dt_game_ms": dt,
                          "observation_seq": raw_seq, "phase": "pre_native_primitive" if source == "native_capture" else "post_simulation_post_animation",
                          "action_mask": [True]*12, "can_decide": True, "target": {"visible": True, "in_w_range": True}}
    return row


def decision(sequence=2, valid=True, action=6):
    return {**common(sequence, "decision"), "decision_id": 11, "execution_id": 21,
            "observation_record_seq": 1, "native_intent": 3, "effective_primitive": 4,
            "action_id": action, "action_mask": [True]*12, "decision_valid": valid, "reason": "test_dispatch"}


def execution(sequence, phase, time=1010, **fields):
    return {**common(sequence, "execution", time), "decision_id": 11, "execution_id": 21,
            "phase": phase, "effective_primitive": 4, "reason": "fixture", **fields}


def packet(rows, **fields):
    return {"capture_generation": 1, "events": rows, "dropped_total": 0, "dropped_since_drain": 0,
            "reset_discarded_total": 0, "capture_failed": False, "capture_failures_total": 0,
            "queue_size": 0, **fields}


def recorder(tmp_path):
    return TrajectoryRecorder(tmp_path, episode_id="fixture-episode", initial=initial(),
                              metadata={"group": {"setup_family": "unit", "seed": 17}, "ood": False})


def native_stats():
    return {"body_observation": {"dropped_total": 0, "event_dropped_total": 0,
                                 "invalid_numeric_rows": 0, "missed_period_buckets": 0}}


def finish(instance):
    return instance.finish(final(), source_files=[], runner_status=native_stats(), control_events={"dropped_total": 0})


def rows(tmp_path):
    return [json.loads(line) for line in (tmp_path / "records.jsonl").read_text().splitlines()]


def test_live_exact_capture_label_execution_transition_and_canonical_time(tmp_path):
    recorder_instance = recorder(tmp_path)
    raw = [observation(1), decision(), execution(3, "accepted"), execution(4, "started"),
           observation(5, "periodic", 1020, raw_seq=99, dt=20), execution(6, "completed", 1050),
           observation(7, "periodic", 1050, raw_seq=100, dt=50)]
    recorder_instance.ingest(packet(raw), final=final())
    manifest = finish(recorder_instance)
    records = rows(tmp_path)
    observations = [row for row in records if row["kind"] == "observation"]
    assert manifest["counts"] == {"observation": 3, "decision": 1, "execution": 3, "transition": 1}
    assert [row["observation_seq"] for row in observations] == [1, 2, 3]
    assert [row["sequence_dt_game_ms"] for row in observations] == [10, 10, 30]
    assert [row["raw_dt_game_ms"] for row in observations] == [10, 20, 50]
    assert observations[0]["bc_label"] == 6 and observations[0]["decision_valid"] is True
    assert all(row["bc_label"] is None and row["decision_valid"] is False for row in observations[1:])
    assert observations[0]["observation"] == raw[0]["observation"]
    transition = next(row for row in records if row["kind"] == "transition")
    assert transition["observation_seq"] == 1 and transition["next_observation_seq"] == 3
    assert transition["duration_ms"] == 40 and transition["usable_for_bc"] is True
    assert transition["terminated"] is True
    assert [json.loads(line) for line in (tmp_path / "raw-trajectory.jsonl").read_text().splitlines()] == raw


def test_native_dispatch_acceptance_is_independent_of_expert_eligibility(tmp_path):
    instance = recorder(tmp_path)
    instance.ingest(packet([observation(1), decision(valid=False, action=None), execution(3, "accepted"),
                            execution(4, "consumed"), execution(5, "cancelled", physical_cancelled=False,
                                                            execution_incomplete=False, reason="consumed_without_start"),
                            observation(6, "periodic", 1050)]), final=final())
    finish(instance)
    records = rows(tmp_path)
    chosen = next(row for row in records if row["kind"] == "decision")
    assert chosen["accepted"] is True and chosen["expert"] is False and chosen["eligible"] is False
    assert chosen["action_id"] is None
    assert records[0]["bc_label"] is None
    assert next(row for row in records if row["kind"] == "transition")["usable_for_bc"] is False
    assert not any(row.get("phase") == "started" for row in records)


def test_recording_stop_is_censored_without_fake_physical_completion(tmp_path):
    instance = recorder(tmp_path)
    instance.ingest(packet([observation(1), decision(), execution(3, "accepted"), execution(4, "started"),
                            observation(5, "periodic", 1050), execution(6, "cancelled", 1050,
                              physical_cancelled=False, execution_incomplete=True, reason="recorder_disabled")]), final=final())
    finish(instance)
    transition = next(row for row in rows(tmp_path) if row["kind"] == "transition")
    assert transition["execution_status"] == "episode_ended" and transition["uncompleted"] is True
    assert transition["usable_for_bc"] is False and transition["terminated"] is True
    assert not any(row.get("phase") == "completed" for row in rows(tmp_path))


@pytest.mark.parametrize("field,value", [("dropped_total", 1), ("dropped_since_drain", 1), ("reset_discarded_total", 1),
                                          ("capture_failed", True), ("capture_failures_total", 1)])
def test_capture_losses_fail_closed_before_dataset_admission(tmp_path, field, value):
    instance = recorder(tmp_path)
    with pytest.raises(RecorderError, match="capture"):
        instance.ingest(packet([], **{field: value}))
    instance.abort()
    assert not (tmp_path / "trajectory-manifest.json").exists()


@pytest.mark.parametrize("bad_sequence", [1, 3])
def test_missing_or_repeated_raw_record_is_never_silent(tmp_path, bad_sequence):
    instance = recorder(tmp_path)
    instance.ingest(packet([observation(1, "periodic")]))
    with pytest.raises(RecorderError, match="missing, repeated or reordered"):
        instance.ingest(packet([observation(bad_sequence, "periodic", 1020)]))
    instance.abort()


def test_decision_requires_exact_raw_capture_and_actual_acceptance(tmp_path):
    instance = recorder(tmp_path)
    instance.ingest(packet([observation(1), decision()]))
    with pytest.raises(RecorderError, match="actual acceptance"):
        instance.ingest(packet([execution(3, "started")]))
    instance.abort()


def test_capture_generation_cannot_mix_episodes(tmp_path):
    instance = recorder(tmp_path)
    instance.ingest(packet([observation(1, "periodic")]))
    with pytest.raises(RecorderError, match="capture generation changed"):
        instance.ingest(packet([], capture_generation=2))
    instance.abort()


def test_non_native_scripted_observation_never_gets_bc_label(tmp_path):
    instance = recorder(tmp_path)
    observed = observation(1, "periodic", control=4)
    accepted = {"session_epoch": 7, "world_epoch": 8, "entity_id": 1, "control_generation": 4,
                "command_id": 9, "execution_id": 31, "observation_seq": 99, "action_id": 6,
                "effective_primitive": 4, "phase": "accepted", "t_game_ms": 1010}
    control = {**common(2, "control"), "source": "scripted", "body_control_event": accepted}
    started = {**common(3, "control"), "source": "scripted", "body_control_event": {**accepted, "phase": "started"}}
    completed = {**common(4, "control", 1050), "source": "scripted", "body_control_event": {**accepted, "phase": "completed", "t_game_ms": 1050}}
    instance.ingest(packet([observed, control, started, completed, observation(5, "periodic", 1050, control=4)]), final=final())
    finish(instance)
    records = rows(tmp_path)
    chosen = next(row for row in records if row["kind"] == "decision")
    assert chosen["source"] == "scripted" and chosen["expert"] is False
    assert all(row["bc_label"] is None for row in records if row["kind"] == "observation")
    assert next(row for row in records if row["kind"] == "transition")["usable_for_bc"] is False


@pytest.mark.parametrize("field", ["dropped_total", "event_dropped_total", "invalid_numeric_rows", "missed_period_buckets"])
def test_finish_requires_actual_zero_observer_counters(tmp_path, field):
    instance = recorder(tmp_path)
    instance.ingest(packet([observation(1, "periodic", 1050)]))
    status = native_stats()
    status["body_observation"][field] = 1
    with pytest.raises(RecorderError, match="lost data"):
        instance.finish(final(), source_files=[], runner_status=status, control_events={"dropped_total": 0})
    instance.abort()
    assert not (tmp_path / "trajectory-manifest.json").exists()


def test_finish_requires_actual_zero_control_drop_counter(tmp_path):
    instance = recorder(tmp_path)
    instance.ingest(packet([observation(1, "periodic", 1050)]))
    with pytest.raises(RecorderError, match="control event queue lost"):
        instance.finish(final(), source_files=[], runner_status=native_stats(), control_events={"dropped_total": 1})
    instance.abort()
    assert not (tmp_path / "trajectory-manifest.json").exists()
