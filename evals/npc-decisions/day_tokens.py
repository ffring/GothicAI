"""Token counts of the day probe's requests (day_probe.py) as the decider sees them: its own renderer and its
checkpoint's tokenizer, the way the service counts a request's cost (gothic_decider/engines/strands.py). No
weights are loaded. Runs on dev-ubuntu, where strands_decider is installed (gothic_ai is not: nothing of it
is imported here).

    scp day_tokens.py preds/day-g7.jsonl dev-ubuntu:gothic-ai/decider-eval/
    ssh dev-ubuntu 'cd ~/gothic-ai/decider-eval && .venv-strands/bin/python day_tokens.py day-g7.jsonl'
    scp dev-ubuntu:gothic-ai/decider-eval/day-g7.tokens.json preds/

Writes <input>.tokens.json beside the input: per row id, the state's tokens and each question's; then
`day_probe.py score g7` reports them.
"""

import json
import os
import statistics
import sys
from pathlib import Path

from strands_decider.modeling import checkpoint_dir
from strands_decider.prompting import render_question, render_state
from strands_decider.schema import SystemOneRequest
from transformers import AutoTokenizer

# the service's checkpoint (the gothic-decider unit's GOTHIC_DECIDER_CHECKPOINT)
CHECKPOINT = os.environ.get(
    "GOTHIC_DECIDER_CHECKPOINT", str(Path.home() / "gothic-ai/decider/models/gothic-g8b")
)


def main(path: Path) -> None:
    tok = AutoTokenizer.from_pretrained(checkpoint_dir(CHECKPOINT))
    out: dict[str, dict[str, int]] = {}
    for line in path.open(encoding="utf-8"):
        row = json.loads(line)
        if not row.get("questions"):
            continue
        req = SystemOneRequest(state=row["state"], questions=row["questions"])
        counts = {"state": len(tok(render_state(req.state), add_special_tokens=True)["input_ids"])}
        for name, q in req.questions.items():
            counts[name] = len(tok(render_question(q).text, add_special_tokens=False)["input_ids"])
        out[row["id"]] = counts
    target = path.with_suffix(".tokens.json")
    target.write_text(json.dumps(out, indent=1), encoding="utf-8")
    for part in ("state", "plan", "do"):
        xs = sorted(c[part] for c in out.values() if part in c)
        if xs:
            p95 = xs[int(len(xs) * 0.95)]
            print(f"{part}: p50 {statistics.median(xs):.0f}, p95 {p95}, max {xs[-1]} ({len(xs)} rows)")
    print(f"-> {target}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
