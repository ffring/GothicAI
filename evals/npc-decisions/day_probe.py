"""Does the behavioral model choose its next hour by its everyday concerns? (docs/npc-tools.md, wave 2 step 6)

The planner asks an npc near the hero what it does in its next hour: the old `plan` menu (applied) and, in
shadow, the new `do` menu with the trips its everyday concerns suggest (daylife.py). Here the same requests,
built by the server's own code (`daylife.day_menu`, `SituationBuilder.plan_state`: no wording is copied) for
the people of the routines' dump (day_people.json, a SCENARIO=places dump), go to the deployed decider.

The grid: the npc's are picked by fixed rules with a seed (the list goes into the report): 8 with a chat
target in reach, 8 with a free prayer spot in reach, 8 with a free drinking spot in reach, 2 on duty (they
must get no question), Xardas (plot: keep and his habit only), and Vino, Malet, Lobart and Hilda always. Each
at 09:00, 14:00 and 19:00, with no concern and with the concerns it was picked for: news (a caught rumor
about an acquaintance, the npc one retelling away, since today) and its control (the same walk without its
reason), a death seen (an acquaintance's body), a loss (the hero took its 50 gold reward by a lie: its state
carries that agenda, as in the game). The specials get every condition.

Each row goes to the decider as the planner sends it (both questions in one request), again with both menus
reversed, and once with the old question alone (latency).

    uv run --project ../../server python day_probe.py menus             # the grid and its sanity, no model
    GOTHIC_DECIDER_URL=ws://<dev-ubuntu>:47832 uv run --project ../../server python day_probe.py run g8b
    uv run --project ../../server python day_probe.py label A           # gpt-6.1-sol, plan quota (~11 calls)
    uv run --project ../../server python day_probe.py label B           # menus reversed, rows shuffled
    uv run --project ../../server python day_probe.py score g8b         # -> preds/day-g8b.md
    uv run --project ../../server python day_probe.py traces <trace dir>   # the game's shadow rows, no model

Token counts come from day_tokens.py on dev-ubuntu (the decider's own renderer and tokenizer: no models on
the Mac); `score` reads its preds/day-<name>.tokens.json when it is there. The decider runs on dev-ubuntu;
this script is only its client. The probe npc's (by id) stay out of the next model's training rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from gothic_ai import characters
from gothic_ai.brain.day import ACTIVITIES, KEEP, SLEEP_STATES, DayMenu, current_entry
from gothic_ai.brain.decider import DeciderClient
from gothic_ai.brain.planner import PlannerConfig
from gothic_ai.brain.situation import NpcContext, SituationBuilder, clock_text
from gothic_ai.concerns import LIED_EN
from gothic_ai.daylife import (
    DRINK_STATE,
    PRAY_STATES,
    TAVERNS,
    TRIP_FROM,
    TRIP_TO,
    TRIPS,
    DayWorld,
    chat_text,
    day_menu,
    routine_entries,
)
from gothic_ai.llm import Done, LLMError, Message, make_backend
from gothic_ai.persona import habits_of
from gothic_ai.places import PlaceBook
from gothic_ai.social import DAY, OUTLAWS, Rumor, Social
from gothic_ai.storyguard import StoryGuard, asleep
from gothic_ai.trace import read as read_trace

HERE = Path(__file__).parent
PREDS = HERE / "preds"
PEOPLE = HERE / "day_people.json"
# the labeller's prompt and the reversal are the tuning's own (decider-service/tuning/label.py, run_gpt.py)
sys.path.insert(0, str(HERE.parent.parent / "decider-service" / "tuning"))
from label import parse, reverse_menus  # noqa: E402
from run_gpt import INSTRUCTIONS, render  # noqa: E402

HOURS = (9, 14, 19)
CONDS = ("none", "control", "news", "death", "loss")
TRIP = {"control": "chat", "news": "chat", "death": "pray", "loss": "tavern"}  # the option a condition brings
PICKED_FOR = {"chat": ("control", "news"), "pray": ("death",), "tavern": ("loss",)}
SEED, PER_KIND, DUTY = 6, 8, 2
VINO, MALETH, LOBART, HILDA, XARDAS = (
    "BAU_952_VINO",
    "BAU_954_MALETH",
    "BAU_950_LOBART",
    "BAU_951_HILDA",
    "NONE_100_XARDAS",
)
ALWAYS = (VINO, MALETH, LOBART, HILDA, XARDAS)
QUEST, NEWS_QUEST = "PROBE_REWARD", "PROBE_NEWS"
# the pass rule, written down before the run (docs/npc-tools.md, wave 2 step 6)
DEAD_KEYS = {"pick_up", "give_back", "rest", "socialize"}
DEAD_TEXTS = ("take a break and rest", "go and chat with people nearby")
MAX_OPTIONS = 8
BOUND_CHARS = 700  # rendered do question, p95
LATENCY_MS = 15.0  # two questions over one, infer_ms p50
SENSITIVITY = 0.15  # P(chat | news) - P(chat | control)
ACCURACY, KIND_MIN, VS_PLAN = 0.75, 0.60, 0.02
STABILITY = 0.85
THRESHOLDS = (0.3, 0.5, 0.7)
BATCH = 16  # rows per labeller call


# --- the grid ----------------------------------------------------------------------------------------------


class Grid:
    """The people of the dump and what the server builds for them."""

    def __init__(self) -> None:
        self.people = {p["npc"]: p for p in json.loads(PEOPLE.read_text(encoding="utf-8"))}
        self.social = Social(list(self.people.values()))
        self.book = PlaceBook.load()
        self.guard = StoryGuard(None)  # without the story graph: the plot by instance prefixes
        cards = characters.load()
        self.builder = SituationBuilder(traits={npc: card.en for npc, card in cards.items()})

    def world(self, npc: str, hour: int, cond: str) -> tuple[DayWorld, str | None]:
        """The world the npc's menu is built in, at the hour of day 1, with the condition's concern; and the
        agenda its state carries."""
        social, clock = self.social, DAY + hour * 60
        friends = sorted(social.ties.get(npc, {}))
        rumors: list[Rumor] = []
        facts: dict[str, list[dict[str, Any]]] = {}
        dead: set[str] = set()
        agenda = None
        if cond in ("news", "control") and friends:
            about = friends[0]
            rumors.append(
                Rumor(
                    NEWS_QUEST,
                    "caught",
                    about,
                    social.names[about],
                    "Убрать поле репы",
                    {about: 0, npc: 1},
                    {npc: 1},
                )
            )
        elif cond == "death" and friends:
            body = friends[0]
            seen = {"fact": "death_seen", "npc": npc, "dead": body, "dead_name": social.names[body], "day": 1}
            facts["death_seen"] = [{**seen, "hour": hour, "minute": 0}]
            dead.add(body)
        elif cond == "loss":
            facts["found_out"] = [{"fact": "found_out", "quest": QUEST, "npc": npc, "day": 1}]
            facts["paid"] = [{"fact": "paid", "quest": QUEST, "item": "ITMI_GOLD", "count": 50}]
            agenda = LIED_EN["found_out"]
        world = DayWorld(
            social,
            rumors,
            lambda r, a, b: b not in r.known,
            dead,
            lambda kind: facts.get(kind, []),
            clock,
            self.book,
        )
        return world, agenda

    def menu(self, npc: str, hour: int, cond: str) -> tuple[DayMenu, DayWorld, str | None, str]:
        """The npc's menus as the desk builds them (its state the routine's now), the control's walk
        without its reason; the world, the agenda and the state it is in."""
        person = self.people[npc]
        entries = routine_entries(person)
        now = current_entry(entries, hour, 0)
        state = now.state.upper() if now is not None else ""
        world, agenda = self.world(npc, hour, cond)
        menu = day_menu(
            npc,
            int(person.get("guild", 0)),
            state,
            entries,
            habits_of(npc),
            hour,
            0,
            self.guard,
            world,
            world.where,
        )
        chat = menu.do.get("chat")
        if cond == "control" and chat is not None and chat.args is not None:
            b, wp = str(chat.partner), chat.args["waypoint"]
            text = chat_text(self.social.names.get(b, b), world.where(wp), None, chat.args["minutes"])
            menu = replace(
                menu, do={k: replace(o, text=text) if k == "chat" else o for k, o in menu.do.items()}
            )
        return menu, world, agenda, state

    def offers(self, npc: str, kind: str) -> bool:
        """The concern of this kind brings its trip at one of the hours at least."""
        cond = PICKED_FOR[kind][-1]
        return any(kind in self.menu(npc, hour, cond)[0].do for hour in HOURS)

    def pick(self) -> dict[str, list[str]]:
        """The npc's of the grid, by the fixed rules and the seed: npc -> what it was picked for."""
        rng = random.Random(SEED)
        picked: dict[str, list[str]] = {npc: ["always"] for npc in ALWAYS}
        pool = sorted(n for n in self.people if n not in picked and self.social.spot(n, 14 * 60) is not None)
        for kind in ("chat", "pray", "tavern"):
            free = [n for n in pool if n not in picked and self.offers(n, kind)]
            for npc in rng.sample(free, min(PER_KIND, len(free))):
                picked[npc] = [kind]
        duty = [n for n in pool if n not in picked and self._at_post(n)]
        for npc in rng.sample(duty, min(DUTY, len(duty))):
            picked[npc] = ["duty"]
        return picked

    def _at_post(self, npc: str) -> bool:
        spot = self.social.spot(npc, 14 * 60)
        return spot is not None and spot.state == "ZS_GUARD_PASSAGE"

    def rows(self) -> list[dict[str, Any]]:
        """Every row of the grid: the request as the planner sends it (none when it would not ask) and what
        each option is."""
        out = []
        for npc, why in self.pick().items():
            conds = CONDS if "always" in why else ("none", *(c for k in why for c in PICKED_FOR.get(k, ())))
            for hour in HOURS:
                for cond in conds:
                    out.append(self.row(npc, hour, cond, why))
        return out

    def row(self, npc: str, hour: int, cond: str, why: list[str]) -> dict[str, Any]:
        person = self.people[npc]
        menu, world, agenda, state = self.menu(npc, hour, cond)
        ctx = NpcContext(npc, person.get("name") or npc, int(person.get("guild", 0)), agenda=agenda)
        clock = clock_text(hour, 0)
        plan = menu.plan[0] if len(menu.plan[0]) >= 2 and menu.stay is None else None
        do = {k: o.text for k, o in menu.do.items()} if len(menu.do) >= 2 and menu.stay is None else None
        sit = self.builder.day(ctx, clock, plan, do)
        spot = self.social.spot(npc, hour * 60)
        return {
            "id": f"{npc}/{hour:02d}/{cond}",
            "npc": npc,
            "name": ctx.name,
            "picked": why,
            "hour": hour,
            "cond": cond,
            "current": {"state": state, "waypoint": spot.waypoint if spot is not None else None},
            "stay": menu.stay,
            "state": sit.state,
            "questions": sit.questions,
            "options": {
                "plan": {k: {"args": a} for k, a in menu.plan[1].items()},
                "do": {
                    k: {
                        "id": o.id,
                        "kind": o.kind,
                        "args": o.args,
                        "concern": o.concern,
                        "partner": o.partner,
                    }
                    for k, o in menu.do.items()
                },
            },
            "expect": self.expect(npc, cond, menu, world, state),
        }

    def expect(self, npc: str, cond: str, menu: DayMenu, world: DayWorld, state: str) -> str | None:
        """The trip the condition's concern should bring, by the rules said once more in plain terms (a check
        of trip_options): a target or spot in reach and with room, the npc free to go. None: no trip due."""
        kind = TRIP.get(cond)
        minute, social = world.clock % DAY, self.social
        here = social.spot(npc, minute)
        guild = int(self.people[npc].get("guild", 0))
        if kind is None or menu.stay is not None or self.guard.plot(npc) or here is None or here.pos is None:
            return None
        if not TRIP_FROM <= minute < TRIP_TO:
            return None

        def near(pos: Any) -> bool:
            return (
                pos is not None
                and sum((a - b) ** 2 for a, b in zip(here.pos, pos, strict=True)) ** 0.5 <= world.trip_cm
            )

        if kind == "chat":
            r = next(iter(world.rumors), None)
            for b, tie in social.ties.get(npc, {}).items():
                there = social.spot(b, minute)
                if r is None or b in r.known or there is None or asleep(there.state):
                    continue
                if there.waypoint.upper().startswith("FP_") or not near(there.pos):
                    continue
                if next(tie.occasions(npc, b, world.clock, world.clock + 60), None) is None:
                    return kind
            return None
        if guild in OUTLAWS:
            return None
        states = PRAY_STATES if kind == "pray" else (DRINK_STATE,)
        spots = {s.waypoint.upper(): s for _, s in social.spots() if s.state in states and s.pos is not None}
        if kind == "tavern":
            spots = {wp: s for wp, s in spots.items() if self._place(wp) in TAVERNS}
            if state == DRINK_STATE or self._place(here.waypoint) in TAVERNS:
                return None
        elif state in PRAY_STATES or here.waypoint.upper() in spots:
            return None
        return kind if any(near(s.pos) and world.free(wp, npc) for wp, s in spots.items()) else None

    def _place(self, waypoint: str) -> str | None:
        place = self.book.of(waypoint) if self.book is not None else None
        return place.id if place is not None else None


