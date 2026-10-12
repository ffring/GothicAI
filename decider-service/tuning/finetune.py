"""Fine-tune Strands Decider v1-2610 on Gothic scenes labelled by gpt-6.1-sol (runs on the GPU host).

Continues the parent's own LoRA adapter and pointer head; strands_decider.train's `init_from` would
swap the head for a fresh one, so the trainer is reused with the parent loaded in its place. Replay
rows (make_replay.py) carry the parent's own answer distributions as the teacher term, so general
skills stay where they were. Afterwards the temperatures are refitted on the Gothic validation rows
(noul keeps the parent's: there are no Gothic yes/no rows), and both models are scored on held-out
replay rows to show what was lost elsewhere.

On the GPU host, in the decider service's venv (needs ~10 GB of GPU memory):
    cd ~/gothic-ai/tuning
    ~/gothic-ai/decider/.venv/bin/python finetune.py --data data --out ckpt/g1
"""

from __future__ import annotations

import argparse
import json
import os
import time

import torch
from strands_decider import train as trainer
from strands_decider.data import replay
from strands_decider.data.format import load_examples
from strands_decider.evaluate import collect_logits, fit_temperature, fit_temperature_by_kind
from strands_decider.modeling import StrandsDeciderModel, checkpoint_dir, config_path

PARENT = "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610"


def accuracy(checkpoint: str, examples: list) -> float:
    model = StrandsDeciderModel.load(checkpoint)
    with torch.no_grad():
        logits, labels, _, _ = collect_logits(model, examples, batch_size=16, max_length=2048)
    del model
    torch.cuda.empty_cache()
    return float((logits.argmax(-1) == labels).float().mean())


def calibrate(checkpoint: str, val: list, parent_cfg: dict) -> dict[str, float]:
    """Temperatures fitted on the Gothic validation rows, per kind where there are enough of them
    (strands' own calibrate wants 200 per kind), else one for all; noul keeps the parent's."""
    model = StrandsDeciderModel.load(checkpoint)
    with torch.no_grad():
        logits, labels, slots, exs = collect_logits(model, val, batch_size=16, max_length=2048)
    del model
    torch.cuda.empty_cache()
    eps = parent_cfg["ordinal_smoothing"]
    t = float(fit_temperature(logits, labels, slots))
    by_kind = fit_temperature_by_kind(logits, labels, slots, exs, min_examples=40, ordinal_smoothing=eps)
    temps = {"noul": parent_cfg["temperature_by_kind"]["noul"]}
    temps |= {ex.kind: t for ex in exs} | by_kind
    with open(config_path(checkpoint), encoding="utf-8") as fh:
        conf = json.load(fh)
    conf["temperature"], conf["temperature_by_kind"] = t, temps
    with open(config_path(checkpoint), "w", encoding="utf-8") as fh:
        json.dump(conf, fh, indent=2)
    return temps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="the tuning data directory (train/, replay/)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--parent", default=PARENT)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--head-lr", type=float, default=2e-4)
    ap.add_argument("--replay-weight", type=float, default=1.0)
    ap.add_argument("--micro-batch", type=int, default=8)
    ap.add_argument("--grad-accum", type=int, default=4)
    args = ap.parse_args()

    train_file = os.path.join(args.data, "train", "gothic_train.jsonl")
    val_file = os.path.join(args.data, "train", "gothic_val.jsonl")
    replay_file = os.path.join(args.data, "replay", "replay.jsonl")
    heldout_file = os.path.join(args.data, "replay", "replay_heldout.jsonl")
    teacher_file = os.path.join(args.out, "replay_teacher.jsonl")
    os.makedirs(args.out, exist_ok=True)
    t0 = time.time()

    # the parent's own distributions on the replay rows, indexed past the Gothic rows
    if not os.path.exists(teacher_file):
        replay.main([args.parent, "--src", replay_file, "--out", teacher_file, "--shift-by", train_file])

    def load_parent(_cfg, attn_implementation=None):
        model = StrandsDeciderModel.load(args.parent, attn_implementation=attn_implementation)
        # trained at temperature 1 like the parent was; refitted below
        model.config.temperature = 1.0
        model.config.temperature_by_kind = {}
        for name, p in model.torso.named_parameters():
            p.requires_grad = "lora_" in name
        for p in model.head.parameters():
            p.requires_grad = True
        return model

    StrandsDeciderModel.from_pretrained_base = staticmethod(load_parent)
    with open(config_path(checkpoint_dir(args.parent)), encoding="utf-8") as fh:
        parent_cfg = json.load(fh)
    cfg = trainer.TrainConfig(
        train_files=[train_file, replay_file],
        val_files=[val_file],
        base_model=parent_cfg["base_model"],
        head_type="pointer",
        pointer_dim=parent_cfg["pointer_dim"],
        max_length=2048,
        use_lora=True,
        lora_r=parent_cfg["lora_r"],
        lora_alpha=parent_cfg["lora_alpha"],
        teacher_file=teacher_file,
        teacher_weight=args.replay_weight,
        epochs=args.epochs,
        micro_batch_size=args.micro_batch,
        grad_accum=args.grad_accum,
        lr=args.lr,
        head_lr=args.head_lr,
        group_by_length=True,
        ordinal_smoothing=parent_cfg["ordinal_smoothing"],
        output_dir=args.out,
        log_every=10,
        eval_every=50,
    )
    trainer.train(cfg)

    temps = calibrate(args.out, load_examples([val_file]), parent_cfg)

    heldout = load_examples([heldout_file])
    summary = {
        "temperature_by_kind": temps,
        "replay_heldout_acc": {
            "parent": accuracy(args.parent, heldout),
            "tuned": accuracy(args.out, heldout),
        },
        "wall_s": round(time.time() - t0),
    }
    with open(os.path.join(args.out, "tuning_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
