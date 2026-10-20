"""CPU-only import check; no weights are instantiated and no GPU is used."""
import argparse
import json
from pathlib import Path

from adapter import assert_clean_imports, pipeline_class


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", choices=["trellis2", "trellis"], required=True)
    args = parser.parse_args()
    cls = pipeline_class(args.root.expanduser().resolve(), args.model)
    import torch
    assert not torch.cuda.is_initialized(), "Import check must not initialize CUDA"
    print(json.dumps({"model": args.model, "pipeline_class": cls.__name__, "cuda_initialized": False,
                      **assert_clean_imports()}))