# --- sanity and lengths --------------------------------------------------------------------------------------


def rendered(q: dict[str, Any]) -> int:
    """A choice question's length as the decider renders it: its instructions and a line per option."""
    lines = [f"{n}. {k} — {v}" for n, (k, v) in enumerate(q["criteria"].items(), 1)]
    return len(q["instructions"]) + sum(len(line) + 1 for line in lines)


def sanity(rows: list[dict[str, Any]], grid: Grid) -> list[str]:
    """What breaks the menus' rules (must be none)."""
    bad = []
    for row in rows:
        rid, cur = row["id"], row["current"]
        for name, q in row["questions"].items():
            for key, text in q["criteria"].items():
                if key in DEAD_KEYS or any(t in text for t in DEAD_TEXTS):
                    bad.append(f"{rid} {name}: dead option {key}")
            if len(q["criteria"]) > MAX_OPTIONS:
                bad.append(f"{rid} {name}: {len(q['criteria'])} options")
        for name, options in row["options"].items():
            for key, o in options.items():
                args = o.get("args")
                if not args:
                    continue
                state, wp = str(args["state"]).upper(), str(args["waypoint"]).upper()
                if state.startswith(SLEEP_STATES):
                    bad.append(f"{rid} {name} {key}: sleep {state}")
                if o.get("kind", "routine") in ("routine", "habit") and state not in ACTIVITIES:
                    bad.append(f"{rid} {name} {key}: no words for {state}")
                if state == cur["state"]:
                    bad.append(f"{rid} {name} {key}: the state it is in, {state}")
                there = grid.social.users(wp, row["hour"] * 60) - {row["npc"]}
                if len(there) >= grid.social.capacity(wp) and o.get("kind") not in TRIPS:
                    bad.append(f"{rid} {name} {key}: {wp} is full")
        do = row["options"]["do"]
        if grid._at_post(row["npc"]) and row["questions"]:
            bad.append(f"{rid}: asked on duty")
        if row["npc"] == XARDAS and row["stay"] is None and set(do) != {KEEP, "h1"}:
            bad.append(f"{rid}: Xardas offered {sorted(do)}")
        if grid.guard.plot(row["npc"]) and any(o["kind"] in TRIPS for o in do.values()):
            bad.append(f"{rid}: a trip for a plot npc")
        kind = TRIP.get(row["cond"])
        if kind is not None and (kind in do) != (row["expect"] == kind):
            due = "due" if row["expect"] == kind else "not due"
            bad.append(f"{rid}: {kind} {'offered' if kind in do else 'missing'} though {due}")
    return bad


