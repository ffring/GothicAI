#!/usr/bin/env python3
"""Read only closed four-actor load before any fresh screening is opened.

Uses the separately frozen final reader's actual runtime/service criteria.
Does not start models, gameplay, tests or service requests, or grant Stage8 credit.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import time


def require(condition,message):
    if not condition:raise ValueError(message)


def digest(path):
    value=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):value.update(block)
    return value.hexdigest()


def report(root,snapshot,load_snapshot):
    started=time.monotonic();here=Path(__file__).resolve()
    binding=json.loads(load_snapshot.read_text());directory=load_snapshot.resolve().parent
    require(binding.get("schema_version")==1 and binding.get("kind")=="body_stage8_load_runtime_audit_snapshot" and
            binding.get("environment_sha256")==digest(root/"environment.json") and
            binding.get("declaration_sha256")==digest(root/"final-evaluation.json") and
            binding.get("final_audit_manifest_sha256")==digest(snapshot) and here.is_relative_to(directory) and
            binding.get("files",{}).get(str(here.relative_to(directory)))==digest(here),"external load-only reader snapshot differs")
    for name,expected in binding["files"].items():
        file=(directory/name).resolve()
        require(file.is_relative_to(directory) and file.is_file() and digest(file)==expected,"external load reader dependency changed")
    suite=json.loads((root/"load/suite.json").read_text())
    require(suite.get("status")=="collected" and len(suite.get("cases",[]))==12 and not suite.get("error") and
            (root/"services-load-after.json").is_file(),"four-actor load is not closed with all12 cohorts")
    spec=importlib.util.spec_from_file_location("frozen_final_load_reader",snapshot.resolve().parent/"body_stage8_final_report.py")
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    declared,bc,combat,_,source_proof=reader.sources(root,snapshot)
    config=bc.read_json(root/"code/evals/npc-body/body-stage8-evaluation-v2.json")
    current=bc.read_json(root/"code/evals/npc-body/body-stage8-v1.json")
    original=bc.read_json(Path(declared["reference_run"])/"code/evals/npc-body/body-stage8-v1.json")
    require(bc.digest(root/"dataset/normalization.json")==declared["candidate"]["normalizer_sha256"],"load selected normalization copy changed")
    for policy in ("gru","mlp"):
        require(bc.digest(root/"models"/policy/"best.pt")==declared["candidate"]["checkpoint_sha256"][policy],"load selected checkpoint copy changed")
    load=combat.four_actor_evidence(root,original)
    manifest=bc.read_json(root/"load/manifest.json");package=root/"code/body-service/src"
    require(manifest.get("policy_code_sha256")=={str(file.relative_to(package)):bc.digest(file) for file in sorted((package/"gothic_body").glob("*.py"))} and
            manifest.get("feature_packer_sha256")==bc.digest(root/"code/scripts/body_trajectory_dataset.py") and
            Path(manifest.get("feature_packer_path",".")).resolve()==(root/"code/scripts/body_trajectory_dataset.py").resolve() and
            manifest.get("device")=="cpu" and manifest.get("checkpoint_sha256")==declared["candidate"]["checkpoint_sha256"],
            "four-actor load imported another actual Gate package/packer/selected weights")
    issues=reader.quality(load["cases"],config)
    for record in suite["cases"]:
        if record["policy"]=="gru" and (record.get("fallback_events") or record.get("model_stats",{}).get("runtime_fallbacks",0)):
            issues.append(record["name"]+": actual runtime fallback during neural load")
    startup=reader.load_startup_evidence(root,declared,bc)
    services,service_issues=combat.protected_service_load(root,current,load);issues.extend(service_issues)
    cadence=reader.service_cadence(root,current,bc)
    execution=declared["runtime_execution_phases"]["load"]
    before=bc.read_json(root/f"services-{execution}-before.json");after=bc.read_json(root/f"services-{execution}-after.json")
    identities=bc.all_services(before,after,original["resources"]["required_services"])
    require(identities["unchanged"] and identities["services_all_active"],"protected service identity/activity changed during load")
    text=(root/f"resources-{execution}.txt").read_text();peak=re.search(r"Maximum resident set size \(kbytes\):\s+(\d+)",text)
    require(re.search(r"Exit status:\s+0\s*$",text,re.M) and peak and 0<int(peak[1])<=6*1024*1024,"load phase failed or exceeded memory budget")
    require(before.get("recorded_at_utc")<=after.get("recorded_at_utc") and before.get("gpu") and after.get("gpu"),"load service clocks/GPU snapshots missing")
    launcher=(root/"code/scripts/body-bc-remote.sh").read_text()
    require(all(value in launcher for value in ("CPUQuota=200%","MemoryMax=6G","TasksMax=128")),"load launcher resource caps missing")
    paths=[file for file in (root/"load").rglob("*") if file.is_file()]
    paths.extend(root/f"{stem}-{execution}{suffix}" for stem,suffix in (("services","-before.json"),("services","-after.json"),("resources",".txt"),("launcher",".log")))
    raw={str(file.relative_to(root)):digest(file) for file in paths}
    return {"schema_version":1,"kind":"body_stage8_load_saved_runtime_report","passed":not issues,"issues":issues,
            "closed_load_only":True,"load_read":True,"fresh_screening_read":False,"stage8_quality_credit":False,
            "behavioral_quality_credit":False,"model_forward_executed":False,"engine_started":False,
            "created_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"audit_wall_s":time.monotonic()-started,
            "candidate_policy":"gru","mlp_role":"comparison_only","candidate_checkpoint_sha256":declared["candidate"]["checkpoint_sha256"],
            "normalization_sha256":declared["candidate"]["normalizer_sha256"],"environment_sha256":digest(root/"environment.json"),
            "declaration_sha256":digest(root/"final-evaluation.json"),"load_reader_sha256":digest(here),
            "load_audit_manifest_sha256":digest(load_snapshot),"external_reader_binding":source_proof,"raw_sha256":raw,
            "evidence":{"four_actual_actors":load,"eight_backend_startups":startup,"protected_services":services,"service_cadence":cadence,
                        "resources":{"execution_phase":execution,"peak_rss_kib":int(peak[1]),"services":identities}}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("run","audit-manifest","load-audit-manifest","output-dir"):parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args();args.output_dir.mkdir(parents=True,exist_ok=False)
    try:value=report(args.run.resolve(),args.audit_manifest.resolve(),args.load_audit_manifest.resolve())
    except (OSError,ValueError,KeyError,TypeError,IndexError,RuntimeError,StopIteration) as error:
        value={"schema_version":1,"kind":"body_stage8_load_saved_runtime_report","passed":False,"issues":[str(error)],
               "load_read":True,"fresh_screening_read":False,"stage8_quality_credit":False,"behavioral_quality_credit":False,
               "model_forward_executed":False,"engine_started":False}
    (args.output_dir/"report.json").write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"passed":value["passed"],"issues":value["issues"]}),flush=True)
    return 0 if value["passed"] else 2


if __name__=="__main__":raise SystemExit(main())
