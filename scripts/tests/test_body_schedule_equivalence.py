"""Lockstep/realtime decision equivalence reader on synthetic runner files."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parents[1]/"body_schedule_equivalence.py"


def write(folder, *, action=4, wall=1, skip=False, late=False):
    folder.mkdir(parents=True)
    initial = {"scenario": "native_duel", "seed": 90000, "side": 0, "distance_cm": 250, "primary_entity_id": 1}
    final = {**initial, "reason": "opponent_down", "start_game_ms": 16294, "game_ms": 30000, "elapsed_wall_ms": wall}
    lines = lambda rows: "".join(json.dumps(row)+"\n" for row in rows)
    (folder/"arena-events.jsonl").write_text(lines([{"kind": "reset_ready", "arena": initial}, {"kind": "terminal", "arena": final}]))
    (folder/"body-control-events.jsonl").write_text(lines([
        {"entity_id": 1, "phase": "accepted", "action_id": action, "t_game_ms": 16361 if late else 16327,
         "observation_seq": 3, "t_wall_ms": wall, "mode": "realtime"},
        {"entity_id": 2, "phase": "accepted", "action_id": 9, "t_game_ms": 16327}]))
    (folder/"observations.jsonl").write_text(lines([{"entity_id": 1, "observation_seq": 3, "t_game_ms": 16327,
                                                     "snapshot_wall_monotonic_ns": wall}]))
    steps = [16, 17, 19 if skip else 18]
    ipc = [{"direction": "request", "value": {"id": 1, "cmd": "arena_start"}},
           {"direction": "response", "value": {"id": 1, "sim_step": 16}}]
    for index, step in enumerate(steps[1:], start=2):
        ipc += [{"direction": "request", "value": {"id": index, "cmd": "body_observations"}},
                {"direction": "response", "value": {"id": index, "sim_step": step, "arena": {"game_ms": 16294+34*(step-16)}}}]
    (folder/"ipc.jsonl").write_text(lines(ipc))


def run(tmp_path, realtime, lockstep):
    output = tmp_path/"equivalence.json"
    done = subprocess.run([sys.executable, str(SCRIPT), "--realtime", str(realtime), "--lockstep", str(lockstep),
                           "--output", str(output)], capture_output=True, text=True)
    return done.returncode, json.loads(output.read_text())


def test_wall_clock_and_mode_fields_do_not_count(tmp_path):
    write(tmp_path/"rt/a", wall=1)
    write(tmp_path/"ls/a", wall=999)
    code, value = run(tmp_path, tmp_path/"rt", tmp_path/"ls")
    assert code == 0 and value["totals"]["identical"] == 1 == value["totals"]["pairs"]


def test_first_divergent_decision_and_prior_realtime_skip_are_reported(tmp_path):
    write(tmp_path/"rt/a", skip=True)
    write(tmp_path/"ls/a", action=1)
    code, value = run(tmp_path, tmp_path/"rt", tmp_path/"ls")
    pair = value["pairs"][0]
    assert code == 2 and not pair["identical"]
    assert pair["first_command_difference"] == {"index": 0, "keys": ["action_id"], "game_ms": 16327}
    assert pair["realtime_skipped_ticks"] == [{"sim_step": 19, "ticks": 2, "game_ms": 16396}]
    assert pair["divergence_after_realtime_fault"] is False
    assert value["totals"]["diverged_without_fault"] == 1


def test_late_realtime_command_explains_a_later_divergence(tmp_path):
    write(tmp_path/"rt/a", late=True)
    write(tmp_path/"ls/a")
    code, value = run(tmp_path, tmp_path/"rt", tmp_path/"ls")
    pair = value["pairs"][0]
    assert code == 2 and pair["realtime_late_commands"] == [{"game_ms": 16361, "observation_game_ms": 16327, "action_id": 4}]
    assert pair["divergence_after_realtime_fault"] is True
    assert value["totals"]["realtime_late_commands"] == 1 == value["totals"]["diverged_after_realtime_fault"]
