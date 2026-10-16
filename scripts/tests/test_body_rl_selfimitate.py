"""Episode selection of filtered self-imitation, over synthetic collector summaries."""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from body_rl_selfimitate import select_episodes  # noqa: E402


def collect(root, scores, reason="opponent_down"):
    (root/"worker-00").mkdir(parents=True)
    rows = [{"worker": 0, "name": f"ep-{root.name}-{index}", "reason": reason, "score": score, "fallbacks": 0}
            for index, score in enumerate(scores)]
    (root/"worker-00/episodes.jsonl").write_text("".join(json.dumps(row)+"\n" for row in rows))
    return root


def test_overall_selection_can_leave_out_a_harder_collection(tmp_path):
    open_ = collect(tmp_path/"open", [1.4, 1.4, 1.3, 1.3])
    wall = collect(tmp_path/"wall", [1.2, 1.1, 1.0, 0.9])
    _, selected = select_episodes([open_, wall], 0.25)
    assert [row["name"] for row in selected] == ["ep-open-0", "ep-open-1"]


def test_per_collection_selection_keeps_each_share_and_its_own_files(tmp_path):
    open_ = collect(tmp_path/"open", [1.4, 1.4, 1.3, 1.3])
    wall = collect(tmp_path/"wall", [1.2, 1.1, 1.0, 0.9])
    summaries, selected = select_episodes([open_, wall], 0.25, per_collect=True)
    assert len(summaries) == 8
    assert [row["name"] for row in selected] == ["ep-open-0", "ep-wall-0"]
    assert selected[1]["npz"] == str(wall/"worker-00/ep-wall-0.npz")


def test_losses_and_fallbacks_are_never_selected(tmp_path):
    lost = collect(tmp_path/"lost", [2.0, 2.0], reason="primary_down")
    _, selected = select_episodes([lost], 1.0, per_collect=True)
    assert selected == []
