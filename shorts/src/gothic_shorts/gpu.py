"""A job on the GPU server (GOTHIC_GPU_SSH, read as align.py reads it): the files go over by scp into a fresh
folder there, a command runs in it, the results come back, the folder is deleted. Nothing but ssh, scp and
ffmpeg runs on this machine."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .align import setting


class GpuError(RuntimeError):
    pass


def _run(argv: list[str], what: str) -> str:
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode != 0:
        raise GpuError(f"{what} failed:\n{(done.stderr or done.stdout).strip()[-2000:]}")
    return done.stdout


def job(send: list[Path], command: str, fetch: dict[str, Path], what: str, dest: str | None = None) -> str:
    """Runs `command` on the server with `send` in its folder (`{dir}` in the command is that folder, the
    files keep their names); then every `fetch` (a path in that folder) comes back to its local path. Returns
    what the command printed."""
    dest = dest or setting("GOTHIC_GPU_SSH")
    if not dest:
        raise GpuError("GOTHIC_GPU_SSH is not set: the GPU server's ssh destination (environment or .env)")
    ssh = ["ssh", "-o", "BatchMode=yes", dest]
    scp = ["scp", "-q", "-o", "BatchMode=yes"]
    remote = _run([*ssh, "mktemp -d /tmp/gothic-shorts.XXXXXX"], "ssh mktemp").strip()
    try:
        if send:
            _run([*scp, *map(str, send), f"{dest}:{remote}/"], "scp to the GPU server")
        out = _run([*ssh, command.format(dir=shlex.quote(remote))], what)
        for there, here in fetch.items():
            here.parent.mkdir(parents=True, exist_ok=True)
            _run([*scp, f"{dest}:{remote}/{there}", str(here)], "scp from the GPU server")
    finally:
        subprocess.run([*ssh, f"rm -rf {shlex.quote(remote)}"], capture_output=True)
    return out
