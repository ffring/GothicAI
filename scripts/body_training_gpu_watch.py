#!/usr/bin/env python3
"""Bound one explicit user train-cuda unit, including CUDA context memory.

This helper runs OUTSIDE the training unit on dev-ubuntu. It uses only stdlib;
no model is loaded. Its output never includes unrelated process identities.
The launcher must wait for this process and propagate its nonzero exit status.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import datetime as dt
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Callable


INTERVAL_SECONDS = 2
MAX_OWN_GPU_MIB = 1536
MIN_GPU_FREE_MIB = 2048
GQ_UNIT_RE = re.compile(r"gq-([0-9a-f]{12})\.service\Z")
TERMINAL = {"done", "cancelled", "timeout", "failed"}
UNIT_RE = re.compile(r"(?:gothic-body-bc-[A-Za-z0-9][A-Za-z0-9_-]*-train-cuda|gq-[0-9a-f]{12})\.service\Z")


def exact_unit(value: str) -> str:
    if not UNIT_RE.fullmatch(value):
        raise ValueError("Expected exact gothic-body-bc-<run>-train-cuda.service or gq-<id>.service user unit")
    return value


def command(argv: list[str]) -> str:
    result = subprocess.run(argv, text=True, capture_output=True, timeout=5, check=False)
    if result.returncode:
        # Never expose raw systemctl/nvidia-smi diagnostics containing other PIDs.
        raise RuntimeError(f"{argv[0]} failed (exit {result.returncode})")
    return result.stdout


@dataclass(frozen=True)
class UnitState:
    active: str
    cgroup: str
    invocation: str


def unit_state(unit: str, run: Callable[[list[str]], str] = command) -> UnitState | None:
    exact_unit(unit)
    try:
        text = run(["systemctl", "--user", "show", unit, "--property=Id,LoadState,ActiveState,ControlGroup,InvocationID"])
    except RuntimeError:
        # Before systemd-run registers the unit, some systemd versions make
        # `show` fail. Verify absence through an exact-name-only listing;
        # connection/permission failures still fail closed.
        listing = run(["systemctl", "--user", "list-units", unit, "--all", "--plain", "--no-legend", "--no-pager"])
        if not listing.strip():
            return None
        raise
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    if fields.get("LoadState") == "not-found":
        return None
    if fields.get("Id") != unit:
        raise RuntimeError("User unit identity mismatch")
    return UnitState(fields.get("ActiveState", ""), fields.get("ControlGroup", ""), fields.get("InvocationID", ""))


def own_cgroup(unit: str, control_group: str, *, uid: int | None = None,
               root: Path = Path("/sys/fs/cgroup")) -> Path:
    exact_unit(unit)
    uid = os.getuid() if uid is None else uid
    parts = PurePosixPath(control_group).parts
    if (not control_group.startswith("/") or ".." in parts or
            parts[-1:] != (unit,) or f"user-{uid}.slice" not in parts or
            f"user@{uid}.service" not in parts):
        raise RuntimeError("Training unit cgroup is outside this user's exact unit")
    candidate = root.joinpath(*parts[1:])
    # Cgroup paths must not redirect us to another service via a symlink.
    if candidate.resolve() != candidate.absolute():
        raise RuntimeError("Training cgroup path redirects outside its declared unit")
    return candidate


def cgroup_pids(path: Path) -> set[int]:
    pids: set[int] = set()
    if not path.is_dir():
        raise FileNotFoundError("Training cgroup disappeared")
    for directory, _, _ in os.walk(path, followlinks=False):
        try:
            values = (Path(directory) / "cgroup.procs").read_text().split()
        except FileNotFoundError:
            # A transient nested cgroup may disappear as an own child exits.
            if Path(directory) == path:
                raise
            continue
        for value in values:
            pid = int(value)
            if pid <= 0:
                raise RuntimeError("Invalid training cgroup process identity")
            pids.add(pid)
    return pids


def gpu_sample(own_pids: set[int], run: Callable[[list[str]], str] = command) -> dict:
    rows = list(csv.reader(io.StringIO(run([
        "nvidia-smi", "--id=0", "--query-gpu=uuid,memory.free", "--format=csv,noheader,nounits"]))))
    if len(rows) != 1 or len(rows[0]) != 2:
        raise RuntimeError("Expected exactly one GPU0 memory snapshot")
    uuid, raw_free = (value.strip() for value in rows[0])
    free = int(raw_free)
    if not uuid.startswith("GPU-") or free < 0:
        raise RuntimeError("Invalid GPU0 memory snapshot")
    own_usage: dict[int, int] = {}
    processes = run(["nvidia-smi", "--id=0", "--query-compute-apps=gpu_uuid,pid,used_gpu_memory",
                     "--format=csv,noheader,nounits"])
    for row in csv.reader(io.StringIO(processes)):
        if not row or not any(value.strip() for value in row):
            continue
        if len(row) != 3:
            raise RuntimeError("Invalid GPU process memory snapshot")
        process_uuid, raw_pid, raw_memory = (value.strip() for value in row)
        pid = int(raw_pid)
        if process_uuid != uuid or pid not in own_pids:
            continue
        memory = int(raw_memory)
        if memory < 0:
            raise RuntimeError("Own GPU memory unavailable")
        # Avoid counting a duplicate process listing twice.
        own_usage[pid] = max(memory, own_usage.get(pid, 0))
    return {"gpu_index": 0, "gpu_free_mib": free,
            "own_gpu_mib": sum(own_usage.values()), "own_gpu_process_count": len(own_usage),
            "own_cgroup_process_count": len(own_pids)}


def budget_reasons(sample: dict) -> list[str]:
    reasons = []
    if sample["own_gpu_mib"] > MAX_OWN_GPU_MIB:
        reasons.append("own_gpu_memory_above_1536_mib")
    if sample["gpu_free_mib"] < MIN_GPU_FREE_MIB:
        reasons.append("gpu_free_memory_below_2048_mib")
    return reasons


class InvocationChanged(RuntimeError):
    """The sample no longer identifies the current unit invocation."""


@dataclass(frozen=True)
class QueueJobState:
    status: str
    attempt: str | None
    invocation: str | None
    start: float | None


def queue_job_state(home: Path, unit: str) -> QueueJobState:
    """Read gq's atomically replaced snapshot without mutating its queue."""
    match = GQ_UNIT_RE.fullmatch(unit)
    if match is None:
        raise ValueError("Queue monitoring requires an exact gq-<id>.service unit")
    state = json.loads((home / "state.json").read_text())
    if state.get("version") != 1:
        raise RuntimeError("Unsupported gq state version")
    jobs = [job for job in state["jobs"] if job["id"] == match[1]]
    if len(jobs) != 1:
        raise RuntimeError("Own gq job is missing from queue state")
    job = jobs[0]
    if job["status"] not in {"queued", "starting", "running", *TERMINAL}:
        raise RuntimeError("Unknown own gq job state")
    attempt, invocation = job.get("attempt"), job.get("invocation_id")
    if job["status"] == "running" and not all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value)
            for value in (attempt, invocation)):
        raise RuntimeError("Running gq job has no admission identity")
    return QueueJobState(job["status"], attempt, invocation, job.get("start"))


