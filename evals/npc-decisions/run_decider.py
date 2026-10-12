"""Run one decision model over requests.jsonl on the GPU host and write predictions.

Runs inside the model's own venv on dev-ubuntu:
    .venv-strands/bin/python run_decider.py strands       (also strands-v21, strands-v1-2610)
    .venv-strands/bin/python run_decider.py tuned-g1 /path/to/checkpoint   (a local Strands checkpoint)
    .venv-laya/bin/python run_decider.py laya
    .venv-openjev/bin/python run_decider.py openjev-2b     (also openjev-4b)

Output preds/<name>.jsonl, one line per request:
    {"id", "cond", "ms", "answers": {"react": {"probs": {...}}, "think": {"p_true": x},
                                     "attitude": {"probs": [p0..p4]}, "extra": {...}}}
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import torch

HERE = Path(__file__).parent
WARMUP = 3

STRANDS_CKPTS = {
    "strands": "StrandsAgents/strands-decider-2B-hobson-v19",
    "strands-v21": "StrandsAgents/strands-decider-2B-hobson-v21",
    "strands-v1-2610": "StrandsAgents/strands-decider-2B-qwen3.5-v1-2610",
}
OPENJEV_DIR = Path.home() / "gothic-ai/decider-eval/openjev"


def load_requests() -> list[dict]:
    return [json.loads(line) for line in (HERE / "requests.jsonl").open()]


def normalize(qs: dict, raw: dict) -> dict:
    """System One answers -> our unified shape."""
    out = {}
    for name, q in qs.items():
        a = raw[name]
        if q["type"] == "choice":
            out[name] = {"probs": {k: float(v) for k, v in a["probabilities"].items()}}
        elif q["type"] == "noul":
            out[name] = {"p_true": float(a["noul"])}
        else:
            probs = a["probabilities"]
            out[name] = {"probs": [float(probs[str(i)]) for i in range(len(q["criteria"]))]}
    return out


def strands_runner(ckpt: str):
    from strands_decider.infer import load_engine
    from strands_decider.schema import SystemOneRequest

    engine = load_engine(ckpt)

    def run(req: dict) -> dict:
        resp = engine.evaluate(SystemOneRequest(state=req["state"], questions=req["questions"]))
        raw = {k: v.model_dump() for k, v in resp.answers.items()}
        return normalize(req["questions"], raw)

    return run


def laya_runner():
    from laya import Router

    router = Router(preload=True, device="cuda")

    def run(req: dict) -> dict:
        res = router.predict(req["state"], req["questions"])
        ans = normalize(req["questions"], res["answers"])
        ans["_route"] = res.get("routing", {}).get("model")
        return ans

    return run


def openjev_runner(size: str):
    sys.path.insert(0, str(OPENJEV_DIR / "code"))
    from openjev_decide import RUBRIC_MARK, OpenJev

    jev = OpenJev.from_pretrained(str(OPENJEV_DIR / f"qwen3.5-{size}-nli-v5"), device="cuda")

    def convert(q: dict) -> dict:
        # openjev scores each option as a hypothesis: 'The answer to "<instr>" is <label>: <crit>'
        if q["type"] == "choice":
            crits = q["criteria"]
        elif q["type"] == "noul":
            crits = {"no": q["criteria"]["false"], "yes": q["criteria"]["true"]}
        else:
            crits = {f"level {i}": c for i, c in enumerate(q["criteria"])}
        return {
            "type": q["type"],
            "instructions": q["instructions"] + RUBRIC_MARK + json.dumps(crits, ensure_ascii=False),
            "options": list(crits),
        }

    def run(req: dict) -> dict:
        names = list(req["questions"])
        answers = jev.decide(req["state"], [convert(req["questions"][n]) for n in names])
        out = {}
        for name, a in zip(names, answers, strict=True):
            q = req["questions"][name]
            if q["type"] == "choice":
                out[name] = {"probs": a["probabilities"]}
            elif q["type"] == "noul":
                out[name] = {"p_true": a["noul"]}
            else:
                out[name] = {"probs": [a["probabilities"][f"level {i}"] for i in range(len(q["criteria"]))]}
        return out

    return run


def main() -> None:
    name = sys.argv[1]
    if len(sys.argv) > 2:
        STRANDS_CKPTS[name] = sys.argv[2]
    runner = {
        **{k: (lambda c=c: strands_runner(c)) for k, c in STRANDS_CKPTS.items()},
        "laya": laya_runner,
        "openjev-2b": lambda: openjev_runner("2b"),
        "openjev-4b": lambda: openjev_runner("4b"),
    }[name]

    t0 = time.perf_counter()
    run = runner()
    load_s = time.perf_counter() - t0
    reqs = load_requests()

    with torch.inference_mode():
        for req in reqs[:WARMUP]:
            run(req)
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()

        out_dir = HERE / "preds"
        out_dir.mkdir(exist_ok=True)
        times = []
        with (out_dir / f"{name}.jsonl").open("w") as out:
            for req in reqs:
                t = time.perf_counter()
                answers = run(req)
                torch.cuda.synchronize()
                ms = (time.perf_counter() - t) * 1000
                times.append(ms)
                out.write(json.dumps({"id": req["id"], "cond": req["cond"], "ms": round(ms, 1),
                                      "answers": answers}, ensure_ascii=False) + "\n")

    times.sort()
    meta = {
        "model": name,
        "requests": len(reqs),
        "load_s": round(load_s, 1),
        "ms_p50": round(statistics.median(times), 1),
        "ms_p95": round(times[int(len(times) * 0.95) - 1], 1),
        "peak_alloc_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
        "gpu": torch.cuda.get_device_name(0),
    }
    (out_dir / f"{name}.meta.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
