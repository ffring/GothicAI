#!/usr/bin/env python3
"""Render runner snapshots and extract compact, reproducible measurements."""
import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

COLORS = {"free": "#7d9e8b", "wall": "#ed825b", "ledge": "#e8bf63",
          "drop": "#b58bd4", "water": "#55b9e8", "movable": "#e56ba7"}


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def cells(snapshot, scale):
    layer = snapshot[scale]
    return [dict(zip(layer["columns"], row)) for row in layer["cells"]]


def relative(snapshot, point):
    delta = np.asarray(point) - np.asarray(snapshot["root_cm"])
    f = np.asarray(snapshot["body_forward"])
    r = np.array([-f[2], 0, f[0]])
    return float(delta @ r), float(delta @ f)


def matches(snapshot, scale, points, channel):
    candidates = [c for c in cells(snapshot, scale) if c[channel] > 0]
    result = []
    for point in points:
        if not candidates:
            result.append(None)
            continue
        c = min(candidates, key=lambda c: (c["world_x_cm"]-point[0])**2 +
                (c["world_z_cm"]-point[2])**2)
        dx, dz = c["world_x_cm"]-point[0], c["world_z_cm"]-point[2]
        result.append({"point_cm": point, "cell_world_cm": [c["world_x_cm"], c["world_z_cm"]],
                       "axis_error_cm": max(abs(dx), abs(dz)), "distance_error_cm": math.hypot(dx, dz),
                       "confidence": c[channel], "x": c["x"], "z": c["z"]})
    return result


def hit_near(ray, points, tolerance=25):
    return ray["hit"] and any(math.dist(ray["point_cm"], p) <= tolerance for p in points)


