"""Worktree leases survive their Mac launcher; no real SSH or queue is used."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[2]


def eventually(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "subprocess did not reach the expected state"
        time.sleep(0.02)


def lock_available(path):
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def check_quick_lifetime(tmp_path):
    # Extract production code so closing fd 8 at the gq call breaks this test.
    source = (ROOT / "scripts/ryzen.sh").read_text()
    match = re.search(r"^run_quick\(\) \{\n.*?^\}", source, re.MULTILINE | re.DOTALL)
    assert match is not None
    binaries = tmp_path / "bin"
    binaries.mkdir()
    fake_ssh = binaries / "ssh"
    fake_ssh.write_text(
        f"#!{sys.executable}\n" + r'''
import json
import os
from pathlib import Path
import time

root = Path(os.environ["RYZEN_TEST_HOME"])
(root / "job.json").write_text(json.dumps({"job": os.getpid(), "wrapper": os.getppid()}))
while not (root / "finish").exists():
    time.sleep(0.02)
(root / "job-finished").touch()
''')
    fake_ssh.chmod(0o755)
    control = tmp_path / "control.py"
    control.write_text(r'''
import fcntl
import os
from pathlib import Path

root = Path(os.environ["RYZEN_TEST_HOME"])
with (root / "worktree.lock").open("a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    (root / "locked").touch()
    while os.read(0, 4096):
        pass
''')
    os.mkfifo(tmp_path / "input")
    script = "\n".join([
        "set -euo pipefail",
        f"ROOT={shlex.quote(str(ROOT))}",
        'SESSION="$RYZEN_TEST_HOME"',
        'OUTPUT="$SESSION/output"',
        'STATE="$SESSION/state"',
        "RUN=test RYZEN_HOST=fake AI_PORT='' TUNNEL_OPEN=0",
        "GQ_ARGS=(--class quick --threads 1 --mem 100M --max 1m)",
        "exec 8<>\"$SESSION/input\"",
        f"{shlex.quote(sys.executable)} {shlex.quote(str(control))} 8>&- <\"$SESSION/input\" &",
        "SSH_PID=$!",
        'until [[ -f "$SESSION/locked" ]]; do sleep 0.02; done',
        match.group(0),
        "run_quick true",
    ])
    environment = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "RYZEN_TEST_HOME": str(tmp_path),
    }
    with (tmp_path / "launcher.log").open("wb") as output:
        launcher = subprocess.Popen(
            ["bash", "-c", script], env=environment, stdout=output, stderr=output,
            start_new_session=True,
        )
        completed = False
        try:
            eventually(lambda: (tmp_path / "job.json").exists())
            job = json.loads((tmp_path / "job.json").read_text())
            assert not lock_available(tmp_path / "worktree.lock")
            launcher.kill()
            assert launcher.wait(timeout=5) == -signal.SIGKILL
            # The launcher's orphaned sleep 1 also inherits fd 8. Outwait it so
            # only gq's lease can keep the control reader from observing EOF.
            deadline = time.monotonic() + 1.3
            while time.monotonic() < deadline:
                os.kill(job["job"], 0)
                os.kill(job["wrapper"], 0)
                assert not lock_available(tmp_path / "worktree.lock"), (
                    "worktree became writable while the orphaned quick job was still running"
                )
                time.sleep(0.02)
            (tmp_path / "finish").touch()
            eventually(lambda: (tmp_path / "job-finished").exists())
            eventually(lambda: lock_available(tmp_path / "worktree.lock"))
            completed = True
        finally:
            (tmp_path / "finish").touch()
            if not completed:
                # Kill only remaining members of our isolated process group.
                # macOS may reject killpg for a group that has already gone;
                # individual signals also preserve real permission failures.
                processes = subprocess.check_output(["ps", "-axo", "pid=,pgid="], text=True)
                for line in processes.splitlines():
                    pid, group = map(int, line.split())
                    if group == launcher.pid:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            launcher.wait(timeout=5)


class RyzenLifetimeTests(unittest.TestCase):
    def test_quick_keeps_worktree_lock_after_launcher_is_killed(self):
        with tempfile.TemporaryDirectory(prefix="ryzen-lifetime-test-") as directory:
            check_quick_lifetime(Path(directory))


if __name__ == "__main__":
    unittest.main()
