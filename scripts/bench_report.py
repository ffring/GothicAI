#!/usr/bin/env python3
"""Report for a test-bench run (scripts/bench.sh): finds suspicious frames in metrics.csv and makes
contact sheets (frame grids) from the recorded videos, so the frames can be looked at as images.

  bench_report.py <out dir>                          summary, report.md, sheets/
  bench_report.py frames <out dir> <seg> <from> <to> [step]   one sheet of the given frames
  bench_report.py compare <video a> <video b> [from] [to]      side by side video and sheet (a left, b right)
  bench_report.py diff <out dir a> <out dir b>                 event counts per segment, before -> after
"""
import csv
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict, deque

FPS = 30
TOP_EVENTS = 12
LEGS = ("l", "r", "fl", "fr")  # hind (or the only) pair, front pair of a four-legged creature
OTHER = {"l": "r", "r": "l", "fl": "fr", "fr": "fl"}
CONTACT = ("float", "sink", "toe_sink")

# flag: (threshold, unit, what it means)
# Gaze::Source
GAZE_SRC = {0: "none", 1: "talk", 2: "script", 3: "focus", 4: "attention"}

FLAGS = {
    "float":     (3.0, "cm",  "planted foot above the ground: ankle and toes (contact error > 0)"),
    "sink":      (3.0, "cm",  "planted ankle below the ground (contact error < 0)"),
    "toe_sink":  (3.0, "cm",  "toe joint lower under the ground than in the animation"),
    "pop":       (12.0, "cm", "IK correction of a joint changed abruptly (second difference over 3 frames)"),
    "pelvis":    (2.0, "cm",  "body height jumped in one frame"),
    "knee":      (25.0, "deg", "knee bends away from the animated direction"),
    "toe_twist": (12.0, "deg", "planted toes turned sideways beyond the intended outward turn"),
    "stretch":   (2.0, "cm",  "planted leg straight and still can not reach the ground"),
    "gaze_off":  (15.0, "deg", "looking past the partner although it is within reach of the turn"),
    "aim_off":   (3.0, "deg", "the bow (the spell) aims past where the shot leaves, although the turn could reach it"),
    "gaze_jerk": (10.0, "deg", "the face turned this much in one frame (a turn to the partner goes up to 9)"),
    "anim_pop":  (20.0, "cm", "a joint of the animated pose (before lean, IK, gaze) jumped at a switch of animations: second difference over 3 frames"),
    "lip_open":  (0.3, "",    "the mouth stays open although the voice is silent"),
    "lip_shut":  (0.7, "",    "the voice is loud but the mouth stays shut"),
    "face_jump": (0.5, "",    "a face came or went at once (it fades in about a second; T_HURT in 0.1 s)"),
    "flinch_none": (1.0, "x", "a blow and the upper body hardly gave way: 1.5 degrees over the peak"),
    "flinch_slow": (1.5, "s", "the upper body is not back this long after the last blow"),
    "blink_shut": (0.5, "",   "a blink keeps the eyes shut too long (a blink is closed for 2-4 frames)"),
}
TEXT = ("an_bone", "an_layers")  # columns that are names, not numbers
FACES = ("face_neutral", "face_friendly", "face_angry", "face_hostile", "face_afraid", "face_pain")
LIPS = ("lip_open", "lip_shut")
BLINK_FRAMES = 7  # the eyes half shut by a blink longer than this many frames: stuck
LIP_FRAMES = 4  # the mouth closes and opens over a frame or two: shorter runs are not flagged


def counted(rows, col):
    """what a counter of the watched one added over the rows: a switch of the watched one (a new counter,
    first read as 0) is not counted"""
    return int(sum(d for d in (b[col] - a[col] for a, b in zip(rows, rows[1:])) if 0 < d <= 3))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return math.nan


def load(out):
    path = os.path.join(out, "metrics.csv")
    segs = defaultdict(list)
    if not os.path.exists(path):
        return segs
    with open(path) as f:
        for row in csv.DictReader(f):
            seg = row.pop("seg")
            segs[seg].append({k: (v if k in TEXT else num(v)) for k, v in row.items()})
    return segs


def legs_of(r):
    n = r.get("nlegs")
    return LEGS[:4] if n is not None and n >= 4 else LEGS[:2]


def vec(r, leg, joint):
    return (r[f"{leg}_{joint}_x"], r[f"{leg}_{joint}_y"], r[f"{leg}_{joint}_z"])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def length(a):
    return math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)


def contact_err(r, leg):
    """how far a planted foot is above (+) or below (-) the ground; standing on the toes counts"""
    err = r[f"{leg}_err"]
    if r[f"{leg}_toehas"] > 0 and r[f"{leg}_toey"] - r[f"{leg}_gy"] < 25:  # higher: a wall, not a step
        toe_err = (r[f"{leg}_toef_y"] - r[f"{leg}_toey"]) - (r[f"{leg}_toea_y"] - r["floor"])
        if err > 0:
            return min(err, max(toe_err, 0.0))
    return err