def plot(snapshot, scale, target, fence):
    layer = snapshot[scale]
    n, step = layer["size"], layer["cell_cm"]/100
    rgb = np.zeros((n, n, 3)) + np.array([.055, .075, .10])
    for cell in cells(snapshot, scale):
        # A fresh empty ray above an older wall can share its projected cell.
        # Show the wall channel rather than letting fresher free air hide it.
        obstacles = [k for k in COLORS if k != "free" and cell[k] > 0]
        channel = max(obstacles or list(COLORS), key=lambda k: cell[k])
        c = cell[channel] if cell[channel] > 0 else cell["confidence"]
        if cell[channel] > 0:
            color = np.array(matplotlib.colors.to_rgb(COLORS[channel]))
        else:
            color = np.array([.50, .57, .49])
        rgb[int(cell["z"]), int(cell["x"])] = np.array([.055, .075, .10])*(1-c) + color*c
    half = n*step/2
    fig, ax = plt.subplots(figsize=(8, 8), facecolor="#0e131a")
    ax.set_facecolor("#0e131a")
    ax.imshow(rgb, origin="lower", extent=(-half, half, -half, half), interpolation="nearest")
    body_f = np.asarray(snapshot["body_forward"])
    body_r = np.array([-body_f[2], 0, body_f[0]])
    head_f = np.asarray(snapshot["head_forward"])
    head_r = np.asarray(snapshot["head_right"])
    origin = relative(snapshot, snapshot["head_origin_cm"])
    origin = np.array(origin)/100
    # Actual head orientation on the current body view; centre reaches 100 m,
    # peripheral sectors reach 30 m. Near plot clips the same geometry.
    for lo, hi, radius in [(-100, -60, 30), (-60, 60, 100), (60, 100, 30)]:
        angles = np.radians(np.linspace(lo, hi, 161))
        directions = np.cos(angles)[:, None]*head_f + np.sin(angles)[:, None]*head_r
        endpoints = np.stack((directions @ body_r, directions @ body_f), axis=1)*radius + origin
        polygon = np.vstack((origin, endpoints, origin))
        ax.plot(polygon[:, 0], polygon[:, 1], color="#7dd7fa", lw=.8, ls="--", alpha=.65)
    for ray in snapshot["current_rays"]:
        if abs(ray["pitch_deg"]) > 5:
            continue
        start = np.array(relative(snapshot, ray["origin_cm"]))/100
        end = np.array(relative(snapshot, ray["point_cm"]))/100
        ax.plot([start[0], end[0]], [start[1], end[1]], color="#7dd7fa", lw=.35, alpha=.18)
    ax.scatter([0], [0], marker="^", s=80, color="white", edgecolors="#0e131a", zorder=5)
    if scale == "near":
        remembered = matches(snapshot, scale, fence, "wall")
        positions = np.array([[(m["x"]+.5-n/2)*step, (m["z"]+.5-n/2)*step] for m in remembered if m])
        ax.scatter(positions[:, 0], positions[:, 1], marker="s", s=40, facecolors="none",
                   edgecolors="white", linewidths=.6, zorder=4)
        ax.annotate("remembered fence", np.median(positions, axis=0), xytext=(-65, -36),
                    textcoords="offset points", color="white", fontsize=8,
                    arrowprops={"arrowstyle": "->", "color": "white", "lw": .7})
    ax.annotate("body forward", (0, min(half*.2, 3)), xytext=(0, 0), ha="center", color="white",
                arrowprops={"arrowstyle": "->", "color": "white"}, fontsize=8)
    ax.set(xlim=(-half, half), ylim=(-half, half), aspect="equal", xlabel="right / left, m", ylabel="forward / behind, m")
    ax.tick_params(colors="#d4dbe5")
    ax.xaxis.label.set_color("#d4dbe5")
    ax.yaxis.label.set_color("#d4dbe5")
    for spine in ax.spines.values(): spine.set_color("#475366")
    ax.set_title(f"Lobart fence · {snapshot['tag']} · {scale}\n{n} × {n}, cell {step:g} m · brightness = channel confidence", color="white", pad=15)
    handles = [Line2D([], [], marker="s", ls="", color=color, label=key) for key, color in COLORS.items()]
    handles.append(Line2D([], [], color="#7dd7fa", ls="--", label="current eye"))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(.5, -.09), ncol=4,
              frameon=False, labelcolor="#d4dbe5", fontsize=9)
    fig.tight_layout()
    fig.savefig(target, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, help="contains fence/, perf20/, perf50/, optionally mac/")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    snapshots = {s["tag"]: s for s in read(args.results/"fence/memory-snapshots.jsonl")}
    front, turned = snapshots["front"], snapshots["turned"]
    f = np.asarray(front["body_forward"])
    root = np.asarray(front["root_cm"])
    fence = [r["point_cm"] for r in front["current_rays"]
             if r["hit"] and r["hit_kind"] == "static_vob" and abs(r["normal_y"]) <= .3
             and np.linalg.norm(np.asarray(r["point_cm"])-root) < 500
             and (np.asarray(r["point_cm"])-root) @ f > 0]
    far = [r["point_cm"] for r in front["current_rays"]
           if r["hit"] and r["hit_kind"] in ("world", "static_vob") and abs(r["normal_y"]) <= .3
           and r["distance_cm"] > 3000]
    assert fence, "no calibrated fence hit"
    assert far, "no distant vertical object hit"
    assert abs(float(f @ np.asarray(turned["body_forward"]))+1) < .002, "turn is not 180 degrees"
    measurements = []
    for tag, snapshot in snapshots.items():
        matched = matches(snapshot, "near", fence, "wall")
        errors = [m["axis_error_cm"] for m in matched if m]
        valid = [m for m in matched if m and m["axis_error_cm"] <= 25]
        distances = [relative(snapshot, p)[1] for p in fence]
        plane_errors = [abs((np.array([m["cell_world_cm"][0], p[1], m["cell_world_cm"][1]])-p) @ f)
                        for m, p in zip(matched, fence) if m]
        row = {"tag": tag, "game_ms": snapshot["game_ms"], "root_cm": snapshot["root_cm"],
               "body_yaw_deg": snapshot["body_yaw_deg"], "traveled_cm": snapshot["traveled_cm"],
               "fence_reference_points": len(fence), "fence_matched_within_25cm": len(valid),
               "fence_max_axis_error_cm": max(errors, default=None),
               "fence_max_normal_error_cm": max(plane_errors, default=None),
               "fence_distance_along_body_cm": float(np.median(distances)),
               "fence_wall_confidence_mean": float(np.mean([m["confidence"] for m in matched if m])),
               "current_eye_fence_hits": sum(hit_near(r, fence) for r in snapshot["current_rays"]),
               "fence_matches": matched}
        measurements.append(row)
    hidden = next(e for e in read(args.results/"fence/memory-events.jsonl") if e["kind"] == "memory_hidden_npc")
    unseen = snapshots["unseen"]
    hidden_cells = [c for c in cells(unseen, "near")
                    if math.hypot(c["world_x_cm"]-hidden["root_cm"][0], c["world_z_cm"]-hidden["root_cm"][2]) < 60]
    hidden["memory_movable_max"] = max((c["movable"] for c in hidden_cells), default=0)
    hidden["current_eye_npc_hits_at_spawn"] = sum(r["hit"] and r["hit_kind"] == "npc" and
        math.hypot(r["point_cm"][0]-hidden["root_cm"][0], r["point_cm"][2]-hidden["root_cm"][2]) < 60
        for r in unseen["current_rays"])
    far_matches = matches(turned, "far", far, "wall")
    by_tag = {m["tag"]: m for m in measurements}
    for tag in ("turned", "unseen", "back-1", "back-2", "stopped", "stand-start", "aged"):
        assert by_tag[tag]["current_eye_fence_hits"] == 0, f"{tag}: eye sees the reference fence"
        assert by_tag[tag]["fence_max_normal_error_cm"] <= 25, f"{tag}: fence farther than one cell"
    assert by_tag["aged"]["fence_wall_confidence_mean"] < by_tag["stand-start"]["fence_wall_confidence_mean"], "confidence did not decay"
    assert snapshots["aged"]["game_ms"]-snapshots["stand-start"]["game_ms"] == 60000, "standing span must be 60 simulated seconds"
    assert hidden["memory_movable_max"] == hidden["current_eye_npc_hits_at_spawn"] == 0, "hidden NPC leaked into memory"
    assert any(m and m["axis_error_cm"] <= 200 for m in far_matches), "distant object missing after the turn"
    costs = []
    for name in ("perf20", "perf50", "mac"):
        path = args.results/name/"memory-spans.jsonl"
        if path.exists(): costs.extend(read(path))
    assert {c["actors"] for c in costs if c["tag"] != "mac"} == {20, 50}, "both Ryzen pools are required"
    assert all(c["ray_count"] == 238 and not c["eye_cost_included"] and c["pose_updates_amortized"] for c in costs)
    for name in ("fence", "perf20", "perf50", "mac"):
        summary = args.results/name/"runner-summary.json"
        if summary.exists():
            status = json.loads(summary.read_text())
            assert status["done"] and not any(status[k] for k in ("graphics_device_created", "window_created", "audio_device_created")), name
    result = {"fence": measurements, "hidden": hidden,
              "far_reference_points": len(far), "far_after_turn": far_matches, "costs": costs}
    (args.output/"measurements.json").write_text(json.dumps(result, indent=2)+"\n")
    for tag in ("turned", "aged"):
        for scale in ("near", "far"):
            plot(snapshots[tag], scale, args.output.parent/f"{tag}-{scale}.png", fence)
    for m in measurements:
        print(m["tag"], "distance", round(m["fence_distance_along_body_cm"], 2),
              "confidence", round(m["fence_wall_confidence_mean"], 6),
              "normal error", round(m["fence_max_normal_error_cm"], 2),
              "eye hits", m["current_eye_fence_hits"])
    print("hidden", hidden)
    print("far", len(far), "max axis error", max(m["axis_error_cm"] for m in far_matches if m))
    for c in costs: print(c["tag"], c["update_and_view"])


if __name__ == "__main__":
    main()
