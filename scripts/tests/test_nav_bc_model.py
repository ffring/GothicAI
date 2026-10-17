"""Focused export/reference checks. Run on Ryzen, never on the development Mac.

    python -m unittest discover -s scripts/tests -p test_nav_bc_model.py

The trained, 1,000-step parity check is performed by the training/evaluation
script; these small fixtures isolate the weight format, GRU state, and odd
adaptive-pooling bins.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import torch
from torch.nn import functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nav_bc_model import HEAD_SIZES, NavPolicy, export_weights
from nav_bc_numpy import NumpyPolicy, _adaptive_avg_pool2d


class NavPolicyReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        torch.set_num_threads(1)

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "policy.weights"
        torch.manual_seed(205)
        self.model = NavPolicy(vector_size=37).eval()
        self.preprocessing = {"near_pool": 2, "far_pool": 4, "vector_fields": ["sensors_only"]}
        self.header = export_weights(self.model, self.path, self.preprocessing)
        self.policy = NumpyPolicy(self.path)

    def test_single_file_export_round_trip_and_offsets(self) -> None:
        self.assertEqual(list(Path(self.directory.name).iterdir()), [self.path])
        with self.path.open("rb") as stream:
            header = json.loads(stream.readline())
            payload = stream.read()
        self.assertEqual(header, self.header)
        self.assertEqual(header["preprocessing"], self.preprocessing)
        self.assertEqual(len(payload), header["data_nbytes"])
        offset = 0
        state = self.model.state_dict()
        self.assertEqual(set(self.policy.weights), set(state))
        for entry in header["tensors"]:
            self.assertEqual(entry["offset"], offset)
            self.assertEqual(entry["dtype"], "float32")
            array = self.policy.weights[entry["name"]]
            np.testing.assert_array_equal(array, state[entry["name"]].numpy())
            self.assertEqual(list(array.shape), entry["shape"])
            self.assertEqual(array.nbytes, entry["nbytes"])
            offset += entry["nbytes"]
        self.assertEqual(offset, len(payload))

    def test_sequence_matches_torch_with_hidden_carry_and_episode_reset(self) -> None:
        rng = np.random.default_rng(719)
        length, boundary = 15, 6
        near = rng.standard_normal((1, length, 9, 32, 32), dtype=np.float32)
        far = rng.standard_normal((1, length, 9, 25, 25), dtype=np.float32)
        vector = rng.standard_normal((1, length, 37), dtype=np.float32)
        inputs = tuple(torch.from_numpy(value) for value in (near, far, vector))
        with torch.inference_mode():
            whole_logits, whole_hidden = self.model(*inputs)
            first_logits, carried = self.model(*(value[:, :boundary] for value in inputs))
            rest_logits, carried = self.model(*(value[:, boundary:] for value in inputs), carried)
        for name in HEAD_SIZES:
            segmented = torch.cat((first_logits[name], rest_logits[name]), dim=1)
            torch.testing.assert_close(segmented, whole_logits[name], rtol=1e-5, atol=1e-6)
        torch.testing.assert_close(carried, whole_hidden, rtol=1e-5, atol=1e-6)

        hidden = None
        for step in range(length):
            logits, hidden = self.policy.step(near[0, step], far[0, step], vector[0, step], hidden)
            for name in HEAD_SIZES:
                np.testing.assert_allclose(logits[name], whole_logits[name][0, step].numpy(), rtol=0, atol=1e-5)
        np.testing.assert_allclose(hidden, whole_hidden[0, 0].numpy(), rtol=0, atol=1e-5)
        self.assertGreater(float(np.linalg.norm(hidden)), 0.01)

        # A new episode must reset state. Omitted state is equivalent to
        # explicit zeros, and both match torch's fresh recurrent sequence.
        fresh, fresh_hidden = self.policy.step(near[0, 0], far[0, 0], vector[0, 0])
        zeroed, zeroed_hidden = self.policy.step(
            near[0, 0], far[0, 0], vector[0, 0], np.zeros(256, dtype=np.float32)
        )
        for name in HEAD_SIZES:
            np.testing.assert_array_equal(fresh[name], zeroed[name])
            np.testing.assert_allclose(fresh[name], whole_logits[name][0, 0].numpy(), rtol=0, atol=1e-5)
        np.testing.assert_array_equal(fresh_hidden, zeroed_hidden)

    def test_odd_adaptive_pooling_matches_torch_overlapping_bins(self) -> None:
        values = np.arange(16 * 7 * 7, dtype=np.float32).reshape(16, 7, 7)
        # These impulses belong to both bins; non-overlapping pooling loses one.
        values[:, 3, 3] += np.float32(1000)
        expected = F.adaptive_avg_pool2d(torch.from_numpy(values), (2, 2)).numpy().reshape(-1)
        np.testing.assert_array_equal(_adaptive_avg_pool2d(values), expected)

    def test_truncated_payload_is_rejected(self) -> None:
        self.path.write_bytes(self.path.read_bytes()[:-3])
        with self.assertRaisesRegex(ValueError, "payload length"):
            NumpyPolicy(self.path)

    def test_tensor_range_outside_payload_is_rejected(self) -> None:
        with self.path.open("rb") as stream:
            header = json.loads(stream.readline())
            payload = stream.read()
        header["tensors"][0]["offset"] = len(payload)
        self.path.write_bytes(json.dumps(header).encode("utf-8") + b"\n" + payload)
        with self.assertRaisesRegex(ValueError, "invalid payload range"):
            NumpyPolicy(self.path)


if __name__ == "__main__":
    unittest.main()
