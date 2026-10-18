#!/usr/bin/env python3
"""CPU profiles and realtime timing of the headless runner step.

A step runs from the tick's due time (the previous response's
next_tick_wall_monotonic_ns) to its observation sample (observer-timing
wall_sample_us). CPU samples use CLOCK_MONOTONIC, the runner's steady_clock.
The first second is sim_step <= 47. ITIMER_PROF samples CPU time, so sleeps
between ticks add no cost.

Use profile to symbolize body-runner-cpu-sample.c records and compare CPU ms
per step, tree to inspect children or callers in the resulting folded stacks,
and window to summarize realtime duel steps, command windows and rejections.
"""

import argparse
import bisect
import collections
import glob
import json
import os
from pathlib import Path
import re
import struct
import subprocess

MAGIC = 0x50524f46


def read_maps(path):
    rows, base = [], {}
    for line in open(path):
        parts = line.split()
        if len(parts) < 6 or not parts[5].startswith("/"):
            continue
        start, end = (int(x, 16) for x in parts[0].split("-"))
        offset = int(parts[2], 16)
        if offset == 0:
            base[parts[5]] = min(base.get(parts[5], start), start)
        if "x" in parts[1]:
            rows.append((start, end, parts[5]))
    rows.sort()
    return rows, base


def read_samples(path):
    data = open(path, "rb").read()
    i, out = 0, []
    while i + 16 <= len(data):
        magic, n, ns = struct.unpack_from("<IIQ", data, i)
        if magic != MAGIC:
            raise ValueError(f"bad record at {i} in {path}")
        frames = struct.unpack_from(f"<{n}Q", data, i + 16)
        out.append((ns, frames))
        i += 16 + 8 * n
    return out


def step_intervals(duels):
    """[(due_ns, sample_ns, early)] for every measured step of every duel dir."""
    out = []
    for d in sorted(glob.glob(os.path.join(duels, "rt*-*-gru"))):
        samples = {}
        for line in open(os.path.join(d, "observer-timing.jsonl")):
            r = json.loads(line)
            if r.get("kind") == "sample_step":
                samples[r["sim_step"]] = r["wall_sample_us"] * 1000
        due = {}
        for line in open(os.path.join(d, "ipc.jsonl")):
            r = json.loads(line)
            v = r["value"]
            if r["direction"] == "response" and v.get("next_tick_wall_monotonic_ns") and v.get("sim_step") is not None:
                due[v["sim_step"] + 1] = v["next_tick_wall_monotonic_ns"]
        for step, t in samples.items():
            if step in due and t >= due[step]:
                out.append((due[step], t, step <= 47))
    out.sort()
    return out


class Symbolizer:
    def __init__(self):
        self.cache = {}

    def resolve(self, binary, vaddrs):
        todo = sorted({a for a in vaddrs if (binary, a) not in self.cache})
        if not todo:
            return
        name = os.path.basename(binary)
        proc = subprocess.run(["addr2line", "-a", "-f", "-C", "-i", "-e", binary],
                              input="\n".join(hex(a) for a in todo), capture_output=True, text=True)
        current, chain = None, []
        lines = proc.stdout.splitlines()
        k = 0
        while k < len(lines):
            line = lines[k]
            if re.fullmatch(r"0x[0-9a-f]+", line):
                if current is not None:
                    self.cache[(binary, current)] = chain
                current, chain = int(line, 16), []
                k += 1
                continue
            func = line
            k += 2  # function, then file:line
            if func == "??":
                func = f"{name}+?"
            chain.append(func)
        if current is not None:
            self.cache[(binary, current)] = chain
        for a in todo:
            self.cache.setdefault((binary, a), [f"{name}+?"])

    def chain(self, binary, vaddr):
        return self.cache[(binary, vaddr)]


def short(func):
    func = re.sub(r"\(anonymous namespace\)::", "", func)
    func = re.sub(r"\[abi:[^\]]*\]", "", func)
    depth, out = 0, []
    for ch in func:  # drop argument lists and template arguments
        if ch in "(<":
            depth += 1
        elif ch in ")>":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out).strip() or func


