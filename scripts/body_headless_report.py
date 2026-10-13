#!/usr/bin/env python3
"""Create a compact stage 2 report from completed native runner evidence.

Read-only reporting; never starts a game/model, changes evidence or blesses a
partial run. CLI: body_headless_report.py RUN_DIR --output-dir REPORT_DIR.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODES = {"lockstep": "lockstep", "lockstep-delayed": "lockstep_delayed", "realtime": "realtime"}
SEEDS = [17, 29, 43]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key}")
            result[key] = value
        return result
    def finite(value: str) -> None:
        raise ValueError(f"non-finite JSON value {value}")
    value = json.loads(path.read_text(), object_pairs_hook=unique, parse_constant=finite)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: JSON object required")
    return value


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def ratio(left: Any, right: Any) -> float | None:
    return left/right if number(left) and number(right) and right > 0 else None


def compact_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {"samples": 0}
    def span(key: str) -> list[float] | None:
        values = [row.get(key) for row in samples]
        return [min(values), max(values)] if all(number(value) for value in values) else None
    first, last = samples[0], samples[-1]
    user = last.get("cpu_user_s", 0)-first.get("cpu_user_s", 0)
    system = last.get("cpu_system_s", 0)-first.get("cpu_system_s", 0)
    interval = last.get("t_wall_monotonic", 0)-first.get("t_wall_monotonic", 0)
    peak = span("peak_rss_kib")
    return {"samples": len(samples), "rss_kib_range": span("rss_kib"),
            "peak_rss_kib": peak[1] if peak else None, "threads_range": span("threads"),
            "file_descriptors_range": span("file_descriptors"), "sample_interval_wall_s": interval,
            "cpu_user_s": user, "cpu_system_s": system, "cpu_total_s": user+system,
            "mean_busy_cpu_cores": ratio(user+system, interval),
            "forbidden_device_descriptors": sorted({value for row in samples for value in row.get("forbidden_device_descriptors", [])}),
            "display_socket_descriptors": sorted({value for row in samples for value in row.get("display_socket_descriptors", [])})}


def compact_runtime(run: dict[str, Any]) -> dict[str, Any]:
    final = run.get("final", {})
    elapsed = final.get("elapsed_wall_ms")
    simulated = final.get("simulated_ms")
    native = final.get("native_choices")
    processing = run.get("advance_processing_wall_s")
    lockstep = run.get("mode") == "lockstep"
    return {"mode": run.get("mode"), "status": run.get("status"),
            "delay_per_advance_s": run.get("delay_per_advance_s"),
            "substeps": final.get("sim_step"), "advance_request_substeps": run.get("advance_request_substeps"),
            "simulated_ms": simulated, "elapsed_wall_ms_after_reset": elapsed,
            "simulated_wall_ratio_after_reset": ratio(simulated, elapsed),
            "cold_reset_wall_ms": final.get("reset_wall_ms"),
            "advance_processing_wall_s": processing,
            "substeps_per_processing_s": ratio(run.get("advance_request_substeps"), processing) if lockstep else None,
            "native_choices": native,
            "native_choices_per_processing_s": ratio(native, processing) if lockstep else None,
            "native_choices_per_wall_s_after_reset": ratio(native*1000 if number(native) else None, elapsed),
            "native_choices_per_simulated_s": ratio(native*1000 if number(native) else None, simulated),
            "max_realtime_lag_ms": final.get("max_realtime_lag_ms"),
            "resources": compact_samples(run.get("resources", []))}


def compact_services(value: dict[str, Any]) -> dict[str, Any]:
    services = {}
    for name, text in value.get("services", {}).items():
        services[name] = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    return {"gpu_shared_host": value.get("gpu"), "services": services,
            "service_latency_measurement": value.get("service_latency_measurement")}


def summarize(suite: dict[str, Any], manifest: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    gates, issues = [], []
    def gate(key: str, label: str, passed: bool, detail: str) -> None:
        gates.append({"id": key, "label": label, "status": "passed" if passed else "failed", "detail": detail})
        if not passed:
            issues.append(f"{key}: {detail}")
    gate("suite", "Полная серия", suite.get("status") == "passed" and suite.get("full_stage2_evidence") is True and
         suite.get("required_gate_failures") == [] and manifest.get("suite_status") == "passed",
         f"suite={suite.get('status', 'missing')}, full_stage2_evidence={suite.get('full_stage2_evidence')}, "
         f"required_gate_failures={suite.get('required_gate_failures', 'missing')}, manifest={manifest.get('suite_status', 'missing')}")
    gate("headless", "Без graphics/audio device", manifest.get("headless") is True and manifest.get("renderer") == "none" and
         manifest.get("display") is None and all(manifest.get(key) is False for key in
                                                ("graphics_device_created", "window_created", "audio_device_created")),
         "Проверены явные flags отсутствия device/window/playback и renderer=none")
    comparisons = suite.get("comparisons", {})
    def comparison(key: str, *, full: bool = False, invariance: bool = False, archival: bool = False) -> dict[str, Any]:
        wrapper = comparisons.get(key, {})
        report = wrapper.get("report", {}) or {}
        rows = report.get("episodes", [])
        valid = (wrapper.get("exit_code") == 0 and report.get("status") == "matched" and
                 [row.get("seed") for row in rows] == SEEDS and
                 all(row.get("status") == "matched" and row.get("difference_count") == 0 for row in rows))
        if full:
            valid = valid and report.get("profile") == "full" and report.get("comparison_kind") == "rendered_vs_headless" and report.get("full_stage2_evidence") is True
        if invariance:
            valid = valid and report.get("profile") == "full" and report.get("comparison_kind") == "headless_schedule_invariance"
        if archival:
            valid = valid and report.get("profile") == "archived_reference" and report.get("comparison_kind") == "rendered_vs_headless"
        gate(key, key.replace("_", " "), valid,
             f"status={report.get('status', 'missing')}, profile={report.get('profile', 'missing')}, comparison={report.get('comparison_kind', 'missing')}")
        return {"status": report.get("status"), "comparison_kind": report.get("comparison_kind"),
                "profile": report.get("profile"), "full_stage2_evidence": report.get("full_stage2_evidence"),
                "episodes": [{key: row.get(key) for key in ("seed", "status", "difference_count", "checks", "reference_duration_ms", "candidate_duration_ms")}
                             for row in report.get("episodes", [])]}
    compact_comparisons = {f"rendered_vs_{name}": comparison(f"rendered_vs_{name}", full=True) for name in MODES}
    compact_comparisons.update({f"lockstep_vs_{name}": comparison(f"lockstep_vs_{name}", invariance=True)
                                for name in ("lockstep-delayed", "realtime")})
    compact_comparisons["historical_vs_branch_replay"] = comparison("historical_vs_branch_replay", archival=True)
    replay_completeness = comparisons.get("replay_completeness", {})
    replay_baseline = replay_completeness.get("report", {}) or {}
    gate("replay_completeness", "Полнота архивного replay", replay_completeness.get("exit_code") == 0 and
         replay_baseline.get("status") == "complete" and [row.get("seed") for row in replay_baseline.get("episodes", [])] == SEEDS,
         "Complete trace: seeds 17/29/43, actor setup и terminal события")
    replay = suite.get("replay_animation_control", {})
    consumed = replay.get("consumed_source_sequences", [])
    fixture_path = run_dir / "archived-animation-branches.json"
    try:
        fixture = read_json(fixture_path)
        fixture_sequences = [row["source_seq"] for row in fixture["entries"]]
    except (OSError, ValueError, KeyError, TypeError) as error:
        fixture, fixture_sequences = {}, []
        issues.append(f"replay_fixture: {error}")
    replay_summaries = replay.get("summaries", [])
    seed_counts = {17: 6, 29: 7, 43: 4}
    replay_counts_valid = len(replay_summaries) == 3 and all(
        len([row for row in replay_summaries if row.get("seed") == seed and row.get("status") == "complete" and
             row.get("expected_entries") == count and row.get("consumed_entries") == count and row.get("remaining_entries") == 0 and row.get("errors") == 0]) == 1
        for seed, count in seed_counts.items())
    gate("replay_consumption", "Replay: 17 веток ровно один раз", replay.get("status") == "passed" and replay.get("mode") == "replay" and
         replay.get("fixture_entries") == 17 and replay.get("accepted_blocks") == 17 and isinstance(consumed, list) and
         len(consumed) == 17 and len(set(consumed)) == 17 and sorted(consumed) == sorted(fixture_sequences) and
         fixture.get("kind") == "body_animation_branch_fixture" and replay_counts_valid,
         f"entries={replay.get('fixture_entries')}, accepted_blocks={replay.get('accepted_blocks')}, consumed={len(consumed) if isinstance(consumed, list) else 'invalid'}")
    senses, resets, ipc = (suite.get(key, {}) for key in ("senses", "resets", "ipc"))
    gate("senses", "Зрение/преграды/звук", senses.get("status") == "passed" and set(senses.get("cases", {})) == {"visible", "occluded"},
         "Оба case: настоящие world rays, native sight и завершение звука по metadata duration")
    reset_episodes = resets.get("episodes", [])
    generations = [row.get("generation") for row in reset_episodes]
    reset_metrics = (resets.get("latency_ms") or {}).get("n") == 100 and number((resets.get("latency_ms") or {}).get("p95")) and all(
        number(resets.get(key)) for key in ("warmed_initial_rss_kib", "warmed_final_rss_kib", "rss_growth_limit_kib", "file_descriptor_span", "threads_span"))
    reset_stable = reset_metrics and (resets["warmed_final_rss_kib"]-resets["warmed_initial_rss_kib"] <= resets["rss_growth_limit_kib"] and
                                      resets["file_descriptor_span"] <= 4 and resets["threads_span"] <= 2)
    gate("resets", "100 последовательных resets", resets.get("status") == "passed" and resets.get("count") == 100 and resets.get("issues") == [] and
         len(reset_episodes) == 100 and len(set(generations)) == 100 and len({row.get("state_sha256") for row in reset_episodes}) == 1 and reset_stable,
         f"count={resets.get('count')}, status={resets.get('status')}, issues={resets.get('issues', 'missing')}")
    ipc_checks = ipc.get("checks", {})
    required_ipc = ("lockstep_freeze", "advance_until_decision", "realtime_partial_line", "pipelined_status", "clean_close")
    gate("ipc", "JSONL IPC", ipc.get("status") == "passed" and all(ipc_checks.get(key, {}).get("status") == "passed" for key in required_ipc),
         "Freeze, stop на native decision, partial line в realtime, pipeline и clean close")
    runtime = {name: compact_runtime(suite.get(key, {})) for name, key in MODES.items()}
    for name, key in MODES.items():
        run = suite.get(key, {})
        final = run.get("final", {})
        samples = run.get("resources", [])
        complete = comparisons.get(f"{name}_completeness", {})
        summary = complete.get("report", {}) or {}
        flags = all(final.get(key) is False for key in ("graphics_device_created", "window_created", "audio_device_created"))
        measured = samples and all(number(row.get(key)) and row[key] >= 0 for row in samples
                                   for key in ("rss_kib", "peak_rss_kib", "threads", "file_descriptors", "cpu_user_s", "cpu_system_s", "t_wall_monotonic"))
        no_devices = all(row.get("forbidden_device_descriptors") == [] and row.get("display_socket_descriptors") == [] for row in samples)
        counters = all(number(final.get(key)) and final[key] > 0 for key in ("elapsed_wall_ms", "simulated_ms", "sim_step", "native_choices"))
        if name != "realtime":
            counters = (counters and number(run.get("advance_processing_wall_s")) and run["advance_processing_wall_s"] > 0 and
                        number(run.get("substeps_per_processing_s")) and run.get("advance_request_substeps") == final["sim_step"])
        gate(f"{name}_runtime", f"{name}: runtime evidence", bool(run.get("status") == "completed" and final.get("done") is True and
             flags and measured and no_devices and counters and complete.get("exit_code") == 0 and summary.get("status") == "complete" and
             [row.get("seed") for row in summary.get("episodes", [])] == SEEDS),
             "Completed, device attestation, /proc samples и complete melee trace всех трёх seeds")
        perception = suite.get(f"{name}_native_perceptions", {})
        gate(f"{name}_perception", f"{name}: native perceptions", perception.get("status") == "passed" and
             perception.get("native_callback_dispatches", 0) > 0 and bool(perception.get("periodic_by_actor")),
             f"native callback dispatches={perception.get('native_callback_dispatches', 'missing')}")
    diagnostics = []
    for name in MODES:
        key = f"historical_vs_isolated_{name}"
        wrapper = suite.get("historical_diagnostics", {}).get(key, {})
        report = wrapper.get("report", {}) or {}
        gate(key, f"Историческая диагностика: {name}", wrapper.get("exit_code") in (0, 2) and report.get("status") in ("matched", "diverged"),
             f"Записан {report.get('status', 'missing')}; legacy shared RNG не является isolated RNG")
        diagnostics.append({"mode": name, "status": report.get("status"),
                            "episodes": [{"seed": row.get("seed"), "difference_count": row.get("difference_count"),
                                          "first_areas": [item.get("area") for item in row.get("first_differences", [])[:3]]}
                                         for row in report.get("episodes", [])]})
    def episodes(key: str) -> list[dict[str, Any]]:
        rows = (comparisons.get(key, {}).get("report", {}) or {}).get("episodes", [])
        return [{"seed": row.get("seed"), "duration_ms": row.get("duration_ms"), "outcome": row.get("outcome"),
                 "terminal_actor_hp": (row.get("terminal") or {}).get("actor_hp"),
                 "terminal_target_hp": (row.get("terminal") or {}).get("target_hp")} for row in rows]
    source = {key: manifest.get(key) for key in ("recorded_at_utc", "completed_at_utc", "host", "platform", "source_sha256", "source_files_hashed",
                                                "binary_sha256", "engine_library_sha256", "save_sha256", "scenario_sha256", "config_sha256", "settings_sha256",
                                                "reference_manifest_sha256", "reference_trace_sha256", "reference_binary_sha256", "reference", "game_version", "sim_fps", "runtime_limits")}
    source["asset_files_hashed"] = len(manifest.get("assets", []))
    if source["engine_library_sha256"] is None:
        source["engine_library_sha256"] = ipc.get("provenance", {}).get("engine_library_sha256")
        source["engine_library_hash_source"] = "suite.ipc.provenance.engine_library_sha256"
    else:
        source["engine_library_hash_source"] = "manifest.engine_library_sha256"
    ipc_provenance = ipc.get("provenance", {})
    gate("ipc_build_provenance", "IPC на проверенном build", all(ipc_provenance.get(ipc_key) == manifest.get(manifest_key) for ipc_key, manifest_key in
         (("runner_sha256", "binary_sha256"), ("save_sha256", "save_sha256"), ("scenario_sha256", "scenario_sha256"), ("settings_sha256", "settings_sha256"))) and
         isinstance(source["engine_library_sha256"], str) and len(source["engine_library_sha256"]) == 64,
         "IPC фиксирует тот же runner/save/scenario/settings и SHA-256 использованной engine library")
    source["animation_fixture_sha256"] = sha256(fixture_path) if fixture_path.is_file() else None
    source["animation_fixture_source_events_sha256"] = fixture.get("source_events_sha256")
    gate("source", "Provenance", source["asset_files_hashed"] > 0 and manifest.get("animation_rng_mode") == "isolated" and
         all(isinstance(source.get(key), str) and len(source[key]) == 64 for key in
             ("source_sha256", "binary_sha256", "save_sha256", "scenario_sha256", "config_sha256", "settings_sha256", "reference_trace_sha256")) and
         fixture.get("source_events_sha256") == manifest.get("reference_trace_sha256"),
         "Хеши исходников/бинарника/save/config/assets и связь fixture с неизменным archive trace")
    rendered = suite.get("rendered", {})
    remote_path = (suite.get("lockstep", {}).get("final", {}) or {}).get("output_dir")
    if remote_path:
        source["remote_run_dir"] = str(PurePosixPath(remote_path).parent)
    head = run_dir / "source-head.txt"
    if head.is_file():
        source["source_head"] = head.read_text().strip()
    return {"schema_version": 1, "status": "incomplete" if issues else "complete", "issues": issues, "gates": gates,
            "runtime": runtime, "rendered": {"wall_s_including_load": rendered.get("wall_s"),
                                             "renderer_gpu_vram_mib": rendered.get("renderer_gpu_vram_mib"),
                                             "resources": compact_samples(rendered.get("resources", []))},
            "comparisons": compact_comparisons, "episodes": {"isolated": episodes("lockstep_completeness"), "archival_branch_replay": episodes("replay_completeness")},
            "historical_diagnostics": diagnostics,
            "replay": {key: replay.get(key) for key in ("mode", "status", "fixture_entries", "accepted_blocks", "branch_draws", "consumed_source_sequences")},
            "resets": {**{key: resets.get(key) for key in ("status", "count", "latency_ms", "warmed_initial_rss_kib", "warmed_final_rss_kib", "rss_growth_limit_kib", "file_descriptor_span", "threads_span")},
                       "warmed_rss_delta_kib": resets.get("warmed_final_rss_kib", 0)-resets.get("warmed_initial_rss_kib", 0)},
            "senses": {"status": senses.get("status"), "cases": sorted(senses.get("cases", {}))},
            "ipc": {"status": ipc.get("status"), "checks": {key: {name: value for name, value in ipc_checks.get(key, {}).items()
                                                                   if name in ("status", "game_ms_advanced", "substeps_advanced", "observed_wall_ms", "exit_code")}
                                                        for key in required_ipc}},
            "services_before": compact_services(manifest.get("services_before", {})),
            "services_after": compact_services(suite.get("services_after", {})), "provenance": source}


def fmt(value: Any, precision: int = 2) -> str:
    return f"{value:.{precision}f}" if number(value) else "нет данных"


def markdown(report: dict[str, Any], run_dir: Path, out: Path) -> str:
    link = lambda path: Path(os.path.relpath(path, out)).as_posix()
    lines = ["# Этап 2: настоящий headless runner", "",
             f"Статус: **{'подтверждён' if report['status'] == 'complete' else 'не подтверждён: неполные или проваленные данные'}**.", "",
             f"[Протокол/runtime]({link(ROOT / 'docs/npc-body-headless-runner.md')}), [план]({link(ROOT / 'docs/npc-body-control-plan.md')}), "
             f"[контракт]({link(ROOT / 'docs/npc-body-combat-contract.md')}). "
             f"[Сырые данные, ignored]({link(run_dir)}), [suite.json]({link(run_dir / 'suite.json')}), [manifest.json]({link(run_dir / 'manifest.json')}).", "",
             "| Критерий | Результат | Проверенная граница |", "|---|---|---|"]
    for gate in report.get("gates", []):
        lines.append(f"| {gate['label']} | {'пройден' if gate['status'] == 'passed' else 'провален'} | {gate['detail'].replace('|', '/')} |")
    if report.get("issues"):
        lines += ["", "Причины отказа:", ""]+[f"- {issue}" for issue in report["issues"]]
    if report["status"] != "complete":
        lines += ["", "Полная готовность этапа 2 не подтверждена. Отсутствующие или проваленные данные не считаются пройденным gate.", ""]
        return "\n".join(lines)
    lines += ["", "## Механика и RNG", "",
              "Основное сравнение использует одну версию настоящего движка: rendered probe под CPU lavapipe/Xvfb и runner без graphics/audio device. "
              "На обоих путях выбран `isolated` RNG вариантов block-анимации. Сравнение охватывает native очереди/consume, animation tags, урон/HP, "
              "root motion, collision flags и исход дуэлей в допусках зафиксированной конфигурации.", "",
              "Исторический reference этапа 1 использовал общий `std::rand`; потребление RNG звуком зависело от wall clock. "
              "Его обычное сравнение с isolated запуском сохранено как диагностика. Отдельный архивный replay воспроизводит только записанные варианты "
              "block-клипов: 17 веток, каждая ровно один раз. Native AI самостоятельно выбирает действия; HP, урон, движение и исходы не подставляются из записи.", "",
              "| Seed | Isolated: мс / исход / HP actor–target | Архивный branch replay: мс / исход / HP actor–target |", "|---|---|---|"]
    for seed in SEEDS:
        cells = []
        for mode in ("isolated", "archival_branch_replay"):
            episode = next((row for row in report.get("episodes", {}).get(mode, []) if row.get("seed") == seed), {})
            cells.append(f"{episode.get('duration_ms', 'нет данных')} / {episode.get('outcome', 'нет данных')} / {episode.get('terminal_actor_hp', '?')}–{episode.get('terminal_target_hp', '?')}")
        lines.append(f"| {seed} | {' | '.join(cells)} |")
    lines += ["", "`actor` — Bandit, `target` — Paladin. Эти три seeds проверяют механику; статистическое качество будущего контроллера ими не оценено.", "",
              "| Историческая диагностика | Статус | Число ordered расхождений по seeds |", "|---|---|---|"]
    for diagnostic in report.get("historical_diagnostics", []):
        counts = ", ".join(f"{row['seed']}: {row['difference_count']}" for row in diagnostic.get("episodes", []))
        lines.append(f"| {diagnostic['mode']} | {diagnostic.get('status')} | {counts} |")
    lines += ["", "## Скорость и ресурсы", "",
              "Wall время и sim/wall ratio ниже начинаются после cold reset; намеренная IPC задержка учитывается в elapsed wall. "
              "Processing throughput lockstep исключает введённый sleep. Realtime paced ожидание не объявляется processing throughput. "
              "CPU/RSS измерены по `/proc` на протяжении процесса, включая загрузку; RSS peak — максимум наблюдаемого VmHWM.", "",
              "| Режим | Sim / wall после reset, с | Sim/wall | Substeps / processing с | Native choices / processing с | Peak RSS, MiB | CPU user+sys, с | Threads / FD max |",
              "|---|---:|---:|---:|---:|---:|---:|---|"]
    for name, runtime in report.get("runtime", {}).items():
        resources = runtime.get("resources", {})
        sim = runtime.get("simulated_ms")
        wall = runtime.get("elapsed_wall_ms_after_reset")
        threads, fds = resources.get("threads_range"), resources.get("file_descriptors_range")
        lines.append(f"| {name} | {fmt(sim/1000 if number(sim) else None)} / {fmt(wall/1000 if number(wall) else None)} | "
                     f"{fmt(runtime.get('simulated_wall_ratio_after_reset'))} | {fmt(runtime.get('substeps_per_processing_s'))} | "
                     f"{fmt(runtime.get('native_choices_per_processing_s'))} | {fmt(ratio(resources.get('peak_rss_kib'), 1024))} | "
                     f"{fmt(resources.get('cpu_total_s'))} | {threads[1] if threads else '?'} / {fds[1] if fds else '?'} |")
    realtime = report.get("runtime", {}).get("realtime", {})
    lines += ["", f"Realtime: максимальное отставание {fmt(realtime.get('max_realtime_lag_ms'), 0)} мс; "
              f"native choices {fmt(realtime.get('native_choices_per_wall_s_after_reset'))}/wall с. "
              "Native choice — событие родного AI для бойцов, а не policy observation/action или RL transition. Скорость GRU не измерялась."]
    rendered = report.get("rendered", {})
    lines += ["", f"Rendered диагностический прогон: wall с загрузкой {fmt(rendered.get('wall_s_including_load'))} с, "
              f"peak RSS {fmt(ratio(rendered.get('resources', {}).get('peak_rss_kib'), 1024))} MiB; "
              f"renderer GPU VRAM {fmt(rendered.get('renderer_gpu_vram_mib'), 0)} MiB (CPU lavapipe).", ""]
    resets = report.get("resets", {})
    lines += ["## Reset и IPC", "",
              f"Проверено {resets.get('count', 'нет данных')} resets: latency p95 {fmt((resets.get('latency_ms') or {}).get('p95'))} мс. "
              f"Warmed RSS {fmt(ratio(resets.get('warmed_initial_rss_kib'), 1024))} → {fmt(ratio(resets.get('warmed_final_rss_kib'), 1024))} MiB, "
              f"дельта {fmt(ratio(resets.get('warmed_rss_delta_kib'), 1024))} MiB; span FD {resets.get('file_descriptor_span')}, threads {resets.get('threads_span')}. "
              "Проверены поколения и одинаковое экспонированное начальное actor/animation state. Это не равенство всей памяти или будущего policy hidden state.", "",
              "IPC проверяет остановку lockstep без команды, `advance(until_decision)`, продолжение realtime при неполной JSONL строке, "
              "две команды в одном write и чистое закрытие. Это transport scheduling; внешний боевой API остаётся следующим этапом.", "",
              "## Общий сервер", "", "| Сервис | До | После |", "|---|---|---|"]
    before, after = report.get("services_before", {}), report.get("services_after", {})
    for unit in ("gothic-voxtral", "gothic-voxcpm"):
        state = lambda side: "/".join(side.get("services", {}).get(unit, {}).get(key, "нет данных") for key in ("ActiveState", "SubState"))
        lines.append(f"| {unit} | {state(before)} | {state(after)} |")
    lines += ["", f"Shared GPU snapshot до: `{before.get('gpu_shared_host')}`; после: `{after.get('gpu_shared_host')}`. "
              "Эти показатели относятся ко всему хосту. ASR/TTS/Strands latency не измеряли: `service_latency_measurement=null`; "
              "состояние units не доказывает отсутствие влияния на задержки.", "", "## Provenance и границы", ""]
    provenance = report.get("provenance", {})
    lines += [f"Хост `{provenance.get('host')}`, {provenance.get('game_version')}, fixed {provenance.get('sim_fps')} Hz. "
              f"Хешировано {provenance.get('source_files_hashed')} source files и {provenance.get('asset_files_hashed')} asset files; private assets/save в отчёт не копируются.", "",
              f"Серверный evidence: `{provenance.get('remote_run_dir', 'нет данных')}`; исходный archive: `{provenance.get('reference')}`. "
              "Manifest/raw журналы сохраняют полную конфигурацию; компактный JSON хранит идентифицирующие хеши.", "",
              "| Объект | SHA-256 |", "|---|---|"]
    for key in ("source_sha256", "binary_sha256", "engine_library_sha256", "reference_trace_sha256", "animation_fixture_sha256"):
        lines.append(f"| {key} | `{provenance.get(key)}` |")
    lines += ["", "Gate относится к native human 1H melee-v1 и отдельным sight/sound probes. PFX/spells, CPU morph rendering, "
              "archery, весь остров и навигационные задачи этим набором не подтверждены. GRU, action ownership/masks, "
              "обучающий recorder, BC/PPO и несколько параллельных арен остаются следующими этапами.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run_dir, output = args.run_dir.resolve(), args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        suite, manifest = read_json(run_dir / "suite.json"), read_json(run_dir / "manifest.json")
        report = summarize(suite, manifest, run_dir)
        report["inputs"] = {"run_dir": str(run_dir), "suite_sha256": sha256(run_dir / "suite.json"),
                            "manifest_sha256": sha256(run_dir / "manifest.json")}
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = {"schema_version": 1, "status": "incomplete", "issues": [str(error)], "gates": []}
    report["generated_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    (output / "headless-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    (output / "headless-report.md").write_text(markdown(report, run_dir, output))
    print(f"body headless report {report['status']}: {output / 'headless-report.md'}")
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
