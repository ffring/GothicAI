"""Training rows for the `do` question: what an npc does in its next game hour (docs/npc-tools.md, wave 2
step 6; server/src/gothic_ai/daylife.py). g8b reads the menu but not the reason of a walk in it: on the day
probe (evals/npc-decisions/day_probe.py) P(chat) is the same with the news and without. Hence g9.

The requests are the server's own: the probe's Grid builds them with daylife.day_menu and
SituationBuilder for the people of the routines' dump (day_people.json), here for those NOT in the probe
(by id, so the probe stays fair). Each npc at two hours of the day, with no concern and with the concerns
its menu can bring a walk for, varied more than the probe's:
- news: the hero lied to (caught), cheated (found_out) or did not pay back (broken) one of the npc's
  acquaintances, the npc one retelling away; control: the same walk without its reason;
- death: the body of an acquaintance, seen today;
- loss: the hero cheated the npc of its reward (its state carries that agenda, as in the game) or did not
  pay its debt back (the lender's agenda).

Labels: gpt-6.1-sol twice, with the labeller's prompt of label.py and run_gpt.py; the B pass has the
menus reversed and the rows shuffled. A row goes to training only if both passes chose the same option;
every tenth npc goes to validation with all its rows.

    cd decider-service/tuning
    uv run --project ../../server python make_day.py rows               # data/day/rows.jsonl, no model
    uv run --project ../../server python make_day.py label A            # gpt-6.1-sol (16 rows a call)
    uv run --project ../../server python make_day.py label B
    uv run --project ../../server python make_day.py build --out day1   # data/day1/gothic_{train,val}.jsonl

The rows are finetune.py Examples (kind choice, task gothic:do).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from gothic_ai.concerns import LIED_EN
from gothic_ai.daylife import DayWorld
from gothic_ai.llm import Done, LLMError, Message, make_backend
from gothic_ai.social import DAY, Rumor
from label import parse, reverse_menus
from run_gpt import INSTRUCTIONS, render

HERE = Path(__file__).parent
DATA = HERE / "data"
sys.path.insert(0, str(HERE.parent.parent / "evals" / "npc-decisions"))
from day_probe import NEWS_QUEST, QUEST, Grid  # noqa: E402

SEED = 9
HOURS = range(7, 21)  # the walks' hours (06:00-21:00), an hour of a day's work
PER_NPC = 2  # hours per npc
NONE_SHARE = 0.5  # of an npc's hours, how many also get a row with no concern (the routine goes on)
# of the rows a concern brings, the share kept: walks to tell are the most, their control needs fewer
KEEP_SHARE = {"news": 0.8, "control": 0.45, "death": 1.0, "loss": 1.0}
KINDS = ("caught", "found_out", "broken")  # what the news is
TITLES = ("Убрать поле репы", "Принести шкуры волков", "Отнести письмо в город", "Найти пропавшую овцу")
DEBT = 25
BATCH = 16  # rows per labeller call
VAL_EVERY = 10  # every tenth npc: validation
TASK = "gothic:do"


class TrainGrid(Grid):
    """The probe's grid with the concerns drawn per row (`variant`), for npc's outside the probe."""

    def __init__(self) -> None:
        super().__init__()
        self.variant: dict[str, Any] = {}

    def world(self, npc: str, hour: int, cond: str) -> tuple[DayWorld, str | None]:
        if not self.variant:  # the probe's own picks and plain rows
            return super().world(npc, hour, cond)
        v, social, clock = self.variant, self.social, DAY + hour * 60
        rumors: list[Rumor] = []
        facts: dict[str, list[dict[str, Any]]] = {}
        dead: set[str] = set()
        agenda = None
        if cond in ("news", "control") and v.get("about"):
            about = v["about"]
            rumors.append(
                Rumor(
                    NEWS_QUEST,
                    v["kind"],
                    about,
                    social.names[about],
                    v["title"],
                    {about: 0, npc: 1},
                    {npc: 1},
                )
            )
        elif cond == "death" and v.get("about"):
            body = v["about"]
            seen = {"fact": "death_seen", "npc": npc, "dead": body, "dead_name": social.names[body], "day": 1}
            facts["death_seen"] = [{**seen, "hour": hour, "minute": 0}]
            dead.add(body)
        elif cond == "loss":
            if v["kind"] == "broken":
                facts["broken"] = [{"fact": "broken", "debt": "D1", "npc": npc, "day": 1}]
                agenda = f"make the hero pay back the {DEBT} gold he owes you; his day has passed"
            else:
                facts["found_out"] = [{"fact": "found_out", "quest": QUEST, "npc": npc, "day": 1}]
                facts["paid"] = [{"fact": "paid", "quest": QUEST, "item": "ITMI_GOLD", "count": v["gold"]}]
                agenda = LIED_EN["found_out"]
        world = DayWorld(
            social,
            rumors,
            lambda r, a, b: b not in r.known,
            dead,
            lambda k: facts.get(k, []),
            clock,
            self.book,
        )
        return world, agenda

    def draw(self, rng: random.Random, npc: str, cond: str) -> dict[str, Any]:
        friends = sorted(self.social.ties.get(npc, {}))
        return {
            "about": rng.choice(friends) if friends else None,
            "kind": rng.choice(KINDS) if cond in ("news", "control") else rng.choice(("found_out", "broken")),
            "title": rng.choice(TITLES),
            "gold": rng.choice((20, 50, 100)),
        }

    def training_rows(self) -> list[dict[str, Any]]:
        rng = random.Random(SEED)
        held = set(self.pick())  # the probe's npc's
        people = sorted(n for n in self.people if n not in held and self.social.spot(n, 14 * 60) is not None)
        out = []
        for npc in people:
            for hour in rng.sample(list(HOURS), PER_NPC):
                conds = ["none"] if rng.random() < NONE_SHARE else []
                for cond in ("news", "control", "death", "loss"):
                    self.variant = self.draw(rng, npc, cond)
                    row = self.row(npc, hour, cond, ["train"])
                    want = {"news": "chat", "control": "chat", "death": "pray", "loss": "tavern"}[cond]
                    if row["questions"] and want in row["options"]["do"] and rng.random() < KEEP_SHARE[cond]:
                        conds.append(cond)
                        out.append(self._only_do(row, self.variant))
                if "none" in conds:
                    self.variant = {}
                    row = self.row(npc, hour, "none", ["train"])
                    if row["questions"]:
                        out.append(self._only_do(row, {}))
        return out

    @staticmethod
    def _only_do(row: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
        """The do question alone (the one g9 learns), the drawn concern kept for the report."""
        tag = f"{variant.get('kind', '')}:{variant.get('about', '')}" if variant else ""
        return {
            "id": f"{row['id']}/{tag}" if tag else row["id"],
            "npc": row["npc"],
            "hour": row["hour"],
            "cond": row["cond"],
            "variant": variant,
            "state": row["state"],
            "questions": {"do": row["questions"]["do"]},
            "kinds": {k: o["kind"] for k, o in row["options"]["do"].items()},
        }


def rows_path() -> Path:
    return DATA / "day" / "rows.jsonl"


def labels_path(pass_: str) -> Path:
    return DATA / "day" / f"labels_{pass_}.jsonl"


def make_rows() -> None:
    rows = [r for r in TrainGrid().training_rows() if "do" in r["questions"]]
    rows_path().parent.mkdir(parents=True, exist_ok=True)
    with rows_path().open("w", encoding="utf-8") as out:
        for r in rows:
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(
        f"{len(rows)} rows of {len({r['npc'] for r in rows})} npc's: {dict(Counter(r['cond'] for r in rows))}"
    )
    print(f"-> {rows_path()}")


async def label(pass_: str, effort: str) -> None:
    rows = [json.loads(line) for line in rows_path().open(encoding="utf-8")]
    path = labels_path(pass_)
    done = {json.loads(line)["id"] for line in path.open()} if path.exists() else set()
    todo = [
        {"id": r["id"], "state": r["state"], "questions": r["questions"]} for r in rows if r["id"] not in done
    ]
    if pass_ == "B":
        todo = [reverse_menus(r) for r in todo]
        random.Random(7).shuffle(todo)
    batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
    print(f"{len(rows)} rows, {len(done)} labelled, {len(batches)} calls to go", flush=True)
    backend = make_backend("chatgpt", reasoning_effort=effort)
    try:
        for i, chunk in enumerate(batches):
            got: dict[str, Any] = {}
            for attempt in range(3):
                try:
                    text = ""
                    messages = [Message("user", "\n\n".join(render(r) for r in chunk))]
                    async for ev in backend.stream(instructions=INSTRUCTIONS, messages=messages):
                        if isinstance(ev, Done):
                            text = ev.text
                    m = re.search(r"\{.*\}", text, re.DOTALL)
                    got = {a["id"]: a for a in json.loads(m.group(0))["answers"]} if m else {}
                    break
                except (LLMError, json.JSONDecodeError, KeyError, TypeError) as e:
                    if isinstance(e, LLMError) and not e.retryable:
                        raise
                    print(f"call {i}: {e!r}, retry {attempt + 1}", flush=True)
                    await asyncio.sleep(5 * (attempt + 1))
            with path.open("a", encoding="utf-8") as out:
                for r in chunk:
                    ans = parse(r, got.get(r["id"], {}))
                    if ans is not None:
                        out.write(json.dumps({"id": r["id"], "answers": ans}, ensure_ascii=False) + "\n")
            print(
                f"call {i + 1}/{len(batches)}: {sum(1 for r in chunk if r['id'] in got)} of {len(chunk)}",
                flush=True,
            )
    finally:
        await backend.aclose()


def labels(pass_: str) -> dict[str, str]:
    path = labels_path(pass_)
    return (
        {d["id"]: d["answers"].get("do") for d in map(json.loads, path.open(encoding="utf-8"))}
        if path.exists()
        else {}
    )


def build(out_name: str) -> None:
    rows = [json.loads(line) for line in rows_path().open(encoding="utf-8")]
    a, b = labels("A"), labels("B")
    npcs = sorted({r["npc"] for r in rows})
    val_npcs = set(npcs[::VAL_EVERY])
    out_dir = DATA / out_name
    out_dir.mkdir(parents=True, exist_ok=True)
    kept: Counter[str] = Counter()
    gold: dict[str, Counter[str]] = {}
    with (
        (out_dir / "gothic_train.jsonl").open("w", encoding="utf-8") as train,
        (out_dir / "gothic_val.jsonl").open("w", encoding="utf-8") as val,
    ):
        for r in rows:
            choice = a.get(r["id"])
            if choice is None or choice != b.get(r["id"]):
                kept["disagree" if choice is not None and r["id"] in b else "unlabelled"] += 1
                continue
            q = r["questions"]["do"]
            keys = list(q["criteria"])
            example = {
                "kind": "choice",
                "state": r["state"],
                "instructions": q["instructions"],
                "options": [[k, q["criteria"][k]] for k in keys],
                "label": keys.index(choice),
                "task": TASK,
            }
            split = "val" if r["npc"] in val_npcs else "train"
            (val if split == "val" else train).write(json.dumps(example, ensure_ascii=False) + "\n")
            kept[split] += 1
            gold.setdefault(r["cond"], Counter())[r["kinds"].get(choice, "?")] += 1
    print(f"kept: {dict(kept)}")
    for cond, c in sorted(gold.items()):
        print(f"  {cond:8s} gold: {dict(c)}")
    print(f"-> {out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rows")
    lab = sub.add_parser("label")
    lab.add_argument("pass_", choices=("A", "B"))
    lab.add_argument("--effort", default="low")
    bld = sub.add_parser("build")
    bld.add_argument("--out", default="day1")
    args = ap.parse_args()
    if args.cmd == "rows":
        make_rows()
    elif args.cmd == "label":
        asyncio.run(label(args.pass_, args.effort))
    else:
        build(args.out)


if __name__ == "__main__":
    main()
