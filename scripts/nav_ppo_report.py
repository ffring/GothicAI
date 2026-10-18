#!/usr/bin/env python3
"""Render final navigation PPO curves and paired, recorded test trajectories.

--run is the nav_ppo_loop.py work directory (curves.jsonl); --baseline and
--final are nav_rl.py drive directories already summarized to metrics.json.
Only final report artifacts are written, never weights or model inferences.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics


HEADS = ("forward", "strafe", "turn", "walk", "jump", "climb")
REGIONS = ("market", "harbour", "onar", "lobart", "port_streets", "upper_streets")
COMPONENT_NAMES = {
    "progress_cm": "Detour progress", "arrived": "Arrival", "blocked": "Blocked",
    "fall_damage": "Fall damage", "death": "Death", "jump": "Jump", "climb": "Climb", "time": "Time",
}
COLORS = ("#0072b2", "#009e73", "#d55e00", "#cc79a7", "#8c564b", "#e69f00", "#56b4e9", "#555555")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_curves(run):
    path = run / "curves.jsonl"
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            training = row["training"]
            values = [row["iteration"], row["decisions"]]
            if training is not None:
                values.extend([training["mean_reward"], training["kl_reference"],
                               training["turn_in_place_fraction"], training["blocked_fraction"],
                               *(training["entropy_by_head"][head] for head in HEADS),
                               *training["reward_components"].values(), *training["reward_component_abs_fraction"].values()])
            elif not row.get("stop_reason"):
                raise ValueError("An iteration without an update must state its stop reason")
            if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
                raise ValueError("Non-finite or nonnumeric metric")
            if rows and (row["iteration"] <= rows[-1]["iteration"] or row["decisions"] < rows[-1]["decisions"]
                         or training is not None and row["decisions"] == rows[-1]["decisions"]):
                raise ValueError("Iterations must increase and cumulative decisions cannot decrease")
            if row.get("validation") is not None:
                validation = row["validation"]
                if not 0 <= validation["arrived"] <= validation["n"] or validation["n"] <= 0:
                    raise ValueError("Invalid validation episode counts")
            rows.append(row)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid PPO history at {path}:{number}: {exc}") from exc
    if not rows:
        raise ValueError(f"No completed PPO iterations in {path}")
    return rows


def save_figure(fig, destination):
    paths = []
    for suffix in ("svg", "png"):
        path = destination.with_suffix("." + suffix)
        fig.savefig(path, dpi=160, bbox_inches="tight")
        paths.append(str(path))
    return paths


def plot_curves(run, rows, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(4, 2, figsize=(13, 15), sharex=True)
    arrival, reward, components, shares, kl, entropy, turn, blocked = axes.flat
    trained = [row for row in rows if row["training"] is not None]
    iterations = [row["iteration"] for row in trained]
    training = [row["training"] for row in trained]
    stopped = [row for row in rows if row.get("stop_reason")]
    evaluated = [row for row in rows if row.get("validation") is not None]
    baseline_path = run / "baseline" / "validation" / "metrics.json"
    baseline = read_json(baseline_path)["all"] if baseline_path.is_file() else None
    if baseline:
        arrival.axhline(100 * baseline["arrival_fraction"], color="#555555", ls="--", lw=1,
                        label=f"Round 2 validation: {baseline['arrived']}/{baseline['n']}")
    if evaluated:
        arrival.plot([row["iteration"] for row in evaluated],
                     [100 * row["validation"]["arrival_fraction"] for row in evaluated],
                     "o-", color=COLORS[0], ms=4, label="Greedy validation")
        for row in evaluated:
            validation = row["validation"]
            arrival.annotate(f"{validation['arrived']}/{validation['n']}",
                             (row["iteration"], 100 * validation["arrival_fraction"]),
                             xytext=(0, 5), textcoords="offset points", ha="center", fontsize=6)
    else:
        arrival.text(.5, .5, "No post-update validation", transform=arrival.transAxes, ha="center")
    arrival.set(title="Independent validation · argmax", ylabel="Arrived, %", ylim=(0, 105))
    reward.plot(iterations, [value["mean_reward"] for value in training], color=COLORS[0])
    reward.axhline(0, color="#777777", lw=.7)
    reward.set(title="Population collection · sample", ylabel="Mean reward / valid decision")

    component_keys = list(dict.fromkeys(key for value in training for key in value["reward_components"]))
    for index, key in enumerate(component_keys):
        color = COLORS[index % len(COLORS)]
        label = COMPONENT_NAMES.get(key, key)
        components.plot(iterations, [value["reward_components"].get(key, 0) for value in training],
                        color=color, lw=1.2, label=label)
        shares.plot(iterations, [100 * value["reward_component_abs_fraction"].get(key, 0) for value in training],
                    color=color, lw=1.2, label=label)
    components.axhline(0, color="#777777", lw=.7)
    components.set(title="Signed weighted reward components", ylabel="Reward / valid decision")
    shares.set(title="Absolute component contribution shares", ylabel="Share of absolute contributions, %", ylim=(0, 105))
    kl.plot(iterations, [value["kl_reference"] for value in training], color=COLORS[2], label="KL to round 2")
    kl.set(title="Actor drift from round 2", ylabel="KL, nats / decision")
    for index, head in enumerate(HEADS):
        entropy.plot(iterations, [value["entropy_by_head"][head] for value in training],
                     color=COLORS[index], label=head)
    entropy.set(title="Categorical entropy by actor head", ylabel="Entropy, nats", ylim=(0, None))
    turn.plot(iterations, [100 * value["turn_in_place_fraction"] for value in training],
              color=COLORS[2], label="Sample collection")
    if evaluated:
        turn.plot([row["iteration"] for row in evaluated],
                  [100 * row["validation"]["action_fraction"]["turn_only"] for row in evaluated],
                  "o-", color=COLORS[0], ms=4, label="Greedy validation")
    turn.set(title="Turning without forward or strafe", ylabel="Decisions, %", ylim=(0, 100))
    blocked.plot(iterations, [100 * value["blocked_fraction"] for value in training], color=COLORS[2])
    blocked.set(title="Blocked intervals · sample collection", ylabel="Decisions, %", ylim=(0, 100))

    critic_iterations = [row["iteration"] for row in trained if row.get("critic_only")]
    selection_path = run / "best" / "selection.json"
    selected = read_json(selection_path).get("best_iteration") if selection_path.is_file() else None
    for axis in axes.flat:
        axis.grid(alpha=.2)
        if critic_iterations:
            axis.axvspan(min(critic_iterations) - .5, max(critic_iterations) + .5, color="#999999", alpha=.12)
        if selected is not None:
            axis.axvline(selected, color=COLORS[1], lw=1, ls=":", alpha=.8)
        for row in stopped:
            axis.axvline(row["iteration"], color=COLORS[2], lw=1, ls="--", alpha=.8)
        axis.set_xlim(min(rows[0]["iteration"], selected if selected is not None else rows[0]["iteration"]) - .4,
                      rows[-1]["iteration"] + .4)
        if axis.get_legend_handles_labels()[0]:
            axis.legend(fontsize=7, loc="best", ncol=2 if axis in (components, shares, entropy) else 1)
    axes[-1, 0].set_xlabel("PPO iteration")
    axes[-1, 1].set_xlabel("PPO iteration")
    detail = f"{rows[-1]['decisions'] / 1_000_000:.2f} million sampled decisions"
    if selected is not None:
        detail += f" · dotted green: selected iteration {selected}"
    if critic_iterations:
        detail += " · grey: critic warm-up"
    if stopped:
        detail += "\n" + "; ".join(f"Stop at iteration {row['iteration']}: {row['stop_reason']}" for row in stopped)
    if not trained:
        detail += "\nNo optimizer updates; training metrics are unavailable"
    fig.suptitle("Navigation recurrent PPO\n" + detail, fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, .96))
    paths = save_figure(fig, output / "curves")
    plt.close(fig)
    return paths


def pair_key(episode):
    return episode["start"], episode["goal"]


def load_drive(root):
    metrics = read_json(root / "metrics.json")
    values = [episode for episode in metrics["episodes"] if episode.get("split") != "control"]
    mapping = {}
    relocated = None
    for episode in values:
        key = pair_key(episode)
        if key in mapping:
            raise ValueError(f"Duplicate measured pair in {root}: {key}")
        episode = dict(episode)
        path = Path(episode["episode"])
        if not path.is_absolute():
            path = root / path
        if not (path / "trajectory.txt").is_file():
            # Drive outputs can be copied from Ryzen for local report rendering.
            # Match their metadata, never an ambiguous suffix of an old path.
            if relocated is None:
                relocated = {}
                for metadata in sorted(root.rglob("episode.json")):
                    data = read_json(metadata)
                    if data.get("drive") and all(data.get(field) for field in ("start", "goal")):
                        relocated.setdefault(pair_key(data), []).append(metadata.parent)
            candidates = relocated.get(key, [])
            if len(candidates) != 1:
                raise ValueError(f"Missing or ambiguous recorded trajectory for {key} in {root}")
            path = candidates[0]
        episode["episode"] = str(path)
        mapping[key] = episode
    if not mapping:
        raise ValueError(f"No measured test episodes in {root}")
    return mapping


def matched_episodes(baseline, final):
    if baseline.keys() != final.keys():
        raise ValueError("Round 2 and final drives must contain exactly the same directed test pairs")
    if len(baseline) != 60 or Counter(episode["region"] for episode in baseline.values()) != Counter({region: 10 for region in REGIONS}):
        raise ValueError("Final test requires exactly 60 pairs, ten in each of the six standard regions")
    for key in baseline:
        if baseline[key]["region"] != final[key]["region"]:
            raise ValueError(f"Region differs for the same pair: {key}")
    return sorted(baseline, key=lambda key: (baseline[key]["region"], *key))


def select_comparisons(baseline, final, count=6):
    keys = matched_episodes(baseline, final)
    if len(keys) < count:
        raise ValueError(f"Need {count} measured test pairs, found {len(keys)}")
    recovered = [key for key in keys if not baseline[key]["success"] and final[key]["success"]]
    former_failures = [key for key in keys if not baseline[key]["success"]]
    both_arrived = [key for key in keys if baseline[key]["success"] and final[key]["success"]]
    priority = list(dict.fromkeys([*recovered, *former_failures, *both_arrived, *keys]))
    selected, regions = [], set()
    for key in priority:
        if baseline[key]["region"] not in regions and len(selected) < count:
            selected.append(key)
            regions.add(baseline[key]["region"])
    for key in priority:
        if len(selected) >= count:
            break
        if key not in selected:
            selected.append(key)
    return selected


def trajectory(episode):
    import numpy as np
    path = Path(episode["episode"]) / "trajectory.txt"
    rows = np.loadtxt(path, ndmin=2)
    if rows.shape[1] != 5 or not len(rows) or not np.isfinite(rows).all():
        raise ValueError(f"Invalid recorded trajectory: {path}")
    return rows


def goal_xz(episode):
    import numpy as np
    position = episode.get("target_position")
    if position is None or len(position) != 3 or not np.isfinite(position).all():
        raise ValueError(f"Missing finite 3D target_position: {episode['episode']}")
    return np.asarray(position, dtype=float)[[0, 2]] / 100


def plot_comparison(baseline, final, selected, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(len(selected), 2, figsize=(11, 3.8 * len(selected)), squeeze=False)
    for row, key in enumerate(selected):
        before, after = baseline[key], final[key]
        paths = [trajectory(episode)[:, [1, 3]] / 100 for episode in (before, after)]
        goals = [goal_xz(episode) for episode in (before, after)]
        if np.linalg.norm(goals[0] - goals[1]) > .01:
            raise ValueError(f"Measured goal positions differ for {key}")
        bounds = np.vstack([*paths, goals[0][None, :]])
        low, high = bounds.min(axis=0), bounds.max(axis=0)
        center = (low + high) / 2
        span = max(float((high - low).max()), 5) * 1.15
        for column, (episode, path, title, color) in enumerate(zip(
                (before, after), paths, ("Round 2", "PPO selected"), (COLORS[2], COLORS[0]))):
            axis = axes[row, column]
            axis.plot(path[:, 0], path[:, 1], color=color, lw=1.4)
            axis.scatter(*path[0], c="#25314c", marker="o", s=25, label="Start", zorder=4)
            axis.scatter(*path[-1], c=color, marker="s", s=25, label="Finish", zorder=5)
            axis.scatter(*goals[column], c=COLORS[5], edgecolors="#7b5a00", linewidths=.5,
                         marker="*", s=110, label="Goal", zorder=6)
            axis.set(xlim=(center[0] - span / 2, center[0] + span / 2),
                     ylim=(center[1] - span / 2, center[1] + span / 2),
                     xlabel="World X, m", ylabel="World Z, m", aspect="equal")
            result = "arrived" if episode["success"] else episode.get("observed_reason", "failed")
            axis.set_title(f"{title}: {result} · {episode['elapsed_s']:.1f} s", fontsize=10)
            axis.grid(alpha=.2)
            axis.legend(loc="best", fontsize=7)
        recovered = " · recovered failure" if not before["success"] and after["success"] else ""
        axes[row, 0].text(0, 1.17, f"{before['region']}{recovered}\n{key[0]} → {key[1]}",
                          transform=axes[row, 0].transAxes, fontsize=9)
    fig.suptitle("Same held-out test pairs · actual CharacterVirtual trajectories", fontsize=13, y=1.005)
    fig.tight_layout(h_pad=4.3)
    paths = save_figure(fig, output / "comparison")
    plt.close(fig)
    return paths


def measured_summary(episodes):
    """Use recorded movement and jump-key rising edges for distance-normalized rates."""
    import numpy as np
    decisions = sum(episode["decisions"] for episode in episodes)
    elapsed_ms = sum(episode["elapsed_ms"] for episode in episodes)
    distance_cm, jump_presses = 0., 0
    for episode in episodes:
        positions = trajectory(episode)[:, [1, 3]]
        distance_cm += float(np.linalg.norm(np.diff(positions, axis=0), axis=1).sum())
        actions = np.loadtxt(Path(episode["episode"]) / "actions.txt", ndmin=2)
        if len(actions) != episode["decisions"] + 1 or actions.shape[1] < 7 or not np.isfinite(actions).all():
            raise ValueError(f"Invalid measured actions: {episode['episode']}")
        jump = actions[:-1, 5] != 0  # terminal reset excluded, as in nav_drive.py
        jump_presses += int(np.count_nonzero(jump & ~np.r_[False, jump[:-1]]))
    success = [episode for episode in episodes if episode["success"]]
    km = distance_cm / 100_000
    turn_count = sum(episode["action_counts"]["turn_only"] for episode in episodes)
    blocked_ms = sum(episode.get("blocked_ms") or 0 for episode in episodes)
    return dict(n=len(episodes), arrived=len(success), arrival_fraction=len(success) / len(episodes),
                median_arrival_s=statistics.median(episode["elapsed_s"] for episode in success) if success else None,
                median_elapsed_s=statistics.median(episode["elapsed_s"] for episode in episodes),
                decisions=decisions, turn_in_place_fraction=turn_count / decisions if decisions else None,
                elapsed_s=elapsed_ms / 1000, blocked_s=blocked_ms / 1000,
                blocked_elapsed_fraction=blocked_ms / elapsed_ms if elapsed_ms else None,
                blocked_spans=sum(episode.get("blocked_spans") or 0 for episode in episodes),
                stationary_episodes=sum(episode["stationary_spells_3s"] > 0 for episode in episodes),
                stationary_spells_3s=sum(episode["stationary_spells_3s"] for episode in episodes),
                travelled_xz_km=km, jump_presses=jump_presses, jump_presses_per_km=jump_presses / km if km else None,
                failures=dict(Counter(episode["observed_reason"] for episode in episodes if not episode["success"])))


def final_metrics(baseline, final, selected):
    keys = matched_episodes(baseline, final)
    result = dict(
        definitions=dict(turn_in_place="Turn head nonzero, forward and strafe heads zero; fraction of decisions",
                         stationary="Consecutive 3 s endpoint-displacement windows <=25 cm in 3D, merged into spells",
                         jump="Jump-key rising edges; initial pressed key counts once; terminal reset excluded",
                         distance="Sum of recorded horizontal XZ displacements, km",
                         reward_components="Weighted signed mean per path-valid decision",
                         reward_component_abs_fraction="Sum of absolute weighted component contributions / sum across components"),
        comparison_pairs=[dict(start=key[0], goal=key[1], region=baseline[key]["region"],
                               recovered_failure=not baseline[key]["success"] and bool(final[key]["success"])) for key in selected],
        recovered_failures=sum(not baseline[key]["success"] and bool(final[key]["success"]) for key in keys),
    )
    for label, values in (("round2", baseline), ("final", final)):
        episodes = list(values.values())
        result[label] = dict(all=measured_summary(episodes),
                             by_region={region: measured_summary([episode for episode in episodes if episode["region"] == region])
                                        for region in sorted({episode["region"] for episode in episodes})})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run", "baseline", "final", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--plot-count", type=int, default=6, choices=(4, 5, 6))
    args = parser.parse_args()
    for name in ("run", "baseline", "final", "output"):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    try:
        rows = load_curves(args.run)
        baseline, final = load_drive(args.baseline), load_drive(args.final)
        selected = select_comparisons(baseline, final, args.plot_count)
        metrics = final_metrics(baseline, final, selected)
        metrics["collection_health"] = [dict(iteration=row["iteration"], cumulative_decisions=row["decisions"],
                                              **row["collection_health"])
                                        for row in rows if row.get("collection_health") is not None]
        metrics["stops"] = [dict(iteration=row["iteration"], decisions=row["decisions"], reason=row["stop_reason"])
                            for row in rows if row.get("stop_reason")]
        args.output.mkdir(parents=True, exist_ok=True)
        figures = [*plot_curves(args.run, rows, args.output), *plot_comparison(baseline, final, selected, args.output)]
        write_json(args.output / "final_metrics.json", metrics)
        print(json.dumps(dict(figures=figures, metrics=str(args.output / "final_metrics.json"),
                              comparison_pairs=metrics["comparison_pairs"]), ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(1, f"nav-ppo-report: {exc}\n")


if __name__ == "__main__":
    main()
