"""Action-change evaluation contracts; run on Ryzen with the other BC tests."""

from pathlib import Path
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nav_bc_train as train


def test_training_batches_cap_dagger_and_retain_every_episode():
    teachers = [{"id": f"teacher-{i}", "source_kind": "teacher"} for i in range(3)]
    dagger = [{"id": f"dagger-{i}", "source_kind": "dagger"} for i in range(11)]
    batches = train.training_batches(teachers + dagger, 5, 17)
    assert batches == train.training_batches(teachers + dagger, 5, 17)
    assert {entry["id"] for batch in batches for entry in batch} == {
        entry["id"] for entry in teachers + dagger}
    for batch in batches:
        assert len(batch) <= 5
        assert sum(entry["source_kind"] == "dagger" for entry in batch) <= len(batch) // 2


def test_training_batches_require_teacher_and_mixed_batch_capacity():
    with pytest.raises(ValueError, match="successful teacher"):
        train.training_batches([{"source_kind": "dagger"}], 8, 17)
    with pytest.raises(ValueError, match="at least two"):
        train.training_batches([{"source_kind": "teacher"}, {"source_kind": "dagger"}], 1, 17)


def test_dagger_step_cap_uses_valid_steps_when_episode_lengths_differ():
    mask = np.array([[1, 1, 0, 0], [1, 0, 0, 0], [1, 1, 1, 1], [1, 1, 1, 1]], bool)
    train.cap_dagger_steps(mask, [False, False, True, True], np.random.default_rng(17))
    assert mask[:2].sum() == 3
    assert mask[2:].sum() == 3
    assert not mask[:2, 2:].any()
    # A tail with no teacher labels cannot form a DAgger-only update.
    mask = np.array([[0, 0], [1, 1]], bool)
    train.cap_dagger_steps(mask, [False, True], np.random.default_rng(17))
    assert not mask.any()


def test_index_rejects_v1_semantics_even_with_same_vector_dimensions(tmp_path):
    index = {"cache_version": train.CACHE_VERSION, "vector_size": train.VECTOR_SIZE,
             "preprocessing": {**train.PREPROCESSING, "roads_schema": "next_node_v1"},
             "episodes": []}
    (tmp_path / "index.json").write_text(json.dumps(index))
    with pytest.raises(ValueError, match="NAVDEMO v2"):
        train.read_index(tmp_path)


@pytest.mark.parametrize("mask, expected_count, expected_accuracy", [
    ([1, 0, 1], 2, .5),
    ([0, 0, 0], 0, None),
])
def test_evaluate_action_changes_uses_mask_and_handles_no_changes(monkeypatch, mask,
                                                                expected_count, expected_accuracy):
    truth = np.tile(np.array([2, 1, 1, 1, 0, 0], dtype=np.int64), (3, 1))
    prediction = truth.copy()
    prediction[0, :3] = [1, 0, 0]
    prediction[1, 3:] = [0, 1, 1]
    episode = {"labels": truth, "baseline": truth, "situations": np.zeros((3, 3), np.uint8),
               "action_change": np.array(mask, np.uint8)}
    monkeypatch.setattr(train, "load", lambda *_: episode)
    monkeypatch.setattr(train, "predict_episode", lambda *_: prediction)
    result = train.evaluate(SimpleNamespace(eval=lambda: None), None, [{"region": "farm"}], "cpu")
    changed = result["action_change"]
    assert changed["steps"] == expected_count
    assert changed["all_heads_accuracy"] == expected_accuracy
    for head in train.HEADS:
        assert sum(changed["model"][head]["support"]) == expected_count
        if expected_count:
            expected = .5 if head in ("forward", "strafe", "turn") else 1.
            assert changed["model"][head]["accuracy"] == expected
        else:
            assert changed["model"][head]["accuracy"] == 0
