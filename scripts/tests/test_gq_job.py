"""Shell submit/wait integration; the fake never touches a queue or systemd."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest


HELPER = Path(__file__).resolve().parents[1] / "gq-job.sh"
JOB_ID = "012345abcdef"
ARGS = ["--class", "quiet", "--threads", "3", "--mem", "4G", "--gpu-mem", "2G",
        "--display", "--cpu-quota", "200%", "--max", "30m", "--name", "test phase",
        "--", "env", "PHASE=value with spaces", "printf", "%s\\n", "literal $value"]

FAKE_GQ = r'''
import json
import os
from pathlib import Path
import signal
import sys
import time

root = Path(os.environ["FAKE_GQ_HOME"])
args = sys.argv[1:]
with (root / "calls.jsonl").open("a") as output:
    output.write(json.dumps({"args": args, "cwd": os.getcwd(),
                             "owner": os.environ.get("GQ_OWNER"),
                             "env": os.environ.get("TEST_ENV"),
                             "parent": os.getppid()}) + "\n")
if args[0] == "submit":
    if os.environ.get("FAKE_SUBMIT_CODE"):
        sys.exit(int(os.environ["FAKE_SUBMIT_CODE"]))
    print("012345abcdef")
elif args[0] == "wait":
    # A real wait observes cancellation from queue state. Ignore group signals
    # here too, so tests prove the helper cancelled and reaped the final status.
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, signal.SIG_IGN)
    (root / "waiting").write_text(str(os.getppid()))
    if os.environ.get("FAKE_WAIT_BLOCK"):
        deadline = time.monotonic() + 10
        while not (root / "cancelled").exists():
            if time.monotonic() > deadline:
                sys.exit(99)
            time.sleep(0.01)
    print("job stdout", flush=True)
    print("job stderr", file=sys.stderr, flush=True)
    sys.exit(int(os.environ.get("FAKE_WAIT_CODE", "0")))
elif args[0] == "cancel":
    (root / "cancelled").write_text(args[1])
else:
    sys.exit(98)
'''


@pytest.fixture
def host(tmp_path):
    root = tmp_path / "fake"
    root.mkdir()
    home = tmp_path / "home"
    work = home / "gothic-ai" / "nav-dagger" / "code"
    work.mkdir(parents=True)
    binary = root / "fake-gq"
    binary.write_text(f"#!{sys.executable}\n" + FAKE_GQ)
    binary.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "GQ_BIN": str(binary),
           "FAKE_GQ_HOME": str(root), "TEST_ENV": "inherited value"}
    env.pop("GQ_OWNER", None)
    env.pop("GQ_JOB_ID_FILE", None)
    return {"root": root, "home": home, "work": work, "binary": binary, "env": env}


def command(body='gq_job "$@"'):
    return ["bash", "-c", 'source "$1"; shift; ' + body, "test-gq-job", str(HELPER), *ARGS]


def calls(host):
    path = host["root"] / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def run(host, *, body='gq_job "$@"', cwd=None, env=None):
    return subprocess.run(command(body), cwd=cwd or host["work"],
                          env=env or host["env"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=10)


def test_submit_wait_preserves_arguments_environment_and_cwd_at_eof(host):
    result = run(host)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "job stdout\n"
    assert "job stderr" in result.stderr
    assert f"submitted {JOB_ID} (nav-dagger)" in result.stderr
    records = calls(host)
    assert [record["args"] for record in records] == [["submit", *ARGS], ["wait", JOB_ID]]
    assert all(record["owner"] == "nav-dagger" for record in records)
    assert all(record["env"] == "inherited value" for record in records)
    assert all(record["cwd"] == str(host["work"]) for record in records)


@pytest.mark.parametrize("exit_code", [0, 17, 143])
def test_returns_wait_exit_status(host, exit_code):
    host["env"]["FAKE_WAIT_CODE"] = str(exit_code)
    assert run(host).returncode == exit_code


def test_explicit_owner_allows_submission_outside_task_folder(host):
    host["env"]["GQ_OWNER"] = "body-other-session"
    result = run(host, cwd=host["root"])
    assert result.returncode == 0, result.stderr
    assert all(record["owner"] == "body-other-session" for record in calls(host))


@pytest.mark.parametrize("relative", ["", "gothic-ai", "gothic-ai/bin", "gothic-ai/queue"])
def test_missing_task_owner_fails_before_submit(host, relative):
    cwd = host["home"] / relative
    cwd.mkdir(parents=True, exist_ok=True)
    result = run(host, cwd=cwd)
    assert result.returncode == 2
    assert "set GQ_OWNER" in result.stderr
    assert calls(host) == []


def test_job_id_file_contains_only_submitted_id(host):
    id_file = host["root"] / "job-id"
    host["env"]["GQ_JOB_ID_FILE"] = str(id_file)
    assert run(host).returncode == 0
    assert id_file.read_text() == JOB_ID + "\n"


def test_submit_failure_does_not_wait_or_cancel(host):
    host["env"]["FAKE_SUBMIT_CODE"] = "42"
    assert run(host).returncode == 42
    assert [record["args"][0] for record in calls(host)] == ["submit"]


def test_default_binary_path_and_gq_available(host):
    binary = host["home"] / "gothic-ai" / "bin" / "gq"
    binary.parent.mkdir()
    binary.symlink_to(host["binary"])
    host["env"].pop("GQ_BIN")
    result = run(host, body='gq_available && gq_job "$@"')
    assert result.returncode == 0, result.stderr
    binary.unlink()
    assert run(host, body="gq_available").returncode == 1


def test_unavailable_override_fails_without_submitting(host):
    host["env"]["GQ_BIN"] = str(host["root"] / "absent")
    result = run(host)
    assert result.returncode == 127
    assert calls(host) == []


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_signals_cancel_own_job_and_return_wait_status(host, signum):
    host["env"].update(FAKE_WAIT_BLOCK="1", FAKE_WAIT_CODE="73")
    # Signal an isolated foreground process group, as terminal Ctrl-C does. The
    # wrapper stays alive to collect the helper's status. Running gq_job in the
    # foreground avoids Bash's ignored SIGINT for background function calls.
    process = subprocess.Popen(command('trap ":" INT TERM HUP; gq_job "$@"'),
                               cwd=host["work"], env=host["env"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        waiting = host["root"] / "waiting"
        deadline = time.monotonic() + 5
        while not waiting.exists() or not waiting.read_text():
            assert process.poll() is None
            assert time.monotonic() < deadline, "fake gq wait did not start"
            time.sleep(0.01)
        helper_pid = int(waiting.read_text())
        assert helper_pid != process.pid  # gq_job isolates its traps in a subshell.
        os.killpg(process.pid, signum)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 73, stderr
        assert stdout == "job stdout\n"
        assert [record["args"] for record in calls(host)] == [
            ["submit", *ARGS], ["wait", JOB_ID], ["cancel", JOB_ID]]
        assert (host["root"] / "cancelled").read_text() == JOB_ID
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)


def test_function_does_not_replace_caller_traps(host):
    result = run(host, body='trap "printf caller-trap\\\\n" TERM; before=$(trap -p TERM); '
                           'gq_job "$@"; [[ "$(trap -p TERM)" == "$before" ]]')
    assert result.returncode == 0, result.stderr
