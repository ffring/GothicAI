#!/usr/bin/env python3
"""Independent saved-runtime admission for the repaired stage 8 evaluation.

Data/model proofs are supplied by the separately bound child numerical auditor.
This reader executes no model and creates no simulator or service workload.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys


def require(value,message):
    if not value:raise ValueError(message)


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);require(spec and spec.loader,"missing frozen reader helper")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def audit_snapshot(root,snapshot,here):
    digest=lambda path:hashlib.sha256(Path(path).read_bytes()).hexdigest()
    binding=json.loads(snapshot.read_text());directory=snapshot.resolve().parent
    require(binding.get("schema_version")==1 and binding.get("kind")=="body_stage8_final_runtime_audit_snapshot" and
            binding.get("environment_sha256")==digest(root/"environment.json") and
            binding.get("declaration_sha256")==digest(root/"final-evaluation.json"),"final runtime audit snapshot differs")
    require(here.is_relative_to(directory) and binding.get("files",{}).get(str(here.relative_to(directory)))==digest(here),
            "actual final runtime reader is not separately frozen")
    for name,expected in binding["files"].items():
        file=(directory/name).resolve()
        require(file.is_relative_to(directory) and file.is_file() and digest(file)==expected,"external reader/helper changed")
    helper=directory/"body_block_reaction_report.py"
    require(binding["files"].get("body_block_reaction_report.py")==digest(helper),"prepared runtime helper not in audit snapshot")
    return helper


SHADOW_OLD=b'require(record.get("shadow") is True and record.get("no_fallback_credit") is False and'
SHADOW_NEW=b'require(record.get("shadow") is (record["policy"] != "native") and record.get("no_fallback_credit") is False and'


def combat_correction(root,snapshot,bc,environment):
    binding=bc.read_json(snapshot);correction=binding.get("combat_helper_correction")
    if correction is None:return None,None
    directory=snapshot.resolve().parent;original=root/"code/scripts/body_combat_report.py";replacement=directory/"body_combat_report.py"
    require(correction.get("kind")=="shadow_native_comparator_flag_only" and
            correction.get("original_helper_sha256")==environment["frozen_files"]["code/scripts/body_combat_report.py"]==bc.digest(original) and
            binding["files"].get("body_combat_report.py")==bc.digest(replacement),"shadow audit correction source binding differs")
    before=original.read_bytes()
    require(before.count(SHADOW_OLD)==1 and replacement.read_bytes()==before.replace(SHADOW_OLD,SHADOW_NEW),
            "external combat helper changed more than the exact shadow comparator flag")
    failed=correction.get("original_failed_known_report",{});path=Path(failed.get("path",".")).resolve()
    require(path.is_relative_to(root/"validation") and path.is_file() and failed.get("sha256")==bc.digest(path),
            "original failed known shadow audit is not preserved")
    previous=bc.read_json(path)
    require(previous.get("passed") is False and previous.get("issues")==["shadow acquired or commanded the native actor"],
            "shadow correction borrowed another failure")
    return replacement,{**correction,"actual_helper_sha256":bc.digest(replacement)}


def sources(root,snapshot):
    sys.path.insert(0,str(root/"code/scripts"))
    import body_final_evaluation as declaration_module
    import body_bc_report as bc
    import body_combat_report as combat
    here=Path(__file__).resolve();helper=audit_snapshot(root,snapshot,here)
    reaction=load("final_prepared_runtime_reader",helper)
    declared=declaration_module.validate_declaration(root)
    environment=bc.read_json(root/"environment.json")
    for module in (declaration_module,bc,combat,bc.canonical):
        file=Path(module.__file__).resolve()
        require(file.is_relative_to(root/"code") and environment["frozen_files"].get(str(file.relative_to(root)))==bc.digest(file),
                "final reader imported another source/data contract")
    replacement,correction=combat_correction(root,snapshot,bc,environment)
    if replacement is not None:combat=load("final_shadow_comparator_corrected_reader",replacement)
    return declared,bc,combat,reaction,{"audit_snapshot_sha256":bc.digest(snapshot),"reporter_sha256":bc.digest(here),
                                     "combat_helper_correction":correction}


def exact_policy_wire(path,wire,bc):
    for entry in bc.canonical.read_jsonl(path/"policy-inputs.jsonl"):
        if entry.get("kind") in ("model_input","arbitration"):
            row=entry["request"]["observation"] if entry["kind"]=="model_input" else entry["observation"]
            key=bc.canonical.identity(row)+(row["observation_seq"],)
            require(key in wire and bc.json_digest(wire[key])==bc.json_digest(row),"policy payload differs from exact received IPC (including types)")


def execution_profile(profile,declared):
    expected=declared.get("inference_cpu_threads")
    if expected is None:return
    require(isinstance(profile,dict) and type(expected) is int and expected in (1,2) and
            type(profile.get("inference_cpu_threads")) is int and type(profile.get("torch_intraop_threads")) is int and
            profile.get("inference_cpu_threads")==profile.get("torch_intraop_threads")==expected and
            type(profile.get("torch_interop_threads")) is int and profile["torch_interop_threads"]>0,
            "actual backend inference CPU threads differ from declared profile")


def backend_execution_evidence(folder,declared,bc):
    if "inference_cpu_threads" not in declared:return None
    timings=[]
    for path in folder.glob("*/policy-inputs.jsonl"):
        for entry in bc.canonical.read_jsonl(path):
            if entry.get("kind")!="model_input" or entry.get("response",{}).get("model_processed") is not True:continue
            response=entry["response"];execution_profile(response.get("backend_execution_profile"),declared)
            timing=response.get("backend_timing",{})
            require(set(timing)=={"wall_ms","main_thread_cpu_ms","process_cpu_ms"} and
                    all(type(value) in (int,float) and math.isfinite(value) and value>=0 for value in timing.values()),
                    "actual backend CPU/wall diagnostic timing missing or nonfinite")
            clocks=entry["timing"];outer=(clocks["process_end_wall_monotonic_ns"]-clocks["process_begin_wall_monotonic_ns"])/1e6
            require(timing["wall_ms"]<=response["inference_ms"]<=outer,"backend timing exceeds actual complete model call wall interval")
            timings.append({"episode":path.parent.name,"observation_seq":entry["request"]["observation"]["observation_seq"],
                            "process_wall_ms":outer,"backend_execution_profile":response["backend_execution_profile"],**timing})
    require(timings,"declared inference profile has no actual measured forwards")
    return {"inference_cpu_threads":declared["inference_cpu_threads"],"actual_forward_count":len(timings),"samples":timings,
            "wall_latency_population_unchanged":True,"cpu_stall_cause_claim":False}


DEADLINE_COUNTERS={"valid_hints","invalid_hints","heartbeat_requests","numeric_queries","event_queries",
                   "legacy_poll_batches","already_advanced_batches","deadline_wait_batches","sleep_calls"}


def native_worker_evidence(folder,manifest,declared,bc):
    if "native_serial_workers" not in declared:return None
    serial=declared["native_serial_workers"]
    require(type(serial) is bool and manifest.get("native_serial_workers") is serial,"actual native worker CLI differs from declaration")
    count=0
    for path in folder.glob("*/ipc.jsonl"):
        for entry in bc.canonical.read_jsonl(path):
            if entry.get("direction")!="response":continue
            profile=entry["value"].get("worker_execution",{})
            require(type(profile.get("serial")) is bool and profile["serial"] is serial and
                    profile.get("mode")==("caller_serial" if serial else "default_parallel") and
                    type(profile.get("worker_pool_created")) is bool and
                    (profile.get("worker_pool_created") is False and type(profile.get("native_task_parallelism_cap")) is int and
                     profile["native_task_parallelism_cap"]==1 if serial else profile.get("native_task_parallelism_cap") is None),
                    "actual native response worker dispatch profile differs")
            count+=1
    require(count,"native worker profile has no actual engine responses")
    return {"native_serial_workers":serial,"actual_response_count":count,"total_process_thread_cap_claim":False,
            "rpc_delay_cause_claim":False,"latency_samples_excluded":0}


def deadline_wait_evidence(proof,pairs,transport,enabled,poll_ms):
    require(isinstance(proof,dict) and proof.get("enabled") is enabled and
            proof.get("clock_domain")=="linux-clock-monotonic","native deadline polling profile differs")
    counters=proof.get("counters");records=proof.get("wait_records")
    require(isinstance(counters,dict) and set(counters)<=DEADLINE_COUNTERS and
            all(type(value) is int and value>=0 for value in counters.values()) and
            isinstance(records,list) and len(records)<=65536,"native deadline wait counters/capacity invalid")
    require(type(poll_ms) in (int,float) and math.isfinite(poll_ms) and 0<poll_ms<=5,"deadline polling exceeds original 5ms poll bound")
    requests={};responses={};receipts={};valid=0;invalid=0
    for request,response,_,_ in pairs:
        require(response["id"] not in responses,"duplicate native deadline response ID")
        requests[response["id"]]=request;responses[response["id"]]=response
        arena=response.get("arena",{});deadline=response.get("next_tick_wall_monotonic_ns");stamp=response.get("wall_monotonic_ns")
        genuine=(type(deadline) is int and deadline>0 and type(stamp) is int and stamp>0 and
                 response.get("clock_domain")=="linux-clock-monotonic" and response.get("mode")=="realtime" and
                 arena.get("started") is True and not (arena.get("terminated") or arena.get("truncated") or response.get("paused")))
        valid+=int(genuine);invalid+=int(deadline is not None and not genuine)
    for entry in transport:
        if entry.get("direction")!="response":continue
        require(entry.get("kind")=="ipc_clock" and entry.get("id") not in receipts and
                type(entry.get("wall_monotonic_ns")) is int,"deadline collector receipt clock invalid")
        receipts[entry["id"]]=entry
    for counter,command in (("heartbeat_requests","body_heartbeat"),("numeric_queries","body_observations"),("event_queries","body_events")):
        require(counters.get(counter,0)<=sum(request.get("cmd")==command for request in requests.values()),
                "deadline query count exceeds actual IPC requests")
    require(counters.get("valid_hints",0)<=valid and counters.get("invalid_hints",0)<=invalid,
            "deadline hint counts exceed actual native headers")
    require(counters.get("deadline_wait_batches",0)==len(records) and
            counters.get("sleep_calls",0)==sum(row.get("sleep_calls",0) for row in records),"deadline wait/sleep counters differ")
    previous=0;actual_sleep=0;requested_sleep=0
    for row in records:
        require(enabled and row.get("kind")=="native_deadline_wait" and row.get("poll_ms")==poll_ms and
                all(type(row.get(key)) is int and row[key]>=0 for key in
                    ("begin_wall_monotonic_ns","end_wall_monotonic_ns","next_tick_wall_monotonic_ns",
                     "native_response_wall_monotonic_ns","response_id","sim_step","game_ms","sleep_calls","requested_sleep_ns","actual_sleep_ns")),
                "native deadline wait clock/types/profile invalid")
        begin,end,deadline=row["begin_wall_monotonic_ns"],row["end_wall_monotonic_ns"],row["next_tick_wall_monotonic_ns"]
        response=responses.get(row["response_id"],{});receipt=receipts.get(row["response_id"],{})
        arena=response.get("arena",{})
        require(previous<=begin<=end and begin<deadline and
                row["native_response_wall_monotonic_ns"]==response.get("wall_monotonic_ns")==receipt.get("native_wall_monotonic_ns") and
                response.get("clock_domain")=="linux-clock-monotonic" and response.get("mode")=="realtime" and
                arena.get("started") is True and not (arena.get("terminated") or arena.get("truncated") or response.get("paused")) and
                response.get("next_tick_wall_monotonic_ns")==deadline and response.get("sim_step")==row["sim_step"] and
                arena.get("game_ms")==row["game_ms"] and
                row["native_response_wall_monotonic_ns"]<=receipt.get("wall_monotonic_ns",-1)<=begin,
                "deadline wait differs from actual active native hint/collector receipt")
        require(row["requested_sleep_ns"]<=row["sleep_calls"]*int(min(poll_ms,5)*1e6) and
                row["requested_sleep_ns"]<=deadline-begin and row["actual_sleep_ns"]<=end-begin,
                "deadline requested sleep exceeded bounded poll/deadline or actual span")
        previous=end;actual_sleep+=row["actual_sleep_ns"];requested_sleep+=row["requested_sleep_ns"]
    if not enabled:
        require(not records and not any(counters.get(key,0) for key in ("valid_hints","invalid_hints","legacy_poll_batches","already_advanced_batches")),
                "native deadline waits/hints occurred without declaration")
    return {"enabled":enabled,"counters":counters,"wait_records":len(records),"requested_sleep_ns":requested_sleep,
            "actual_sleep_ns":actual_sleep,"actual_wakeup_within_5ms_claim":False,"latency_samples_excluded":0}


def deadline_polling_evidence(folder,declared,bc):
    if "native_deadline_polling" not in declared:return None
    config=bc.read_json(folder.parent/"code/evals/npc-body/body-stage8-v1.json")
    summaries={}
    for file in folder.glob("*/episode.json"):
        record=bc.read_json(file);directory=file.parent
        pairs=bc.timed_pairs(bc.canonical.read_jsonl(directory/"ipc.jsonl"))
        transport=bc.canonical.read_jsonl(directory/"collector-transport.jsonl")
        summaries[directory.name]=deadline_wait_evidence(record.get("native_deadline_polling"),pairs,transport,
                                                        declared["native_deadline_polling"],config["runtime"]["poll_ms"])
    require(summaries,"native deadline polling has no closed actual episodes")
    return {"episodes":summaries,"real_latency_samples_removed":0,"periodic_inputs_excluded":0}


def bootstrap_gc_evidence(proof,declared,bc,last_warm):
    reset=declared.get("collector_bootstrap_gc_reset",False)
    require(proof.get("normalize_bootstrap",False) is reset,"collector bootstrap normalization opt-in differs")
    if not reset:
        require("bootstrap_normalization" not in proof,"undeclared bootstrap GC normalization occurred")
        return
    bootstrap=proof.get("bootstrap_normalization",{});identity=declared.get("collector_bootstrap_interpreter",{})
    require(identity and all(bootstrap.get(key)==value for key,value in identity.items()) and
            bootstrap.get("kind")=="body_collector_bootstrap_gc_normalization" and
            bootstrap.get("interpreter_metadata_source")=="runtime_sys_and_executable_bytes" and
            isinstance(bootstrap.get("sys_version"),str) and bootstrap["sys_version"].startswith("3.12.13 ") and
            bootstrap.get("freeze_count_before")==375 and bootstrap.get("freeze_count_after")==0 and
            bootstrap.get("gc_enabled_before") is True and bootstrap.get("gc_enabled_after") is True and
            bootstrap.get("thresholds_before")==bootstrap.get("thresholds_after")==proof["startup"]["thresholds_before"],
            "bootstrap GC identity/count/dynamic policy differs from explicit private interpreter binding")
    begin,end=bootstrap.get("begin_wall_monotonic_ns"),bootstrap.get("end_wall_monotonic_ns")
    require(type(begin) is int and type(end) is int and last_warm<=begin<=end<=proof["startup"]["begin_wall_monotonic_ns"] and
            Path(identity["executable"]).resolve()==Path(identity["resolved_executable"]) and
            bc.digest(Path(identity["resolved_executable"]))==identity["executable_sha256"],"bootstrap normalization executable/clock binding differs")


def collector_gc_evidence(folder,manifest,declared,bc,last_warm,first_reset):
    if declared.get("collector_static_gc_freeze",False) is not True:
        require("collector_gc" not in manifest and not (folder/"collector-gc.json").exists(),"collector GC changed without declaration")
        return None
    proof=bc.read_json(folder/"collector-gc.json");root=folder.parent
    helper=root/"code/scripts/body_collector_gc.py"
    require(manifest.get("collector_gc")==proof and proof.get("schema_version")==1 and
            proof.get("kind")=="body_collector_static_gc_profile" and proof.get("mode")=="freeze_static_keep_dynamic" and
            proof.get("enabled") is True and proof.get("max_events")==65536 and proof.get("dropped_total")==0 and
            Path(proof.get("helper_path",".")).resolve()==helper.resolve() and proof.get("helper_sha256")==bc.digest(helper)==
            bc.read_json(root/"environment.json")["frozen_files"].get("code/scripts/body_collector_gc.py"),"collector static/dynamic GC source proof differs")
    startup,restore=proof.get("startup",{}),proof.get("restore",{})
    for section in (startup,restore):
        begin,end=section.get("begin_wall_monotonic_ns"),section.get("end_wall_monotonic_ns")
        require(type(begin) is int and type(end) is int and 0<begin<=end and section.get("gc_enabled_before") is True and
                section.get("gc_enabled_after") is True and section.get("thresholds_before")==section.get("thresholds_after") and
                isinstance(section.get("thresholds_before"),list) and len(section["thresholds_before"])==3 and
                all(type(value) is int and value>=0 for value in section["thresholds_before"]),"collector changed dynamic GC policy or source clocks")
    bootstrap_gc_evidence(proof,declared,bc,last_warm)
    require(startup.get("freeze_count_before")==0 and type(startup.get("freeze_count_after")) is int and startup["freeze_count_after"]>0 and
            type(startup.get("collected_before_freeze")) is int and startup["collected_before_freeze"]>=0 and
            last_warm<=startup["begin_wall_monotonic_ns"]<=startup["end_wall_monotonic_ns"]<first_reset,
            "static GC freeze did not follow warmup before the first scene")
    last_reply=max(end*1e9 for path in folder.glob("*/ipc.jsonl") for _,_,_,end in bc.timed_pairs(bc.canonical.read_jsonl(path)))
    require(startup["thresholds_after"]==restore["thresholds_before"] and type(restore.get("freeze_count_before")) is int and
            restore["freeze_count_before"]>0 and restore.get("freeze_count_after")==0 and restore.get("owned_freeze_released") is True and
            last_reply<=restore["begin_wall_monotonic_ns"],"collector GC ownership/policy was not restored after native activity")
    events=proof.get("dynamic_gc_events");require(isinstance(events,list) and len(events)<=65536,"dynamic GC trace exceeded declared capacity")
    previous=startup["end_wall_monotonic_ns"]
    for event in events:
        begin,end=event.get("begin_wall_monotonic_ns"),event.get("end_wall_monotonic_ns")
        require(type(begin) is int and type(end) is int and previous<=begin<=end<=restore["begin_wall_monotonic_ns"] and
                type(event.get("generation")) is int and event["generation"] in (0,1,2) and
                all(type(event.get(key)) is int and event[key]>=0 for key in ("collected","uncollectable")),"dynamic GC event clocks/counters differ")
        previous=end
    return {"profile":proof,"file_sha256":bc.digest(folder/"collector-gc.json"),"real_latency_samples_excluded":0}


def startup_evidence(folder,manifest,declared,policy_root,bc):
    warmups=bc.read_json(folder/"startup-warmup.json")
    require(manifest.get("startup_warmup")==warmups and manifest.get("startup_warmup_before_any_arena") is True and
            set(warmups)=={"gru","mlp"},"backend startup work is missing or not separately bound before scenes")
    last=0
    for name,proof in warmups.items():
        before,after=proof.get("runtime_before",{}),proof.get("runtime_after",{})
        execution_profile(proof.get("backend_execution_profile"),declared)
        if "inference_cpu_threads" in declared:
            require(before.get("execution_profile")==after.get("execution_profile")==proof.get("backend_execution_profile"),
                    "startup runtime/backend execution profiles disagree")
        require(proof.get("kind")=="body_policy_startup_warmup" and proof.get("steps")==2 and proof.get("device")=="cpu" and
                proof.get("feature_count")==184 and proof.get("feature_values")=="synthetic_zero" and proof.get("head_mask")==[True]*12 and
                proof.get("state_in") is None and proof.get("returned_hidden_discarded_each_step") is True and
                proof.get("model_process_calls")==proof.get("commands")==0 and proof.get("quality_credit") is False and
                before==after and before.get("actors")==0 and before.get("expected_epochs") is None,
                "startup warmup created real memory/commands or removed a real measured input")
        require(proof.get("checkpoint_sha256")==declared["candidate"]["checkpoint_sha256"][name] and
                proof.get("normalizer_sha256")==declared["candidate"]["normalizer_sha256"] and
                proof.get("backend_source_sha256")==bc.digest(policy_root/"code/body-service/src/gothic_body/runtime.py"),
                "startup used another backend/checkpoint/normalizer")
        times=proof.get("timings",[])
        require(len(times)==2 and [row.get("step") for row in times]==[1,2],"startup step count fabricated")
        for row in times:
            start,end=row.get("begin_wall_monotonic_ns"),row.get("end_wall_monotonic_ns")
            require(type(start) is int and type(end) is int and last<=start<end and
                    abs(row["elapsed_ms"]-(end-start)/1e6)<=1e-9,"startup timing differs from actual source clock")
            last=end
    first_reset=min(begin*1e9 for path in folder.glob("*/ipc.jsonl") for request,_,begin,_ in bc.timed_pairs(bc.canonical.read_jsonl(path))
                    if request.get("cmd")=="reset")
    require(last<first_reset,"startup backend work occurred inside a physical scene")
    gc=collector_gc_evidence(folder,manifest,declared,bc,last,first_reset)
    return {"backend_steps_per_architecture":2,"before_first_reset":True,"real_observations_excluded":0,
            "file_sha256":bc.digest(folder/"startup-warmup.json"),"proofs":warmups,
            "backend_execution":backend_execution_evidence(folder,declared,bc),"collector_gc":gc,
            "native_deadline_polling":deadline_polling_evidence(folder,declared,bc),
            "native_workers":native_worker_evidence(folder,manifest,declared,bc)}


def repaired_phase(root,name,config,declared,bc,combat,reaction):
    folder=root/name;suite=bc.read_json(folder/"suite.json");manifest=bc.read_json(folder/"manifest.json")
    require(suite.get("schema_version")==2 and suite.get("kind")=="body_stage8_evaluation_protocol_repair" and
            suite.get("phase")==name and suite.get("status")=="collected" and not suite.get("error") and
            suite.get("original_v6_credit") is False and suite.get("quality_gate_evaluated") is False,
            "repaired evaluation collection missing/incomplete/promoted")
    require(dt.datetime.fromisoformat(declared["created_at_utc"])<=dt.datetime.fromisoformat(manifest["recorded_at_utc"]),
            "selected-model evaluation preceded immutable preregistration")
    require(manifest.get("config_sha256")==bc.digest(root/"code/evals/npc-body/body-stage8-evaluation-v2.json") and
            manifest.get("driver_sha256")==bc.digest(root/"code/scripts/body_combat_evaluation.py") and
            manifest.get("binary_sha256")==declared["updated_engine"]["binary_sha256"] and
            manifest.get("source_sha256")==declared["updated_engine"]["source_sha256"] and manifest.get("device")=="cpu" and
            manifest.get("checkpoint_sha256")==declared["candidate"]["checkpoint_sha256"],"runtime binary/source/config/selected weights differ")
    reference=Path(declared["reference_run"]);candidate=Path(declared["candidate"]["run"])
    policy_root=root if config["reference"]["policy_code_run_name"]==root.name else reference
    require(manifest.get("policy_code_root")==str(policy_root/"code") and manifest.get("reference_run")==str(candidate),
            "repaired runtime did not import its explicit actual policy code with selected child weights")
    require(set(manifest["policy_modules"])==set(config["bindings"]["frozen_policy_modules"]),"repaired runtime module inventory incomplete/extra")
    for module,entry in manifest["policy_modules"].items():
        file=Path(entry["path"]).resolve()
        require(file.is_relative_to(policy_root/"code") and bc.read_json(policy_root/"environment.json")["frozen_files"].get(str(file.relative_to(policy_root)))==entry["sha256"]==bc.digest(file)==
                config["bindings"]["frozen_policy_modules"][module],
                "repaired runtime imported changed policy/packer")
    require(manifest.get("policy_code_sha256")=={file.name:bc.digest(file) for file in sorted((policy_root/"code/body-service/src/gothic_body").glob("*.py"))},
            "repaired runtime model/backend package inventory differs from its actual declared imports")
    startup=None
    if config["runtime"].get("startup_warmup_steps"):
        require(manifest.get("compact_replies")==config["runtime"].get("compact_replies"),"runtime compact transport declaration differs")
        startup=startup_evidence(folder,manifest,declared,policy_root,bc)
    phase=config[name];expected={case["name"]+"-"+policy:(case,policy) for case in phase["cases"] for policy in phase["policies"]}
    require(len(suite["cases"])==len(expected) and {record["name"] for record in suite["cases"]}==set(expected),"whole paired screening/realtime matrix incomplete/duplicated")
    normalizer=bc.read_json(candidate/"dataset/normalization.json");cfg={**config,"_reference_run":str(candidate)}
    episodes=[];raw={};initials={};prepared={}
    for record in suite["cases"]:
        case,policy=expected[record["name"]];path=folder/record["name"]
        require(bc.read_json(path/"episode.json")==record and record["case"]==case and record["policy"]==policy and
                record.get("mode")==phase["mode"] and record.get("original_v6_credit") is False,"saved runtime case/profile differs")
        for initial in (record["initial"],):
            require(not bc.clean_reset(initial,bc.read_json(root/"code/evals/npc-body/body-arena-v1.json")["arena_profile"]),"runtime arena did not reset canonical HP/equipment/pose/queues")
        semantic=bc.semantic_initial(record["initial"])
        if case["name"] in initials:require(initials[case["name"]]==semantic,"native/GRU/MLP physical reset differs")
        else:initials[case["name"]]=semantic
        observations=bc.canonical.read_jsonl(path/"observations.jsonl");mechanics=bc.canonical.read_jsonl(path/"events.jsonl")
        controls=bc.canonical.read_jsonl(path/"body-control-events.jsonl");pairs=bc.timed_pairs(bc.canonical.read_jsonl(path/"ipc.jsonl"))
        arena=bc.canonical.read_jsonl(path/"arena-events.jsonl")
        require([row["arena"] for row in arena if row.get("kind")=="reset_ready"]==[record["initial"]] and
                [row["arena"] for row in arena if row.get("kind")=="terminal"]==[record["final"]],"copied runtime reset/final differs from actual engine")
        require(record["final"].get("infrastructure") is False and record["final"].get("terminated")!=record["final"].get("truncated"),"runtime terminal is infrastructure/malformed")
        for observation in observations:combat.no_oracle_features(observation)
        wire,roundtrip=bc.wire_observation_sources(observations,pairs);exact_policy_wire(path,wire,bc)
        commands=bc.command_evidence(controls,observations,pairs,record["final"])
        physics=bc.linked_mechanics(record["initial"],record["final"],controls,mechanics)
        require(not commands["issues"] and not physics["issues"],"runtime actions/masks/lifecycle/native damage/block counters differ")
        integrity=combat.capture_integrity(record,mechanics,pairs)
        visibility=combat.visibility_diagnostics(mechanics,record["initial"],controls,observations)
        if record["final"]["outcome"]=="control_lost":
            require(not bc.control_loss_issues({"scope":"paired","provider":"scripted"},record["initial"],record["final"],controls,observations,mechanics),
                    "control-loss truncation lacks actual native ownership/visibility boundary")
        runtime=None;preparation=None;timing=None;defense=None
        if policy!="native":
            require(record["checkpoint_sha256"]==declared["candidate"]["checkpoint_sha256"][policy] and
                    record["normalizer_sha256"]==declared["candidate"]["normalizer_sha256"],"episode weights/normalizer changed")
        if case["task"]=="block":
            require(record.get("initializer")=="reaction_prepared_v1" and record.get("frozen_rollout_case")=={**case,"task":config["reaction_rollout"]["task_alias"]},
                    "reaction case changed task instead of suppressing duplicate attacker injection")
            preparation,wire,features=reaction.prepared_evidence(path,record,cfg,pairs,observations,controls,mechanics,bc,mode=phase["mode"])
            prepared.setdefault(case["name"],[]).append(features)
            activation=record["activation"]
            require(activation.get("native_mode_rebase")==phase["mode"] and activation.get("mode_rebase_game_advance")==0,
                    "realtime reaction was not activated by the actual native scheduler")
            duration=record["reaction_durations"];final=record["final"];origin=record["prepared_start"]["game_ms"]
            require(duration=={"arena_start_game_ms":final["start_game_ms"],"prepared_game_ms":origin,"final_game_ms":final["game_ms"],
                    "preparation_game_ms":origin-final["start_game_ms"],"active_game_ms":final["game_ms"]-origin,
                    "total_game_ms":final["elapsed_game_ms"],"declared_timeout_ms":case["timeout_ms"],"preparation_included_in_timeout":True},
                    "reaction timeout/active duration semantics changed")
            runtime=reaction.model_history(path,record,preparation,wire,controls,pairs,bc,normalizer)
            if runtime is not None and policy!="native":
                runtime={**runtime,"neural_control_fraction":runtime["healthy_fraction"]}
            proof=bc.read_json(path/"preparation.json")
            require([row["arena"] for row in arena if row.get("kind")=="reaction_armed"]==[proof["prepared_before_activation"]] and
                    [row["arena"] for row in arena if row.get("kind")=="reaction_active"]==[record["prepared_start"]],"prepared/activated snapshots lack authoritative engine journal")
        else:
            require(record.get("initializer")=="frozen_v6_original" and "prepared_start" not in record,"ordinary task was silently given reactive preparation")
            if policy!="native":runtime=combat.runtime_evidence(path,record,normalizer,observations,pairs,controls)
        boundary=bc.native_fallback_boundary(record["initial"],record["final"],controls,mechanics) if policy!="native" else None
        healthy=policy=="native" or (not any(boundary.values()) and not record.get("fallback_events") and
                record["final"]["outcome"]!="control_lost" and record.get("model_stats",{}).get("runtime_fallbacks",0)==0)
        if name=="realtime-reaction" and policy!="native":
            timing=combat.latency_evidence(path,record,observations,pairs)
            defense=combat.defense_reactions(path,record,mechanics,controls)
        success=task_success(case,record["final"],physics,bc)
        if case["task"]=="block" and policy!="native" and success:
            linked=combat.defense_reactions(path,record,mechanics,controls)
            success=reaction.model_credit(record,True,runtime,linked,controls,bc,mechanics,action=9)
        softlocks=combat.finite_softlocks(mechanics,controls,record["final"]) if policy!="native" else None
        episodes.append({"name":record["name"],"policy":policy,"task":case["task"],"credited_success":bool(healthy and success),
            "quality_credit":healthy,"outcome":record["final"]["outcome"],"runtime":runtime,"latency":timing,"defense_reactions":defense,
            "preparation":preparation,"softlocks":softlocks,"visibility":visibility,"capture_integrity":integrity,
            "native_fallback_boundary":boundary,"native_to_wire":roundtrip,"actual_physics":physics})
        for file in path.iterdir():
            if file.is_file():raw[str(file.relative_to(root))]=bc.digest(file)
    require(all(len(values)==3 and all(row==values[0] for row in values) for values in prepared.values()),"paired prepared starting features differ")
    require(suite.get("resources") and all(row.get("forbidden_device_descriptors")==row.get("display_socket_descriptors")==[] for row in suite["resources"]),
            "runtime headless/CPU device evidence missing")
    return {"phase":name,"episodes":episodes,"raw_sha256":raw,"manifest_sha256":bc.digest(folder/"manifest.json"),"startup":startup}


def task_success(case,final,physics,bc):
    task=case["task"];outcome=final["outcome"]
    if outcome=="time_limit":require(final["elapsed_game_ms"]>=case["timeout_ms"],"task truncated before declared native timeout")
    elif outcome=="death_or_down":require(any(actor["hp"]<=0 or actor["down"] for actor in final["actors"]),"down outcome lacks down actor")
    elif outcome=="control_lost":return False
    else:require(outcome=="task_success","unrecognized native task outcome")
    if task=="native_duel":return outcome=="death_or_down" and final["reason"]=="opponent_down"
    if outcome!="task_success":return False
    if task=="distance":return bc.distance(final)<=220.001 and final.get("distance_held_ms",0)>=200
    if task=="attack":return physics["linked_attack_hits"]>0 and physics["linked_opponent_hp_loss"]>0
    require(task=="block","unknown task")
    primary=bc.actors(final)["primary"]
    return (final["reason"]=="melee_hit_blocked" and primary["hp"]==300 and primary["received_damage"]==0 and
            primary["blocked_hits"]>=1 and physics["linked_blocked_hits"]>=1)


def quality(episodes,config):
    failures=[]
    for episode in episodes:
        if episode["policy"]!="gru":continue
        runtime=episode.get("runtime") or {};fraction=runtime.get("neural_control_fraction")
        if fraction is None or fraction<config["quality"]["minimum_healthy_neural_control_fraction"]:failures.append(episode["name"]+": healthy control below .95")
        if (episode.get("softlocks") or {}).get("violations"):failures.append(episode["name"]+": actual finite animation softlock")
        if episode.get("latency") is not None:
            for key,budget in (("model_step_ms","model_step_p95_ms"),("collector_to_command_send_ms","collector_to_command_send_p95_ms"),
                               ("observation_to_arbitration_ms","observation_to_arbitration_p95_ms")):
                values=episode["latency"].get(key)
                if values is None or values["p95"]>config["quality"][budget]:failures.append(episode["name"]+": "+key+" missing/exceeds budget")
    return failures


def ordinary_realtime_cases(evidence):
    nonblock=[row for row in evidence["episodes"] if row["task"]!="block"]
    neutral=[row for row in evidence["episodes"] if row["task"]=="block"]
    require(len(nonblock)==18 and len(neutral)==6,"selected-model ordinary realtime matrix differs")
    evidence["neutral_block_results_not_replaced"]=neutral
    return nonblock


BURST_CADENCE={"schema_version":1,"kind":"four_bursts_monotonic_v1","bursts":4,"measured_per_burst":5,
    "warmup_first_burst":2,"nominal_start_interval_ms":20000,"minimum_actual_start_interval_ms":20000,
    "maximum_actual_start_interval_ms":25000,"baseline_offsets_ms":[0,20000,40000,60000],
    "during_timing":"actual_arena_start_plus_reset_floor_before_reset"}


def cadence_rows(records,cadence):
    require(cadence==BURST_CADENCE,"service cadence changed the declared four bursts")
    result={}
    for service in ("asr","tts","decider","gemma"):
        starts=[]
        for burst in range(4):
            rows=[row for row in records if row.get("service")==service and row.get("burst_index")==burst]
            require(len(rows)==5+(2 if burst==0 else 0) and sum(row.get("warmup") is True for row in rows)==(2 if burst==0 else 0),
                    "service burst request/warmup counts differ")
            for row in rows:
                begin,nominal=row.get("request_begin_wall_monotonic_ns"),row.get("nominal_burst_start_wall_monotonic_ns")
                require(type(begin) is int and type(nominal) is int and 0<nominal<=begin and
                        row.get("request_cadence_kind")==cadence["kind"],"actual service burst clock/nominal binding missing")
            starts.append(min(row["request_begin_wall_monotonic_ns"] for row in rows))
        intervals=[(b-a)/1e6 for a,b in zip(starts,starts[1:])]
        require(all(20000<=value<=25000 for value in intervals),"actual service burst interval outside 20..25s envelope")
        result[service]={"actual_starts_wall_monotonic_ns":starts,"intervals_ms":intervals,"measured_requests":20,"warmup_requests":2}
    require(len(records)==88,"unknown/extra service burst records")
    return result


def service_cadence(root,config,bc):
    directory=root/"load/services";fixtures=bc.read_json(directory/"fixtures.json")
    cadence=config["services"].get("request_cadence")
    require(cadence==BURST_CADENCE and fixtures.get("request_cadence")==cadence,"actual service fixture omitted declared cadence")
    def phase(name):
        require(bc.read_json(directory/name/"result.json").get("request_cadence")==cadence,"service phase changed burst cadence")
        return bc.canonical.read_jsonl(directory/name/"requests.jsonl")
    before=cadence_rows(phase("before"),cadence)
    during={provider:cadence_rows([row for index in range(4) for row in phase(f"during-{provider}-{index}")],cadence)
            for provider in config["load"]["policies"]}
    return {"configuration":cadence,"before":before,"during":during,"same_timing_envelope":True}


def load_startup_evidence(root,declared,bc):
    folder=root/"load";manifest=bc.read_json(folder/"manifest.json");warmups=bc.read_json(folder/"startup-warmup.json")
    require(manifest.get("startup_warmup")==warmups and set(warmups)=={"gru","mlp"} and
            manifest.get("startup_runtime_counts")=={"gru":4,"mlp":4} and manifest.get("startup_warmup_before_any_arena") is True and
            manifest.get("compact_replies") is True,"four-actor actual backend startup/compact profile missing")
    last=0
    for name in ("gru","mlp"):
        proofs=warmups[name]
        require(isinstance(proofs,list) and len(proofs)==4 and [row.get("runtime_pool_slot") for row in proofs]==[0,1,2,3],
                "four-actor runtime pool duplicated or incomplete")
        for proof in proofs:
            before,after=proof.get("runtime_before",{}),proof.get("runtime_after",{})
            execution_profile(proof.get("backend_execution_profile"),declared)
            if "inference_cpu_threads" in declared:
                require(before.get("execution_profile")==after.get("execution_profile")==proof.get("backend_execution_profile"),
                        "load runtime/backend execution profiles disagree")
            require(proof.get("kind")=="body_policy_startup_warmup" and proof.get("steps")==2 and proof.get("device")=="cpu" and
                    proof.get("feature_count")==184 and proof.get("feature_values")=="synthetic_zero" and proof.get("head_mask")==[True]*12 and
                    proof.get("state_in") is None and proof.get("returned_hidden_discarded_each_step") is True and
                    proof.get("model_process_calls")==proof.get("commands")==0 and proof.get("quality_credit") is False and
                    before==after and before.get("actors")==0 and before.get("expected_epochs") is None and
                    proof.get("checkpoint_sha256")==declared["candidate"]["checkpoint_sha256"][name] and
                    proof.get("normalizer_sha256")==declared["candidate"]["normalizer_sha256"] and
                    proof.get("backend_source_sha256")==bc.digest(root/"code/body-service/src/gothic_body/runtime.py"),
                    "load startup changed actor memory/model or fabricated a quality command")
            times=proof.get("timings",[]);require(len(times)==2 and [row.get("step") for row in times]==[1,2],"load backend warmup step count differs")
            for row in times:
                start,end=row.get("begin_wall_monotonic_ns"),row.get("end_wall_monotonic_ns")
                require(type(start) is int and type(end) is int and last<=start<end and abs(row["elapsed_ms"]-(end-start)/1e6)<=1e-9,
                        "load backend startup timestamps contradict actual timing")
                last=end
    suite=bc.read_json(folder/"suite.json");first=[]
    for record in suite["cases"]:
        pairs=bc.timed_pairs(bc.canonical.read_jsonl(folder/record["name"]/"ipc.jsonl"))
        first.extend(begin*1e9 for request,_,begin,_ in pairs if request.get("cmd") in ("reset","body_acquire","arena_start"))
        if record["policy"]!="native":
            slots=record.get("runtime_startup_slots",{})
            require(set(slots)=={str(entity) for entity in record["physical_actor_ids"]} and sorted(slots.values())==[0,1,2,3],
                    "actual neural actor did not use a unique warmed runtime pool slot")
    require(first and last<min(first),"load backend startup occurred after actual scene reset/ownership/start")
    gc=collector_gc_evidence(folder,manifest,declared,bc,last,min(first))
    return {"actual_independent_warmed_runtimes":8,"steps_per_runtime":2,"before_any_arena":True,"real_samples_removed":0,
            "startup_sha256":bc.digest(folder/"startup-warmup.json"),"proofs":warmups,
            "backend_execution":backend_execution_evidence(folder,declared,bc),"collector_gc":gc,
            "native_deadline_polling":deadline_polling_evidence(folder,declared,bc),
            "native_workers":native_worker_evidence(folder,manifest,declared,bc)}


def report(root,snapshot,native_faults,native_reference):
    declared,bc,combat,reaction,provenance=sources(root,snapshot)
    config=bc.read_json(root/"code/evals/npc-body/body-stage8-evaluation-v2.json")
    original=bc.read_json(Path(declared["reference_run"])/"code/evals/npc-body/body-stage8-v1.json")
    current=bc.read_json(root/"code/evals/npc-body/body-stage8-v1.json")
    issues=[];checkpoint_issues=[];evidence={"preregistration":declared,"audit_source":provenance}
    service_measurement=None;service_budget_issues=[]
    def gate(name,operation):
        try:value=operation();evidence[name]=value;return value
        except (OSError,ValueError,KeyError,TypeError,IndexError,StopIteration) as error:issues.append(name+": "+str(error));return None
    real=gate("realtime_reaction",lambda:repaired_phase(root,"realtime-reaction",config,declared,bc,combat,reaction))
    screen=gate("fresh_screening",lambda:repaired_phase(root,"screening",config,declared,bc,combat,reaction))
    ordinary=gate("ordinary_selected_realtime",lambda:combat.phase_evidence(root,"realtime",original,Path(declared["reference_run"])))
    if ordinary:
        nonblock=ordinary_realtime_cases(ordinary)
        checkpoint_issues.extend(quality(nonblock,config))
        if current["runtime"].get("startup_warmup_steps"):gate("ordinary_backend_startup",lambda:startup_evidence(root/"realtime",bc.read_json(root/"realtime/manifest.json"),declared,root,bc))
    if real:
        checkpoint_issues.extend(quality(real["episodes"],config))
        if not any(episode["policy"]=="gru" and any(row["action_id"]==9 for row in (episode.get("defense_reactions") or {}).get("linked_reactions",[]))
                   for episode in real["episodes"]):checkpoint_issues.append("no actual GRU block9 within observed incoming-hit window")
        gate("actual_model_faults",lambda:{name:bc.service_probe(root/"realtime-reaction"/("serve-probe-"+name),declared["candidate"]["checkpoint_sha256"][name])
                                          for name in ("gru","mlp")})
    if screen:
        comparison,failed=combat.combat_comparisons(screen["episodes"],{**original,"quality":config["quality"]})
        evidence["combat_quality"]=comparison;checkpoint_issues.extend(failed);checkpoint_issues.extend(quality(screen["episodes"],config))
    normalizer=root/"dataset/normalization.json"
    require(normalizer.is_file() and bc.digest(normalizer)==declared["candidate"]["normalizer_sha256"],"gate copy of selected child normalization missing/changed")
    for policy in ("gru","mlp"):require(bc.digest(root/"models"/policy/"best.pt")==declared["candidate"]["checkpoint_sha256"][policy],"gate copies of selected child weights changed")
    shadow=gate("shadow",lambda:combat.phase_evidence(root,"shadow",original,Path(declared["reference_run"])))
    if shadow and current["runtime"].get("startup_warmup_steps"):gate("shadow_backend_startup",lambda:startup_evidence(root/"shadow",bc.read_json(root/"shadow/manifest.json"),declared,root,bc))
    load_evidence=gate("four_actual_neural_actors",lambda:combat.four_actor_evidence(root,original))
    if load_evidence:
        load_manifest=bc.read_json(root/"load/manifest.json");package=root/"code/body-service/src"
        require(load_manifest.get("policy_code_sha256")=={str(file.relative_to(package)):bc.digest(file) for file in sorted((package/"gothic_body").glob("*.py"))} and
                load_manifest.get("feature_packer_sha256")==bc.digest(root/"code/scripts/body_trajectory_dataset.py") and
                Path(load_manifest.get("feature_packer_path",".")).resolve()==(root/"code/scripts/body_trajectory_dataset.py").resolve() and
                load_manifest.get("device")=="cpu" and load_manifest.get("checkpoint_sha256")==declared["candidate"]["checkpoint_sha256"],
                "four-actor load imported another actual Gate package/packer/selected weights")
        checkpoint_issues.extend(quality(load_evidence["cases"],config))
        gate("load_backend_startup",lambda:load_startup_evidence(root,declared,bc))
        if combat.service_gate_role(current)=="remote_host_not_measured":
            # No protected service shares this host; the load manifest must say nothing was measured.
            gate("protected_services_not_colocated",lambda:require(load_manifest.get("protected_services_measured") is False and
                 not (root/"load/services").exists(),"remote-host load measured or fabricated protected-service evidence"))
        else:
            service=gate("protected_services",lambda:combat.protected_service_load(root,current,load_evidence))
            if service:evidence["protected_services"],service_budget_issues=service;service_measurement=evidence["protected_services"]
            if current["services"].get("request_cadence") is not None:gate("service_cadence",lambda:service_cadence(root,current,bc))
    def faults():
        import body_api_report as api
        manifest=bc.read_json(native_faults/"manifest.json")
        require(manifest["binary_sha256"]==declared["updated_engine"]["binary_sha256"],"native fault proof is scoped to another binary")
        cfg=bc.read_json(root/"code/evals/npc-body/body-api-v1.json")
        result=api.summarize(native_faults,cfg,native_reference)
        require(result.get("status")=="complete" and result.get("full_stage4_evidence") is True and not result.get("issues"),"actual current native fault suite incomplete")
        return {"run":str(native_faults),"report":result,"files":{str(file):bc.digest(file) for file in native_faults.rglob("*") if file.is_file()}}
    gate("current_native_faults",faults)
    resources={}
    for phase in ("shadow","realtime","realtime-reaction","screening","load"):
        def resource(phase=phase):
            execution=declared["runtime_execution_phases"][phase]
            before=bc.read_json(root/f"services-{execution}-before.json");after=bc.read_json(root/f"services-{execution}-after.json")
            services=bc.all_services(before,after,original["resources"]["required_services"])
            text=(root/f"resources-{execution}.txt").read_text();peak=re.search(r"Maximum resident set size \(kbytes\):\s+(\d+)",text)
            require(re.search(r"Exit status:\s+0\s*$",text,re.M) and peak and 0<int(peak[1])<=6*1024*1024,"runtime phase failed or exceeded memory budget")
            require(before.get("recorded_at_utc")<=after.get("recorded_at_utc") and before.get("gpu") and after.get("gpu"),"runtime resource/service timestamps or GPU snapshots missing")
            require("CPUQuota=200%" in (root/"code/scripts/body-bc-remote.sh").read_text() and "MemoryMax=6G" in (root/"code/scripts/body-bc-remote.sh").read_text() and
                    "TasksMax=128" in (root/"code/scripts/body-bc-remote.sh").read_text(),"runtime launcher resource caps missing")
            return {"output_phase":phase,"execution_phase":execution,"services":services,"peak_rss_kib":int(peak[1]),
                    "time_sha256":bc.digest(root/f"resources-{execution}.txt"),"before_sha256":bc.digest(root/f"services-{execution}-before.json"),
                    "after_sha256":bc.digest(root/f"services-{execution}-after.json"),"launcher_log_sha256":bc.digest(root/f"launcher-{execution}.log")}
        resources[phase]=gate("resources-"+phase,resource)
    service_report=combat.service_gate_summary(current,service_measurement,service_budget_issues,
        [issue for issue in issues if issue.startswith(("protected_services:","service_cadence:","resources-load:"))])
    checkpoint_issues.extend(service_report["combat_budget_issues"])
    ready=not issues and not checkpoint_issues
    return {"schema_version":1,"kind":"body_stage8_final_runtime_report","implementation_ready":not issues,"stage8_ready":ready,
            "checkpoint_ready":ready,"combat_gate_ready":ready,"service_gate_ready":service_report["service_gate_ready"],
            "service_gate_role":service_report["service_gate_role"],"service_report":service_report,
            "stage8_readiness_scope":"combat_runtime" if service_report["service_gate_role"] in ("standalone_report","remote_host_not_measured") else "combat_runtime_and_candidate_services",
            "combined_runtime_and_service_ready":ready and service_report["service_gate_ready"],
            "release_ready":False,"issues":issues,"checkpoint_issues":checkpoint_issues,"evidence":evidence,
            "resources":resources,"original_neutral_block_failures_replaced":False,"fresh_selection_allowed":False,
            "limitations":["Known reaction cases do not replace original v6 neutral-block failures.",
                "Preparation is included in the native held-start timeout; active duration is separate.",
                "The later 100-seed release gate remains outstanding."]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",type=Path,required=True);parser.add_argument("--audit-manifest",type=Path,required=True)
    parser.add_argument("--native-faults",type=Path,required=True);parser.add_argument("--native-reference",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True);args=parser.parse_args()
    try:value=report(args.run.resolve(),args.audit_manifest.resolve(),args.native_faults.resolve(),args.native_reference.resolve())
    except (OSError,ValueError,KeyError,TypeError,IndexError,StopIteration) as error:
        value={"kind":"body_stage8_final_runtime_report","implementation_ready":False,"stage8_ready":False,"checkpoint_ready":False,"issues":[str(error)]}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/"body-stage8-final.json").write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")
    (args.output_dir/"body-stage8-services.json").write_text(json.dumps(value.get("service_report",{
        "kind":"body_stage8_protected_service_report","status":"incomplete","measurement_ready":False,"service_gate_ready":False,
        "measurement_issues":value.get("issues",[]),"physical_quality_credit":False}),indent=2,allow_nan=False)+"\n")
    print(json.dumps({key:value.get(key) for key in ("implementation_ready","checkpoint_ready","issues","checkpoint_issues")}))
    return 0 if value.get("stage8_ready") else 2


if __name__=="__main__":raise SystemExit(main())
