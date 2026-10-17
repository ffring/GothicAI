#!/usr/bin/env python3
"""Saved-known-reaction audit only; no engine, models, forwards or requests."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import time

root=Path.home()/"gothic-ai/body/data/stage8-final-gate-v1"
out=root/"validation/known-reaction-audit"
source=root/"validation/final-audit-code/body_stage8_final_report.py"
snapshot=source.parent/"audit.json"
start=time.monotonic()
value={"schema_version":1,"kind":"body_stage8_known_reaction_saved_data_audit","passed":False,
       "created_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"scope":"closed known realtime-reaction only; no fresh screening read",
       "model_forward_executed":False,"engine_started":False,"source_modified":False,"fresh_screening_read":False}
try:
    require=lambda condition,message: condition or (_ for _ in ()).throw(ValueError(message))
    suite=json.loads((root/"realtime-reaction/suite.json").read_text())
    require(suite.get("status")=="collected" and not suite.get("error") and (root/"services-realtime-reaction-after.json").exists(),
            "known reaction phase not yet closed")
    spec=importlib.util.spec_from_file_location("frozen_stage8_final_reader",source)
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    declared,bc,combat,reaction,provenance=reader.sources(root,snapshot)
    config=bc.read_json(root/"code/evals/npc-body/body-stage8-evaluation-v2.json")
    evidence=reader.repaired_phase(root,"realtime-reaction",config,declared,bc,combat,reaction)
    failures=reader.quality(evidence["episodes"],config)
    gru=[row for row in evidence["episodes"] if row["policy"]=="gru"]
    require(len(gru)==2,"known GRU reaction side denominator changed")
    for episode in gru:
        require(episode["credited_success"] and episode["outcome"]=="task_success" and episode["quality_credit"],
                episode["name"]+": strict block task failed or fallback lost model credit")
        require(episode["runtime"]["runtime_fallbacks"]==0 and episode["runtime"]["neural_control_fraction"]>=.95,
                episode["name"]+": runtime fallback/unhealthy ownership")
        require(any(row["action_id"]==9 and row["hp_preserved"] and
                    row["observed_windup_game_ms"]<=row["actual_started_game_ms"]<=row["incoming_attempt_game_ms"] and
                    row["pose_change_seq"]<row["hit_attempt_seq"]<row["defence_result_seq"]
                    for row in episode["defense_reactions"]["linked_reactions"]),
                episode["name"]+": no timely actual model block9 and paired incoming blocked hit")
    require(not failures,"; ".join(failures))
    probes={name:bc.service_probe(root/"realtime-reaction"/("serve-probe-"+name),declared["candidate"]["checkpoint_sha256"][name])
            for name in ("gru","mlp")}
    require(all(proof["requests"]==14 for proof in probes.values()),"actual 14-request model fault probe incomplete")
    before=bc.read_json(root/"services-realtime-reaction-before.json")
    after=bc.read_json(root/"services-realtime-reaction-after.json")
    original=bc.read_json(Path(declared["reference_run"])/"code/evals/npc-body/body-stage8-v1.json")
    services=bc.all_services(before,after,original["resources"]["required_services"])
    require(services["unchanged"] and services["services_all_active"],"required protected service changed/inactive")
    value.update(passed=True,quality_issues=failures,episodes=evidence["episodes"],raw_sha256=evidence["raw_sha256"],
                 manifest_sha256=evidence["manifest_sha256"],actual_model_faults=probes,services=services,
                 candidate_checkpoint_sha256=declared["candidate"]["checkpoint_sha256"],normalization_sha256=declared["candidate"]["normalizer_sha256"],
                 audit_source=provenance,environment_sha256=bc.digest(root/"environment.json"),
                 declaration_sha256=bc.digest(root/"final-evaluation.json"))
except Exception as error:
    value["issues"]=[str(error)];value["exception_type"]=type(error).__name__
value["audit_wall_s"]=time.monotonic()-start
out.mkdir(parents=True,exist_ok=True)
(out/"report.json").write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")
print(json.dumps({key:value.get(key) for key in ("passed","issues","audit_wall_s")}),flush=True)
raise SystemExit(0 if value["passed"] else 2)
