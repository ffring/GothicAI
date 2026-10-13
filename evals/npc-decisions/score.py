"""preds/*.jsonl + gold.json -> results.md (markdown tables) and a summary on stdout.

    cd evals/npc-decisions && python3 score.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
CONF_GATE = 0.9  # act automatically only above this confidence
CONDS = ("en", "ru", "en_rev")
THRESHOLDS = (0.3, 0.5, 0.7, 0.9)


def argmax(probs: dict | list):
    if isinstance(probs, dict):
        return max(probs, key=probs.get)
    return max(range(len(probs)), key=probs.__getitem__)


def confidence(probs: dict) -> float:
    """Normalised max-probability, as the System One API defines it for choice."""
    p = list(probs.values())
    n = len(p)
    return max(0.0, (n * max(p) - 1) / (n - 1)) if n > 1 else 1.0


def pct(num: int, den: int) -> str:
    return f"{100 * num / den:.0f}%" if den else "—"


def score_rows(rows: list[dict], gold: dict) -> dict:
    s = defaultdict(int)
    for r in rows:
        g, a = gold[r["id"]], r["answers"]
        pick = argmax(a["react"]["probs"])
        s["n"] += 1
        s["react_gold"] += pick == g["react"][0]
        s["react_ok"] += pick in g["react"]
        conf = confidence(a["react"]["probs"])
        if conf >= CONF_GATE:
            s["gated"] += 1
            s["gated_ok"] += pick in g["react"]
        s["think"] += (a["think"]["p_true"] > 0.5) == g["think"]
        s["think_pos"] += g["think"]
        s["think_tp"] += g["think"] and a["think"]["p_true"] > 0.5
        s["think_fp"] += (not g["think"]) and a["think"]["p_true"] > 0.5
        lvl = argmax(a["attitude"]["probs"])
        s["att_ok"] += lvl in g["attitude"]
        s["att_near"] += abs(lvl - g["attitude"][0]) <= 1
        if "extra" in g:
            s["extra_n"] += 1
            s["extra_ok"] += argmax(a["extra"]["probs"]) in g["extra"]
        for tag in g["tags"]:
            s[f"tag_n:{tag}"] += 1
            s[f"tag_ok:{tag}"] += pick in g["react"]
    return s


def main() -> None:
    gold = json.loads((HERE / "gold.json").read_text())
    preds = {p.stem: [json.loads(x) for x in p.open()] for p in sorted((HERE / "preds").glob("*.jsonl"))}
    # a model's answers to requests.jsonl; other files there (the day's probe and labels) are not
    preds = {m: rows for m, rows in preds.items() if rows and rows[0].get("id") in gold}
    models = sorted(preds)
    metas = {}
    for m in models:
        mp = HERE / "preds" / f"{m}.meta.json"
        metas[m] = json.loads(mp.read_text()) if mp.exists() else {}

    out = ["# Результаты", ""]
    out += [
        f"`react ✓` — выбран эталон или приемлемый вариант; `react =` — ровно эталон; "
        f"`≥{CONF_GATE}` — ответы с уверенностью от {CONF_GATE}: сколько их и сколько из них приемлемы; "
        "`think` — точность «звать ли большую модель» (в скобках: найдено случаев «да» / ложных «да»); "
        "`attitude ✓` — уровень в приемлемых, `±1` — не дальше одного уровня; `extra` — выбор параметра.",
        "",
        "| модель | условие | react ✓ | react = | ≥0.9: доля / верно | think | attitude ✓ | ±1 | extra |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    tags = sorted({t for g in gold.values() for t in g["tags"]})
    by_tag = {}
    for m in models:
        for cond in CONDS:
            rows = [r for r in preds[m] if r["cond"] == cond]
            if not rows:
                continue
            s = score_rows(rows, gold)
            n = s["n"]
            neg = n - s["think_pos"]
            out.append(
                f"| {m} | {cond} | {pct(s['react_ok'], n)} | {pct(s['react_gold'], n)} "
                f"| {pct(s['gated'], n)} / {pct(s['gated_ok'], s['gated'])} "
                f"| {pct(s['think'], n)} ({s['think_tp']}/{s['think_pos']}, {s['think_fp']}/{neg}) "
                f"| {pct(s['att_ok'], n)} | {pct(s['att_near'], n)} | {s['extra_ok']}/{s['extra_n']} |"
            )
            if cond == "en":
                by_tag[m] = s

    out += ["", "## Порог уверенности для react (условие en)", "",
            "Ниже порога сервер не вмешивается: NPC продолжает распорядок, то есть ответ считается `continue`. "
            "`охват` — доля ситуаций, где модель уверена; `верно среди уверенных`; "
            "`итог` — доля приемлемых действий с учётом отката на `continue`.", "",
            "| модель | " + " | ".join(f"≥{t}: охват / верно / итог" for t in THRESHOLDS) + " |",
            "|---|" + "---|" * len(THRESHOLDS)]
    for m in models:
        rows = [r for r in preds[m] if r["cond"] == "en"]
        if not rows or m.startswith("gpt"):
            continue
        cells = []
        for t in THRESHOLDS:
            sure = [r for r in rows if confidence(r["answers"]["react"]["probs"]) >= t]
            ok_sure = sum(argmax(r["answers"]["react"]["probs"]) in gold[r["id"]]["react"] for r in sure)
            unsure_ok = sum("continue" in gold[r["id"]]["react"] for r in rows if r not in sure)
            cells.append(f"{pct(len(sure), len(rows))} / {pct(ok_sure, len(sure))} / "
                         f"{pct(ok_sure + unsure_ok, len(rows))}")
        out.append(f"| {m} | " + " | ".join(cells) + " |")

    out += ["", "## think: можно ли подобрать порог (условие en)", "",
            "AUROC — насколько вероятность «да» отделяет нужные случаи от ненужных (0,5 — случайно, 1 — идеально); "
            "`лучший порог` подобран на этом же наборе, поэтому оценка оптимистичная.", "",
            "| модель | AUROC | лучший порог | точность при нём |", "|---|---|---|---|"]
    for m in models:
        rows = [r for r in preds[m] if r["cond"] == "en"]
        if not rows or m.startswith("gpt"):
            continue
        ys = [(r["answers"]["think"]["p_true"], gold[r["id"]]["think"]) for r in rows]
        pos = [p for p, y in ys if y]
        neg = [p for p, y in ys if not y]
        auroc = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))
        best = max(sorted({p for p, _ in ys}), key=lambda t: sum((p >= t) == y for p, y in ys))
        acc = sum((p >= best) == y for p, y in ys)
        out.append(f"| {m} | {auroc:.2f} | {best:.3f} | {pct(acc, len(ys))} |")

    out += ["", "## Порядок вариантов", "",
            "Доля ситуаций, где модель выбрала тот же `react` при обратном порядке вариантов (en против en_rev).", "",
            "| модель | совпадение |", "|---|---|"]
    for m in models:
        en = {r["id"]: argmax(r["answers"]["react"]["probs"]) for r in preds[m] if r["cond"] == "en"}
        rev = {r["id"]: argmax(r["answers"]["react"]["probs"]) for r in preds[m] if r["cond"] == "en_rev"}
        common = en.keys() & rev.keys()
        if common:
            out.append(f"| {m} | {pct(sum(en[i] == rev[i] for i in common), len(common))} |")

    out += ["", "## react ✓ по типам ситуаций (условие en)", "",
            "| модель | " + " | ".join(f"{t} ({by_tag[models[0]][f'tag_n:{t}']})" for t in tags) + " |",
            "|---|" + "---|" * len(tags)]
    for m in models:
        if m in by_tag:
            s = by_tag[m]
            out.append(f"| {m} | " + " | ".join(pct(s[f"tag_ok:{t}"], s[f"tag_n:{t}"]) for t in tags) + " |")

    out += ["", "## Задержка и память", "",
            "| модель | запрос p50 | p95 | пик памяти | загрузка |", "|---|---|---|---|---|"]
    for m in models:
        mt = metas[m]
        if "ms_p50" in mt:
            out.append(f"| {m} | {mt['ms_p50']} мс | {mt['ms_p95']} мс | {mt['peak_alloc_gb']} ГБ | {mt['load_s']} с |")
        elif "call_ms_p50" in mt:
            out.append(f"| {m} | {mt['call_ms_p50']} мс на вызов из {mt['batch']} ситуаций | — | — | — |")

    # where gpt and the deciders disagree with gold, for manual review
    out += ["", "## Ошибки react (условие en)", "", "| ситуация | эталон | " + " | ".join(models) + " |",
            "|---|---|" + "---|" * len(models)]
    picks = {m: {r["id"]: argmax(r["answers"]["react"]["probs"]) for r in preds[m] if r["cond"] == "en"}
             for m in models}
    for sid, g in gold.items():
        row = [picks[m].get(sid, "") for m in models]
        if any(p and p not in g["react"] for p in row):
            cells = [p if p in g["react"] else f"**{p}**" for p in row]
            out.append(f"| {sid} | {'/'.join(g['react'])} | " + " | ".join(cells) + " |")

    (HERE / "results.md").write_text("\n".join(out) + "\n")
    print("\n".join(out[:12 + len(models) * 3]))


if __name__ == "__main__":
    main()