def toe_yaw(r, leg):
    """horizontal turn of the toes from the animated direction, degrees, + is outward"""
    a = sub(vec(r, leg, "toea"), vec(r, leg, "anka"))
    f = sub(vec(r, leg, "toef"), vec(r, leg, "ankf"))
    la, lf = math.hypot(a[0], a[2]), math.hypot(f[0], f[2])
    if la < 8 or lf < 8:
        return math.nan  # the foot points almost straight down: its horizontal direction is noise
    cos = (a[0] * f[0] + a[2] * f[2]) / (la * lf)
    ang = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
    side = sub(vec(r, leg, "hip"), vec(r, OTHER[leg], "hip"))
    grow = (f[0] / lf - a[0] / la) * side[0] + (f[2] / lf - a[2] / la) * side[2]
    return ang if grow >= 0 else -ang


def frame_flags(prev2, prev, r, switch=False):
    """flags of one frame: list of (flag, leg, value); switch: next to a switch of animations"""
    out = []
    on = r["ik_valid"] > 0 and r["ik_w"] > 0.99
    if prev is not None and on and prev["ik_w"] > 0.99:
        jump = abs(r["pelvis"] - prev["pelvis"])
        if "chest" in r and legs_of(r) == LEGS:
            jump = max(jump, abs(r["chest"] - prev["chest"]))
        out.append(("pelvis", "-", jump))
    if "g_w" in r:
        # within reach: the head can turn 65 degrees, with the body 110 (the goal is clamped to that)
        if r["g_w"] > 0.99 and r["g_err"] >= 0 and abs(r["g_yawgoal"]) < 60:
            out.append(("gaze_off", "-", r["g_err"]))
        if prev is not None and "g_w" in prev and "g_fyaw" in r:
            # the face in the world, while the gaze holds it (the body may turn under it); not right
            # after a segment starts: the bench teleports the hero there
            if r["g_w"] > 0 and prev["g_w"] > 0 and r["frame"] >= 6:
                dy = (r["g_fyaw"] - prev["g_fyaw"] + 180) % 360 - 180
                out.append(("gaze_jerk", "-", math.hypot(dy, r["g_fpitch"] - prev["g_fpitch"])))
        elif prev is not None and "g_w" in prev:
            out.append(("gaze_jerk", "-", abs(r["g_yaw"] * r["g_w"] - prev["g_yaw"] * prev["g_w"])))
    # the bench starts the history of the pose with the segment: two frames before the first difference.
    # Only at a switch: within an animation it is how the animation moves (a running waran: 80 cm)
    if "an_pop" in r and r["frame"] >= 2 and switch:
        out.append(("anim_pop", r.get("an_bone", "-"), r["an_pop"]))
    if "a_w" in r and r["a_w"] > 0.99 and r.get("a_steady", 1) > 0 and r["a_err"] >= 0 and abs(r["a_yaw"]) < 40 \
            and -45 < r["a_pitch"] < 55:
        out.append(("aim_off", "-", r["a_err"]))
    if prev is not None and FACES[0] in r and FACES[0] in prev:
        out.append(("face_jump", "-", max(abs(r[c] - prev[c]) for c in FACES)))
    if r.get("bk_w", 0) > 0.5:
        out.append(("blink_shut", "-", r["bk_w"]))
    if r.get("lip_on", 0) > 0:
        # the level the mouth aims at (the voice a moment ahead) against how wide it is shown
        if r["lip_level"] < 0.05:
            out.append(("lip_open", "-", r["lip_open"]))
        if r["lip_open"] < 0.1:
            out.append(("lip_shut", "-", r["lip_level"]))
    for leg in legs_of(r):
        planted = r[f"{leg}_plant"] > 0.9 and r[f"{leg}_has"] > 0
        if on and planted:
            err = r[f"{leg}_err"]
            toe_err = math.nan
            if r[f"{leg}_toehas"] > 0 and r[f"{leg}_toey"] - r[f"{leg}_gy"] < 25:  # higher: a wall, not a step
                anim_clear = r[f"{leg}_toea_y"] - r["floor"]
                clear = r[f"{leg}_toef_y"] - r[f"{leg}_toey"]
                toe_err = clear - anim_clear
                out.append(("toe_sink", leg, -toe_err))
            # standing on the toes (end of a step) is standing too
            out.append(("float", leg, err if math.isnan(toe_err) else min(err, toe_err)))
            out.append(("sink", leg, -err))
            hip, knee, ank = vec(r, leg, "hip"), vec(r, leg, "kneef"), vec(r, leg, "ankf")
            full = length(sub(knee, hip)) + length(sub(ank, knee))
            if full > 0 and length(sub(ank, hip)) > 0.97 * full:
                out.append(("stretch", leg, -err))
            out.append(("toe_twist", leg, abs(toe_yaw(r, leg) - r[f"{leg}_out"])))
        if on:
            out.append(("knee", leg, r[f"{leg}_knee"]))
        if prev2 is not None and on and prev["ik_w"] > 0.99 and prev2["ik_w"] > 0.99:
            jump = 0.0
            for j in ("knee", "ank", "toe"):
                c2 = sub(vec(r, leg, j + "f"), vec(r, leg, j + "a"))
                c1 = sub(vec(prev, leg, j + "f"), vec(prev, leg, j + "a"))
                c0 = sub(vec(prev2, leg, j + "f"), vec(prev2, leg, j + "a"))
                jump = max(jump, length((c2[0] - 2 * c1[0] + c0[0], c2[1] - 2 * c1[1] + c0[1], c2[2] - 2 * c1[2] + c0[2])))
            out.append(("pop", leg, jump))
    return out