def profile(directory, duels, period_us, sym):
    intervals = step_intervals(duels)
    starts = [a for a, _, _ in intervals]
    early_steps = sum(e for _, _, e in intervals)
    later_steps = len(intervals) - early_steps
    raw = []
    for binfile in sorted(glob.glob(os.path.join(directory, "*.bin"))):
        maps_file = binfile[:-4] + ".maps"
        if not os.path.exists(maps_file) or os.path.getsize(binfile) == 0:
            continue
        rows, base = read_maps(maps_file)
        row_starts = [r[0] for r in rows]
        for ns, frames in read_samples(binfile):
            k = bisect.bisect_right(starts, ns) - 1
            if k < 0 or ns > intervals[k][1]:
                continue
            resolved = []
            for depth, addr in enumerate(frames):
                j = bisect.bisect_right(row_starts, addr) - 1
                if j < 0 or addr >= rows[j][1]:
                    resolved.append(("?", 0))
                    continue
                binary = rows[j][2]
                resolved.append((binary, addr - base.get(binary, 0)))
            raw.append((intervals[k][2], resolved))
    by_binary = collections.defaultdict(set)
    for _, resolved in raw:
        for depth, (binary, vaddr) in enumerate(resolved):
            if binary != "?":
                by_binary[binary].update((vaddr, vaddr - 1))
    for binary, vaddrs in by_binary.items():
        sym.resolve(binary, vaddrs)
    stats = {}
    for label, want in (("all", None), ("first_second", True), ("later", False)):
        inclusive, self_cost, folded = collections.Counter(), collections.Counter(), collections.Counter()
        pc_cost, group_cost = collections.Counter(), collections.Counter()
        n = 0
        for early, resolved in raw:
            if want is not None and early != want:
                continue
            stack = []  # innermost first
            # frames: the handler (libcpusample.so), the signal trampoline, the interrupted pc, return addresses
            first = 0
            while first < len(resolved) and resolved[first][0].endswith("libcpusample.so"):
                first += 1
            pc_func = "?"
            for depth, (binary, vaddr) in enumerate(resolved[first + 1:]):
                if binary == "?":
                    stack.append("?")
                    continue
                chain = sym.chain(binary, vaddr if depth == 0 else vaddr - 1)
                if depth == 0:
                    pc_func = short(chain[-1])  # the real (non-inlined) function at the pc
                stack.extend(short(f) for f in chain)
            if not stack:
                continue
            n += 1
            self_cost[stack[0]] += 1
            pc_cost[pc_func] += 1
            group = ("jolt" if pc_func.startswith("JPH::") else "bullet" if pc_func.startswith(("bt", "b3")) else
                     "libc" if pc_func.startswith(("__", "_int_", "mem", "str")) else "engine")
            group_cost[group] += 1
            for f in set(stack):
                inclusive[f] += 1
            folded[";".join(reversed(stack))] += 1
        steps = {"all": len(intervals), "first_second": early_steps, "later": later_steps}[label]
        scale = period_us / 1000 / max(steps, 1)
        stats[label] = {"steps": steps, "samples": n, "cpu_ms_per_step": round(n * scale, 3),
                        "inclusive_ms": {f: round(c * scale, 3) for f, c in inclusive.most_common()},
                        "self_ms": {f: round(c * scale, 3) for f, c in self_cost.most_common()},
                        "pc_function_ms": {f: round(c * scale, 3) for f, c in pc_cost.most_common()},
                        "group_ms": {f: round(c * scale, 3) for f, c in group_cost.most_common()},
                        "folded": dict(folded.most_common())}
        if label == "all":
            wall = sum(b - a for a, b, _ in intervals) / 1e6 / max(len(intervals), 1)
            stats[label]["wall_ms_per_step"] = round(wall, 3)
    return stats


