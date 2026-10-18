"""Queue admission and per-worktree uploads without a game or real SSH."""
import fcntl
import json
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


class QueueSplitTests(unittest.TestCase):
    def run_game(self, action='e2e', prepare=True, build_exit=0):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix='ryzen-phases-test-'))
        root = Path(directory)
        (root / 'scripts').mkdir()
        queue = root / 'scripts/gq'
        queue.write_text(f'#!{sys.executable}\n' + r'''
import json
import os
from pathlib import Path
import sys
root = Path(os.environ['TEST_SESSION'])
with (root / 'requests.jsonl').open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\n')
name = sys.argv[sys.argv.index('--name') + 1]
if name.startswith('build ') and int(os.environ['TEST_BUILD_EXIT']):
    print('compile failure: build log tail', flush=True)
    sys.exit(int(os.environ['TEST_BUILD_EXIT']))
''')
        queue.chmod(0o755)
        harness = r'''
set -euo pipefail
ROOT="$TEST_SESSION" SESSION="$TEST_SESSION" STATE="$TEST_SESSION/state"
OUTPUT="$SESSION/output" RUN=run RYZEN_HOST=fake AI_PORT=''
ACTION="$TEST_ACTION" JOBS=7 LOCK_WAIT=23 SCENARIO=stages
BENCH_SCENARIO=/benchmarks/smoke.bench RYZEN_MAX=9m
if [[ "$ACTION" == bench ]]; then BENCH_CLASS=quiet; else unset BENCH_CLASS; fi
TUNNEL_OPEN=0 RUN_STARTED=0 RUN_FINISHED=0 RUN_RC=0
SSH_PID=$$ ENV_ARGS=(RYZEN_JOBS=7) GQ_ARGS=()
exec 8>"$SESSION/control"
: > "$OUTPUT"
guard_ack() { :; }
preparation_needed() { [[ "$TEST_PREPARE" == 1 ]]; }
'''
        harness += '\n'.join([function('run_quick'), function('game_queue_args'), function('run_game')])
        harness += '\nrun_game env "${ENV_ARGS[@]}" bash "$STATE/source/scripts/ryzen.sh" __remote "$ACTION" "$STATE" "$RUN" stages\n'
        result = subprocess.run(
            ['bash', '-c', harness], capture_output=True, text=True, timeout=10,
            env=dict(os.environ, TEST_SESSION=str(root), TEST_ACTION=action,
                     TEST_PREPARE=str(int(prepare)), TEST_BUILD_EXIT=str(build_exit)),
        )
        requests = root / 'requests.jsonl'
        return result, [json.loads(line) for line in requests.read_text().splitlines()] if requests.exists() else []

    @staticmethod
    def queue_options(request):
        return request[:request.index('--')]

    def assert_option(self, options, name, expected):
        self.assertIn(name, options)
        self.assertEqual(options[options.index(name) + 1], expected)

    def assert_game_request(self, request, action):
        options = self.queue_options(request)
        for name, value in [('--class', 'quiet' if action == 'bench' else 'quick'),
                            ('--threads', '4'), ('--mem', '8G'), ('--max', '9m'),
                            ('--gpu-mem', '4G' if action == 'bench' else '2G'),
                            ('--wait-max', '23'),
                            ('--name', 'bench smoke' if action == 'bench' else 'e2e stages')]:
            self.assert_option(options, name, value)
        self.assertIn('--display', options)
        self.assertIn('RYZEN_RUN_ID=run-game', request[request.index('--') + 1:])

    def test_preparation_and_game_have_separate_resources_and_names(self):
        for action in ('e2e', 'bench'):
            with self.subTest(action=action):
                result, requests = self.run_game(action)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(requests), 2, requests)
                build = self.queue_options(requests[0])
                for name, value in [('--class', 'quick'), ('--threads', '7'), ('--mem', '12G'),
                                    ('--max', '9m'),
                                    ('--name', 'build bench smoke' if action == 'bench' else 'build e2e stages')]:
                    self.assert_option(build, name, value)
                for option in ('--display', '--gpu-mem', '--wait-max'):
                    self.assertNotIn(option, build)
                command = requests[0][requests[0].index('--') + 1:]
                self.assertIn('RYZEN_RUN_ID=run-build', command)
                self.assertEqual(command[command.index('__remote') + 1:][:2], ['prepare', action])
                self.assert_game_request(requests[1], action)

    def test_ready_snapshot_submits_only_game(self):
        for action in ('e2e', 'bench'):
            with self.subTest(action=action):
                result, requests = self.run_game(action, prepare=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(requests), 1, requests)
                self.assert_game_request(requests[0], action)

    def test_build_failure_returns_its_status_without_game_submission(self):
        result, requests = self.run_game(build_exit=42)
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertEqual(len(requests), 1, requests)
        self.assert_option(self.queue_options(requests[0]), '--name', 'build e2e stages')
        self.assertIn('compile failure: build log tail', result.stdout)

    def test_game_rejects_stale_engine_without_building(self):
        with tempfile.TemporaryDirectory(prefix='ryzen-stale-test-') as directory:
            state = Path(directory)
            (state / 'inputs/run').mkdir(parents=True)
            (state / 'inputs/run/source.sha256').write_text('snapshot-digest\n')
            (state / 'build/opengothic').mkdir(parents=True)
            binary = state / 'build/opengothic/Gothic2Notr'
            binary.touch()
            binary.chmod(0o755)
            (state / 'build/source.sha256').write_text('previous-digest\n')
            (state / 'build/CMakeCache.txt').write_text('OPENGOTHIC_ENABLE_APP_CADENCE:BOOL=ON\n')
            harness = '\n'.join([
                'set -euo pipefail',
                'build_engine() { touch "$TEST_STATE/build-was-called"; }',
                'source_digest() { echo "unexpected source scan" >&2; exit 99; }',
                function('engine_ready'), function('require_prepared'),
                'require_prepared bench "$TEST_STATE" run stages',
            ])
            result = subprocess.run(['bash', '-c', harness], capture_output=True, text=True, timeout=5,
                                    env=dict(os.environ, TEST_STATE=directory))
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('source.sha256', result.stdout + result.stderr)
            self.assertIn('refusing to build', (result.stdout + result.stderr).lower())
            self.assertFalse((state / 'build-was-called').exists())


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
require_prepared() { :; }
toolchain() { printf '%s' "$GOTHIC_AI_URL" > "$TEST_SESSION/rewritten"; exit 0; }
remote_bench "$TEST_SESSION" run unused
'''
            # Exit in toolchain after observing the URL; no game tools are used.
            process = subprocess.Popen(['bash', '-ec', harness], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, env=dict(os.environ, TEST_SESSION=str(root), RYZEN_AI_TUNNEL='1',
                                                           GOTHIC_AI_URL='ws://user:secret@localhost:47827/ws?q=x#fragment'))
            try:
                self.assertEqual(process.stdout.readline().strip(), 'Ryzen: game admitted')
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
