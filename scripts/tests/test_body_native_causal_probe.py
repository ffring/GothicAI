"""Pure Linux counter parsing and observational interval boundaries."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import body_native_causal_probe as probe


def test_stat_comm_with_spaces_and_closing_parenthesis_preserves_field_indices():
    fields = ["R"]+[str(index) for index in range(4, 26)]
    value = "123 (a worker) name) "+" ".join(fields)
    row = probe.parse_stat(value)
    assert row == {"state": "R", "minflt": 10, "majflt": 12, "utime_ticks": 14,
                   "stime_ticks": 15, "num_threads": 20, "starttime_ticks": 22, "rss_pages": 24}


def test_cgroup_cpu_and_proc_io_parse_actual_integer_counters():
    assert probe.key_values("usage_usec 123\nnr_throttled 4\nthrottled_usec 1500\n") == {
        "usage_usec": 123, "nr_throttled": 4, "throttled_usec": 1500}
    assert probe.key_values("read_bytes: 4096\nwrite_bytes: 0\n") == {"read_bytes": 4096, "write_bytes": 0}


def test_psi_retains_cumulative_microseconds_and_averages_separately():
    assert probe.pressure("some avg10=0.20 avg60=0.10 avg300=0.03 total=125\n") == {
        "some": {"avg10": .2, "avg60": .1, "avg300": .03, "total": 125}}


def test_counter_reset_cannot_be_attributed_as_negative_cpu_or_throttle():
    with pytest.raises(RuntimeError, match="moved backwards"):
        probe.delta({"usage_usec": 200}, {"usage_usec": 100})
    assert probe.schedstat("500 120 4") == {"runtime_ns": 500, "runqueue_ns": 120, "timeslices": 4}


def sample(begin, end, cpu, throttled, rss):
    return {"begin_wall_monotonic_ns": begin, "end_wall_monotonic_ns": end,
        "process": {"utime_ticks": cpu, "stime_ticks": 1, "minflt": cpu, "majflt": 0, "rss_pages": rss},
        "main_schedstat": {"runtime_ns": cpu*100, "runqueue_ns": cpu*50, "timeslices": cpu},
        "io": {"read_bytes": cpu*4096}, "cgroup_cpu_stat": {"usage_usec": cpu*1000, "nr_throttled": throttled},
        "pressure": {"cpu": {"some": {"total": cpu*10}}}}


def tick():
    return ({"tick_id": 4, "sim_step": 3, "game_ms": 100, "wall_monotonic_ns": 100,
             "tick_due_wall_monotonic_ns": 80},
            {"tick_id": 4, "sim_step": 4, "game_ms": 133, "wall_monotonic_ns": 200})


def test_join_brackets_counter_scope_without_claiming_exact_tick_cpu():
    # The overlapping sample cannot serve as either external boundary.
    rows = [sample(40, 50, 2, 0, 100), sample(90, 110, 3, 0, 90), sample(210, 230, 5, 1, 80)]
    row = probe.join_tick(tick(), rows, 100)
    assert row["sample_begin_ns"] == 40 and row["sample_end_ns"] == 230
    assert row["native_cpu_ms"] == 30 and row["whole_probe_cgroup_cpu_delta"]["nr_throttled"] == 1
    assert row["pressure_total_delta_us"] == {"cpu": {"some": 30}}
    assert "adjacent ticks" in row["counter_scope"] and "rss_pages" not in row["native_process_delta"]


def test_missing_counter_brackets_remain_missing():
    row = probe.join_tick(tick(), [sample(90, 110, 3, 0, 100)], 100)
    assert row["bracketed"] is False and "native_cpu_ms" not in row


def test_old_binary_worker_field_is_only_accepted_as_unattested_default_diagnostic():
    assert probe.worker_profile({}, False) == {"attested": False, "legacy_diagnostic_only": True}
    with pytest.raises(RuntimeError, match="lacks actual"):
        probe.worker_profile({}, True)
    with pytest.raises(RuntimeError):
        probe.worker_profile({"worker_execution": None}, False)


def test_serial_worker_profile_requires_actual_caller_mode_no_pool_and_integer_cap():
    value = {"serial": True, "mode": "caller_serial", "worker_pool_created": False, "native_task_parallelism_cap": 1}
    assert probe.worker_profile({"worker_execution": value}, True)["attested"] is True
    for key, changed in (("serial", False), ("mode", "default_parallel"), ("worker_pool_created", True),
                         ("native_task_parallelism_cap", True), ("native_task_parallelism_cap", 2)):
        with pytest.raises(RuntimeError):
            probe.worker_profile({"worker_execution": {**value, key: changed}}, True)


def test_default_worker_field_cannot_claim_serial_execution_or_serial_cap():
    value = {"serial": False, "mode": "default_parallel", "worker_pool_created": True, "native_task_parallelism_cap": None}
    assert probe.worker_profile({"worker_execution": value}, False)["attested"] is True
    with pytest.raises(RuntimeError):
        probe.worker_profile({"worker_execution": {**value, "native_task_parallelism_cap": 1}}, False)
    with pytest.raises(RuntimeError):
        probe.worker_profile({"worker_execution": value}, "false")
