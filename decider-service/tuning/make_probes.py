"""Game-format probes: the scenes of the `rescue` e2e (and their neighbours) worded exactly as the planner
words them in the game today: Russian names, no traits. Labelled by gpt-6.1-sol in both passes; written as
a data directory score_val.py reads (data/probes/train/val_requests.jsonl, val_gold.json), keeping only the
questions both passes agree on.

    cd decider-service/tuning
    uv run --project ../../server python make_probes.py [--out data/probes_g7]
    # on the GPU host: score_val.py --data data/probes parent=... g5=ckpt/g5 g6=ckpt/g6

The questions are worded as the planner words them now (brain/situation.py): probes made before a wording
change score the models trained on the old wording.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from gothic_ai.brain.situation import NpcContext, SituationBuilder, clock_text
from gothic_ai.brain.stimuli import THREAT_CONTEXT, Stimulus
from gothic_ai.llm import Done, Message, make_backend
from label import parse, reverse_menus

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent / "evals" / "npc-decisions"))
from run_gpt import INSTRUCTIONS, render  # noqa: E402

NPCS = [  # (id, Russian name, guild): as the roster reports them (Maleth is a settler, GIL_OUT)
    ("BAU_954_MALETH", "Малет", 12),
    ("BAU_960_BENGAR", "Бенгар", 8),
    ("VLK_409_ZURIS", "Зурис", 3),
    ("MIL_310_STADTWACHE", "Стражник", 2),
    ("SLD_800_SENTENZA", "Сентенза", 7),
    ("VLK_415_MEIER", "Мейер", 3),
]
CREATURES = [
    ("WOLF", "Волк"),
    ("MOLERAT", "Крысокрот"),
    ("MEATBUG", "Мясной жук"),
    ("ORCWARRIOR", "Орк-воин"),
]


def scenes() -> list[tuple[str, NpcContext, list[Stimulus]]]:
    out = []
    now = 100.0
    near = Stimulus("player_near", now - 5, "PLAYER", 600)
    for npc, name, guild in NPCS:
        for sym, cname in CREATURES:
            ctx = NpcContext(npc, name, guild, 600, True)
            coming = Stimulus("monster_near", now, sym, 400, None, cname)
            bites = Stimulus("npc_attack", now, sym, 150, None, cname)
            killed = Stimulus("monster_killed", now, "PLAYER", 300, cname, None, THREAT_CONTEXT["npc_attack"])
            out.append((f"{npc}/{sym}/near", ctx, [coming, near]))
            out.append((f"{npc}/{sym}/attack", ctx, [bites, near]))
            out.append((f"{npc}/{sym}/rescued", ctx, [killed, near]))
        # the hero killed a wolf that never came near this npc, 25 m away and not looking
        far = NpcContext(npc, name, guild, 2500, False)
        out.append((f"{npc}/WOLF/killed_far", far, [Stimulus("monster_killed", now, "PLAYER", 2000, "Волк")]))
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "data" / "probes"))
    out_dir = Path(ap.parse_args().out) / "train"
    b = SituationBuilder()
    rows = []
    for rid, ctx, stims in scenes():
        sit = b.react(ctx, stims, 100.0, clock_text(8, 15))
        qs = {**sit.questions, **b.attitude(sit).questions, **b.escalate(sit).questions}
        rows.append({"id": rid, "variant": "game", "state": sit.state, "questions": qs})
    backend = make_backend("chatgpt", reasoning_effort="low")
    answers = {}
    try:
        for p, rs in (("A", rows), ("B", [reverse_menus(r) for r in reversed(rows)])):
            got: dict = {}
            for i in range(0, len(rs), 20):
                text = ""
                prompt = "\n\n".join(render(r) for r in rs[i : i + 20])
                async for ev in backend.stream(instructions=INSTRUCTIONS, messages=[Message("user", prompt)]):
                    if isinstance(ev, Done):
                        text = ev.text
                m = re.search(r"\{.*\}", text, re.DOTALL)
                got |= {a["id"]: a for a in json.loads(m.group(0))["answers"]} if m else {}
            answers[p] = {r["id"]: parse(r, got.get(r["id"], {})) or {} for r in rows}
    finally:
        await backend.aclose()
    out_dir.mkdir(parents=True, exist_ok=True)
    gold, kept = {}, []
    for r in rows:
        a, bb = answers["A"][r["id"]], answers["B"][r["id"]]
        agreed = {q: a[q] for q in r["questions"] if q in a and q in bb and a[q] == bb[q]}
        if agreed:
            gold[r["id"]] = agreed
            kept.append({**r, "questions": {q: r["questions"][q] for q in agreed}})
        print(f"{r['id']:34s} A {a}  B {bb}")
    with (out_dir / "val_requests.jsonl").open("w") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "val_gold.json").write_text(json.dumps(gold, ensure_ascii=False, indent=1))
    print(f"{len(kept)} of {len(rows)} probes kept")


if __name__ == "__main__":
    asyncio.run(main())
