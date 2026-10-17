"""GPU watchdog safety with fake systemd/GPU snapshots; no jobs or models."""
import argparse
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import body_training_gpu_watch as module


UNIT = "gothic-body-bc-repair-v7-defence-train-cuda.service"
CGROUP = f"/user.slice/user-1000.slice/user@1000.service/app.slice/{UNIT}"
STATE = module.UnitState("active", CGROUP, "a"*32)


@pytest.mark.parametrize("unit", ["gothic-decider.service", "device-assistant.service", "gothic-body-bc-x-train.service",
                                  UNIT+";gothic-decider.service", "*.service", "gq-*.service", "gq-123.service",
                                  "gq-0123456789ag.service", "gq-0123456789ab.service;gothic-decider.service"])
def test_unit_guard_cannot_target_services_or_patterns(unit):
    with pytest.raises(ValueError, match="exact"):
        module.exact_unit(unit)


@pytest.mark.parametrize("unit", [UNIT, "gq-0123456789ab.service"])
def test_exact_legacy_or_queue_unit_is_accepted(unit):
    assert module.exact_unit(unit) == unit


def test_cgroup_cannot_include_another_users_or_manager_processes(tmp_path):
    assert module.own_cgroup(UNIT, CGROUP, uid=1000, root=tmp_path).name == UNIT
    with pytest.raises(RuntimeError, match="outside"):
        module.own_cgroup(UNIT, CGROUP, uid=1001, root=tmp_path)
    with pytest.raises(RuntimeError, match="outside"):
        module.own_cgroup(UNIT, CGROUP.removesuffix("/"+UNIT), uid=1000, root=tmp_path)


def test_gpu_sample_counts_only_own_processes_on_gpu_zero_without_leaking_other_pids():
    def run(argv):
        if "--query-gpu=uuid,memory.free" in argv:
            return "GPU-one, 4096\n"
        return "GPU-one, 77, 1550\nGPU-one, 991234, [N/A]\nGPU-two, 77, 9000\nGPU-one, 77, 1550\n"
    sample = module.gpu_sample({77, 78}, run)
    assert sample["own_gpu_mib"] == 1550
    assert sample["own_gpu_process_count"] == 1
    assert "991234" not in json.dumps(sample)
    assert module.budget_reasons(sample) == ["own_gpu_memory_above_1536_mib"]


def test_unknown_own_memory_fails_closed_instead_of_reporting_zero():
    def run(argv):
        return "GPU-one, 4096\n" if "--query-gpu=uuid,memory.free" in argv else "GPU-one, 77, [N/A]\n"
    with pytest.raises(ValueError):
        module.gpu_sample({77}, run)


def test_stop_rechecks_exact_invocation_and_never_stops_reused_unit(monkeypatch):
    calls = []
    monkeypatch.setattr(module, "own_cgroup", lambda *a: Path("/fake"))
    def run(argv):
        calls.append(argv)
        return f"Id={UNIT}\nLoadState=loaded\nActiveState=active\nControlGroup={CGROUP}\nInvocationID={'b'*32}\n"
    with pytest.raises(RuntimeError, match="changed"):
        module.stop_exact_invocation(UNIT, STATE, run)
    assert not any("stop" in argv for argv in calls)


def test_stop_uses_one_exact_user_unit_after_identity_check(monkeypatch):
    calls = []
    monkeypatch.setattr(module, "own_cgroup", lambda *a: Path("/fake"))
    def run(argv):
        calls.append(argv)
        return f"Id={UNIT}\nLoadState=loaded\nActiveState=active\nControlGroup={CGROUP}\nInvocationID={'a'*32}\n"
    assert module.stop_exact_invocation(UNIT, STATE, run)
    assert calls[-1] == ["systemctl", "--user", "stop", UNIT]


def test_missing_startup_unit_can_be_verified_without_listing_other_units():
    calls = []
    def run(argv):
        calls.append(argv)
        if "show" in argv:
            raise RuntimeError("unit not found")
        return ""
    assert module.unit_state(UNIT, run) is None
    assert calls[-1][3] == UNIT


