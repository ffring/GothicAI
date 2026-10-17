"""Admission and lifecycle checks; never touch the machine's real queue or units."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
from pathlib import Path
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
