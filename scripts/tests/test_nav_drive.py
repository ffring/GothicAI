"""DAgger labels and previous inputs have deliberately different provenance."""
import gzip
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest


SPEC = importlib.util.spec_from_file_location("nav_demos", Path(__file__).resolve().parents[1] / "nav_demos.py")
demos = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demos)


def write_episode(path, drive=True, corrupt_previous=None, teacher_turn=-1):
    # Cross the reader's 64-record chunk boundary as well as ordinary adjacent rows.
    count = 65
    path.mkdir()
    records = np.zeros(count, dtype=demos.RECORD_DTYPE)
    records["time_ms"] = 1000 + np.arange(count) * 100
    records["eye"]["distance_cm"] = 1000
    records["keys"]["turn"] = teacher_turn
    records["keys"]["walk"] = 1
    actual = records["keys"].copy()
    if drive:
        actual["forward"] = 1
        actual["turn"] = 1
    records["previous_keys"][1:] = actual[:-1]
    if corrupt_previous is not None:
        records["previous_keys"][corrupt_previous]["turn"] = 0
    metadata = dict(success=False, drive=drive, decisions=count, elapsed_ms=count * 100,
                    eye_layout=[[0, 0, 1000]] * demos.RAY_COUNT)
    (path / "episode.json").write_text(json.dumps(metadata))
    with gzip.open(path / "samples.bin.gz", "wb") as stream:
        stream.write(demos.HEADER.pack(demos.MAGIC, demos.VERSION, demos.RECORD_BYTES,
                                      demos.RAY_COUNT, demos.NEAR_SIZE, demos.FAR_SIZE, demos.PERIOD_MS))
        stream.write(records.tobytes())
    actions = np.zeros((count + 1, 9))
    actions[:, 0] = np.arange(count + 1) * 100
    for column, field in enumerate(("forward", "strafe", "turn", "walk", "jump", "climb",
                                    "head_yaw_deg", "head_pitch_deg"), 1):
        actions[:-1, column] = actual[field]
    actions[-1, 4] = 1
    np.savetxt(path / "actions.txt", actions)
    return actions


def test_drive_accepts_teacher_labels_with_actual_learner_previous_keys(tmp_path):
    episode = tmp_path / "drive"
    write_episode(episode)
    result = demos.summarize(episode, validate=True)
    assert result["validated"]
    assert result["decisions"] == 65
    # A failed drive is still retained and checked for DAgger.
    assert result["episodes"] == 1
    assert result["key_counts"]["turn_left"] == 65


@pytest.mark.parametrize("row", [1, 64])
def test_drive_rejects_corrupted_previous_keys_within_and_between_chunks(tmp_path, row):
    episode = tmp_path / "drive"
    write_episode(episode, corrupt_previous=row)
    with pytest.raises(ValueError, match="previous keys mismatch"):
        demos.summarize(episode, validate=True)


def test_teacher_actions_are_still_checked_against_teacher_keys(tmp_path):
    episode = tmp_path / "teacher"
    actions = write_episode(episode, drive=False)
    assert demos.summarize(episode, validate=True)["decisions"] == 65
    actions[1, 3] = 1
    np.savetxt(episode / "actions.txt", actions)
    with pytest.raises(ValueError, match=r"actions.turn differs from recorded keys"):
        demos.summarize(episode, validate=True)


def test_teacher_fractional_axis_preserves_existing_navdemo_contract(tmp_path):
    episode = tmp_path / "teacher"
    write_episode(episode, drive=False, teacher_turn=.5)
    result = demos.summarize(episode, validate=True)
    assert result["validated"]
    assert result["decisions"] == 65
    assert result["key_counts"]["turn_right"] == 65


def test_drive_still_rejects_fractional_applied_axis(tmp_path):
    episode = tmp_path / "drive"
    actions = write_episode(episode)
    actions[1, 3] = .5
    np.savetxt(episode / "actions.txt", actions)
    with pytest.raises(ValueError, match=r"actions.turn is not a digital axis"):
        demos.summarize(episode, validate=True)