def spread(xs: list[float]) -> str:
    if not xs:
        return "—"
    xs = sorted(xs)
    return f"{statistics.median(xs):.0f} / {xs[int(len(xs) * 0.95)]:.0f} / {xs[-1]:.0f}"


def p95(xs: list[float]) -> float:
    return sorted(xs)[int(len(xs) * 0.95)] if xs else 0.0


def lengths(rows: list[dict[str, Any]], tokens: dict[str, dict[str, int]]) -> list[str]:
    """The step-4 measure: the state as before (the loss rows carry the agenda), the questions' length."""
    asked = [r for r in rows if r["questions"]]
    lines = ["| what | p50 / p95 / max chars | p50 / p95 / max tokens |", "|---|---|---|"]

    def tok(part: str, rs: list[dict[str, Any]]) -> str:
        return spread([tokens[r["id"]][part] for r in rs if part in tokens.get(r["id"], {})])

    plain = [r for r in asked if r["cond"] != "loss"]
    loss = [r for r in asked if r["cond"] == "loss"]
    lines.append(f"| state | {spread([len(r['state']) for r in plain])} | {tok('state', plain)} |")
    lines.append(
        f"| state, loss rows (the agenda) | {spread([len(r['state']) for r in loss])} | {tok('state', loss)} |"
    )
    for name in ("plan", "do"):
        rs = [r for r in asked if name in r["questions"]]
        chars = [rendered(r["questions"][name]) for r in rs]
        lines.append(f"| `{name}` question | {spread(chars)} | {tok(name, rs)} |")
    sizes = Counter(len(r["questions"]["do"]["criteria"]) for r in asked if "do" in r["questions"])
    lines.append("")
    lines.append(f"do menu sizes: {dict(sorted(sizes.items()))}")
    return lines


