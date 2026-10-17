"""Exercise legacy PPO scheduling with a stub instead of loading a model."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest


LOOP = Path(__file__).resolve().parents[1] / "body-rl-ppo-loop.sh"


@pytest.mark.parametrize("use_lock", [False, True], ids=["unlocked", "locked"])
@pytest.mark.parametrize("exit_code", [0, 17])
def test_legacy_update_waits_for_lock_and_preserves_exit_status(tmp_path, use_lock, exit_code):
    if use_lock and shutil.which("flock") is None:
        pytest.skip("legacy locking requires the flock CLI")
    # Resume with a populated critic, including on macOS's Bash 3 with set -u.
    previous = tmp_path / "iter-001" / "update"
    previous.mkdir(parents=True)
    (previous / "best.pt").touch()
    collected = tmp_path / "iter-002" / "collect"
    collected.mkdir(parents=True)
    (collected / "summary.json").write_text("{}")
    marker = tmp_path / "update-started.json"
    python = tmp_path / "stub-python"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "assert sys.argv[2] == 'update', sys.argv\n"
        "pathlib.Path(os.environ['UPDATE_MARKER']).write_text(json.dumps(sys.argv[1:]))\n"
        "sys.exit(int(os.environ['UPDATE_EXIT']))\n"
    )
    python.chmod(0o755)
    env = {**os.environ, "GQ_BIN": str(tmp_path / "no-gq"),
           "PPO_CODE": str(LOOP.parent.parent), "PPO_WORK": str(tmp_path),
           "PPO_PYTHON": str(python), "PPO_RUNNER": "unused-runner",
           "PPO_START": "unused.pt", "PPO_REFERENCE": "reference.pt", "PPO_POOL": "peer.pt",
           "PPO_ITERATIONS": "2", "PPO_EVAL_EVERY": "99", "PPO_SNAPSHOT_EVERY": "99",
           "PPO_WARMUP": "2", "PPO_THREADS": "3", "PPO_UPDATE_ARGS": "--device cpu",
           "UPDATE_MARKER": str(marker), "UPDATE_EXIT": str(exit_code)}
    env.pop("PPO_LOCK", None)
    lock_path = tmp_path / "simulation.lock"
    if use_lock:
        env["PPO_LOCK"] = str(lock_path)
    with lock_path.open("w") as lock, (tmp_path / "output").open("w+") as output:
        fcntl.flock(lock, fcntl.LOCK_EX)
        process = subprocess.Popen(["bash", str(LOOP)], env=env, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            if use_lock:
                deadline = time.monotonic() + 5
                while "iter-002 update" not in (tmp_path / "output").read_text():
                    assert process.poll() is None
                    assert time.monotonic() < deadline, "PPO did not reach update"
                    time.sleep(0.01)
                with pytest.raises(subprocess.TimeoutExpired):
                    process.wait(timeout=0.2)
                assert not marker.exists(), "PPO update started while another process held the lock"
            else:
                # Without PPO_LOCK, an unrelated held lock must not block update.
                process.wait(timeout=5)
            fcntl.flock(lock, fcntl.LOCK_UN)
            assert process.wait(timeout=5) == exit_code, (tmp_path / "output").read_text()
            arguments = json.loads(marker.read_text())
            assert arguments[0] == str(LOOP.parent / "body_rl_ppo.py")
            assert arguments[1] == "update"
            assert arguments[arguments.index("--threads") + 1] == "3"
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
