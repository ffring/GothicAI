"""The remote worktree lease survives EOF until its exact queue job ends."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import runpy
import select
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
RUN = "test-run"


class Guardian:
    def __init__(self, directory):
        self.root = Path(directory)
        self.state = self.root / "worktree"
        self.queue_home = self.root / "queue"
        self.queue_home.mkdir()
        self.lock = self.root / "worktree.lock"
        self.output = b""
        # The controller acquires the lock, then execs the guardian with fd 9.
        # Use Python for the fd handoff so this also runs on macOS without flock.
        holder = self.lock.open("a")
        fcntl.flock(holder, fcntl.LOCK_EX)
        launcher = """
import os
import sys
source_fd = int(sys.argv[1])
os.dup2(source_fd, 9)
os.set_inheritable(9, True)
if source_fd != 9:
    os.close(source_fd)
os.execv(sys.executable, [sys.executable, '-u', *sys.argv[2:]])
"""
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-c", launcher, str(holder.fileno()),
                 str(ROOT / "scripts/ryzen-lock.py"), str(self.state),
                 str(ROOT / "scripts/gq.py")],
                pass_fds=(holder.fileno(),), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=dict(os.environ, GQ_HOME=str(self.queue_home)),
            )
        finally:
            holder.close()
        self.wait_output("Ryzen: queue guard ready")

    def wait_output(self, message, timeout=5):
        expected = message.encode() + b"\n"
        deadline = time.monotonic() + timeout
        while expected not in self.output:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no guardian message {message!r}: {self.output!r}")
            if not select.select([self.process.stdout], [], [], remaining)[0]:
                continue
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                stderr = self.process.stderr.read().decode()
                raise AssertionError(f"guardian exited before {message!r}: {stderr}")
            self.output += chunk

    def send(self, message):
        self.process.stdin.write((message + "\n").encode())
        self.process.stdin.flush()

    def arm(self):
        self.send(f"arm {RUN}")
        self.wait_output(f"Ryzen: queue guard armed {RUN}")

    def eof(self):
        self.process.stdin.close()

    def snapshot(self, status, *, run=RUN, script=None):
        job = {
            "id": "job-test", "status": status,
            "command": ["env", f"RYZEN_RUN_ID={run}", "bash",
                        script or str(self.state / "source/scripts/ryzen.sh"),
                        "__remote", "pytest", str(self.state), run],
        }
        self.write_state({"version": 1, "jobs": [job]})

    def write_state(self, snapshot):
        temporary = self.queue_home / "state.json.tmp"
        temporary.write_text(json.dumps(snapshot) + "\n")
        temporary.replace(self.queue_home / "state.json")

    def locked(self):
        with self.lock.open("a") as holder:
            try:
                fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
        return False

    def assert_retained(self, case, duration=0.3):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            case.assertIsNone(self.process.poll(), self.output)
            case.assertTrue(self.locked(), "worktree lock was released before terminal state")
            time.sleep(0.02)

    def assert_released(self, case):
        case.assertEqual(self.process.wait(timeout=5), 0)
        case.assertFalse(self.locked())

    def close(self):
        if self.process.poll() is None:
            self.process.kill()  # Only our isolated guardian; it ignores TERM by design.
        self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


class RyzenLockTests(unittest.TestCase):
    def guardian(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="ryzen-lock-test-"))
        guardian = Guardian(directory)
        self.addCleanup(guardian.close)
        return guardian

    def test_registration_grace_period_is_60_seconds(self):
        guardian = self.guardian()
        namespace = runpy.run_path(str(ROOT / 'scripts/ryzen-lock.py'))
        wait = namespace['wait_terminal']
        elapsed = 0

        def sleep(seconds):
            nonlocal elapsed
            elapsed += seconds

        clock = SimpleNamespace(monotonic=lambda: elapsed, sleep=sleep)
        with patch.dict(wait.__globals__, time=clock), patch.dict(wait.__globals__, report=lambda message: messages.append(message)):
            messages = []
            wait(guardian.state, RUN, False, guardian.queue_home, 1, {'done'})
        self.assertGreaterEqual(elapsed, 60)
        self.assertLess(elapsed, 60.3)
        releasing = [message for message in messages if 'releasing' in message]
        self.assertEqual(releasing, [f'Ryzen: no gq request registered for {RUN} in 60s; releasing'])

    def test_observed_request_waits_past_registration_deadline(self):
        guardian = self.guardian()
        guardian.snapshot('running')
        namespace = runpy.run_path(str(ROOT / 'scripts/ryzen-lock.py'))
        wait = namespace['wait_terminal']
        elapsed = 0

        def sleep(_seconds):
            nonlocal elapsed
            elapsed += 61
            # Even a temporarily missing observed record cannot release it.
            if elapsed == 61:
                guardian.write_state({'version': 1, 'jobs': []})
            else:
                guardian.snapshot('done')

        clock = SimpleNamespace(monotonic=lambda: elapsed, sleep=sleep)
        messages = []
        with patch.dict(wait.__globals__, time=clock, report=messages.append):
            wait(guardian.state, RUN, False, guardian.queue_home, 1, {'done'})
        self.assertEqual(elapsed, 122)
        self.assertFalse(any('releasing' in message for message in messages))

    def test_eof_keeps_active_job_locked_until_terminal(self):
        guardian = self.guardian()
        guardian.snapshot("running")
        guardian.arm()
        guardian.eof()
        guardian.assert_retained(self)
        guardian.snapshot("cancelled")
        guardian.assert_released(self)

    def test_eof_waits_for_late_registration(self):
        guardian = self.guardian()
        guardian.arm()
        guardian.eof()
        guardian.assert_retained(self)
        guardian.snapshot("queued")
        guardian.assert_retained(self)
        guardian.snapshot("running")
        guardian.assert_retained(self)
        guardian.snapshot("done")
        guardian.assert_released(self)

    def test_invalid_queue_snapshot_keeps_lock(self):
        guardian = self.guardian()
        guardian.snapshot("running")
        guardian.arm()
        guardian.eof()
        guardian.assert_retained(self)
        temporary = guardian.queue_home / "state.json.tmp"
        temporary.write_text("{broken json")
        temporary.replace(guardian.queue_home / "state.json")
        guardian.assert_retained(self)
        guardian.snapshot("done")
        guardian.assert_released(self)

    def test_closed_output_pipe_keeps_lock_until_terminal(self):
        guardian = self.guardian()
        guardian.snapshot("running")
        guardian.arm()
        guardian.process.stdout.close()
        guardian.eof()
        guardian.assert_retained(self)
        guardian.snapshot("cancelled")
        guardian.assert_released(self)

    def test_complete_without_submission_releases_validation_failure(self):
        guardian = self.guardian()
        guardian.arm()
        guardian.send(f"complete {RUN}")
        guardian.send(f"drain {RUN}")
        guardian.wait_output(f"Ryzen: queue guard complete {RUN}")
        self.assertTrue(guardian.locked(), "drain must retain the lease for log collection")
        guardian.eof()
        guardian.assert_released(self)

    def test_queued_cancellation_is_terminal(self):
        guardian = self.guardian()
        guardian.snapshot("queued")
        guardian.arm()
        guardian.eof()
        guardian.assert_retained(self)
        guardian.snapshot("cancelled")
        guardian.assert_released(self)

    def test_foreign_run_cannot_confirm_completion(self):
        guardian = self.guardian()
        guardian.snapshot("done", run="another-run")
        guardian.arm()
        guardian.eof()
        guardian.assert_retained(self)
        guardian.snapshot("done")
        guardian.assert_released(self)

    def test_matching_marker_with_foreign_script_cannot_confirm_completion(self):
        guardian = self.guardian()
        guardian.snapshot("done", script=str(guardian.root / "other-worktree/source/scripts/ryzen.sh"))
        guardian.arm()
        guardian.eof()
        guardian.assert_retained(self)
        guardian.snapshot("done")
        guardian.assert_released(self)


if __name__ == "__main__":
    unittest.main()
