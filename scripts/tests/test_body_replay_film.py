"""Replay files and bench scenes for filming recorded arena duels, on synthetic runner files."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1]/"body_replay_film.py"


def write(folder, setup=None, owned=False):
    folder.mkdir(parents=True)
    lines = lambda rows: "".join(json.dumps(row)+"\n" for row in rows)
    initial = {"scenario": "native_duel", "seed": 90014, "side": 0, "distance_cm": 400, "timeout_ms": 60000,
               "primary_entity_id": 1, "opponent_entity_id": 2}
    if setup:
        initial["setup"] = setup
    if owned:
        initial["opponent_owned"] = True
    final = {**initial, "outcome": "death_or_down", "reason": "opponent_down", "elapsed_game_ms": 16133}
    (folder/"arena-events.jsonl").write_text(lines([{"kind": "reset_ready", "arena": initial}, {"kind": "terminal", "arena": final}]))
    command = {"provider": "stage9-rl", "goal_version": 1, "target_entity_id": 2, "ttl_ms": 100}
    (folder/"body-control-events.jsonl").write_text(lines([
        {**command, "entity_id": 1, "phase": "accepted", "sim_step": 17, "command_id": 1, "action_id": 1, "observation_seq": 3},
        {**command, "entity_id": 1, "phase": "completed", "sim_step": 30, "command_id": 1, "action_id": 1, "observation_seq": 3},
        {**command, "entity_id": 2, "phase": "accepted", "sim_step": 18, "command_id": 1, "action_id": 6, "observation_seq": 4},
        {**command, "entity_id": 1, "phase": "accepted", "sim_step": 44, "command_id": 2, "action_id": 6, "observation_seq": 61}]))
    (folder/"observations.jsonl").write_text(lines([{"entity_id": 1, "observation_seq": 1, "sim_step": 16},
                                                    {"entity_id": 2, "observation_seq": 2, "sim_step": 16}]))


def test_primary_commands_count_steps_from_the_reset_sample(tmp_path):
    write(tmp_path/"ep")
    done = subprocess.run([sys.executable, str(SCRIPT), "prepare", f"r2u-90014={tmp_path/'ep'}", "--output", str(tmp_path/"film")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    replay = json.loads((tmp_path/"film/r2u-90014/replay.json").read_text())
    assert replay["arena"] == {"scenario": "native_duel", "seed": 90014, "side": 0, "distance_cm": 400, "timeout_ms": 60000}
    assert replay["provider"] == "stage9-rl" and replay["outcome"]["reason"] == "opponent_down"
    assert [(c["step"], c["command_id"], c["action_id"], c["observation_seq"]) for c in replay["commands"]] == [(1, 1, 1, 3), (28, 2, 6, 61)]


def test_scene_replaces_the_arena_line_and_keeps_the_opening(tmp_path):
    write(tmp_path/"ep")
    scenario = tmp_path/"arena.bench"
    scenario.write_text("set time 12 0\nwait 500\nbodyarena 17\nwait 600100\n")
    subprocess.run([sys.executable, str(SCRIPT), "prepare", f"bc={tmp_path/'ep'}", "--output", str(tmp_path/"film"),
                    "--scenario", str(scenario), "--tail-ms", "2000"], check=True, capture_output=True)
    lines = (tmp_path/"film/bc/scene.bench").read_text().splitlines()
    assert lines[:2] == ["set time 12 0", "wait 500"]
    assert lines[3] == f"bodyreplay {(tmp_path/'film/bc/replay.json').resolve()} 2000"
    assert "bodyarena 17" not in lines and lines[-3:] == ["rec duel", "wait 600100", "stop"]


def test_a_wall_arena_keeps_its_reset_fields_but_not_the_placement_proof(tmp_path):
    write(tmp_path/"ep", setup={"schema_version": 1, "geometry": "near_wall", "yaw_offset_deg": 0, "center_offset_cm": [0, 0],
                                "geometry_proof": {"kind": "existing_static_wall"}})
    subprocess.run([sys.executable, str(SCRIPT), "prepare", f"wall={tmp_path/'ep'}", "--output", str(tmp_path/"film")],
                   check=True, capture_output=True)
    arena = json.loads((tmp_path/"film/wall/replay.json").read_text())["arena"]
    assert arena["geometry"] == "near_wall" and arena["center_offset_cm"] == [0, 0]
    assert "geometry_proof" not in arena and "schema_version" not in arena


def test_a_self_play_duel_replays_both_actors_in_accepted_order(tmp_path):
    write(tmp_path/"ep", owned=True)
    subprocess.run([sys.executable, str(SCRIPT), "prepare", f"sp={tmp_path/'ep'}", "--output", str(tmp_path/"film")],
                   check=True, capture_output=True)
    replay = json.loads((tmp_path/"film/sp/replay.json").read_text())
    assert replay["arena"]["opponent_owned"] is True
    assert [(c["step"], c["action_id"], c.get("actor", "primary")) for c in replay["commands"]] == [
        (1, 1, "primary"), (2, 6, "opponent"), (28, 6, "primary")]


@pytest.mark.parametrize("platform,scale,audio,expected", [
    ("darwin", 1, True, 0), ("darwin", 2, True, 0), ("darwin", 2, False, 1), ("linux", None, False, 0),
])
def test_render_native_macos_with_game_audio_and_linux_xvfb(tmp_path, monkeypatch, platform, scale, audio, expected):
    spec = importlib.util.spec_from_file_location("body_replay_film", SCRIPT)
    film = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(film)
    monkeypatch.setattr(film.sys, "platform", platform)
    def check_output(argv, *, text):
        assert platform == "darwin" and argv[:3] == ["osascript", "-l", "JavaScript"]
        assert "backingScaleFactor" in argv[-1] and text is True
        return str(scale)+"\n"

    monkeypatch.setattr(film.subprocess, "check_output", check_output)
    for name in ("VK_DRIVER_FILES", "VK_ICD_FILENAMES", "LP_NUM_THREADS", "ALSOFT_DRIVERS", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.setenv(name, "inherited-linux-setting")
    monkeypatch.setenv("TEMPEST_AUDIO_LOOPBACK", "0")
    monkeypatch.setenv("OPENGOTHIC_PHYSICS_THREADS", "8")
    monkeypatch.delenv("OPENGOTHIC_BENCH_ENCODER", raising=False)
    folder = tmp_path/"p30-100000"
    folder.mkdir()
    (folder/"scene.bench").write_text("rec duel\nwait 1000\nstop\n")
    # A silent rerun must fail even if the previous take left an audio file behind.
    (folder/"duel.wav").write_bytes(b"old take")
    save = tmp_path/"save.sav"
    save.write_bytes(b"arena save")
    args = SimpleNamespace(scenes=[folder], game=tmp_path/"Gothic2Notr", assets=tmp_path/"assets", save=save,
                           size="1920x1080", threads=4, textures="", timeout_s=1800)
    calls = []

    def run(argv, *, cwd, env, stdout, stderr, timeout):
        calls.append((argv, env))
        assert cwd == folder/"run" and (cwd/"save_slot_1.sav").read_bytes() == b"arena save"
        assert env["TEMPEST_WINDOW_SIZE"] == ("960x540" if scale == 2 else "1920x1080")
        assert env["OPENGOTHIC_BENCH_REALTIME"] == "1"
        assert env["OPENGOTHIC_BENCH"] == str(folder/"scene.bench")
        assert env["OPENGOTHIC_BENCH_OUT"] == str(folder)
        assert "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env
        if platform == "darwin":
            assert argv[0] == str(args.game) and "xvfb-run" not in argv
            assert not any(name in env for name in ("VK_DRIVER_FILES", "VK_ICD_FILENAMES", "LP_NUM_THREADS", "ALSOFT_DRIVERS"))
            assert env["TEMPEST_AUDIO_LOOPBACK"] == "48000"
            assert env["OPENGOTHIC_PHYSICS_THREADS"] == "0"
            assert env["OPENGOTHIC_BENCH_ENCODER"] == "-c:v h264_videotoolbox -b:v 24M"
            config = (cwd/"Gothic.ini").read_text()
            assert "vidResIndex=0\n" in config
            for setting in ("soundVolume=1", "musicVolume=0", "musicEnabled=0", "worldSounds=0"):
                assert setting+"\n" in config
        else:
            assert argv[:7] == ["nice", "-n", "10", "xvfb-run", "-a", "-s", "-screen 0 1920x1080x24"]
            assert env["VK_DRIVER_FILES"] == env["VK_ICD_FILENAMES"] == "/usr/share/vulkan/icd.d/lvp_icd.json"
            assert env["LP_NUM_THREADS"] == "4" and env["ALSOFT_DRIVERS"] == "null"
            assert not (cwd/"Gothic.ini").exists()
        (folder/"replay.jsonl").write_text(json.dumps({"kind": "replay_end", "accepted": 10, "rejected": 0})+"\n")
        (folder/"duel.mp4").write_bytes(b"video")
        if audio:
            (folder/"duel.wav").write_bytes(b"audio")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(film.subprocess, "run", run)
    assert film.render(args) == expected
    assert len(calls) == 1