# --- run ------------------------------------------------------------------------------------------------------


def reversed_questions(questions: dict[str, Any]) -> dict[str, Any]:
    return reverse_menus({"questions": questions})["questions"]


async def ask(client: DeciderClient, rid: str, state: str, questions: dict[str, Any]) -> dict[str, Any]:
    t = time.perf_counter()
    out = await client.decide(rid, "background", 1, 30000, state, questions)
    ms = (time.perf_counter() - t) * 1000
    answers = {
        name: {
            "choice": a.get("choice"),
            "probabilities": a.get("probabilities", {}),
            "confidence": a.get("confidence"),
        }
        for name, a in (out.answers or {}).items()
    }
    return {
        "kind": out.kind,
        "answers": answers,
        "infer_ms": out.infer_ms,
        "queue_ms": out.queue_ms,
        "ms": round(ms, 1),
    }


async def run(name: str) -> None:
    grid = Grid()
    rows = grid.rows()
    client = DeciderClient(os.environ["GOTHIC_DECIDER_URL"])
    PREDS.mkdir(exist_ok=True)
    path = PREDS / f"day-{name}.jsonl"
    asked = [r for r in rows if r["questions"]]
    print(f"{len(rows)} rows, {len(asked)} asked", flush=True)
    with path.open("w", encoding="utf-8") as out:
        for i, row in enumerate(rows):
            if row["questions"]:
                qs = row["questions"]
                row["forward"] = await ask(client, f"{row['npc']}#day{i}", row["state"], qs)
                row["reversed"] = await ask(
                    client, f"{row['npc']}#day{i}r", row["state"], reversed_questions(qs)
                )
                if "plan" in qs:
                    one = {"plan": qs["plan"]}
                    row["plan_only"] = await ask(client, f"{row['npc']}#day{i}p", row["state"], one)
                row["model"] = client.model
                do = row["forward"]["answers"].get("do", {})
                print(f"{row['id']:32s} do={do.get('choice')} ({do.get('confidence')})", flush=True)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"-> {path}")


