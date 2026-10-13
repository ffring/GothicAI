"""The hero's phrases (voice_phrases.yaml) -> training rows for the `voice` question: what sound an npc makes
at once to what the hero just said (server/src/gothic_ai/brain/reflex.py, Reflexes.phrase).

Every phrase is put in the hero's mouth to `--per-phrase` npcs drawn from the character cards
(gamedata/characters.json), the state built by the server's own code the way the game builds it: the npc's
card line, the time, the distance, a few of the lines it perceives (place, a stranger, others about) and
its intent. Half of the phrases come as speech recognition writes them, without "ё" («Лестер мертв.»),
some of them lower case without punctuation too. The npcs of evals/talk-reactions (Xardas, Lobart, the
gate guards) are left out, and so are its phrases (voice_phrases.yaml), so the eval stays fair. Every
tenth phrase of a kind goes to validation, with all its npcs.

    cd decider-service/tuning
    uv run --project ../../server python make_voice.py [--per-phrase 2] [--out voice]

Writes data/<out>/gothic_train.jsonl and gothic_val.jsonl (finetune.py Examples, task gothic:voice), and
for score_voice.py on the GPU host: eval_requests.jsonl (evals/talk-reactions, its states built as its
run.py builds them, typed and as heard) and questions.json (the voice and gesture questions, the voice
thresholds).
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

import yaml
from gothic_ai import characters
from gothic_ai.brain import reflex
from gothic_ai.brain.reflex import Reflexes
from gothic_ai.brain.situation import NpcContext, clock_text
from gothic_ai.executor import Intent
from gothic_ai.perception import ARMOR_LOOKS

HERE = Path(__file__).parent
DATA = HERE / "data"
PLACES = Path(characters.__file__).parent / "gamedata" / "places.json"
EVAL = HERE.parent.parent / "evals" / "talk-reactions"
# the npcs of evals/talk-reactions: never in training
EVAL_NPCS = {"NONE_100_XARDAS", "BAU_950_LOBART", "MIL_309_STADTWACHE"}
GOALS = [("keep_routine", 70), ("none", 12), ("follow_hero", 6), ("guard_hero", 4), ("change_plans", 8)]
ATTITUDES = [("neutral", 40), ("friendly", 25), ("wary", 18), ("hostile", 7), ("devoted", 10)]
NO_INTENT = 0.15  # the first turn of a talk: the conversational model has not set one yet
NO_CARD = 0.1  # an npc without a card: "You are Бромор, someone."
SPOKEN = 0.12  # as speech recognition may give it: lower case, no punctuation, no "ё"
HEARD = 0.45  # as speech recognition writes it: no "ё", else as said (it never writes "ё")
DOING = ["talking", "eating", "asleep", "working"]


def weighted(rng: random.Random, pairs: list[tuple[str, int]]) -> str:
    return rng.choices([k for k, _ in pairs], [w for _, w in pairs])[0]


def heard(text: str) -> str:
    """As speech recognition writes it: no "ё"."""
    return text.replace("ё", "е").replace("Ё", "Е")


def spoken(text: str) -> str:
    """Lower case, no punctuation but the dashes inside words, no "ё"."""
    text = re.sub(r"[«»\"!?.,;:…—–]", " ", heard(text).lower())
    return " ".join(text.split())


def view(rng: random.Random, places: list[dict], names: list[str]) -> tuple[str, ...]:
    """A few lines of what the npc perceives, as perception.english writes them."""
    lines = []
    if rng.random() < 0.55:
        place = rng.choice(places)
        lines.append(f"You are {place['where_en']}" + (", indoors." if rng.random() < 0.25 else "."))
    if rng.random() < 0.25:
        look = rng.choice(list(ARMOR_LOOKS.values()))[1]
        lines.append(f"The hero: you do not know him, he looks like {look}.")
    if rng.random() < 0.25:
        d = rng.randint(2, 15)
        if rng.random() < 0.5:
            who = f"{rng.choice(names)}, whom you know,"
        else:
            who = f"A stranger who looks like {rng.choice(list(ARMOR_LOOKS.values()))[1]},"
        what = f", {rng.choice(DOING)}" if rng.random() < 0.5 else ""
        lines.append(f"{who} is {d} m away{what}.")
    return tuple(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-phrase", type=int, default=2, help="npcs each phrase is said to")
    ap.add_argument("--out", default="voice", help="directory under data/")
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    phrases = yaml.safe_load((HERE / "voice_phrases.yaml").read_text(encoding="utf-8"))
    cards = [c for npc, c in characters.load().items() if npc not in EVAL_NPCS and "TORWACHE" not in npc]
    places = json.loads(PLACES.read_text(encoding="utf-8"))["places"]
    names = sorted({c.name for c in cards})
    reflexes = Reflexes(decider=None)  # type: ignore[arg-type] - only its _state
    instructions = reflex.INSTRUCTIONS["voice"]
    options = [[k, o.text] for k, o in reflex.VOICE_OPTIONS.items()]
    keys = [k for k, _ in options]
    assert set(phrases) == set(keys), set(phrases) ^ set(keys)

    train, val = [], []
    stats: Counter = Counter()
    for kind, texts in phrases.items():
        for i, text in enumerate(texts):
            is_val = i % 10 == 0
            for card in rng.sample(cards, args.per_phrase):
                words = text.replace("{name}", card.name)
                form = rng.random()
                if form < SPOKEN:
                    words = spoken(words)
                    stats["spoken"] += 1
                elif form < SPOKEN + HEARD and heard(words) != words:
                    words = heard(words)
                    stats["heard, no ё"] += 1
                traits = None if rng.random() < NO_CARD else card.en
                npc = NpcContext(
                    card.npc,
                    card.name,
                    -1,
                    rng.choice([100, 150, 200, 250, 300, 400]),
                    True,
                    traits=traits,
                    view=view(rng, places, names),
                )
                intent = None
                if rng.random() >= NO_INTENT:
                    intent = Intent(goal=weighted(rng, GOALS), attitude=weighted(rng, ATTITUDES))
                hour = rng.choice([*range(6, 23)] * 3 + [*range(0, 6), 23])
                clock = clock_text(hour, rng.randrange(0, 60, 5)) if rng.random() < 0.9 else None
                state = reflexes._state(npc, intent, clock, [f'The hero says: "{words}"'])
                (val if is_val else train).append(
                    {
                        "kind": "choice",
                        "state": state,
                        "instructions": instructions,
                        "options": options,
                        "label": keys.index(kind),
                        "task": "gothic:voice",
                    }
                )
                stats[f"{kind} {'val' if is_val else 'train'}"] += 1
    rng.shuffle(train)
    out_dir = DATA / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("gothic_train", train), ("gothic_val", val)):
        with (out_dir / f"{name}.jsonl").open("w", encoding="utf-8") as out:
            out.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    print(f"train {len(train)}, val {len(val)} rows -> {out_dir}")
    for k in sorted(stats):
        print(f"  {k}: {stats[k]}")

    questions = {
        q: {
            "type": "choice",
            "instructions": reflex.INSTRUCTIONS[q],
            "criteria": {k: o.text for k, o in opts.items()},
        }
        for q, opts in (("voice", reflex.VOICE_OPTIONS), ("gesture", reflex.GESTURE_OPTIONS))
    }
    thresholds = {k: o.threshold for k, o in reflex.VOICE_OPTIONS.items()}
    (out_dir / "questions.json").write_text(
        json.dumps({"questions": questions, "thresholds": thresholds}, ensure_ascii=False, indent=1)
    )
    ev = yaml.safe_load((EVAL / "phrases.yaml").read_text(encoding="utf-8"))
    with (out_dir / "eval_requests.jsonl").open("w", encoding="utf-8") as out:
        for i, p in enumerate(ev["phrases"]):
            n = ev["npcs"][p["npc"]]
            traits = " ".join(n["traits"].split())
            ctx = NpcContext(n["id"], n["name"], -1, n["distance_cm"], True, traits=traits)
            intent = Intent(**n["intent"]) if n.get("intent") else None
            state, as_heard = (
                reflexes._state(ctx, intent, n.get("clock"), [f'The hero says: "{say}"'])
                for say in (p["say"], heard(p["say"]))
            )
            row = {
                "i": i,
                "npc": p["npc"],
                "say": p["say"],
                "good": p["good"],
                "state": state,
                "heard": as_heard,
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"eval {len(ev['phrases'])} requests")


if __name__ == "__main__":
    main()
