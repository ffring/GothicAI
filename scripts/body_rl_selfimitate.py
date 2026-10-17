#!/usr/bin/env python3
"""Stage 9.2: filtered self-imitation of the best collected episodes.

export-bc  packs the BC dataset's train/eval chunks into one array file (run where the
           dataset's episode paths exist), checked against the parent checkpoint normalizer.
train      fine-tunes the parent policy on the BC train chunks mixed with the top-scoring
           collected episodes. Labels there are the policy's own sampled actions at its real
           decisions, inside the same masks. BC eval chunks guard against forgetting the
           teacher. The new checkpoint keeps the parent's normalizer and dataset provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

PHASE = "post_simulation_post_animation"


def pack_chunks(chunks):
    import numpy
    length = max(len(chunk["observations"]) for chunk in chunks)
    features = len(chunks[0]["observations"][0]["features"])
    out = {"features": numpy.zeros((len(chunks), length, features), numpy.float32),
           "masks": numpy.zeros((len(chunks), length, 12), bool), "resets": numpy.zeros((len(chunks), length), bool),
           "labels": numpy.zeros((len(chunks), length), numpy.int16), "loss": numpy.zeros((len(chunks), length), bool),
           "lengths": numpy.zeros(len(chunks), numpy.int32)}
    for index, chunk in enumerate(chunks):
        rows = chunk["observations"]
        out["lengths"][index] = len(rows)
        out["features"][index, :len(rows)] = [row["features"] for row in rows]
        out["masks"][index, :len(rows)] = [row["action_mask"] for row in rows]
        out["resets"][index, :len(rows)] = [row["reset"] for row in rows]
        out["labels"][index, :len(rows)] = [row["label"] or 0 for row in rows]
        out["loss"][index, :len(rows)] = [row["loss_mask"] for row in rows]
    return out


def unpack_chunks(arrays, prefix):
    chunks = []
    for index, length in enumerate(arrays[prefix+"lengths"]):
        rows = []
        for step in range(int(length)):
            loss = bool(arrays[prefix+"loss"][index, step])
            rows.append({"features": arrays[prefix+"features"][index, step].tolist(),
                         "action_mask": arrays[prefix+"masks"][index, step].tolist(),
                         "reset": bool(arrays[prefix+"resets"][index, step]),
                         "label": int(arrays[prefix+"labels"][index, step]) if loss else None,
                         "loss_mask": loss, "phase": PHASE, "periodic": True})
        chunks.append({"observations": rows, "source": prefix.rstrip("_")})
    return chunks


def episode_chunks(arrays, *, chunk_length=32, burn_in=8):
    """Same cut as the dataset loader: reset per chunk, burn-in prefix without loss."""
    total, chunks = len(arrays["labels"]), []
    for start in range(0, total, chunk_length):
        prefix = max(0, start-burn_in)
        rows = []
        for index in range(prefix, min(total, start+chunk_length)):
            label = int(arrays["labels"][index])
            loss = index >= start and label >= 1
            rows.append({"features": arrays["features"][index].tolist(), "action_mask": arrays["masks"][index].tolist(),
                         "reset": index == prefix, "label": label if loss else None, "loss_mask": loss,
                         "phase": PHASE, "periodic": True})
        if any(row["loss_mask"] for row in rows):
            chunks.append({"observations": rows, "source": "self"})
    return chunks


def export_bc(args):
    import numpy
    from gothic_body.checkpoint import load_checkpoint
    from gothic_body.data import load_split
    from gothic_body.schema import json_sha256
    parent = load_checkpoint(args.checkpoint)
    arrays = {}
    for split in ("train", "eval"):
        loader, chunks = load_split(args.dataset, split, chunk_length=32, burn_in=8, dataset_code=args.dataset_code)
        if json_sha256(loader.normalizer) != json_sha256(parent["normalizer"]):
            raise ValueError("BC dataset normalizer differs from the parent checkpoint")
        chunks = [chunk for chunk in chunks if any(row["loss_mask"] for row in chunk["observations"])]
        arrays.update({f"{split}_{key}": value for key, value in pack_chunks(chunks).items()})
        print(f"{split}: {len(chunks)} supervised chunks", flush=True)
    numpy.savez_compressed(args.output, **arrays)
    meta = {"kind": "body_bc_chunk_export", "parent_checkpoint_sha256": parent["checkpoint_sha256"],
            "normalizer_sha256": json_sha256(parent["normalizer"]), "dataset": str(args.dataset)}
    Path(str(args.output)+".json").write_text(json.dumps(meta, indent=2)+"\n")


def select_episodes(collect_dirs, top_fraction, per_collect=False):
    """The best wins without fallback, over all collections or within each one (a geometry mix keeps its shares)."""
    groups = []
    for directory in collect_dirs:
        rows = [json.loads(line) for path in sorted(Path(directory).glob("worker-*/episodes.jsonl")) for line in path.read_text().splitlines()]
        for row in rows:
            row["npz"] = str(Path(directory)/f"worker-{row['worker']:02d}"/f"{row['name']}.npz")
        groups.append(rows)
    if not per_collect:
        groups = [[row for rows in groups for row in rows]]
    summaries, selected = [], []
    for rows in groups:
        ranked = sorted((row for row in rows if row["reason"] == "opponent_down" and not row.get("fallbacks")),
                        key=lambda row: row["score"], reverse=True)
        summaries += rows
        selected += ranked[:max(1, int(len(rows)*top_fraction))]
    return summaries, sorted(selected, key=lambda row: row["score"], reverse=True)


def evaluate(model, chunks, weights, device):
    from gothic_body.evaluation import evaluate_chunks
    metrics = evaluate_chunks(model, chunks, device=device, batch_size=16, class_weights=weights)
    metrics.pop("predictions", None)
    return {key: metrics[key] for key in ("nll", "accuracy", "macro_f1") if key in metrics}


def train(args):
    import numpy
    import torch
    import torch.nn.functional as functional
    from gothic_body.checkpoint import load_checkpoint, save_checkpoint
    from gothic_body.data import tensor_batch
    torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    started = time.monotonic()
    parent = load_checkpoint(args.parent)
    model, normalizer = parent["model"], parent["normalizer"]
    bc = numpy.load(args.bc_chunks)
    bc_train, bc_eval = unpack_chunks(bc, "train_"), unpack_chunks(bc, "eval_")
    summaries, selected = select_episodes(args.collect, args.top_fraction, args.select_per_collect)
    self_chunks = [chunk for row in selected for chunk in episode_chunks(numpy.load(row["npz"]))]
    if not self_chunks:
        raise ValueError("No selected episode carries a decision")
    weights = torch.tensor(normalizer["class_weights"], dtype=torch.float32)
    before = evaluate(model, bc_eval, normalizer["class_weights"], "cpu")
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    shuffle = random.Random(args.seed)
    history = []
    half = max(1, args.batch_size//2)
    for epoch in range(1, args.epochs+1):
        model.train()
        order = list(range(len(self_chunks)))
        shuffle.shuffle(order)
        totals = [0.0, 0.0]
        for offset in range(0, len(order), half):
            batch = [self_chunks[index] for index in order[offset:offset+half]]
            batch += shuffle.sample(bc_train, min(len(bc_train), half*args.bc_ratio))
            features, masks, resets, labels, loss_mask = tensor_batch(batch, "cpu")
            optimizer.zero_grad(set_to_none=True)
            logits, _ = model(features, masks, resets)
            row_weights = weights[labels]
            if args.self_weighting == "none":
                # BC class weights balance a teacher's labels; on the policy's own samples they pull
                # it toward rare actions, so the self rows count once each.
                own = torch.tensor([chunk.get("source") == "self" for chunk in batch])[:, None].expand_as(labels)
                row_weights = torch.where(own, torch.ones_like(row_weights), row_weights)
            active = row_weights[loss_mask]
            losses = functional.cross_entropy(logits[loss_mask], labels[loss_mask], reduction="none")
            loss = (losses*active).sum()/active.sum()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
            optimizer.step()
            totals[0] += float(loss.detach())*len(batch)
            totals[1] += len(batch)
        metrics = evaluate(model, bc_eval, normalizer["class_weights"], "cpu")
        history.append({"epoch": epoch, "train_loss": totals[0]/totals[1], "bc_eval": metrics})
        print(f"epoch {epoch}: train {totals[0]/totals[1]:.4f} bc_eval nll {metrics.get('nll'):.4f} acc {metrics.get('accuracy')}", flush=True)
    args.output.mkdir(parents=True, exist_ok=False)
    record = {"kind": "filtered_self_imitation", "parent_checkpoint_sha256": parent["checkpoint_sha256"],
              "parent_training_kind": parent["metadata"]["training"].get("kind"),
              "collect": [str(path) for path in args.collect], "episodes_seen": len(summaries),
              "episodes_selected": len(selected), "selection": "wins without fallback, top fraction by score"+
              (" within each collection" if args.select_per_collect else ""),
              "top_fraction": args.top_fraction, "selected_score_min": selected[-1]["score"],
              "self_chunks": len(self_chunks), "bc_ratio": args.bc_ratio, "epochs": args.epochs,
              "learning_rate": args.learning_rate, "seed": args.seed, "self_weighting": args.self_weighting,
              "bc_eval_before": before, "history": history,
              "selected_episode_ids": [row["name"] for row in selected],
              "selected_sha256": hashlib.sha256("\n".join(sorted(row["npz"] for row in selected)).encode()).hexdigest()}
    checkpoint = save_checkpoint(args.output/"best.pt", model, normalizer=normalizer,
                                 provenance=parent["metadata"]["dataset"], training=record)
    record.update(checkpoint_sha256=checkpoint["checkpoint_sha256"], wall_s=time.monotonic()-started)
    (args.output/"training.json").write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps({"checkpoint_sha256": checkpoint["checkpoint_sha256"], "selected": len(selected),
                      "bc_eval_before": before, "bc_eval_after": history[-1]["bc_eval"]}), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export-bc")
    export.add_argument("--dataset", type=Path, required=True)
    export.add_argument("--dataset-code", type=Path, required=True)
    export.add_argument("--checkpoint", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    fit = commands.add_parser("train")
    fit.add_argument("--parent", type=Path, required=True)
    fit.add_argument("--bc-chunks", type=Path, required=True)
    fit.add_argument("--collect", type=Path, action="append", required=True)
    fit.add_argument("--output", type=Path, required=True)
    fit.add_argument("--top-fraction", type=float, default=0.25)
    fit.add_argument("--select-per-collect", action="store_true",
                     help="the top fraction of each --collect separately, e.g. one per arena geometry")
    fit.add_argument("--epochs", type=int, default=4)
    fit.add_argument("--batch-size", type=int, default=16)
    fit.add_argument("--bc-ratio", type=int, default=1)
    fit.add_argument("--learning-rate", type=float, default=1e-4)
    fit.add_argument("--threads", type=int, default=8)
    fit.add_argument("--seed", type=int, default=11)
    fit.add_argument("--self-weighting", choices=("class", "none"), default="class",
                     help="loss weights of the policy's own rows: BC class weights (9.2) or none")
    args = parser.parse_args()
    (export_bc if args.command == "export-bc" else train)(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
