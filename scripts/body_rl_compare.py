#!/usr/bin/env python3
"""Stage 9: paired comparison of RL collector evaluations on the same held-out scenarios.

Usage: body_rl_compare.py NAME=DIR [NAME=DIR ...]; the first is the baseline. Per run: wins,
losses, HP, duration of wins, decisions per duel, score and the main action shares. Per pair with
the baseline: discordant wins with an exact McNemar p and 95% intervals of the HP and score deltas.
A run against a self-play opponent (stage 9.3) also prints its head-to-head share of wins per
opponent and learner seat, with a 95% Wilson interval.
"""
import glob, json, math, statistics as st, sys
def load(d):
    out = {}
    for p in glob.glob(d+"/worker-*/episodes.jsonl"):
        for l in open(p):
            x = json.loads(l); out[(x["seed"], x["side"], x["distance_cm"])] = x
    return out
def mc(a, b):
    n = a+b; k = min(a, b)
    return min(1, 2*sum(math.comb(n, i) for i in range(k+1))/2**n) if n else 1.0
runs = {name: load(path) for name, path in (arg.split("=") for arg in sys.argv[1:])}
names = list(runs)
for name, r in runs.items():
    v = list(r.values()); w = [x for x in v if x["reason"] == "opponent_down"]; dec = sum(x["decisions"] for x in v)
    acts = {k: round(sum(x.get(f"action_{k}", 0) for x in v)/dec*100, 1) for k in (1, 4, 6, 9, 10)}
    print(f"{name:10s} n={len(v)} wins={len(w)} losses={len(v)-len(w)} hp={st.mean(x['primary_hp'] for x in v):.1f} "
          f"win_s={st.mean(x['elapsed_game_ms'] for x in w)/1000:.2f} dec/duel={dec/len(v):.1f} score={st.mean(x['score'] for x in v):.3f} actions%={acts}")
def wilson(k, n):
    if n == 0: return (0.0, 1.0)
    z, p = 1.96, k/n; c = (p+z*z/(2*n))/(1+z*z/n); h = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return (c-h, c+h)
for name, r in runs.items():
    groups = {}
    for x in r.values():
        if x.get("opponent", "native") != "native":
            groups.setdefault((x["opponent"], x.get("learner_seat", "primary")), []).append(x["reason"] == "opponent_down")
    for (opponent, seat), wins in sorted(groups.items()):
        low, high = wilson(sum(wins), len(wins))
        print(f"{name:10s} vs {opponent} as {seat}: {sum(wins)}/{len(wins)} = {sum(wins)/len(wins):.3f} [{low:.3f}, {high:.3f}]")
base = names[0]
for name in names[1:]:
    A, B = runs[base], runs[name]; k = sorted(set(A) & set(B)); win = lambda x: x["reason"] == "opponent_down"
    oa = sum(win(A[i]) and not win(B[i]) for i in k); ob = sum(win(B[i]) and not win(A[i]) for i in k)
    d = [B[i]["primary_hp"]-A[i]["primary_hp"] for i in k]; s = [B[i]["score"]-A[i]["score"] for i in k]
    se = lambda v: 1.96*st.stdev(v)/math.sqrt(len(v))
    print(f"{base} -> {name}: only {base} {oa}, only {name} {ob}, McNemar p={mc(oa, ob):.4f}, dHP={st.mean(d):+.1f}±{se(d):.1f}, dscore={st.mean(s):+.3f}±{se(s):.3f}")
