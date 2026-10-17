"""Admission and lifecycle checks; never touch the machine's real queue or units."""
from __future__ import annotations

import fcntl
from importlib.machinery import SourceFileLoader
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "gq.py"
GIB = 1024**3


@pytest.fixture(scope="module")
def gq():
    spec = importlib.util.spec_from_file_location("gq_test_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def preserve_test_process_priority(gq, monkeypatch):
    # Renicing the imported module would otherwise change pytest's own process.
    monkeypatch.setattr(gq.os, "nice", lambda _increment: 19)


FAKE_SYSTEMD = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["FAKE_SYSTEMD_HOME"])
root.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps({"tool": Path(sys.argv[0]).name, "args": args}) + "\n")

def unit_name():
    for index, value in enumerate(args):
        if value.startswith("--unit="):
            return value.split("=", 1)[1].removesuffix(".service")
        if value in ("--unit", "-u"):
            return args[index + 1].removesuffix(".service")
    for value in args:
        if value.startswith("gq-"):
            return value.removesuffix(".service")
    return ""

unit = unit_name()
path = root / (unit + ".json")
if Path(sys.argv[0]).name == "systemd-run":
    if os.environ.get("FAKE_SYSTEMD_FAIL"):
        print("fake systemd launch failed", file=sys.stderr)
        sys.exit(1)
    path.write_text(json.dumps({"ActiveState": "active", "SubState": "running",
                               "Result": "success", "ExecMainCode": "1",
                               "ExecMainStatus": "0", "LoadState": "loaded"}))
    print("Running as unit: " + unit + ".service", file=sys.stderr)
    sys.exit(0)
state = json.loads(path.read_text()) if path.exists() else {
    "ActiveState": "inactive", "SubState": "dead", "Result": "success",
    "ExecMainCode": "1", "ExecMainStatus": "0", "LoadState": "not-found"}
command = next((value for value in args if value in
                ("show", "stop", "is-active", "reset-failed", "status")), "show")
if command in ("stop", "reset-failed"):
    state.update(ActiveState="inactive", SubState="dead")
    path.write_text(json.dumps(state))
    sys.exit(0)
if command == "is-active":
    print(state["ActiveState"])
    sys.exit(0 if state["ActiveState"] == "active" else 3)
if "--value" in args:
    fields = []
    for index, value in enumerate(args):
        if value in ("--property", "-p"):
            fields.extend(args[index + 1].split(","))
        elif value.startswith("--property="):
            fields.extend(value.split("=", 1)[1].split(","))
    for field in fields:
        print(state.get(field, ""))
else:
    for key, value in state.items():
        print(key + "=" + str(value))
