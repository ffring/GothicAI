#!/usr/bin/env python3
"""Pure NumPy reference for the exported recurrent NPC walking policy.

Only NumPy and the Python standard library are needed. For C++ parity, weights
and intermediate values are float32, convolution is cross-correlation with
OIHW weights, and GRU gates use torch's reset-after convention.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np


def _relu(value: np.ndarray) -> np.ndarray:
    return np.maximum(value, np.float32(0))


def _sigmoid(value: np.ndarray) -> np.ndarray:
    # Clip before exp to keep exceptional input finite without warnings. The
    # lower limit differs from zero by less than float32 can affect the gates.
    value = np.clip(value, np.float32(-80), np.float32(80))
    return np.float32(1) / (np.float32(1) + np.exp(-value))


def _conv2d(value: np.ndarray, weight: np.ndarray, bias: np.ndarray) -> np.ndarray:
    padded = np.pad(value, ((0, 0), (1, 1), (1, 1)), mode="constant")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (3, 3), axis=(1, 2))
    windows = windows[:, ::2, ::2]
    # Form each C,KY,KX window in the same order as an OIHW weight row.
    height, width = windows.shape[1:3]
    columns = windows.transpose(1, 2, 0, 3, 4).reshape(height * width, -1)
    result = columns @ weight.reshape(weight.shape[0], -1).T
    result += bias
    return result.reshape(height, width, weight.shape[0]).transpose(2, 0, 1)


def _adaptive_avg_pool2d(value: np.ndarray) -> np.ndarray:
    # torch adaptive pooling overlaps bins for odd-sized inputs (the far map
    # finishes at 7x7); fixed non-overlapping pooling would change the result.
    channels, height, width = value.shape
    result = np.empty((channels, 2, 2), dtype=np.float32)
    for y in range(2):
        start_y, end_y = y * height // 2, ((y + 1) * height + 1) // 2
        for x in range(2):
            start_x, end_x = x * width // 2, ((x + 1) * width + 1) // 2
            result[:, y, x] = value[:, start_y:end_y, start_x:end_x].mean(axis=(1, 2), dtype=np.float32)
    return result.reshape(-1)


class NumpyPolicy:
    """Read an exported file and evaluate one decision with explicit GRU state."""

    def __init__(self, path: str | Path) -> None:
        with Path(path).open("rb") as stream:
            # A bounded line catches corrupt files without reading arbitrary
            # payload as JSON; normal headers are only a few kilobytes.
            line = stream.readline(4 * 1024 * 1024)
            if not line.endswith(b"\n"):
                raise ValueError("weight file has no bounded JSON header line")
            self.header: dict[str, Any] = json.loads(line)
            if self.header.get("format") != "gothic-nav-bc" or self.header.get("version") != 1:
                raise ValueError("unsupported navigation weight format")
            if self.header.get("byte_order") != "little":
                raise ValueError("unsupported weight byte order")
            payload = stream.read()
        if len(payload) != self.header["data_nbytes"]:
            raise ValueError("weight payload length differs from JSON header")
        architecture = self.header["architecture"]
        if architecture["name"] != "map-cnn-vector-gru" or architecture["version"] != 1:
            raise ValueError("unsupported policy architecture")
        self.vector_size = int(architecture["inputs"]["vector"][0])
        self.hidden_size = int(architecture["gru"]["hidden_size"])
        self.head_names = tuple(architecture["heads"])
        self.weights: dict[str, np.ndarray] = {}
        for tensor in self.header["tensors"]:
            if tensor["dtype"] != "float32":
                raise ValueError("only float32 weights are supported")
            name, shape, offset = tensor["name"], tuple(tensor["shape"]), tensor["offset"]
            count = int(np.prod(shape))
            if offset < 0 or offset + count * 4 > len(payload) or count * 4 != tensor["nbytes"]:
                raise ValueError(f"invalid payload range for tensor {name}")
            if name in self.weights:
                raise ValueError(f"duplicate tensor {name}")
            self.weights[name] = np.frombuffer(payload, dtype="<f4", count=count, offset=offset).reshape(shape)

    def _linear(self, value: np.ndarray, prefix: str) -> np.ndarray:
        return self.weights[prefix + ".weight"] @ value + self.weights[prefix + ".bias"]

    def _map(self, value: np.ndarray, prefix: str) -> np.ndarray:
        value = _relu(_conv2d(value, self.weights[prefix + ".0.weight"], self.weights[prefix + ".0.bias"]))
        value = _relu(_conv2d(value, self.weights[prefix + ".2.weight"], self.weights[prefix + ".2.bias"]))
        return _adaptive_avg_pool2d(value)

    def step(
        self, near: np.ndarray, far: np.ndarray, vector: np.ndarray, hidden: np.ndarray | None = None
    ) -> tuple[dict[str, np.ndarray], np.ndarray]:
        """Return head logits and the next ``(hidden_size,)`` recurrent state."""
        near = np.asarray(near, dtype=np.float32)
        far = np.asarray(far, dtype=np.float32)
        vector = np.asarray(vector, dtype=np.float32)
        if near.shape != (9, 32, 32) or far.shape != (9, 25, 25) or vector.shape != (self.vector_size,):
            raise ValueError("expected near[9,32,32], far[9,25,25], vector[vector_size]")
        if hidden is None:
            hidden = np.zeros(self.hidden_size, dtype=np.float32)
        else:
            hidden = np.asarray(hidden, dtype=np.float32)
            if hidden.shape != (self.hidden_size,):
                raise ValueError("hidden state must have shape [hidden_size]")
        features = np.concatenate((
            self._map(near, "near_encoder"),
            self._map(far, "far_encoder"),
            _relu(self._linear(vector, "vector_encoder.0")),
        ))
        features = _relu(self._linear(features, "fusion.0"))
        gates_input = self.weights["gru.weight_ih_l0"] @ features + self.weights["gru.bias_ih_l0"]
        gates_hidden = self.weights["gru.weight_hh_l0"] @ hidden + self.weights["gru.bias_hh_l0"]
        input_reset, input_update, input_new = np.split(gates_input, 3)
        hidden_reset, hidden_update, hidden_new = np.split(gates_hidden, 3)
        reset = _sigmoid(input_reset + hidden_reset)
        update = _sigmoid(input_update + hidden_update)
        new = np.tanh(input_new + reset * hidden_new)
        hidden = (np.float32(1) - update) * new + update * hidden
        logits = {name: self._linear(hidden, "head_" + name) for name in self.head_names}
        return logits, hidden


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("weights", type=Path)
    parser.add_argument("--input", type=Path, help="NPZ with near, far, vector and optional hidden arrays")
    parser.add_argument("--output", type=Path, help="write head logits and next hidden state as NPZ")
    args = parser.parse_args()
    policy = NumpyPolicy(args.weights)
    if args.input is None:
        print(json.dumps(policy.header, ensure_ascii=False, indent=2))
        return
    with np.load(args.input, allow_pickle=False) as sample:
        logits, hidden = policy.step(sample["near"], sample["far"], sample["vector"], sample["hidden"] if "hidden" in sample else None)
    if args.output:
        np.savez(args.output, **logits, hidden=hidden)
    else:
        print(json.dumps({"logits": {name: values.tolist() for name, values in logits.items()}, "hidden": hidden.tolist()}))


if __name__ == "__main__":
    main()
