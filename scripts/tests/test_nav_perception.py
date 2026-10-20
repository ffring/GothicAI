"""Frozen-source and no-recording contracts for paired perception replays."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("nav_perception", Path(__file__).resolve().parents[1] / "nav_perception.py")
perception = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(perception)


def write_rows(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values))


def source_case(tmp_path, monkeypatch, times=(0, 100, 200), decisions=3):
    case = dict(id="point-case", goal_kind="point", split="train", start="START", goal="GOAL")
    source, data, task = (tmp_path / name for name in ("source", "data", "task"))
    write_rows(source / "expanded-scenarios.jsonl", [case])
    monkeypatch.setattr(perception, "EXPECTED", {"point": 1})
    directory = data / "v3-retry/point/train/point-case"
    write_rows(directory / "teacher-paths.jsonl", [dict(elapsed_ms=t, position_cm=[i, 2, 3],
        yaw_deg=90, goal_cm=[1000, 0, 0]) for i, t in enumerate(times)])
    (directory / "episode.json").write_text(json.dumps(dict(case, teacher_version=3, demo_version=2, decisions=decisions)))
    return task, source, data, directory


def test_replay_preserves_all_100ms_poses_and_never_writes_source(tmp_path, monkeypatch):
    task, source, data, directory = source_case(tmp_path, monkeypatch)
    original = {path: path.read_bytes() for path in directory.iterdir()}
    result = perception.prepare(task, source, data)
    rows = (task / "replay/poses.txt").read_text().splitlines()
    assert len(rows) == result["updates"] == 3
    assert [int(row.split()[1]) for row in rows] == [0, 100, 200]
    assert rows[-1].split()[-1] == "1"  # final pose is always sampled
    assert result["recordings_created"] is False
    assert original == {path: path.read_bytes() for path in directory.iterdir()}
    assert not list(task.rglob("samples.bin.gz"))


@pytest.mark.parametrize("times,decisions", [((0, 200, 300), 3), ((0, 100), 3)])
def test_replay_rejects_missing_source_decisions(tmp_path, monkeypatch, times, decisions):
    task, source, data, _ = source_case(tmp_path, monkeypatch, times, decisions)
    with pytest.raises(ValueError, match="interval|Incomplete"):
        perception.prepare(task, source, data)


def test_replay_rejects_wrong_frozen_identity(tmp_path, monkeypatch):
    task, source, data, directory = source_case(tmp_path, monkeypatch)
    metadata = json.loads((directory / "episode.json").read_text())
    metadata["goal"] = "DIFFERENT"
    (directory / "episode.json").write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="identity"):
        perception.prepare(task, source, data)


def test_replay_rejects_duplicate_scenario_even_if_counts_match(tmp_path, monkeypatch):
    _, source, _, _ = source_case(tmp_path, monkeypatch)
    case = next(perception.v3.rows(source / "expanded-scenarios.jsonl"))
    write_rows(source / "expanded-scenarios.jsonl", [case, case])
    monkeypatch.setattr(perception, "EXPECTED", {"point": 2})
    with pytest.raises(ValueError, match="Duplicate"):
        perception.frozen_cases(source)