def switches(rows):
    """frames where the animations switch: another one plays, or one restarts (AnimBlend saw it)"""
    return [i for i in range(1, len(rows)) if rows[i].get("an_layers") != rows[i - 1].get("an_layers")
            or rows[i].get("bl_started", 0) > 0]


def switch_frames(rows):
    """frames a switch can show in: the one before it (it is seen a frame late) to 6 after"""
    return {j for i in switches(rows) for j in range(max(0, i - 1), min(len(rows), i + 7))}


def analyze(rows):
    """per-segment stats and events (runs of flagged frames)"""
    stats = {"frames": len(rows)}
    errs = []
    occl = 0
    on = 0
    current = {}   # (flag, leg) -> event being built
    events = []
    prev2 = prev = None
    near = switch_frames(rows)
    for idx, r in enumerate(rows):
        if r["ik_w"] > 0.99:
            on += 1
        if r["occl"] > 0:
            occl += 1
        for leg in legs_of(r):
            # with IK off too: that is how far the animation misses the ground
            if (r["ik_w"] > 0.99 or r["ik_w"] <= 0) and r[f"{leg}_plant"] > 0.9 and r[f"{leg}_has"] > 0:
                errs.append(abs(contact_err(r, leg)))
        hit = set()
        for flag, leg, v in frame_flags(prev2, prev, r, idx in near):
            if not (v > FLAGS[flag][0]):
                continue
            key = (flag, leg)
            hit.add(key)
            ev = current.get(key)
            if ev is not None and r["frame"] - ev["end"] <= 3:
                ev["end"] = int(r["frame"])
                if v > ev["worst"]:
                    ev["worst"], ev["at"] = v, int(r["frame"])
            else:
                ev = {"flag": flag, "leg": leg, "start": int(r["frame"]), "end": int(r["frame"]),
                      "worst": v, "at": int(r["frame"])}
                if flag == "anim_pop":
                    # what played a few frames before and what plays now: a pop at a switch is a transition
                    before = rows[max(0, idx - 3)].get("an_layers", "-")
                    ev["layers"] = r.get("an_layers", "-") if before == r.get("an_layers") else \
                        f"{before} -> {r.get('an_layers', '-')}"
                current[key] = ev
                events.append(ev)
        prev2, prev = prev, r
    # how much the height of a planted foot above the ground varies: ~0 when it stands on the ground,
    # whatever the creature; works for a body tilted to the ground as a whole too (no floor plane needed)
    gaps = defaultdict(list)
    for r in rows:
        for leg in legs_of(r):
            if r[f"{leg}_plant"] > 0.9 and r[f"{leg}_has"] > 0:
                gaps[leg].append(r[f"{leg}_ankf_y"] - r[f"{leg}_gy"])
    dev = []
    for v in gaps.values():
        if len(v) > 5:
            med = sorted(v)[len(v) // 2]
            dev += [abs(x - med) for x in v]
    if dev:
        dev.sort()
        stats["spread"] = dev[int(len(dev) * 0.9)]
    errs.sort()
    if errs:
        stats["contact_p50"] = errs[len(errs) // 2]
        stats["contact_p90"] = errs[int(len(errs) * 0.9)]
        stats["contact_max"] = errs[-1]
    # limp: the body is lower on the steps of one leg than on the other's (each pair of legs)
    stance = {leg: [] for leg in LEGS}
    for r in rows:
        if r["ik_w"] > 0.99:
            for leg in legs_of(r):
                if r[f"{leg}_plant"] > 0.99 and r[f"{OTHER[leg]}_plant"] < 0.5:
                    stance[leg].append(r["chest"] if leg.startswith("f") else r["pelvis"])
    for a, b in (("l", "r"), ("fl", "fr")):
        if len(stance[a]) > 5 and len(stance[b]) > 5:
            limp = abs(sum(stance[a]) / len(stance[a]) - sum(stance[b]) / len(stance[b]))
            stats["limp"] = max(stats.get("limp", 0.0), limp)
    gaze = sorted(r["g_err"] for r in rows if r.get("g_w", 0) > 0.99 and r.get("g_err", -1) >= 0
                  and abs(r.get("g_yawgoal", 0)) < 60)
    if gaze:
        stats["gaze_p90"] = gaze[int(len(gaze) * 0.9)]
    # how often it looks: share of frames, times it turned to someone, why (talk, script, ...)
    if rows and "g_on" in rows[0]:
        stats["gaze_on"] = sum(1 for r in rows if r["g_on"] > 0) / len(rows)
        stats["gaze_looks"] = sum(1 for a, b in zip(rows, rows[1:]) if a["g_on"] == 0 and b["g_on"] > 0) + int(rows[0]["g_on"] > 0)
        stats["gaze_src"] = sorted({GAZE_SRC.get(int(r.get("g_src", 0)), "?") for r in rows if r["g_on"] > 0})
    # aim: how far off the shot direction while aiming, how far the upper body bends
    # (only while the bow holds still: reloading swings the arrow around on purpose)
    aims = sorted(r["a_err"] for r in rows if r.get("a_w", 0) > 0.99 and r.get("a_err", -1) >= 0
                  and r.get("a_steady", 1) > 0)
    if aims:
        stats["aim_p90"] = aims[int(len(aims) * 0.9)]
        stats["aim_bend"] = max((r["a_bend"] for r in rows if r.get("a_w", 0) > 0.99), key=abs)
    # lean: the largest roll into a turn, the trunk pitch range (degrees)
    if rows and "ln_roll" in rows[0]:
        stats["lean_roll"] = max(abs(r["ln_roll"]) for r in rows)
        stats["lean_pitch"] = (min(r["ln_pitch"] for r in rows), max(r["ln_pitch"] for r in rows))
    # the animated pose: pops (second difference of a joint) overall and next to a switch of animations
    if rows and "an_pop" in rows[0]:
        pops = sorted(r["an_pop"] for r in rows[2:] if not math.isnan(r["an_pop"]))
        if pops:
            stats["anim_p99"] = pops[min(len(pops) - 1, int(len(pops) * 0.99))]
            stats["anim_max"] = pops[-1]
        stats["anim_switches"] = len(switches(rows))
        stats["anim_switch_max"] = max([rows[j]["an_pop"] for j in near if j >= 2] or [0.0])
    # lips: how well the mouth follows the voice (correlation of the voice level and the shown opening)
    lips = [(r["lip_level"], r["lip_open"]) for r in rows if r.get("lip_on", 0) > 0]
    if len(lips) > 10:
        n = len(lips)
        ma = sum(a for a, _ in lips) / n
        mb = sum(b for _, b in lips) / n
        va = sum((a - ma) ** 2 for a, _ in lips)
        vb = sum((b - mb) ** 2 for _, b in lips)
        cov = sum((a - ma) * (b - mb) for a, b in lips)
        if va > 0 and vb > 0:
            stats["lip_corr"] = cov / math.sqrt(va * vb)
    # hands while it speaks: how much of the talk has a gesture, how much of the listening after it
    # (a second after the voice), gestures started, started over a playing one, stopped early
    if rows and "ge_talk" in rows[0]:
        talk = [r for r in rows if r["ge_talk"] > 0]
        quiet, since = [], None
        for i, r in enumerate(rows):
            since = i if r["ge_talk"] > 0 else since
            if r["ge_talk"] == 0 and since is not None and i - since > FPS:
                quiet.append(r)
        if talk:
            stats["ges_talk"] = sum(1 for r in talk if r["ge_id"] > 0) / len(talk)
            stats["ges_listen"] = None if not quiet else sum(1 for r in quiet if r["ge_id"] > 0) / len(quiet)
            for k in ("starts", "cuts", "stops") + (("nods",) if "ge_nods" in rows[0] else ()):
                stats["ges_" + k] = counted(rows, "ge_" + k)
            stats["ges_kinds"] = len({int(r["ge_id"]) for r in rows if r["ge_id"] > 0})
    # listening to the hero (nods as his words come) and thinking of an answer (the eyes aside)
    if rows and "ge_listen" in rows[0] and any(r["ge_listen"] > 0 or r["ge_think"] > 0 for r in rows):
        stats["ges_lnods"] = counted(rows, "ge_lnods")
        stats["ges_aside"] = max(r["ge_aside"] for r in rows)
    # flinch: blows, how far the upper body gives way at most (trunk bend, twist, head), how long it takes
    # to come back after a blow; a blow without a flinch, a flinch that does not come back
    if rows and "f_hits" in rows[0]:
        hits = [i for i in range(1, len(rows)) if rows[i]["f_hits"] > rows[i - 1]["f_hits"]]
        if hits:
            stats["flinch_hits"] = len(hits)
            stats["flinch_peak"] = max(max(r["f_bend"], abs(r["f_twist"]), r["f_head"]) for r in rows)
            settle = 0.0
            for k, i in enumerate(hits):
                end = hits[k + 1] if k + 1 < len(hits) else len(rows)
                peak = max(max(r["f_bend"], abs(r["f_twist"]), r["f_head"]) for r in rows[i:min(end, i + 8)])
                if peak < 1.5:
                    events.append({"flag": "flinch_none", "leg": "-", "start": int(rows[i]["frame"]),
                                   "end": int(rows[i]["frame"]), "worst": 1.5 / max(peak, 0.01), "at": int(rows[i]["frame"])})
                if k + 1 == len(hits):
                    rest = next((j for j in range(i, len(rows)) if rows[j]["f_on"] == 0), None)
                    if rest is not None:
                        settle = max(settle, (rows[rest]["t"] - rows[i]["t"]) / 1000.0)
                    elif len(rows) - i > 45:
                        t = (rows[-1]["t"] - rows[i]["t"]) / 1000.0
                        events.append({"flag": "flinch_slow", "leg": "-", "start": int(rows[i]["frame"]),
                                       "end": int(rows[-1]["frame"]), "worst": t, "at": int(rows[-1]["frame"])})
            stats["flinch_settle"] = settle
    # blinks of the watched one: how many a minute, of them in a pause of the voice, at a shift of the eyes
    if rows and "bk_n" in rows[0] and rows[-1]["bk_on"] > 0:
        secs = (rows[-1]["t"] - rows[0]["t"]) / 1000.0
        if secs > 1:
            for k in ("pause", "shift", "double"):
                stats["blink_" + k] = counted(rows, "bk_" + k)
            # the blinks seen: the eyes going half shut
            stats["blink_n"] = sum(1 for a, b in zip(rows, rows[1:]) if a["bk_w"] <= 0.3 < b["bk_w"])
            stats["blink_rate"] = stats["blink_n"] * 60.0 / secs
    stats["ik_on"] = on / max(1, len(rows))
    stats["hidden"] = occl / max(1, len(rows))
    stats["speed"] = sum(r["speed"] for r in rows) / max(1, len(rows))
    # a contact error for a single frame at the start or the end of a step is not visible
    events = [ev for ev in events if ev["flag"] not in CONTACT or ev["end"] > ev["start"]
              or ev["worst"] >= 2 * FLAGS[ev["flag"]][0]]
    events = [ev for ev in events if ev["flag"] not in LIPS or ev["end"] - ev["start"] + 1 >= LIP_FRAMES]
    events = [ev for ev in events if ev["flag"] != "blink_shut" or ev["end"] - ev["start"] + 1 > BLINK_FRAMES]
    # a blow throws the head around on purpose (the flinch, the stumble): no gaze jerk for a second after it
    if rows and "f_hits" in rows[0]:
        struck = [int(rows[i]["frame"]) for i in range(1, len(rows)) if rows[i]["f_hits"] > rows[i - 1]["f_hits"]]
        events = [ev for ev in events if ev["flag"] != "gaze_jerk" or
                  not any(0 <= ev["start"] - f <= 30 for f in struck)]
    for ev in events:
        ev["severity"] = ev["worst"] / FLAGS[ev["flag"]][0]
    return stats, events


ARCHER_STAGE = {0: "lowered", 1: "raising", 2: "nocked", 3: "drawing", 4: "held", 5: "reloading"}


def arrows(out):
    """the arrows of the manual aim per segment, from bench.log: [(zone or 'miss', draw, flew cm)]. An arrow
    counts for the segment it was shot in: a far miss may come down when the next one runs (they come
    down in the order they were shot)"""
    path = os.path.join(out, "bench.log")
    per = defaultdict(list)
    if not os.path.exists(path):
        return per
    seg, shot = None, deque()
    with open(path, errors="replace") as f:  # names of the game's npcs come in its codepage
        for line in f:
            line = line.strip()
            if line.startswith("rec stop"):
                seg = None
            elif line.startswith("rec "):
                seg = line.split()[1]
            elif line.startswith("arrow shot"):
                shot.append(seg)
            elif line.startswith("arrow hit") or line.startswith("arrow missed"):
                at = shot.popleft() if shot else seg
                if not at:
                    continue
                if line.startswith("arrow hit"):
                    body = line.split(": ", 1)[1]
                    zone = body.split(",")[0].strip()
                    draw = float(body.split("draw ")[1].split(",")[0])
                    flew = float(body.split("flew ")[1].split(" ")[0])
                    per[at].append((zone, draw, flew))
                else:
                    # "came down at <xyz>, flew <cm> cm" or "flew out at <xyz>, flew <cm> cm"
                    m = re.search(r"flew (-?[0-9.]+) cm", line)
                    per[at].append(("miss", math.nan, float(m.group(1)) if m else math.nan))
    return per


def manual_aim(out, segs):
    """the manual aim: hits by zone, the sway at full draw, how long the draw takes"""
    shots = arrows(out)
    lines = []
    for seg, rows in segs.items():
        if not rows or "m_stage" not in rows[0] or not any(r["m_stage"] >= 1 for r in rows):
            continue
        held = [r for r in rows if r["m_stage"] == 4]
        sway = math.sqrt(sum(r["m_sway_yaw"] ** 2 + r["m_sway_pitch"] ** 2 for r in held) / len(held)) if held else math.nan
        offs = sorted(r["m_off"] for r in held if r["m_off"] >= 0)
        off = f"{offs[len(offs) // 2]:.2f} / {offs[int(len(offs) * 0.9)]:.2f}" if offs else "-"
        draws, start = [], None
        for r in rows:
            if r["m_stage"] == 3 and start is None:
                start = r["t"]
            elif r["m_stage"] != 3 and start is not None:
                if r["m_stage"] == 4:
                    draws.append(r["t"] - start)
                start = None
        draw = f"{sum(draws) / len(draws) / 1000:.2f}" if draws else "-"
        a = shots.get(seg, [])
        count = defaultdict(int)
        for z, _, _ in a:
            count[z] += 1
        hits = " ".join(f"{z} {count[z]}" for z in ("head", "body", "limb", "miss") if count[z])
        flew = f"{sum(x[2] for x in a) / len(a) / 100:.1f}" if a else "-"
        # the crosshair: how open at full draw, how far it jumps in a frame (it stays where it aims)
        spreads = sorted(r["m_spread"] for r in held if r.get("m_spread", -1) >= 0)
        spread = f"{spreads[len(spreads) // 2]:.2f}" if spreads else "-"
        jumps = [r["m_cross"] for r in rows if r.get("m_cross", -1) >= 0]
        jump = f"{max(jumps):.2f}" if jumps else "-"
        # how smooth it opens and closes: the fastest, deg/s; the largest change of that speed in a frame
        # (a jerk: it starts moving at once)
        rates = []
        for p, q in zip(rows, rows[1:]):
            if p.get("m_spread", -1) >= 0 and q.get("m_spread", -1) >= 0 and q["t"] > p["t"]:
                rates.append((q["m_spread"] - p["m_spread"]) / (q["t"] - p["t"]) * 1000)
        rate = f"{max(abs(v) for v in rates):.0f} / {max(abs(v - u) for u, v in zip(rates, rates[1:])):.0f}" \
            if len(rates) > 1 else "-"
        lines.append(f"| {seg} | {len(a)} | {hits or '-'} | {flew} | {sway:.2f} | {off} | {spread} | {jump} | {rate} | {draw} | "
                     f"{max(r['m_fatigue'] for r in rows):.2f} | {'yes' if any(r['m_holding'] > 0 for r in rows) else '-'} |")
    if not lines:
        return []
    return ["## Manual aim", "",
            "| segment | shots | hits | flew m | sway RMS deg (held) | off p50 / p90 deg (held) | crosshair open deg (held) | crosshair jump max % screen | crosshair speed max / its step deg/s | draw s | fatigue max | breath held |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"] + lines + [""]


def hit_zones(out):
    """the hit zones fitted to the bodies (`zones` with `zones fit on|off`): of each zone, the points of the
    body in it, in another zone only; by the body's mesh, with the guess and fitted"""
    path = os.path.join(out, "game.log")
    if not os.path.exists(path):
        return []
    fit, table = "on", {}
    for l in open(path, encoding="utf-8", errors="replace"):
        m = re.search(r"\[bench\] zones fit (on|off)", l)
        if m:
            fit = m.group(1)
            continue
        if "coverage (points" not in l:
            continue
        body = re.search(r" body (\S+)\s*$", l)
        name = body.group(1) if body else "?"
        zones = {z: (int(n), int(o), int(w)) for z, n, o, w in
                 re.findall(r"(head|body|limb) (\d+): (\d+)% / (\d+)% / \d+%", l)}
        fitted = re.search(r"(\d+ of \d+) capsules fitted", l)
        table.setdefault(name, {})[fit] = (zones, fitted.group(1) if fitted else "-")
    if not table:
        return []
    lines = ["## Hit zones", "",
             "Of each zone: the points of the body in it, guessed -> fitted (in another zone only, fitted).", "",
             "| body | head | trunk | limbs | fitted capsules |", "|---|---|---|---|---|"]
    for name, v in table.items():
        def cell(z):
            a = v.get("off", ({}, ""))[0].get(z)
            b = v.get("on", ({}, ""))[0].get(z)
            if a is None and b is None:
                return "-"
            s = (f"{a[1]}%" if a else "?") + " -> " + (f"{b[1]}%" if b else "?")
            return s + (f" ({b[2]}%)" if b and b[2] >= 5 else "")
        lines.append(f"| {name} | {cell('head')} | {cell('body')} | {cell('limb')} | {v.get('on', ({}, '-'))[1]} |")
    return lines + [""]


def sheet(video, frames, path, cols=4, width=400):
    """tile the given frame numbers of a video into one image"""
    if not frames:
        return None
    expr = "+".join(f"eq(n\\,{f})" for f in frames)
    rows = math.ceil(len(frames) / cols)
    vf = f"select='{expr}',scale={width}:-1,tile={cols}x{rows}:padding=4:color=black"
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", video, "-vf", vf, "-frames:v", "1", "-fps_mode", "vfr", path]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"ffmpeg failed for {path}: {res.stderr.strip()[:300]}", file=sys.stderr)
        return None
    return path


def spread(a, b, n):
    if b - a + 1 <= n:
        return list(range(a, b + 1))
    return sorted({a + round(i * (b - a) / (n - 1)) for i in range(n)})


def report(out):
    segs = load(out)
    os.makedirs(os.path.join(out, "sheets"), exist_ok=True)
    lines = [f"# Bench report: {os.path.basename(out)}", ""]
    summary = {"segments": {}, "events": []}
    all_events = []
    table = []

    for seg, rows in segs.items():
        stats, events = analyze(rows)
        video = os.path.join(out, seg + ".mp4")
        has_video = os.path.exists(video)
        overview = None
        if has_video and rows:
            n = len(rows)
            overview = sheet(video, spread(0, n - 1, 16), os.path.join(out, "sheets", f"{seg}.png"))
        summary["segments"][seg] = {**stats, "video": video if has_video else None, "overview": overview,
                                    "events": len(events)}
        for ev in events:
            ev["seg"] = seg
        all_events += events

        counts = defaultdict(int)
        for ev in events:
            counts[ev["flag"]] += 1
        table.append((seg, stats, ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))))

    lines.append("| segment | frames | speed cm/s | IK on % | legs hidden % | contact p90 / max cm | spread cm | limp cm | gaze on % (looks, why) | gaze err p90 deg | lean roll / pitch deg | anim pop p99 / max cm (switches: n, max) | aim err p90 / bend deg | lips corr | hits, peak deg, back s | gestures talk / listen % (started, over one, stopped) | blinks /min (in a pause, at a shift) | events |")
    lines.append("|" + "---|" * 18)
    for seg, st, ev in table:
        still = " (still)" if st["speed"] < 15 else ""
        p90 = st.get("contact_p90")
        mx = st.get("contact_max")
        contact = "-" if p90 is None else f"{p90:.1f} / {mx:.1f}"
        limp = "-" if "limp" not in st else f"{st['limp']:.1f}"
        gap = "-" if "spread" not in st else f"{st['spread']:.1f}"
        gz = "-" if "gaze_p90" not in st else f"{st['gaze_p90']:.1f}"
        gon = "-" if "gaze_on" not in st else \
            f"{st['gaze_on'] * 100:.0f} ({st['gaze_looks']}{', ' + ' '.join(st['gaze_src']) if st['gaze_src'] else ''})"
        ln = "-" if "lean_roll" not in st else \
            f"{st['lean_roll']:.1f} / {st['lean_pitch'][0]:.1f}..{st['lean_pitch'][1]:.1f}"
        an = "-" if "anim_p99" not in st else \
            f"{st['anim_p99']:.1f} / {st['anim_max']:.1f} ({st['anim_switches']}, {st['anim_switch_max']:.1f})"
        am = "-" if "aim_p90" not in st else f"{st['aim_p90']:.1f} / {st['aim_bend']:.0f}"
        lc = "-" if "lip_corr" not in st else f"{st['lip_corr']:.2f}"
        gl = "-" if st.get("ges_listen") is None else f"{st['ges_listen'] * 100:.0f}"
        gs = "-" if "ges_talk" not in st else \
            f"{st['ges_talk'] * 100:.0f} / {gl} ({st['ges_starts']}, {st['ges_cuts']}, {st['ges_stops']})" + \
            ("" if "ges_nods" not in st else f" nods {st['ges_nods']}")
        if "ges_lnods" in st:
            gs = ("" if gs == "-" else gs + "; ") + f"listening: nods {st['ges_lnods']}, eyes aside {st['ges_aside']:.1f}"
        bk = "-" if "blink_rate" not in st else \
            f"{st['blink_rate']:.0f} ({st['blink_pause']}, {st['blink_shift']})"
        fl = "-" if "flinch_hits" not in st else \
            f"{st['flinch_hits']}, {st['flinch_peak']:.1f}, {st['flinch_settle']:.2f}"
        lines.append(f"| {seg} | {st['frames']} | {st['speed']:.0f}{still} | {st['ik_on'] * 100:.0f} | "
                     f"{st['hidden'] * 100:.0f} | {contact} | {gap} | {limp} | {gon} | {gz} | {ln} | {an} | {am} | {lc} | {fl} | {gs} | {bk} | {ev or '-'} |")
    lines.append("")
    lines.append(f"Overview sheets (16 frames each): {os.path.join(out, 'sheets', '<segment>.png')}")
    lines.append("")
    lines += manual_aim(out, segs)
    lines += hit_zones(out)

    all_events.sort(key=lambda e: -e["severity"])
    lines.append("## Worst events")
    for i, ev in enumerate(all_events[:TOP_EVENTS]):
        thr, unit, what = FLAGS[ev["flag"]]
        video = os.path.join(out, ev["seg"] + ".mp4")
        path = None
        if os.path.exists(video):
            frames = spread(max(0, ev["start"] - 6), ev["end"] + 6, 16)
            path = sheet(video, frames, os.path.join(out, "sheets", f"ev{i:02d}-{ev['seg']}-{ev['flag']}-{ev['at']}.png"))
        ev["sheet"] = path
        where = f"bone={ev['leg']} [{ev.get('layers', '-')}]" if ev["flag"] == "anim_pop" else f"leg={ev['leg']}"
        lines.append(f"{i:2d}. {ev['seg']} {ev['flag']} {where} frames {ev['start']}-{ev['end']} "
                     f"worst {ev['worst']:.1f} {unit} at {ev['at']} ({what})" + (f"\n    {path}" if path else ""))
    if not all_events:
        lines.append("none")
    summary["events"] = all_events

    with open(os.path.join(out, "report.md"), "w") as fo:
        fo.write("\n".join(lines) + "\n")
    with open(os.path.join(out, "report.json"), "w") as fo:
        json.dump(summary, fo, indent=1)
    print("\n".join(lines))


