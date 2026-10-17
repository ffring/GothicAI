#!/usr/bin/env python3
"""Auxiliary external CPU trace around one episode of an unchanged frozen driver.

Pass the original driver's complete command line after ``--``. Only the exact
native runner argv gains --scheduling-diagnostics. No result from this wrapper
is quality, training or model-selection evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time


TARGET = "realtime-native_duel-223-1-gru"
SAMPLER_OVERRIDES = {"max_samples": 4096, "sampler_capacity_bytes": 32*1024*1024}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen source: "+str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def source_inventory():
    result = {}
    for name, module in sorted(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if filename and (name.startswith("body_") or name.startswith("gothic_body")):
            path = Path(filename).resolve()
            if path.is_file():
                result[name] = {"path": str(path), "sha256": sha256(path)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostics-script", type=Path, required=True)
    parser.add_argument("--sampler-source", type=Path, required=True)
    parser.add_argument("--profile-output", type=Path, required=True)
    parser.add_argument("driver_argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("this wrapper and the original model driver run only on dev-ubuntu")
    forwarded = args.driver_argv[1:] if args.driver_argv[:1] == ["--"] else args.driver_argv
    originals = argparse.ArgumentParser(add_help=False)
    for name in ("runner", "config", "output", "policy-code-root", "gru-checkpoint", "mlp-checkpoint"):
        originals.add_argument("--"+name, type=Path, required=True)
    originals.add_argument("--phase", required=True)
    originals.add_argument("--device", default="cpu")
    originals.add_argument("--case", action="append")
    original, _ = originals.parse_known_args(forwarded)
    if original.phase != "realtime" or original.device != "cpu" or original.case:
        parser.error("profile requires the unchanged complete ordinary realtime CPU phase")
    driver_source = args.diagnostics_script.resolve()
    sampler_source = args.sampler_source.resolve()
    code_root = original.policy_code_root.resolve()
    if driver_source != code_root/"scripts/body_policy_diagnostics.py":
        parser.error("driver source and frozen policy-code-root must match")
    args.profile_output = args.profile_output.resolve()
    args.profile_output.mkdir(exist_ok=False)
    original_argv = [str(driver_source), *forwarded]
    report = {"schema_version": 1, "kind": "body_native_model_profile", "status": "failed",
              "diagnostic_only": True, "quality_credit": False, "selection_credit": False,
              "quality_gate_evaluated": False, "target_episode": TARGET,
              "driver": {"path": str(driver_source), "sha256": sha256(driver_source)},
              "sampler": {"path": str(sampler_source), "sha256": sha256(sampler_source)},
              "wrapper_sha256": sha256(Path(__file__)), "original_argv": original_argv,
              "sampler_profile_overrides": SAMPLER_OVERRIDES,
              "config": {"path": str(original.config.resolve()), "sha256": sha256(original.config)},
              "candidate_checkpoints": {name: {"path": str(path.resolve()), "sha256": sha256(path)}
                  for name, path in (("gru", original.gru_checkpoint), ("mlp", original.mlp_checkpoint))},
              "native_binary": {"path": str(original.runner.resolve()), "sha256": sha256(original.runner)},
              "native_launches": [], "target_matches": 0, "sampling": None}
    save(args.profile_output/"profile.json", report)
    old_argv, old_path = sys.argv, list(sys.path)
    old_popen = subprocess.Popen
    driver = sampler_module = sampler = None
    old_episode_log = old_terminate = None

    def stop_sampler(reason):
        nonlocal sampler
        if sampler is not None:
            active, sampler = sampler, None
            report["sampling"]["stop_begin_wall_monotonic_ns"] = time.monotonic_ns()
            active.stop(args.profile_output)
            report["sampling"].update(stop_end_wall_monotonic_ns=time.monotonic_ns(), stop_reason=reason,
                                      samples=len(active.rows), bytes=active.bytes, error=active.error)
            save(args.profile_output/"profile.json", report)

    try:
        sys.argv = original_argv
        sys.path.insert(0, str(driver_source.parent))
        # Original imports own all runtime/helper identities before the sampler
        # resolves its shared stdlib helpers from the already frozen modules.
        driver = load_module("body_policy_diagnostics", driver_source)
        sampler_module = load_module("body_native_frozen_sampler", sampler_source)
        sampler_module.PROFILE = {**sampler_module.PROFILE, **SAMPLER_OVERRIDES}
        report["effective_sampler_profile"] = {key: sampler_module.PROFILE[key] for key in
            ("sampler_ms", "thread_sampler_ms", "max_samples", "max_threads", "sampler_capacity_bytes")}
        report["imported_modules"] = source_inventory()
        old_episode_log = driver.DiagnosticRunner.episode_log
        old_terminate = driver.DiagnosticRunner.terminate

        def traced_popen(argv, *positional, **keywords):
            native = (isinstance(argv, (list, tuple)) and bool(argv) and
                      Path(argv[0]).resolve() == original.runner.resolve())
            if native:
                before = list(argv)
                argv = [*before, "--scheduling-diagnostics"] if "--scheduling-diagnostics" not in before else before
                launch = {"original_argv": before, "instrumented_argv": argv,
                          "added_flags": [] if argv == before else ["--scheduling-diagnostics"],
                          "wall_monotonic_ns": time.monotonic_ns()}
                process = old_popen(argv, *positional, **keywords)
                launch["pid"] = process.pid
                report["native_launches"].append(launch)
                return process
            return old_popen(argv, *positional, **keywords)

        def traced_episode_log(runner, output):
            nonlocal sampler
            stop_sampler("next_episode")
            value = old_episode_log(runner, output)
            if Path(output).name == TARGET:
                report["target_matches"] += 1
                if report["target_matches"] != 1:
                    raise RuntimeError("target episode repeated; diagnostic is not a retry loop")
                sampler = sampler_module.Sampler(runner.process.pid)
                report["sampling"] = {"pid": runner.process.pid, "episode_output": str(Path(output).resolve()),
                    "start_wall_monotonic_ns": time.monotonic_ns(), "before_target_reset": True,
                    "metadata": sampler.metadata}
                save(args.profile_output/"profile.json", report)
                sampler.start()
            return value

        def traced_terminate(runner):
            stop_sampler("runner_terminate")
            return old_terminate(runner)

        subprocess.Popen = traced_popen
        driver.DiagnosticRunner.episode_log = traced_episode_log
        driver.DiagnosticRunner.terminate = traced_terminate
        report["original_driver_returncode"] = driver.main()
        stop_sampler("original_main_returned")
        if report["target_matches"] != 1:
            raise RuntimeError("the original phase did not reach the declared target episode")
        if report["sampling"].get("error") is not None:
            raise RuntimeError("external sampler failed: "+str(report["sampling"]["error"]))
        report["status"] = "collected" if report["original_driver_returncode"] == 0 else "original_phase_failed"
        return int(report["original_driver_returncode"] or 0)
    except Exception as error:
        report["error"] = str(error)
        return 1
    finally:
        try:
            stop_sampler("wrapper_finally")
        except Exception as error:
            report["sampler_stop_error"] = str(error)
        subprocess.Popen = old_popen
        if driver is not None and old_episode_log is not None:
            driver.DiagnosticRunner.episode_log = old_episode_log
            driver.DiagnosticRunner.terminate = old_terminate
        report["imported_modules_final"] = source_inventory()
        sampling = report.get("sampling")
        if sampling is not None:
            paths = [args.profile_output/name for name in ("external-samples.jsonl", "external-metadata.json")]
            paths.extend(Path(sampling["episode_output"])/name for name in
                ("observer-timing.jsonl", "observations.jsonl", "events.jsonl", "ipc.jsonl",
                 "collector-transport.jsonl", "policy-inputs.jsonl", "body-control-events.jsonl"))
            report["raw_artifacts"] = {str(path): {"sha256": sha256(path), "bytes": path.stat().st_size}
                                       for path in paths if path.is_file()}
        report["end_wall_monotonic_ns"] = time.monotonic_ns()
        save(args.profile_output/"profile.json", report)
        sys.argv, sys.path[:] = old_argv, old_path


if __name__ == "__main__":
    raise SystemExit(main())
