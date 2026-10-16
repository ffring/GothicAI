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
MOVABLE_GROUPS = {"item", "ok"}


def body_key(body):
    return body["world"], body["zen_id"]


def name(contact):
    return contact["other"].rsplit(".", 1)[0].upper()


def size(contact):
    return max(contact["other_max_cm"][i] - contact["other_min_cm"][i] for i in range(3))


def on_top(body, contact):
    low, high = body["hull_min_cm"][1], body["hull_max_cm"][1]
    return (contact["category"] == 4 and contact["side"] != 0 and
            (contact["other_min_cm"][1] - low) / max(high - low, 1e-3) >= 0.9)


def decide(body, depth, deep_movable_pairs=None):
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
    # The overlap query may report a deep movable pair in only one direction.
    # Both endpoints must stay static, preserving the higher-priority own reasons.
    if deep_movable_pairs and body_key(body) in deep_movable_pairs:
        return "neighbour", deep_movable_pairs[body_key(body)]
    for c in contacts:
        if on_top(body, c):
            return "item", c
    return "ok", None


def decide_all(bodies, depth):
    """Classify a complete census, including both endpoints of deep movable pairs."""
    indexed = {body_key(body): body for body in bodies}
    pairs = {}

    def remember(key, contact):
        if key not in pairs or contact["depth_cm"] > pairs[key]["depth_cm"]:
            pairs[key] = contact

    for body in bodies:
        for contact in body["contacts"]:
            if contact["category"] != 10 or contact["depth_cm"] <= depth:
                continue
            remember(body_key(body), contact)
            other_key = body["world"], contact.get("other_zen_id")
            if other_key not in indexed:
                continue  # Old census files have no other_zen_id.
            reverse = dict(contact, other=body["visual"], other_zen_id=body["zen_id"],
                           other_min_cm=body["hull_min_cm"], other_max_cm=body["hull_max_cm"])
            remember(other_key, reverse)

    return {body_key(body): decide(body, depth, pairs) for body in bodies}


def check_engine_decision(body, group):
    """Reject disagreements with the engine while accepting historical census files."""
    expected = {"disposition": group, "enabled": group in MOVABLE_GROUPS}
    for field, value in expected.items():
        if field in body and body[field] != value:
            raise ValueError(f"{body['world']} vob {body['zen_id']}: engine {field}={body[field]!r}, "
                             f"reader expected {value!r}")


def is_candle(body):
    return body["visual"].rsplit(".", 1)[0].upper() == "NW_CITY_CANDLE_01"


def contact_notes(body):
    contacts = sorted(body["contacts"], key=lambda c: c["depth_cm"], reverse=True)
    return "; ".join(f"{name(c)} {c['depth_cm']:.2f} cm, side {c['side']}, category {c['category']}"
                     for c in contacts) or "no contacts"


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
            shift[(row["world"], row["zen_id"])] = row.get("max_shift_cm", row["shift_cm"])

    decisions = decide_all(bodies, args.depth)
    by_world, by_class = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    candles = collections.defaultdict(collections.Counter)
    enabled_shifts = collections.defaultdict(list)
    large_shifts = []
    moved, causes, curve, rows = collections.defaultdict(list), collections.defaultdict(collections.Counter), \
        collections.defaultdict(list), []
    edges = [-1e9, 1, 2, 3, 4, 5, 7, 10, 20, 1e9]
    for body in bodies:
        key = body_key(body)
        group, contact = decisions[key]
        check_engine_decision(body, group)
        enabled = group in MOVABLE_GROUPS
        by_world[body["world"]][group] += 1
        by_world["all"][group] += 1
        by_class[body["class"]][group] += 1
        if is_candle(body):
            candles[body["world"]][group] += 1
            candles["all"][group] += 1
        if contact is not None:
            causes[group][name(contact)] += 1
        s = shift.get(key)
        if s is not None:
            moved[group].append(s)
            if enabled:
                enabled_shifts[body["world"]].append(s)
                enabled_shifts["all"].append(s)
                if s > 10:
                    large_shifts.append((body, s))
            if any(c["side"] == 0 for c in body["contacts"]):
                deepest = max(c["depth_cm"] for c in body["contacts"])
                band = next(i for i in range(len(edges) - 1) if edges[i] <= deepest < edges[i + 1])
                curve[band].append(s)
        rows.append({"world": body["world"], "zen_id": body["zen_id"], "visual": body["visual"],
                     "class": body["class"], "group": group, "enabled": enabled, "candle": is_candle(body),
                     "by": name(contact) if contact else "",
                     "depth_cm": round(contact["depth_cm"], 1) if contact else "",
                     "wake_shift_cm": round(s, 1) if s is not None else "",
                     "x": round(body["position_cm"][0]), "y": round(body["position_cm"][1]),
                     "z": round(body["position_cm"][2])})

    worlds = sorted(w for w in by_world if w != "all") + ["all"]
    print(f"depth threshold {args.depth:g} cm")
    table("map", worlds, by_world)
    table("class", sorted(by_class), by_class)
    table("candles by map", worlds, candles)

    print("\n| map | total | static | movable | candles static | candles movable |")
    print("|---|---:|---:|---:|---:|---:|")
    for world in worlds:
        total = sum(by_world[world].values())
        enabled = sum(by_world[world][g] for g in MOVABLE_GROUPS)
        candle_enabled = sum(candles[world][g] for g in MOVABLE_GROUPS)
        print(f"| {world} | {total} | {total - enabled} | {enabled} | "
              f"{sum(candles[world].values()) - candle_enabled} | {candle_enabled} |")

    print("\n| wake: remaining movable | movable | observed | < 5 cm | max, cm | > 30 cm |")
    print("|---|---:|---:|---:|---:|---:|")
    for world in worlds:
        enabled = sum(by_world[world][g] for g in MOVABLE_GROUPS)
        values = enabled_shifts[world]
        below5 = f"{sum(s < 5 for s in values) / len(values):.2%}" if values else "—"
        maximum = f"{max(values):.2f}" if values else "—"
        print(f"| {world} | {enabled} | {len(values)} | {below5} | {maximum} | "
              f"{sum(s > 30 for s in values)} |")

    if large_shifts:
        print("\nRemaining movable vobs shifted > 10 cm (contacts at the original pose):")
        print("\n| map | zen_id | visual | shift, cm | contacts |")
        print("|---|---:|---|---:|---|")
        for body, value in sorted(large_shifts, key=lambda row: (row[0]["world"], -row[1])):
            print(f"| {body['world']} | {body['zen_id']} | {body['visual']} | {value:.2f} | {contact_notes(body)} |")

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
            if rows:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(sorted(rows, key=lambda r: (r["world"], r["group"], r["zen_id"])))


if __name__ == "__main__":
    main()
