"""Replay files and bench scenes for filming recorded arena duels, on synthetic runner files."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[1]/"body_replay_film.py"


def write(folder):
    folder.mkdir(parents=True)
    lines = lambda rows: "".join(json.dumps(row)+"\n" for row in rows)
    initial = {"scenario": "native_duel", "seed": 90014, "side": 0, "distance_cm": 400, "timeout_ms": 60000,
               "primary_entity_id": 1}
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
