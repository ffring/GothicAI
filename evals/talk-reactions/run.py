"""Ask a running decider service what each npc of phrases.yaml says at once to the hero's phrase, the way
the server asks it (`Reflexes.phrase`: the sound and the gesture in one request), and score the sound.

    cd evals/talk-reactions
    uv run --project ../../server --with pyyaml python run.py ws://<gpu-host>:47832 [--out preds/NAME.jsonl]

`--wording FILE` tries other words for the question (a YAML file with `instructions` and `criteria`, as
in reflex.py) without touching the server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from collections import Counter
from pathlib import Path

import yaml

from gothic_ai.brain import reflex
from gothic_ai.brain.decider import DeciderClient, Outcome
from gothic_ai.brain.reflex import Option, Reflexes
from gothic_ai.brain.situation import NpcContext
from gothic_ai.executor import Intent

HERE = Path(__file__).parent
STRONG = ("surprise", "displeasure", "doubt")  # out of place, these jar; the others are calm sounds or none


class Recorder:
    """The decider, keeping its last raw answers (the probabilities of every option)."""

    def __init__(self, decider: DeciderClient) -> None:
        self.decider = decider
        self.last: Outcome | None = None

    async def decide(self, *args, **kwargs) -> Outcome:  # noqa: ANN002, ANN003
        self.last = await self.decider.decide(*args, **kwargs)
        return self.last

    async def cancel(self, rid: str) -> None:
        await self.decider.cancel(rid)


def reword(path: Path) -> None:
    w = yaml.safe_load(path.read_text())
    reflex.INSTRUCTIONS["voice"] = " ".join(w["instructions"].split())
    reflex.VOICE_OPTIONS.clear()
    for k, text in w["criteria"].items():
        reflex.VOICE_OPTIONS[k] = Option(" ".join(text.split()))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--wording", type=Path)
    args = ap.parse_args()
    if args.wording:
        reword(args.wording)
    data = yaml.safe_load((HERE / "phrases.yaml").read_text())
    client = DeciderClient(args.url)
    rec = Recorder(client)
    rows = []
    for i, p in enumerate(data["phrases"]):
        n = data["npcs"][p["npc"]]
        ctx = NpcContext(n["id"], n["name"], -1, n["distance_cm"], True, traits=" ".join(n["traits"].split()))
        reflexes = Reflexes(rec)  # type: ignore[arg-type]
        intent = Intent(**n["intent"]) if n.get("intent") else None
        out = await reflexes.phrase(ctx, p["say"], intent, n.get("clock"))
        voice = out.get("voice")
        if voice is None:
            print(f"{p['say']}: no answer ({rec.last.kind if rec.last else '-'})")
            continue
        probs = (rec.last.answers or {})["voice"].get("probabilities", {}) if rec.last else {}
        gesture = out.get("gesture")
        rows.append(
            {
                "i": i,
                "npc": p["npc"],
                "say": p["say"],
                "good": p["good"],
                "choice": voice.choice,
                "confidence": round(voice.confidence, 3),
                "applied": voice.applied,
                "probs": {k: round(float(v), 3) for k, v in probs.items()},
                "gesture": gesture.choice if gesture else None,
                "latency_ms": round(voice.latency_ms, 1),
            }
        )
    await client.aclose()

    for r in rows:
        ok = "+" if r["choice"] in r["good"] else "-"
        top = sorted(r["probs"].items(), key=lambda kv: -kv[1])[:2]
        alt = ", ".join(f"{k} {v:.2f}" for k, v in top)
        print(
            f"{ok} {r['npc']:6s} {r['choice']:11s} {r['confidence']:.2f}  [{alt}]  "
            f"gesture {r['gesture'] or '-':10s} {r['say']}  (want {'/'.join(r['good'])})"
        )
    n = len(rows)
    right = sum(r["choice"] in r["good"] for r in rows)
    best = sum(r["choice"] == r["good"][0] for r in rows)
    applied = [r for r in rows if r["applied"]]
    applied_right = sum(r["choice"] in r["good"] for r in applied)
    lat = [r["latency_ms"] for r in rows]
    print(f"\nright {right}/{n} ({right / n:.0%}), the best one {best}/{n}")
    if applied:
        print(f"over the thresholds: {len(applied)}/{n} answered, {applied_right}/{len(applied)} right")
    jarring = [r for r in applied if r["choice"] in STRONG and r["choice"] not in r["good"]]
    print(
        f"a strong sound out of place: {len(jarring)}/{n}"
        + "".join(f"\n  {r['choice']}: {r['say']}" for r in jarring)
    )
    by_gold: dict[str, Counter[str]] = {}
    for r in rows:
        by_gold.setdefault(r["good"][0], Counter())[r["choice"]] += 1
    for gold, c in by_gold.items():
        total = sum(c.values())
        print(f"  {gold:11s} {c[gold]}/{total}  " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
    print(
        f"latency p50 {statistics.median(lat):.0f} ms, p90 {sorted(lat)[int(len(lat) * 0.9)]:.0f} ms, "
        f"max {max(lat):.0f} ms; model {client.model}"
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


if __name__ == "__main__":
    asyncio.run(main())
