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
