"""End-to-end check of the npc planner against the real decider service, without the game.

A fake island: 8 npc's near the player, 30 a bit further, 250 far away. Near ones notice the player
(events) and run out of actions (idle) every few seconds; the planner decides who to ask and when.

    cd evals/npc-decisions
    uv run --project ../../server python planner_sim.py [--url ws://<gpu-host>:47832] [--seconds 60]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import time

from gothic_ai.brain import DeciderClient, LogApplier, NpcPlanner
from gothic_ai.brain.situation import SituationBuilder
from gothic_ai.protocol import RosterEntry, RoutineEntry, RoutineInfo

SAYINGS = ["Привет!", "Где тут рынок?", "Хорошая погода сегодня.", "Ты не видел Ли?", "Продай мне что-нибудь."]
NEAR = [  # name, guild, traits, agenda
    ("Pepe", 8, "A young, timid shepherd.", "get help against the wolves that take your sheep every night"),
    ("Zuris", 3, "A pushy potion seller.", "sell the hero healing potions"),
    ("Pablo", 2, "A suspicious militiaman.", "find out who stole the merchant's purse this morning"),
    ("Edda", 3, "A poor, kind woman who cooks fish soup.", None),
    ("Harad", 3, "The city smith, proud and busy.", None),
    ("Gritta", 3, "A gossiping young woman.", None),
    ("Bosper", 3, "A sharp-tongued bowyer.", None),
    ("Ignaz", 3, "An odd alchemist who talks to himself.", None),
]
EVENTS = [("player_near", None), ("player_speech", None), ("item_dropped", "an apple"), ("player_sits", None)]
# everyone's day, as the game tells it: without a routine there is no plan (FAR plans load the decider)
DAY = [
    RoutineEntry(start="06:00", end="18:00", state="ZS_PICK_FP", waypoint="FIELD_01"),
    RoutineEntry(start="18:00", end="22:00", state="ZS_STAND_EATING", waypoint="FARM_YARD"),
    RoutineEntry(start="22:00", end="06:00", state="ZS_GOTOBED", waypoint="FARM_BED"),
]


async def routine_of(npc: str) -> RoutineInfo:
    return RoutineInfo(type="routine", npc=npc, current="ZS_PICK_FP", entries=DAY)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("GOTHIC_DECIDER_URL"), required="GOTHIC_DECIDER_URL" not in os.environ)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--event-every", type=float, default=4.0, help="mean seconds between events per near npc")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    rnd = random.Random(3)
    roster = (
        [RosterEntry(id=f"NEAR_{i}", name=NEAR[i][0], guild=NEAR[i][1], distance=rnd.uniform(300, 1500),
                     in_view=i < 4) for i in range(8)]
        + [RosterEntry(id=f"MID_{i}", name=f"Mid {i}", guild=2, distance=rnd.uniform(2100, 2800)) for i in range(30)]
        + [RosterEntry(id=f"FAR_{i}", name=f"Far {i}", guild=8, distance=rnd.uniform(4000, 20000)) for i in range(250)]
    )
    client = DeciderClient(args.url)
    applier = LogApplier(keep=100_000)
    builder = SituationBuilder(traits={f"NEAR_{i}": n[2] for i, n in enumerate(NEAR)})
    planner = NpcPlanner(client, applier, builder, routine_of=routine_of)
    planner.game_time = (14, 0)
    runner = asyncio.create_task(planner.run())
    planner.on_roster(roster, time.monotonic())
    for i, n in enumerate(NEAR):
        planner.set_agenda(f"NEAR_{i}", n[3])

    sent_at: dict[str, float] = {}
    stop = time.monotonic() + args.seconds

    async def npc_life(npc: str) -> None:
        while time.monotonic() < stop:
            await asyncio.sleep(rnd.expovariate(1 / args.event_every))
            now = time.monotonic()
            if rnd.random() < 0.3:
                planner.on_idle(npc, now)
                continue
            kind, detail = rnd.choice(EVENTS)
            if kind == "player_speech":
                detail = rnd.choice(SAYINGS)
                sent_at[npc] = now
            planner.on_event(npc, kind, now, distance_cm=400, detail=detail)

    async def roster_feed() -> None:
        while time.monotonic() < stop:
            await asyncio.sleep(0.5)
            planner.on_roster(roster, time.monotonic())

    await asyncio.gather(*(npc_life(f"NEAR_{i}") for i in range(8)), roster_feed())
    await asyncio.sleep(1)
    snap = planner.snapshot()
    runner.cancel()
    await client.aclose()

    print(snap)
    total_sent = sum(snap["sent"].values())
    print(f"requests: {total_sent} in {args.seconds:.0f} s = {total_sent / args.seconds:.1f}/s")
    for label, pick in (
        ("urgent (player spoke)", lambda d: d.question == "react" and "player_speech" in d.stimuli),
        ("near, other events", lambda d: d.question == "react" and d.stimuli and "player_speech" not in d.stimuli),
        ("timers / idle", lambda d: d.question == "react" and not d.stimuli),
        ("plans (far)", lambda d: d.question == "plan"),
    ):
        lat = sorted(d.latency_ms for d in applier.recent if pick(d))
        if lat:
            print(f"  {label:24s} n={len(lat):4d}  p50 {lat[len(lat) // 2]:6.1f} ms  p95 {lat[int(len(lat) * 0.95)]:6.1f} ms"
                  f"  max {lat[-1]:6.1f} ms")
    choices: dict[str, int] = {}
    for d in applier.recent:
        if d.question in ("react", "plan"):
            choices[d.choice] = choices.get(d.choice, 0) + 1
    print("applied choices:", dict(sorted(choices.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    asyncio.run(main())
