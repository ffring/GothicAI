"""Queue admission and per-worktree uploads without a game or real SSH."""
import fcntl
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'ryzen.sh'


def function(name):
    match = re.search(r'^' + name + r'\(\) \{\n.*?^\}', SCRIPT.read_text(), re.M | re.S)
    assert match is not None, name
    return match.group(0)


def wait_for(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f'no marker: {path}')
        time.sleep(0.02)


class TunnelTests(unittest.TestCase):
    def run_job(self, directory, mode='run', forward_fail=False, reserved_port=False):
        root = Path(directory)
        (root / 'scripts').mkdir()
        (root / 'inputs/run').mkdir(parents=True)
        gq = root / 'scripts/gq'
        gq.write_text(f'#!{sys.executable}\n' + r'''
import os
from pathlib import Path
import sys
import time
root = Path(os.environ['TEST_SESSION'])
(root / 'submitted').touch()
(root / 'job-pid').write_text(str(os.getpid()))
if os.environ['TEST_MODE'] == 'busy':
    print('Ryzen занят', file=sys.stderr, flush=True)
    sys.exit(75)
while not (root / 'admitted').exists():
    time.sleep(0.02)
print('Ryzen: waiting for AI tunnel', flush=True)
while not (root / 'inputs/run/tunnel-ready').exists():
    time.sleep(0.02)
(root / 'job-ran').touch()
''')
        gq.chmod(0o755)
        harness = r'''
set -euo pipefail
ROOT="$TEST_SESSION" SESSION="$TEST_SESSION" STATE="$TEST_SESSION"
OUTPUT="$SESSION/output" RUN=run RYZEN_HOST=test AI_PORT=47827
TUNNEL_OPEN=0 RUN_STARTED=0 RUN_FINISHED=0 RUN_RC=0
SSH_PID=$$
GQ_ARGS=(--name test)
exec 8>"$SESSION/control"
: > "$OUTPUT"
ssh() {
  printf '%s\n' "$*" >> "$SESSION/forward"
  [[ -f "$SESSION/admitted" && "$TEST_FORWARD_FAIL" == 0 ]] || return 255
  [[ "$*" != *'-O cancel'* ]] || return 0
  if [[ "$TEST_RESERVED_PORT" == 1 && ! -f "$SESSION/retried" ]]; then
    touch "$SESSION/retried"
    printf '47827\n'
    return 0
  fi
  printf '51234\n'
}
remote() { "$@"; }
guard_ack() { :; }
'''
        env = dict(os.environ, TEST_SESSION=str(root), TEST_MODE=mode,
                   TEST_FORWARD_FAIL=str(int(forward_fail)), TEST_RESERVED_PORT=str(int(reserved_port)))
        process = subprocess.Popen(
            ['bash', '-c', harness + function('start_tunnel') + '\n' + function('run_quick') + '\nrun_quick true'],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            start_new_session=True,
        )
        self.addCleanup(self.stop_job, process, root)
        return root, process

    @staticmethod
    def stop_job(process, root):
        if process.poll() is None:
            process.kill()
        if (root / 'job-pid').exists() and not (root / 'job-ran').exists():
            try:
                os.kill(int((root / 'job-pid').read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=5)

    def test_tunnel_opens_only_after_job_admission(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        root, process = self.run_job(directory)
        wait_for(root / 'submitted')
        time.sleep(0.15)
        self.assertFalse((root / 'forward').exists())
        (root / 'admitted').touch()
        output, _ = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, output)
        self.assertTrue((root / 'job-ran').exists())
        forwards = (root / 'forward').read_text().splitlines()
        self.assertEqual(len(forwards), 1)
        self.assertIn('-O forward', forwards[0])
        self.assertIn(str(root / 'control'), forwards[0])
        self.assertIn('127.0.0.1:0:127.0.0.1:47827', forwards[0])
        self.assertEqual((root / 'inputs/run/tunnel-ready').read_text(), '51234\n')

    def test_queue_timeout_does_not_open_tunnel(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        root, process = self.run_job(directory, mode='busy')
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 75, output)
        self.assertIn('Ryzen занят', output)
        self.assertFalse((root / 'forward').exists())
        self.assertFalse((root / 'job-ran').exists())

    def test_failed_forward_stops_job_before_game(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        root, process = self.run_job(directory, forward_fail=True)
        wait_for(root / 'submitted')
        (root / 'admitted').touch()
        output, _ = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 1, output)
        self.assertFalse((root / 'inputs/run/tunnel-ready').exists())
        self.assertFalse((root / 'job-ran').exists())

    def test_e2e_port_is_cancelled_and_reallocated(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        root, process = self.run_job(directory, reserved_port=True)
        wait_for(root / 'submitted')
        (root / 'admitted').touch()
        output, _ = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, output)
        calls = (root / 'forward').read_text().splitlines()
        self.assertEqual(len(calls), 3)
        self.assertIn('-O cancel', calls[1])
        self.assertIn('127.0.0.1:0:127.0.0.1:47827', calls[1])
        self.assertEqual((root / 'inputs/run/tunnel-ready').read_text(), '51234\n')

    def test_remote_bench_waits_then_preserves_url_components(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'inputs/run').mkdir(parents=True)
            harness = function('remote_bench') + r'''
toolchain() { printf '%s' "$GOTHIC_AI_URL" > "$TEST_SESSION/rewritten"; exit 0; }
remote_bench "$TEST_SESSION" run unused
'''
            # Exit in toolchain after observing the URL; no game tools are used.
            process = subprocess.Popen(['bash', '-ec', harness], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, env=dict(os.environ, TEST_SESSION=str(root), RYZEN_AI_TUNNEL='1',
                                                           GOTHIC_AI_URL='ws://user:secret@localhost:47827/ws?q=x#fragment'))
            try:
                self.assertEqual(process.stdout.readline().strip(), 'Ryzen: waiting for AI tunnel')
                self.assertFalse((root / 'rewritten').exists())
                (root / 'inputs/run/tunnel-ready').write_text('51234\n')
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stdout + stderr)
                self.assertEqual((root / 'rewritten').read_text(), 'ws://user:secret@localhost:51234/ws?q=x#fragment')
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)


