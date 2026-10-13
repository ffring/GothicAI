"""Does the behavioral model come up to the hero by character? (docs/npc-tools.md, wave 2 step 1)

The planner asks `react` with the npc's card line (characters.json `en`) and, with a concern, its agenda
("What you want: ..."). Here the same states, in the planner's own wording, go to the deployed decider for
npc's pushy by their cards (Bronko, Canthar, Hilda) and not (Lobart, Onar), with and without a debt, the
hero near or farther, seen or not. P(approach_talk) per npc and condition goes to preds/initiative-<name>.md.

    GOTHIC_DECIDER_URL=http://<dev-ubuntu>:47832 uv run --project ../../server python initiative_probe.py g7

The decider runs on dev-ubuntu; this script is only its client (no models on the Mac).
"""

import asyncio
import itertools
import os
import statistics
import sys
from pathlib import Path

from gothic_ai.brain.decider import DeciderClient
from gothic_ai.brain.situation import NpcContext, SituationBuilder, clock_text
from gothic_ai.brain.stimuli import Stimulus
from gothic_ai.characters import load

NPCS = [  # symbol, name, guild (as story_people reports it)
    ("BAU_935_BRONKO", "Бронко", 0),
    ("VLK_468_CANTHAR", "Кантар", 0),
    ("BAU_951_HILDA", "Хильда", 12),
    ("BAU_954_MALETH", "Малет", 12),
    ("BAU_950_LOBART", "Лобарт", 12),
    ("BAU_900_ONAR", "Онар", 8),
]
DEBT = "make the hero pay back the 25 gold he owes you; his day has passed"
NOW = 1000.0
CLOCK = clock_text(10, 0)


async def main(name: str) -> None:
    cards = load()
    builder = SituationBuilder(traits={npc: card.en for npc, card in cards.items()})
    client = DeciderClient(os.environ["GOTHIC_DECIDER_URL"])
    rows = []
    for (npc, ru, guild), agenda, metres, seen, near in itertools.product(
        NPCS, (None, DEBT), (8, 15), (True, False), (True, False)
    ):
        ctx = NpcContext(npc, ru, guild, metres * 100, seen, agenda=agenda)
        stimuli = [Stimulus("player_near", NOW, distance_cm=metres * 100)] if near else []
        sit = builder.react(ctx, stimuli, NOW, CLOCK)
        out = await client.decide(f"{npc}#probe", "background", 1, 2000, sit.state, sit.questions)
        answer = (out.answers or {}).get("react", {}) if out.kind == "answers" else {}
        p = float((answer.get("probabilities") or {}).get("approach_talk", 0.0))
        rows.append((npc, agenda is not None, metres, seen, near, p, answer.get("choice")))
        print(f"{npc:18s} debt={agenda is not None!s:5s} {metres:2d} m seen={seen!s:5s} near={near!s:5s} "
              f"P={p:.2f} {answer.get('choice')}", flush=True)

    pushy = {npc: cards[npc].pushy if npc in cards else 3 for npc, _, _ in NPCS}
    lines = [
        f"# P(approach_talk), {name}",
        "",
        "From initiative_probe.py: the planner's react state with the npc's card line; mean over hero 8/15 m,"
        " seen or not, player_near or no stimulus (8 states per cell).",
        "",
        "| npc | pushy | no concern | debt overdue | chosen with the debt |",
        "|---|---|---|---|---|",
    ]
    for npc, ru, _ in NPCS:
        mine = [r for r in rows if r[0] == npc]
        free = statistics.mean(r[5] for r in mine if not r[1])
        debt = [r for r in mine if r[1]]
        chosen = sum(1 for r in debt if r[6] == "approach_talk")
        lines.append(
            f"| {ru} | {pushy[npc]} | {free:.2f} | {statistics.mean(r[5] for r in debt):.2f} | "
            f"{chosen} of {len(debt)} |"
        )

    def mean_p(test, debt: bool) -> float:
        return statistics.mean(r[5] for r in rows if test(pushy[r[0]]) and r[1] == debt)

    gap = mean_p(lambda p: p >= 4, True) - mean_p(lambda p: p <= 2, True)
    lines += [
        "",
        f"Pushy (4–5) minus reserved (1–2), with the debt: {gap:+.2f}"
        + (" — the card line alone sets them apart." if gap >= 0.15 else " — under 0.15: the card line"
           " alone does not set them apart."),
    ]
    out = Path(__file__).parent / "preds" / f"initiative-{name}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "g7"))