def test_budget_failure_stops_only_the_bound_job_and_persists_failure(tmp_path, monkeypatch):
    stops = []
    monkeypatch.setattr(module, "unit_state", lambda *a: STATE)
    monkeypatch.setattr(module, "own_cgroup", lambda *a: tmp_path)
    monkeypatch.setattr(module, "cgroup_pids", lambda *a: {77})
    monkeypatch.setattr(module, "gpu_sample", lambda *a: {
        "gpu_index": 0, "gpu_free_mib": 1900, "own_gpu_mib": 1600,
        "own_gpu_process_count": 1, "own_cgroup_process_count": 1})
    monkeypatch.setattr(module, "stop_exact_invocation", lambda unit, state: stops.append((unit, state)) or True)
    args = argparse.Namespace(unit=UNIT, output=tmp_path/"watch.jsonl", summary=tmp_path/"watch.json",
                              job_done=tmp_path/"done", startup_timeout_seconds=60)
    assert module.watch(args) == 2
    assert stops == [(UNIT, STATE)]
    summary = json.loads(args.summary.read_text())
    assert summary["status"] == "budget_failure" and summary["stop_requested"]
    events = [json.loads(line) for line in args.output.read_text().splitlines()]
    assert any(row["kind"] == "budget_failure" for row in events)
    assert summary["peak_own_gpu_mib"] == 1600


GQ_UNIT = "gq-0123456789ab.service"
GQ_CGROUP = f"/user.slice/user-1000.slice/user@1000.service/app.slice/{GQ_UNIT}"
GQ_A = module.UnitState("active", GQ_CGROUP, "a"*32)
GQ_B = module.UnitState("active", GQ_CGROUP, "b"*32)
GOOD_SAMPLE = {"gpu_index": 0, "gpu_free_mib": 4096, "own_gpu_mib": 100,
               "own_gpu_process_count": 1, "own_cgroup_process_count": 1}
BAD_SAMPLE = {**GOOD_SAMPLE, "own_gpu_mib": 1600}


def queue_state(status, *, attempt="1"*32, invocation="a"*32, start=1):
    return module.QueueJobState(status, attempt, invocation, start)


def queue_watch(tmp_path, monkeypatch, jobs, units):
    """Advance fake atomic queue/systemd snapshots only at monitor sleeps."""
    step = [0]
    states = []
    samples = []
    stops = []
    args = argparse.Namespace(unit=GQ_UNIT, output=tmp_path/"watch.jsonl", summary=tmp_path/"watch.json",
                              job_done=tmp_path/"done", startup_timeout_seconds=60,
                              gq_home=tmp_path/"queue")
    monkeypatch.setattr(module, "queue_job_state", lambda *a: jobs[step[0]])
    def unit_state(*a):
        states.append(step[0])
        return units[step[0]]
    monkeypatch.setattr(module, "unit_state", unit_state)
    monkeypatch.setattr(module, "own_cgroup", lambda *a: tmp_path)
    monkeypatch.setattr(module, "cgroup_pids", lambda *a: {77})
    def sample(*a):
        samples.append(step[0])
        return GOOD_SAMPLE
    monkeypatch.setattr(module, "gpu_sample", sample)
    monkeypatch.setattr(module, "stop_exact_invocation", lambda unit, state: stops.append(state) or True)
    def sleep(*a):
        step[0] += 1
        assert step[0] < len(jobs), "monitor did not finish after terminal queue state"
    monkeypatch.setattr(module.time, "sleep", sleep)
    return args, step, states, samples, stops


def test_queue_monitor_waits_through_denied_active_attempt_and_missing_unit(tmp_path, monkeypatch):
    jobs = [queue_state("starting", start=None), queue_state("queued", start=None),
            queue_state("running", attempt="2"*32, invocation="b"*32),
            queue_state("done", attempt="2"*32, invocation="b"*32)]
    args, step, states, samples, stops = queue_watch(tmp_path, monkeypatch, jobs, [GQ_A, None, GQ_B, None])
    assert module.watch(args) == 0
    # Unit A was active before gq admitted any command and must never count
    # as successful monitoring, even if its GPU usage would have been zero.
    assert step == [3] and states == [2, 2] and samples == [2] and stops == []
    summary = json.loads(args.summary.read_text())
    assert summary["status"] == "complete" and summary["samples"] == 1
    assert summary["invocation_id"] == GQ_B.invocation


def test_missing_queue_unit_after_sample_waits_for_job_done(tmp_path, monkeypatch):
    jobs = [queue_state("running"), queue_state("queued"), queue_state("queued")]
    args, step, states, samples, stops = queue_watch(tmp_path, monkeypatch, jobs, [GQ_A, None, None])
    def sleep(*a):
        step[0] += 1
        assert step[0] <= 2
        if step[0] == 2:
            args.job_done.touch()
    monkeypatch.setattr(module.time, "sleep", sleep)
    assert module.watch(args) == 0
    assert step == [2] and samples == [0] and stops == []
    assert json.loads(args.summary.read_text())["samples"] == 1