@unittest.skipUnless(shutil.which('rsync'), 'needs rsync')
class TextureTests(unittest.TestCase):
    def test_two_worktree_uploads_do_not_replace_readers_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binaries = root / 'bin'
            binaries.mkdir()
            wrapper = binaries / 'rsync'
            wrapper.write_text(f'#!{sys.executable}\n' + f'''
import os
from pathlib import Path
import subprocess
import sys
args = sys.argv[1:]
if ':' in args[-1]:
    host, path = args[-1].split(':', 1)
    assert host == 'test'
    args[-1] = str(Path(os.environ['TEST_REMOTE_HOME']) / path) + '/'
sys.exit(subprocess.call([{shutil.which('rsync')!r}, *args]))
''')
            wrapper.chmod(0o755)
            remote_home = root / 'remote'
            expected = []
            with (root / 'A.lock').open('w') as lock_a, (root / 'B.lock').open('w') as lock_b:
                fcntl.flock(lock_a, fcntl.LOCK_EX)
                fcntl.flock(lock_b, fcntl.LOCK_EX)
                for name in ('A', 'B'):
                    textures = root / name
                    textures.mkdir()
                    (textures / f'{name}.dds').write_text(name)
                    remote = f'gothic-ai/e2e/{name}'
                    state = remote_home / remote
                    harness = '\n'.join([
                        'set -euo pipefail',
                        f'STATE={shlex.quote(str(state))}',
                        f'REMOTE={shlex.quote(remote)}',
                        f'HD_TEXTURES={shlex.quote(str(textures))}',
                        'RYZEN_HOST=test', 'ENV_ARGS=()', 'remote() { "$@"; }',
                        function('sync_hd_textures'), 'sync_hd_textures',
                        'printf "%s\\n" "${ENV_ARGS[@]}"',
                    ])
                    result = subprocess.run(['bash', '-c', harness], capture_output=True, text=True,
                                            env=dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'],
                                                     TEST_REMOTE_HOME=str(remote_home)), timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    target = state / f'hd-textures/textures/{name}.dds'
                    self.assertEqual(result.stdout.strip(), f'OPENGOTHIC_TEXTURES={target.parent}')
                    expected.append(target)
                for path in expected:
                    self.assertEqual(path.read_text(), path.stem)


if __name__ == '__main__':
    unittest.main()
