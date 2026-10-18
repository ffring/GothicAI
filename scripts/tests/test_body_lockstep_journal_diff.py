"""Lockstep journal comparison on synthetic collector files."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "body_lockstep_journal_diff.py"
FILES = ("observations.jsonl", "events.jsonl", "arena-events.jsonl", "body-control-events.jsonl")


def write(run, *, wall=1, x=100, hp=40, episode="ep-0000"):
    folder = run / "worker-0" / episode
    folder.mkdir(parents=True)
    rows = [
        {"kind": "ready", "sim_step": 16, "session_epoch": wall, "t": wall},
        {
            "sim_step": 17,
            "snapshot_wall_monotonic_ns": wall,
            "duration_ns": wall,
            "snapshot": {
                "x": x,
                "hp": hp,
                "t_wall_ms": wall,
                "entities": [{"entity_id": 1, "hp": 40, "policy_wall_ms": wall, "sample_ns": wall,
                              "session_epoch": wall, "t": wall}],
            },
        },
    ]
    text = "".join(json.dumps(row) + "\n" for row in rows)
    for name in FILES:
        (folder / name).write_text(text)
    return folder


def run_diff(a, b):
    done = subprocess.run([sys.executable, str(SCRIPT), str(a), str(b)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_wall_and_session_fields_are_ignored_recursively(tmp_path):
    write(tmp_path / "a", wall=1)
    write(tmp_path / "b", wall=999)
    assert run_diff(tmp_path / "a", tmp_path / "b") == (
        "episodes: 1 vs 1, common 1\n"
        + "".join(f"{name}: identical 1/1\n" for name in FILES)
    )


@pytest.mark.parametrize("changed", [{"x": 101}, {"hp": 39}], ids=["position", "health"])
def test_game_state_differences_are_reported(tmp_path, changed):
    write(tmp_path / "a")
    write(tmp_path / "b", **changed)
    output = run_diff(tmp_path / "a", tmp_path / "b")
    assert output.startswith("episodes: 1 vs 1, common 1\n")
    for name in FILES:
        assert f"{name}: identical 0/1\n" in output
        assert f"first difference {name} ep-0000 line 1:\n  A " in output
    key, value = next(iter(changed.items()))
    assert f'"{key}": {value}' in output


def test_different_line_counts_are_reported(tmp_path):
    write(tmp_path / "a")
    folder = write(tmp_path / "b")
    (folder / "events.jsonl").write_text('{}\n')
    output = run_diff(tmp_path / "a", tmp_path / "b")
    assert "events.jsonl: identical 0/1\n" in output
    assert "observations.jsonl: identical 1/1\n" in output
    assert "first difference events.jsonl ep-0000 line -1:\n  A 2 lines\n  B 1 lines\n" in output


def test_only_common_episode_names_are_compared(tmp_path):
    write(tmp_path / "a")
    write(tmp_path / "a", episode="ep-0001")
    write(tmp_path / "b")
    output = run_diff(tmp_path / "a", tmp_path / "b")
    assert output.startswith("episodes: 2 vs 1, common 1\n")
    assert "first difference" not in output
    for name in FILES:
        assert f"{name}: identical 1/1\n" in output
