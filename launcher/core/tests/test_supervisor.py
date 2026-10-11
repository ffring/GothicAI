import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from gothic_launcher import supervisor as sv
from gothic_launcher.commands import Ports, Proc
from gothic_launcher.state import AlreadyRunning, SessionFile
from gothic_launcher.supervisor import Event, Supervisor

FAKE = str(Path(__file__).with_name("fake_service.py"))


class Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._changed = asyncio.Event()

    def __call__(self, e: Event) -> None:
        self.events.append(e)
        self._changed.set()

    def count(self, proc: str, kind: str) -> int:
        return sum(1 for e in self.events if e.proc == proc and e.kind == kind)

    async def wait(self, pred: Callable[[], bool], timeout: float = 20) -> None:
        async with asyncio.timeout(timeout):
            while not pred():
                self._changed.clear()
                await self._changed.wait()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(sv, "RESTART_DELAYS_S", (0.1,))
    monkeypatch.setattr(sv, "STATUS_EVERY_S", 0.1)
    monkeypatch.setattr(sv, "STOP_GRACE_S", 1.0)


@pytest.fixture
def session(tmp_path):
    return SessionFile(tmp_path / "launcher.json")


def service(name: str, port: int, tmp_path: Path, *extra: str) -> Proc:
    argv = [sys.executable, FAKE, "service", "--port", str(port), "--pidfile", str(tmp_path / f"{name}.pid")]
    return Proc(name, [*argv, *extra], tmp_path, health=f"http://127.0.0.1:{port}/health")


def game(tmp_path: Path, *extra: str) -> Proc:
    argv = [sys.executable, FAKE, "game", "--pidfile", str(tmp_path / "game.pid"), *extra]
    return Proc("game", argv, tmp_path / "home", {"GOTHIC_AI_URL": "ws://x"}, restart=False, stamp=True)


def pids(path: Path) -> list[int]:
    return [int(p) for p in path.read_text().split()]


