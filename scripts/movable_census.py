#!/usr/bin/env python3
"""Reads movable-census bench runs: what each movable vob of a map touches and how far it moves when woken.

    python3 scripts/movable_census.py .bench/movable-census-NEWWORLD ... [--depth 4] [--csv out.csv]

Each directory holds movable-clamps.jsonl and movable-drift.jsonl of one world (scripts/bench/movable-census.bench).
A movable vob is sorted into the first group that applies:

  hangs       nothing below it within 1 cm
  grown into  some contact penetrates deeper than --depth cm (wall, floor, structure or another movable),
              except small things standing on its top
  carries     a static vob larger than 80 cm rests on its top (planks on barrels, a crate pile, a cauldron)
  item        a small static vob (<= 80 cm) rests on its top (candles)
  ok          the rest
"""
import argparse
import collections
import csv
import json
import statistics
from pathlib import Path

SMALL_CM = 80.0
GROUPS = ["hangs", "wall", "floor", "structure", "neighbour", "carries", "item", "ok"]


def name(contact):
    return contact["other"].rsplit(".", 1)[0].upper()


def size(contact):
    return max(contact["other_max_cm"][i] - contact["other_min_cm"][i] for i in range(3))


def on_top(body, contact):
    low, high = body["hull_min_cm"][1], body["hull_max_cm"][1]
    return (contact["category"] == 4 and contact["side"] != 0 and
            (contact["other_min_cm"][1] - low) / max(high - low, 1e-3) >= 0.9)


def decide(body, depth):
    contacts = body["contacts"]
    if not any(c["side"] == 0 for c in contacts):
        return "hangs", None
    deep = [c for c in contacts if c["depth_cm"] > depth and not (on_top(body, c) and size(c) <= SMALL_CM)]
    if deep:
        c = max(deep, key=lambda c: c["depth_cm"])
        group = ("neighbour" if c["category"] == 10 else "floor" if c["side"] == 0 else
                 "wall" if c["category"] == 2 else "structure")
        return group, c
    for c in contacts:
        if on_top(body, c) and size(c) > SMALL_CM:
            return "carries", c
    for c in contacts:
        if on_top(body, c):
            return "item", c
    return "ok", None


def load(path, file):
    lines = [json.loads(line) for line in (Path(path) / file).read_text().splitlines() if line.strip()]
    return lines


def table(title, keys, counts):
    print(f"\n| {title} | total | " + " | ".join(GROUPS) + " |")
    print("|---|---:|" + "---:|" * len(GROUPS))
    for key in keys:
        print(f"| {key} | {sum(counts[key].values())} | " + " | ".join(str(counts[key][g]) for g in GROUPS) + " |")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--depth", type=float, default=4.0, help="penetration (cm) that counts as grown into")
    parser.add_argument("--csv")
    args = parser.parse_args()

    bodies, shift = [], {}
    for run in args.runs:
        bodies += load(run, "movable-clamps.jsonl")
        drift = load(run, "movable-drift.jsonl")
        for row in drift:  # the last checkpoint wins
            shift[(row["world"], row["zen_id"])] = row["shift_cm"]

    by_world, by_class = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    moved, causes, curve, rows = collections.defaultdict(list), collections.defaultdict(collections.Counter), \
        collections.defaultdict(list), []
    edges = [-1e9, 1, 2, 3, 4, 5, 7, 10, 20, 1e9]
    for body in bodies:
        group, contact = decide(body, args.depth)
        key = (body["world"], body["zen_id"])
        by_world[body["world"]][group] += 1
        by_world["all"][group] += 1
        by_class[body["class"]][group] += 1
        if contact is not None:
            causes[group][name(contact)] += 1
        s = shift.get(key)
        if s is not None:
            moved[group].append(s)
            if any(c["side"] == 0 for c in body["contacts"]):
                deepest = max(c["depth_cm"] for c in body["contacts"])
                band = next(i for i in range(len(edges) - 1) if edges[i] <= deepest < edges[i + 1])
                curve[band].append(s)
        rows.append({"world": body["world"], "zen_id": body["zen_id"], "visual": body["visual"],
                     "class": body["class"], "group": group, "by": name(contact) if contact else "",
                     "depth_cm": round(contact["depth_cm"], 1) if contact else "",
                     "wake_shift_cm": round(s, 1) if s is not None else "",
                     "x": round(body["position_cm"][0]), "y": round(body["position_cm"][1]),
                     "z": round(body["position_cm"][2])})

    worlds = sorted(w for w in by_world if w != "all") + ["all"]
    print(f"depth threshold {args.depth:g} cm")
    table("map", worlds, by_world)
    table("class", sorted(by_class), by_class)

    if moved:
        print("\n| group | n | median shift, cm | p90 | max | >= 10 cm |")
        print("|---|---:|---:|---:|---:|---:|")
        for group in GROUPS:
            v = sorted(moved[group])
            if v:
                print(f"| {group} | {len(v)} | {statistics.median(v):.1f} | {v[int(0.9 * (len(v) - 1))]:.1f} | "
                      f"{v[-1]:.1f} | {sum(x >= 10 for x in v)} |")
        print("\n| deepest contact, cm | n | median shift, cm | p90 | >= 2 cm | >= 10 cm |")
        print("|---|---:|---:|---:|---:|---:|")
        for band in sorted(curve):
            v = sorted(curve[band])
            low, high = edges[band], edges[band + 1]
            label = f"< {high:g}" if low < -1e8 else f">= {low:g}" if high > 1e8 else f"{low:g}-{high:g}"
            print(f"| {label} | {len(v)} | {statistics.median(v):.1f} | {v[int(0.9 * (len(v) - 1))]:.1f} | "
                  f"{sum(x >= 2 for x in v) / len(v):.0%} | {sum(x >= 10 for x in v) / len(v):.0%} |")

    for group in GROUPS:
        if causes[group]:
            print(f"\n{group}: " + ", ".join(f"{k} {n}" for k, n in causes[group].most_common(10)))

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(sorted(rows, key=lambda r: (r["world"], r["group"], r["zen_id"])))


if __name__ == "__main__":
    main()
