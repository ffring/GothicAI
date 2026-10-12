"""Scene cards -> the questions the decider is asked (data/rows.jsonl), two per card:

prod  the state and menu exactly as the planner builds them (server/src/gothic_ai/brain/situation.py):
      `react` over the generic actions plus event extras, `attitude` and `escalate` (call the large model?);
rich  the card's own scene text and npc-specific menu with voiced SVM lines, as planned for per-npc
      menus; `react` and `escalate` (free text, so escalate does not hang on the planner's phrasing).

  cd decider-service/tuning
  uv run --project ../../server python build_rows.py
"""

from __future__ import annotations

import json
from pathlib import Path

from gothic_ai.brain.situation import (
    ESCALATE,
    ESCALATE_LEVELS,
    REACT,
    NpcContext,
    SituationBuilder,
    clock_text,
)
from gothic_ai.brain.stimuli import Stimulus

HERE = Path(__file__).parent
DATA = HERE / "data"


def prod(card: dict, builder: SituationBuilder) -> dict:
    npc = NpcContext(
        id="NPC",
        name=card["npc"],
        guild=int(card["guild"]),
        distance_cm=float(card["distance_m"]) * 100,
        in_view=bool(card["in_view"]),
        traits=card.get("traits"),
        agenda=card.get("agenda"),
        facts=tuple(card.get("facts") or ()),
    )
    now = 1000.0
    stimuli = [
        Stimulus(
            e["kind"],
            now,
            detail=e.get("detail"),
            distance_cm=npc.distance_cm if e["kind"] == "player_near" else None,
        )
        for e in card["events"]
    ]
    react = builder.react(npc, stimuli, now, clock_text(int(card["hour"]), int(card["minute"])))
    questions = {**react.questions, **builder.attitude(react).questions, **builder.escalate(react).questions}
    return {"state": react.state, "questions": questions}


def rich(card: dict) -> dict:
    menu = {k: v.strip() for k, v in card["menu"].items()}
    return {
        "state": " ".join(card["scene"].split()),
        "questions": {
            "react": {"type": "choice", "instructions": REACT, "criteria": menu},
            "escalate": {"type": "score", "instructions": ESCALATE, "criteria": ESCALATE_LEVELS},
        },
    }


def main() -> None:
    builder = SituationBuilder()
    n = 0
    with (DATA / "cards.jsonl").open() as src, (DATA / "rows.jsonl").open("w") as out:
        for i, line in enumerate(src):
            card = json.loads(line)
            for variant, row in (("prod", prod(card, builder)), ("rich", rich(card))):
                row = {"id": f"c{i:05d}/{variant}", "card": i, "variant": variant, **row}
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
    print(f"{n} rows")


if __name__ == "__main__":
    main()