def compare(a, b, first=None, last=None):
    """a and b side by side: the same scenario before and after a change, or IK off and on"""
    name = lambda v: os.path.basename(os.path.splitext(v)[0])
    base = os.path.join(os.path.dirname(b), f"{name(a)}-vs-{name(b)}")
    trim = ""
    if first is not None:
        trim = f"trim=start_frame={first}:end_frame={last + 1},setpts=PTS-STARTPTS,"
    vf = f"[0:v]{trim}scale=640:-2[a];[1:v]{trim}scale=640:-2[b];[a][b]hstack=inputs=2"
    video = base + ".mp4"
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", a, "-i", b, "-filter_complex", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", video]
    if subprocess.run(cmd).returncode != 0:
        print("ffmpeg failed")
        return
    n = int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                            "stream=nb_read_frames", "-of", "csv=p=0", video], capture_output=True, text=True).stdout.strip() or 0)
    print(video)
    print(sheet(video, spread(0, max(0, n - 1), 8), base + ".png", cols=2, width=800) or "sheet failed")


def diff(a, b):
    """event counts and contact error of two runs of the same scenario"""
    sa, sb = load(a), load(b)
    total = defaultdict(lambda: [0, 0])
    print("| segment | contact p90/max a | contact p90/max b | events a -> b |")
    print("|---|---|---|---|")
    for seg in sa:
        if seg not in sb:
            continue
        res = []
        for rows in (sa[seg], sb[seg]):
            st, ev = analyze(rows)
            cnt = defaultdict(int)
            for e in ev:
                cnt[e["flag"]] += 1
            res.append((st, cnt))
        flags = sorted(set(res[0][1]) | set(res[1][1]))
        for f in flags:
            total[f][0] += res[0][1][f]
            total[f][1] += res[1][1][f]
        changes = ", ".join(f"{f} {res[0][1][f]}->{res[1][1][f]}" for f in flags if res[0][1][f] != res[1][1][f])

        def c(st):
            ret = "-" if "contact_p90" not in st else f"{st['contact_p90']:.1f}/{st['contact_max']:.1f}"
            ret += "" if "spread" not in st else f" spread {st['spread']:.1f}"
            ret += "" if "anim_p99" not in st else \
                f" pop {st['anim_p99']:.1f}/{st['anim_max']:.1f} sw {st['anim_switch_max']:.1f}"
            ret += "" if "ges_talk" not in st else \
                f" gest {st['ges_talk'] * 100:.0f}/{'-' if st['ges_listen'] is None else round(st['ges_listen'] * 100)}%" \
                f" n{st['ges_starts']} over{st['ges_cuts']}"
            return ret + ("" if "limp" not in st else f" limp {st['limp']:.1f}")
        print(f"| {seg} | {c(res[0][0])} | {c(res[1][0])} | {changes or 'same'} |")
    print()
    print("total: " + ", ".join(f"{f} {v[0]}->{v[1]}" for f, v in sorted(total.items())))


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "diff":
        diff(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "compare":
        rng = [int(x) for x in sys.argv[4:6]]
        compare(sys.argv[2], sys.argv[3], *(rng if len(rng) == 2 else []))
        return
    if len(sys.argv) >= 2 and sys.argv[1] == "frames":
        out, seg, a, b = sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
        step = int(sys.argv[6]) if len(sys.argv) > 6 else 1
        frames = list(range(a, b + 1, step))
        path = os.path.join(out, "sheets", f"{seg}-{a}-{b}-{step}.png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        print(sheet(os.path.join(out, seg + ".mp4"), frames, path) or "failed")
        return
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    report(sys.argv[1])


if __name__ == "__main__":
    main()