def report_profile(args):
    sym = Symbolizer()
    result = {}
    for spec in args.profile:
        name, rest = spec.split("=", 1)
        samples, duels = rest.split(":", 1)
        result[name] = profile(samples, duels, args.period_us, sym)
    names = list(result)
    for name in names:
        s = result[name]
        print(f"{name}: steps {s['all']['steps']}, wall {s['all'].get('wall_ms_per_step')} ms/step, cpu "
              f"{s['all']['cpu_ms_per_step']} ms/step (first second {s['first_second']['cpu_ms_per_step']}, later {s['later']['cpu_ms_per_step']})")
    sec = args.section
    print(f"\n== cpu by kind of function at the pc ({sec}) ==")
    for g in ("engine", "jolt", "bullet", "libc"):
        print(f"{g:>8}" + "".join(f"{result[n][sec]['group_ms'].get(g, 0.0):9.3f}" for n in names))
    for kind in ("pc_function_ms", "inclusive_ms", "self_ms"):
        print(f"\n== {kind} per step ({sec}) ==")
        funcs = set()
        for name in names:
            funcs.update(list(result[name][sec][kind])[:args.top])
        rows = []
        for f in funcs:
            vals = [result[n][sec][kind].get(f, 0.0) for n in names]
            rows.append((vals[-1] - vals[0], vals, f))
        rows.sort(key=lambda r: -max(r[1]) if kind != "self_ms" else -abs(r[0]))
        print("".join(f"{n:>9}" for n in names) + "    delta  function")
        for delta, vals, f in rows[:args.top]:
            print("".join(f"{v:9.3f}" for v in vals) + f" {delta:+8.3f}  {f[:150]}")
    if args.json:
        json.dump(result, open(args.json, "w"), indent=1)


def q(xs, p):
    xs = sorted(xs)
    return round(xs[int(p*(len(xs)-1))], 2) if xs else None


def realtime(path):
    rows = [c for c in json.load(open(path/"suite.json"))["cases"] if c["policy"] == "gru"]

    def hp(c, who):
        f = c["final"]
        return {a["entity_id"]: a for a in f["actors"]}[f[who+"_entity_id"]]["hp"]
    won = [c for c in rows if hp(c, "opponent") <= 0 and hp(c, "primary") > 0]
    ctrl = [c["neural_control_fraction"] for c in rows if c.get("neural_control_fraction") is not None]
    stats = lambda key: sum((c["model_stats"].get(key) or 0) for c in rows)
    steps, windows, early_steps, early_windows = [], [], [], []
    for d in sorted(glob.glob(str(path/"rt*-*-gru"))):
        samples = {}
        for line in open(d+"/observer-timing.jsonl"):
            r = json.loads(line)
            if r.get("kind") == "sample_step":
                samples[r["sim_step"]] = r["wall_sample_us"]*1000
        due = {}
        for line in open(d+"/ipc.jsonl"):
            r = json.loads(line)
            v = r["value"]
            if r["direction"] == "response" and v.get("next_tick_wall_monotonic_ns") and v.get("sim_step") is not None:
                due[v["sim_step"]+1] = v["next_tick_wall_monotonic_ns"]
        for step, t in samples.items():
            early = step <= 47
            if step in due and t >= due[step]:
                (early_steps if early else steps).append((t-due[step])/1e6)
            if step+1 in due and due[step+1] > t:
                (early_windows if early else windows).append((due[step+1]-t)/1e6)
    return {"duels": len(rows), "wins": len(won), "hp_mean": round(sum(hp(c, "primary") for c in rows)/len(rows), 2),
            "no_damage": sum(hp(c, "primary") == 300 for c in rows),
            "win_s_mean": round(sum(c["final"]["elapsed_game_ms"] for c in won)/max(len(won), 1)/1000, 2),
            "accepted_commands": stats("accepted_model_commands"), "rejected_commands": stats("rejected_model_commands"),
            "duels_with_rejected": sum((c["model_stats"].get("rejected_model_commands") or 0) > 0 for c in rows),
            "neural_control_min": round(min(ctrl), 3), "duels_below_095": sum(x < .95 for x in ctrl),
            "fallbacks": stats("runtime_fallbacks"),
            "step_ms_first_second": {"p50": q(early_steps, .5), "p95": q(early_steps, .95)},
            "step_ms_later": {"p50": q(steps, .5), "p95": q(steps, .95)},
            "window_ms_first_second": {"p5": q(early_windows, .05), "p50": q(early_windows, .5)},
            "window_ms_later": {"p5": q(windows, .05), "p50": q(windows, .5)}}


