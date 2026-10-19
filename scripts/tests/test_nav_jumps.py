"""Budget and exact historical-link coverage for the CV probe collector."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("nav_jumps", Path(__file__).resolve().parents[1] / "nav_jumps.py")
jumps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jumps)


def test_links_preserve_uint32_ids_and_reject_duplicate_ids(tmp_path):
    path = tmp_path / "links.txt"
    source = [dict(start=(1., 2., 3.), end=(4., 5., 6.), type=2, id=4294967295)]
    jumps.write_links(path, source)
    assert jumps.links(path) == source
    path.write_text(path.read_text() * 2)
    with pytest.raises(ValueError, match="Duplicate"):
        jumps.links(path)


def test_batch_requires_every_original_link_and_full_cv(tmp_path):
    expected = [dict(start=(0., 0., 0.), end=(1., 0., 0.), type=1, id=42)]
    jumps.write_links(tmp_path / "accepted-links.txt", expected)
    path = tmp_path / "transitions.jsonl"
    row = dict(link_id=42, accepted=True, type="jump", reason="landed_without_damage", frames=10, cv_frames=4,
               expect_cv=True, forced_cv_frames=10, preparation_frames=4, preparation_cv_frames=4)
    path.write_text(json.dumps(row) + "\n")
    assert jumps.verify_batch(tmp_path, expected)[1] == expected
    row["preparation_cv_frames"] = 3
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(RuntimeError, match="real CV preparation"):
        jumps.verify_batch(tmp_path, expected)
    row["preparation_cv_frames"] = 4
    row["link_id"] = 41
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(RuntimeError, match="Incomplete/duplicate"):
        jumps.verify_batch(tmp_path, expected)


def test_rejection_reasons_are_counted_by_type():
    counts = jumps.summary([dict(type="climb", accepted=False, reason="native_has_no_climb"),
                            dict(type="jump", accepted=True, reason="landed_without_damage")])
    assert counts["climb"] == dict(attempts=1, accepted=0, rejected_reasons={"native_has_no_climb": 1})
    assert counts["jump"]["accepted"] == 1
    assert counts["drop"]["attempts"] == 0


def test_discovery_counts_new_directed_links_only():
    old = dict(start=(0., 0., 0.), end=(1., 0., 0.), type=1, id=1)
    repeated = dict(old, start=(.2, 0., 0.), id=2)
    reverse = dict(old, start=old["end"], end=old["start"], id=3)
    climb = dict(old, type=2, id=4)
    assert jumps.novel_links([repeated, reverse, climb, climb], [old]) == [reverse, climb]
