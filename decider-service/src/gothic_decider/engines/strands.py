"""Strands Decider on the GPU, many requests per forward pass.

The stock engine answers one request (one state) per call. Here every (state + question) of every
request in the batch is one row of a padded batch; rows of similar length go together, at most
`token_budget` padded tokens per forward pass. Tokenisation, option positions and the readout are the
package's own, so answers match `SystemOneEngine.evaluate` (checked on the 76 eval scenes: same
choices, probabilities within 0.011).

Needs the `gpu` extras plus causal-conv1d; without flash-linear-attention and causal-conv1d the
Gated DeltaNet layers fall back to slow reference code (1.3x slower).
"""

from __future__ import annotations

import copy
import logging
import time
from functools import lru_cache

import torch
from strands_decider.infer import EngineConfig, SystemOneEngine, _option_token_index, _to_answer
from strands_decider.modeling import StrandsDeciderModel
from strands_decider.prompting import render_question, render_state
from strands_decider.schema import SystemOneRequest

from ..protocol import DecideRequest

log = logging.getLogger(__name__)

DEFAULT_CHECKPOINT = "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610"
WARMUP_ROWS = 96  # fla autotunes per batch size: compile every row count a forward pass may get


class StrandsEngine:
    def __init__(
        self,
        checkpoint: str = DEFAULT_CHECKPOINT,
        *,
        device: str = "cuda",
        merge_lora: bool = True,
        token_budget: int = 8192,
        mem_cap_gb: float | None = 9.0,
    ) -> None:
        self.name = checkpoint.rsplit("/", 1)[-1]
        self.token_budget = token_budget
        if mem_cap_gb and device.startswith("cuda"):
            # other models share this GPU: an oversized batch must fail here, not there
            total = torch.cuda.get_device_properties(0).total_memory
            torch.cuda.set_per_process_memory_fraction(min(1.0, mem_cap_gb * 2**30 / total))
        t = time.perf_counter()
        model = StrandsDeciderModel.load(checkpoint)
        if merge_lora and model.config.use_lora:
            model.torso = _merge_lora(model.torso)
        self._e = SystemOneEngine(model, EngineConfig(device=device, model_name=self.name))
        # cost() runs on the event loop while run() tokenises in the worker thread: separate tokenizers
        self._cost_tok = copy.deepcopy(self._e.tok)
        log.info(
            "loaded %s in %.1f s (LoRA %s)",
            checkpoint,
            time.perf_counter() - t,
            "merged" if merge_lora else "separate",
        )

    # --- cost ---------------------------------------------------------------------------

    def cost(self, req: DecideRequest) -> int:
        state = self._tokens(render_state(req.state))
        sreq = SystemOneRequest(state=req.state, questions=req.questions)
        return sum(state + self._question_tokens(render_question(q).text) for q in sreq.questions.values())

    def _tokens(self, text: str) -> int:
        return len(self._cost_tok(text, add_special_tokens=True)["input_ids"])

    @lru_cache(maxsize=4096)  # noqa: B019 - one engine per process
    def _question_tokens(self, text: str) -> int:
        return len(self._cost_tok(text, add_special_tokens=False)["input_ids"])

    # --- inference ----------------------------------------------------------------------

    @torch.inference_mode()
    def run(self, reqs: list[DecideRequest]) -> list[dict[str, dict]]:
        e = self._e
        rows, opt_rows, n_slots, kinds, owners, rendered_all = [], [], [], [], [], []
        for ri, req in enumerate(reqs):
            sreq = SystemOneRequest(state=req.state, questions=req.questions)
            names = list(sreq.questions)
            rendered = [render_question(sreq.questions[n]) for n in names]
            s, q = e._fit(render_state(sreq.state), [rq.text for rq in rendered])
            for name, rq, qi, offs in zip(names, rendered, q, e._last_offsets, strict=True):
                rows.append(s + qi)
                opt_rows.append([len(s) + i for i in _option_token_index(offs, rq.option_spans, 0)])
                n_slots.append(rq.n_slots)
                kinds.append(rq.kind)
                owners.append((ri, name))
                rendered_all.append(rq)

        probs_by_row: dict[int, list[float]] = {}
        for chunk in self._chunks(rows):
            probs = self._forward(
                [rows[j] for j in chunk],
                [opt_rows[j] for j in chunk],
                [n_slots[j] for j in chunk],
                [kinds[j] for j in chunk],
            )
            probs_by_row.update(zip(chunk, probs, strict=True))

        answers: list[dict[str, dict]] = [{} for _ in reqs]
        smoothing = e.model.config.ordinal_smoothing
        for i, ((ri, name), rq) in enumerate(zip(owners, rendered_all, strict=True)):
            row = probs_by_row[i][: rq.n_slots]
            answers[ri][name] = _to_answer(rq, row, ordinal_smoothing=smoothing).model_dump()
        return answers

    def _chunks(self, rows: list[list[int]]) -> list[list[int]]:
        """Row indices grouped by similar length, each group at most `token_budget` padded tokens."""
        out: list[list[int]] = []
        chunk: list[int] = []
        for i in sorted(range(len(rows)), key=lambda i: len(rows[i])):
            if chunk and (len(chunk) + 1) * len(rows[i]) > self.token_budget:
                out.append(chunk)
                chunk = []
            chunk.append(i)
        if chunk:
            out.append(chunk)
        return out

    def _forward(self, rows, opt_rows, n_slots, kinds) -> list[list[float]]:
        e = self._e
        ids, mask = e._pad(rows)
        width = max(len(r) for r in opt_rows)
        opt_idx = torch.tensor([r + [-1] * (width - len(r)) for r in opt_rows], device=e.device)
        out = e.model(
            input_ids=ids,
            attention_mask=mask,
            n_slots=torch.tensor(n_slots, device=e.device),
            temperature=e._temperatures(kinds),
            opt_idx=opt_idx,
        )
        return out["log_probs"].exp().float().cpu().tolist()

    # --- warmup -------------------------------------------------------------------------

    def warmup(self) -> None:
        q = {
            "react": {
                "type": "choice",
                "instructions": "What do you do right now?",
                "criteria": {
                    "continue": "keep doing what you do",
                    "greet": "greet the hero",
                    "flee": "run away",
                    "attack": "attack the hero",
                },
            }
        }
        t = time.perf_counter()
        for state in (
            "You are a guard at the city gate. The hero walks past.",
            "You are a farmer. " + "The hero did a favour for you once. " * 25,
        ):
            for n in range(1, WARMUP_ROWS + 1):
                reqs = [DecideRequest(f"w{i}", "background", 0, 1000, state, q) for i in range(n)]
                if n * self.cost(reqs[0]) > self.token_budget:
                    break
                self.run(reqs)
        torch.cuda.synchronize()
        log.info("warmup done in %.1f s", time.perf_counter() - t)


def _merge_lora(torso: torch.nn.Module) -> torch.nn.Module:
    """Fold the adapter into the weights in fp32, then back to the checkpoint dtype.

    Unmerged, every adapted linear runs two extra small matmuls (rank 16-48, ~190 linears per pass).
    Merging in bf16 directly loses more precision than going through fp32.
    """
    dtype = next(torso.parameters()).dtype
    merged = torso.to(torch.float32).merge_and_unload()
    return merged.to(dtype)