# --- labels ---------------------------------------------------------------------------------------------------


async def label(pass_: str, batch: int, effort: str) -> None:
    rows = [r for r in Grid().rows() if r["questions"]]
    out_path = PREDS / f"day-labels-{pass_}.jsonl"
    done = {json.loads(line)["id"] for line in out_path.open()} if out_path.exists() else set()
    todo = [
        {"id": r["id"], "state": r["state"], "questions": r["questions"]} for r in rows if r["id"] not in done
    ]
    if pass_ == "B":
        todo = [reverse_menus(r) for r in todo]
        random.Random(7).shuffle(todo)
    batches = [todo[i : i + batch] for i in range(0, len(todo), batch)]
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
            with out_path.open("a", encoding="utf-8") as out:
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


def labels(pass_: str) -> dict[str, dict[str, str]]:
    path = PREDS / f"day-labels-{pass_}.jsonl"
    if not path.exists():
        return {}
    return {d["id"]: d["answers"] for d in map(json.loads, path.open(encoding="utf-8"))}


# --- score ----------------------------------------------------------------------------------------------------


def net(row: dict[str, Any], name: str, threshold: float) -> str:
    """What the planner would do: the choice if sure enough, else the routine goes on."""
    a = row["forward"]["answers"].get(name, {})
    return a.get("choice") if float(a.get("confidence") or 0.0) >= threshold else KEEP


