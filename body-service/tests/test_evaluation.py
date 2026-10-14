"""Sparse evaluation retains decision context and never forwards empty chunks."""
import math
import platform

import pytest

if platform.system() != "Linux":
    pytest.skip("Numerical body evaluation runs on dev-ubuntu only", allow_module_level=True)

import torch

from gothic_body.evaluation import evaluate_chunks


class ContextSensitivePolicy:
    """A deterministic recurrence whose decision needs the burn-in prefix."""
    def __init__(self):
        self.inputs = []

    def eval(self):
        return self

    def __call__(self, features, masks, resets):
        self.inputs.append(features.clone())
        memory = features.new_zeros(features.shape[0])
        steps = []
        for index in range(features.shape[1]):
            memory = torch.where(resets[:, index], torch.zeros_like(memory), memory) + features[:, index, 0]
            logits = features.new_zeros((features.shape[0], 12))
            logits[:, 1] = torch.where(memory <= 5, 1.0, 0.0)
            logits[:, 2] = torch.where(memory > 5, 3.0, 0.0)
            steps.append(logits.masked_fill(~masks[:, index], torch.finfo(logits.dtype).min))
        return torch.stack(steps, dim=1), memory


def row(seq, value, *, label=None, reset=False, burn_in=False):
    raw_mask = [True, True, True] + [False] * 9 if label is not None else [True] + [False] * 11
    mask = [False] + raw_mask[1:] if label is not None else raw_mask
    return {"features": [value], "observation_seq": seq, "t_game_ms": seq * 50,
            "label": label, "loss_mask": label is not None and not burn_in,
            "action_mask": mask, "raw_recorded_action_mask": raw_mask, "reset": reset, "burn_in": burn_in}


def chunk(name, rows):
    return {"episode_id": name, "identity": [1, 1, 1, 10], "observations": rows}


def test_skipped_empty_chunks_preserve_burnin_predictions_order_and_losses():
    policy = ContextSensitivePolicy()
    chunks = [chunk("empty-before", [row(1, 999, reset=True)]),
              chunk("decision-two", [row(8, 1, reset=True, burn_in=True), row(9, 2), row(11, 3, label=2)]),
              chunk("empty-between", [row(15, 999, reset=True)]),
              chunk("decision-one", [row(20, 1, reset=True, burn_in=True), row(22, 1, label=1)]),
              chunk("empty-after", [row(30, 999, reset=True)])]
    weights = [0, 1, 2] + [1] * 9
    result = evaluate_chunks(policy, chunks, batch_size=2, class_weights=weights, include_predictions=True)
    assert result["accuracy"] == 1
    assert result["confusion"][1][1] == result["confusion"][2][2] == 1
    assert [(row["episode_id"], row["observation_seq"]) for row in result["predictions"]] == [
        ("decision-two", 11), ("decision-one", 22)]
    assert not any(bool((features == 999).any()) for features in policy.inputs)
    assert policy.inputs[0][0, :, 0].tolist() == [1, 2, 3]
    first, second = math.log1p(math.exp(-3)), math.log1p(math.exp(-1))
    assert result["nll"] == pytest.approx((first + second) / 2, rel=1e-6)
    assert result["weighted_nll"] == pytest.approx((2 * first + second) / 3, rel=1e-6)
    assert result["sequence_count"] == 5
    assert result["forwarded_sequence_count"] == 2
    assert result["empty_sequences_skipped"] == 3


def test_all_empty_split_fails_without_any_policy_forward():
    policy = ContextSensitivePolicy()
    with pytest.raises(ValueError, match="no true expert labels"):
        evaluate_chunks(policy, [chunk("empty", [row(1, 999, reset=True)])])
    assert policy.inputs == []
