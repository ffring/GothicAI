"""Continuous selection must retain memory across artificial BC chunk edges."""
import platform

import pytest

if platform.system() != "Linux":
    pytest.skip("Body numerical forwards run only on dev-ubuntu", allow_module_level=True)

import torch

from gothic_body.evaluation import evaluate_continuous
from gothic_body.models import ActorConfig, create_actor
from gothic_body.training import TrainConfig


def row(seq, value, *, label=None, reset=False, burn_in=False, feature_count=1):
    raw_mask = [True] * 12 if label is not None else [True] + [False] * 11
    mask = [False] + raw_mask[1:] if label is not None else raw_mask
    return {"features": [value] * feature_count, "observation_seq": seq, "t_game_ms": seq * 50,
            "label": label, "loss_mask": label is not None, "action_mask": mask,
            "raw_recorded_action_mask": raw_mask, "reset": reset, "burn_in": burn_in}


def stream(name, rows, generation=1):
    return {"episode_id": name, "identity": [1, 1, generation, 10], "observations": rows}


class PersistentSignalPolicy:
    def eval(self):
        return self

    def __call__(self, features, masks, resets):
        memory = features.new_zeros(features.shape[0])
        output = []
        for index in range(features.shape[1]):
            memory = torch.where(resets[:, index], torch.zeros_like(memory), memory) + features[:, index, 0]
            logits = features.new_zeros((features.shape[0], 12))
            logits[:, 1] = (memory <= 5) * 5.0
            logits[:, 2] = (memory > 5) * 5.0
            output.append(logits.masked_fill(~masks[:, index], torch.finfo(features.dtype).min))
        return torch.stack(output, dim=1), memory


def test_continuous_selection_keeps_old_context_and_resets_actual_next_generation():
    first = [row(1, 10, reset=True)] + [row(seq, 0) for seq in range(2, 42)] + [row(42, 0, label=2)]
    next_generation = [row(43, 0, reset=True), row(44, 0, label=1)]
    result = evaluate_continuous(PersistentSignalPolicy(),
                                 [stream("one", first), stream("one", next_generation, generation=2)],
                                 batch_size=1, class_weights=[0] + [1] * 11, include_predictions=True)
    assert result["accuracy"] == 1
    assert [item["observation_seq"] for item in result["predictions"]] == [42, 44]
    assert result["actual_stream_reset_count"] == result["stream_count"] == 2
    assert result["observation_count"] == 44
    assert result["artificial_chunk_resets"] == result["duplicate_burnin_labels"] == 0


@pytest.mark.parametrize("architecture", ["gru", "mlp"])
def test_continuous_metrics_equal_actual_step_inference_beyond_64_ticks(architecture):
    torch.set_num_threads(2)
    torch.manual_seed(17)
    model = create_actor(ActorConfig(architecture, 7)).eval()
    rows = [row(seq, (seq % 11) / 10, label=2 if seq in (32, 64, 97) else None,
                reset=seq == 1, feature_count=7) for seq in range(1, 98)]
    expected_actions, expected_nll, state = [], [], None
    with torch.inference_mode():
        for item in rows:
            logits, state = model.step(torch.tensor([item["features"]], dtype=torch.float32),
                                      torch.tensor([item["action_mask"]], dtype=torch.bool), state,
                                      torch.tensor([item["reset"]], dtype=torch.bool))
            if item["loss_mask"]:
                expected_actions.append(int(logits.argmax(-1)))
                expected_nll.append(float(-logits.log_softmax(-1)[0, item["label"]]))
    actual = evaluate_continuous(model, [stream("actual", rows)], batch_size=1,
                                  class_weights=[0] + [1] * 11, include_predictions=True)
    assert [item["chosen_action"] for item in actual["predictions"]] == expected_actions
    assert actual["nll"] == pytest.approx(sum(expected_nll) / len(expected_nll), rel=1e-6)
    assert actual["actual_stream_reset_count"] == 1


@pytest.mark.parametrize("fault", ["reset", "burn_in", "duplicate"])
def test_continuous_evaluation_rejects_chunk_artifacts(fault):
    rows = [row(1, 1, reset=True), row(2, 1, label=1)]
    if fault == "reset":
        rows[1]["reset"] = True
    elif fault == "burn_in":
        rows[1]["burn_in"] = True
    else:
        rows[1]["observation_seq"] = 1
    with pytest.raises(ValueError, match="artificial|duplicates"):
        evaluate_continuous(PersistentSignalPolicy(), [stream("invalid", rows)])


def test_default_selection_context_stays_chunked():
    assert TrainConfig().evaluation_mode == "chunked"
    assert TrainConfig(evaluation_mode="continuous", chunk_length=64, burn_in=32).evaluation_mode == "continuous"
