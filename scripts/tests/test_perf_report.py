"""Small CSV fixtures for frame cadence, dynamic zones and repeat comparisons."""
import csv
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("perf_report", Path(__file__).parents[1] / "perf_report.py")
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


def profile(directory, rows, gpu=False, extra_zone=False):
    directory.mkdir(parents=True, exist_ok=True)
    fields = ["attempt", "segment", "begin_ns", "end_ns", "observer_ns", "outcome", "overflow",
              "encode_ns", "encode_calls", "encode_first_ns", "encode_last_ns", "palette_bytes"]
    if gpu:
        fields += ["gpu_ns", "gpu_done", "gpu_buffers"]
    if extra_zone:
        fields += ["p30_ns", "p30_calls", "p30_first_ns", "p30_last_ns"]
    with (directory / "frameprofile.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for index, row in enumerate(rows):
            values = dict.fromkeys(fields, 0)
            values.update(attempt=index, segment="perf-market/view-1", outcome=1,
                          begin_ns=index * 10_000_000, encode_ns=1_000_000,
                          encode_calls=1, palette_bytes=1024)
            values.update(row)
            values["end_ns"] = values["begin_ns"] + 2_000_000
            writer.writerow(values)
    (directory / "metadata.json").write_text(json.dumps({"git_commit": "abc123", "drawable": [1728, 1117]}))
    return directory


def cadence_rows(step_ms, zone_ms=1):
    return [{"begin_ns": index * step_ms * 1_000_000, "encode_ns": int(zone_ms * 1_000_000)}
            for index in range(5)]


def test_percentiles_dynamic_columns_segments_and_no_gpu(tmp_path):
    directory = profile(tmp_path / "market" / "repeat-1", [
        {"segment": "warmup", "begin_ns": 0},
        {"begin_ns": 100_000_000, "p30_ns": 3_000_000},
        {"begin_ns": 110_000_000, "p30_ns": 3_000_000},
        {"begin_ns": 130_000_000, "p30_ns": 3_000_000},
        {"begin_ns": 160_000_000},
        {"segment": "perf-market/view-2", "begin_ns": 200_000_000},
        {"segment": "perf-market/view-2", "begin_ns": 240_000_000},
        {"segment": "warmup", "begin_ns": 900_000_000},
    ], extra_zone=True)
    result = report.build_report(tmp_path)
    market = result["places"]["market"]
    first = market["views"]["view-1"]
    assert first["frames"] == 3
    assert first["fps"] == pytest.approx(50)
    assert first["frame_ms"] == {"mean": 20, "p50": 20, "p95": 29, "p99": pytest.approx(29.8)}
    assert market["views"]["view-2"]["frames"] == 1
    assert first["gpu"] is None
    assert [zone["name"] for zone in first["zones"]] == ["p30", "encode"]
    assert first["zones"][0]["frame_pct"] == 15
    assert first["amounts"] == {"palette_bytes": {"total": 3072, "per_frame": 1024}}
    assert result["sources"][0]["metadata"]["drawable"] == [1728, 1117]
    assert report.percentile([1], .99) == 1
    assert report.percentile([], .95) is None
    assert report.main(["run", str(directory)]) == 0
    assert (directory / "report.md").exists()
    assert json.loads((directory / "report.json").read_text())["places"]["market"]["summary"]["frames"] == 4


def test_fence_attempts_cost_time_but_do_not_inflate_rendered_fps(tmp_path):
    profile(tmp_path, [
        {"begin_ns": 0},
        {"begin_ns": 10_000_000, "outcome": 2, "encode_ns": 0, "encode_calls": 0},
        {"begin_ns": 30_000_000},
        {"begin_ns": 50_000_000},
    ])
    stats = report.build_report(tmp_path)["places"]["market"]["summary"]
    assert stats["frames"] == 2
    assert stats["attempts"] == 3
    assert stats["fps"] == 40
    assert stats["frame_ms"]["p95"] == pytest.approx(29.5)
    assert stats["cpu_ms"]["mean"] == 3
    assert stats["zones"][0]["mean_ms"] == 1
    assert stats["amounts"]["palette_bytes"]["per_frame"] == 1536


def test_gpu_completion_and_matching_cpu_duration(tmp_path):
    profile(tmp_path, [
        {"gpu_ns": 4_000_000, "gpu_done": 1, "gpu_buffers": 2},
        {"gpu_ns": 50_000_000, "gpu_done": 0, "gpu_buffers": 1},
        {"gpu_ns": 1_000_000, "gpu_done": 1, "gpu_buffers": 1},
        {"gpu_ns": 100_000_000, "gpu_done": 1, "gpu_buffers": 1},
    ], gpu=True)
    gpu = report.build_report(tmp_path)["places"]["market"]["summary"]["gpu"]
    assert gpu["completed_frames"] == 2
    assert gpu["completion_fraction"] == pytest.approx(2 / 3)
    assert gpu["mean_ms"] == 2.5
    assert gpu["p95_ms"] == pytest.approx(3.85)
    assert gpu["longer_than_cpu_fraction"] == .5
    assert gpu["buffers_per_completed_frame"] == 1.5


def test_compare_repeat_noise_sorted_regressions_and_leaf_inputs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root, values in [(a, [(10, 2), (12, 4)]), (b, [(14, 3.5), (16, 4.5)])]:
        for repeat, (step, cost) in enumerate(values, 1):
            profile(root / "market" / f"repeat-{repeat}", [
                {**row, "p30_ns": 1_000_000 if root == a else 6_000_000}
                for row in cadence_rows(step, cost)], extra_zone=True)
    comparison = report.compare_reports(report.build_report(a), report.build_report(b))
    market = comparison["places"]["market"]
    assert market["fps"]["delta"] < 0
    assert market["p95_ms"]["delta"] == 4
    assert market["p95_ms"]["within_noise"] is False
    assert [zone["name"] for zone in market["zones"]] == ["p30", "encode"]
    assert market["zones"][0]["delta"] == 5
    assert market["zones"][0]["delta_pct"] == 500
    assert market["zones"][1]["delta"] == 1
    assert market["zones"][1]["within_noise"] is True
    assert "в пределах шума" in report.compare_markdown(comparison)
    left, right = a / "market/repeat-1", a / "market/repeat-2"
    assert report.main(["compare", str(left), str(right)]) == 0
    leaf = json.loads((right / "compare.json").read_text())
    assert leaf["places"]["market"]["zones"][0]["within_noise"] is None
    assert (right / "compare.md").exists()
