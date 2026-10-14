"""All numerical tests are invoked by the dev-ubuntu launcher only."""
import json
import platform

import pytest

if platform.system() != "Linux":
    pytest.skip("Body numerical forwards must run on dev-ubuntu", allow_module_level=True)

import torch

from gothic_body.checkpoint import load_checkpoint, save_checkpoint
from gothic_body.data import tensor_batch
from gothic_body.evaluation import evaluate_chunks, summarize_confusion
from gothic_body.models import ActorConfig, create_actor
from gothic_body.schema import ACTIONS, FEATURE_VERSION


@pytest.fixture(autouse=True)
def deterministic():
    torch.set_num_threads(2)
    torch.manual_seed(17)


@pytest.mark.parametrize("architecture", ["gru", "mlp"])
def test_step_sequence_agrees_across_actor_resets(architecture):
    model = create_actor(ActorConfig(architecture, 7)).eval()
    features = torch.randn(3, 11, 7)
    masks = torch.rand(3, 11, 12) > 0.5
    masks[..., 0] = True
    resets = torch.zeros(3, 11, dtype=torch.bool)
    resets[:, 0] = True
    resets[0, 3], resets[1, 6], resets[2, 8] = True, True, True
    with torch.inference_mode():
        sequence, sequence_state = model(features, masks, resets)
        state, steps = None, []
        for index in range(11):
            logits, state = model.step(features[:, index], masks[:, index], state, resets[:, index])
            steps.append(logits)
        torch.testing.assert_close(sequence, torch.stack(steps, dim=1), rtol=0, atol=0)
        torch.testing.assert_close(sequence_state, state, rtol=0, atol=0)


@pytest.mark.parametrize("architecture", ["gru", "mlp"])
def test_reset_clears_history_and_does_not_erase_other_actor(architecture):
    model = create_actor(ActorConfig(architecture, 7)).eval()
    features, masks = torch.randn(2, 7), torch.ones(2, 12, dtype=torch.bool)
    with torch.inference_mode():
        _, dirty = model.step(features + 10, masks)
        original = dirty.clone()
        logits, state = model.step(features, masks, dirty, torch.tensor([True, False]))
        fresh, fresh_state = model.step(features[:1], masks[:1])
        other, other_state = model.step(features[1:], masks[1:], dirty[1:])
        torch.testing.assert_close(logits[:1], fresh, rtol=1e-5, atol=1e-6)
        torch.testing.assert_close(state[:1], fresh_state, rtol=1e-5, atol=1e-6)
        torch.testing.assert_close(logits[1:], other, rtol=1e-5, atol=1e-6)
        torch.testing.assert_close(state[1:], other_state, rtol=1e-5, atol=1e-6)
        assert torch.equal(dirty, original)


@pytest.mark.parametrize("architecture", ["gru", "mlp"])
def test_mask_only_changes_logits_never_memory(architecture):
    model = create_actor(ActorConfig(architecture, 7)).eval()
    features = torch.randn(1, 7)
    all_mask = torch.ones(1, 12, dtype=torch.bool)
    mask = torch.zeros_like(all_mask)
    mask[:, [1, 4]] = True
    all_logits, state = model.step(features, all_mask)
    logits, masked_state = model.step(features, mask)
    assert torch.equal(state, masked_state)
    assert torch.equal(logits[mask], all_logits[mask])
    assert torch.equal(logits.softmax(-1)[~mask], torch.zeros(10))
    assert bool(mask.gather(1, logits.argmax(-1, keepdim=True)).all())


@pytest.mark.parametrize("architecture", ["gru", "mlp"])
def test_invalid_inputs_are_rejected(architecture):
    model = create_actor(ActorConfig(architecture, 7))
    features, mask = torch.zeros(1, 7), torch.ones(1, 12, dtype=torch.bool)
    with pytest.raises(ValueError, match="Empty action"):
        model.step(features, torch.zeros_like(mask))
    with pytest.raises(ValueError, match="finite"):
        model.step(features + float("nan"), mask)
    with pytest.raises(ValueError, match="shape"):
        model.step(features, mask, torch.zeros(1, 2))
    with pytest.raises(ValueError, match="boolean"):
        model.step(features, mask, reset=torch.ones(1))
    state = model.initial_state(features)
    with pytest.raises(ValueError, match="finite"):
        model.step(features, mask, state + float("nan"))


def chunk(rows):
    return {"episode_id": "train-one", "identity": [1, 2, 3, 10], "observations": rows}


