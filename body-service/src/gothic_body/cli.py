"""Model-free argument parsing; model commands import Torch only on the server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _data_args(parser):
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--dataset-code", type=Path)


def _model_args(parser):
    parser.add_argument("--architecture", choices=("gru", "mlp"), default="gru")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--stack-size", type=int, choices=(4, 8), default=8)


def _train_args(parser):
    _data_args(parser)
    _model_args(parser)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--chunk-length", type=int, default=32)
    parser.add_argument("--burn-in", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--weight-decay", type=float, default=0.0001)
    parser.add_argument("--gradient-clip", type=float, default=1.0)
    parser.add_argument("--max-wall-seconds", type=float, default=1800)
    parser.add_argument("--evaluation-mode", choices=("chunked", "continuous"), default="chunked")
    parser.add_argument("--minimum-cuda-free-mib", type=int, default=1536,
                        help="Free service headroom remaining after the full CUDA allocator cap")
    parser.add_argument("--cuda-memory-fraction", type=float, default=0.04,
                        help="Explicit CUDA training allocator fraction; at most 0.04")
    parser.add_argument("--cuda-memory-limit-mib", type=int, default=1024,
                        help="Explicit CUDA training absolute allocator ceiling; at most 1024 MiB")


def _config(args):
    from .training import TrainConfig
    return TrainConfig(**{key: getattr(args, key) for key in TrainConfig.__dataclass_fields__ if hasattr(args, key)})


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    train_parser = subparsers.add_parser("train", help="BC with eval-only checkpoint selection")
    _train_args(train_parser)
    train_parser.add_argument("--epochs", type=int, default=120)
    train_parser.add_argument("--patience", type=int, default=25)
    overfit_parser = subparsers.add_parser("overfit", help="Train-only bounded pipeline diagnostic")
    _train_args(overfit_parser)
    overfit_parser.set_defaults(learning_rate=0.003, weight_decay=0)
    overfit_parser.add_argument("--steps", type=int, default=500)
    overfit_parser.add_argument("--max-chunks", type=int, default=8)
    overfit_parser.add_argument("--target-accuracy", type=float, default=0.98)
    verify_parser = subparsers.add_parser("verify", help="Step/sequence/reset/mask checks and single-actor benchmark")
    _data_args(verify_parser)
    _model_args(verify_parser)
    verify_parser.add_argument("--output", required=True, type=Path)
    verify_parser.add_argument("--iterations", type=int, default=500)
    verify_parser.add_argument("--warmup", type=int, default=100)
    evaluate_parser = subparsers.add_parser("evaluate", help="Held-out per-action metrics and audited predictions")
    _data_args(evaluate_parser)
    evaluate_parser.add_argument("--checkpoint", required=True, type=Path)
    evaluate_parser.add_argument("--output", required=True, type=Path)
    evaluate_parser.add_argument("--split", required=True, choices=("train", "eval", "test", "ood"))
    evaluate_parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    evaluate_parser.add_argument("--threads", type=int, default=2)
    evaluate_parser.add_argument("--batch-size", type=int, default=8)
    evaluate_parser.add_argument("--chunk-length", type=int, default=32)
    evaluate_parser.add_argument("--burn-in", type=int, default=8)
    evaluate_parser.add_argument("--evaluation-mode", choices=("chunked", "continuous"), default="chunked")
    build_parser = subparsers.add_parser("build-dataset", help="Validate and project a periodic-only BC dataset")
    build_parser.add_argument("episodes", nargs="+", type=Path)
    build_parser.add_argument("--output", required=True, type=Path)
    build_parser.add_argument("--dataset-code", type=Path)
    build_parser.add_argument("--existing-assignments", type=Path)
    build_parser.add_argument("--teacher-registry", type=Path)
    build_parser.add_argument("--split-seed", type=int, default=20261013)
    build_parser.add_argument("--max-label-age-game-ms", type=int, default=67)
    try:
        from .serving import configure_serve_parser
    except ModuleNotFoundError as error:
        if error.name not in ("gothic_body.serving",):
            raise
    else:
        configure_serve_parser(subparsers)
    args = parser.parse_args(argv)
    if args.command == "build-dataset":
        from .data import dataset_module
        registry = json.loads(args.teacher_registry.read_text()) if args.teacher_registry else None
        result = dataset_module(args.dataset_code).build_dataset(args.episodes, args.output,
                    split_seed=args.split_seed, sampling_view="periodic-v1", max_label_age_game_ms=args.max_label_age_game_ms,
                    existing_assignments=args.existing_assignments, teacher_registry=registry)
        print(json.dumps({"validated": result["validated"], "episodes": result["episode_count"],
                          "diagnostic_bc_coverage_ready": result["diagnostic_bc_coverage_ready"]}))
        return 0
    if args.command == "train":
        from .training import train
        result = train(args.dataset, args.output, _config(args), dataset_code=args.dataset_code)
    elif args.command == "overfit":
        from .training import overfit
        result = overfit(args.dataset, args.output, _config(args), dataset_code=args.dataset_code,
                         steps=args.steps, max_chunks=args.max_chunks, target_accuracy=args.target_accuracy)
    elif args.command == "verify":
        from .verification import verify
        result = verify(args.dataset, args.output, architecture=args.architecture, device=args.device,
                        seed=args.seed, threads=args.threads, stack_size=args.stack_size, dataset_code=args.dataset_code,
                        iterations=args.iterations, warmup=args.warmup)
    elif args.command == "evaluate":
        from .training import configure_torch
        configure_torch(_config(args))
        from .evaluation import evaluate_checkpoint
        result = evaluate_checkpoint(args.checkpoint, args.dataset, output=args.output, split=args.split,
                                     device=args.device, batch_size=args.batch_size, chunk_length=args.chunk_length,
                                     burn_in=args.burn_in, dataset_code=args.dataset_code,
                                     evaluation_mode=args.evaluation_mode)
    elif args.command == "serve":
        from .serving import serve_main
        return serve_main(args)
    else:
        raise ValueError("Unhandled policy command")
    print(json.dumps({key: result[key] for key in ("kind", "status", "passed", "checkpoint_sha256", "label_count", "accuracy")
                      if key in result}, ensure_ascii=False, allow_nan=False))
    return 0 if result.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
