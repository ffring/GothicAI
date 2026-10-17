#!/usr/bin/env python3
"""Read only closed shadow/ordinary/reaction evidence before four-actor load.

No model, engine, service workload, load evidence or fresh screening is opened.
The separately frozen full reader supplies identical runtime/physics criteria.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time


def require(condition,message):
    if not condition:raise ValueError(message)


def digest(path):
    value=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):value.update(block)
    return value.hexdigest()


def known_quality(ordinary,reaction,reader,config,combat):
    nonblock=reader.ordinary_realtime_cases(ordinary)
    failures=reader.quality(nonblock+reaction["episodes"],config)
    gru=[row for row in reaction["episodes"] if row["policy"]=="gru"]
    require(len(gru)==2,"known reactive GRU side denominator changed")
    for episode in gru:
        runtime=episode.get("runtime") or {}
        if not (episode["credited_success"] and episode["quality_credit"] and episode["outcome"]=="task_success" and
                runtime.get("runtime_fallbacks")==0 and runtime.get("neural_control_fraction",0)>=.95):
            failures.append(episode["name"]+": strict actual block/no-fallback/control gate failed")
        linked=(episode.get("defense_reactions") or {}).get("linked_reactions",[])
        if not any(row["action_id"]==9 and row["hp_preserved"] and
                   row["observed_windup_game_ms"]<=row["actual_started_game_ms"]<=row["incoming_attempt_game_ms"] and
                   row["pose_change_seq"]<row["hit_attempt_seq"]<row["defence_result_seq"] for row in linked):
            failures.append(episode["name"]+": no timely actual model block9 before paired incoming blocked hit")
    comparisons,physical_failures=combat.combat_comparisons(nonblock+reaction["episodes"],{**config,"candidate_policy":"gru"})
    failures.extend(physical_failures)
    return comparisons,failures


def report(root,snapshot,known_snapshot=None):
    started=time.monotonic();here=Path(__file__).resolve()
    environment=json.loads((root/"environment.json").read_text())
    if here.is_relative_to(root/"code"):
        require(environment["frozen_files"].get(str(here.relative_to(root)))==digest(here),"known-only Gate reader source changed")
    else:
        require(known_snapshot is not None,"external known reader needs its own immutable snapshot")
        binding=json.loads(known_snapshot.read_text());directory=known_snapshot.resolve().parent
        require(binding.get("schema_version")==1 and binding.get("kind")=="body_stage8_known_runtime_audit_snapshot" and
                binding.get("environment_sha256")==digest(root/"environment.json") and
                binding.get("declaration_sha256")==digest(root/"final-evaluation.json") and
                binding.get("final_audit_manifest_sha256")==digest(snapshot) and here.is_relative_to(directory) and
                binding.get("files",{}).get(str(here.relative_to(directory)))==digest(here),"external known-only reader snapshot differs")
        for name,expected in binding["files"].items():
            file=(directory/name).resolve()
            require(file.is_relative_to(directory) and file.is_file() and digest(file)==expected,"external known reader dependency changed")
    for name,denominator in (("shadow",3),("realtime",24),("realtime-reaction",6)):
        suite=json.loads((root/name/"suite.json").read_text())
        require(suite.get("status")=="collected" and len(suite.get("cases",[]))==denominator and not suite.get("error") and
                (root/f"services-{name}-after.json").is_file(),name+": phase is not closed")
    source=snapshot.resolve().parent/"body_stage8_final_report.py"
    spec=importlib.util.spec_from_file_location("frozen_final_known_reader",source)
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    declared,bc,combat,reaction_helper,source_proof=reader.sources(root,snapshot)
    config=bc.read_json(root/"code/evals/npc-body/body-stage8-evaluation-v2.json")
    current=bc.read_json(root/"code/evals/npc-body/body-stage8-v1.json")
    reference=Path(declared["reference_run"])
    shadow=combat.phase_evidence(root,"shadow",current,reference)
    ordinary=combat.phase_evidence(root,"realtime",current,reference)
    reaction=reader.repaired_phase(root,"realtime-reaction",config,declared,bc,combat,reaction_helper)
    startups={}
    if current["runtime"].get("startup_warmup_steps"):
        for phase in ("shadow","realtime"):
            startups[phase]=reader.startup_evidence(root/phase,bc.read_json(root/phase/"manifest.json"),declared,root,bc)
    comparisons,quality_issues=known_quality(ordinary,reaction,reader,config,combat)
    probes={name:bc.service_probe(root/"realtime-reaction"/("serve-probe-"+name),declared["candidate"]["checkpoint_sha256"][name])
            for name in ("gru","mlp")}
    require(all(proof["requests"]==14 for proof in probes.values()),"actual model fault probes lack all14 source-bound receipts")
    services={};files={}
    for phase in ("shadow","realtime","realtime-reaction"):
        before=root/f"services-{phase}-before.json";after=root/f"services-{phase}-after.json"
        services[phase]=bc.all_services(bc.read_json(before),bc.read_json(after),current["resources"]["required_services"])
        require(services[phase]["unchanged"] and (current.get("service_gate_role")=="remote_host_not_measured" or services[phase]["services_all_active"]),
                "protected service identity/inactive change during "+phase)
        for file in (before,after,root/f"resources-{phase}.txt",root/f"launcher-{phase}.log"):
            require(file.is_file(),"closed phase resource/log missing")
            files[str(file.relative_to(root))]=digest(file)
    for name,evidence in (("shadow",shadow),("realtime",ordinary),("realtime-reaction",reaction)):
        files.update(evidence.get("raw_sha256",{}))
        for file in (root/name/"suite.json",root/name/"manifest.json"):
            files[str(file.relative_to(root))]=digest(file)
    return {"schema_version":1,"kind":"body_stage8_known_saved_runtime_report","passed":not quality_issues,
            "issues":quality_issues,"closed_known_stages_only":True,"load_read":False,"fresh_screening_read":False,
            "stage8_quality_credit":False,"model_forward_executed":False,"engine_started":False,
            "created_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"audit_wall_s":time.monotonic()-started,
            "candidate_checkpoint_sha256":declared["candidate"]["checkpoint_sha256"],"normalization_sha256":declared["candidate"]["normalizer_sha256"],
            "environment_sha256":digest(root/"environment.json"),"declaration_sha256":digest(root/"final-evaluation.json"),
            "known_reader_sha256":digest(here),"external_reader_binding":source_proof,"raw_sha256":files,
            "known_audit_manifest_sha256":digest(known_snapshot) if known_snapshot else None,
            "evidence":{"shadow":shadow,"ordinary_realtime":ordinary,"realtime_reaction":reaction,"startup":startups,
                        "actual_model_faults":probes,"known_task_comparisons":comparisons,"services":services}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",type=Path,required=True);parser.add_argument("--audit-manifest",type=Path,required=True)
    parser.add_argument("--known-audit-manifest",type=Path)
    parser.add_argument("--output-dir",type=Path,required=True);args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=False)
    try:value=report(args.run.resolve(),args.audit_manifest.resolve(),args.known_audit_manifest.resolve() if args.known_audit_manifest else None)
    except (OSError,ValueError,KeyError,TypeError,IndexError,RuntimeError,StopIteration) as error:
        value={"schema_version":1,"kind":"body_stage8_known_saved_runtime_report","passed":False,"issues":[str(error)],
               "load_read":False,"fresh_screening_read":False,"stage8_quality_credit":False,"model_forward_executed":False,"engine_started":False}
    (args.output_dir/"report.json").write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"passed":value["passed"],"issues":value["issues"]}),flush=True)
    return 0 if value["passed"] else 2


if __name__=="__main__":raise SystemExit(main())