def report_tree(args):
    with open(args.json) as stream:
        data = json.load(stream)
    func, sec = args.func, args.section
    depth = args.depth if args.depth is not None else (2 if args.callers else 1)
    limit = args.rows if args.rows is not None else (12 if args.callers else 25)
    out = {}
    for name, prof in data.items():
        s = prof[sec]
        scale = s["cpu_ms_per_step"] / max(s["samples"], 1)
        counts = collections.Counter()
        for stack, n in s["folded"].items():
            frames = stack.split(";")
            if func not in frames:
                continue
            i = frames.index(func)  # outermost occurrence
            if args.callers:
                key = " < ".join(reversed(frames[max(0, i-depth):i]))
            else:
                key = ";".join(frames[i+1:i+1+depth]) or "<self>"
            counts[key] += n
        out[name] = {key: count * scale for key, count in counts.items()}
    names = list(out)
    keys = set().union(*out.values())
    rows = sorted(keys, key=lambda key: -max(out[name].get(key, 0) for name in names))
    if not args.callers:
        print("".join(f"{name:>8}" for name in names), "   delta  child of", func)
    for key in rows[:limit]:
        values = [out[name].get(key, 0) for name in names]
        width = 200 if args.callers else 160
        print("".join(f"{value:8.3f}" for value in values), f"{values[-1]-values[0]:+8.3f} ", key[:width])


def report_window(args):
    print(json.dumps({spec.split("=", 1)[0]: realtime(Path(spec.split("=", 1)[1]))
                      for spec in args.profiles}, indent=1))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    cpu = commands.add_parser("profile", help="compare CPU ms per runner step",
                              description="Symbolize CPU samples inside measured runner steps and compare profiles.")
    cpu.add_argument("--profile", action="append", required=True, help="name=SAMPLES_DIR:DUELS_DIR")
    cpu.add_argument("--period-us", type=int, default=1000, help="CPU sampling period in microseconds (default: 1000)")
    cpu.add_argument("--top", type=int, default=60, help="maximum function rows (default: 60)")
    cpu.add_argument("--section", default="later", help="all, first_second or later (default: later)")
    cpu.add_argument("--json", help="write profiles, including folded stacks, to this JSON file")
    cpu.set_defaults(run=report_profile)

    tree = commands.add_parser("tree", help="inspect children or callers in folded step stacks",
                               description="Compare CPU ms per step for children or call paths of a function.")
    tree.add_argument("json", metavar="JSON", help="JSON written by the profile subcommand")
    tree.add_argument("func", metavar="FUNC", help="exact function name in the folded stacks")
    mode = tree.add_mutually_exclusive_group()
    mode.add_argument("--children", dest="callers", action="store_false", help="show children (default)")
    mode.add_argument("--callers", action="store_true", help="show call paths into the function")
    tree.add_argument("--depth", type=int, help="frames below/above the function (default: children 1, callers 2)")
    tree.add_argument("--rows", type=int, help="maximum rows (default: children 25, callers 12)")
    tree.add_argument("--section", default="later", help="all, first_second or later (default: later)")
    tree.set_defaults(run=report_tree, callers=False)

    window = commands.add_parser("window", help="summarize realtime steps, windows and rejected commands",
                                 description="Print realtime duel metrics as JSON; the first second is sim_step <= 47.")
    window.add_argument("profiles", nargs="+", metavar="name=DUELS_DIR", help="one or more named realtime duel runs")
    window.set_defaults(run=report_window)

    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