'''


@pytest.fixture
def host(tmp_path, monkeypatch):
    """All unit calls and compatibility-lock inspection are isolated fakes."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("systemd-run", "systemctl"):
        executable = binaries / name
        executable.write_text(FAKE_SYSTEMD)
        executable.chmod(0o755)
    home = tmp_path / "queue"
    locks = tmp_path / "proc-locks"
    locks.write_text("")
    legacy = tmp_path / "legacy.lock"
    legacy.touch()
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_SYSTEMD_HOME", str(tmp_path / "systemd"))
    monkeypatch.setenv("GQ_HOME", str(home))
    monkeypatch.setenv("GQ_PROFILE", "ryzen")
    monkeypatch.setenv("GQ_LEGACY_LOCK", str(legacy))
    monkeypatch.setenv("GQ_OWNER", "test-worktree")
    return {"home": home, "legacy": legacy, "locks": locks,
            "systemd": tmp_path / "systemd", "env": dict(os.environ)}


@pytest.fixture
def queue(gq, host, monkeypatch):
    q = gq.Queue(home=host["home"], legacy_lock=host["legacy"], proc_locks=host["locks"])
    monkeypatch.setattr(q, "now", lambda: 1000.0)
    monkeypatch.setattr(q, "disk_free", lambda: 100 * GIB)
    # The scheduler is exercised independently from the systemd lifecycle.
    monkeypatch.setattr(q, "reconcile", lambda: None)
    monkeypatch.setattr(q, "launch", lambda job: q.started(job["id"]))
    return q


def spec(kind="quick", *, owner="session-a", threads=4, mem=GIB,
         gpu_mem=0, display=False, maximum=60, name="test", command=None):
    return {"class": kind, "threads": threads, "mem": mem,
            "gpu_mem": gpu_mem, "display": display, "max": maximum,
            "name": name, "command": command or ["true"], "owner": owner}


def enqueue(q, kind="quick", **kwargs):
    return q.submit(spec(kind, **kwargs))


def start(q, kind="quick", **kwargs):
    ident = enqueue(q, kind, **kwargs)
    q.tick()
    assert q.get(ident)["status"] == "running"
    return ident


def test_quick_runs_with_batch_even_when_all_batch_threads_are_claimed(queue):
    batch = start(queue, "batch", threads=28, mem=16 * GIB)
    quick = start(queue, threads=4, mem=4 * GIB)
    assert queue.get(batch)["status"] == queue.get(quick)["status"] == "running"


@pytest.mark.parametrize("resource", ["mem", "gpu_mem", "display"])
def test_quick_waits_for_its_memory_gpu_or_window(queue, resource):
    first = {"mem": 12 * GIB, "gpu_mem": 6 * GIB, "display": True}
    waiting = {"mem": 9 * GIB} if resource == "mem" else (
        {"gpu_mem": 5 * GIB} if resource == "gpu_mem" else {"display": True})
    running = start(queue, **first)
    ident = enqueue(queue, **waiting)
    queue.tick()
    assert queue.get(ident)["status"] == "queued"
    queue.finish(running, "success")
    queue.tick()
    assert queue.get(ident)["status"] == "running"


def test_batch_thread_budget_includes_all_running_batches(queue):
    first = start(queue, "batch", threads=16)
    second = start(queue, "batch", owner="session-b", threads=12)
    third = enqueue(queue, "batch", owner="session-c", threads=1)
    queue.tick()
    assert queue.get(third)["status"] == "queued"
    queue.finish(first, "success")
    queue.tick()
    assert queue.get(second)["status"] == queue.get(third)["status"] == "running"


def test_batch_fifo_does_not_allow_a_smaller_job_to_jump_ahead(queue):
    running = start(queue, "batch", owner="running", threads=20)
    first = enqueue(queue, "batch", owner="first", threads=12)
    second = enqueue(queue, "batch", owner="second", threads=8)
    queue.tick()
    assert queue.get(first)["status"] == queue.get(second)["status"] == "queued"
    queue.finish(running, "success")
    queue.tick()
    assert queue.get(first)["status"] == queue.get(second)["status"] == "running"


def test_a_session_does_not_start_second_batch_while_another_session_waits(queue):
    start(queue, "batch", owner="session-a", threads=8)
    same_session = enqueue(queue, "batch", owner="session-a", threads=8)
    other_session = enqueue(queue, "batch", owner="session-b", threads=20)
    queue.tick()
    assert queue.get(same_session)["status"] == "queued"
    assert queue.get(other_session)["status"] == "running"


def test_fairness_skip_preserves_fifo_for_all_later_batches_of_that_owner(queue):
    first = start(queue, "batch", owner="session-a", threads=8)
    second = enqueue(queue, "batch", owner="session-a", threads=8)
    other = enqueue(queue, "batch", owner="session-b", threads=8)
    third = enqueue(queue, "batch", owner="session-a", threads=8)
    queue.tick()
    assert queue.get(first)["status"] == queue.get(other)["status"] == "running"
    assert queue.get(second)["status"] == queue.get(third)["status"] == "queued"


def test_same_session_can_share_budget_when_no_other_session_is_waiting(queue):
    start(queue, "batch", owner="session-a", threads=8)
    start(queue, "batch", owner="session-a", threads=8)


def test_first_quiet_stops_batch_admission_and_waits_for_all_jobs(queue):
    batch = start(queue, "batch", maximum=120)
    quick = start(queue, maximum=30)
    quiet = enqueue(queue, "quiet")
    next_batch = enqueue(queue, "batch", owner="session-b")
    queue.tick()
    assert queue.get(quiet)["status"] == queue.get(next_batch)["status"] == "queued"
    queue.finish(batch, "success")
    queue.tick()
    assert queue.get(quiet)["status"] == "queued"
    queue.finish(quick, "success")
    queue.tick()
    assert queue.get(quiet)["status"] == "running"
    assert queue.get(next_batch)["status"] == "queued"
    queue.finish(quiet, "success")
    queue.tick()
    assert queue.get(next_batch)["status"] == "running"


def test_short_quick_can_finish_before_last_batch_ahead_of_quiet(queue, monkeypatch):
    start(queue, "batch", owner="one", maximum=60)
    start(queue, "batch", owner="two", maximum=120)
    quiet = enqueue(queue, "quiet")
    monkeypatch.setattr(queue, "now", lambda: 1010.0)
    quick = start(queue, maximum=100)
    assert queue.get(quiet)["status"] == "queued"
    assert queue.get(quick)["status"] == "running"


@pytest.mark.parametrize("maximum", [110, 111])
def test_quick_must_end_strictly_before_last_batch_for_waiting_quiet(queue, monkeypatch, maximum):
    start(queue, "batch", maximum=120)
    enqueue(queue, "quiet")
    monkeypatch.setattr(queue, "now", lambda: 1010.0)
    quick = enqueue(queue, maximum=maximum)
    queue.tick()
    assert queue.get(quick)["status"] == "queued"


def test_pending_quiet_without_running_batch_blocks_new_quick(queue):
    first = start(queue, maximum=120)
    quiet = enqueue(queue, "quiet")
    quick = enqueue(queue, maximum=1)
    queue.tick()
    assert queue.get(quick)["status"] == queue.get(quiet)["status"] == "queued"
    queue.finish(first, "success")
    queue.tick()
    assert queue.get(quiet)["status"] == "running"
    assert queue.get(quick)["status"] == "queued"


def test_quiet_jobs_are_exclusive_and_ordered(queue):
    first = start(queue, "quiet")
    second = enqueue(queue, "quiet")
    quick = enqueue(queue)
    queue.tick()
    assert queue.get(second)["status"] == queue.get(quick)["status"] == "queued"
    queue.finish(first, "success")
    queue.tick()
    assert queue.get(second)["status"] == "running"
    assert queue.get(quick)["status"] == "queued"


@pytest.mark.parametrize("kind", ["quick", "batch", "quiet"])
def test_disk_floor_blocks_new_jobs_and_allows_exact_threshold(queue, monkeypatch, kind):
    monkeypatch.setattr(queue, "disk_free", lambda: 20 * GIB - 1)
    ident = enqueue(queue, kind)
    queue.tick()
    assert queue.get(ident)["status"] == "queued"
    monkeypatch.setattr(queue, "disk_free", lambda: 20 * GIB)
    queue.tick()
    assert queue.get(ident)["status"] == "running"


def test_batch_maximum_is_two_hours(queue, gq):
    with pytest.raises(gq.QueueError, match="(?i)(max|2|7200|час)"):
        enqueue(queue, "batch", maximum=7201)
    assert enqueue(queue, "batch", maximum=7200)


def write_legacy_waiter(host, *, inode=None, blocked=True, mode="WRITE"):
    stat = host["legacy"].stat()
    key = f"{os.major(stat.st_dev):02x}:{os.minor(stat.st_dev):02x}:{inode or stat.st_ino}"
    host["locks"].write_text(f"12: {'-> ' if blocked else ''}FLOCK ADVISORY {mode} 12345 {key} 0 EOF\n")


@pytest.mark.parametrize("kind", ["quick", "batch", "quiet"])
def test_old_exclusive_lock_prevents_all_new_admissions(queue, host, kind):
    ident = enqueue(queue, kind)
    with host["legacy"].open("a+") as old_client:
        fcntl.flock(old_client, fcntl.LOCK_EX | fcntl.LOCK_NB)
        queue.tick()
        assert queue.get(ident)["status"] == "queued"
    queue.tick()
    assert queue.get(ident)["status"] == "running"


def test_blocked_old_exclusive_waiter_drains_batches_like_quiet(queue, host):
    running = start(queue, "batch", maximum=120)
    write_legacy_waiter(host)
    assert queue.legacy_waiting()
    batch = enqueue(queue, "batch", owner="session-b")
    short_quick = enqueue(queue, maximum=30)
    long_quick = enqueue(queue, maximum=120)
    queue.tick()
    assert queue.get(batch)["status"] == queue.get(long_quick)["status"] == "queued"
    assert queue.get(short_quick)["status"] == "running"
    queue.finish(running, "success")
    queue.finish(short_quick, "success")
    queue.tick()
    assert queue.get(batch)["status"] == queue.get(long_quick)["status"] == "queued"
    host["locks"].write_text("")
    queue.tick()
    assert queue.get(batch)["status"] == queue.get(long_quick)["status"] == "running"


@pytest.mark.parametrize("options", [
    {"inode": 1}, {"blocked": False}, {"mode": "READ"},
])
def test_unrelated_or_nonwaiting_proc_locks_do_not_stop_admission(queue, host, options):
    write_legacy_waiter(host, **options)
    assert not queue.legacy_waiting()
    start(queue, "batch")


def records(q):
    path = q.home / "journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("result,exit_code,exit_status,status,code", [
    ("success", "exited", "0", "done", 0),
    ("success", "exited", "7", "failed", 7),
    ("exit-code", "exited", "2", "failed", 2),
    ("signal", "killed", "9", "failed", 137),
    ("timeout", "killed", "15", "timeout", 124),
])
def test_completion_reason_code_and_resources_are_journalled(
        queue, monkeypatch, result, exit_code, exit_status, status, code):
    ident = start(queue, mem=3 * GIB, gpu_mem=GIB, name="journal-name")
    monkeypatch.setattr(queue, "now", lambda: 1012.0)
    queue.finish(ident, result, exit_code, exit_status)
    job = queue.get(ident)
    assert (job["status"], job["code"]) == (status, code)
    submitted, started, finished = records(queue)
    assert [entry["event"] for entry in (submitted, started, finished)] == ["submitted", "started", "finished"]
    assert finished["id"] == ident
    assert finished["reason"] == status
    assert finished["code"] == code
    assert finished["duration"] == 12
    assert finished["wait"] == 0
    assert finished["owner"] == "session-a"
    assert finished["mem"] == 3 * GIB and finished["gpu_mem"] == GIB
    assert finished["name"] == "journal-name"
    queue.finish(ident, result, exit_code, exit_status)
    assert len(records(queue)) == 3, "repeated callbacks must not duplicate completion"


def test_cancellation_checks_owner_and_records_queued_request(queue, gq):
    ident = enqueue(queue, owner="owner-a")
    with pytest.raises(gq.QueueError, match="force"):
        queue.cancel(ident, "owner-b")
    assert queue.get(ident)["status"] == "queued"
    queue.cancel(ident, "owner-b", force=True)
    assert queue.get(ident)["status"] == "cancelled"
    finished = records(queue)[-1]
    assert finished["reason"] == "cancelled" and finished["code"] == 143


def test_run_wait_max_returns_75_and_cancels_request(queue, gq, monkeypatch, capsys):
    ident = enqueue(queue)
    monkeypatch.setattr(queue, "disk_free", lambda: 0)
    clock = [1000.0]
    monkeypatch.setattr(queue, "now", lambda: clock[0])
    monkeypatch.setattr(gq.select, "select", lambda *args: ([], [], []))
    monkeypatch.setattr(gq.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + 1))
    assert gq.wait_job(queue, ident, run=True, wait_max=2) == 75
    assert queue.get(ident)["status"] == "cancelled"
    assert "Ryzen занят" in capsys.readouterr().err
    assert clock[0] == 1002.0
    assert records(queue)[-1]["reason"] == "cancelled"


@pytest.mark.parametrize("transition", ["running", "finished"])
def test_wait_max_does_not_cancel_a_job_admitted_after_its_snapshot(
        queue, gq, monkeypatch, capsys, transition):
    ident = enqueue(queue)
    original_jobs = queue.jobs
    raced = False

    def jobs_then_admit():
        nonlocal raced
        snapshot = original_jobs()
        if not raced:
            raced = True
            with queue.transaction() as state:
                state["jobs"][0].update(status="starting", reserved=queue.now())
            assert queue.started(ident)
            if transition == "finished":
                queue.finish(ident, "exit-code", "exited", "7")
        return snapshot

    def finish_running_job(_seconds):
        queue.finish(ident, "exit-code", "exited", "7")

    monkeypatch.setattr(queue, "tick", lambda: None)
    monkeypatch.setattr(queue, "jobs", jobs_then_admit)
    monkeypatch.setattr(gq.select, "select", lambda *args: ([], [], []))
    monkeypatch.setattr(gq.time, "sleep", finish_running_job)
    assert gq.wait_job(queue, ident, run=True, wait_max=0) == 7
    assert raced and queue.get(ident)["status"] == "failed"
    assert "Ryzen занят" not in capsys.readouterr().err
    assert records(queue)[-1]["code"] == 7
    assert not any(record.get("reason") == "cancelled" for record in records(queue))


def test_eof_from_disconnected_ssh_cancels_waiting_run(queue, gq, monkeypatch):
    ident = enqueue(queue)
    monkeypatch.setattr(queue, "disk_free", lambda: 0)
    read_end, write_end = os.pipe()
    os.close(write_end)
    with os.fdopen(read_end) as ssh_stdin:
        monkeypatch.setattr(gq.sys, "stdin", ssh_stdin)
        assert gq.wait_job(queue, ident, run=True) == 143
    assert queue.get(ident)["status"] == "cancelled"
    assert records(queue)[-1]["reason"] == "cancelled"


@pytest.mark.parametrize("action", ["wait", "run"])
@pytest.mark.parametrize("transition", ["running", "cancelled"])
def test_wait_progress_survives_a_concurrent_admission_or_cancellation(
        queue, gq, monkeypatch, capsys, action, transition):
    ident = enqueue(queue)
    original_jobs = queue.jobs
    original_wait = gq.wait_job
    armed = action == "run"
    transitioned = False

    def racing_jobs():
        nonlocal transitioned
        snapshot = original_jobs()
        if armed and not transitioned:
            transitioned = True
            # Another scheduler changes the state just after this snapshot is read.
            if transition == "running":
                with queue.transaction() as state:
                    state["jobs"][0].update(status="starting", reserved=queue.now())
                assert queue.started(ident)
            else:
                queue.cancel(ident, "session-a")
        return snapshot

    def wait_with_race(*args, **kwargs):
        nonlocal armed
        armed = True
        return original_wait(*args, **kwargs)

    def complete_after_progress(_seconds):
        assert transitioned
        if transition == "running":
            queue.finish(ident, "success")

    monkeypatch.setattr(queue, "tick", lambda: None)
    monkeypatch.setattr(queue, "jobs", racing_jobs)
    monkeypatch.setattr(gq, "Queue", lambda: queue)
    monkeypatch.setattr(gq, "wait_job", wait_with_race)
    monkeypatch.setattr(gq.select, "select", lambda *args: ([], [], []))
    monkeypatch.setattr(gq.time, "sleep", complete_after_progress)
    code = gq.main(["wait", ident]) if action == "wait" else gq.wait_job(queue, ident, run=True)
    assert code == (0 if transition == "running" else 143)
    assert queue.get(ident)["status"] == ("done" if transition == "running" else "cancelled")
    assert records(queue)[-1]["reason"] == ("done" if transition == "running" else "cancelled")
    assert "list.index" not in capsys.readouterr().err


def make_queue(gq, host, monkeypatch):
    q = gq.Queue(home=host["home"], legacy_lock=host["legacy"], proc_locks=host["locks"])
    monkeypatch.setattr(q, "disk_free", lambda: 100 * GIB)
    # Lifecycle race tests deliberately reconcile immediately after each mutation.
    # The shared five-second cadence has its own test using unmodified Queue instances.
    monkeypatch.setattr(q, "claim_reconcile", lambda: True)
    return q


def test_dead_waiter_is_removed_but_detached_submit_is_preserved(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    monkeypatch.setattr(q, "disk_free", lambda: 0)
    dead = q.submit(spec(), waiter={"pid": 999999999, "start": "dead-process"})
    detached = q.submit(spec())
    q.tick()
    assert q.get(dead)["status"] == "cancelled"
    assert q.get(detached)["status"] == "queued"
    assert records(q)[-1]["id"] == dead
    assert records(q)[-1]["reason"] == "cancelled"


def systemd_state(host, ident, **overrides):
    path = host["systemd"] / f"gq-{ident}.json"
    state = json.loads(path.read_text())
    state.update(overrides)
    path.write_text(json.dumps(state))


def calls(host, tool):
    path = host["systemd"] / "calls.jsonl"
    return [record["args"] for record in map(json.loads, path.read_text().splitlines())
            if record["tool"] == tool]


@pytest.mark.parametrize("kind,weight,nice", [("quick", 100, 0), ("batch", 10, 19), ("quiet", 100, 0)])
def test_systemd_launch_sets_affinity_limits_runtime_and_stop_callback(gq, host, monkeypatch, kind, weight, nice):
    q = make_queue(gq, host, monkeypatch)
    ident = q.submit(spec(kind, mem=3 * GIB, maximum=40))
    q.tick()
    assert q.get(ident)["status"] == "starting"
    arguments = calls(host, "systemd-run")[0]
    assert "--user" in arguments
    assert arguments[arguments.index("--unit") + 1] == f"gq-{ident}"
    properties = dict(value.removeprefix("--property=").split("=", 1)
                      for value in arguments if value.startswith("--property="))
    assert properties["CPUAffinity"] == "2-15 18-31"
    assert "AllowedCPUs" not in properties
    assert properties["CPUWeight"] == str(weight)
    assert properties["Nice"] == str(nice)
    assert properties["MemoryMax"] == str(3 * GIB)
    assert properties["RuntimeMaxSec"] == "50s"
    assert properties["TimeoutStopSec"] == "60"
    assert properties["CollectMode"] == "inactive-or-failed"
    assert "_finish" in properties["ExecStopPost"] and ident in properties["ExecStopPost"]
    assert properties["StandardOutput"] == f"append:{q.home}/logs/{ident}.log"
    assert "_execute" in arguments and arguments[-1] == ident


def test_reconcile_recovers_systemd_timeout_without_waiter_or_callback(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(q, "now", lambda: clock[0])
    ident = q.submit(spec(maximum=1))
    q.tick()
    q.started(ident)
    systemd_state(host, ident, ActiveState="failed", Result="timeout", ExecMainCode="2", ExecMainStatus="15")
    clock[0] = 1006.0
    q.reconcile()
    assert q.get(ident)["status"] == "timeout"
    finished = records(q)[-1]
    assert finished["reason"] == "timeout" and finished["code"] == 124
    assert any("show" in args for args in calls(host, "systemctl"))


def test_cancel_running_unit_requests_stop_and_callback_records_cancelled(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    ident = q.submit(spec(owner="session-a"))
    q.tick()
    q.started(ident)
    q.cancel(ident, "session-a")
    stop = calls(host, "systemctl")[-1]
    assert "stop" in stop and f"gq-{ident}.service" in stop
    q.finish(ident, "signal", "killed", "15")
    assert q.get(ident)["status"] == "cancelled"
    assert records(q)[-1]["reason"] == "cancelled"


def test_cli_reports_a_failed_stop_and_reconcile_retries_until_it_stops(
        gq, host, monkeypatch, capsys):
    q = make_queue(gq, host, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(q, "now", lambda: clock[0])
    ident = q.submit(spec(owner="test-worktree"))
    q.tick()
    assert q.started(ident)
    original_systemctl = gq.systemctl
    stop_calls = []

    def fail_first_stop(*args):
        if "stop" in args:
            stop_calls.append(args)
            if len(stop_calls) == 1:
                return subprocess.CompletedProcess(args, 1, "", "fake stop refused")
        return original_systemctl(*args)

    monkeypatch.setattr(gq, "systemctl", fail_first_stop)
    monkeypatch.setattr(gq, "Queue", lambda: q)
    assert gq.main(["cancel", ident]) != 0
    error = capsys.readouterr().err
    assert "fake stop refused" in error and "stop" in error
    assert q.get(ident)["status"] == "running"
    assert q.get(ident)["cancel_reason"] == "cancelled"
    assert len(stop_calls) == 1

    clock[0] += 6
    q.reconcile()
    assert len(stop_calls) == 2
    state = json.loads((host["systemd"] / f"gq-{ident}.json").read_text())
    assert state["ActiveState"] == "inactive"
    q.reconcile()
    assert q.get(ident)["status"] == "cancelled"
    assert records(q)[-1]["reason"] == "cancelled"


def test_failed_systemd_launch_is_terminal_and_logged(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    monkeypatch.setenv("FAKE_SYSTEMD_FAIL", "1")
    ident = q.submit(spec())
    q.tick()
    assert q.get(ident)["status"] == "failed"
    assert "fake systemd launch failed" in (q.home / "logs" / f"{ident}.log").read_text()
    assert records(q)[-1]["reason"] == "failed"


def test_timed_out_launch_without_a_unit_releases_resources_and_finishes_once(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(q, "now", lambda: clock[0])
    monkeypatch.setattr(gq, "process_token", lambda _pid: "live-launcher")
    original_run = gq.subprocess.run
    timed_out = False

    def timeout_first_launch(args, **kwargs):
        nonlocal timed_out
        if args[0] == "systemd-run" and not timed_out:
            timed_out = True
            raise subprocess.TimeoutExpired(args, 15)
        return original_run(args, **kwargs)

    monkeypatch.setattr(gq.subprocess, "run", timeout_first_launch)
    ident = q.submit(spec("batch", threads=28, mem=20 * GIB))
    q.tick()
    clock[0] += 600
    q.tick()
    assert timed_out
    assert q.get(ident)["status"] in {"cancelled", "failed"}
    finished = [entry for entry in records(q) if entry["event"] == "finished"]
    assert len(finished) == 1 and finished[0]["id"] == ident
    assert not any(entry["event"] == "started" for entry in records(q))
    q.finish(ident, "signal", "killed", "15")
    replacement = q.submit(spec("batch", owner="replacement", threads=28, mem=20 * GIB))
    q.tick()
    assert q.get(replacement)["status"] == "starting"
    assert len([entry for entry in records(q) if entry["event"] == "finished"]) == 1


def test_timed_out_launch_with_a_live_unit_keeps_resources_until_stopped(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(q, "now", lambda: clock[0])
    monkeypatch.setattr(gq, "process_token", lambda _pid: "live-launcher")
    original_run = gq.subprocess.run
    original_systemctl = gq.systemctl
    timed_out = False
    stops = []

    def timeout_after_creating_unit(args, **kwargs):
        nonlocal timed_out
        result = original_run(args, **kwargs)
        if args[0] == "systemd-run" and not timed_out:
            timed_out = True
            raise subprocess.TimeoutExpired(args, 15)
        return result

    def stopping_asynchronously(*args):
        if "stop" in args:
            stops.append(args)
            # --no-block only requests a stop; the unit is still running.
            return subprocess.CompletedProcess(args, 0, "", "")
        return original_systemctl(*args)

    monkeypatch.setattr(gq.subprocess, "run", timeout_after_creating_unit)
    monkeypatch.setattr(gq, "systemctl", stopping_asynchronously)
    ident = q.submit(spec("batch", threads=28, mem=20 * GIB))
    q.tick()
    replacement = q.submit(spec("batch", owner="replacement", threads=28, mem=20 * GIB))
    clock[0] += 600
    q.tick()
    assert stops and q.get(ident)["status"] == "starting"
    assert q.get(replacement)["status"] == "queued"
    assert not any(entry["event"] == "finished" for entry in records(q))
    q.finish(ident, "signal", "killed", "15")
    q.tick()
    assert q.get(ident)["status"] == "cancelled"
    assert q.get(replacement)["status"] == "starting"
    finished = [entry for entry in records(q) if entry["event"] == "finished"]
    assert len(finished) == 1 and finished[0]["reason"] == "cancelled"


def test_exec_stop_post_callback_records_timeout_even_without_waiter(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    ident = q.submit(spec())
    q.tick()
    q.started(ident)
    env = {**host["env"], "SERVICE_RESULT": "timeout", "EXIT_CODE": "killed", "EXIT_STATUS": "15"}
    completed = subprocess.run([sys.executable, str(SCRIPT), "_finish", ident],
                               env=env, capture_output=True, text=True, timeout=5)
    assert completed.returncode == 0, completed.stderr
    assert q.get(ident)["status"] == "timeout"
    assert records(q)[-1]["reason"] == "timeout"


@pytest.mark.parametrize("kind,shared_allowed", [("quick", True), ("batch", True), ("quiet", False)])
def test_unit_process_holds_legacy_lock_until_command_finishes(gq, host, monkeypatch, kind, shared_allowed):
    q = make_queue(gq, host, monkeypatch)
    ready = host["home"].parent / "ready"
    release = host["home"].parent / "release"
    command = [sys.executable, "-c", (
        "from pathlib import Path; import sys,time; "
        "Path(sys.argv[1]).write_text('ready'); "
        "deadline=time.monotonic()+10; "
        "exec('while not Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.01)')"
    ), str(ready), str(release)]
    ident = q.submit(spec(kind, command=command))
    q.tick()
    process = subprocess.Popen([sys.executable, str(SCRIPT), "_execute", ident],
                               env=host["env"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), "unit helper did not start the command"
        assert q.get(ident)["status"] == "running"
        with host["legacy"].open("a+") as old_client:
            with pytest.raises(BlockingIOError):
                fcntl.flock(old_client, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with host["legacy"].open("a+") as new_client:
            if shared_allowed:
                fcntl.flock(new_client, fcntl.LOCK_SH | fcntl.LOCK_NB)
            else:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(new_client, fcntl.LOCK_SH | fcntl.LOCK_NB)
    finally:
        release.touch()
        stdout, stderr = process.communicate(timeout=12)
    assert process.returncode == 0, stderr
    with host["legacy"].open("a+") as old_client:
        fcntl.flock(old_client, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("when", ["before-popen", "after-popen"])
@pytest.mark.parametrize("cancel_request", [False, True])
def test_stop_during_spawn_prevents_the_user_command_from_running(
        gq, host, monkeypatch, when, cancel_request):
    q = make_queue(gq, host, monkeypatch)
    marker = host["home"].parent / "ran-after-stop"
    command = [sys.executable, "-c", (
        "from pathlib import Path; import sys,time; "
        "time.sleep(.3); Path(sys.argv[1]).write_text('ran')"
    ), str(marker)]
    ident = q.submit(spec(command=command))
    q.tick()
    original_popen = subprocess.Popen
    children = []
    stopped = False

    def request_stop():
        nonlocal stopped
        stopped = True
        if cancel_request:
            assert q.cancel(ident, "session-a")
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler), "the unit helper must handle TERM while it owns the lock"
        handler(signal.SIGTERM, None)

    def spawning_with_stop(*args, **kwargs):
        # Deliver TERM after handler installation, immediately around the actual
        # spawn. The child does not exist yet or is not assigned in execute yet.
        if when == "before-popen":
            request_stop()
        child = original_popen(*args, **kwargs)
        children.append(child)
        if when == "after-popen":
            request_stop()
        return child

    monkeypatch.setattr(gq, "systemctl", lambda *args: subprocess.CompletedProcess(args, 0, "", ""))
    monkeypatch.setattr(gq.subprocess, "Popen", spawning_with_stop)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        assert gq.execute(q, ident) == 143
        assert stopped
        assert not marker.exists(), "a user command ran after cancellation/TERM during spawn"
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def test_execute_keeps_the_user_commands_nonzero_exit_code(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    ident = q.submit(spec(command=[sys.executable, "-c", "raise SystemExit(7)"]))
    q.tick()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        assert gq.execute(q, ident) == 7
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def test_execute_forwards_term_to_a_command_that_has_already_started(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    ready = host["home"].parent / "command-ready"
    marker = host["home"].parent / "command-survived-term"
    command = [sys.executable, "-c", (
        "from pathlib import Path; import sys,time; "
        "Path(sys.argv[1]).touch(); time.sleep(.6); Path(sys.argv[2]).touch()"
    ), str(ready), str(marker)]
    ident = q.submit(spec(command=command))
    q.tick()
    helper = subprocess.Popen([sys.executable, str(SCRIPT), "_execute", ident],
                              env=host["env"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and helper.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), "the command did not start"
        helper.terminate()
        _stdout, stderr = helper.communicate(timeout=5)
        assert helper.returncode == 143, stderr
        assert not marker.exists(), "TERM reached the helper but not its command"
    finally:
        if helper.poll() is None:
            helper.kill()
        helper.communicate(timeout=5)


def test_owner_and_default_submission_are_stable_from_worktree_subdirectories(
        queue, gq, monkeypatch, tmp_path):
    worktree = tmp_path / "worktrees" / "session-a" / "gothic"
    subdirectory = worktree / "server"
    subdirectory.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /unused/worktrees/session-a\n")
    monkeypatch.delenv("GQ_OWNER", raising=False)
    monkeypatch.chdir(worktree)
    request = spec()
    request.pop("owner")
    ident = queue.submit(request)
    assert queue.get(ident)["owner"] == gq.owner() == "session-a"
    monkeypatch.chdir(subdirectory)
    assert gq.owner() == "session-a"
    assert queue.cancel(ident, gq.owner())
    assert queue.get(ident)["status"] == "cancelled"


def test_worktrees_with_the_same_repository_basename_have_distinct_owners(gq, monkeypatch, tmp_path):
    monkeypatch.delenv("GQ_OWNER", raising=False)
    owners = []
    for session in ("session-a", "session-b"):
        root = tmp_path / "worktrees" / session / "gothic"
        (root / "server").mkdir(parents=True)
        (root / ".git").write_text(f"gitdir: /unused/worktrees/{session}\n")
        monkeypatch.chdir(root / "server")
        owners.append(gq.owner())
    assert owners == ["session-a", "session-b"]


def test_owner_uses_repository_root_for_a_regular_checkout(gq, monkeypatch, tmp_path):
    root = tmp_path / "my-repository"
    (root / ".git").mkdir(parents=True)
    (root / "server" / "nested").mkdir(parents=True)
    monkeypatch.delenv("GQ_OWNER", raising=False)
    monkeypatch.chdir(root / "server" / "nested")
    assert gq.owner() == "my-repository"


def test_explicit_owner_overrides_worktree_identity_for_submission(queue, gq, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GQ_OWNER", "explicit-session")
    request = spec()
    request.pop("owner")
    ident = queue.submit(request)
    assert gq.owner() == queue.get(ident)["owner"] == "explicit-session"


def test_owner_uses_source_worktree_when_called_from_outside_a_repository(gq, monkeypatch, tmp_path):
    root = tmp_path / "worktrees" / "source-session" / "gothic"
    (root / "scripts").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.delenv("GQ_OWNER", raising=False)
    monkeypatch.setattr(gq, "SCRIPT", str(root / "scripts" / "gq.py"))
    monkeypatch.chdir(elsewhere)
    assert gq.owner() == "source-session"


def test_installed_queue_requires_owner_when_no_worktree_can_be_identified(
        queue, gq, monkeypatch, tmp_path):
    installed = tmp_path / "gothic-ai" / "bin"
    installed.mkdir(parents=True)
    monkeypatch.delenv("GQ_OWNER", raising=False)
    monkeypatch.setattr(gq, "SCRIPT", str(installed / "gq"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(gq.QueueError, match="GQ_OWNER"):
        gq.owner()
    # Explicit callers already supplied ownership; do not evaluate the fallback.
    ident = queue.submit(spec(owner="explicit-session"))
    assert queue.get(ident)["owner"] == "explicit-session"


def test_format_version_mismatch_refuses_to_modify_state(gq, host):
    host["home"].mkdir()
    original = json.dumps({"version": gq.FORMAT_VERSION + 1, "jobs": []})
    (host["home"] / "state.json").write_text(original)
    with pytest.raises(gq.QueueError, match="версия формата"):
        gq.Queue(home=host["home"], legacy_lock=host["legacy"], proc_locks=host["locks"])
    assert (host["home"] / "state.json").read_text() == original


def test_estimate_adds_ahead_limits_and_uses_same_name_median(queue, monkeypatch):
    for duration in (10, 30, 20):
        ident = start(queue, name="repeat")
        monkeypatch.setattr(queue, "now", lambda duration=duration: 1000.0 + duration)
        queue.finish(ident, "success")
        monkeypatch.setattr(queue, "now", lambda: 1000.0)
    start(queue, name="other", maximum=100)
    enqueue(queue, name="ahead", maximum=50)
    target = enqueue(queue, name="repeat", maximum=40)
    monkeypatch.setattr(queue, "now", lambda: 1010.0)
    assert queue.estimate(target) == {"upper": 170, "typical": 20}


def test_cli_requires_declared_maximum(gq):
    with pytest.raises(SystemExit) as failure:
        gq.parser().parse_args(["run", "--class", "quick", "--threads", "4", "--mem", "1G", "--name", "missing", "--", "true"])
    assert failure.value.code == 2


def test_wait_streams_log_and_returns_command_exit_code(queue, gq, capsys):
    ident = start(queue)
    (queue.home / "logs" / f"{ident}.log").write_text("command output\n")
    queue.finish(ident, "exit-code", "exited", "7")
    assert gq.wait_job(queue, ident) == 7
    assert capsys.readouterr().out == "command output\n"


def test_status_exposes_owner_class_resources_limit_and_estimate(queue, gq, capsys):
    ident = start(queue, "batch", owner="visible-owner", threads=12, mem=8 * GIB,
                  gpu_mem=2 * GIB, display=True, maximum=90)
    gq.status(queue)
    output = capsys.readouterr().out
    assert ident in output and "visible-owner" in output
    assert "batch" in output and "running" in output
    assert "12t 8G gpu=2G окно=1" in output
    assert "90s" in output and "ОЦЕНКА" in output


def test_fairness_only_counts_other_sessions_still_waiting(queue):
    first = enqueue(queue, "batch", owner="session-a", threads=8)
    other = enqueue(queue, "batch", owner="session-b", threads=8)
    second = enqueue(queue, "batch", owner="session-a", threads=8)
    queue.tick()
    assert all(queue.get(ident)["status"] == "running" for ident in (first, other, second))


def test_reconcile_recovers_missing_unit_after_launcher_died(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(q, "now", lambda: clock[0])
    ident = q.submit(spec())
    q.tick()
    with q.transaction() as state:
        state["jobs"][0]["launcher"] = {"pid": 999999999, "start": "dead"}
    (host["systemd"] / f"gq-{ident}.json").unlink()
    clock[0] += 6
    q.reconcile()
    assert q.get(ident)["status"] == "failed"


def test_live_launcher_keeps_pending_reservations_during_sequential_launches(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(q, "now", lambda: clock[0])
    monkeypatch.setattr(gq, "process_token", lambda _pid: "live-launcher")
    original_launch = q.launch
    starts = []

    def slow_launch(job):
        q.reconcile()
        original_launch(job)
        starts.append(q.started(job["id"]))
        clock[0] += 11

    monkeypatch.setattr(q, "launch", slow_launch)
    ids = [q.submit(spec(mem=GIB)) for _ in range(4)]
    q.tick()
    assert clock[0] == 1044.0
    assert starts == [True] * 4
    assert all(q.get(ident)["status"] == "running" for ident in ids)
    assert len(calls(host, "systemd-run")) == 4
    assert not any(record.get("reason") == "failed" for record in records(q))


def test_missing_running_unit_is_recovered_even_while_launcher_lives(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(q, "now", lambda: clock[0])
    monkeypatch.setattr(gq, "process_token", lambda _pid: "live-launcher")
    ident = q.submit(spec())
    q.tick()
    assert q.started(ident)
    (host["systemd"] / f"gq-{ident}.json").unlink()
    clock[0] += 6
    q.reconcile()
    assert q.get(ident)["status"] == "failed"
    assert records(q)[-1]["reason"] == "failed"


def test_follow_logs_reads_output_written_before_terminal_state(queue, gq, monkeypatch, capsys):
    ident = start(queue)
    log = queue.home / "logs" / f"{ident}.log"
    log.write_text("first\n")
    original_stream = gq.stream_log
    completed = False

    def completing_stream(q, job_id, offset):
        nonlocal completed
        offset = original_stream(q, job_id, offset)
        if not completed:
            completed = True
            with log.open("a") as output:
                output.write("last\n")
            q.finish(job_id, "success")
        return offset

    monkeypatch.setattr(gq, "Queue", lambda: queue)
    monkeypatch.setattr(gq, "stream_log", completing_stream)
    assert gq.main(["logs", ident, "-f"]) == 0
    assert capsys.readouterr().out == "first\nlast\n"


def test_legacy_retry_waits_for_prior_exec_stop_post_to_unload(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    ident = q.submit(spec())
    q.tick()
    with q.transaction() as state:
        state["jobs"][0]["retry"] = True
    q.finish(ident, "success")
    q.tick()
    assert q.get(ident)["status"] == "queued"
    assert len(calls(host, "systemd-run")) == 1
    (host["systemd"] / f"gq-{ident}.json").unlink()
    q.tick()
    assert q.get(ident)["status"] == "starting"
    assert len(calls(host, "systemd-run")) == 2


@pytest.mark.parametrize("readmit", [False, True, "same-clock"])
def test_stale_reconcile_completion_cannot_finish_a_deferred_or_readmitted_job(
        gq, host, monkeypatch, readmit):
    q = make_queue(gq, host, monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(q, "now", lambda: clock[0])
    ident = q.submit(spec())
    q.tick()
    with q.transaction() as state:
        state["jobs"][0]["retry"] = True
    clock[0] += 6
    original_systemctl = gq.systemctl
    raced = False

    def show_after_stop_callback(*args):
        nonlocal raced
        if "show" in args and not raced:
            raced = True
            # The old attempt lost the legacy-lock race. ExecStopPost updates
            # the queue while another process is inspecting that same unit.
            q.finish(ident, "success")
            if readmit:
                (host["systemd"] / f"gq-{ident}.json").unlink()
                observed_at = clock[0]
                # A clock adjustment can give the next reservation the same timestamp;
                # its distinct attempt must still invalidate the stale observation.
                clock[0] = 1000.0 if readmit == "same-clock" else observed_at + 1
                q.tick()
                clock[0] = observed_at + 1
                assert q.get(ident)["status"] == "starting"
            return subprocess.CompletedProcess(args, 0, (
                "LoadState=loaded\nActiveState=inactive\nResult=success\n"
                "ExecMainCode=1\nExecMainStatus=0\n"), "")
        return original_systemctl(*args)

    monkeypatch.setattr(gq, "systemctl", show_after_stop_callback)
    q.reconcile()
    job = q.get(ident)
    assert raced
    assert job["status"] == ("starting" if readmit else "queued")
    if not readmit:
        assert job["retry_pending"]
    assert [entry["event"] for entry in records(q)] == ["submitted", "deferred"]
    if not readmit:
        (host["systemd"] / f"gq-{ident}.json").unlink()
        q.tick()
    assert q.started(ident)
    q.finish(ident, "success")
    assert q.get(ident)["status"] == "done"
    assert [entry["event"] for entry in records(q)] == ["submitted", "deferred", "started", "finished"]
    assert len(calls(host, "systemd-run")) == 2


def test_stale_unloaded_observation_does_not_clear_a_new_attempts_retry(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    monkeypatch.setattr(q, "now", lambda: 1000.0)
    ident = q.submit(spec())
    q.tick()

    def defer_current_attempt():
        with q.transaction() as state:
            state["jobs"][0]["retry"] = True
        q.finish(ident, "success")

    defer_current_attempt()
    old_attempt = q.get(ident)["attempt"]
    original_systemctl = gq.systemctl
    raced = False

    def show_after_next_attempt_deferred(*args):
        nonlocal raced
        if "show" in args and not raced:
            raced = True
            (host["systemd"] / f"gq-{ident}.json").unlink()
            q.tick()
            assert q.get(ident)["status"] == "starting"
            defer_current_attempt()
            systemd_state(host, ident, ActiveState="deactivating", SubState="stop-post")
            return subprocess.CompletedProcess(args, 0, "LoadState=not-found\n", "")
        return original_systemctl(*args)

    monkeypatch.setattr(gq, "systemctl", show_after_next_attempt_deferred)
    q.reconcile()
    current = q.get(ident)
    assert raced and current["attempt"] != old_attempt
    assert current["status"] == "queued" and current["retry_pending"]
    q.tick()
    assert q.get(ident)["status"] == "queued"
    assert len(calls(host, "systemd-run")) == 2
    assert [entry["event"] for entry in records(q)] == ["submitted", "deferred", "deferred"]


def test_deferred_batch_keeps_fifo_while_quick_can_run(gq, host, monkeypatch):
    q = make_queue(gq, host, monkeypatch)
    first = q.submit(spec("batch", owner="first", threads=28))
    q.tick()
    with q.transaction() as state:
        state["jobs"][0]["retry"] = True
    q.finish(first, "success")
    systemd_state(host, first, ActiveState="deactivating", SubState="stop-post")
    second = q.submit(spec("batch", owner="second", threads=28))
    quick = q.submit(spec())
    q.tick()
    assert q.get(first)["status"] == "queued" and q.get(first)["retry_pending"]
    assert q.get(second)["status"] == "queued"
    assert q.get(quick)["status"] == "starting"
    assert q.started(quick)
    q.finish(quick, "success")
    (host["systemd"] / f"gq-{first}.json").unlink()
    q.tick()
    assert q.get(first)["status"] == "starting"
    assert q.get(second)["status"] == "queued"
    assert q.started(first)
    q.finish(first, "success")
    q.tick()
    assert q.get(second)["status"] == "starting"
    assert q.started(second)
    started = [entry["id"] for entry in records(q) if entry["event"] == "started"]
    assert started == [quick, first, second]


def test_unit_lock_holder_detects_orphaned_children_in_its_own_cgroup(gq, tmp_path):
    membership = tmp_path / "membership"
    group = tmp_path / "user.slice" / "gq-abc.service"
    group.mkdir(parents=True)
    membership.write_text("0::/user.slice/gq-abc.service\n")
    (group / "cgroup.procs").write_text(f"{os.getpid()}\n999999\n")
    assert gq.unit_has_children("abc", membership, tmp_path)
    assert not gq.unit_has_children("another-unit", membership, tmp_path)
    (group / "cgroup.procs").write_text(f"{os.getpid()}\n")
    assert not gq.unit_has_children("abc", membership, tmp_path)
    (group / "cgroup.procs").unlink()
    assert gq.unit_has_children("abc", membership, tmp_path), "unreadable unit fails closed"


def test_reconcile_prunes_a_thousand_old_jobs_but_wait_and_logs_use_the_archive(
        gq, host, monkeypatch, capsys):
    q = gq.Queue(home=host["home"], legacy_lock=host["legacy"], proc_locks=host["locks"])
    now = 200000.0
    monkeypatch.setattr(q, "now", lambda: now)
    monkeypatch.setattr(q, "disk_free", lambda: 100 * GIB)
    seed = q.submit(spec())
    template = q.get(seed)
    old_end = now - 24 * 3600 - 1
    old_jobs = [dict(template, id=f"old-{index:04d}", status="failed", reason="failed",
                     code=7, submitted=old_end - 10, start=old_end - 5, end=old_end,
                     env={"ONLY_OLD_JOB_ENV": "x" * 1000}) for index in range(1000)]
    recent = dict(template, id="recent", status="done", reason="done", code=0, end=now - 60)
    with q.transaction() as state:
        state["jobs"] = [*old_jobs, recent]
    archived = old_jobs[0]
    journal = dict(archived, event="finished", time=old_end)
    journal.pop("env")
    journal_path = q.home / "journal.jsonl"
    journal_path.write_text(json.dumps(journal) + "\n")
    log_path = q.home / "logs" / f"{archived['id']}.log"
    log_path.write_text("archived output\n")

    q.reconcile()
    assert [job["id"] for job in q.jobs()] == ["recent"]
    assert "ONLY_OLD_JOB_ENV" not in (q.home / "state.json").read_text()
    assert q.get(archived["id"])["code"] == 7
    assert journal_path.read_text() == json.dumps(journal) + "\n"
    assert log_path.read_text() == "archived output\n"

    monkeypatch.setattr(gq, "Queue", lambda: q)
    assert gq.main(["wait", archived["id"]]) == 7
    assert capsys.readouterr().out == "archived output\n"
    assert gq.main(["logs", archived["id"]]) == 0
    assert capsys.readouterr().out == "archived output\n"


def test_logs_can_read_a_preserved_file_without_a_state_or_journal_entry(queue, gq, monkeypatch, capsys):
    ident = "logs-only"
    (queue.home / "logs" / f"{ident}.log").write_text("preserved log\n")
    monkeypatch.setattr(gq, "Queue", lambda: queue)
    assert gq.main(["logs", ident]) == 0
    assert capsys.readouterr().out == "preserved log\n"


def test_ten_ticks_in_five_seconds_reconcile_each_active_job_once_across_processes(
        gq, host, monkeypatch):
    queues = [gq.Queue(home=host["home"], legacy_lock=host["legacy"], proc_locks=host["locks"])
              for _ in range(2)]
    clock = [1000.0]
    for q in queues:
        monkeypatch.setattr(q, "now", lambda: clock[0])
        monkeypatch.setattr(q, "disk_free", lambda: 100 * GIB)
    first = queues[0].submit(spec())
    second = queues[0].submit(spec())
    queues[0].tick()
    assert queues[0].started(first) and queues[0].started(second)

    for index in range(10):
        clock[0] = 1005.0 + index / 2
        queues[index % 2].tick()
    shows = [args for args in calls(host, "systemctl") if "show" in args]
    assert len(shows) == 2
    for ident in (first, second):
        assert sum(f"gq-{ident}.service" in args for args in shows) == 1

    clock[0] = 1010.0
    queues[1].tick()
    assert len([args for args in calls(host, "systemctl") if "show" in args]) == 4


@pytest.mark.parametrize("action", ["wait", "_pump", "logs"])
def test_waiters_lower_priority_once_and_poll_queued_jobs_once_per_second(
        queue, gq, monkeypatch, action):
    ident = enqueue(queue)
    priorities = []
    sleeps = []

    def cancel_after_sleep(seconds):
        sleeps.append(seconds)
        queue.cancel(ident, "session-a")

    monkeypatch.setattr(queue, "tick", lambda: None)
    monkeypatch.setattr(gq, "Queue", lambda: queue)
    monkeypatch.setattr(gq.os, "nice", lambda increment: priorities.append(increment))
    monkeypatch.setattr(gq.time, "sleep", cancel_after_sleep)
    args = [action, ident] + (["-f"] if action == "logs" else [])
    assert gq.main(args) == (143 if action == "wait" else 0)
    assert sleeps == [1]
    assert priorities == [19]


@pytest.mark.parametrize("source", ["environment", "file", "missing"])
def test_profile_selection_uses_environment_then_private_file_otherwise_errors(
        gq, host, monkeypatch, tmp_path, source):
    private_home = tmp_path / "profile-home"
    private_home.mkdir()
    monkeypatch.setenv("HOME", str(private_home))
    profile_path = private_home / "gothic-ai" / "bin" / "gq.profile"
    if source == "environment":
        monkeypatch.setenv("GQ_PROFILE", "ryzen")
        profile_path.parent.mkdir(parents=True)
        profile_path.write_text("file-must-not-override-environment\n")
    else:
        monkeypatch.delenv("GQ_PROFILE", raising=False)
        if source == "file":
            profile_path.parent.mkdir(parents=True)
            profile_path.write_text("ryzen\n")

    kwargs = dict(home=host["home"], legacy_lock=host["legacy"], proc_locks=host["locks"])
    if source == "missing":
        with pytest.raises(gq.QueueError, match="GQ_PROFILE|gq\\.profile"):
            gq.Queue(**kwargs)
    else:
        q = gq.Queue(**kwargs)
        assert q.profile_name == "ryzen" and q.profile == gq.PROFILES["ryzen"]


def test_install_copies_executable_and_private_profile_to_remote_home(gq, monkeypatch, tmp_path):
    wrapper_path = SCRIPT.with_name("gq")
    loader = SourceFileLoader("gq_wrapper_test_module", str(wrapper_path))
    module_spec = importlib.util.spec_from_loader(loader.name, loader)
    wrapper = importlib.util.module_from_spec(module_spec)
    loader.exec_module(wrapper)
    remote_home = tmp_path / "remote-home"
    remote_home.mkdir()
    ssh_calls = []

    def local_ssh(args, *, stdin):
        ssh_calls.append(args)
        result = subprocess.run(["sh", "-c", args[-1]], stdin=stdin,
                                env={**os.environ, "HOME": str(remote_home)},
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        return result.returncode

    monkeypatch.delenv("GQ_PROFILE", raising=False)
    monkeypatch.setattr(wrapper.subprocess, "call", local_ssh)
    assert wrapper.install("ryzen") == 0
    assert len(ssh_calls) == 1 and ssh_calls[0][-2] == "ryzen"
    installed = remote_home / "gothic-ai" / "bin"
    assert (installed / "gq").read_bytes() == SCRIPT.read_bytes()
    assert os.access(installed / "gq", os.X_OK)
    assert (installed / "gq.profile").read_text() == "ryzen\n"
    assert sorted(path.name for path in installed.iterdir()) == ["gq", "gq.profile"]


def test_linked_worktrees_with_matching_directory_names_use_their_git_identity(gq, monkeypatch, tmp_path):
    monkeypatch.delenv('GQ_OWNER', raising=False)
    owners = []
    for session in ('linked-a', 'linked-b'):
        root = tmp_path / 'checkouts' / session / 'gothic'
        (root / 'server').mkdir(parents=True)
        gitdir = tmp_path / 'common.git' / 'worktrees' / session
        (root / '.git').write_text(f'gitdir: {gitdir}\n')
        monkeypatch.chdir(root / 'server')
        owners.append(gq.owner())
    assert owners == ['linked-a', 'linked-b']