def p_of(row: dict[str, Any], key: str) -> float:
    return float(row["forward"]["answers"].get("do", {}).get("probabilities", {}).get(key, 0.0))


def score(name: str) -> None:
    grid = Grid()
    rows = [json.loads(line) for line in (PREDS / f"day-{name}.jsonl").open(encoding="utf-8")]
    tokens_path = PREDS / f"day-{name}.tokens.json"
    tokens = json.loads(tokens_path.read_text()) if tokens_path.exists() else {}
    asked = [r for r in rows if r.get("forward", {}).get("kind") == "answers"]
    threshold = PlannerConfig().plan_threshold
    verdicts: list[tuple[str, bool, str]] = []
    out = [f"# The day menu, {name}", "", f"day_probe.py: {len(rows)} rows, {len(asked)} answered."]

    # 1. sanity
    bad = sanity(rows, grid)
    verdicts.append(("1. menus sane", not bad, f"{len(bad)} broken" if bad else "100%"))
    out += (
        ["", "## 1. Menus", "", *(f"- {b}" for b in bad[:30])]
        if bad
        else ["", "## 1. Menus", "", "All rules hold."]
    )

    # 2. lengths
    do_chars = [rendered(r["questions"]["do"]) for r in rows if "do" in r["questions"]]
    verdicts.append(("2. do question p95 ≤ 700 chars", p95(do_chars) <= BOUND_CHARS, f"{p95(do_chars):.0f}"))
    out += ["", "## 2. Length", "", *lengths(rows, tokens)]
    if not tokens:
        out.append("Tokens: run day_tokens.py on dev-ubuntu.")

    # 3. latency
    two = [r["forward"]["infer_ms"] for r in asked if len(r["questions"]) == 2 and r["forward"]["infer_ms"]]
    one = [r["plan_only"]["infer_ms"] for r in asked if r.get("plan_only", {}).get("infer_ms")]
    gap = statistics.median(two) - statistics.median(one) if two and one else float("nan")
    verdicts.append(("3. two questions ≤ one + 15 ms (infer p50)", gap <= LATENCY_MS, f"{gap:+.1f} ms"))
    out += [
        "",
        "## 3. Latency",
        "",
        f"infer_ms p50: two questions {spread(two)}, one {spread(one)} (p50 / p95 / max).",
    ]

    # 4. sensitivity
    def mean_p(cond: str, key: str) -> float:
        ps = [
            p_of(r, key)
            for r in asked
            if r["cond"] == cond and key in r["questions"].get("do", {}).get("criteria", {})
        ]
        return statistics.mean(ps) if ps else float("nan")

    sens = mean_p("news", "chat") - mean_p("control", "chat")
    verdicts.append(("4. mean P(chat): news − control ≥ 0.15", sens >= SENSITIVITY, f"{sens:+.2f}"))
    out += [
        "",
        "## 4. Concern sensitivity",
        "",
        "| condition | trip | mean P(trip) | chosen |",
        "|---|---|---|---|",
    ]
    for cond, key in TRIP.items():
        rs = [
            r for r in asked if r["cond"] == cond and key in r["questions"].get("do", {}).get("criteria", {})
        ]
        chosen = sum(1 for r in rs if net(r, "do", threshold) == key)
        out.append(f"| {cond} | {key} | {mean_p(cond, key):.2f} | {chosen} of {len(rs)} |")

    # 5. accuracy
    a, b = labels("A"), labels("B")
    out += ["", "## 5. Accuracy", ""]
    if not a or not b:
        verdicts.append(("5. accuracy", False, "not labelled yet (label A, label B)"))
        out.append("Not labelled yet.")
    else:

        def gold(row: dict[str, Any], q: str) -> str | None:
            x, y = a.get(row["id"], {}).get(q), b.get(row["id"], {}).get(q)
            return x if x is not None and x == y else None

        def acc(rs: list[dict[str, Any]], q: str, th: float = threshold) -> float:
            rs = [r for r in rs if gold(r, q) is not None]
            return sum(net(r, q, th) == gold(r, q) for r in rs) / len(rs) if rs else float("nan")

        both = [r for r in asked if gold(r, "do") is not None]
        agree = (
            len(both) / len([r for r in asked if r["id"] in a and r["id"] in b]) if a and b else float("nan")
        )
        do_acc = acc(asked, "do")
        kinds = {c: acc([r for r in asked if r["cond"] == c], "do") for c in CONDS}
        same = [r for r in asked if gold(r, "do") is not None and gold(r, "plan") is not None]
        vs = acc(same, "do") - acc(same, "plan")
        worst = min(
            (v for c, v in kinds.items() if c in ("news", "death", "loss") and not math.isnan(v)), default=0.0
        )
        verdicts.append(("5. do ≥ 75% net", do_acc >= ACCURACY, f"{do_acc:.0%}"))
        verdicts.append(("5. no concern kind < 60%", worst >= KIND_MIN, f"worst {worst:.0%}"))
        verdicts.append(("5. do ≥ plan − 2 pp, same rows", vs >= -VS_PLAN, f"{vs * 100:+.1f} pp"))
        out += [
            f"A/B agreement (the ceiling): {agree:.0%} of {len(asked)} rows; gold rows: {len(both)}.",
            (
                f"Net at the planner's threshold {threshold}: do {do_acc:.0%}; on the rows with both golds do "
                f"{acc(same, 'do'):.0%}, plan {acc(same, 'plan'):.0%} ({len(same)} rows)."
            ),
            "",
            "| condition | do net |",
            "|---|---|",
            *(f"| {c} | {v:.0%} |" for c, v in kinds.items()),
            "",
            "| do options | rows | "
            + " | ".join(f"≥ {t}: coverage / correct / net" for t in THRESHOLDS)
            + " |",
            "|---|---|" + "---|" * len(THRESHOLDS),
        ]
        by_size: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in both:
            by_size[len(r["questions"]["do"]["criteria"])].append(r)
        for size, rs in sorted(by_size.items()):
            cells = []
            for t in THRESHOLDS:
                sure = [r for r in rs if float(r["forward"]["answers"]["do"].get("confidence") or 0) >= t]
                right = sum(r["forward"]["answers"]["do"].get("choice") == gold(r, "do") for r in sure)
                cells.append(
                    f"{len(sure) / len(rs):.0%} / {right / len(sure) if sure else 0:.0%} / {acc(rs, 'do', t):.0%}"
                )
            out.append(f"| {size} | {len(rs)} | " + " | ".join(cells) + " |")

    # 6. stability
    def same_both_ways(r: dict[str, Any], q: str) -> bool:
        return r["forward"]["answers"].get(q, {}).get("choice") == r["reversed"]["answers"].get(q, {}).get(
            "choice"
        )

    stable = [same_both_ways(r, "do") for r in asked if "do" in r["questions"] and r.get("reversed")]
    share = sum(stable) / len(stable) if stable else float("nan")
    verdicts.append(("6. same choice reversed ≥ 85%", share >= STABILITY, f"{share:.0%}"))
    out += [
        "",
        "## 6. Stability",
        "",
        f"do: the same choice with the menu reversed in {share:.0%} of {len(stable)} rows.",
    ]

    picked = sorted({(r["npc"], r["name"], ",".join(r["picked"])) for r in rows})
    out += ["", "## The npc's", "", *(f"- {n} {ru}: {why}" for n, ru, why in picked)]
    passed = all(ok for _, ok, _ in verdicts)
    head = ["", "## Verdict", "", "| rule | pass | value |", "|---|---|---|"]
    head += [f"| {rule} | {'yes' if ok else 'no'} | {value} |" for rule, ok, value in verdicts]
    head += [
        "",
        "All six pass: the do menu may come out of shadow."
        if passed
        else "Not all pass: the menus stay in shadow.",
    ]
    text = "\n".join(out[:3] + head + out[3:]) + "\n"
    (PREDS / f"day-{name}.md").write_text(text, encoding="utf-8")
    print(text)


