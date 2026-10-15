#!/usr/bin/env python3
"""Stage 9: RL collector throughput on the server host for a given worker count.

Usage on the server: body_collect_scale_probe.py WORKERS EPISODES OUTPUT [collector flags...]
Runs scripts/body_rl_collect.py (BC GRU, sampled duels from seed 90000) and samples once a
second the host CPU busy share, MemAvailable and the CPU time of the collector's process tree
split into runners and Python. Stops the run if available memory falls under 1.5 GB.
Measurement only: no quality credit.
"""
import json, os, signal, subprocess, sys, time
from pathlib import Path

HOME = Path.home()
TICK = os.sysconf("SC_CLK_TCK")


def cpu_busy():
    fields = list(map(int, open("/proc/stat").readline().split()[1:]))
    idle = fields[3]+fields[4]
    return sum(fields)-idle, sum(fields)


def mem_available_mb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return int(line.split()[1])//1024


def processes(root):
    tree, out = {}, {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            stat = open(f"/proc/{pid}/stat").read()
            comm = stat[stat.index("(")+1:stat.rindex(")")]
            rest = stat[stat.rindex(")")+2:].split()
            ppid, utime, stime = int(rest[1]), int(rest[11]), int(rest[12])
            rss = int(open(f"/proc/{pid}/statm").read().split()[1])*4096//1024//1024
            tree[int(pid)] = (ppid, comm, (utime+stime)/TICK, rss)
        except (OSError, ValueError, IndexError):
            continue
    keep = {root}
    changed = True
    while changed:
        changed = False
        for pid, (ppid, *_rest) in tree.items():
            if ppid in keep and pid not in keep:
                keep.add(pid)
                changed = True
    return {pid: tree[pid] for pid in keep if pid in tree}


workers, episodes, out, extra = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3]), sys.argv[4:]
source = HOME/"gothic-ai/body/source"
command = [str(HOME/"gothic-ai/body/.venv/bin/python"), str(source/"scripts/body_rl_collect.py"),
           "--runner", str(HOME/"gothic-ai/body-api/build/opengothic/gothic-body-runner"),
           "--assets", str(HOME/"gothic-ai/body-baseline/assets"),
           "--save", str(HOME/"gothic-ai/body-baseline/runs/melee-reference-stage1-v4/run/save_slot_1.sav"),
           "--reference", str(HOME/"gothic-ai/body-baseline/runs/melee-reference-stage1-v4"),
           "--scenario", str(source/"scripts/bench/body-arena.bench"),
           "--checkpoint", str(HOME/"gothic-ai/body/data/stage8-defence-bc-v2/models/gru/best.pt"),
           "--output", str(out), "--workers", str(workers), "--episodes", str(episodes),
           "--seed-start", "90000", "--temperature", "1.0", *extra]
env = {**os.environ, "PYTHONPATH": f"{source}/body-service/src:{source}/scripts"}
busy0, total0 = cpu_busy()
started = time.monotonic()
process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
cpu, samples, min_available, peak_rss = {}, [], mem_available_mb(), 0
last = (busy0, total0)
while process.poll() is None:
    time.sleep(1)
    tree = processes(process.pid)
    for pid, (_ppid, comm, seconds, _rss) in tree.items():
        cpu[pid] = (comm, max(seconds, cpu.get(pid, ("", 0))[1]))
    peak_rss = max(peak_rss, sum(rss for *_x, rss in tree.values()))
    busy, total = cpu_busy()
    samples.append((busy-last[0])/max(1, total-last[1]))
    last = (busy, total)
    min_available = min(min_available, mem_available_mb())
    if min_available < 1500:
        process.send_signal(signal.SIGTERM)
        print("aborted: memory", flush=True)
wall = time.monotonic()-started
busy1, total1 = cpu_busy()
summary = json.loads((out/"summary.json").read_text()) if (out/"summary.json").exists() else {}
roles = {}
for comm, seconds in cpu.values():
    role = "runner" if comm.startswith("gothic-body") else "python" if comm.startswith("python") else comm
    roles[role] = roles.get(role, 0)+seconds
steady = samples[3:-3] if len(samples) > 8 else samples
print(json.dumps({"workers": workers, "episodes": summary.get("episodes"), "wall_s": round(wall, 1),
                  "episodes_per_min": round(summary.get("episodes_per_min", 0), 1),
                  "decisions_per_s": round(summary.get("decisions_per_s", 0), 1),
                  "host_busy_mean": round((busy1-busy0)/(total1-total0), 3),
                  "host_busy_steady": round(sum(steady)/max(1, len(steady)), 3),
                  "cpu_s_by_role": {key: round(value, 1) for key, value in roles.items()},
                  "peak_tree_rss_gb": round(peak_rss/1024, 1), "min_mem_available_gb": round(min_available/1024, 1)}), flush=True)
