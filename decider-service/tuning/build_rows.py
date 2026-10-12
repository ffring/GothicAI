"""Scene cards -> the questions the decider is asked (data/rows.jsonl), two or three per card:

prod  the state and menu exactly as the planner builds them (server/src/gothic_ai/brain/situation.py):
      `react` over the generic actions plus event extras, `attitude` and `escalate` (call the large model?);
game  the same as the game sends it today, for cards with Russian names (gen_cards.py --danger/--rescue):
      no traits, agenda or memories (the planner has none for most npc's), the npc's and the creature's
      Russian names (the bridge reports the Russian release's names);
game_out  the game variant once more with the npc a settler (GIL_OUT): in the game many villagers and farm
      hands are (Maleth at Lobart's farm), in the cards almost none; for commoners, citizens and farmers;
rich  the card's own scene text and npc-specific menu with voiced SVM lines, as planned for per-npc
      menus; `react` and `escalate` (free text, so escalate does not hang on the planner's phrasing).

  cd decider-service/tuning
  uv run --project ../../server python build_rows.py [--since data/rows_g5.jsonl --changed data/changed.txt]

With `--since`, the ids of rows that are worded differently now (the planner's wording or menus changed)
go to `--changed`: label.py relabels them (`--ids`).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gen_cards import MONSTERS_RU
from gothic_ai.brain.situation import (
    ESCALATE,
    ESCALATE_LEVELS,
    REACT,
    NpcContext,
    SituationBuilder,
    clock_text,
)
from gothic_ai.brain.stimuli import THREAT_CONTEXT, Stimulus

HERE = Path(__file__).parent
DATA = HERE / "data"

# card events whose detail is who does it -> the planner's event kind (protocol v6), told by `other_name`
# commoner, citizen, farmer: the game often has these as settlers (GIL_OUT)
AS_SETTLER = {0, 3, 8}
SETTLER = 12

FROM_OTHERS = {"monster_near": "monster_near", "attacked_by": "npc_attack", "cry_for_help": "cry_for_help"}


def creature(name: str | None) -> str:
    """'A wolf', 'the wolf' -> 'wolf' (a key of MONSTERS_RU when it is one of them)."""
    n = (name or "someone").strip().lower()
    for article in ("a ", "an ", "the "):
        n = n.removeprefix(article)
    return n


def shown(e: dict, game: bool, capital: bool = True) -> str:
    """The event's creature or person as the state names it: the Russian release's name in the game
    variant; `capital` where it starts the sentence."""
    if game and (ru := e.get("detail_ru") or MONSTERS_RU.get(creature(e.get("detail")))):
        return ru
    name = (e.get("detail") or "someone").strip()
    return name[:1].upper() + name[1:] if capital else name


def stimuli(card: dict, now: float, hero_cm: float, game: bool) -> list[Stimulus]:
    """The card's events in order, as the planner keeps them (NpcPlanner.on_event): a killed creature takes
    its threats with it, and the kill says what it had been to the npc."""
    out: list[Stimulus] = []
    for e in card["events"]:
        kind, detail = e["kind"], e.get("detail")
        if kind == "monster_killed":
            victim = creature(detail).upper()
            gone = {s.kind for s in out if s.policy.threat and s.other == victim}
            out = [s for s in out if not (s.policy.threat and s.other == victim)]
            context = next((THREAT_CONTEXT[k] for k in THREAT_CONTEXT if k in gone), "")
            by = (e.get("by") or "hero").strip()
            if by.lower() in ("hero", "the hero", "player"):
                other, other_name = "PLAYER", None
            elif by.lower() in ("self", "npc", "you"):
                other, other_name = "NPC", "you"
            else:
                other, other_name = "OTHER", by[:1].upper() + by[1:]
            out.append(Stimulus(kind, now, other, None, shown(e, game, capital=False), other_name, context))
        elif kind in FROM_OTHERS:
            out.append(
                Stimulus(FROM_OTHERS[kind], now, other=creature(detail).upper(), other_name=shown(e, game))
            )
        else:
            out.append(
                Stimulus(kind, now, detail=detail, distance_cm=hero_cm if kind == "player_near" else None)
            )
    return out


def prod(card: dict, builder: SituationBuilder, game: bool = False, guild: int | None = None) -> dict:
    npc = NpcContext(
        id="NPC",
        name=(card.get("npc_ru") or card["npc"]) if game else card["npc"],
        guild=int(card["guild"]) if guild is None else guild,
        distance_cm=float(card["distance_m"]) * 100,
        in_view=bool(card["in_view"]),
        traits=None if game else card.get("traits"),
        agenda=None if game else card.get("agenda"),
        facts=() if game else tuple(card.get("facts") or ()),
    )
    now = 1000.0
    react = builder.react(
        npc,
        stimuli(card, now, npc.distance_cm, game),
        now,
        clock_text(int(card["hour"]), int(card["minute"])),
    )
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="rows.jsonl the labels were made for")
    ap.add_argument("--changed", help="where to list the rows worded differently since then")
    args = ap.parse_args()
    before = {}
    if args.since:
        before = {r["id"]: (r["state"], r["questions"]) for r in map(json.loads, open(args.since))}

    builder = SituationBuilder()
    n, changed = 0, []
    with (DATA / "cards.jsonl").open() as src, (DATA / "rows.jsonl").open("w") as out:
        for i, line in enumerate(src):
            card = json.loads(line)
            variants = [("prod", prod(card, builder))]
            if card.get("npc_ru"):
                variants.append(("game", prod(card, builder, game=True)))
                if int(card["guild"]) in AS_SETTLER:
                    variants.append(("game_out", prod(card, builder, game=True, guild=SETTLER)))
            variants.append(("rich", rich(card)))
            for variant, row in variants:
                row = {"id": f"c{i:05d}/{variant}", "card": i, "variant": variant, **row}
                if row["id"] in before and before[row["id"]] != (row["state"], row["questions"]):
                    changed.append(row["id"])
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
    print(f"{n} rows, {len(changed)} worded differently since {args.since}" if args.since else f"{n} rows")
    if args.changed:
        Path(args.changed).write_text("".join(f"{i}\n" for i in changed))


if __name__ == "__main__":
    main()
