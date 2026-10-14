#!/usr/bin/env python3
"""Independent, model-free stage 7 evidence checks over frozen server artifacts."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import body_trajectory_dataset as canonical
from body_trajectory_report import production_services
from body_arena_report import (actors, clean_reset, command_evidence, control_loss_issues,
                               distance, linked_mechanics, semantic_initial, timed_pairs)

ACTIONS = canonical.ACTIONS


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def number(value: Any, name: str) -> float:
    require(type(value) in (int, float) and math.isfinite(value), f"nonfinite/nonnumeric {name}")
    return float(value)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            result.update(block)
    return result.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return canonical.read_json(path)


def read_value(path: Path) -> Any:
    return canonical.strict_json(path.read_text())


def artifact(root: Path, relative: Any) -> Path:
    require(isinstance(relative, str) and bool(relative), "artifact reference missing")
    path = (root/relative).resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(), f"artifact escapes run or is missing: {relative}")
    return path


def is_cache_path(path: Path | str) -> bool:
    return bool({".pytest_cache","__pycache__"}.intersection(Path(path).parts))


def verify_executable_inventory(root: Path, files: dict[str, str]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    checked, ignored = {}, []
    for name,sha in files.items():
        path = (root/name).resolve()
        require(path.is_relative_to((root/"code").resolve()), "executable binding outside frozen code")
        if is_cache_path(name):
            ignored.append({"path":str(path),"declared_sha256":sha,"current_sha256":digest(path) if path.is_file() else None})
            continue
        require(path.is_file() and digest(path) == sha, f"frozen executable changed: {name}")
        checked[name] = sha
    return checked,ignored


def closed_approval_flow(source: str) -> bool:
    """Inspect the frozen collector; never execute its historical capture code."""
    tree = ast.parse(source)
    main = next((node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name == "main"),None)
    if main is None:return False
    body = next((node.body for node in main.body if isinstance(node,ast.Try)),[])
    def calls(node, name):
        return [part for part in ast.walk(node) if isinstance(part,ast.Call) and ast.unparse(part.func) == name]
    def find(predicate):
        return next((index for index,node in enumerate(body) if predicate(node)),-1)
    close = find(lambda node:any(call.args and isinstance(call.args[0],ast.Dict) and
                 ast.literal_eval(call.args[0]) == {"cmd":"close"} for call in calls(node,"runner.call")))
    wait = find(lambda node:bool(calls(node,"runner.process.wait")))
    guard = find(lambda node:isinstance(node,ast.If) and ast.unparse(node.test) == "runner.process.returncode != 0" and
                 any(isinstance(part,ast.Raise) for part in node.body) and not node.orelse)
    bind = find(lambda node:bool(calls(node,"bind_closed_sources")))
    approve = find(lambda node:isinstance(node,ast.For) and bool(calls(node,"approve_scripted_demonstration")))
    analysis = find(lambda node:isinstance(node,ast.If) and ast.unparse(node.test) == "not args.smoke" and bool(calls(node,"build_dataset")))
    return 0 <= close < wait < guard < bind < approve < analysis


def compact_final_snapshot(full: dict[str, Any]) -> dict[str, Any]:
    require(type(full.get("game_ms")) is int and full["game_ms"] >= 0 and
            all(type(full.get(key)) is bool for key in ("terminated","truncated","infrastructure")) and
            all(isinstance(full.get(key),str) for key in ("outcome","reason")), "full demonstration final snapshot malformed")
    return {"t_game_ms":full["game_ms"],**{key:full[key] for key in ("terminated","truncated","infrastructure","outcome","reason")}}


def demonstration_snapshot_binding(record: dict[str, Any], saved: dict[str, Any],
                                   manifest: dict[str, Any], arena: list[dict[str, Any]]) -> None:
    require(record == saved, "source suite episode differs from its original episode.json")
    require(manifest.get("initial") == record.get("initial") and manifest.get("counts") == record.get("counts") and
            manifest.get("final") == compact_final_snapshot(record["final"]),
            "compact recorded final/initial/counts differ from full source episode")
    starts = [row.get("arena") for row in arena if row.get("kind") == "reset_ready"]
    ends = [row.get("arena") for row in arena if row.get("kind") == "terminal"]
    require(starts == [record["initial"]] and ends == [record["final"]],
            "source full initial/final differs from actual native reset/terminal journal")


def collection_recovery(root: Path, config: dict[str, Any]) -> dict[str, Any] | None:
    collection = root/"demonstrations"
    suite = read_json(collection/"suite.json")
    proof = suite.get("recovery_provenance")
    if proof is None:return None
    require(isinstance(proof,dict) and proof.get("schema_version") == 1 and
            proof.get("kind") == "closed_demonstrations_offline_recovery", "unknown demonstration recovery chain")
    source_run = Path(proof["source_run_directory"])
    source = Path(proof["source_directory"])
    require(source_run.is_absolute() and source.is_absolute() and source_run.resolve() != root.resolve() and
            source.resolve() == (source_run/"demonstrations").resolve(), "recovery capture source root differs")
    source_run, source = source_run.resolve(), source.resolve()
    def bound(field, expected=None):
        require(isinstance(field,dict) and isinstance(field.get("path"),str), "recovery source binding malformed")
        path = Path(field["path"])
        require(path.is_absolute() and path.resolve().is_relative_to(source_run) and path.is_file(), "recovery source outside declared capture run")
        path = path.resolve()
        require(expected is None or path == expected.resolve(), "recovery source binding references another file")
        require(digest(path) == field.get("sha256"), "immutable recovery source SHA differs")
        return path
    source_suite_path = bound(proof["source_suite"],source/"suite.json")
    original = read_json(source_suite_path)
    require(original.get("kind") == "body_demonstrations_stage7_suite" and original.get("suite_scope") == "full" and
            original.get("status") == proof["source_suite"].get("status") == "infrastructure_failure" and
            original.get("error") in (None,"") and proof["source_suite"].get("error") == original.get("error"),
            "recovery would conceal a capture/teacher failure")
    old_manifest = read_json(bound(proof["source_manifest"],source/"manifest.json"))
    new_manifest = read_json(collection/"manifest.json")
    require(new_manifest.get("recovery_provenance") == proof, "recovered manifest/suite provenance differs")
    old_environment = read_json(bound(proof["captured_environment"],source_run/"environment.json"))
    require(Path(proof["captured_code_directory"]).resolve() == source_run/"code", "recovery captured executable directory differs")
    files = old_environment.get("frozen_files")
    require(isinstance(files,dict) and files, "original frozen executable hashes missing")
    require(proof.get("captured_code_sha256") == files and proof.get("validator_sha256") == digest(Path(canonical.__file__)) and
            proof.get("source_mutation") is False and proof.get("model_forward_performed") is False and proof.get("dataset_built") is False,
            "recovery executable inventory/validator or offline-only declaration differs")
    _,ignored_cache = verify_executable_inventory(source_run,files)
    declared_ignored = proof.get("ignored_cache_files")
    require(proof.get("inventory_cache_exclusions") == [".pytest_cache","__pycache__"] and isinstance(declared_ignored,list) and
            {(row.get("path"),row.get("declared_sha256")) for row in declared_ignored} ==
            {(row["path"],row["declared_sha256"]) for row in ignored_cache} and
            all(row.get("current_sha256") is None or isinstance(row.get("current_sha256"),str) and
                re.fullmatch(r"[0-9a-f]{64}",row["current_sha256"]) for row in declared_ignored),
            "recovery ignored-cache declaration exceeds exact original cache paths")
    require(digest(source_run/"source.tar.gz") == old_environment.get("source_archive_sha256") and
            digest(source_run/"reference/gothic-body-runner") == old_environment.get("binary_sha256") ==
            read_json(root/"environment.json").get("binary_sha256"), "original capture archive/binary provenance differs")
    bound(proof.get("source_archive"),source_run/"source.tar.gz")
    bound(proof.get("source_binary"),source_run/"reference/gothic-body-runner")
    driver = source_run/"code/scripts/body_demonstration_suite.py"
    require(digest(driver) == proof.get("collected_driver_sha256") == old_manifest.get("collector_sha256") ==
            new_manifest.get("collector_sha256") == new_manifest.get("collected_driver_sha256"), "historical capture driver SHA differs")
    recovery_driver = root/"code/scripts/body_demonstration_recover.py"
    require(digest(recovery_driver) == proof.get("recovery_driver_sha256") == new_manifest.get("recovery_driver_sha256"), "offline recovery driver SHA differs")
    require(digest(source_run/"code/evals/npc-body/body-demonstrations-v1.json") ==
            digest(root/"code/evals/npc-body/body-demonstrations-v1.json") and
            digest(source/"teacher-registry.json") == digest(collection/"teacher-registry.json") and
            digest(source_run/"code/scripts/body_demonstration_teacher.py") ==
            digest(root/"code/scripts/body_demonstration_teacher.py"), "recovery changed teacher rules/configuration")
    bound(proof.get("source_config"),source_run/"code/evals/npc-body/body-demonstrations-v1.json")
    bound(proof.get("source_registry"),source/"teacher-registry.json")
    inherited = proof.get("source_stage6_dataset",{})
    require(inherited.get("path") == original.get("existing_dataset") and inherited.get("sha256") == original.get("existing_dataset_sha256") and
            Path(inherited["path"]).is_file() and digest(Path(inherited["path"])) == inherited["sha256"], "recovery changed inherited stage6 data")
    timeout = proof.get("original_timeout",{})
    require(timeout.get("reason") == "unit_timeout_after_closed_collection_and_teacher_approvals" and
            timeout.get("backend_exit_evidence") == "captured_zero_returncode_guard_before_closed_binding_and_all_approvals",
            "recovery is not the declared post-approval analysis timeout")
    guard = timeout.get("guard_proof",{})
    bound(guard.get("collected_driver"),driver)
    require(guard.get("verified_control_flow") is True and guard.get("backend_returncode") == 0 and guard.get("inferred") is True and
            closed_approval_flow(driver.read_text()), "engine zero-exit inference is not proven by frozen collector ordering")
    timeout_path = bound(timeout.get("evidence"),source_run/"launcher-demos.log")
    log = timeout_path.read_text()
    require("Finished with result: timeout" in log and "code=killed, status=15/TERM" in log,
            "source process did not end with the declared unit timeout")
    for record in original.get("episodes",[]):
        require(f"[{record['name']}] declared real actions completed" in log, "original collection did not finish every declared episode")
    launcher = (source_run/"code/scripts/body-bc-remote.sh").read_text()
    require(all(value in launcher for value in ("budget=900","--property=CPUQuota=200%","--property=MemoryMax=6G",
            "--property=TasksMax=128",'--property="RuntimeMaxSec=$budget"')), "original timeout phase lacks the enforced resource limits")
    peak = re.search(r"Memory peak:\s*([0-9.]+)([KMGT]?)",log)
    require(peak is not None and 0 < float(peak[1])*1024**("KMGT".index(peak[2])+1 if peak[2] else 0) <=
            config["resources"]["memory_max_gib"]*1024**3, "original bounded unit peak memory missing or exceeded")
    source_resources = proof.get("source_resources",{})
    source_time = bound(source_resources,source_run/"resources-demos.txt")
    require(source_resources.get("empty") is True and source_resources.get("measurement_available") is False and
            source_time.read_bytes() == b"", "original missing process RSS/exit measurement was rewritten")
    services = proof.get("source_service_evidence",{})
    before = read_json(bound(services.get("before"),source_run/"services-demos-before.json"))
    after = read_json(bound(services.get("after"),source_run/"services-demos-after.json"))
    stable = all_services(before,after,config["resources"]["required_services"])
    require(before.get("gpu") and after.get("gpu") and before.get("recorded_at_utc") and
            before["recorded_at_utc"] <= after.get("recorded_at_utc",""), "original recovery capture resource snapshots missing/reversed")
    borrowed = proof.get("borrowed_episodes")
    require(isinstance(borrowed,list) and len(borrowed) == len(original.get("episodes",[])) == len(suite.get("episodes",[])) and
            {row["name"] for row in borrowed} == {row["name"] for row in original["episodes"]} == {row["name"] for row in suite["episodes"]},
            "recovery borrowed episode set differs from original capture")
    for row in borrowed:
        directory = Path(row["source_dir"]).resolve()
        require(directory == source/"episodes"/row["name"], "recovery episode is outside exact original directory")
        link = collection/"episodes"/row["name"]
        require(link.is_symlink() and link.resolve() == directory, "recovered episode alias has another target")
        actual = {}
        for file in directory.rglob("*"):
            if is_cache_path(file.relative_to(directory)):continue
            require(not file.is_symlink(), "original recovery episode contains an unbound symlink")
            if file.is_file():actual[str(file.relative_to(directory))] = digest(file)
        require(actual == row.get("file_sha256"), "recovery episode source inventory changed")
        original_record = next(record for record in original["episodes"] if record["name"] == row["name"])
        saved = read_json(directory/"episode.json")
        arena = canonical.read_jsonl(directory/"arena-events.jsonl")
        approved_manifest = read_json(directory/"trajectory-manifest.json")
        original_manifest = read_json(directory/"trajectory-manifest-original.json")
        demonstration_snapshot_binding(original_record,saved,approved_manifest,arena)
        demonstration_snapshot_binding(original_record,saved,original_manifest,arena)
        require(original_manifest.get("records_file") == "records.jsonl" and approved_manifest == {
            **original_manifest,"records_file":"records-approved.jsonl",
            "scripted_expert_approval":approved_manifest.get("scripted_expert_approval"),
            "source_files":original_manifest["source_files"]+[
                {"path":"records-approved.jsonl","sha256":digest(directory/"records-approved.jsonl")},
                {"path":"trajectory-manifest-original.json","sha256":digest(directory/"trajectory-manifest-original.json")}]},
            "approval changed original manifest metadata beyond separate records/proof bindings")
        recovered_record = next(record for record in suite["episodes"] if record["name"] == row["name"])
        require({key:value for key,value in recovered_record.items() if key not in ("approved_actions","approval_sha256")} == original_record,
                "recovery changed full original episode facts")
    close = proof.get("engine_close",{})
    closing = next((row for row in borrowed if row["name"] == close.get("episode")),None)
    require(closing is not None, "engine close is outside borrowed episodes")
    ipc_path = bound({"path":close.get("ipc_file"),"sha256":close.get("sha256")},Path(closing["source_dir"])/"ipc.jsonl")
    ipc = canonical.read_jsonl(ipc_path)
    request, response = ipc[close["request_index"]],ipc[close["response_index"]]
    require(close["request_index"] < close["response_index"] and request.get("direction") == "request" and response.get("direction") == "response" and
            request.get("value",{}).get("cmd") == "close" and request["value"].get("id") == response.get("value",{}).get("id") == close.get("id") and
            response["value"].get("done") is close.get("done") is True and response["value"].get("ok") is close.get("ok") is True,
            "closed collection has no matching actual native close receipt")
    final_record = next(row for row in original["episodes"] if row["name"] == close["episode"])
    capture = response["value"].get("body_trajectory",{})
    require(close["response_index"] == close["request_index"]+1 == len(ipc)-1 and
            sum(row.get("direction") == "request" and row.get("value",{}).get("cmd") == "close" for row in ipc) == 1 and
            response["value"].get("arena") == final_record["final"] and capture == close.get("capture") and
            all(type(capture.get(field)) is int and capture[field] >= 0 for field in
                ("queue_size","dropped_total","reset_discarded_total","capture_failures_total","emitted","drained")) and
            capture.get("queue_size") == capture.get("dropped_total") == capture.get("reset_discarded_total") == capture.get("capture_failures_total") == 0 and
            capture.get("capture_failed") is False and capture.get("emitted") == capture.get("drained") and
            request.get("wall_monotonic") == close.get("request_wall_monotonic") <= close.get("response_wall_monotonic") == response.get("wall_monotonic"),
            "closed collection native final/capture queues or close causal order differs")
    return {"provenance":proof,"source_directory":str(source),"borrowed_episodes":{row["name"]:row for row in borrowed},
            "capture_unit_result":"timeout","capture_unit_peak_memory_display":peak[1]+peak[2],
            "capture_unit_peak_memory_bytes_from_rounded_display":float(peak[1])*1024**("KMGT".index(peak[2])+1 if peak[2] else 0),
            "capture_unit_cpu_time_display":next(iter(re.findall(r"CPU time consumed:\s*([^\n]+)",log)),None),
            "capture_unit_runtime_display":next(iter(re.findall(r"Service runtime:\s*([^\n]+)",log)),None),
            "capture_process_peak_rss_kib":None,"capture_process_exit_status":None,"engine_returncode_inferred_from_frozen_flow":0,
            "capture_production_services":stable,"ignored_cache_files_current":ignored_cache}


def frozen_environment(root: Path) -> dict[str, Any]:
    environment = read_json(root/"environment.json")
    files = environment.get("frozen_files")
    require(isinstance(files, dict) and bool(files), "frozen executable file hashes missing")
    require(any(name.endswith("/scripts/body_bc_report.py") for name in files), "frozen independent reporter missing")
    checked,ignored_cache = verify_executable_inventory(root,files)
    reporter = Path(__file__).resolve()
    audit = None
    if reporter != (root/"code/scripts/body_bc_report.py").resolve():
        audit_root = root/"validation/audit-code"
        require(reporter == (audit_root/"scripts/body_bc_report.py").resolve(), "external reporter is not the declared frozen audit")
        audit = read_json(audit_root/"audit.json")
        require(audit.get("kind") == "independent_reporter_repair_snapshot" and
                audit.get("frozen_run_environment_sha256") == digest(root/"environment.json") and
                audit.get("original_report_sha256") == digest(root/"validation/body-bc.json") and
                audit.get("config_sha256") == digest(root/"code/evals/npc-body/body-bc-v1.json") and
                audit.get("dataset_helper_sha256") == digest(Path(canonical.__file__)), "independent audit changed original experiment evidence")
        require(isinstance(audit.get("files"),dict) and audit["files"].get("scripts/body_bc_report.py") == digest(reporter),
                "executing repaired reporter is not frozen by audit manifest")
        for name,sha in audit["files"].items():
            require(digest(artifact(audit_root,name)) == sha, "independent audit source changed")
    for module in (Path(canonical.__file__).resolve(),*[Path(sys.modules[name].__file__).resolve() for name in
            ("body_trajectory_report","body_arena_report","body_api_report") if name in sys.modules]):
        relative = str(module.relative_to(root.resolve())) if module.is_relative_to(root.resolve()) else None
        require(relative in files and digest(module) == files[relative], "executing reporter/validator is not the frozen run code")
    if audit is None:
        require(digest(reporter) == files.get("code/scripts/body_bc_report.py"), "executing reporter changed")
    require(digest(root/"source.tar.gz") == environment.get("source_archive_sha256"), "frozen source archive changed")
    require(digest(root/"reference/gothic-body-runner") == environment.get("binary_sha256"), "frozen native runner changed")
    return {"files": len(checked), "ignored_cache_files":ignored_cache,"source_archive_sha256": environment["source_archive_sha256"],
            "binary_sha256": environment["binary_sha256"], "reporter_sha256": digest(reporter),
            "original_reporter_sha256":files["code/scripts/body_bc_report.py"],
            "independent_reporter_audit":None if audit is None else {"audit_manifest_sha256":digest(root/"validation/audit-code/audit.json"),
                "original_report_sha256":audit["original_report_sha256"],
                "reason":"exact_received_IPC_binding_with_native_finite_float_roundtrip_at_most_2_ULP"},
            "packages": environment.get("packages"), "python": environment.get("python")}


def classification(predictions: list[dict[str, Any]], class_weights: list[float]) -> dict[str, Any]:
    """Reconstruct metrics from each actual masked prediction, rather than flags."""
    require(len(class_weights) == len(ACTIONS), "train class weights have wrong length")
    weights = [number(value, "class weight") for value in class_weights]
    require(all(value >= 0 for value in weights), "negative class weight")
    matrix = [[0 for _ in ACTIONS] for _ in ACTIONS]
    losses, weighted, weight_sum = 0., 0., 0.
    for row in predictions:
        truth, chosen = row.get("true_action"), row.get("chosen_action")
        require(type(truth) is int and 1 <= truth < len(ACTIONS), "prediction has no real non-continue expert label")
        require(type(chosen) is int and 0 <= chosen < len(ACTIONS), "prediction action outside schema")
        mask, probabilities = row.get("action_mask"), row.get("action_log_probs")
        require(isinstance(mask, list) and len(mask) == len(ACTIONS) and all(type(bit) is bool for bit in mask), "prediction mask malformed")
        require(mask[truth] and mask[chosen], "expert or predicted action outside recorded model-view mask")
        require(isinstance(probabilities, list) and len(probabilities) == len(ACTIONS), "prediction probabilities malformed")
        valid = []
        for action, (allowed, value) in enumerate(zip(mask, probabilities)):
            if allowed:
                log_prob = number(value, "allowed action log-probability")
                require(log_prob <= 1e-6, "positive action log-probability")
                valid.append((action, log_prob))
            else:
                require(value is None, "masked action log-probability must be null")
        require(math.isclose(sum(math.exp(value) for _, value in valid), 1., rel_tol=1e-5, abs_tol=1e-5), "masked probabilities do not sum to one")
        require(chosen == max(valid, key=lambda pair: pair[1])[0], "prediction does not equal masked argmax")
        loss = -probabilities[truth]
        require(math.isclose(number(row.get("true_action_nll"), "true-action NLL"), loss, rel_tol=1e-6, abs_tol=1e-6), "recorded NLL differs from probabilities")
        require(math.isclose(number(row.get("train_class_weight"), "prediction class weight"), weights[truth], rel_tol=1e-7, abs_tol=1e-7), "prediction weight differs from train-only normalizer")
        matrix[truth][chosen] += 1
        losses += loss; weighted += weights[truth]*loss; weight_sum += weights[truth]
    require(bool(predictions) and weight_sum > 0, "held-out predictions contain no weighted expert labels")
    per_action = {}
    for action, name in enumerate(ACTIONS):
        tp = matrix[action][action]; support = sum(matrix[action]); fp = sum(row[action] for row in matrix)-tp; fn = support-tp
        precision = tp/(tp+fp) if tp+fp else 0.
        recall = tp/support if support else 0.
        f1 = 2*precision*recall/(precision+recall) if precision+recall else 0.
        per_action[name] = {"action_id": action, "support": support, "tp": tp, "fp": fp, "fn": fn,
                            "precision": precision, "recall": recall, "f1": f1}
    return {"confusion_matrix": matrix, "per_action": per_action, "count": len(predictions),
            "accuracy": sum(matrix[i][i] for i in range(len(ACTIONS)))/len(predictions),
            "macro_f1": sum(per_action[ACTIONS[i]]["f1"] for i in range(1, len(ACTIONS)))/(len(ACTIONS)-1),
            "unweighted_nll": losses/len(predictions), "weighted_nll": weighted/weight_sum}


def choose_epoch(history: list[dict[str, Any]], selection_split: str = "eval") -> int:
    require(bool(history), "training epoch history missing")
    require(selection_split == "eval", "checkpoint selected from test/OOD/train")
    epochs = []
    for row in history:
        require(type(row.get("epoch")) is int and row["epoch"] >= 0 and row["epoch"] not in epochs, "epoch history duplicate/malformed")
        epochs.append(row["epoch"])
        require(isinstance(row.get("eval"), dict), "epoch lacks eval metrics")
        number(row["eval"].get("macro_f1"), "eval macro F1"); number(row["eval"].get("nll"), "eval NLL")
        require(not any(key in row for key in ("test_metrics", "ood_metrics", "test_macro_f1", "ood_macro_f1")), "held-out test/OOD entered epoch selection history")
    return max(history, key=lambda row: (row["eval"]["macro_f1"], -row["eval"]["nll"], -row["epoch"]))["epoch"]


def effective_mask(observation: dict[str, Any]) -> list[bool]:
    mask = observation.get("action_mask", observation.get("availability_mask"))
    require(isinstance(mask, list) and len(mask) == 12 and all(type(bit) is bool for bit in mask), "source mask malformed")
    result = list(mask)
    if not any(result):
        result[0] = True  # A derived memory-only token; the engine raw mask stays empty.
    elif observation.get("native_decision_eligible") is True and any(result[1:]):
        result[0] = False
    return result


def runtime_decision(observation: dict[str, Any], request: dict[str, Any], response: dict[str, Any]) -> int | None:
    """Recompute the categorical choice separately from permission to command."""
    require(type(request.get("command_enabled")) is bool and type(request.get("memory_only")) is bool,
            "runtime command/memory mode missing or malformed")
    memory_only = not request["command_enabled"] or observation.get("can_decide") is not True
    require(request["memory_only"] == memory_only and response.get("memory_only") is memory_only,
            "runtime memory-only mode differs from physical command eligibility")
    mask, logits = effective_mask(observation), response.get("logits")
    require(isinstance(logits, list) and len(logits) == 12 and
            all(type(value) in (int, float) and math.isfinite(value) for value in logits),
            "runtime logits not finite/schema-compatible")
    chosen = max((i for i, allowed in enumerate(mask) if allowed), key=lambda i: logits[i])
    eligible = observation.get("can_decide") is True and observation.get("action_mask_valid") is True and not memory_only
    action = chosen if eligible and chosen else None
    require(response.get("selected_action_id") == chosen and response.get("action_id") == action and
            response.get("new_decision") is (action is not None),
            "runtime selected action or memory-only command differs from masked scores")
    return action


def overfit_source_support(selected: list[dict[str, Any]], chunks: list[dict[str, Any]],
                           included_actions: list[int]) -> dict[str, int]:
    def key(row):
        return (row.get("episode_id"), tuple(row.get("identity", [])),
                row.get("first_observation_seq"), row.get("last_observation_seq"))
    sources = {key(row): row for row in chunks}
    require(len(sources) == len(chunks), "overfit source chunk boundaries duplicated")
    seen, support = set(), Counter()
    for row in selected:
        identity = key(row)
        require(identity not in seen and identity in sources and sources[identity]["split"] == "train",
                "overfit selected chunk duplicates, invents or leaks a source sequence")
        seen.add(identity)
        support.update(sources[identity]["support_by_action"])
    require(all(support[action] > 0 for action in included_actions),
            "overfit selected subset lacks genuine loss-row support for an included action")
    return {ACTIONS[action]: support[action] for action in included_actions}


class PopulationMoments:
    def __init__(self):
        self.count, self.mean, self.m2 = 0, {}, {}

    def add(self, values):
        require(not self.mean or values.keys() == self.mean.keys(), "training feature schema changed")
        self.count += 1
        for key,value in values.items():
            delta = value-self.mean.get(key,0.)
            self.mean[key] = self.mean.get(key,0.)+delta/self.count
            self.m2[key] = self.m2.get(key,0.)+delta*(value-self.mean[key])

    def variance(self, key):
        return max(0.,self.m2[key]/self.count)


def sum_episode_coverage(target, incoming):
    for name in ("expert_labels_by_action","usable_labels_by_action","condition_observations"):
        for key,value in incoming[name].items():target[name][key] = target[name].get(key,0)+value
    for name in ("episode_outcomes_by_action","execution_outcomes_by_action","usable_labels_by_source","condition_labels"):
        for key,values in incoming[name].items():
            into = target[name].setdefault(key,{})
            for item,value in values.items():into[item] = into.get(item,0)+value
    threshold = target["minimum_true_expert_labels_per_included_class"]
    target["insufficient_classes"] = [{"action_id":action,"action":ACTIONS[action],
        "usable_expert_labels":target["usable_labels_by_action"][ACTIONS[action]],"required":threshold,
        "missing":threshold-target["usable_labels_by_action"][ACTIONS[action]]}
        for action in target["included_actions"] if target["usable_labels_by_action"][ACTIONS[action]] < threshold]
    target["missing_conditions"] = [name for name,value in target["condition_observations"].items() if value == 0]
    target["diagnostic_bc_coverage_ready"] = not target["insufficient_classes"] and not target["missing_conditions"]


def dataset_evidence(root: Path, config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    path = root/"dataset"
    index, normalizer = read_json(path/"dataset.json"), read_json(path/"normalization.json")
    cfg = config["dataset"]
    require(index.get("schema_version") == 1 and index.get("feature_version") == cfg["feature_version"] and
            index.get("action_version") == cfg["action_version"] and index.get("actions") == list(ACTIONS), "model dataset schema/action order differs")
    require(index.get("model_sampling") == {"kind": "periodic_only", "version": 1,
            "max_label_age_game_ms": cfg["max_label_age_game_ms"], "ambiguous": "drop_all", "recurrent_updates": "periodic_only"}, "model sampling profile differs")
    require(digest(path/"normalization.json") == index.get("normalization_sha256"), "model normalizer SHA differs")
    inherited = index.get("inherited_assignments", {})
    source = inherited.get("source", {})
    inherited_path = Path(source.get("path", ""))
    require(inherited_path.is_file() and digest(inherited_path) == source.get("sha256"), "immutable stage6 split source missing/changed")
    old = read_json(inherited_path)
    require(inherited.get("groups") == old.get("groups") and all(index["groups"].get(key) == split for key, split in old["groups"].items()), "stage6 group assignments changed")
    registry = index.get("teacher_registry")
    require(isinstance(registry, dict) and canonical.registry_sha256(registry) == index.get("teacher_registry_sha256"), "approved expert registry missing/changed")
    headers = [canonical.ValidatedEpisode(Path(entry["path"]),read_json(Path(entry["path"])/entry["manifest_file"]),[],{},{},{},{})
               for entry in index["episodes"]]
    require(len(headers) == index.get("episode_count"), "model dataset episode count differs")
    assignments = canonical.split_groups(headers, split_seed=cfg["split_seed"], existing_assignments=old["groups"], group_overrides=inherited.get("overrides"))
    require(assignments == index["groups"], "new split groups differ from declared inherited assignment")
    train_ids = [episode.manifest["episode_id"] for episode in headers if assignments[canonical.group_key(episode.manifest)] == "train"]
    require(normalizer.get("source_split") == "train" and normalizer.get("feature_version") == cfg["feature_version"] and
            normalizer.get("episode_ids") == train_ids, "normalizer leaks held-out data")
    names = normalizer["feature_names"]
    require(names == canonical.canonical_feature_names(cfg["feature_version"]), "model feature order differs")
    coverage = {split:canonical._coverage([],min_labels=cfg["minimum_true_expert_labels_per_included_class"],included_actions=cfg["included_actions"])
                for split in ("train","eval","test","ood")}
    labels, ids, epochs, hashes, moments, counts = {}, set(), set(), {}, PopulationMoments(), Counter()
    for entry in index["episodes"]:
        episode_path = Path(entry["path"])
        require(digest(episode_path/entry["manifest_file"]) == entry["manifest_sha256"] and digest(episode_path/entry["records_file"]) == entry["records_sha256"], "canonical episode bytes changed")
        episode = canonical.validate_episode(episode_path, teacher_registry=registry)
        identity = (episode.manifest["session_epoch"], episode.manifest["world_epoch"])
        require(episode.manifest["episode_id"] not in ids and identity not in epochs, "duplicated canonical episode/physical world")
        ids.add(episode.manifest["episode_id"]); epochs.add(identity)
        require(entry["group"] == canonical.group_key(episode.manifest) and entry["split"] == index["groups"][entry["group"]], "model dataset split/group differs")
        view = canonical.periodic_model_view(episode, max_label_age_game_ms=cfg["max_label_age_game_ms"])
        require(view.summary == entry["summary"], "persisted periodic projection differs from canonical source")
        require(all(row["observation"].get("periodic") is True and row["observation"]["phase"] == "post_simulation_post_animation" for row in view.observations.values()), "event-only/teacher snapshot entered recurrent memory")
        for key, transition in view.transitions.items():
            if transition["usable_for_bc"]:
                decision = view.decisions[key]
                row = view.observations[(key[0], decision["observation_seq"])]
                original = episode.decisions[key]
                require(original["expert"] and original["accepted"] and episode.transitions[key]["usable_for_bc"], "projection invented an expert label")
                age = original["t_game_ms"]-row["t_game_ms"]
                require(0 <= age <= cfg["max_label_age_game_ms"] and canonical.identity(row) == canonical.identity(original), "model label leaks future/generation")
                require(row["observation"]["native_decision_eligible"] is True and row["observation"]["availability_mask"][decision["action_id"]], "label outside causal physical opportunity")
                label_key = (entry["episode_id"], canonical.identity(row), row["observation_seq"])
                require(label_key not in labels, "multiple expert labels at one model input")
                labels[label_key] = {"split": entry["split"], "action_id": decision["action_id"], "action_mask": effective_mask(row["observation"]),
                                     "raw_mask": row["observation"].get("action_mask", row["observation"]["availability_mask"]),
                                     "t_game_ms": row["t_game_ms"], "decision_id": decision["decision_id"]}
        sum_episode_coverage(coverage[entry["split"]],canonical._coverage([view],min_labels=cfg["minimum_true_expert_labels_per_included_class"],included_actions=cfg["included_actions"]))
        if entry["split"] == "train":
            for record in view.observations.values():moments.add(canonical.record_features(record))
            counts.update(view.decisions[key]["action_id"] for key,transition in view.transitions.items() if transition["usable_for_bc"])
        for item in episode.manifest["source_files"]:
            p = episode_path/item["path"]; hashes[str(p)] = digest(p)
        del episode,view
    count = moments.count
    require(count > 0 and count == normalizer["observation_count"], "train population differs")
    for name in names:
        v = moments.variance(name); std = 1. if math.sqrt(v) < 1e-6 else math.sqrt(v)
        for field, actual in (("mean", moments.mean[name]), ("variance", v), ("std", std)):
            require(math.isclose(normalizer[field][name], actual, rel_tol=1e-8, abs_tol=1e-8), f"train-only {field} differs: {name}")
    require(normalizer["constant_features"] == [name for name in names if math.sqrt(moments.variance(name)) < 1e-6], "constant feature set differs")
    require(normalizer["class_counts"] == [counts[i] for i in range(12)], "class counts leak held-out/mixed-phase labels")
    classes, total = sum(counts[i] > 0 for i in range(1, 12)), sum(counts.values())
    weights = [0.] + [total/(classes*counts[i]) if counts[i] else 0. for i in range(1, 12)]
    require(normalizer["class_weights"] == weights, "class weights differ from effective train labels")
    sequences, train_chunks = {}, []
    for split in ("train", "eval", "test", "ood"):
        require(index["coverage"][split] == coverage[split], "coverage differs from effective periodic model view")
        observed = set(); chunks = 0
        for chunk in canonical.OrderedSequenceLoader(path, split=split, chunk_length=config["training"]["chunk_length"], burn_in=config["training"]["burn_in"]):
            chunks += 1
            require(chunk["reset"] is True and chunk["observations"][0]["reset"] is True, "recurrent boundary missing")
            for row in chunk["observations"]:
                require(row.get("periodic") is True and row["phase"] == "post_simulation_post_animation", "nonperiodic loader input")
                require(not row["burn_in"] or not row["loss_mask"], "burn-in enters supervised loss")
                if row["loss_mask"]:
                    key = (chunk["episode_id"], tuple(chunk["identity"]), row["observation_seq"])
                    require(key in labels and key not in observed and labels[key]["action_id"] == row["label"] and row["action_mask"] == labels[key]["action_mask"], "loader dropped/duplicated/masked a label")
                    observed.add(key)
            if split == "train":
                train_chunks.append({"episode_id":chunk["episode_id"],"identity":chunk["identity"],"split":split,
                    "first_observation_seq":chunk["observations"][0]["observation_seq"],
                    "last_observation_seq":chunk["observations"][-1]["observation_seq"],
                    "support_by_action":dict(Counter(row["label"] for row in chunk["observations"] if row["loss_mask"]))})
        expected = {key for key, value in labels.items() if value["split"] == split}
        require(observed == expected, "ordered loader loses effective expert labels")
        sequences[split] = {"chunks": chunks, "labels": len(observed)}
    return {"episodes": len(headers), "dataset_sha256": digest(path/"dataset.json"), "normalization_sha256": digest(path/"normalization.json"),
            "feature_count": len(names), "train_observations": count, "train_labels": total, "coverage": coverage,
            "sequences": sequences, "inherited_groups": old["groups"], "raw_sha256": hashes,
            "effective_coverage_ready": coverage["train"]["diagnostic_bc_coverage_ready"]}, {"index": index, "normalizer": normalizer, "labels": labels, "train_chunks":train_chunks}


def model_evidence(root: Path, architecture: str, config: dict[str, Any], dataset: dict[str, Any],
                   *, verification_device: str | None = None) -> dict[str, Any]:
    path, index, normalizer = root/"models"/architecture, dataset["index"], dataset["normalizer"]
    dataset_sha, normalization_sha = digest(root/"dataset/dataset.json"), digest(root/"dataset/normalization.json")
    expected_provenance = {"dataset_sha256": dataset_sha, "normalization_sha256": normalization_sha,
                           "dataset_code_sha256": digest(Path(canonical.__file__)), "feature_version": index["feature_version"],
                           "action_version": index["action_version"], "model_sampling": index["model_sampling"], "groups": index["groups"],
                           "train_episode_ids": [entry["episode_id"] for entry in index["episodes"] if entry["split"] == "train"]}
    verify = read_json(path/"verify.json")
    declared_verification_device = config["training"]["device"] if verification_device is None else verification_device
    require(verify.get("kind") == "body-bc-verification" and verify.get("architecture") == architecture and
            verify.get("dataset") == expected_provenance and verify.get("device") == declared_verification_device, "numerical verification schema/provenance differs")
    tolerance = config["verification"]["tolerance"]
    for key in ("step_sequence_max_error", "step_sequence_state_max_error", "reset_max_error", "actor_isolation_max_error"):
        require(0 <= number(verify.get(key), key) <= tolerance, f"numerical invariant failed: {key}")
    require(verify.get("invalid_probability_max") == 0, "invalid primitive has nonzero probability")
    require(verify.get("threads") == config["training"]["threads"] and verify.get("step_latency_ms", {}).get("warmup") == config["verification"]["warmup"] and
            verify["step_latency_ms"].get("samples") == config["verification"]["iterations"], "benchmark execution budget differs")
    require(0 < number(verify["step_latency_ms"].get("p95"), "model warm p95") <= config["verification"]["model_step_p95_budget_ms"], "model step latency budget failed")
    overfit = read_json(path/"overfit/overfit.json")
    require(overfit.get("kind") == "body-bc-overfit" and overfit.get("source_split") == "train" and overfit.get("architecture") == architecture and
            overfit.get("deployment_candidate") is False, "overfit diagnostic entered held-out/deployment path")
    require(overfit.get("target_accuracy") == config["overfit"]["target_accuracy"] and
            number(overfit.get("after", {}).get("accuracy"), "overfit accuracy") >= config["overfit"]["target_accuracy"], "train-only tiny overfit did not converge")
    require(0 < overfit.get("steps", 0) <= config["overfit"]["steps"] and 0 < len(overfit.get("selected_sequences", [])) <= config["overfit"]["max_chunks"], "overfit budget or source sequence list differs")
    train_ids = set(expected_provenance["train_episode_ids"])
    require(all(row.get("episode_id") in train_ids for row in overfit["selected_sequences"]), "overfit source leaks held-out data")
    included = config["dataset"]["included_actions"]
    support = overfit_source_support(overfit["selected_sequences"],dataset["train_chunks"],included)
    require(overfit.get("included_actions") == included and overfit.get("selected_support_ready") is True and
            overfit.get("selected_support_by_action") == support, "overfit support claim differs from actual selected train chunks")
    actions = overfit["after"].get("per_action", [])
    require(isinstance(actions,list) and len(actions) == 12 and {row.get("action_id") for row in actions} == set(range(12)),
            "overfit per-action support inventory differs")
    require(all(next(row["support"] for row in actions if row["action_id"] == action) == support[ACTIONS[action]]
                for action in included), "overfit measured support differs from actual selected train loss rows")
    selection = read_json(path/"selection.json")
    candidates = selection.get("candidates")
    require(isinstance(candidates, list) and len(candidates) == len(config["training"]["seeds"]) and
            {row.get("seed") for row in candidates} == set(config["training"]["seeds"]), "training seed candidates differ")
    inspected = []
    for candidate in candidates:
        job = artifact(root, candidate["path"]+"/training.json").parent
        trained, sidecar, history = read_json(job/"training.json"), read_json(job/"best.pt.json"), read_value(job/"history.json")
        require(trained.get("kind") == "body-bc-training" and trained.get("status") == "complete" and
                trained.get("dataset") == expected_provenance and trained.get("test_and_ood_used_for_selection") is False, "training provenance or selection scope differs")
        require(trained["config"]["architecture"] == architecture and trained["config"]["seed"] == candidate["seed"], "training candidate identity differs")
        for key in ("epochs", "batch_size", "chunk_length", "burn_in", "learning_rate", "weight_decay", "gradient_clip", "patience", "threads", "device", "max_wall_seconds"):
            require(trained["config"].get(key) == config["training"][key], f"training budget differs: {key}")
        require(digest(job/"best.pt") == trained["checkpoint_sha256"] == sidecar["checkpoint_sha256"] == candidate["checkpoint_sha256"], "trained checkpoint bytes differ")
        require(digest(job/"training.json") == candidate["training_sha256"] and digest(job/"history.json") == candidate["history_sha256"], "seed-selection source hashes differ")
        selected_epoch = choose_epoch(history, trained["selection"]["split"])
        require(selected_epoch == trained["selection"]["selected_epoch"] == candidate["epoch"], "chosen epoch differs from eval-only history")
        selected_metrics = next(row["eval"] for row in history if row["epoch"] == selected_epoch)
        require(candidate["eval_macro_f1"] == selected_metrics["macro_f1"] and candidate["eval_nll"] == selected_metrics["nll"], "seed score differs from selected epoch")
        metadata = sidecar["metadata"]
        require(metadata.get("dataset") == expected_provenance and metadata.get("feature_names") == normalizer["feature_names"] and
                metadata.get("normalization_sha256") == normalization_sha and metadata.get("actions") == list(ACTIONS) and
                metadata.get("feature_version") == config["dataset"]["feature_version"] and metadata.get("sampling_version") == "periodic-v1", "checkpoint schema/normalizer/sampling differs")
        embedded_sha = hashlib.sha256(json.dumps(normalizer, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        require(metadata.get("embedded_normalizer_sha256") == embedded_sha, "checkpoint embedded normalizer digest differs")
        require(metadata.get("training", {}).get("kind") == "behavioral_cloning" and metadata["training"].get("selected_epoch") == selected_epoch and
                metadata["training"].get("selection_split") == "eval" and metadata["training"].get("selected_eval") == selected_metrics, "checkpoint metadata differs from selection evidence")
        model = metadata["model_config"]
        require(model.get("architecture") == architecture and model.get("feature_count") == len(normalizer["feature_names"]) and model.get("action_count") == 12 and
                model.get("hidden_size") == config["models"][architecture]["hidden_size"] and model.get("head_size") == config["models"][architecture]["head_size"], "model architecture differs from declaration")
        if architecture == "gru": require(model.get("encoder_size") == config["models"]["gru"]["encoder_size"], "GRU encoder differs")
        else: require(model.get("stack_size") == config["models"]["mlp"]["stack_size"], "MLP temporal stack differs")
        inspected.append(candidate)
    selected = max(inspected, key=lambda row: (row["eval_macro_f1"], -row["eval_nll"]))
    require(selection.get("selected") == selected, "training seed selected using a different metric")
    source = root/selected["path"]
    for name in ("best.pt", "best.pt.json", "training.json", "history.json"):
        require(digest(path/name) == digest(source/name), "selected deployment artifacts differ from eval-selected candidate")
    evaluations = {}
    for split in ("eval", "test", "ood"):
        directory = path/split
        metrics = read_json(directory/"metrics.json")
        require(metrics.get("kind") == "body-bc-evaluation" and metrics.get("split") == split and metrics.get("dataset") == expected_provenance and
                metrics.get("checkpoint_sha256") == selected["checkpoint_sha256"], "held-out evaluation uses other weights/data")
        predictions = canonical.read_jsonl(artifact(directory, metrics.get("predictions_file")))
        seen = set()
        for prediction in predictions:
            key = (prediction["episode_id"], tuple(prediction["identity"]), prediction["observation_seq"])
            source_label = dataset["labels"].get(key)
            require(key not in seen and source_label is not None and source_label["split"] == split, "prediction duplicates/leaks or invents a source label")
            seen.add(key)
            require(prediction.get("checkpoint_sha256") == selected["checkpoint_sha256"] and prediction.get("dataset_sha256") == dataset_sha and prediction.get("split") == split, "prediction artifact provenance differs")
            require(prediction["true_action"] == source_label["action_id"] and prediction["action_mask"] == source_label["action_mask"] and
                    prediction.get("raw_recorded_action_mask") == source_label["raw_mask"] and prediction["t_game_ms"] == source_label["t_game_ms"], "prediction label/mask/time differs from causal model input")
        require(seen == {key for key, value in dataset["labels"].items() if value["split"] == split}, "held-out predictions omit true labels")
        actual = classification(predictions, normalizer["class_weights"])
        require(metrics.get("confusion") == actual["confusion_matrix"] and metrics.get("label_count") == actual["count"], "held-out confusion/count differs")
        for action in metrics.get("per_action", []):
            expected = actual["per_action"][ACTIONS[action["action_id"]]]
            for key in ("support", "tp", "fp", "fn", "precision", "recall", "f1"):
                require(math.isclose(number(action.get(key), key), expected[key], rel_tol=1e-7, abs_tol=1e-7), "held-out per-action metric differs")
        require(len(metrics.get("per_action", [])) == 12 and metrics.get("macro_f1_classes") == list(ACTIONS[1:]), "held-out metric class inventory differs")
        for key, target in (("accuracy", "accuracy"), ("macro_f1", "macro_f1"), ("nll", "unweighted_nll"), ("weighted_nll", "weighted_nll")):
            require(math.isclose(number(metrics.get(key), key), actual[target], rel_tol=1e-5, abs_tol=1e-6), f"held-out {key} differs from predictions")
        evaluations[split] = {"count": actual["count"], "accuracy": actual["accuracy"], "macro_f1": actual["macro_f1"],
                              "nll": actual["unweighted_nll"], "confusion": actual["confusion_matrix"], "per_action": actual["per_action"],
                              "predictions_sha256": digest(directory/metrics["predictions_file"])}
    return {"architecture": architecture, "checkpoint_sha256": selected["checkpoint_sha256"], "selected_seed": selected["seed"],
            "selected_epoch": selected["epoch"], "verify": verify, "overfit_accuracy": overfit["after"]["accuracy"],
            "overfit_support_by_action": support, "training_candidates": inspected, "evaluations": evaluations}


def json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def native_wire_relation(native: Any, wire: Any, *, max_ulps: int = 2) -> dict[str, int]:
    """Validate the measured RapidJSON roundtrip, while preserving every field."""
    differences = 0; observed = 0
    def walk(first,second,path):
        nonlocal differences,observed
        require(type(first) == type(second), f"native/wire type differs: {path}")
        if isinstance(first,dict):
            require(first.keys() == second.keys(), f"native/wire fields differ: {path}")
            for key in first:walk(first[key],second[key],path+"."+key)
        elif isinstance(first,list):
            require(len(first) == len(second), f"native/wire array length differs: {path}")
            for index,(a,b) in enumerate(zip(first,second)):walk(a,b,f"{path}[{index}]")
        elif type(first) is float:
            require(math.isfinite(first) and math.isfinite(second), f"native/wire nonfinite float: {path}")
            if first != second:
                # Same-sign IEEE-754 bit ranks measure representable double steps.
                require(math.copysign(1.,first) == math.copysign(1.,second), f"native/wire float sign differs: {path}")
                a = struct.unpack(">Q",struct.pack(">d",first))[0]
                b = struct.unpack(">Q",struct.pack(">d",second))[0]
                delta = abs(a-b)
                require(delta <= max_ulps, f"native/wire float exceeds {max_ulps} ULP: {path}")
                differences += 1;observed = max(observed,delta)
        else:require(first == second, f"native/wire nonfloat value differs: {path}")
    walk(native,wire,"observation")
    return {"float_differences":differences,"observed_max_ulps":observed,"allowed_max_ulps":max_ulps}


def wire_observation_sources(observations: list[dict[str, Any]], pairs: list[Any]) -> tuple[dict[Any, Any], dict[str, int]]:
    native = {}
    for row in observations:
        key = canonical.identity(row)+(row["observation_seq"],)
        require(key not in native, "duplicate native observation identity/sequence")
        native[key] = row
    wire, summary = {}, {"wire_rows":0,"float_differences":0,"observed_max_ulps":0,"allowed_max_ulps":2}
    for request,response,_,_ in pairs:
        if request.get("cmd") != "body_observations":continue
        rows = response.get("observations",response.get("rows",[]))
        require(isinstance(rows,list), "actual observation IPC rows missing")
        for row in rows:
            key = canonical.identity(row)+(row["observation_seq"],)
            require(key in native and key not in wire, "wire observation missing native source or delivered twice")
            actual = native_wire_relation(native[key],row)
            summary["wire_rows"] += 1;summary["float_differences"] += actual["float_differences"]
            summary["observed_max_ulps"] = max(summary["observed_max_ulps"],actual["observed_max_ulps"])
            wire[key] = row
    return wire,summary


def runtime_episode(path: Path, record: dict[str, Any], config: dict[str, Any], normalizer: dict[str, Any],
                    observations: list[dict[str, Any]], pairs: list[Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    journal_path = path/"policy-inputs.jsonl"
    require(digest(journal_path) == record.get("model_journal_sha256"), "policy-input journal hash differs")
    entries = canonical.read_jsonl(journal_path)
    source,wire_proof = wire_observation_sources(observations,pairs)
    processed, arbitrations, previous, command_ids = {}, [], {}, set()
    periodic_eligible = sum(row.get("periodic") is True and row.get("can_decide") is True and row["entity_id"] == record["initial"]["primary_entity_id"] for row in observations)
    physical_eligible = sum(row.get("can_decide") is True and row["entity_id"] == record["initial"]["primary_entity_id"] for row in observations)
    terminal_barrier = None
    for entry in entries:
        require(terminal_barrier is None, "policy input/command after terminal memory barrier")
        kind = entry.get("kind")
        if kind == "terminal_barrier":
            terminal_barrier = entry
            request, response = entry.get("request", {}), entry.get("response", {})
            require(request.get("session_epoch") == record["final"]["session_epoch"] and request.get("world_epoch") == record["final"]["world_epoch"] and
                    request.get("final_game_ms") == record["final"]["game_ms"] and response.get("accepted") is True and
                    response.get("model_processed") is False and response.get("hidden_reset") is True and
                    response.get("runtime_after_clear", {}).get("actors") == 0 and response["runtime_after_clear"].get("episode_closed") is True and
                    response["runtime_after_clear"].get("expected_epochs") is None, "terminal barrier did not invalidate the matching native episode")
            before_path = artifact(path, request.get("final_before_clear_file"))
            require(digest(before_path) == request.get("final_before_clear_sha256"), "pre-clear native/model final proof changed")
            before = read_json(before_path)
            require(before.get("final") == record["final"] and before.get("runtime_status") == response.get("runtime_before_clear") and
                    before.get("runtime_status") == record.get("runtime_status") and before.get("model_stats") == record.get("model_stats"), "model memory cleared before authoritative final snapshot")
            continue
        if kind == "excluded_observation":
            require(entry.get("reason") == "outside_policy_generation", "unknown excluded model observation")
            continue
        require(kind in ("model_input", "arbitration"), "unknown policy journal record")
        request, response = entry.get("request", {}), entry.get("response", {})
        row = request.get("observation") if kind == "model_input" else entry.get("observation")
        require(isinstance(row, dict) and row.get("periodic") is True and row.get("phase") == "post_simulation_post_animation", "event-only/teacher observation updated model or commanded body")
        key = canonical.identity(row)+(row["observation_seq"],)
        require(source.get(key) == row, "policy observation differs from exact received numeric IPC source")
        require(response.get("checkpoint_sha256") == record["checkpoint_sha256"] and response.get("normalizer_sha256") == record["normalizer_sha256"] and
                response.get("sampling_version") == "periodic-v1", "runtime weights/normalizer/schema changed")
        if kind == "model_input":
            require(key not in processed and response.get("model_processed") is True and response.get("status") == "processed", "healthy runtime duplicated/dropped/fell back on an input")
            entity = row["entity_id"]; old = previous.get(entity)
            expected_seq = 1 if old is None else old["stream_seq"]+1
            dt = row["t_game_ms"]-(record["initial"]["game_ms"] if old is None else old["game_ms"])
            require(request.get("stream_seq") == response.get("stream_seq") == expected_seq and response.get("sequence_dt_game_ms") == dt, "runtime sampling sequence/dt differs from BC history")
            require(response.get("hidden_reset") == (old is None or old["identity"] != canonical.identity(row)), "runtime recurrent reset/generation boundary differs")
            features = canonical.pack_features(row, normalizer, sequence_dt_game_ms=dt)
            require(response.get("features_sha256") == json_digest(features) and response.get("observation_sha256") == json_digest(row), "runtime feature packing differs from train view")
            mask = effective_mask(row)
            require(response.get("effective_action_mask") == mask and response.get("raw_action_mask") == row.get("action_mask", row["availability_mask"]), "runtime categorical mask differs from causal data view")
            runtime_decision(row, request, response)
            processed[key] = response
            previous[entity] = {"stream_seq": expected_seq, "game_ms": row["t_game_ms"], "identity": canonical.identity(row)}
        else:
            require(key in processed and response.get("model_processed") is False and response.get("hidden_reset") is False and
                    response.get("periodic_observation_seq") == row["observation_seq"] and response.get("periodic_game_ms") == row["t_game_ms"], "arbitration did not reuse the exact latest periodic state")
            require(response.get("features_sha256") == processed[key]["features_sha256"], "arbitration changed model input features")
            require(response.get("status") == "decision" and response.get("action_id") == processed[key]["action_id"], "healthy arbitration changed decision/fell back")
            command, receipt = entry.get("command_request"), entry.get("command_response")
            require(isinstance(command, dict) and isinstance(receipt, dict) and receipt.get("accepted") is True, "arbitration has no actual accepted body command")
            require(command.get("observation_seq") == row["observation_seq"] and command.get("issued_game_ms") == row["t_game_ms"] and
                    command.get("action_id") == response["action_id"] and all(command.get(field) == row[field] for field in canonical.IDENTITY), "body command differs from periodic decision identity/time")
            require(any({k:v for k,v in raw.items() if k!='id'} == command and actual == receipt for raw,actual,_,_ in pairs), "policy command/receipt has no matching raw IPC")
            identity = canonical.identity(command)+(command["command_id"],)
            require(identity not in command_ids, "policy command repeated at arbitration")
            command_ids.add(identity); arbitrations.append(entry)
    require(terminal_barrier is not None, "terminal memory invalidation barrier missing")
    current = {}
    commands_by_identity = {canonical.identity(entry["command_request"])+(entry["command_request"]["command_id"],): entry for entry in arbitrations}
    for request, response, _, _ in pairs:
        if request.get("cmd") == "body_observations":
            for row in response.get("observations", response.get("rows", [])):
                current[row["entity_id"]] = row
        elif request.get("cmd") == "body_action" and request.get("entity_id") == record["initial"]["primary_entity_id"]:
            entry = commands_by_identity.get(canonical.identity(request)+(request["command_id"],))
            require(entry is not None and current.get(request["entity_id"]) == entry["observation"], "policy commanded an older or event-only observation")
    primary = record["initial"]["primary_entity_id"]
    acquired = {row["control_generation"] for row in events if row.get("entity_id") == primary and row.get("phase") == "ownership" and row.get("reason") == "acquired"}
    expected_inputs = {canonical.identity(row)+(row["observation_seq"],) for row in observations if
                       row["entity_id"] == primary and row.get("periodic") is True and row.get("control_generation") in acquired}
    require(set(processed) == expected_inputs, "recurrent policy skipped/coalesced a healthy periodic input")
    stats = record.get("model_stats", {})
    require(stats.get("model_processed",0) == len(processed) and stats.get("accepted_model_commands",0) == len(arbitrations), "runtime summary counts differ from raw journal")
    opportunities = stats.get("latest_eligible_periodic_opportunities",0)
    fraction = len(arbitrations)/opportunities if opportunities else 0.
    require(math.isclose(record.get("neural_control_fraction",-1), fraction), "neural control fraction denominator differs")
    return {"processed_periodic_inputs": len(processed), "accepted_model_commands": len(arbitrations),
            "eligible_periodic_samples": periodic_eligible, "eligible_all_physical_samples": physical_eligible,
            "eligible_periodic_latest_opportunities": opportunities, "neural_control_fraction": fraction,
            "terminal_memory_reset": True, "native_to_received_wire":wire_proof,"policy_journal_sha256": digest(journal_path)}


def demonstration_evidence(root: Path, dataset: dict[str, Any]) -> dict[str, Any]:
    path = root/"demonstrations"; suite = read_json(path/"suite.json")
    cfg = read_json(root/"code/evals/npc-body/body-demonstrations-v1.json")
    included = cfg.get("coverage",{}).get("actions")
    require(isinstance(included,list) and included and len(set(included)) == len(included) and
            all(type(action) is int and 1 <= action < 12 for action in included), "demonstration action subset malformed")
    require(suite.get("kind") == "body_demonstrations_stage7_suite" and suite.get("status") == "collected" and not suite.get("error"), "approved demonstrations collection incomplete")
    recovery = collection_recovery(root,read_json(root/"code/evals/npc-body/body-bc-v1.json"))
    registry = read_json(path/"teacher-registry.json")
    require(registry == dataset["index"].get("teacher_registry"), "model dataset uses another teacher registry")
    expected = set()
    for split in ("train","eval","test"):
        expected.update(f"demo-{split}-{seed}-{variant}" for seed in cfg[split+"_seeds"] for variant in cfg[split+"_variants"])
    expected.update(case["name"] for case in cfg["near_wall_cases"]+cfg["ood_cases"])
    records = suite.get("episodes")
    require(isinstance(records,list) and len(records)==len(expected) and {row["name"] for row in records}==expected, "declared demonstration episodes differ")
    counts, wall_splits = {}, set()
    for record in records:
        if recovery is None:
            episode_path = artifact(path, record["manifest_file"]).parent
        else:
            require(record["manifest_file"] == f"episodes/{record['name']}/trajectory-manifest.json", "recovered episode manifest alias differs")
            episode_path = Path(recovery["borrowed_episodes"][record["name"]]["source_dir"])
        episode = canonical.validate_episode(episode_path, teacher_registry=registry)
        view = canonical.periodic_model_view(episode,max_label_age_game_ms=67)
        split=dataset["index"]["groups"][canonical.group_key(episode.manifest)]
        require(split==record["split"] and episode.manifest["initial"]==record["initial"], "demonstration declared split/reset differs")
        require(episode.manifest.get("teacher_provenance",{}).get("teacher_id")==cfg["teacher_id"], "demonstration teacher provenance differs")
        actual=Counter(view.decisions[key]["action_id"] for key,row in view.transitions.items() if row["usable_for_bc"])
        require(all(actual[action]>=1 for action in included), "demonstration lacks at least one genuine completed periodic expert example per declared class")
        require({str(action):actual[action] for action in range(1,12)}=={str(key):value for key,value in record["approved_actions"].items()}, "approved demonstration counts differ")
        if recovery is not None:
            borrowed = recovery["borrowed_episodes"][record["name"]]
            require(borrowed.get("approved_actions") == borrowed.get("model_usable_actions") ==
                    {str(key):value for key,value in record["approved_actions"].items()}, "recovery count claims differ from immutable periodic physical proof")
        counts.setdefault(split,Counter()).update(actual)
        if record.get("geometry")=="near_wall":
            proof=record["initial"].get("setup",{}).get("geometry_proof",{})
            require(proof.get("capsule_move_blocked") is True and proof.get("capsule_block_is_land") is True and
                    proof.get("initial_capsules_free") is True and proof.get("safe_outward_move") is True and proof.get("grounded_placements") is True,
                    "near-wall demonstration lacks native collision/ground proof")
            wall_splits.add(split)
    thresholds={"train":cfg["coverage"]["minimum_new_train_usable_per_action"],"eval":cfg["coverage"]["minimum_eval_usable_per_action"],"test":cfg["coverage"]["minimum_test_usable_per_action"]}
    for split,threshold in thresholds.items():require(all(counts.get(split,{}).get(action,0)>=threshold for action in included), "genuine new demonstration coverage is insufficient")
    require(set(cfg["coverage"]["wall_required_splits"])<=wall_splits, "demonstrations lack declared wall splits")
    return {"episodes":len(records),"required_new_actions":[ACTIONS[action] for action in included],
            "effective_examples":{split:{ACTIONS[action]:value for action,value in values.items()} for split,values in counts.items()},
            "wall_splits":sorted(wall_splits),"teacher_registry_sha256":canonical.registry_sha256(registry),
            "recovery":recovery}


def service_probe(path: Path, checkpoint_sha: str) -> dict[str, Any]:
    proof = read_json(path/"probe.json")
    for filename, field in (("request.jsonl","request_sha256"),("response.jsonl","response_sha256"),("service-journal.jsonl","journal_sha256")):
        require(digest(path/filename) == proof.get(field), "real service probe journal SHA differs")
    requests, responses = canonical.read_jsonl(path/"request.jsonl"), canonical.read_jsonl(path/"response.jsonl")
    journal = canonical.read_jsonl(path/"service-journal.jsonl")
    require(proof.get("checkpoint_sha256") == checkpoint_sha and proof.get("exit_code") == 0 and len(requests)==len(responses)==len(journal), "service probe failed/incomplete/other weights")
    for request,response,pair in zip(requests,responses,journal):
        require(request.get("id") == response.get("id") and pair == {"request":request,"response":response}, "service raw request/response join differs")
    observations = [(request,response) for request,response in zip(requests,responses) if request.get("cmd") == "observe"]
    require(any(response.get("model_processed") is True for _,response in observations), "real service never processed an observation")
    require(any(response.get("status") == "cached" and response.get("model_processed") is False for _,response in observations), "retry updated hidden instead of caching")
    for reason in ("unexpected_gap","stale_sequence","stale_epoch","checkpoint_changed_mid_episode"):
        require(any(response.get("reason") == reason and response.get("model_processed") is False and response.get("action_id") is None for _,response in observations), f"real service fault not exercised: {reason}")
    ends=[(request,response) for request,response in zip(requests,responses) if request.get("cmd")=="end_episode"]
    require(any(response.get("accepted") is True and response.get("hidden_reset") is True and response.get("runtime_after_clear",{}).get("actors")==0 for _,response in ends), "real service terminal reset not exercised")
    require(any(response.get("accepted") is False and response.get("reason")=="stale_episode_end" and response.get("hidden_reset") is False for _,response in ends), "stale terminal handshake changed the new episode")
    require(any(response.get("reason")=="episode_ended" and response.get("model_processed") is False for _,response in observations), "service accepted an observation after terminal close")
    return {"real_process_exit":0,"requests":len(requests),"duplicate_gap_epoch_checkpoint_terminal_checked":True,"journal_sha256":proof["journal_sha256"]}


def all_services(before: dict[str,Any],after: dict[str,Any],names:list[str]) -> dict[str,Any]:
    first,second={},{}
    for snapshot,result in ((before,first),(after,second)):
        require(isinstance(snapshot.get("services"),dict),"actual service identities missing")
        for name in names:
            text=snapshot["services"].get(name)
            require(isinstance(text,str),f"required service snapshot missing: {name}")
            values={}
            for line in text.splitlines():
                require("=" in line,"malformed service field")
                key,value=line.split("=",1);require(key not in values,"duplicate service field");values[key]=value
            state={key:values.get(key) for key in ("ActiveState","SubState","MainPID")}
            require(isinstance(state["MainPID"],str) and state["MainPID"].isascii() and state["MainPID"].isdigit(),"malformed service PID")
            active=state["ActiveState"]=="active" and state["SubState"]=="running" and int(state["MainPID"])>0
            inactive=state["ActiveState"]=="inactive" and state["SubState"]=="dead" and state["MainPID"]=="0"
            require(active or inactive,"invalid stable service state")
            result[name]=state
    require(first==second,"protected production service identity changed")
    return {"unchanged":True,"identity":first,"services_all_active":all(row["ActiveState"]=="active" for row in first.values())}


def resource_evidence(root:Path,config:dict[str,Any]) -> dict[str,Any]:
    phases={}
    names=config["resources"]["required_services"]
    recovery = collection_recovery(root,config)
    for phase in ("demos","dataset","verify","overfit","train","evaluate","arena"):
        measured_phase = "recover" if phase == "demos" and recovery is not None else phase
        before,after=read_json(root/f"services-{measured_phase}-before.json"),read_json(root/f"services-{measured_phase}-after.json")
        services=all_services(before,after,names)
        require(before.get("recorded_at_utc") and after.get("recorded_at_utc") and before["recorded_at_utc"]<=after["recorded_at_utc"],"resource phase timestamps reversed/missing")
        require(before.get("gpu") and after.get("gpu"),"actual GPU availability snapshots missing")
        text=(root/f"resources-{measured_phase}.txt").read_text()
        require(re.search(r"Exit status:\s+0\s*$",text,re.M),f"resource phase did not exit successfully: {measured_phase}")
        match=re.search(r"Maximum resident set size \(kbytes\):\s+(\d+)",text)
        require(match is not None and 0<int(match[1])<=config["resources"]["memory_max_gib"]*1024*1024,"process peak RSS exceeded resource budget or was not measured")
        phases[phase]={"measured_phase":measured_phase,"production_services":services,"peak_rss_kib":int(match[1]),"time_file_sha256":digest(root/f"resources-{measured_phase}.txt"),
                       "before_sha256":digest(root/f"services-{measured_phase}-before.json"),"after_sha256":digest(root/f"services-{measured_phase}-after.json")}
        if phase == "demos" and recovery is not None:
            phases[phase]["original_capture"] = {key:value for key,value in recovery.items() if key not in ("borrowed_episodes","provenance")}
    return {"host":"dev-ubuntu","selected_device":config["training"]["device"],"gpu_performance_claim":False,"phases":phases}


def native_fallback_boundary(initial: dict[str, Any], final: dict[str, Any],
                             control: list[dict[str, Any]], mechanics: list[dict[str, Any]]) -> dict[str, Any]:
    """A pending release or final cleanup does not prove native combat ran."""
    restored = [row for row in control if row.get("entity_id") == initial["primary_entity_id"] and
                row.get("phase") == "ownership" and row.get("reason") == "native_restored"]
    for row in restored:
        require(all(row.get(key) == final[key] for key in ("session_epoch", "world_epoch")),
                "native fallback ownership has another episode identity")
        require(type(row.get("t_game_ms")) is int and row["t_game_ms"] >= 0,
                "native fallback ownership time missing or malformed")
    before = [row for row in restored if row["t_game_ms"] < final["game_ms"]]
    at_final = [row for row in restored if row["t_game_ms"] == final["game_ms"]]
    continued = []
    if at_final:
        ids = {row.get("actor_id") for row in mechanics if
               row.get("phase") in ("arena_actor_mapping", "observation_actor_mapping") and
               row.get("observation_entity_id") == initial["primary_entity_id"]}
        require(len(ids) == 1 and None not in ids, "final native restoration lacks a unique physical actor mapping")
        # Controller::tick can restore ownership and enter the ordinary native
        # executor at the same game time. A generic native_attempt is insufficient:
        # selecting Idle for cleanup also emits that hook with historical choice IDs.
        continued = [row for row in mechanics if row.get("actor_id") in ids and
                     row.get("t_game") == final["game_ms"] and
                     row.get("phase") in ("native_choice", "native_consume")]
    return {"restorations_before_final": before, "same_final_native_continuations": continued}


def arena_evidence(root: Path, config: dict[str, Any], dataset: dict[str, Any], models: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    path = root/"arena"; suite, manifest = read_json(path/"suite.json"), read_json(path/"manifest.json")
    require(suite.get("kind") == "body_policy_stage7_suite" and suite.get("status") == "collected" and not suite.get("error"), "whole-episode arena collection incomplete")
    records = suite.get("cases"); declared = {case["name"]+"-"+policy:(case,policy) for case in config["arena"]["cases"] for policy in config["arena"]["policies"]}
    require(isinstance(records,list) and len(records)==len(declared) and {row.get("name") for row in records}==set(declared), "paired native/GRU/MLP episode set differs")
    results, resets, outcome_counts, hashes = [], {}, {}, {}
    for record in records:
        case, policy = declared[record["name"]]
        require(record.get("case") == case and record.get("policy") == policy, "arena case declaration differs")
        directory = path/record["name"]
        rows = {name:canonical.read_jsonl(directory/name) for name in ("arena-events.jsonl","events.jsonl","body-control-events.jsonl","observations.jsonl","ipc.jsonl")}
        starts = [row["arena"] for row in rows["arena-events.jsonl"] if row.get("kind")=="reset_ready"]
        ends = [row["arena"] for row in rows["arena-events.jsonl"] if row.get("kind")=="terminal"]
        require(len(starts)==len(ends)==1 and record.get("initial")==starts[0] and record.get("final")==ends[0], "arena copied snapshots differ from raw native terminal/reset")
        initial, final = starts[0], ends[0]
        require(not clean_reset(initial) and final.get("infrastructure") is False and final.get("terminated") != final.get("truncated"), "arena reset/terminal invalid")
        require(initial.get("scenario")==case["task"] and all(initial.get(field)==case[field] for field in ("seed","side","distance_cm","timeout_ms")), "arena physical setup differs from config")
        semantic = {"actors":semantic_initial(initial),"setup":initial.get("setup")}
        if case["name"] in resets:require(resets[case["name"]]==semantic,"paired policy initial states differ")
        else:resets[case["name"]]=semantic
        pairs = timed_pairs(rows["ipc.jsonl"])
        require(not any(response.get("ok") is False for _,response,_,_ in pairs), "native arena RPC failed")
        counter = linked_mechanics(initial,final,rows["body-control-events.jsonl"],rows["events.jsonl"])
        require(not counter["issues"],"arena HP/defence counters differ from native mechanics")
        commands = command_evidence(rows["body-control-events.jsonl"],rows["observations.jsonl"],pairs,final)
        require(not commands["issues"],"arena command lifecycle/masks differ")
        if final["outcome"]=="control_lost":
            require(not control_loss_issues({"scope":"paired","provider":"scripted"},initial,final,rows["body-control-events.jsonl"],rows["observations.jsonl"],rows["events.jsonl"]),"control-loss truncation is not proven by raw native ownership")
        if final["outcome"]=="task_success":
            if case["task"]=="distance": require(distance(final)<=220.001 and final.get("distance_held_ms",0)>=200,"distance success lacks held actual position")
            elif case["task"]=="attack":require(counter["linked_attack_hits"]>0 and counter["linked_opponent_hp_loss"]>0,"attack success has no mapped melee hit/HP loss")
            elif case["task"]=="block":require(counter["linked_blocked_hits"]>0 and actors(final)["primary"]["hp"]==actors(initial)["primary"]["hp"],"block success has no actual blocked hit without HP loss")
            else:raise ValueError("duel cannot use micro task-success outcome")
        elif final["outcome"]=="death_or_down":require(any(row["hp"]<=0 or row["down"] for row in final["actors"]),"death terminal has no down actor")
        elif final["outcome"]=="time_limit":require(final["elapsed_game_ms"]>=case["timeout_ms"],"time-limit occurred before declared game deadline")
        elif final["outcome"]!="control_lost":raise ValueError("arena outcome unrecognized")
        credited = True; runtime = None; fallback_boundary = None
        if policy!="native":
            require(record.get("checkpoint_sha256")==models[policy]["checkpoint_sha256"] and record.get("normalizer_sha256")==digest(root/"dataset/normalization.json"),"arena policy used other weights/normalizer")
            runtime = runtime_episode(directory,record,config,dataset["normalizer"],rows["observations.jsonl"],pairs,rows["body-control-events.jsonl"])
            fallback_boundary = native_fallback_boundary(initial,final,rows["body-control-events.jsonl"],rows["events.jsonl"])
            credited = not any(fallback_boundary.values()) and not record.get("fallback_events") and final["outcome"]!="control_lost"
            require(record.get("no_fallback_credit")==credited,"candidate native fallback credit differs from raw ownership")
        success = (final["outcome"]=="death_or_down" and final.get("reason")=="opponent_down") if case["task"]=="native_duel" else final["outcome"]=="task_success"
        success = bool(success and credited)
        bucket = outcome_counts.setdefault((policy,case["task"]),Counter());bucket["episodes"]+=1;bucket["successes"]+=success
        for name in rows:hashes[str((directory/name).relative_to(root))]=digest(directory/name)
        results.append({"name":record["name"],"policy":policy,"task":case["task"],"outcome":final["outcome"],"credited_success":success,
                        "fallback_credit_allowed":credited,"native_fallback_boundary":fallback_boundary,"runtime":runtime})
    comparison, quality_issues = {}, []
    for policy in ("gru","mlp"):
        comparison[policy]={}
        for task in sorted({case["task"] for case in config["arena"]["cases"]}):
            native, candidate = outcome_counts[("native",task)],outcome_counts[(policy,task)]
            baseline=native["successes"]/native["episodes"]; rate=candidate["successes"]/candidate["episodes"];delta=rate-baseline
            passed=delta>=config["arena"]["minimum_success_delta_to_native"]
            comparison[policy][task]={"native_success":baseline,"candidate_success":rate,"delta":delta,"passed":passed,"episodes":candidate["episodes"]}
            if not passed:quality_issues.append(f"{policy}/{task}: success delta {delta:.6f} below {config['arena']['minimum_success_delta_to_native']}")
    probes={policy:service_probe(path/("serve-probe-"+policy),models[policy]["checkpoint_sha256"]) for policy in ("gru","mlp")}
    services=production_services(manifest.get("services_before"),manifest.get("services_after"))
    resources=suite.get("resources")
    require(isinstance(resources,list) and resources and all(sample.get("pid")==suite.get("runner_pid") and sample.get("forbidden_device_descriptors")==sample.get("display_socket_descriptors")==[] for sample in resources),"actual headless runner resource/device evidence missing")
    return {"episodes":results,"comparisons":comparison,"service_probes":probes,"production_services":services,"raw_sha256":hashes,
            "resource_samples":len(resources),"runner_pid":suite["runner_pid"]},quality_issues


def report(root: Path, config_path: Path) -> dict[str, Any]:
    config = read_json(config_path)
    implementation_issues, checkpoint_issues = [], []
    evidence, dataset, models = {}, None, {}
    try:
        evidence["environment"] = frozen_environment(root)
        require(config_path.resolve().is_relative_to((root/"code").resolve()), "configuration must be frozen in the run")
        preflight = read_json(root/"preflight.json")
        require(preflight.get("host") == "dev-ubuntu", "model execution is not on dev-ubuntu")
        require(preflight.get("gpu") and preflight.get("ram") and preflight.get("disk"), "fresh GPU/RAM/disk preflight missing")
        evidence["preflight"] = preflight
    except (OSError, ValueError, KeyError, TypeError) as error:
        implementation_issues.append(str(error))
    try:evidence["resources"]=resource_evidence(root,config)
    except (OSError,ValueError,KeyError,TypeError) as error:implementation_issues.append("resources: "+str(error))
    try:
        evidence["dataset"], dataset = dataset_evidence(root, config)
        evidence["demonstrations"] = demonstration_evidence(root,dataset)
        if not evidence["dataset"]["effective_coverage_ready"]:
            checkpoint_issues.append("effective periodic expert coverage is insufficient")
    except (OSError, ValueError, KeyError, TypeError, IndexError, ImportError) as error:
        implementation_issues.append("data: "+str(error))
    if dataset is not None:
        for architecture in ("gru","mlp"):
            try:models[architecture]=model_evidence(root,architecture,config,dataset)
            except (OSError,ValueError,KeyError,TypeError,IndexError) as error:implementation_issues.append(architecture+": "+str(error))
        if len(models)==2:
            try:
                selected=read_json(root/"selection.json")
                require(selected.get("selection_split")=="eval" and selected.get("test_and_ood_used_for_selection") is False and
                        selected.get("config_sha256")==digest(config_path),"top-level checkpoint selection/config binding differs")
                for architecture in ("gru","mlp"):
                    require(selected["models"][architecture]==read_json(root/"models"/architecture/"selection.json"),"global/model checkpoint selection differs")
                evidence["arena"],quality=arena_evidence(root,config,dataset,models)
                checkpoint_issues.extend(quality)
            except (OSError,ValueError,KeyError,TypeError,IndexError) as error:implementation_issues.append("arena: "+str(error))
    evidence["models"]=models
    if "arena" not in evidence:checkpoint_issues.append("frozen whole-episode policy quality is not established")
    candidate=config["arena"]["candidate_policy"]
    coverage_ok=bool(evidence.get("dataset",{}).get("effective_coverage_ready"))
    per_model={architecture:not implementation_issues and coverage_ok and "arena" in evidence and
               not any(issue.startswith(architecture+"/") for issue in checkpoint_issues) for architecture in ("gru","mlp")}
    return {"schema_version": 1, "kind": "body_bc_stage7_report", "config_sha256": digest(config_path),
            "status":"implementation_complete" if not implementation_issues else "incomplete",
            "implementation_ready": not implementation_issues, "checkpoint_ready":per_model.get(candidate,False),
            "candidate_policy":candidate,"checkpoint_ready_by_architecture":per_model,
            "implementation_issues": implementation_issues, "checkpoint_issues": checkpoint_issues,
            "evidence": evidence, "limitations": ["This is diagnostic BC admission, not the release or PPO gate."]}


def markdown(value: dict[str, Any]) -> str:
    return "\n".join(["# Body behavioral cloning", "", f"Implementation ready: **{value['implementation_ready']}**. Checkpoint ready: **{value['checkpoint_ready']}**.", "",
                      *[f"- {issue}" for issue in value["implementation_issues"]], *[f"- {issue}" for issue in value["checkpoint_issues"]], ""])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path); parser.add_argument("--config", type=Path, required=True); parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if Path(__file__).resolve() == (args.run/"validation/audit-code/scripts/body_bc_report.py").resolve():
        require(args.output_dir.resolve().is_relative_to((args.run/"validation").resolve()) and
                args.output_dir.resolve() != (args.run/"validation").resolve(), "independent audit must preserve the original frozen report")
    value = report(args.run.resolve(), args.config.resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/"body-bc.json").write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")
    (args.output_dir/"body-bc.md").write_text(markdown(value)+"\n")
    return 0 if value["implementation_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
