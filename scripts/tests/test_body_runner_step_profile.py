"""Runner CPU-step and realtime-window reports on synthetic sampler files."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import struct

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "body_runner_step_profile.py"
SPEC = importlib.util.spec_from_file_location("body_runner_step_profile", SCRIPT)
profile_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile_tool)


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def write_samples(path, samples):
    path.write_bytes(b"".join(
        struct.pack("<IIQ", profile_tool.MAGIC, len(frames), ns)
        + struct.pack(f"<{len(frames)}Q", *frames)
        for ns, frames in samples
    ))


def write_duel(path, samples, due):
    path.mkdir(parents=True)
    write_jsonl(path / "observer-timing.jsonl", [
        {"kind": "sample_step", "sim_step": step, "wall_sample_us": us}
        for step, us in samples
    ] + [{"kind": "other", "sim_step": 99, "wall_sample_us": 99999}])
    write_jsonl(path / "ipc.jsonl", [
        {"direction": "response", "value": {
            "sim_step": step - 1, "next_tick_wall_monotonic_ns": ns}}
        for step, ns in due
    ] + [
        {"direction": "request", "value": {
            "sim_step": 98, "next_tick_wall_monotonic_ns": 99999000}},
        {"direction": "response", "value": {"next_tick_wall_monotonic_ns": 1}},
    ])


class FakeSymbolizer:
    """Keep address adjustment observable without ever invoking addr2line."""

    def __init__(self):
        self.resolved = {}
        self.lookups = []

    def resolve(self, binary, addresses):
        self.resolved[binary] = set(addresses)

    def chain(self, binary, address):
        self.lookups.append((binary, address))
        assert binary == "/fixture/runner"
        assert address in self.resolved[binary]
        return {
            0x100: ["Inline::leaf<int>(float)", "JPH::FootIK::cast()"],
            0x1FF: ["Runner::step()"],
        }[address]


@pytest.fixture
def cpu_files(tmp_path):
    duels = tmp_path / "duels"
    write_duel(
        duels / "rt0-a-gru",
        [(46, 100), (47, 3000), (48, 9000), (49, 10000)],
        [(47, 1000000), (48, 5000000), (49, 11000000)],
    )
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "cpu.123.maps").write_text(
        "1000-1100 r-xp 00000000 00:00 0 /fixture/libcpusample.so\n"
        "2000-2100 r-xp 00000000 00:00 0 /fixture/libc.so\n"
        "3000-4000 r-xp 00000000 00:00 0 /fixture/runner\n"
    )
    full_stack = (0x1008, 0x2008, 0x3100, 0x3200, 0x3200)
    flat_stack = (0x1008, 0, 0x3100)
    write_samples(samples / "cpu.123.bin", [
        (999999, full_stack),
        (1000000, full_stack),
        (2000000, full_stack),
        (3000000, full_stack),
        (4000000, full_stack),
        (5000000, flat_stack),
        (9000000, flat_stack),
        (9000001, full_stack),
    ])
    return samples, duels


def test_read_samples_preserves_timestamp_and_frame_order(tmp_path):
    path = tmp_path / "cpu.123.bin"
    records = [(123456789, (0xABC, 0, 0xDEF)), (987654321, (0x123,))]
    write_samples(path, records)
    assert profile_tool.read_samples(path) == records


def test_read_samples_rejects_bad_magic(tmp_path):
    path = tmp_path / "broken.bin"
    path.write_bytes(struct.pack("<IIQ", 0, 0, 123))
    with pytest.raises(ValueError, match="bad record at 0"):
        profile_tool.read_samples(path)


def test_read_maps_keeps_executable_ranges_and_lowest_zero_offset_base(tmp_path):
    path = tmp_path / "cpu.123.maps"
    path.write_text(
        "5000-6000 r-xp 00001000 00:00 0 /fixture/runner\n"
        "7000-7100 rw-p 00000000 00:00 0 [heap]\n"
        "2000-2100 r-xp 00000000 00:00 0 /fixture/libc.so\n"
        "4000-5000 r--p 00000000 00:00 0 /fixture/runner\n"
        "3000-3100 r--p 00000000 00:00 0 /fixture/runner\n"
        "8000-8100 r-xp 00000000 00:00 0\n"
    )
    assert profile_tool.read_maps(path) == (
        [(0x2000, 0x2100, "/fixture/libc.so"),
         (0x5000, 0x6000, "/fixture/runner")],
        {"/fixture/libc.so": 0x2000, "/fixture/runner": 0x3000},
    )


def test_step_intervals_require_due_and_nonnegative_duration(cpu_files):
    _, duels = cpu_files
    assert profile_tool.step_intervals(duels) == [
        (1000000, 3000000, True),
        (5000000, 9000000, False),
    ]


def test_profile_counts_only_step_samples_and_preserves_stack_layout(cpu_files):
    samples, duels = cpu_files
    sym = FakeSymbolizer()
    result = profile_tool.profile(samples, duels, 2000, sym)
    all_steps = result["all"]
    assert all_steps == {
        "steps": 2,
        "samples": 5,
        "cpu_ms_per_step": 5.0,
        "inclusive_ms": {"Inline::leaf": 5.0, "JPH::FootIK::cast": 5.0,
                         "Runner::step": 3.0},
        "self_ms": {"Inline::leaf": 5.0},
        "pc_function_ms": {"JPH::FootIK::cast": 5.0},
        "group_ms": {"jolt": 5.0},
        "folded": {
            "Runner::step;Runner::step;JPH::FootIK::cast;Inline::leaf": 3,
            "JPH::FootIK::cast;Inline::leaf": 2,
        },
        "wall_ms_per_step": 3.0,
    }
    assert result["first_second"]["steps"] == 1
    assert result["first_second"]["samples"] == 3
    assert result["first_second"]["cpu_ms_per_step"] == 6.0
    assert result["first_second"]["inclusive_ms"]["Runner::step"] == 6.0
    assert result["later"]["steps"] == 1
    assert result["later"]["samples"] == 2
    assert result["later"]["cpu_ms_per_step"] == 4.0
    assert "Runner::step" not in result["later"]["inclusive_ms"]
    # Handler and trampoline never reach symbol lookup; only return addresses use -1.
    assert set(sym.lookups) == {("/fixture/runner", 0x100),
                                ("/fixture/runner", 0x1FF)}


def test_profile_cli_writes_same_json_and_compares_profiles(cpu_files, tmp_path, monkeypatch, capsys):
    samples, duels = cpu_files
    monkeypatch.setattr(profile_tool, "Symbolizer", FakeSymbolizer)
    output = tmp_path / "profile.json"
    profile_tool.main([
        "profile", "--profile", f"old={samples}:{duels}",
        "--profile", f"new={samples}:{duels}", "--period-us", "2000",
        "--top", "1", "--section", "first_second", "--json", str(output),
    ])
    result = json.loads(output.read_text())
    assert result["old"] == result["new"] == profile_tool.profile(samples, duels, 2000, FakeSymbolizer())
    printed = capsys.readouterr().out
    assert "old: steps 2, wall 3.0 ms/step, cpu 5.0 ms/step" in printed
    assert "pc_function_ms per step (first_second)" in printed
    assert "+0.000" in printed


@pytest.fixture
def realtime_files(tmp_path):
    duels = tmp_path / "duels"
    write_duel(
        duels / "rt0-a-gru",
        [(46, 12000), (47, 25000), (48, 43000), (49, 79000),
         (50, 104000), (51, 137000), (52, 125000), (53, 151000)],
        [(46, 10000000), (47, 20000000), (48, 40000000),
         (49, 70000000), (50, 100000000), (51, 130000000),
         (52, 131000000), (53, 150000000)],
    )
    def case(policy, primary_hp, opponent_hp, accepted, rejected, control):
        return {"policy": policy, "final": {
            "primary_entity_id": 1, "opponent_entity_id": 2,
            "actors": [{"entity_id": 2, "hp": opponent_hp},
                       {"entity_id": 1, "hp": primary_hp}],
            "elapsed_game_ms": 12340,
        }, "neural_control_fraction": control, "model_stats": {
            "accepted_model_commands": accepted, "rejected_model_commands": rejected,
            "runtime_fallbacks": 1 if rejected else None}}
    (duels / "suite.json").write_text(json.dumps({"cases": [
        case("gru", 300, 0, 10, 2, .946),
        case("gru", 180, 20, 7, None, .99),
        case("other", 999, 0, 900, 800, .1),
    ]}))
    return duels


def test_quantiles_use_sorted_floor_index_and_round_to_two_places():
    assert profile_tool.q([9, 1, 7, 4], .95) == 7
    assert profile_tool.q([9, 1, 7, 4], .5) == 4
    assert profile_tool.q([1.239], .5) == 1.24
    assert profile_tool.q([], .5) is None


def test_window_cli_preserves_step_window_quantiles_and_rejections(realtime_files, capsys):
    profile_tool.main(["window", f"old={realtime_files}", f"new={realtime_files}"])
    result = json.loads(capsys.readouterr().out)
    expected = {
        "duels": 2, "wins": 1, "hp_mean": 240.0, "no_damage": 1,
        "win_s_mean": 12.34, "accepted_commands": 17, "rejected_commands": 2,
        "duels_with_rejected": 1, "neural_control_min": .946,
        "duels_below_095": 1, "fallbacks": 1,
        "step_ms_first_second": {"p50": 2.0, "p95": 2.0},
        "step_ms_later": {"p50": 4.0, "p95": 7.0},
        "window_ms_first_second": {"p5": 8.0, "p50": 8.0},
        "window_ms_later": {"p5": 21.0, "p50": 25.0},
    }
    assert result == {"old": expected, "new": expected}


def table_rows(text):
    rows = {}
    for line in text.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) != 4:
            continue
        try:
            values = tuple(float(value) for value in parts[:3])
        except ValueError:
            continue
        rows[parts[3]] = values
    return rows


@pytest.fixture
def folded_profiles(tmp_path):
    path = tmp_path / "profile.json"
    data = {}
    for name, cpu in [("old", 14), ("new", 7)]:
        data[name] = {
            "later": {"cpu_ms_per_step": cpu, "samples": 7, "folded": {
                "Main;Root;Target;Child;Leaf": 2,
                "Main;Root;Target": 1,
                "Main;Root;Target;Target;Reentrant": 1,
                "Main;Root;Other": 3,
            }},
            "first_second": {"cpu_ms_per_step": 6, "samples": 2,
                             "folded": {"Main;Root;Target;Early": 2}},
        }
    path.write_text(json.dumps(data))
    return path


def test_tree_children_uses_outermost_match_depth_and_section(folded_profiles, capsys):
    profile_tool.main(["tree", str(folded_profiles), "Target", "--depth", "2"])
    assert table_rows(capsys.readouterr().out) == {
        "Child;Leaf": (4.0, 2.0, -2.0),
        "<self>": (2.0, 1.0, -1.0),
        "Target;Reentrant": (2.0, 1.0, -1.0),
    }
    profile_tool.main(["tree", str(folded_profiles), "Target", "--children",
                       "--section", "first_second", "--rows", "1"])
    assert table_rows(capsys.readouterr().out) == {"Early": (6.0, 6.0, 0.0)}


def test_tree_callers_uses_outermost_match_and_default_depth(folded_profiles, capsys):
    profile_tool.main(["tree", str(folded_profiles), "Target", "--callers"])
    assert table_rows(capsys.readouterr().out) == {"Root < Main": (8.0, 4.0, -4.0)}
    profile_tool.main(["tree", str(folded_profiles), "Target", "--callers", "--depth", "1",
                       "--section", "first_second"])
    assert table_rows(capsys.readouterr().out) == {"Root": (6.0, 6.0, 0.0)}