def observation(seq, *, label=None, loss=False, reset=False, burn=False):
    eligible = label is not None
    raw_mask = [True] * 12 if eligible else [True] + [False] * 11
    mask = [False] + [True] * 11 if eligible else raw_mask
    return {"features": [seq / 10] * 7, "action_mask": mask, "raw_recorded_action_mask": raw_mask, "label": label,
            "loss_mask": loss, "reset": reset, "burn_in": burn, "observation_seq": seq, "t_game_ms": seq * 50}


@pytest.mark.parametrize("architecture", ["gru", "mlp"])
def test_only_true_decision_loss_and_padding_burnin_are_excluded(architecture):
    rows = [observation(1, label=2, reset=True, burn=True), observation(2), observation(3, label=6, loss=True)]
    chunks = [chunk(rows), chunk([observation(8, reset=True)])]
    model = create_actor(ActorConfig(architecture, 7))
    features, masks, resets, labels, loss_mask = tensor_batch(chunks, "cpu")
    assert int(loss_mask.sum()) == 1
    assert not bool(masks[loss_mask][:, 0].any())
    assert not bool(masks[0, 0, 0])  # eligible burn-in also uses the shared rule
    assert bool(masks[0, 1, 0])  # locked memory row retains continue
    assert bool(masks[1, :, 0].all())  # locked and padded rows carry no loss
    logits, _ = model(features, masks, resets)
    logits.retain_grad()
    torch.nn.functional.cross_entropy(logits[loss_mask], labels[loss_mask]).backward()
    assert torch.equal(logits.grad[~loss_mask], torch.zeros_like(logits.grad[~loss_mask]))
    assert bool(logits.grad[loss_mask].abs().sum() > 0)
    result = evaluate_chunks(model, chunks, include_predictions=True, class_weights=[0] + [1] * 11)
    assert result["label_count"] == len(result["predictions"]) == 1
    assert result["predictions"][0]["observation_seq"] == 3


def normalizer():
    names = [f"test.{index}" for index in range(7)]
    return {"feature_version": FEATURE_VERSION, "source_split": "train", "feature_names": names,
            "mean": dict.fromkeys(names, 0), "std": dict.fromkeys(names, 1), "episode_ids": ["train-one"]}


@pytest.mark.parametrize("architecture", ["gru", "mlp"])
def test_checkpoint_roundtrip_frozen_schema_and_normalizer(architecture, tmp_path):
    model = create_actor(ActorConfig(architecture, 7)).eval()
    provenance = {"train_episode_ids": ["train-one"], "model_sampling": {"kind": "periodic_only", "version": 1},
                  "normalization_sha256": "normalizer-source-sha"}
    path = tmp_path / "policy.pt"
    save_checkpoint(path, model, normalizer=normalizer(), provenance=provenance, training={"kind": "test"})
    artifact = load_checkpoint(path)
    assert artifact["normalizer"] == normalizer()
    features, masks = torch.randn(1, 7), torch.ones(1, 12, dtype=torch.bool)
    with torch.inference_mode():
        expected, expected_state = model.step(features, masks)
        actual, actual_state = artifact["model"].step(features, masks)
    assert torch.equal(expected, actual)
    assert torch.equal(expected_state, actual_state)
    sidecar = path.with_suffix(".pt.json")
    metadata = json.loads(sidecar.read_text())
    metadata["metadata"]["actions"][1] = "invented"
    sidecar.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="sidecar"):
        load_checkpoint(path)


def test_checkpoint_source_byte_tamper_fails(tmp_path):
    model = create_actor(ActorConfig("gru", 7))
    path = tmp_path / "policy.pt"
    save_checkpoint(path, model, normalizer=normalizer(),
                    provenance={"train_episode_ids": ["train-one"], "normalization_sha256": "x",
                                "model_sampling": {"kind": "periodic_only", "version": 1}}, training={})
    with path.open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="bytes"):
        load_checkpoint(path)


def test_confusion_zero_support_is_explicit_in_fixed_macro():
    confusion = [[0] * 12 for _ in range(12)]
    confusion[1][1] = 8
    confusion[2][1] = 2
    metrics = summarize_confusion(confusion)
    assert metrics["label_count"] == 10
    assert metrics["accuracy"] == 0.8
    assert metrics["per_action"][1]["precision"] == 0.8
    assert metrics["per_action"][1]["recall"] == 1
    assert metrics["macro_f1"] == pytest.approx((8 / 9) / 11)
    assert metrics["macro_f1_classes"] == list(ACTIONS[1:])


def test_mlp_stack_contains_actual_recent_observations():
    model = create_actor(ActorConfig("mlp", 1, stack_size=4))
    mask, state = torch.ones(1, 12, dtype=torch.bool), None
    for value in (1, 2, 3, 4, 5):
        _, state = model.step(torch.tensor([[float(value)]]), mask, state)
    assert state.flatten().tolist() == [3, 4, 5]
