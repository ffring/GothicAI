"""Held-out metrics from actual expert decision opportunities."""
from __future__ import annotations

from pathlib import Path
import time

from .data import dataset_provenance, load_full_split, load_split, tensor_batch
from .schema import ACTIONS, atomic_json, model_host_guard


def summarize_confusion(confusion: list[list[int]]) -> dict:
    count = len(ACTIONS)
    rows = []
    for action in range(count):
        true_positive = confusion[action][action]
        support = sum(confusion[action])
        false_positive = sum(confusion[index][action] for index in range(count)) - true_positive
        false_negative = support - true_positive
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        rows.append({"action_id": action, "action": ACTIONS[action], "support": support,
                     "tp": true_positive, "fp": false_positive, "fn": false_negative,
                     "precision": precision, "recall": recall, "f1": f1})
    supported = [row for row in rows[1:] if row["support"] > 0]
    total = sum(sum(row) for row in confusion)
    return {"label_count": total, "accuracy": sum(confusion[index][index] for index in range(count)) / total if total else 0.0,
            "macro_f1": sum(row["f1"] for row in rows[1:]) / (count - 1),
            "macro_f1_classes": list(ACTIONS[1:]),
            "macro_f1_rule": "All included actions 1..11; absent support and zero division contribute zero.",
            "macro_f1_supported": sum(row["f1"] for row in supported) / len(supported) if supported else 0.0,
            "per_action": rows,
            "confusion": confusion,
            "missing_heldout_actions": [row["action"] for row in rows[1:] if not row["support"]]}


def evaluate_chunks(model, chunks: list[dict], *, device="cpu", batch_size: int = 8,
                    class_weights=None, include_predictions=False) -> dict:
    model_host_guard()
    import torch
    import torch.nn.functional as functional
    model.eval()
    sequence_count = len(chunks)
    # Every chunk starts with its own reset and recorded burn-in context. A
    # chunk without a supervised opportunity cannot affect another chunk's
    # memory or any classification metric. Keep the remaining chunks whole.
    chunks = [chunk for chunk in chunks if any(row["loss_mask"] for row in chunk["observations"])]
    confusion = [[0] * len(ACTIONS) for _ in ACTIONS]
    predictions, loss_sum, weighted_sum, weight_sum, label_count = [], 0.0, 0.0, 0.0, 0
    started = time.monotonic()
    weights = torch.tensor(class_weights, dtype=torch.float32, device=device) if class_weights is not None else None
    with torch.inference_mode():
        for offset in range(0, len(chunks), batch_size):
            selected = chunks[offset:offset + batch_size]
            features, masks, resets, labels, loss_mask = tensor_batch(selected, device)
            logits, _ = model(features, masks, resets)
            if not bool(loss_mask.any()):
                continue
            active_logits, active_labels = logits[loss_mask], labels[loss_mask]
            losses = functional.cross_entropy(active_logits, active_labels, reduction="none")
            selected_weights = weights[active_labels] if weights is not None else torch.ones_like(losses)
            loss_sum += float(losses.sum())
            weighted_sum += float((losses * selected_weights).sum())
            weight_sum += float(selected_weights.sum())
            label_count += active_labels.numel()
            chosen = logits.argmax(-1).cpu().tolist()
            log_probs = functional.log_softmax(logits, dim=-1).cpu().tolist() if include_predictions else None
            for batch_index, chunk in enumerate(selected):
                for row_index, row in enumerate(chunk["observations"]):
                    if not row["loss_mask"]:
                        continue
                    actual, predicted = row["label"], chosen[batch_index][row_index]
                    if not row["action_mask"][predicted]:
                        raise ValueError("Policy selected a masked primitive")
                    confusion[actual][predicted] += 1
                    if include_predictions:
                        decision_mask = row["action_mask"]
                        predictions.append({"episode_id": chunk["episode_id"], "identity": chunk["identity"],
                                            "entity_id": chunk["identity"][-1], "observation_seq": row["observation_seq"],
                                            "t_game_ms": row["t_game_ms"], "decision_id": row.get("decision_id"),
                                            "source_observation_seq": row.get("source_observation_seq", row["observation_seq"]),
                                            "true_action": actual, "chosen_action": predicted,
                                            "action_mask": decision_mask,
                                            "raw_recorded_action_mask": row["raw_recorded_action_mask"],
                                            "action_log_probs": [value if allowed else None for value, allowed in
                                                                 zip(log_probs[batch_index][row_index], decision_mask)],
                                            "true_action_nll": -log_probs[batch_index][row_index][actual],
                                            "train_class_weight": float(class_weights[actual]) if class_weights is not None else 1.0})
    result = summarize_confusion(confusion)
    if label_count != result["label_count"] or label_count == 0:
        raise ValueError("Evaluation split has no true expert labels")
    result.update({"nll": loss_sum / label_count, "weighted_nll": weighted_sum / weight_sum if weight_sum else None,
                   "wall_seconds": time.monotonic() - started, "sequence_count": sequence_count,
                   "forwarded_sequence_count": len(chunks), "empty_sequences_skipped": sequence_count - len(chunks),
                   "burn_in_labels_excluded": True, "predictions": predictions})
    return result