def test_readmitted_queue_attempt_gets_its_own_budget_samples_and_stop(tmp_path, monkeypatch):
    jobs = [queue_state("starting", start=None), queue_state("queued", start=None),
            queue_state("running", attempt="2"*32, invocation="b"*32)]
    args, step, states, samples, stops = queue_watch(tmp_path, monkeypatch, jobs, [GQ_A, None, GQ_B])
    monkeypatch.setattr(module, "gpu_sample", lambda *a: BAD_SAMPLE)
    assert module.watch(args) == 2
    assert step == [2] and stops == [GQ_B]
    summary = json.loads(args.summary.read_text())
    assert summary["status"] == "budget_failure" and summary["samples"] == 1
    assert summary["invocation_id"] == GQ_B.invocation


def test_stale_queue_gpu_sample_cannot_stop_a_later_invocation(tmp_path, monkeypatch):
    jobs = [queue_state("running"), queue_state("running", attempt="2"*32, invocation="b"*32),
            queue_state("done", attempt="2"*32, invocation="b"*32)]
    args, step, states, samples, stops = queue_watch(tmp_path, monkeypatch, jobs, [GQ_A, GQ_B, None])
    def sample(*a):
        if step[0] == 0:
            step[0] = 1
            return BAD_SAMPLE
        return GOOD_SAMPLE
    monkeypatch.setattr(module, "gpu_sample", sample)
    # The first sleep follows a discarded stale sample; stay on the newly
    # admitted invocation so it receives a fresh sample of its own.
    sleeps = [0]
    def sleep(*a):
        sleeps[0] += 1
        if sleeps[0] > 1:
            step[0] += 1
    monkeypatch.setattr(module.time, "sleep", sleep)
    assert module.watch(args) == 0
    summary = json.loads(args.summary.read_text())
    assert stops == [] and summary["samples"] == 1 and summary["peak_own_gpu_mib"] == 100
    events = [json.loads(line) for line in args.output.read_text().splitlines()]
    assert any(row["kind"] == "sample_discarded" for row in events)
    assert summary["invocation_id"] == GQ_B.invocation


def test_queue_invocation_change_requires_a_new_admission_attempt(tmp_path, monkeypatch):
    jobs = [queue_state("running"), queue_state("running", invocation="b"*32)]
    args, step, states, samples, stops = queue_watch(tmp_path, monkeypatch, jobs, [GQ_A, GQ_B])
    assert module.watch(args) == 3
    summary = json.loads(args.summary.read_text())
    assert summary["error"] == "Training invocation changed during monitoring"
    # Error handling still asks stop_exact_invocation to verify the old bound
    # invocation; it never substitutes B based on a sample from A.
    assert stops == [GQ_A]


def test_queue_cancelled_before_admission_needs_no_gpu_sample(tmp_path, monkeypatch):
    args, step, states, samples, stops = queue_watch(
        tmp_path, monkeypatch, [queue_state("cancelled", start=None)], [None])
    # A fast cancellation can return before the monitor process starts.
    args.job_done.touch()
    assert module.watch(args) == 0
    summary = json.loads(args.summary.read_text())
    assert summary["status"] == "not_started" and summary["samples"] == 0
    assert states == samples == stops == []


def test_queue_state_reader_uses_only_exact_job_and_requires_running_identity(tmp_path):
    path = tmp_path/"state.json"
    path.write_text(json.dumps({"version": 1, "jobs": [
        {"id": "other", "status": "running"},
        {"id": "0123456789ab", "status": "queued", "start": None}]}))
    assert module.queue_job_state(tmp_path, GQ_UNIT) == module.QueueJobState("queued", None, None, None)
    path.write_text(json.dumps({"version": 1, "jobs": [
        {"id": "0123456789ab", "status": "running", "attempt": "1"*32, "start": 1}]}))
    with pytest.raises(RuntimeError, match="admission identity"):
        module.queue_job_state(tmp_path, GQ_UNIT)



def test_active_admitted_queue_unit_without_cgroup_still_fails_closed(tmp_path, monkeypatch):
    args, step, states, samples, stops = queue_watch(
        tmp_path, monkeypatch, [queue_state("running")], [GQ_A])
    def disappeared(*a):
        raise FileNotFoundError("gone")
    monkeypatch.setattr(module, "cgroup_pids", disappeared)
    assert module.watch(args) == 3
    summary = json.loads(args.summary.read_text())
    assert summary["error"] == "Active training unit has no readable cgroup"
    assert stops == [GQ_A] and summary["samples"] == 0