# --- the game's traces ------------------------------------------------------------------------------------------


def traces(directory: str) -> None:
    """The planner's day rows from a play session (brain rows of `plan` and `do`), in short."""
    rows = [
        r
        for p in sorted(Path(directory).glob("*.jsonl"))
        for r in read_trace(p)
        if r.get("type") == "brain" and r.get("question") in ("plan", "do")
    ]
    print(f"{len(rows)} day rows in {directory}")
    hours = Counter(
        m.group(1) if (m := re.search(r"It is (\d\d):", r.get("state", ""))) else "?" for r in rows
    )
    print("by game hour:", dict(sorted(hours.items())))
    print("applied / shadow:", dict(Counter((r["question"], r.get("why") or "applied") for r in rows)))
    do = [r for r in rows if r["question"] == "do"]
    trips = [r for r in do if any((o or {}).get("kind") in TRIPS for o in (r.get("args") or {}).values())]
    print(f"do rows with a trip: {len(trips)} of {len(do)}")
    if trips:
        p = [
            sum(
                v
                for k, v in (r.get("probabilities") or {}).items()
                if (r["args"].get(k) or {}).get("kind") in TRIPS
            )
            for r in trips
        ]
        print(f"mean P(trip) where one is offered: {statistics.mean(p):.2f}")
        print("chosen:", dict(Counter(r.get("choice") for r in trips)))
    dead = [r["rid"] for r in rows if DEAD_KEYS & set(r.get("options") or {})]
    print(f"dead options: {len(dead)} rows {dead[:5]}")
    for q in ("plan", "do"):
        qs = [r for r in rows if r["question"] == q]
        chars = [
            sum(len(f"{n}. {k} — {v}") + 1 for n, (k, v) in enumerate(r["options"].items(), 1)) for r in qs
        ]
        print(
            f"{q}: options chars p50/p95/max {spread(chars)}; infer_ms {spread([r['infer_ms'] for r in qs if r.get('infer_ms')])}"
        )