def evaluate_checkpoint(checkpoint: Path | str, dataset: Path | str, *, output: Path | str, split: str,
                        device="cpu", batch_size=8, chunk_length=32, burn_in=8, dataset_code=None,
                        evaluation_mode="chunked") -> dict:
    from .checkpoint import load_checkpoint
    artifact = load_checkpoint(checkpoint, device)
    if evaluation_mode == "continuous":
        loader, chunks = load_full_split(dataset, split, dataset_code=dataset_code)
        evaluator = evaluate_continuous
    elif evaluation_mode == "chunked":
        loader, chunks = load_split(dataset, split, chunk_length=chunk_length, burn_in=burn_in, dataset_code=dataset_code)
        evaluator = evaluate_chunks
    else:
        raise ValueError("Unknown evaluation mode")
    provenance = dataset_provenance(loader, dataset_code)
    if artifact["metadata"]["dataset"] != provenance or artifact["normalizer"] != loader.normalizer:
        raise ValueError("Evaluation dataset/normalizer differs from frozen checkpoint")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    result = evaluator(artifact["model"], chunks, device=device, batch_size=batch_size,
                       class_weights=loader.normalizer["class_weights"], include_predictions=True)
    predictions = result.pop("predictions")
    import json
    with (output / "predictions.jsonl").open("w", encoding="utf-8") as stream:
        for row in predictions:
            row.update({"checkpoint_sha256": artifact["checkpoint_sha256"],
                        "dataset_sha256": provenance["dataset_sha256"], "split": split})
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    result.update({"kind": "body-bc-evaluation", "schema_version": 1, "split": split,
                   "checkpoint_sha256": artifact["checkpoint_sha256"], "dataset": provenance,
                   "predictions_file": "predictions.jsonl", "selection_usage": "eval only; test/OOD final reporting only",
                   "decision_mask_rule": "Shared policy_action_mask: exclude continue iff physically eligible with available primitive."})
    atomic_json(output / "metrics.json", result)
    return result


def evaluate_continuous(model, streams: list[dict], *, device="cpu", batch_size=8,
                        class_weights=None, include_predictions=False) -> dict:
    """Run entire actor streams; memory resets only at actual native boundaries."""
    identities = set()
    observation_count, reset_count = 0, 0
    for stream in streams:
        key = (stream["episode_id"], tuple(stream["identity"]))
        if key in identities:
            raise ValueError("Continuous evaluation repeats one actor/epoch/generation stream")
        identities.add(key)
        previous_sequence, previous_game_ms = None, None
        if not stream["observations"]:
            raise ValueError("Empty continuous actor stream")
        for index, row in enumerate(stream["observations"]):
            if row.get("burn_in") is not False or row.get("reset") is not (index == 0):
                raise ValueError("Continuous evaluation contains an artificial chunk reset/burn-in")
            if previous_sequence is not None and (row["observation_seq"] <= previous_sequence or
                                                   row["t_game_ms"] < previous_game_ms):
                raise ValueError("Continuous evaluation duplicates or reorders actor observations")
            previous_sequence, previous_game_ms = row["observation_seq"], row["t_game_ms"]
            observation_count += 1
            reset_count += int(row["reset"])
    result = evaluate_chunks(model, streams, device=device, batch_size=batch_size,
                             class_weights=class_weights, include_predictions=include_predictions)
    result.update({"evaluation_mode": "continuous", "context_semantics": "Full actual actor/epoch/generation stream",
                   "stream_count": len(streams), "observation_count": observation_count,
                   "actual_stream_reset_count": reset_count, "artificial_chunk_resets": 0,
                   "duplicate_burnin_labels": 0})
    return result
