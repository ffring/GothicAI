"""Does the behavioral model speak up in a conversation by character? (docs/npc-tools.md, step B3)

The bandits below Xardas's tower and Cavalorn with the hero: the hero says something, one of them answers
him, and each of the others is asked `interject` (keep quiet, put in a word, step in) in the very state the
game builds (brain/reflex.py, `Reflexes.interject`), with its card line. P(speak) = P(word) + P(step in) per
npc goes to preds/interject-<name>.md: Brago (abrupt, commanding) against the bandit from the steps
(pushy, but a coward), the suspicious one, and Cavalorn.

    GOTHIC_DECIDER_URL=ws://<dev-ubuntu>:47832 uv run --project ../../server python interject_probe.py g9e

The decider runs on dev-ubuntu; this script is only its client (no models on the Mac).
"""

import asyncio
import os
import statistics
import sys
from pathlib import Path

from gothic_ai.brain.decider import DeciderClient
from gothic_ai.brain.reflex import Reflexes
from gothic_ai.brain.situation import NpcContext, clock_text
from gothic_ai.characters import load
from gothic_ai.executor import Intent
from gothic_ai.interject import SPEAK_P, heard_lines, speak_p

BRAGO, STEPS, MATE, CAVALORN = "BDT_1014_BANDIT_L", "BDT_1013_BANDIT_L", "BDT_1015_BANDIT_L", "BAU_4300_ADDON_CAVALORN"
NPCS = {BRAGO: ("Браго", 9), STEPS: ("Бандит", 9), MATE: ("Бандит", 9), CAVALORN: ("Кавалорн", 0)}
LABEL = {BRAGO: "Браго", STEPS: "Бандит у лестницы", MATE: "Бандит из пещеры", CAVALORN: "Кавалорн"}
# what the hero says, whom to, and the answer: (to, to all, phrase, answer)
SCENES = [
    (BRAGO, True, "Пацаны, здорово!", "Чего надо? Говори быстро."),
    (BRAGO, False, "Браго, я ищу того, кто ограбил Кавалорна.", "Ну нашёл. И что дальше?"),
    (STEPS, False, "Эй, ты не видел тут торговца с телегой?", "Может, и видел. А что мне за это будет?"),
    (MATE, False, "Ты кто такой?", "Это я тебя спрашиваю. Рожа твоя мне незнакома."),
    (CAVALORN, False, "Кавалорн, это они тебя ограбили?", "Они самые. Узнаю эти рожи."),
    (BRAGO, True, "Отдайте письмо Кавалорна, и я уйду.", "Ха! Попробуй забери."),
    (STEPS, True, "У меня есть золото. Договоримся?", "Золото — это разговор. Сколько?"),
    (MATE, False, "Отвали, я не к тебе.", "Да ну? Тут всё к нам."),
]
CLOCK = clock_text(10, 30)


def view(viewer: str, speaker: str) -> tuple[str, ...]:
    """What it sees, as perception.py tells the behavioral model: the bandits know each other."""
    lines = []
    for npc, (name, guild) in NPCS.items():
        if npc == viewer:
            continue
        knows = guild == NPCS[viewer][1] == 9
        who = f"{name}, whom you know," if knows else "A stranger"
        hostile = ", hostile to the hero" if guild == 9 and viewer != CAVALORN and npc != CAVALORN else ""
        lines.append(f"{who} is {3 if npc == speaker else 5} m away{hostile}.")
    return tuple(lines[:3])


async def main(name: str) -> None:
    cards = load()
    client = DeciderClient(os.environ["GOTHIC_DECIDER_URL"])
    reflexes = Reflexes(client, deadline_ms=2000)
    rows: list[tuple[str, int, float, str, bool]] = []
    for i, (to, to_all, phrase, answer) in enumerate(SCENES):
        speaker = NPCS[to][0]
        heard = heard_lines(speaker, phrase, answer, to_all=to_all)
        for npc, (ru, guild) in NPCS.items():
            if npc == to:
                continue
            card = cards.get(npc)
            ctx = NpcContext(npc, ru, guild, 400, True, traits=card.en if card else None, view=view(npc, to))
            intent = Intent("follow_hero", "PLAYER", "friendly") if npc == CAVALORN else None
            r = await reflexes.interject(ctx, heard, intent, CLOCK, to_all=to_all)
            if r is None:
                print(f"{LABEL[npc]:18s} scene {i}: no answer", flush=True)
                continue
            p = speak_p(r)
            rows.append((npc, i, p, r.choice, to_all))
            print(f"{LABEL[npc]:18s} scene {i}: P(speak)={p:.2f} {r.choice} {r.probabilities}", flush=True)

    lines = [
        f"# P(speak up), {name} ({client.model or 'decider'})",
        "",
        "From interject_probe.py: the game's `interject` state with the npc's card line; P(speak) ="
        f" P(word) + P(step in), it speaks from {SPEAK_P}. {len(SCENES)} scenes at the bandits' cave, each npc"
        " asked in those it did not answer; «to one»: the hero spoke to another, «to all»: to them all.",
        "",
        "| npc | card: courage / talk / pushy | P(speak), to one | speaks, to one | P(speak), to all | "
        "word / step in / silent |",
        "|---|---|---|---|---|---|",
    ]
    for npc in NPCS:
        mine = [r for r in rows if r[0] == npc]
        if not mine:
            continue
        card = cards.get(npc)
        scales = f"{card.courage} / {card.talk} / {card.pushy}" if card else "—"
        one = [r for r in mine if not r[4]]
        many = [r for r in mine if r[4]]
        mean_one = statistics.mean(r[2] for r in one) if one else float("nan")
        mean_all = statistics.mean(r[2] for r in many) if many else float("nan")
        speaks = sum(1 for r in one if r[2] >= SPEAK_P)
        choices = " / ".join(str(sum(1 for r in mine if r[3] == c)) for c in ("word", "step_in", "silent"))
        lines.append(
            f"| {LABEL[npc]} | {scales} | {mean_one:.2f} | {speaks} of {len(one)} | {mean_all:.2f} | {choices} |"
        )
    brago = statistics.mean(r[2] for r in rows if r[0] == BRAGO and not r[4])
    steps = statistics.mean(r[2] for r in rows if r[0] == STEPS and not r[4])
    lines += [
        "",
        f"Brago minus the bandit from the steps, to one: {brago - steps:+.2f}"
        + (" — Brago speaks up more." if brago - steps >= 0.1 else " — not apart by 0.1."),
    ]
    out = Path(__file__).parent / "preds" / f"interject-{name}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "probe"))