# --- main -------------------------------------------------------------------------------------------------------


def menus() -> None:
    """The grid without the model: who, how many rows, the sanity and the lengths."""
    grid = Grid()
    rows = grid.rows()
    asked = [r for r in rows if r["questions"]]
    print(f"{len(rows)} rows, {len(asked)} asked; npc's: {len({r['npc'] for r in rows})}")
    for npc, why in grid.pick().items():
        print(f"  {npc} ({grid.social.names.get(npc)}): {', '.join(why)}")
    bad = sanity(rows, grid)
    print(f"sanity: {len(bad)} broken", *bad[:20], sep="\n  ")
    print("\n".join(lengths(rows, {})))
    for cond, key in TRIP.items():
        rs = [r for r in asked if r["cond"] == cond]
        print(f"{cond}: {sum(key in r['options']['do'] for r in rs)} of {len(rs)} asked rows offer {key}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("menus")
    sub.add_parser("run").add_argument("name")
    lp = sub.add_parser("label")
    lp.add_argument("pass_", choices=["A", "B"])
    lp.add_argument("--batch", type=int, default=BATCH)
    lp.add_argument("--effort", default="low")
    sub.add_parser("score").add_argument("name")
    sub.add_parser("traces").add_argument("directory")
    args = ap.parse_args()
    if args.mode == "menus":
        menus()
    elif args.mode == "run":
        asyncio.run(run(args.name))
    elif args.mode == "label":
        asyncio.run(label(args.pass_, args.batch, args.effort))
    elif args.mode == "score":
        score(args.name)
    else:
        traces(args.directory)


if __name__ == "__main__":
    main()
