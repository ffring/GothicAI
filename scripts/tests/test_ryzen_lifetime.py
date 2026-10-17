"""Real Mac gq wrapper, production cleanup, and an independently stopping job."""
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


def eventually(predicate, timeout=7):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('subprocess did not reach the expected state')
        time.sleep(0.02)


def lock_available(path):
    with path.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def function(name):
    match = re.search(r'^' + name + r'\(\) \{\n.*?^\}',
                      (ROOT / 'scripts/ryzen.sh').read_text(), re.M | re.S)
    assert match is not None
    return match.group(0)


class RyzenLifetimeTests(unittest.TestCase):
    def start_launcher(self, directory, fail_before_registration=False):
        root = Path(directory)
        binaries = root / 'bin'
        binaries.mkdir()
        (root / 'session').mkdir()
        (root / 'queue').mkdir()
        worker = root / 'worker.py'
        worker.write_text(r'''
import json
import os
from pathlib import Path
import time

root = Path(os.environ['RYZEN_TEST_HOME'])
job = {'id': 'own-job', 'command': ['env', 'RYZEN_RUN_ID=test',
       str(root / 'state/source/scripts/ryzen.sh')], 'status': 'running'}
def publish(status):
    job['status'] = status
    pending = root / 'queue/state.tmp'
    pending.write_text(json.dumps({'version': 1, 'jobs': [job]}))
    pending.replace(root / 'queue/state.json')
publish('running')
(root / 'started').touch()
while not (root / 'stop').exists() and not (root / 'finish').exists():
    time.sleep(0.02)
if (root / 'stop').exists():
    # The task outlives the SSH client and local gq wrapper by three seconds.
    time.sleep(3)
publish('cancelled' if (root / 'stop').exists() else 'done')
(root / 'job-finished').touch()
''')
        fake_ssh = binaries / 'ssh'
        fake_ssh.write_text(f'#!{sys.executable}\n' + r'''
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
root = Path(os.environ['RYZEN_TEST_HOME'])
if os.environ['RYZEN_TEST_SSH_FAIL'] == '1':
    (root / 'ssh-failed').touch()
    raise SystemExit(255)
job = subprocess.Popen([sys.executable, str(root / 'worker.py')],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, start_new_session=True)
metadata = root / 'job.tmp'
metadata.write_text(json.dumps({'job': job.pid, 'wrapper': os.getppid()}))
metadata.replace(root / 'job.json')
def stop(_signum, _frame):
    (root / 'stop').touch()
    raise SystemExit(143)
signal.signal(signal.SIGTERM, stop)
while job.poll() is None:
    time.sleep(0.02)
sys.exit(job.returncode)
''')
        fake_ssh.chmod(0o755)
        control = root / 'control.py'
        control.write_text(r'''
import fcntl
import os
from pathlib import Path
import sys
root = Path(os.environ['RYZEN_TEST_HOME'])
lock = (root / 'worktree.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
os.dup2(lock.fileno(), 9)
os.set_inheritable(9, True)
os.execv(sys.executable, [sys.executable, '-u', sys.argv[1],
                         str(root / 'state'), sys.argv[2]])
''')
        os.mkfifo(root / 'session/input')
        harness = '\n'.join([
            'set -euo pipefail',
            f'ROOT={shlex.quote(str(ROOT))}',
            'SESSION="$RYZEN_TEST_HOME/session"',
            'OUTPUT="$SESSION/output" STATE="$RYZEN_TEST_HOME/state"',
            "RUN=test RYZEN_HOST=fake AI_PORT='' TUNNEL_OPEN=0",
            'RUN_CREATED=1 RUN_STARTED=0 RUN_FINISHED=0 RUN_RC=0 SSH_PID= GQ_PID=',
            'GQ_ARGS=(--class quick --threads 1 --mem 100M --max 1m --name test)',
            'exec 8<>"$SESSION/input"',
            function('guard_ack'), function('cleanup'), function('run_quick'),
            'finish_run() { touch "$RYZEN_TEST_HOME/collected"; }',
            'trap cleanup EXIT', "trap 'exit 143' TERM", "trap 'exit 130' INT",
            f'{shlex.quote(sys.executable)} {shlex.quote(str(control))} '
            f'{shlex.quote(str(ROOT / "scripts/ryzen-lock.py"))} '
            f'{shlex.quote(str(ROOT / "scripts/gq.py"))} '
            '8>&- <"$SESSION/input" > >(exec 8>&-; tee -a "$OUTPUT") 2>&1 &',
            'SSH_PID=$!', 'guard_ack "Ryzen: queue guard ready"',
            'run_quick env "RYZEN_RUN_ID=$RUN" bash "$STATE/source/scripts/ryzen.sh" __remote pytest "$STATE" "$RUN"',
        ])
        environment = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'],
                           RYZEN_TEST_HOME=str(root), GQ_HOME=str(root / 'queue'),
                           RYZEN_TEST_SSH_FAIL=str(int(fail_before_registration)))
        output = (root / 'launcher.log').open('wb')
        self.addCleanup(output.close)
        launcher = subprocess.Popen(['bash', '-c', harness], env=environment,
                                    stdout=output, stderr=output, start_new_session=True)
        self.addCleanup(self.stop_children, launcher, root)
        if fail_before_registration:
            eventually(lambda: (root / 'ssh-failed').exists())
        else:
            eventually(lambda: (root / 'started').exists())
            eventually(lambda: (root / 'job.json').exists())
        self.assertFalse(lock_available(root / 'worktree.lock'))
        return root, launcher

    @staticmethod
    def stop_children(launcher, root):
        if (root / 'job.json').exists() and not (root / 'job-finished').exists():
            pid = json.loads((root / 'job.json').read_text())['job']
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        processes = subprocess.check_output(['ps', '-axo', 'pid=,pgid='], text=True)
        for line in processes.splitlines():
            pid, group = map(int, line.split())
            if group == launcher.pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        launcher.wait(timeout=5)

    def test_quick_keeps_worktree_lock_after_launcher_is_killed(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix='ryzen-kill-test-'))
        root, launcher = self.start_launcher(directory)
        launcher.kill()
        self.assertEqual(launcher.wait(timeout=5), -signal.SIGKILL)
        deadline = time.monotonic() + 1.3
        while time.monotonic() < deadline:
            self.assertFalse(lock_available(root / 'worktree.lock'))
            time.sleep(0.02)
        (root / 'finish').touch()
        eventually(lambda: (root / 'job-finished').exists())
        eventually(lambda: lock_available(root / 'worktree.lock'))

    def test_term_keeps_lock_and_artifacts_until_remote_job_is_terminal(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix='ryzen-stop-test-'))
        root, launcher = self.start_launcher(directory)
        launcher.terminate()
        eventually(lambda: (root / 'stop').exists())
        stopped_at = time.monotonic()
        while not (root / 'job-finished').exists():
            self.assertIsNone(launcher.poll(), (root / 'launcher.log').read_text())
            self.assertFalse(lock_available(root / 'worktree.lock'))
            self.assertFalse((root / 'collected').exists())
            self.assertLess(time.monotonic() - stopped_at, 7)
            time.sleep(0.02)
        self.assertEqual(launcher.wait(timeout=5), 143)
        self.assertGreaterEqual(time.monotonic() - stopped_at, 2.8)
        self.assertTrue((root / 'collected').exists())
        self.assertTrue(lock_available(root / 'worktree.lock'))

    def test_ssh_255_before_registration_releases_lock_within_65_seconds(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix='ryzen-no-job-test-'))
        started = time.monotonic()
        root, launcher = self.start_launcher(directory, fail_before_registration=True)
        self.assertEqual(launcher.wait(timeout=65 - (time.monotonic() - started)), 255)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 60)
        self.assertLessEqual(elapsed, 65)
        self.assertTrue(lock_available(root / 'worktree.lock'))
        message = 'no gq request registered for test in 60s; releasing'
        self.assertEqual((root / 'launcher.log').read_text().count(message), 1)
        self.assertFalse((root / 'queue/state.json').exists())
        print(f'\nSSH 255 before registration: exit=255, elapsed={elapsed:.2f}s, lock released', flush=True)


if __name__ == '__main__':
    unittest.main()
