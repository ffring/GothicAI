# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""situations.yaml -> requests.jsonl (System One requests) + gold.json.

Conditions:
  en      state in English (hero lines in Russian), as planned for production;
  ru      the same state fully in Russian;
  en_rev  as `en`, but options of every choice question in reverse order (position bias check).
Questions are always English: the server owns their wording.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

HERE = Path(__file__).parent
CONDITIONS = ("en", "ru", "en_rev")


def questions(sit: dict, common: dict, reverse: bool) -> dict:
    def order(options: dict) -> dict:
        items = list(options.items())
        return dict(reversed(items) if reverse else items)

    qs = {
        "react": {
            "type": "choice",
            "instructions": common["react"]["instructions"],
            "criteria": order(sit["react"]["options"]),
        },
        "think": {
            "type": "noul",
            "instructions": common["think"]["instructions"].strip(),
            "criteria": {k: v.strip() for k, v in common["think"]["criteria"].items()},
        },
        "attitude": {
            "type": "score",
            "instructions": common["attitude"]["instructions"],
            "criteria": list(common["attitude"]["levels"]),
        },
    }
    if "extra" in sit:
        qs["extra"] = {
            "type": "choice",
            "instructions": sit["extra"]["instructions"],
            "criteria": order(sit["extra"]["options"]),
        }
    return qs


def main() -> None:
    data = yaml.safe_load((HERE / "situations.yaml").read_text())
    common, sits = data["questions"], data["situations"]
    ids = [s["id"] for s in sits]
    assert len(ids) == len(set(ids)), "duplicate ids"

    gold = {}
    with (HERE / "requests.jsonl").open("w") as out:
        for s in sits:
            for opt_q in ("react", "extra"):
                if opt_q in s:
                    q = s[opt_q]
                    assert q["gold"] in q["options"], (s["id"], opt_q)
                    assert set(q.get("ok", [])) <= set(q["options"]), (s["id"], opt_q)
            gold[s["id"]] = {
                "tags": s.get("tags", []),
                "react": [s["react"]["gold"], *s["react"].get("ok", [])],
                "think": bool(s["think"]),
                "attitude": [s["attitude"]["gold"], *s["attitude"].get("ok", [])],
                **(
                    {"extra": [s["extra"]["gold"], *s["extra"].get("ok", [])]}
                    if "extra" in s
                    else {}
                ),
            }
            for cond in CONDITIONS:
                state = s["state_ru" if cond == "ru" else "state_en"].strip()
                req = {"id": s["id"], "cond": cond, "state": state,
                       "questions": questions(s, common, reverse=cond == "en_rev")}
                out.write(json.dumps(req, ensure_ascii=False) + "\n")
    (HERE / "gold.json").write_text(json.dumps(gold, ensure_ascii=False, indent=1))
    print(f"{len(sits)} situations x {len(CONDITIONS)} conditions -> requests.jsonl, gold.json")


if __name__ == "__main__":
    main()