async def gone(pid: int, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_the_game_starts_once_the_services_are_ready_and_ends_the_session(tmp_path, session):
    ports = Ports.free()
    services = [
        service("asr", ports.asr, tmp_path, "--ready-after", "0.5", "--child"),
        service("server", ports.server, tmp_path),
    ]
    checks = [f"http://127.0.0.1:{p}/health" for p in (ports.asr, ports.server)]
    rec = Recorder()
    sup = Supervisor(
        services, game(tmp_path, "--check", *checks, "--code", "7"), tmp_path / "logs", rec, session=session
    )

    assert await sup.run() == 7

    kinds = [(e.proc, e.kind) for e in rec.events]
    assert kinds.index(("game", "starting")) > max(
        kinds.index(("asr", "ready")), kinds.index(("server", "ready"))
    )
    assert ("game", "exited") in kinds
    for name in ("asr", "server"):
        for pid in pids(tmp_path / f"{name}.pid"):  # the asr child too
            assert await gone(pid)
    game_log = (tmp_path / "logs" / "game.log").read_text()
    assert re.search(r"^\d\d:\d\d:\d\d game started, server ws://x$", game_log, re.M)
    assert "listening on" in (tmp_path / "logs" / "asr.log").read_text()
    assert session.path.read_text() == ""  # released: nothing left to clean up


async def test_a_service_that_dies_is_restarted_without_the_rest_of_its_group(tmp_path, session):
    port = Ports.free().tts
    rec = Recorder()
    sup = Supervisor(
        [service("tts", port, tmp_path, "--child")], None, tmp_path / "logs", rec, session=session
    )
    run = asyncio.create_task(sup.run())
    await rec.wait(lambda: rec.count("tts", "ready") == 1)
    leader, child = pids(tmp_path / "tts.pid")

    os.kill(leader, signal.SIGKILL)  # like `kill -9` of `uv run`: its child would keep the port
    await rec.wait(lambda: rec.count("tts", "ready") == 2)

    assert await gone(child)
    new_leader = pids(tmp_path / "tts.pid")[0]
    assert new_leader != leader
    assert rec.count("tts", "exited") == 1 and rec.count("tts", "restarting") == 1
    assert (tmp_path / "logs" / "tts.log").read_text().count("starting") == 2  # one log, appended
    sup.stop()
    assert await run == 0
    assert await gone(new_leader)


async def test_a_service_that_cannot_start_stops_the_others(tmp_path, session):
    ports = Ports.free()
    ok = service("asr", ports.asr, tmp_path)
    bad = service("tts", ports.tts, tmp_path, "--ready-after", "30", "--exit-after", "0.3", "--code", "3")
    rec = Recorder()

    assert await Supervisor([ok, bad], game(tmp_path), tmp_path / "logs", rec, session=session).run() == 1

    failed = [e for e in rec.events if e.kind == "failed"]
    assert failed[0].proc == "tts" and "code 3" in failed[0].detail and "boom" in failed[0].detail
    assert not any(e.proc == "game" for e in rec.events)
    assert await gone(pids(tmp_path / "asr.pid")[0])


async def test_stop_ends_the_game_and_kills_what_ignores_sigterm(tmp_path, session):
    port = Ports.free().server
    rec = Recorder()
    sup = Supervisor(
        [service("server", port, tmp_path, "--ignore-term")],
        game(tmp_path, "--run", "600"),
        tmp_path / "logs",
        rec,
        session=session,
    )
    run = asyncio.create_task(sup.run())
    await rec.wait(lambda: rec.count("game", "starting") == 1)
    async with asyncio.timeout(10):
        while not ((tmp_path / "game.pid").exists() and (tmp_path / "game.pid").read_text()):
            await asyncio.sleep(0.05)

    sup.stop()

    assert await run == 0
    assert await gone(int((tmp_path / "game.pid").read_text()))
    assert await gone(pids(tmp_path / "server.pid")[0])
    assert {e.proc for e in rec.events if e.kind == "stopped"} == {"game", "server"}


async def test_health_changes_are_reported(tmp_path, session):
    health = tmp_path / "health.json"
    health.write_text(json.dumps({"ok": True, "providers": [{"name": "inworld", "ok": True}]}))
    rec = Recorder()
    port = Ports.free().tts
    sup = Supervisor(
        [service("tts", port, tmp_path, "--health-file", str(health))],
        None,
        tmp_path / "logs",
        rec,
        session=session,
    )
    run = asyncio.create_task(sup.run())
    await rec.wait(lambda: rec.count("tts", "ready") == 1)

    health.write_text(json.dumps({"ok": True, "providers": [{"name": "inworld", "ok": False}]}))
    await rec.wait(lambda: rec.count("tts", "status") == 1)

    status = next(e for e in rec.events if e.kind == "status")
    assert status.data == {"ok": True, "providers": [{"name": "inworld", "ok": False}]}
    sup.stop()
    await run


def test_a_second_launcher_is_refused(tmp_path):
    first = SessionFile(tmp_path / "launcher.json")
    first.acquire()
    with pytest.raises(AlreadyRunning) as err:
        SessionFile(tmp_path / "launcher.json").acquire()
    assert err.value.pid == os.getpid()
    first.release()
    SessionFile(tmp_path / "launcher.json").acquire()


def test_groups_left_by_a_killed_launcher_are_stopped(tmp_path):
    left = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)
    path = tmp_path / "launcher.json"
    path.write_text(json.dumps({"pid": 1, "written": time.time(), "groups": {"tts": left.pid}}))

    assert SessionFile(path).acquire() == {"tts": left.pid}
    assert left.wait(timeout=5) == -signal.SIGKILL


def test_a_reused_group_id_is_left_alone(tmp_path):
    newer = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)
    path = tmp_path / "launcher.json"
    # the list is older than the process: its group id belonged to someone else then
    path.write_text(json.dumps({"pid": 1, "written": time.time() - 3600, "groups": {"tts": newer.pid}}))
    try:
        assert SessionFile(path).acquire() == {}
        assert newer.poll() is None
    finally:
        newer.kill()
        newer.wait()
