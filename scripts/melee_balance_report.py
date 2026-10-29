#!/usr/bin/env python3
"""Summarize arena balance evidence from one or more body_sim_bench outputs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics


ROLES = ("primary", "opponent")
FINISH = ("T_1HSFINISH", "T_2HSFINISH")


def read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def journal(directory: Path, name: str) -> list[dict]:
    """Read the retained rotation before the current file, without double counting."""
    return read_rows(directory/(name+".previous.jsonl"))+read_rows(directory/(name+".jsonl"))


def ratio(a: float, b: float) -> float | None:
    return a/b if b else None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered)-1)*fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low]+(ordered[high]-ordered[low])*(position-low)


def who(value) -> str:
    return value.get("name", "") if isinstance(value, dict) else str(value or "")


def finish(animation: str) -> bool:
    return any(name in animation for name in FINISH)


def episode_metrics(folder: Path, episode: dict, shared: dict, variant: str) -> dict:
    unavailable = {}

    def missing(key: str, reason: str):
        unavailable[key] = reason
        return None

    arena_rows = read_rows(folder/"arena-events.jsonl")
    arenas = [row["arena"] for row in arena_rows if isinstance(row.get("arena"), dict)]
    terminal = next((row["arena"] for row in reversed(arena_rows) if row.get("kind") == "terminal"),
                    arenas[-1] if arenas else {})
    elapsed = episode.get("game_ms", episode.get("elapsed_game_ms", terminal.get("elapsed_game_ms")))
    duration = elapsed/1000 if isinstance(elapsed, (int, float)) else None
    start = terminal.get("start_game_ms")
    end = terminal.get("game_ms")
    profiles = terminal.get("actors", [])
    symbols = {row.get("symbol"): row.get("role") for row in profiles if row.get("role") in ROLES}
    # These are the fixed native arena instances, also used by older compact bench output.
    if not symbols:
        symbols = {"BDT_1003_BANDIT_M": "primary", "PAL_255_RITTER": "opponent"}
    roles = lambda value: symbols.get(who(value))
    events_path = folder/"events.jsonl"
    events = [row for row in read_rows(events_path) if row.get("kind") == "body_combat"]
    if start is not None and end is not None:
        events = [row for row in events if start <= row.get("t_game", -1) <= end]
    for order, row in enumerate(events):
        row["report_order"] = order
    seed = episode.get("seed", terminal.get("seed"))
    result = {"seed": seed, "side": episode.get("side", terminal.get("side")),
              "duration_s": duration, "reason": episode.get("reason", terminal.get("reason")),
              "arena_reason": episode.get("arena_reason", terminal.get("reason", episode.get("reason"))),
              "source": str(folder), "fighters": {}, "unavailable": unavailable}
    if duration is None:
        missing("duration_s", "No simulated episode duration in bench or arena journal")

    # A swing is one actual attack window, not a native decision, animation hook,
    # optimal-frame attempt or repeated snapshot. A combo's windows count separately.
    starts = {role: {} for role in ROLES}
    layer_rows = [row for row in events if row.get("phase") == "phase_snapshot" or
                  row.get("phase") == "native_attempt" and row.get("accepted") is True]
    layer_schema = any("is_attack" in layer for row in layer_rows
                       for layer in row.get("layers_after", row.get("layers", [])))
    for row in layer_rows:
        role = roles(row.get("actor"))
        if role is None:
            continue
        for layer in row.get("layers_after", row.get("layers", [])):
            if layer.get("is_attack") is not True:
                continue
            name = layer.get("name", "")
            optimal = len(set(layer.get("optimal_frames", [])))
            combo = min(layer.get("combo_length", 0), max(0, optimal-1))
            key = (name, layer.get("start"), combo)
            if key not in starts[role]:
                starts[role][key] = {"time": row["t_game"], "order": row["report_order"],
                                    "animation": name, "hit": False, "blocked": False}
    combat_available = events_path.is_file() and bool(events)
    swings_available = combat_available and layer_schema
    damages = [row for row in events if row.get("phase") == "damage" and row.get("damage_kind") == "melee"
               and row.get("has_hit") is True and row.get("hp_before", 0) > row.get("hp_after", 0)
               and roles(row.get("actor")) in ROLES and roles(row.get("target")) in ROLES]
    blocks = [row for row in events if row.get("phase") == "defence_result" and row.get("damage_kind") == "melee"
              and row.get("blocked") is True and roles(row.get("actor")) in ROLES]

    def mark(role: str | None, row: dict, flag: str):
        candidates = [value for value in starts.get(role, {}).values() if value["order"] <= row["report_order"]]
        if candidates:
            max(candidates, key=lambda value: value["order"])[flag] = True

    for row in damages:
        mark(roles(row.get("target")), row, "hit")
    for row in blocks:
        mark(roles(row.get("target")), row, "blocked")
    prone_schema = all("melee_knockdown_before" in row for row in damages)
    finish_schema = all("attacker_animation" in row for row in damages)
    for role in ROLES:
        swings = list(starts[role].values())
        hits = [row for row in damages if roles(row.get("target")) == role]
        damage = sum(row["hp_before"]-row["hp_after"] for row in hits)
        own = {"swings_started": len(swings), "damaging_hits": len(hits),
               "blocks": sum(roles(row.get("actor")) == role for row in blocks),
               "misses": sum(not value["hit"] and not value["blocked"] for value in swings),
               "damage": damage, "hit_fraction": ratio(len(hits), len(swings)),
               "damage_per_hit": ratio(damage, len(hits)), "damage_per_s": ratio(damage, duration or 0),
               "overhead_started": sum(finish(value["animation"]) for value in swings),
               "overhead_hits": sum(finish(row.get("attacker_animation", "")) for row in hits),
               "prone_hits": sum(row.get("melee_knockdown_before") is True for row in hits),
               "prone_damage": sum(row["hp_before"]-row["hp_after"] for row in hits
                                   if row.get("melee_knockdown_before") is True)}
        if not combat_available:
            for key in own:
                own[key] = missing(f"fighters.{role}.{key}", "Combat event journal missing or body trace disabled")
        else:
            if not swings_available:
                for key in ("swings_started", "misses", "hit_fraction", "overhead_started"):
                    own[key] = missing(f"fighters.{role}.{key}", "Attack-layer is_attack field absent from journal")
            if not finish_schema:
                own["overhead_hits"] = missing(f"fighters.{role}.overhead_hits", "Attacker animation absent from damage journal")
            if variant == "vanilla":
                for key in ("prone_hits", "prone_damage"):
                    own[key] = missing(f"fighters.{role}.{key}", "melee_posture is disabled; live knockdown state is not measured")
            elif not prone_schema:
                for key in ("prone_hits", "prone_damage"):
                    own[key] = missing(f"fighters.{role}.{key}", "Pre-damage knockdown state absent from damage journal")
        for key in ("hit_fraction", "damage_per_hit", "damage_per_s"):
            if own[key] is None and f"fighters.{role}.{key}" not in unavailable:
                missing(f"fighters.{role}.{key}", "Zero denominator")
        result["fighters"][role] = own

    def rows_for(name):
        local = journal(folder, name)
        # A final in-flight window may flush on the next reset, after the local
        # copy was saved. Conversely, the shared journal can rotate older duels.
        rows = [row for row in local+shared[name] if row.get("arena_seed") == seed]
        unique = {}
        for row in rows:
            key = ((who(row.get("attacker")), row.get("swing"), row.get("game_time_ms"),
                    row.get("window", {}).get("combo_index")) if name == "melee-blade-shadow"
                   else json.dumps(row, sort_keys=True))
            unique[key] = row
        return sorted(unique.values(), key=lambda row: row.get("tick_ms", row.get("game_time_ms", 0)))

    posture = rows_for("melee-posture")
    blade = rows_for("melee-blade-shadow")
    if start is not None and end is not None:
        posture = [row for row in posture if start <= row.get("tick_ms", -1) <= end]
        blade = [row for row in blade if start <= row.get("game_time_ms", -1) <= end]
    falls = [row for row in posture if row.get("event") == "knockdown" and roles(row.get("target")) in ROLES]
    result["observed_knockdown_rows"] = len(falls)
    result["knockdowns"] = len(falls)
    result["repeat_knockdowns_within_10s_after_getup"] = 0
    result["first_knocked_down_lost"] = None
    for role in ROLES:
        own = result["fighters"][role]
        own["knockdowns"] = sum(roles(row.get("target")) == role for row in falls)
        recovering = None
        down_ms = 0
        recovered = None
        repeat = 0
        for row in posture:
            if roles(row.get("target")) != role:
                continue
            if row.get("event") == "knockdown":
                recovering = row["tick_ms"]
                if recovered is not None and 0 <= row["tick_ms"]-recovered <= 10000:
                    repeat += 1
            elif row.get("event") in ("recover", "cancel"):
                if recovering is not None:
                    down_ms += max(0, row["tick_ms"]-recovering)
                    recovering = None
                recovered = row["tick_ms"]
        if recovering is not None and end is not None:
            down_ms += max(0, end-recovering)
        own["down_time_s"] = down_ms/1000
        own["down_time_fraction"] = ratio(down_ms/1000, duration or 0)
        if own["down_time_fraction"] is None:
            missing(f"fighters.{role}.down_time_fraction", "Unavailable duration or zero denominator")
        own["repeat_knockdowns_within_10s_after_getup"] = repeat
        result["repeat_knockdowns_within_10s_after_getup"] += repeat
    posture_enabled = variant in ("posture-on",)
    # Presence of seed-keyed posture rows also permits reporting custom/older variants.
    posture_available = variant != "vanilla" and (posture_enabled or bool(posture))
    if not posture_available or (posture_enabled and not posture and (damages or not combat_available)):
        reason = ("melee_posture is disabled; no knockdown telemetry in this variant" if not posture_available
                  else "No seed-matched posture journal and insufficient combat evidence (missing/rotated journal)")
        for key in ("knockdowns", "repeat_knockdowns_within_10s_after_getup", "first_knocked_down_lost"):
            result[key] = missing(key, reason)
        for role in ROLES:
            for key in ("knockdowns", "down_time_s", "down_time_fraction", "repeat_knockdowns_within_10s_after_getup"):
                result["fighters"][role][key] = missing(f"fighters.{role}.{key}", reason)
    elif falls:
        first = min(falls, key=lambda row: row["tick_ms"])
        losers = {"primary_down": "primary", "opponent_down": "opponent"}
        loser = losers.get(result["arena_reason"])
        if loser is not None:
            result["first_knocked_down_lost"] = int(roles(first["target"]) == loser)
        else:
            missing("first_knocked_down_lost", "Duel has no winner (timeout or other termination)")
    else:
        missing("first_knocked_down_lost", "No knockdown in this duel")
    result["wall_stops"] = sum(row.get("decision", {}).get("outcome") == "wall" for row in blade)
    if variant == "vanilla":
        result["wall_stops"] = missing("wall_stops", "melee_blade_shadow and melee_physics_hits are disabled")
    elif not blade:
        result["wall_stops"] = missing("wall_stops", "No seed-matched blade journal (missing/rotated journal or no swings)")
    elif not swings_available or any(sum(roles(row.get("attacker")) == role for row in blade) != len(starts[role]) for role in ROLES):
        result["wall_stops"] = missing("wall_stops", "Blade windows do not cover all observed attack starts (open terminal window or missing/rotated rows)")
    return result


def aggregate(variant: str, provider: str, weapon: str, episodes: list[dict]) -> dict:
    unavailable = {}

    def total(values, key):
        if any(value is None for value in values):
            unavailable[key] = "One or more episode values are unavailable; see episodes[].unavailable"
            return None
        return sum(values)

    def divide(numerator, denominator, key):
        if numerator is None or denominator is None or not denominator:
            unavailable[key] = "Unavailable numerator/denominator or zero denominator"
            return None
        return numerator/denominator

    count = len(episodes)
    durations = [row["duration_s"] for row in episodes]
    timed = all(value is not None for value in durations)
    seconds = sum(durations) if timed else None
    outcomes = Counter(row["reason"] for row in episodes)
    result = {"variant": variant, "provider": provider, "weapon": weapon, "duels": count,
              "wins": outcomes["opponent_down"], "losses": outcomes["primary_down"],
              "timeouts": outcomes["time_limit"], "other_outcomes": count-sum(outcomes[name] for name in
                  ("opponent_down", "primary_down", "time_limit")),
              "duration_s": {"mean": statistics.mean(durations) if timed else None,
                             "median": statistics.median(durations) if timed else None,
                             "p90": percentile(durations, .9) if timed else None},
              "fighters": {}, "unavailable": unavailable, "episodes": episodes}
    if not timed:
        for key in result["duration_s"]:
            unavailable["duration_s."+key] = "One or more episode durations are unavailable"
    counts = ("swings_started", "damaging_hits", "blocks", "misses", "damage", "overhead_started", "overhead_hits",
              "prone_hits", "prone_damage", "knockdowns", "repeat_knockdowns_within_10s_after_getup", "down_time_s")
    for role in ROLES:
        own = {key: total([row["fighters"][role][key] for row in episodes], f"fighters.{role}.{key}") for key in counts}
        own["per_duel"] = {key: divide(own[key], count, f"fighters.{role}.per_duel.{key}") for key in counts}
        for key, numerator, denominator in (("hit_fraction", own["damaging_hits"], own["swings_started"]),
                ("damage_per_hit", own["damage"], own["damaging_hits"]), ("damage_per_s", own["damage"], seconds),
                ("prone_damage_fraction", own["prone_damage"], own["damage"]),
                ("down_time_fraction", own["down_time_s"], seconds)):
            own[key] = divide(numerator, denominator, f"fighters.{role}.{key}")
        result["fighters"][role] = own
    combined = {key: total([result["fighters"][role][key] for role in ROLES], "combat."+key) for key in counts}
    combined["per_duel"] = {key: divide(combined[key], count, "combat.per_duel."+key) for key in counts}
    for key, numerator, denominator in (("hit_fraction", combined["damaging_hits"], combined["swings_started"]),
            ("damage_per_hit", combined["damage"], combined["damaging_hits"]),
            ("damage_per_s", combined["damage"], seconds),
            ("prone_damage_fraction", combined["prone_damage"], combined["damage"])):
        combined[key] = divide(numerator, denominator, "combat."+key)
    result["combat"] = combined
    knockdowns = [row["knockdowns"] for row in episodes]
    histogram = {str(bucket) if bucket < 3 else "3+":
                 sum(min(value, 3) == bucket for value in knockdowns)/count
                 if all(value is not None for value in knockdowns) else None for bucket in range(4)}
    for key, value in histogram.items():
        if value is None:
            unavailable["knockdown_duel_fraction."+key] = "Knockdown telemetry unavailable in one or more episodes"
    result["knockdown_duel_fraction"] = histogram
    first_lost = [row["first_knocked_down_lost"] for row in episodes if row["first_knocked_down_lost"] is not None]
    result["first_knocked_down_lost_eligible_duels"] = len(first_lost)
    if any(row["knockdowns"] is None for row in episodes):
        result["first_knocked_down_lost_fraction"] = None
        unavailable["first_knocked_down_lost_fraction"] = "Knockdown telemetry unavailable in one or more episodes"
    else:
        result["first_knocked_down_lost_fraction"] = divide(sum(first_lost), len(first_lost), "first_knocked_down_lost_fraction")
    result["wall_stops"] = total([row["wall_stops"] for row in episodes], "wall_stops")
    result["wall_stops_per_duel"] = divide(result["wall_stops"], count, "wall_stops_per_duel")
    result["observed_knockdown_rows"] = sum(row["observed_knockdown_rows"] for row in episodes)
    return result


def load_series(folder: Path):
    bench = json.loads((folder/"bench.json").read_text())
    for result in bench["results"]:
        base = folder/result["name"]
        weapon = result.get("weapon", bench.get("weapon", "1h"))
        for provider, entries, directory in (("native", result.get("episodes", []), base),
                ("P30", result.get("policy", {}).get("episodes", []), base/"policy/worker-00")):
            if not entries:
                continue
            shared = {name: journal(directory/"run", name) for name in ("melee-posture", "melee-blade-shadow")}
            episodes = []
            for index, episode in enumerate(entries):
                current = directory/(episode["name"] if provider == "P30" else f"episode-{index}")
                episodes.append(episode_metrics(current, episode, shared, result["name"]))
            yield (result["name"], provider, weapon), episodes


def markdown(series: list[dict]) -> str:
    def fmt(value):
        return "null" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)

    lines = ["# Melee balance measurements", "", "Measurements only; no balance conclusions.", "",
             "| Variant | Controller | Weapon | Duels | W/L/T | Duration mean / median / p90 (s) | KD/duel | Wall/duel |",
             "|---|---|---|---:|---|---|---:|---:|"]
    for row in series:
        durations = " / ".join(fmt(row["duration_s"][key]) for key in ("mean", "median", "p90"))
        lines.append(f"| {row['variant']} | {row['provider']} | {row['weapon']} | {row['duels']} | "
                     f"{row['wins']}/{row['losses']}/{row['timeouts']} | {durations} | "
                     f"{fmt(row['combat']['per_duel']['knockdowns'])} | {fmt(row['wall_stops_per_duel'])} |")
    for row in series:
        lines.extend(["", f"## {row['variant']} / {row['provider']} / {row['weapon']}", "",
                      "| Metric | Total | Both / duel | Primary / duel | Opponent / duel |", "|---|---:|---:|---:|---:|"])
        for key in ("swings_started", "damaging_hits", "blocks", "misses", "damage", "knockdowns",
                    "repeat_knockdowns_within_10s_after_getup", "prone_hits", "prone_damage", "overhead_started", "overhead_hits"):
            lines.append(f"| {key} | {fmt(row['combat'][key])} | {fmt(row['combat']['per_duel'][key])} | "
                         f"{fmt(row['fighters']['primary']['per_duel'][key])} | {fmt(row['fighters']['opponent']['per_duel'][key])} |")
        for key in ("hit_fraction", "damage_per_hit", "damage_per_s", "prone_damage_fraction"):
            lines.append(f"| {key} | — | {fmt(row['combat'][key])} | {fmt(row['fighters']['primary'][key])} | "
                         f"{fmt(row['fighters']['opponent'][key])} |")
        lines.append(f"| down_time_fraction | — | — | {fmt(row['fighters']['primary']['down_time_fraction'])} | "
                     f"{fmt(row['fighters']['opponent']['down_time_fraction'])} |")
        lines.extend(["", "| KD in duel | 0 | 1 | 2 | 3+ |", "|---|---:|---:|---:|---:|",
                      "| Duel fraction | "+" | ".join(fmt(row["knockdown_duel_fraction"][key]) for key in ("0", "1", "2", "3+"))+" |",
                      "", f"First knocked-down fighter lost: {fmt(row['first_knocked_down_lost_fraction'])} "
                      f"({row['first_knocked_down_lost_eligible_duels']} decisive duels with knockdowns).",
                      f"Observed knockdown journal rows: {row['observed_knockdown_rows']}."])
        reasons = sorted({reason for episode in row["episodes"] for reason in episode["unavailable"].values()})
        if reasons:
            lines.extend(["", "Unavailable measurements:", ""]+[f"- {reason}." for reason in reasons])
    return "\n".join(lines)+"\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folders", type=Path, nargs="+", help="body_sim_bench output directories containing bench.json")
    parser.add_argument("--output", type=Path, help="destination directory (default: first input directory)")
    args = parser.parse_args()
    grouped = defaultdict(list)
    for folder in args.folders:
        for key, episodes in load_series(folder.resolve()):
            grouped[key].extend(episodes)
    if not grouped:
        parser.error("No native or P30 episodes in the input bench files")
    series = [aggregate(*key, episodes) for key, episodes in grouped.items()]
    output = (args.output or args.folders[0]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "kind": "melee_balance_report", "inputs": [str(path.resolve()) for path in args.folders],
              "definitions": {
                  "swing": "Unique actor/attack-layer name/start/combo window observed in actual accepted hooks or snapshots",
                  "miss": "Started swing with no damaging hit and no opponent block before episode end; includes interrupted/evaded/wall swings",
                  "damage": "Actual positive HP loss; damage events name victim as actor and attacker as target",
                  "blocks": "Successful blocks performed by this fighter, including repeated contacts if journaled",
                  "down_time": "Knockdown until recover/cancel or duel end, including the get-up transition",
                  "first_knocked_down_lost_fraction": "Decisive duels with at least one knockdown; timeouts excluded",
                  "p90": "Linear interpolation at index (n-1)*0.9",
                  "aggregation": "Counts sum all supplied episodes; ratios divide summed counts/durations, not means of ratios",
                  "null": "Unavailable evidence or zero denominator; reasons in unavailable maps"}, "series": series}
    (output/"balance.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    table = markdown(series)
    (output/"balance.md").write_text(table)
    print(table, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