def stop_exact_invocation(unit: str, expected: UnitState,
                          run: Callable[[list[str]], str] = command) -> bool:
    exact_unit(unit)
    current = unit_state(unit, run)
    if current is None or current.active not in {"active", "activating", "deactivating"}:
        return False
    own_cgroup(unit, current.cgroup)
    if not expected.invocation or (current.invocation, current.cgroup) != (expected.invocation, expected.cgroup):
        raise InvocationChanged("Refusing to stop a changed training invocation")
    run(["systemctl", "--user", "stop", unit])
    return True


def watch(args: argparse.Namespace) -> int:
    unit = exact_unit(args.unit)
    output = Path(args.output)
    summary_path = Path(args.summary)
    done = Path(args.job_done)
    queue_home = getattr(args, "gq_home", None)
    queue_home = Path(queue_home).expanduser().resolve() if queue_home is not None else None
    if bool(GQ_UNIT_RE.fullmatch(unit)) != (queue_home is not None):
        raise ValueError("gq units require --gq-home; legacy units must not use it")
    if done.exists() and queue_home is None:
        raise RuntimeError("Launcher completion marker already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    expected: UnitState | None = None
    expected_attempt: str | None = None
    admission: tuple[str | None, str | None] | None = None
    admission_since = started
    never_started = False
    summary = {"schema_version": 1, "unit": unit, "sample_interval_seconds": INTERVAL_SECONDS,
               "max_own_gpu_mib": MAX_OWN_GPU_MIB, "min_gpu_free_mib": MIN_GPU_FREE_MIB,
               "samples": 0, "peak_own_gpu_mib": 0, "minimum_gpu_free_mib": None,
               "stop_requested": False, "status": "starting"}
    exit_code = 0
    with output.open("x") as stream:
        def event(kind: str, **fields) -> None:
            stream.write(json.dumps({"kind": kind, "recorded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                                     "elapsed_seconds": round(time.monotonic()-started, 6), "unit": unit,
                                     **fields}, allow_nan=False, sort_keys=True) + "\n")
            stream.flush()
        event("start", max_own_gpu_mib=MAX_OWN_GPU_MIB, min_gpu_free_mib=MIN_GPU_FREE_MIB,
              sample_interval_seconds=INTERVAL_SECONDS)
        try:
            def queue_matches(job: QueueJobState) -> bool:
                return queue_job_state(queue_home, unit) == job

            while True:
                job = queue_job_state(queue_home, unit) if queue_home is not None else None
                if job is not None:
                    if job.status in TERMINAL or done.exists():
                        never_started = job.start is None
                        break
                    # A systemd unit may be active before _execute admits the
                    # command. A lost legacy-lock race returns it to queued,
                    # with a later attempt using the same name and a new unit.
                    if job.status != "running":
                        time.sleep(INTERVAL_SECONDS)
                        continue
                    identity = (job.attempt, job.invocation)
                    if identity != admission:
                        admission, admission_since = identity, time.monotonic()
                state = unit_state(unit)
                if state is not None and state.active in {"active", "activating"} and state.cgroup:
                    own_cgroup(unit, state.cgroup)
                    if not re.fullmatch(r"[0-9a-f]{32}", state.invocation):
                        raise RuntimeError("Training invocation identity unavailable")
                    if job is not None and state.invocation != job.invocation:
                        # Queue state and systemd are separate snapshots. Wait
                        # through an old unit's teardown; never sample that unit
                        # as the newly admitted command.
                        if time.monotonic() - admission_since >= args.startup_timeout_seconds:
                            raise RuntimeError("Admitted gq invocation did not appear before monitor startup timeout")
                        time.sleep(INTERVAL_SECONDS)
                        continue
                    if expected is None:
                        expected = state
                        expected_attempt = job.attempt if job is not None else None
                        summary["invocation_id"] = state.invocation
                    elif (state.invocation, state.cgroup) != (expected.invocation, expected.cgroup):
                        if job is None or job.attempt == expected_attempt:
                            raise RuntimeError("Training invocation changed during monitoring")
                        # Only a separately admitted attempt of this exact job
                        # can replace the invocation bound to the monitor.
                        expected, expected_attempt = state, job.attempt
                        summary["invocation_id"] = state.invocation
                        event("admitted_invocation", invocation_id=state.invocation, attempt=job.attempt)
                    try:
                        pids = cgroup_pids(own_cgroup(unit, state.cgroup))
                    except FileNotFoundError:
                        latest = unit_state(unit)
                        if job is not None:
                            if queue_matches(job) and latest == state:
                                raise RuntimeError("Active training unit has no readable cgroup")
                            time.sleep(INTERVAL_SECONDS)
                            continue
                        if done.exists() or latest is None or latest.active not in {"active", "activating"}:
                            break
                        raise RuntimeError("Active training unit has no readable cgroup")
                    sample = gpu_sample(pids)
                    if job is not None and (not queue_matches(job) or unit_state(unit) != state):
                        event("sample_discarded", reason="admission changed during sample")
                        time.sleep(INTERVAL_SECONDS)
                        continue
                    reasons = budget_reasons(sample)
                    if reasons:
                        try:
                            stopped = stop_exact_invocation(unit, expected)
                        except InvocationChanged:
                            if job is None:
                                raise
                            event("sample_discarded", reason="invocation changed before stop")
                            time.sleep(INTERVAL_SECONDS)
                            continue
                        summary.update(status="budget_failure", reasons=reasons, stop_requested=stopped)
                        event("budget_failure", reasons=reasons, **sample)
                        exit_code = 2
                    summary["samples"] += 1
                    summary["peak_own_gpu_mib"] = max(summary["peak_own_gpu_mib"], sample["own_gpu_mib"])
                    previous = summary["minimum_gpu_free_mib"]
                    summary["minimum_gpu_free_mib"] = sample["gpu_free_mib"] if previous is None else min(previous, sample["gpu_free_mib"])
                    event("sample", **sample)
                    if exit_code:
                        break
                elif job is not None:
                    if time.monotonic() - admission_since >= args.startup_timeout_seconds:
                        raise RuntimeError("Admitted gq unit did not appear before monitor startup timeout")
                elif expected is not None or done.exists():
                    break
                elif time.monotonic()-started >= args.startup_timeout_seconds:
                    raise RuntimeError("Training unit did not appear before monitor startup timeout")
                if done.exists():
                    break
                time.sleep(INTERVAL_SECONDS)
            if exit_code == 0:
                if not summary["samples"] and not never_started:
                    raise RuntimeError("Training completed without any GPU budget sample")
                summary["status"] = "not_started" if never_started and not summary["samples"] else "complete"
                event("unit_finished", samples=summary["samples"])
        except (Exception, KeyboardInterrupt) as error:
            summary.update(status="monitor_failure", error=str(error) or type(error).__name__)
            exit_code = 3
            if expected is not None:
                try:
                    summary["stop_requested"] = stop_exact_invocation(unit, expected)
                except Exception as stop_error:
                    summary["stop_error"] = str(stop_error)
            event("monitor_failure", error=summary["error"], stop_requested=summary["stop_requested"])
        summary["exit_code"] = exit_code
        event("summary", **{key: value for key, value in summary.items() if key != "unit"})
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({key: summary[key] for key in ("unit", "status", "samples", "peak_own_gpu_mib",
                                                  "minimum_gpu_free_mib", "stop_requested", "exit_code")}, sort_keys=True))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", required=True, help="Exact own gothic-body-bc-...-train-cuda.service or gq-<id>.service name")
    parser.add_argument("--output", required=True, help="New JSONL output file; never overwritten")
    parser.add_argument("--summary", required=True, help="Compact final JSON artifact")
    parser.add_argument("--job-done", required=True, help="Launcher creates this marker after the job returns")
    parser.add_argument("--gq-home", type=Path,
                        help="gq state directory; required only for queued jobs, never changes the queue")
    parser.add_argument("--startup-timeout-seconds", type=int, default=60)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("GPU training monitor runs only on dev-ubuntu/Linux")
    if not 1 <= args.startup_timeout_seconds <= 120:
        parser.error("Monitor startup timeout must be 1..120 seconds")
    try:
        return watch(args)
    except Exception as error:
        print(json.dumps({"status": "monitor_failure", "error": str(error)}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
