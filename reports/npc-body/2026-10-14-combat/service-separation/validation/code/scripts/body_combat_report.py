#!/usr/bin/env python3
"""Independent stage 8 admission from immutable numeric/IPC/native evidence.

This module never imports a model or creates an engine. A successful diagnostic
probe does not establish the later screening, realtime or four-actor gates.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

sys.path.insert(0,str(Path(__file__).resolve().parent))
import body_bc_report as bc

require,read,digest = bc.require,bc.read_json,bc.digest
TASKS = ("distance","attack","block","native_duel")
EXPOSED_REFERENCE_SEEDS={17,61,73,89,101,127,29,113,43,151,163}
SOURCES = {"npc_pre_tick","post_observation","controller_loss_before_idle",
           "controller_loss_after_idle_request","controller_segment_completed",
           "controller_recovered","controller_release_visibility"}
HOUSEKEEPING_BRANCHES = ("hit_lock","fai_or_script_wait","wait_move")
SOURCES.update("owned_consumed_"+branch+"_"+edge for branch in HOUSEKEEPING_BRANCHES for edge in ("before","after"))
ORACLE_KEYS = {"visibility_diagnostic","oracle_debug_only","pose_view_deg","actual_can_see","center_ray","head_ray"}


def percentile(values,quantile=.95):
    require(values and all(type(value) in (int,float) and math.isfinite(value) and value >= 0 for value in values),
            "latency population empty/nonfinite/negative")
    ordered=sorted(values)
    return ordered[max(0,math.ceil(len(ordered)*quantile)-1)]



def declared_clearances(config):
    values=config["new_data"]["micro_wall_clearances_cm"]
    require(isinstance(values,list) and len(values)==len(set(values))==6 and
            all(type(value) in (int,float) and math.isfinite(value) and 60<=value<=180 for value in values),
            "six distinct bounded micro clearances were not declared before collection")
    return values


def validate_config(config:dict[str,Any]) -> dict[str,Any]:
    require(config.get("schema_version") == 1 and config.get("candidate_policy") == "gru", "stage8 candidate/schema differs")
    reference = config["reference"]
    require(reference.get("immutable") is True and reference.get("old_arena_role") == "development_regression", "old experiment must remain a development reference")
    groups = {}
    for encoded,split in config["groups"].items():
        family,seed = json.loads(encoded)
        require(isinstance(family,str) and type(seed) is int and split in ("train","eval","test","ood"), "group assignment malformed")
        require(seed not in groups, "seed belongs to multiple collection groups")
        groups[seed] = split
    new = config["new_data"]
    declared_clearances(config)
    sets = {split:set(new[split+"_seeds"]) for split in ("train","eval","test","ood")}
    require(all(len(values) == len(new[split+"_seeds"]) and all(type(seed) is int for seed in values)
                for split,values in sets.items()), "new seed list malformed/duplicated")
    require(all(not sets[a]&sets[b] for a in sets for b in sets if a != b), "new collection/evaluation split overlap")
    require(not set.union(*sets.values())&EXPOSED_REFERENCE_SEEDS,"new training/selection group reuses an exposed reference seed")
    require(all(groups.get(seed) == split for split,values in sets.items() for seed in values), "new split differs from grouped assignment")
    screen = config["screening"]
    seeds = screen["seeds"]
    require(len(seeds) == len(set(seeds)) >= 20 and not set(seeds)&set(groups), "fresh screening overlaps exposed data or has fewer than20seeds")
    require(screen.get("held_out") is True and screen.get("selection_allowed") is False and screen.get("checkpoint_fixed_before_first_case") is True,
            "screening may influence weight selection")
    cases = screen["cases"]
    require(len(cases) == len(seeds)*len(TASKS) and len({row["name"] for row in cases}) == len(cases), "screening matrix incomplete/duplicated")
    for task in TASKS:
        selected = [row for row in cases if row["task"] == task]
        require({row["seed"] for row in selected} == set(seeds) and len(selected) == len(seeds), "screening task seed set differs")
        require(all(row["side"] == seeds.index(row["seed"])%2 for row in selected), "screening sides differ from declared balanced alternation")
    train = config["training"]
    require(train.get("selection_split") == "eval" and train.get("test_and_ood_selection") is False and
            train.get("selection_view") == "full_episode" and train.get("selection_criterion") == "macro_f1_then_unweighted_nll" and
            train.get("selection_actions") == list(range(1,12)) and train.get("selection_seed_groups") == new["eval_seeds"] and
            train.get("legacy_eval_role") == "development_regression_only", "model selection scope or continuous evaluation differs")
    data = config["dataset"]
    require(data.get("included_actions") == list(range(1,12)) and data.get("minimum_train_labels_per_action",0) >= 20 and
            data.get("normalization_source") == "train_only" and data.get("sampling_view") == "periodic-v1", "BC feature/coverage/split gate weakened")
    quality = config["quality"]
    require(quality.get("minimum_success_delta_to_native") == screen.get("minimum_success_delta_to_native") == -.15 and
            quality.get("minimum_healthy_neural_control_fraction") >= .95 and
            quality.get("maximum_invariant_violations") == quality.get("maximum_softlocks") == 0 and
            quality.get("no_native_fallback_success_credit") is True, "combat gate weakened")
    for field,limit in (("model_step_p95_ms",5),("collector_to_command_send_p95_ms",20),("observation_to_arbitration_p95_ms",100)):
        require(0 < quality[field] <= limit, "realtime latency budget weakened")
    require(0 <= quality["maximum_service_p95_degradation_fraction"] <= .1, "protected-service degradation budget weakened")
    load = config["load"]
    require(load.get("actors") == [1,4] and load.get("same_engine_world") is True and load.get("duplicate_virtual_actors_allowed") is False,
            "four-actor test replaced with virtual or sequential actors")
    require(load.get("cohorts_per_provider")==4 and load.get("minimum_distinct_neural_owners")==4 and load.get("quality_credit") is False and
            config["services"].get("during_cohorts")==4 and config["services"].get("measured_requests_per_cohort")==5 and
            config["services"].get("measured_requests")==20 and config["services"].get("warmup_requests")==2 and
            config["services"].get("concurrency_per_service")==1,"four actor/service workload preregistration differs")
    return {"screening_seeds":seeds,"screening_cases":len(cases),"new_groups":{str(seed):groups[seed] for values in sets.values() for seed in values},
            "screening_selection_allowed":False,"release_gate_claim":False}


def masked_refresh_evidence(entry,row,command,receipt,controls):
    """A genuine forbidden live mask stays a rejected decision, never a start."""
    matched=[actor for actor in receipt.get("body_control",{}).get("actors",[]) if actor.get("entity_id")==command["entity_id"]]
    action=command.get("action_id");source=row.get("action_mask");arena=receipt.get("arena",{})
    require(len(matched)==1 and type(action) is int and 1<=action<=11 and isinstance(source,list) and len(source)==12 and
            all(type(value) is bool for value in source) and source[action] is True and row.get("periodic") is True and
            row.get("can_decide") is True and row.get("action_mask_valid") is True,"masked refusal lacks an eligible allowed periodic source")
    current=matched[0];mask=current.get("action_mask")
    hp=[actor for actor in arena.get("actors",[]) if actor.get("entity_id") in (command["entity_id"],command["target_entity_id"])]
    fields=("entity_id","session_epoch","world_epoch","control_generation","provider","goal_version","target_entity_id")
    require(receipt.get("accepted") is False and receipt.get("reason")=="masked" and receipt.get("execution_id")==0 and
            entry.get("command_id") is None and entry.get("stale_refresh_pending") is True and entry.get("refresh_reason","masked")=="masked" and
            type(entry.get("recovery_attempt")) is int and 1<=entry["recovery_attempt"]<=2 and
            all(current.get(key)==command.get(key) for key in fields) and current.get("owner")=="scripted" and
            current.get("release_pending") is False and current.get("control_allowed") is True and
            type(current.get("observation_seq")) is int and current["observation_seq"]>=row["observation_seq"] and
            type(current.get("t_game_ms")) is int and current["t_game_ms"]>row["t_game_ms"] and
            isinstance(mask,list) and len(mask)==12 and all(type(value) is bool for value in mask) and mask[action] is False and
            arena.get("outcome")=="running" and arena.get("infrastructure") is False and len(hp)==2 and
            all(actor.get("hp",0)>0 and not actor.get("down") and not actor.get("dead") for actor in hp),
            "masked refusal lacks actual healthy live-mask change and bounded refresh proof")
    require(not any(event.get("phase")=="started" and event.get("command_id")==command["command_id"] and
                    all(event.get(key)==command.get(key) for key in bc.canonical.IDENTITY) for event in controls),
            "masked refused command was physically started")
    return {"entity_id":command["entity_id"],"command_id":command["command_id"],"source_observation_seq":row["observation_seq"],
            "source_game_ms":row["t_game_ms"],"current_observation_seq":current["observation_seq"],"current_game_ms":current["t_game_ms"],
            "source_action_mask":source,"actual_current_action_mask":mask,"recovery_attempt":entry["recovery_attempt"],
            "accepted":False,"execution_id":0,"rejected_opportunity_retained":True}


def target_guard_wait_evidence(entry,row,command,receipt,controls):
    matched=[actor for actor in receipt.get("body_control",{}).get("actors",[]) if actor.get("entity_id")==command["entity_id"]]
    wait=entry.get("native_guard_wait",{});action=command["action_id"];source=row.get("action_mask")
    fields=(*bc.canonical.IDENTITY,"provider","goal_version","target_entity_id")
    require(entry.get("native_guard_wait_pending") is True and entry.get("command_id") is None and
            entry.get("execution_id")==0 and receipt.get("accepted") is False and receipt.get("reason")=="target_invalid" and receipt.get("execution_id")==0 and
            row.get("periodic") is True and row.get("can_decide") is True and row.get("action_mask_valid") is True and
            isinstance(source,list) and len(source)==12 and all(type(value) is bool for value in source) and source[action] is True and
            row.get("target",{}).get("visible") is True and len(matched)==1,"native guard wait lacks a real refused eligible periodic decision")
    actor=matched[0];arena=receipt.get("arena",{});mask=actor.get("action_mask")
    hp=[candidate for candidate in arena.get("actors",[]) if candidate.get("entity_id") in (command["entity_id"],command["target_entity_id"])]
    require(all(actor.get(key)==command.get(key)==wait.get(key) for key in fields) and actor.get("owner")=="scripted" and
            actor.get("release_pending") is False and actor.get("control_allowed") is False and mask==[False]*12 and
            all(type(value) is bool for value in mask) and actor.get("observation_seq")>=row["observation_seq"] and
            actor.get("t_game_ms")>=row["t_game_ms"] and arena.get("outcome")=="running" and arena.get("infrastructure") is False and
            len(hp)==2 and all(candidate.get("hp",0)>0 and not candidate.get("down") and not candidate.get("dead") for candidate in hp) and
            wait.get("status")=="pending" and wait.get("refused_observation_seq")==row["observation_seq"] and
            wait.get("native_current_observation_seq")==actor["observation_seq"] and wait.get("begin_game_ms")==actor["t_game_ms"] and
            wait.get("native_current_action_mask")==mask and type(wait.get("wait_bound_ms")) is int and
            0<wait["wait_bound_ms"]<=actor.get("watchdog_timeout_ms",0) and type(wait.get("begin_wall_monotonic_ns")) is int and
            wait["begin_wall_monotonic_ns"]>=receipt.get("wall_monotonic_ns",0),"native guard wait changed live target/mask/lease or existing watchdog")
    require(not any(event.get("phase")=="started" and event.get("command_id")==command["command_id"] and
                    all(event.get(key)==command.get(key) for key in bc.canonical.IDENTITY) for event in controls),"guard-refused command was physically started")
    return {"wait":wait,"native_response_id":receipt["id"],"accepted":False,"execution_id":0,
            "rejected_opportunity_retained":True,"healthy_refresh_credit":False}


def target_guard_history(record,journal,pairs,controls,guard_waits):
    if not guard_waits:return None
    proof=record.get("native_guard_waits",{});events=proof.get("records");pending=proof.get("pending")
    require(isinstance(events,list) and pending==[],"native guard waits did not close at recovered/native final boundary")
    source={response["id"]:response for _,response,_,_ in pairs};summaries=[]
    for guard in guard_waits:
        wait=guard["wait"];identity={key:wait[key] for key in bc.canonical.IDENTITY}
        selected=[event for event in events if all(event.get(key)==value for key,value in identity.items()) and
                  event.get("refused_observation_seq")==wait["refused_observation_seq"]]
        require(selected and selected[0]=={**wait,"phase":"begin","native_response_id":guard["native_response_id"]} and
                [event.get("phase") for event in selected] in (["begin","recovered"],["begin","native_terminal"],["begin","native_handoff","native_terminal"]),
                "native guard classification phases changed")
        end=selected[-1];previous=wait["begin_wall_monotonic_ns"]
        for event in selected[1:]:
            actual=source.get(event.get("native_response_id"),{});stamp=event.get("wall_monotonic_ns");game=event.get("game_ms")
            require(type(stamp) is int and previous<=stamp and type(game) is int and 0<=game-wait["begin_game_ms"]<=wait["wait_bound_ms"] and
                    stamp-wait["begin_wall_monotonic_ns"]<=wait["wait_bound_ms"]*1_000_000 and
                    actual.get("wall_monotonic_ns",stamp+1)<=stamp,"native guard classification clock/watchdog exceeded")
            previous=stamp
            actors=[actor for actor in actual.get("body_control",{}).get("actors",[]) if actor.get("entity_id")==wait["entity_id"]]
            if event["phase"]=="recovered":
                rows=[row for row in actual.get("observations",[]) if row.get("entity_id")==wait["entity_id"] and row.get("observation_seq")==event.get("observation_seq")]
                require(len(rows)==len(actors)==1 and rows[0].get("periodic") is True and
                        rows[0]["observation_seq"]>wait["native_current_observation_seq"] and rows[0]["t_game_ms"]>wait["begin_game_ms"] and
                        all(rows[0].get(key)==value for key,value in identity.items()) and all(actors[0].get(key)==value for key,value in identity.items()) and
                        rows[0].get("control_allowed") is True and rows[0].get("action_mask_valid") is True and
                        all(rows[0]["target"].get(key) is True for key in ("visible","present","current_valid")) and
                        rows[0]["target"].get("entity_id")==wait["target_entity_id"] and rows[0]["goal"].get("version")==wait["goal_version"] and
                        actors[0].get("owner")=="scripted" and actors[0].get("release_pending") is False and actors[0].get("control_allowed") is True and
                        all(actors[0].get(key)==wait[key] for key in ("provider","goal_version","target_entity_id")),"native guard recovered without fresh same-lease visible periodic/current control")
            elif event["phase"]=="native_handoff":
                require(len(actors)==1 and actors[0].get("owner")=="native" and actors[0].get("control_generation")==0 and event.get("native_actor")==actors[0] and
                        any(row.get("phase")=="ownership" and row.get("reason")=="native_restored" and all(row.get(key)==value for key,value in identity.items()) and
                            row.get("wall_monotonic_ns",stamp+1)<=actual["wall_monotonic_ns"] for row in controls) and
                        any(row.get("kind")=="native_guard_handoff" and row.get("entity_id")==wait["entity_id"] and row.get("native_response_id")==actual["id"] for row in record.get("fallback_events",[])),
                        "native guard handoff lacks actual native restoration/fallback loss")
            else:require(actual.get("arena")==record["final"] and (record["final"].get("terminated") or record["final"].get("truncated")),
                         "native guard terminal is not the authoritative real final")
        for entry in journal:
            if entry.get("kind")=="model_input":
                begin=entry["timing"]["process_begin_wall_monotonic_ns"]
                if wait["begin_wall_monotonic_ns"]<=begin<end["wall_monotonic_ns"]:
                    require(entry["request"].get("command_enabled") is False and entry["request"].get("memory_only") is True and
                            entry["response"].get("model_processed") is True and entry["response"].get("action_id") is None,
                            "native guard wait skipped history or proposed a command")
            elif entry.get("kind")=="arbitration":
                begin=entry["timing"]["arbitration_begin_wall_monotonic_ns"]
                require(not wait["begin_wall_monotonic_ns"]<=begin<end["wall_monotonic_ns"],"new command/arbitration during native guard classification")
        summaries.append({"entity_id":wait["entity_id"],"phases":[event["phase"] for event in selected],"wait_bound_ms":wait["wait_bound_ms"],
                          "healthy_refresh_credit":False,"rejected_opportunity_retained":True})
    require(len([event for event in events if event.get("phase")=="begin"])==len(guard_waits),"native guard summary fabricated another wait")
    return {"waits":summaries,"native_grace_changed":False,"periodic_history_skipped":False}


def runtime_evidence(path,record,normalizer,observations,pairs,controls,*,shadow=False,actors=None):
    """Reconstruct recurrent state, receipts and clocks from original wire rows."""
    actors=set(actors or (record["initial"]["primary_entity_id"],))
    journal=bc.canonical.read_jsonl(path/"policy-inputs.jsonl")
    require(digest(path/"policy-inputs.jsonl") == record.get("model_journal_sha256"), "stage8 policy journal hash differs")
    wire,wire_proof=bc.wire_observation_sources(observations,pairs)
    processed,previous,arbitrations,closed,command_keys={}, {}, [],set(),set()
    accepted,rejected,terminal_censored=0,0,0
    refresh_attempts,refresh_sources,masked_refreshes,guard_waits={},{},[],[]
    for entry in journal:
        kind=entry.get("kind")
        if kind=="excluded_observation":
            row=entry.get("observation",{});key=bc.canonical.identity(row)+(row["observation_seq"],)
            require(entry.get("reason")=="outside_policy_generation" and wire.get(key)==row, "excluded policy row differs from actual wire")
            continue
        if kind=="terminal_barrier":
            request,response=entry.get("request",{}),entry.get("response",{})
            entity=request.get("entity_id",record["initial"]["primary_entity_id"])
            require(entity in actors and entity not in closed and request.get("session_epoch")==record["final"]["session_epoch"] and
                    request.get("world_epoch")==record["final"]["world_epoch"] and request.get("final_game_ms")==record["final"]["game_ms"] and
                    response.get("accepted") is True and response.get("model_processed") is False and response.get("hidden_reset") is True and
                    response.get("runtime_after_clear",{}).get("actors")==0 and response["runtime_after_clear"].get("episode_closed") is True and
                    response["runtime_after_clear"].get("expected_epochs") is None, "stage8 terminal actor memory barrier differs")
            before_path=bc.artifact(path,request.get("final_before_clear_file"));before=read(before_path)
            require(digest(before_path)==request.get("final_before_clear_sha256") and before.get("final")==record["final"] and
                    before.get("model_stats")==record.get("model_stats"), "stage8 pre-clear final proof changed")
            state=before.get("runtimes",{}).get(str(entity)) if "runtimes" in before else before.get("runtime_status")
            require(state==response.get("runtime_before_clear"), "stage8 memory cleared before native final sample")
            closed.add(entity);continue
        require(kind in ("model_input","arbitration"), "unknown stage8 neural journal entry")
        request,response=entry.get("request",{}),entry.get("response",{})
        row=request.get("observation") if kind=="model_input" else entry.get("observation")
        require(isinstance(row,dict) and row.get("entity_id") in actors and row["entity_id"] not in closed and row.get("periodic") is True and
                row.get("phase")=="post_simulation_post_animation", "stage8 policy consumed nonperiodic/foreign/closed actor row")
        key=bc.canonical.identity(row)+(row["observation_seq"],);entity=row["entity_id"]
        require(wire.get(key)==row, "stage8 policy input differs from exact received numeric IPC source")
        checkpoint=record["checkpoint_sha256"].get(str(entity)) if isinstance(record.get("checkpoint_sha256"),dict) else record.get("checkpoint_sha256")
        norm_sha=record["normalizer_sha256"].get(str(entity)) if isinstance(record.get("normalizer_sha256"),dict) else record.get("normalizer_sha256")
        require(response.get("checkpoint_sha256")==checkpoint and response.get("normalizer_sha256")==norm_sha and
                response.get("sampling_version")=="periodic-v1", "stage8 weights/normalizer/model sampling changed")
        if kind=="model_input":
            require(key not in processed and response.get("model_processed") is True and response.get("status")=="processed",
                    "stage8 recurrent input duplicated/dropped/fell back")
            old=previous.get(entity);stream=1 if old is None else old["stream_seq"]+1
            dt=row["t_game_ms"]-(record["initial"]["game_ms"] if old is None else old["game_ms"])
            require(request.get("stream_seq")==response.get("stream_seq")==stream and response.get("sequence_dt_game_ms")==dt and
                    response.get("hidden_reset")== (old is None or old["identity"]!=bc.canonical.identity(row)), "stage8 recurrent sequence/dt/reset differs")
            require(old is None or row["t_game_ms"]//50 <= old["game_ms"]//50+1, "stage8 periodic source bucket lost")
            features=bc.canonical.pack_features(row,normalizer,sequence_dt_game_ms=dt)
            require(response.get("features_sha256")==bc.json_digest(features) and response.get("observation_sha256")==bc.json_digest(row) and
                    response.get("effective_action_mask")==bc.effective_mask(row) and response.get("raw_action_mask")==row.get("action_mask",row["availability_mask"]),
                    "stage8 features/head masks differ from causal training view")
            bc.runtime_decision(row,request,response)
            if shadow:require(request.get("command_enabled") is False and request.get("memory_only") is True and response.get("action_id") is None,
                              "shadow policy proposed a physical body command")
            processed[key]=response;previous[entity]={"stream_seq":stream,"game_ms":row["t_game_ms"],"identity":bc.canonical.identity(row)}
            pending=refresh_sources.get(bc.canonical.identity(row))
            if pending is not None and row["observation_seq"]>pending["source_seq"]:pending["fresh_processed"]=True
        else:
            require(not shadow and key in processed and response.get("model_processed") is False and response.get("hidden_reset") is False and
                    response.get("periodic_observation_seq")==row["observation_seq"] and response.get("periodic_game_ms")==row["t_game_ms"] and
                    response.get("features_sha256")==processed[key]["features_sha256"] and response.get("status")=="decision" and
                    response.get("action_id")==processed[key]["action_id"], "stage8 arbitration changed the periodic recurrent decision")
            command,receipt=entry.get("command_request"),entry.get("command_response")
            require(isinstance(command,dict) and isinstance(receipt,dict) and command.get("action_id")==response["action_id"] and
                    command.get("observation_seq")==row["observation_seq"] and command.get("issued_game_ms")==row["t_game_ms"] and
                    all(command.get(field)==row[field] for field in bc.canonical.IDENTITY), "stage8 command does not bind exact periodic decision")
            require(any({k:v for k,v in raw.items() if k!="id"}==command and actual==receipt for raw,actual,_,_ in pairs),
                    "stage8 command/receipt lacks actual raw IPC")
            cmd_key=bc.canonical.identity(command)+(command["command_id"],)
            require(cmd_key not in command_keys,"stage8 policy repeated a command");command_keys.add(cmd_key)
            identity=bc.canonical.identity(row);pending=refresh_sources.pop(identity,None)
            if pending is not None:
                require(pending["fresh_processed"] and row["observation_seq"]>pending["source_seq"],"rejected source was resubmitted before new same-lease periodic inference")
            if receipt.get("accepted") is True:
                require(entry.get("command_id")==command["command_id"] and entry.get("execution_id")==receipt.get("execution_id"),
                        "accepted policy execution identity differs");accepted+=1
                refresh_attempts.pop(identity,None)
            else:
                require(record.get("mode")=="realtime" and receipt.get("accepted") is False and entry.get("command_id") is None,
                        "stage8 rejected race forged physical acceptance")
                if receipt.get("reason")=="arena_terminal":
                    require(entry.get("terminal_censored") is True and receipt.get("arena")==record["final"] and
                            record["final"].get("infrastructure") is False and (record["final"].get("terminated") or record["final"].get("truncated")),
                            "late command censor has no authoritative actual terminal")
                    terminal_censored+=1
                else:
                    if receipt.get("reason")=="masked":masked_refreshes.append(masked_refresh_evidence(entry,row,command,receipt,controls))
                    if entry.get("native_guard_wait_pending") is True:
                        guard_waits.append(target_guard_wait_evidence(entry,row,command,receipt,controls))
                    require(entry.get("terminal_censored") is not True and receipt.get("reason") in
                            ("stale_observation","masked","expired","expired_wall","target_invalid","command_busy","release_pending","not_owned","stale_generation"),
                            "stage8 command rejection is an unexpected engine refusal");rejected+=1
                    if entry.get("stale_refresh_pending") is True:
                        attempt=entry.get("recovery_attempt")
                        require(type(attempt) is int and 1<=attempt<=2 and attempt==refresh_attempts.get(identity,0)+1,
                                "masked/stale recovery exceeds the shared two-attempt budget")
                        refresh_attempts[identity]=attempt;refresh_sources[identity]={"source_seq":row["observation_seq"],"fresh_processed":False}
            arbitrations.append(entry)
    require(closed==actors,"stage8 terminal barriers do not close every actual model actor")
    current={}
    by_command={bc.canonical.identity(entry["command_request"])+(entry["command_request"]["command_id"],):entry for entry in arbitrations}
    for request,response,_,_ in pairs:
        if request.get("cmd")=="body_observations":
            for row in response.get("observations",[]):current[row["entity_id"]]=row
        elif request.get("cmd")=="body_action" and request.get("entity_id") in actors:
            entry=by_command.get(bc.canonical.identity(request)+(request["command_id"],))
            require(entry is not None and current.get(request["entity_id"])==entry["observation"],
                    "stage8 physical command is unjournaled or uses an older/event-only collector row")
    acquisitions={(row["entity_id"],row["control_generation"]) for row in controls if row.get("phase")=="ownership" and row.get("reason")=="acquired"}
    expected={key for key,row in wire.items() if row["entity_id"] in actors and row.get("periodic") is True and
              ((row.get("control_generation")==0) if shadow else (row["entity_id"],row["control_generation"]) in acquisitions)}
    require(set(processed)==expected,"stage8 model skipped/coalesced an actual eligible history input")
    stats=record.get("model_stats",{})
    require(stats.get("model_processed",0)==len(processed) and stats.get("accepted_model_commands",0)==accepted and
            stats.get("rejected_model_commands",0)==rejected and stats.get("terminal_censored_commands",0)==terminal_censored,
            "stage8 model summary differs from actual raw decisions")
    require(stats.get("native_guard_wait_requests",0)==len(guard_waits),"native guard wait request summary differs from actual refusals")
    opportunities=stats.get("latest_eligible_periodic_opportunities",0)
    require(type(opportunities) is int and opportunities==len(arbitrations),"stage8 eligible opportunity denominator differs from journal")
    fraction=accepted/opportunities if opportunities else None
    require(record.get("neural_control_fraction")==fraction,"stage8 healthy-control fraction differs from receipts")
    return {"processed_periodic_inputs":len(processed),"model_actor_ids":sorted(actors),"accepted_commands":accepted,
            "rejected_commands":rejected,"eligible_opportunities":opportunities,"neural_control_fraction":fraction,
            "terminal_censored_commands":terminal_censored,
            "terminal_memory_reset":True,"native_to_received_wire":wire_proof,"masked_refreshes":masked_refreshes,
            "native_guard_waits":target_guard_history(record,journal,pairs,controls,guard_waits)}


def latency_evidence(path,record,observations,pairs):
    transport=bc.canonical.read_jsonl(path/"collector-transport.jsonl")
    require(len(transport)==len(pairs)*2,"collector clock journal dropped/duplicated RPC entries")
    native,received,requests={}, {}, {}
    for (request,response,_,_),pair in zip(pairs,[transport[i:i+2] for i in range(0,len(transport),2)]):
        before,after=pair
        require(before.get("direction")=="request" and after.get("direction")=="response" and
                before.get("id")==request["id"]==after.get("id")==response["id"],"native/collector clock RPC identity differs")
        begin,end=before.get("wall_monotonic_ns"),after.get("wall_monotonic_ns")
        emit=response.get("wall_monotonic_ns")
        require(all(type(value) is int and value>0 for value in (begin,end,emit)) and begin<=emit<=end and
                response.get("clock_domain")=="linux-clock-monotonic" and after.get("native_wall_monotonic_ns")==emit,
                "native steady clock is not bracketed by actual Linux collector request/response")
        requests[request["id"]]=begin
        if request.get("cmd")=="body_observations":
            for row in response.get("observations",[]):
                key=bc.canonical.identity(row)+(row["observation_seq"],)
                require(row.get("clock_domain")=="linux-clock-monotonic" and type(row.get("snapshot_wall_monotonic_ns")) is int and
                        0<row["snapshot_wall_monotonic_ns"]<=emit,"native numeric snapshot clock missing or after emission")
                received[key]=end;native[key]=row["snapshot_wall_monotonic_ns"]
    journal=bc.canonical.read_jsonl(path/"policy-inputs.jsonl");latencies=bc.canonical.read_jsonl(path/"latency.jsonl")
    require(digest(path/"latency.jsonl")==record.get("latency_sha256"),"stage8 latency journal hash differs")
    timed=[entry for entry in journal if entry.get("kind") in ("model_input","arbitration")]
    require(len(timed)==len(latencies),"timing journal omits/duplicates a real model/arbitration call")
    steps,delivery,collection=[],[],[]
    for entry,latency in zip(timed,latencies):
        kind=entry["kind"];row=entry["request"]["observation"] if kind=="model_input" else entry["observation"]
        key=bc.canonical.identity(row)+(row["observation_seq"],);timing=entry.get("timing",{})
        timing_key=tuple(row[field] for field in ("entity_id","session_epoch","world_epoch","control_generation","observation_seq"))
        require(tuple(latency.get("observation_key",()))==timing_key and latency.get("kind")==("model_step" if kind=="model_input" else kind) and
                all(latency.get(field)==value for field,value in timing.items()) and timing.get("collector_received_wall_monotonic_ns")==received.get(key),
                "latency anchor differs from actual received observation/call")
        fields=("process_begin_wall_monotonic_ns","process_end_wall_monotonic_ns") if kind=="model_input" else ("arbitration_begin_wall_monotonic_ns","arbitration_end_wall_monotonic_ns")
        begin,end=(timing.get(field) for field in fields)
        require(type(begin) is int and type(end) is int and received[key]<=begin<=end,"model/arbitration clock precedes receipt or reverses")
        if kind=="model_input":steps.append((end-begin)/1e6)
        else:
            delivery.append((end-native[key])/1e6)
            command=entry.get("command_request")
            if command is not None:
                ids=[raw["id"] for raw,actual,_,_ in pairs if {k:v for k,v in raw.items() if k!="id"}==command and actual==entry.get("command_response")]
                require(len(ids)==1 and timing.get("command_sent_wall_monotonic_ns")==requests[ids[0]] and end<=requests[ids[0]],
                        "command send clock differs from original raw body_action transport")
                collection.append((requests[ids[0]]-received[key])/1e6)
    return {"model_step_ms": {"count":len(steps),"p95":percentile(steps)} if steps else None,
            "observation_to_arbitration_ms":{"count":len(delivery),"p95":percentile(delivery)} if delivery else None,
            "collector_to_command_send_ms":{"count":len(collection),"p95":percentile(collection)} if collection else None,
            "clock_domain":"linux-clock-monotonic","native_reply_bracketed":True}


def combat_comparisons(episodes,config):
    """No successful native continuation is credited to the neural provider."""
    buckets={}
    for row in episodes:
        bucket=buckets.setdefault((row["policy"],row["task"]),Counter())
        bucket["episodes"]+=1;bucket["successes"]+=int(row["credited_success"])
    comparisons,issues={},[]
    for policy in ("gru","mlp"):
        comparisons[policy]={}
        for task in TASKS:
            native,candidate=buckets.get(("native",task),{}),buckets.get((policy,task),{})
            require(native.get("episodes",0)==candidate.get("episodes",0)>0,"combat task native/candidate denominator differs")
            baseline=native["successes"]/native["episodes"];rate=candidate["successes"]/candidate["episodes"]
            delta=rate-baseline;passed=delta+1e-12>=config["quality"]["minimum_success_delta_to_native"]
            comparisons[policy][task]={"episodes":candidate["episodes"],"native_successes":native["successes"],"candidate_successes":candidate["successes"],
                                        "native_success_rate":baseline,"candidate_success_rate":rate,"delta":delta,"passed":passed}
            if policy==config["candidate_policy"] and not passed:issues.append(f"{policy}/{task}: success delta {delta:.6f} below -.15")
    for row in episodes:
        if row["policy"]!=config["candidate_policy"]:continue
        runtime=row.get("runtime") or {};fraction=runtime.get("neural_control_fraction")
        if fraction is None or fraction<config["quality"]["minimum_healthy_neural_control_fraction"]:
            issues.append(row["name"]+": healthy neural control below95percent")
    return {"comparisons":comparisons,"primary_candidate":config["candidate_policy"],"mlp_comparison_only":True},issues


def defense_reactions(path,record,mechanics,controls):
    primary=record["initial"]["primary_entity_id"];opponent=record["initial"]["opponent_entity_id"]
    native_ids={row["observation_entity_id"]:row["actor_id"] for row in mechanics if row.get("phase")=="arena_actor_mapping"}
    require(primary in native_ids and opponent in native_ids,"defense reaction physical actor mapping absent")
    journal=bc.canonical.read_jsonl(path/"policy-inputs.jsonl");proofs=[];censored=Counter()
    for entry in journal:
        if entry.get("kind")!="arbitration" or entry.get("command_response",{}).get("accepted") is not True:continue
        action=entry.get("command_request",{}).get("action_id")
        if action not in (5,9):continue
        row=entry["observation"];target=row.get("target",{})
        if not (target.get("entity_id")==opponent and target.get("current_valid") is True and
                (target.get("attack_anim") is True or target.get("prehit") is True)):
            censored["no_observed_current_windup"]+=1;continue
        request=entry["command_request"];execution=entry["command_response"]["execution_id"]
        starts=[event for event in controls if event.get("phase")=="started" and event.get("entity_id")==primary and
                event.get("command_id")==request["command_id"] and event.get("execution_id")==execution and
                event.get("action_id")==action and all(event.get(field)==request[field] for field in bc.canonical.IDENTITY)]
        if not starts:censored["accepted_but_not_physically_started"]+=1;continue
        require(len(starts)==1,"defense execution starts duplicated")
        started=starts[0]
        hooks=[event for event in mechanics if event.get("phase")=="native_attempt" and event.get("actor_id")==native_ids[primary] and
               event.get("accepted") is True and event.get("pose_changed") is True and event.get("pose_after")==started.get("pose_after") and
               event.get("t_game")==started["t_game_ms"]]
        require(hooks,"defense started has no actual native pose-change hook")
        hook=hooks[-1]
        hits=[event for event in mechanics if event.get("phase")=="hit_attempt" and event.get("actor_id")==native_ids[opponent] and
              event.get("target_id")==native_ids[primary] and event.get("result")=="applied" and event["seq"]>hook["seq"]]
        if not hits:censored["no_subsequent_actual_incoming_attempt"]+=1;continue
        hit=hits[0]
        defenses=[event for event in mechanics if event.get("phase")=="defence_result" and event.get("actor_id")==native_ids[primary] and
                  event.get("target_id")==native_ids[opponent] and event.get("damage_kind")=="melee" and event.get("t_game")==hit["t_game"] and
                  event["seq"]>hit["seq"] and ((action==9 and event.get("blocked") is True) or (action==5 and event.get("jumped_back") is True))]
        if not defenses:censored["actual_attempt_not_protected_by_selected_defense"]+=1;continue
        defense=defenses[0]
        hp_loss=[event for event in mechanics if event.get("phase")=="damage" and event.get("actor_id")==native_ids[primary] and
                 event.get("target_id")==native_ids[opponent] and event.get("t_game")==hit["t_game"] and event.get("hp_after",0)<event.get("hp_before",0)]
        require(not hp_loss and row["t_game_ms"]<=started["t_game_ms"]<=hit["t_game"],"defense reaction missed actual hit deadline or lost HP")
        proofs.append({"observation_seq":row["observation_seq"],"command_id":request["command_id"],"execution_id":execution,"action_id":action,
                       "observed_windup_game_ms":row["t_game_ms"],"actual_started_game_ms":started["t_game_ms"],"incoming_attempt_game_ms":hit["t_game"],
                       "pose_change_seq":hook["seq"],"hit_attempt_seq":hit["seq"],"defence_result_seq":defense["seq"],
                       "reaction_game_ms":started["t_game_ms"]-row["t_game_ms"],"actual_window_game_ms":hit["t_game"]-row["t_game_ms"],"hp_preserved":True})
    return {"linked_reactions":proofs,"censored":dict(censored),"deadline_source":"same actual incoming native hit_attempt; separate journal pose-change order"}


def finite_softlocks(mechanics,controls,final):
    """Only a finite, unchanged protected native segment can prove a softlock."""
    mapping={row["observation_entity_id"]:row["actor_id"] for row in mechanics if row.get("phase")=="arena_actor_mapping"}
    violations=[];inspected=0;unbounded=0
    for start in controls:
        if start.get("phase")!="started":continue
        action=start.get("action_id")
        if action in (1,2):unbounded+=1;continue
        if action not in range(3,12):continue
        native=mapping.get(start.get("entity_id"));require(native is not None,"softlock execution lacks native actor mapping")
        end=min([event["t_game_ms"] for event in controls if event.get("entity_id")==start["entity_id"] and
                 ((event.get("command_id")==start["command_id"] and event.get("execution_id")==start["execution_id"] and
                   event.get("phase") in ("completed","cancelled","rejected")) or
                  (event.get("phase")=="ownership" and event.get("reason")=="native_restored")) and event["t_game_ms"]>=start["t_game_ms"]]
                or [final["game_ms"]])
        frames=[row for row in mechanics if row.get("phase")=="phase_snapshot" and row.get("actor_id")==native and
                start["t_game_ms"]<row.get("t_game",-1)<end]
        if not frames:continue
        first=frames[0];layers=first.get("layers",[])
        signature=[tuple(layer.get(key) for key in ("name","start","combination","speed")) for layer in layers]
        waits=tuple(first.get(key,0) for key in ("fai_wait_until","ani_wait_until","wait_until","output_wait_until"))
        deadlines=[float(value) for value in waits if type(value) is int and value>=0]
        finite=[]
        for layer in layers:
            if not str(layer.get("name","")).startswith("T_"):continue  # Native standing/run cycles have no finite completion contract.
            duration,speed=layer.get("duration_ms"),layer.get("speed")
            require(type(duration) in (int,float) and math.isfinite(duration) and duration>=0 and
                    type(speed) in (int,float) and math.isfinite(speed) and speed>0,"protected native layer timing malformed")
            ends=layer.get("hit_end_ms",[]);combo=layer.get("combination",0)
            cutoff=duration
            if type(combo) is int and 0<=combo<len(ends) and type(ends[combo]) in (int,float) and ends[combo]>=0:cutoff=min(cutoff,ends[combo])
            if cutoff>0:
                elapsed=layer["clock"]-layer["start"]
                finite.append(first["t_game"]+max(0.,cutoff-elapsed)/speed)
        if not finite and action not in (10,11):continue
        if action in (10,11) and not first.get("fai_wait_until",0)>start["t_game_ms"]:continue
        deadlines.extend(finite);deadline=max(deadlines or [first["t_game"]]);inspected+=1
        previous=first
        for frame in frames[1:]:
            current=[tuple(layer.get(key) for key in ("name","start","combination","speed")) for layer in frame.get("layers",[])]
            current_waits=tuple(frame.get(key,0) for key in ("fai_wait_until","ani_wait_until","wait_until","output_wait_until"))
            if current!=signature or current_waits!=waits:break  # Genuine renewed HIT/pose/wait is a new native boundary.
            progressed=any(frame.get(axis)!=previous.get(axis) for axis in ("x","y","z","yaw","hp"))
            if frame["t_game"]>deadline+67 and not progressed and frame["t_game"]<final["game_ms"]:
                violations.append({"entity_id":start["entity_id"],"command_id":start["command_id"],"execution_id":start["execution_id"],
                                   "native_deadline_game_ms":deadline,"still_locked_game_ms":frame["t_game"],"layer_signature":signature})
                break
            previous=frame
    return {"inspected_finite_segments":inspected,"unbounded_movement_segments_excluded":unbounded,"violations":violations,
            "allowance_game_ms":67,"terminal_or_fresh_hit_not_counted":True}


def capture_integrity(record,mechanics,pairs):
    final=record.get("perception_evidence",{}).get("final_native_status")
    require(isinstance(final,dict) and any(request.get("cmd")=="body_control_status" and response==final for request,response,_,_ in pairs) and
            final.get("arena")==record["final"] and final.get("infrastructure_failure") is False,
            "final native telemetry integrity has no authoritative frozen-final raw IPC")
    status=final.get("visibility_diagnostics",{})
    rows=[row for row in mechanics if row.get("phase")=="visibility_diagnostic"]
    require(status.get("enabled") is True and status.get("capture_failed") is False and status.get("dropped_total")==0 and
            status.get("records_total")==len(rows) and status.get("clock_domain")=="linux-clock-monotonic" and
            all(row.get("diagnostic_generation")==status.get("diagnostic_generation") for row in rows),
            "native BIP/LOS diagnostic dropped/failed/truncated or changed generation")
    for request,response,_,_ in pairs:
        if request.get("cmd")=="body_observations":
            require(response.get("dropped_total")==response.get("event_dropped_total")==0,"numeric observer lost a row/event")
        elif request.get("cmd")=="body_events":require(response.get("dropped_total")==0,"native controller event queue dropped a lifecycle record")
    return {"final_integrity_sha256":bc.json_digest(final),"visibility_records":len(rows),"dropped":0,"capture_failed":False}


def post_handoff_targets(rows,initial,controls,observations):
    """Bind real native retargeting without treating it as paired NPC evidence."""
    entities = {actor["entity_id"] for actor in initial["actors"]}
    mappings = {row["observation_entity_id"]:row["actor_id"] for row in rows
                if row.get("phase") == "arena_actor_mapping"}
    foreign = {}
    for row in rows:
        if row.get("phase") != "visibility_diagnostic" or row.get("target_entity_id") in entities:
            continue
        require(row.get("entity_id") in entities and row.get("session_epoch") == initial["session_epoch"] and
                row.get("world_epoch") == initial["world_epoch"] and row.get("owner") == "native" and
                row.get("control_generation") == 0, "foreign visibility target is not a current post-handoff native actor")
        key = (row["entity_id"],row["target_entity_id"])
        if key in foreign:
            require(row.get("actor_id") == foreign[key]["native_actor_id"] and
                    row.get("target_id") == foreign[key]["native_target_id"] and
                    row.get("target") == foreign[key]["target_symbol"] and row["t_game"] >= foreign[key]["first_game_ms"],
                    "post-handoff native target mapping changed")
            foreign[key]["rows"] += 1
            continue
        require(mappings.get(row["entity_id"]) == row.get("actor_id") and
                type(row.get("target_id")) is int and row["target_id"] not in mappings.values() and
                type(row.get("target")) is str and row["target"], "foreign target lacks actual native actor mapping")
        restored = [event for event in controls if event.get("phase") == "ownership" and event.get("reason") == "native_restored" and
                    all(event.get(field) == row.get(field) for field in ("entity_id","session_epoch","world_epoch")) and
                    event.get("control_generation",0) > 0 and event.get("t_game_ms",-1) <= row["t_game"] and
                    0 < event.get("wall_monotonic_ns",0) < row["snapshot_wall_monotonic_ns"]]
        require(restored, "foreign visibility target precedes proved native restoration")
        restore = restored[-1]
        perceived = [event for event in rows if event.get("phase") == "native_perception" and
                     event.get("actor_id") == row["actor_id"] and event.get("target_id") == row["target_id"] and
                     event.get("target") == row["target"] and event.get("perception_kind") == "dispatch" and
                     event.get("native_callback_dispatched") is True and event.get("result") is True and
                     restore["t_game_ms"] <= event.get("t_game",-1) <= row["t_game"] and event.get("seq",0) < row["seq"]]
        require(perceived, "foreign visibility target lacks causal native perception dispatch")
        target_rows = [obs for obs in observations if all(obs.get(field) == row.get(field) for field in ("entity_id","session_epoch","world_epoch")) and
                       obs.get("t_game_ms") == row["t_game"] and obs.get("control_generation") == 0 and
                       obs.get("target",{}).get("entity_id") == row["target_entity_id"] and
                       obs["target"].get("present") is True and obs["target"].get("current_valid") is True]
        require(target_rows, "foreign visibility target lacks same-frame actual numeric target")
        foreign[key] = {"entity_id":row["entity_id"],"target_entity_id":row["target_entity_id"],
                        "native_actor_id":row["actor_id"],"native_target_id":row["target_id"],"target_symbol":row["target"],
                        "native_restore_event_seq":restore["event_seq"],"native_perception_seq":perceived[-1]["seq"],
                        "first_observation_seq":target_rows[0]["observation_seq"],"first_diagnostic_seq":row["seq"],
                        "first_game_ms":row["t_game"],"rows":1,"paired_causal_credit":False,"quality_credit":False}
    return foreign


def owned_housekeeping(rows,controls):
    """Audit the native wait branch around one owned executor invocation."""
    pending,counts,pairs = {},Counter(),[]
    for row in rows:
        source = row.get("source_phase","")
        if row.get("phase") != "visibility_diagnostic" or not source.startswith("owned_consumed_"):
            continue
        branch,edge = source.removeprefix("owned_consumed_").rsplit("_",1)
        require(branch in HOUSEKEEPING_BRANCHES and edge in ("before","after") and row.get("forced_edge") is True,
                "owned housekeeping source/edge differs")
        require(row.get("owner") == "scripted" and all(type(row.get(field)) is int and row[field] > 0
                for field in ("control_generation","command_id","execution_id")), "owned housekeeping lost active command identity")
        for flag,remaining in (("native_fai_wait","native_fai_wait_remaining_ms"),("native_script_wait","native_script_wait_remaining_ms")):
            require(type(row.get(flag)) is bool and type(row.get(remaining)) is int and row[remaining] >= 0 and
                    (row[remaining] == 0 or row[flag]), "native wait flag/remaining time differs")
        require(type(row.get("body_state_base")) is int, "owned housekeeping body state missing")
        key = tuple(row[field] for field in ("entity_id","session_epoch","world_epoch","control_generation","command_id","execution_id","sim_step","t_game"))
        if edge == "before":
            require(key not in pending, "duplicate owned housekeeping entry")
            hit = row["body_state_base"] == 32794  # Native BS_HIT includes its interruptible flag.
            waits = row["native_fai_wait"] or row["native_script_wait"]
            require(branch == ("hit_lock" if hit else "fai_or_script_wait" if waits else "wait_move"),
                    "owned executor branch contradicts actual native wait/HIT gate")
            pending[key] = (branch,row)
            continue
        require(key in pending, "owned housekeeping exit lacks same-step command entry")
        actual_branch,before = pending.pop(key)
        require(branch == actual_branch and before["seq"] < row["seq"] and
                before["snapshot_wall_monotonic_ns"] <= row["snapshot_wall_monotonic_ns"] and
                before.get("actor_id") == row.get("actor_id") and before.get("target_entity_id") == row.get("target_entity_id"),
                "owned housekeeping boundary identity/order differs")
        require(not any(event.get("phase") in ("native_choice","native_consume") and event.get("actor_id") == row.get("actor_id") and
                        before["seq"] < event.get("seq",0) < row["seq"] for event in rows),
                "owned wait housekeeping called a native chooser/consume")
        require(not any(event.get("phase") in ("accepted","started") and event.get("entity_id") == row["entity_id"] and
                        before["snapshot_wall_monotonic_ns"] < event.get("wall_monotonic_ns",0) <= row["snapshot_wall_monotonic_ns"] for event in controls),
                "owned wait housekeeping submitted a new policy command")
        counts[branch] += 1
        pairs.append({"entity_id":row["entity_id"],"command_id":row["command_id"],"execution_id":row["execution_id"],
                      "game_ms":row["t_game"],"branch":branch,"before_seq":before["seq"],"after_seq":row["seq"],
                      "body_yaw_delta_deg":(row["body_yaw_deg"]-before["body_yaw_deg"]+180)%360-180,
                      "native_choice_or_new_command":False})
    require(not pending, "owned housekeeping native exit missing")
    return {"pairs":pairs,"branch_counts":dict(counts),"native_choice_or_new_command":False}


def visibility_diagnostics(rows,initial,controls=(),observations=()):
    selected = [row for row in rows if row.get("phase") == "visibility_diagnostic"]
    require(selected, "native BIP/LOS diagnostic evidence missing")
    previous,causes,paired_causes,native_causes = {},Counter(),Counter(),Counter()
    entities = {actor["entity_id"] for actor in initial["actors"]}
    foreign = post_handoff_targets(rows,initial,controls,observations)
    for row in selected:
        require(row.get("diagnostic_schema_version") == 1 and row.get("oracle_debug_only") is True and
                row.get("clock_domain") == "linux-clock-monotonic" and row.get("source_phase") in SOURCES,
                "visibility diagnostic schema/clock/source differs")
        require(row.get("session_epoch") == initial["session_epoch"] and row.get("world_epoch") == initial["world_epoch"] and
                row.get("entity_id") in entities and (row.get("target_entity_id") in entities or (row["entity_id"],row.get("target_entity_id")) in foreign) and
                row["entity_id"] != row["target_entity_id"],
                "visibility diagnostic actor/target/world differs")
        key = (row["entity_id"],row["diagnostic_generation"])
        seq = row.get("diagnostic_seq")
        require(type(seq) is int and seq == previous.get(key,0)+1, "visibility diagnostic sequence lost or duplicated")
        previous[key] = seq
        require(type(row.get("snapshot_wall_monotonic_ns")) is int and row["snapshot_wall_monotonic_ns"] > 0 and
                row["wall_monotonic_ns"] == row["snapshot_wall_monotonic_ns"], "visibility native timestamp missing")
        require(all(type(row.get(field)) in (int,float) and math.isfinite(row[field]) for field in ("body_yaw_deg","pose_view_deg")),
                "visibility body/BIP yaw nonfinite or missing")
        center,head = row["center_ray"],row["head_ray"]
        require(type(row.get("target_has_head")) is bool and ((head is not None) == row["target_has_head"]), "visibility head schema differs")
        for ray in (center,*([head] if head is not None else [])):
            require(all(type(ray.get(flag)) is bool for flag in ("range_pass","fov_pass","body_fov_pass","ray_hit","hit_vob","query_pass")), "visibility ray flags malformed")
            require(ray["query_pass"] == (ray["range_pass"] and ray["fov_pass"] and not ray["ray_hit"]), "visibility ray result contradicts range/cone/collision")
            require(all(type(ray.get(field)) in (int,float) and math.isfinite(ray[field]) for field in
                ("distance_cm","legacy_angle_deg","cone_cos","body_cone_cos","hit_fraction")), "nonfinite visibility ray")
            require(0 <= ray["hit_fraction"] <= 1 and ray["distance_cm"] >= 0, "visibility collision geometry invalid")
        actual = center["query_pass"] or (head is not None and head["query_pass"])
        require(type(row.get("actual_can_see")) is bool and row["actual_can_see"] == bool(actual) and row.get("query_matches_diagnostic") is True,
                "mirror diagnostic differs from unchanged actual native vision")
        require(row.get("controller_visible") == (row.get("senses_see") is True and actual and row.get("target_terminal") is False),
                "controller visibility differs from native vision/senses/terminal")
        reason = "visible" if actual else "out_of_senses_range" if not center["range_pass"] and (head is None or not head["range_pass"]) else \
                 "outside_pose_cone" if not center["fov_pass"] and (head is None or not head["fov_pass"]) else \
                 "static_occlusion" if center["ray_hit"] and (head is None or head["ray_hit"]) else \
                 "target_head_missing" if head is None else "mixed_ray_rejection"
        require(row.get("reason") == reason, "visibility failure reason contradicts actual ray/cone proof")
        causes[reason] += 1
        (paired_causes if row["target_entity_id"] in entities else native_causes)[reason] += 1
    return {"rows":len(selected),"actors":len(previous),"causes":dict(causes),"native_query_matches":True,
            "paired_rows":sum(paired_causes.values()),"paired_causes":dict(paired_causes),
            "post_handoff_retarget_rows":sum(native_causes.values()),"post_handoff_retarget_causes":dict(native_causes),
            "post_handoff_targets":list(foreign.values()),"owned_housekeeping":owned_housekeeping(rows,controls)}


def no_oracle_features(value):
    if isinstance(value,dict):
        require(not ORACLE_KEYS.intersection(value), "oracle visibility diagnostics entered numerical model input")
        for child in value.values():no_oracle_features(child)
    elif isinstance(value,list):
        for child in value:no_oracle_features(child)


def frozen_sources(root):
    environment = read(root/"environment.json")
    files = environment.get("frozen_files",{})
    require(files, "stage8 executable inventory missing")
    checked,ignored = bc.verify_executable_inventory(root,files)
    for module in (Path(__file__).resolve(),Path(bc.__file__).resolve(),Path(bc.canonical.__file__).resolve()):
        require(module.is_relative_to(root.resolve()) and digest(module) == files.get(str(module.relative_to(root.resolve()))),
                "stage8 reporter/helper is not frozen run code")
    require(digest(root/"source.tar.gz") == environment.get("source_archive_sha256") and
            digest(root/"reference/gothic-body-runner") == environment.get("binary_sha256"), "stage8 archive/binary changed")
    return {"files":len(checked),"ignored_cache_files":ignored,"binary_sha256":environment["binary_sha256"],
            "source_archive_sha256":environment["source_archive_sha256"]}



def selection_dataset(root,config,reference):
    """The merged corpus stays intact; only fresh eval groups select weights."""
    parent=read(root/"dataset-parent/dataset.json");child=read(root/"dataset/dataset.json")
    profile=read(root/"dataset/selection-profile.json")
    fresh={entry["group"] for entry in parent["episodes"] if entry["split"]=="eval" and json.loads(entry["group"])[1] in config["new_data"]["eval_seeds"]}
    require({json.loads(group)[1] for group in fresh}==set(config["new_data"]["eval_seeds"]),"fresh eval group is absent from the merged corpus")
    expected=[entry for entry in parent["episodes"] if entry["split"]!="eval" or entry["group"] in fresh]
    eval_ids=[entry["episode_id"] for entry in expected if entry["split"]=="eval"]
    excluded=[entry["episode_id"] for entry in parent["episodes"] if entry["split"]=="eval" and entry["group"] not in fresh]
    require(child["episodes"]==expected and child["groups"]==parent["groups"],"selection child changed train/test/OOD or retained legacy eval episodes")
    require(digest(root/"dataset/normalization.json")==digest(root/"dataset-parent/normalization.json"),"selection view changed train normalization bytes")
    require(profile.get("selection_split")=="eval" and profile.get("selection_view")=="full_episode" and
            profile.get("selection_evaluation_mode",profile.get("evaluation_mode"))=="continuous" and
            profile.get("selection_evaluation_episode_ids")==eval_ids and set(profile.get("selection_evaluation_groups",[]))==fresh and
            profile.get("excluded_legacy_eval_episode_ids")==excluded and profile.get("dataset_parent_sha256")==digest(root/"dataset-parent/dataset.json") and
            profile.get("stage8_config_sha256")==digest(root/"code/evals/npc-body/body-stage8-v1.json"),"selection profile differs from fresh full actor streams")
    adapter=copy.deepcopy(read(reference/"code/evals/npc-body/body-bc-v1.json"))
    adapter["training"].update(config["training"])
    adapter["dataset"].update({key:config["dataset"][key] for key in ("feature_version","action_version","included_actions","max_label_age_game_ms")})
    adapter["dataset"]["minimum_true_expert_labels_per_included_class"]=config["dataset"]["minimum_train_labels_per_action"]
    adapter["verification"].update(config["verification"]);adapter["overfit"].update(config["overfit"])
    evidence,data=bc.dataset_evidence(root,adapter)
    require(evidence["effective_coverage_ready"] is True,"merged train corpus lacks all eleven genuine periodic classes")
    require(all(data["index"]["groups"].get(group)==split for group,split in read(reference/"dataset/dataset.json")["groups"].items()),
            "stage8 changed immutable earlier split assignments")
    require(all(data["index"]["groups"].get(group)==split for group,split in config["groups"].items()),"stage8 new groups differ from preregistration")
    new_counts=Counter()
    for entry in child["episodes"]:
        if entry["split"]!="train" or json.loads(entry["group"])[1] not in config["new_data"]["train_seeds"]:continue
        episode_path=Path(entry["path"]);manifest=read(episode_path/entry["manifest_file"])
        require(manifest.get("teacher_provenance",{}).get("teacher_id")==config["new_data"]["teacher_id"],"new train labels came from an undeclared teacher")
        primary=manifest["initial"]["primary_entity_id"]
        true_labels={str(label["decision_id"]):label for key,label in data["labels"].items() if key[0]==entry["episode_id"] and key[1][-1]==primary}
        with (episode_path/entry["records_file"]).open() as stream:
            for line in stream:
                decision=json.loads(line)
                if decision.get("kind")=="decision" and decision.get("entity_id")==primary and decision.get("source")=="scripted" and str(decision.get("decision_id")) in true_labels:
                    label=true_labels[str(decision["decision_id"])]
                    require(decision.get("expert") is True and decision.get("accepted") is True and decision["action_id"]==label["action_id"],"new curriculum counted a nonexpert/nonaccepted label")
                    new_counts[label["action_id"]]+=1
    for action,minimum in config["dataset"].get("minimum_new_teacher_train_labels_per_action",{}).items():
        require(new_counts[int(action)]>=minimum,"new safe maneuver curriculum lacks its declared true train labels")
    require(profile.get("new_teacher_train_labels_by_action")=={str(action):new_counts[action] for action in range(1,12)},"new curriculum count differs from primary scripted periodic labels")
    evidence.update(parent_sha256=digest(root/"dataset-parent/dataset.json"),selection_profile_sha256=digest(root/"dataset/selection-profile.json"),
                    selection_evaluation_groups=sorted(fresh),selection_evaluation_episode_ids=eval_ids,excluded_legacy_eval_episode_ids=excluded,
                    new_train_labels_by_action={str(action):new_counts[action] for action in range(1,12)})
    return evidence,data,adapter



def demonstration_cases(config):
    cases=[];micro=config["microcurriculum"];clearances=micro["wall_clearance_by_seed_cm"]
    require(micro.get("apply_clearance_to_full_bouts") is False,"wall clearance variations must be confined to declared maneuver microcases")
    for split in ("train","eval","ood"):
        for index,seed in enumerate(config[split+"_seeds"]):
            distance=config["ood_distances_cm"][index] if split=="ood" else config["distances_cm"][index%len(config["distances_cm"])]
            for geometry in ([config["ood_geometry"]] if split=="ood" else config["geometries"]):
                for side in config["sides"]:
                    case={"name":f"combat-{split}-{seed}-{geometry}-{side}","seed":seed,"split":split,"side":side,"geometry":geometry,
                          "distance_cm":distance,"yaw_offset_deg":(config["ood_yaw_offsets_deg"] if split=="ood" else config["yaw_offsets_deg"])[side],
                          "center_offset_cm":[0,0],"separation_yaw_deg":0,"task":"native_duel","timeout_ms":config["timeout_game_ms"]}
                    cases.append(case)
    if micro["enabled"]:
        for seed in config["train_seeds"]:
            for variant in micro["variants"]:
                cases.append({"name":f"maneuver-train-{seed}-{variant['id']}","seed":seed,"split":"train","side":variant["side"],"geometry":micro["geometry"],
                              "distance_cm":variant["distance_cm"],"yaw_offset_deg":variant["yaw_offset_deg"],"center_offset_cm":[0,0],"separation_yaw_deg":0,
                              "task":micro["scenario"],"timeout_ms":micro["timeout_game_ms"],"curriculum":"safe_native_arc_then_settle","variant_id":variant["id"],
                              "expected_action_id":variant["expected_action_id"],"calibration_case":variant["calibration_case"],"wall_clearance_cm":clearances[str(seed)],"opponent":micro["opponent"],
                              **{key:variant[key] for key in ("wall_tangent_sign",) if key in variant}})
    return cases



def normal_clearance_geometry(geometry,requested):
    def vector(key):
        value=geometry.get(key)
        require(isinstance(value,list) and len(value)==3 and all(type(item) in (int,float) and math.isfinite(item) for item in value),"clearance geometry vector missing/nonfinite: "+key)
        return value
    def dot(a,b):return sum(x*y for x,y in zip(a,b))
    def unit(key):
        value=vector(key)
        require(abs(value[1])<=1e-6 and abs(dot(value,value)-1)<=1e-4,"clearance geometry normal/tangent is not unit horizontal: "+key)
        return value
    outward=unit("placement_wall_normal_xz_world");normal=unit("actual_wall_normal_xz_world");tangent=unit("placement_wall_tangent_world")
    toward=unit("wall_direction_world");search=unit("search_ray_direction_world")
    center,point,anchor=(vector(key) for key in ("primary_collision_center_cm","wall_point_cm","placement_wall_anchor_cm"))
    raw=vector("wall_normal");raw_length=math.hypot(raw[0],raw[2]);orientation=-1 if dot(raw,toward)>0 else 1
    require(raw_length>=1e-6 and abs(raw[1])<=.35 and
            all(abs(normal[index]-orientation*raw[index]/raw_length)<=1e-5 for index in (0,2)),"reported actual normal does not derive from raw collision normal")
    measured=geometry["actual_wall_normal_clearance_cm"];error=geometry["wall_normal_clearance_error_cm"]
    require(geometry.get("placement_basis")=="wall_normal_xz" and geometry.get("requested_wall_normal_clearance_cm")==requested and
            all(type(value) in (int,float) and math.isfinite(value) for value in (requested,measured,error)) and
            60<=requested<=180 and 60<=measured<=180 and abs(measured-requested)<=20 and
            geometry.get("wall_normal_clearance_tolerance_cm")==20 and abs(error-(measured-requested))<=.001 and
            abs(dot([a-b for a,b in zip(center,point)],normal)-measured)<=.01 and
            dot(outward,normal)>=.95-1e-6 and abs(dot(outward,tangent))<=1e-4 and
            all(abs(a+b)<=1e-6 for a,b in zip(outward,toward)) and dot(outward,search)<0,
            "optional clearance lacks actual outward-normal placement/projection proof")
    ray=geometry["actual_wall_clearance_cm"]
    require(type(ray) in (int,float) and math.isfinite(ray) and 60<=ray<=180 and abs(ray-requested)<=20 and
            geometry.get("requested_wall_clearance_cm")==requested and geometry.get("wall_clearance_tolerance_cm")==20 and
            abs(geometry["wall_clearance_error_cm"]-(ray-requested))<=.001 and abs(geometry["obstacle_distance_cm"]-ray)<=.001,
            "normal clearance replaced or weakened original physical ray guard")
    return {"normal_cm":measured,"ray_cm":ray,"independent_projection_cm":dot([a-b for a,b in zip(center,point)],normal)}


def numeric_normal_clearance(geometry,row,ray,reported_error):
    bearing=math.atan2(geometry["wall_direction_world"][2],geometry["wall_direction_world"][0])-row["self"]["yaw_rad"]
    error=abs((ray["angle_rad"]-bearing+math.pi)%(2*math.pi)-math.pi)
    angle=row["self"]["yaw_rad"]+ray["angle_rad"];direction=[math.cos(angle),0,math.sin(angle)]
    projection=-sum(value*normal for value,normal in zip(direction,geometry["actual_wall_normal_xz_world"]))
    measured=ray["distance_cm"]*projection
    require(ray.get("hit") is True and all(type(value) in (int,float) and math.isfinite(value) for value in (ray["distance_cm"],reported_error,error,measured)) and
            0<projection<=1.0001 and error<=math.pi/16+1e-6 and abs(error-reported_error)<=1e-9 and
            abs(measured-geometry["actual_wall_normal_clearance_cm"])<=2,
            "numeric obstacle ray lacks independently projected normal clearance")
    require(ray==min(row["local_obstacles"]["rays"],key=lambda candidate:abs((candidate["angle_rad"]-bearing+math.pi)%(2*math.pi)-math.pi)),
            "numeric clearance used a different ray than nearest actual wall direction")
    return {"normal_cm":measured,"ray_cm":ray["distance_cm"],"angle_error_rad":error}



def tangent_placement_proof(initial,case):
    setup=initial["initial_state"].get("setup",{});geometry=setup.get("geometry_proof",{})
    if "wall_tangent_sign" not in case:
        require("wall_tangent_sign" not in setup and not {"requested_wall_tangent_sign","placement_tangent_sign"}.intersection(geometry),
                "unrequested tangent sign changed legacy candidate/proof semantics")
        return None
    requested=case["wall_tangent_sign"]
    require(type(requested) is int and requested in (-1,1) and case.get("geometry")=="near_wall" and "wall_clearance_cm" in case and
            type(setup.get("wall_tangent_sign")) is int and setup["wall_tangent_sign"]==requested and
            type(geometry.get("requested_wall_tangent_sign")) is int and geometry["requested_wall_tangent_sign"]==requested and
            type(geometry.get("placement_tangent_sign")) is int and geometry["placement_tangent_sign"]==requested,
            "optional tangent placement lacks exact requested/actual integer sign proof")
    side=initial["side"];require(type(side) is int and side in (0,1) and side==case["side"],"tangent pair uses another physical side")
    native_actors=initial["initial_state"]["actors"]
    require(native_actors==initial["actors"],"tangent placement initial actor snapshots differ")
    roles={actor["entity_id"]:actor for actor in native_actors}
    require(len(roles)==len(native_actors)==2 and initial["primary_entity_id"]!=initial["opponent_entity_id"],"tangent proof does not describe two distinct actual roles")
    primary_actor,opponent_actor=(roles[initial[key]] for key in ("primary_entity_id","opponent_entity_id"))
    require(primary_actor.get("role")=="primary" and opponent_actor.get("role")=="opponent" and
            primary_actor.get("physical_side")==side and opponent_actor.get("physical_side")==1-side,"tangent pair logical roles or physical sides differ")
    primary,opponent=primary_actor["pos"],opponent_actor["pos"]
    tangent,normal=(geometry[key] for key in ("placement_wall_tangent_world","placement_wall_normal_xz_world"))
    require(all(isinstance(vector,list) and len(vector)==3 and all(type(value) in (int,float) and math.isfinite(value) for value in vector)
                for vector in (primary,opponent,tangent,normal)),"actual tangent pair displacement missing/nonfinite")
    delta=[b-a for a,b in zip(primary,opponent)]
    projected=sum(a*b for a,b in zip(delta,tangent));perpendicular=sum(a*b for a,b in zip(delta,normal))
    expected=requested*case["distance_cm"]*(1 if side==0 else -1)
    require(abs(sum(value*value for value in tangent)-1)<=1e-4 and abs(tangent[1])<=1e-6 and
            abs(projected-expected)<=.05 and abs(perpendicular)<=.05,"requested sign does not match actual primary/opponent relative wall-tangent placement")
    return {"requested_tangent_sign":requested,"actual_tangent_sign":geometry["placement_tangent_sign"],
            "primary_relative_tangent_sign":requested*(1 if side==0 else -1),"pair_tangent_projection_cm":projected,
            "expected_pair_tangent_projection_cm":expected,"pair_normal_projection_cm":perpendicular,"pair_projection_tolerance_cm":.05}


def clearance_admission(initial,case,evidence):
    tangent_proof=tangent_placement_proof(initial,case)
    if "wall_clearance_cm" not in case:
        setup=initial["initial_state"].get("setup",{})
        require(evidence is None and "wall_clearance_cm" not in setup and
                "requested_wall_clearance_cm" not in setup.get("geometry_proof",{}),"unrequested clearance changed legacy full-bout setup or admission");return
    setup=initial["initial_state"]["setup"];geometry=setup["geometry_proof"];requested=case["wall_clearance_cm"]
    normal_clearance_geometry(geometry,requested)
    actual,error,tolerance=(geometry[key] for key in ("actual_wall_clearance_cm","wall_clearance_error_cm","wall_clearance_tolerance_cm"))
    require(case["geometry"]=="near_wall" and setup.get("wall_clearance_cm")==requested and geometry.get("requested_wall_clearance_cm")==requested and
            all(type(value) in (int,float) and math.isfinite(value) for value in (requested,actual,error,tolerance)) and
            60<=requested<=180 and 60<=actual<=180 and tolerance==20 and abs(error)<=20 and abs(error-(actual-requested))<=.001 and
            abs(geometry["safe_height_delta_cm"])<=50 and abs(geometry["placement_height_delta_cm"])<=50 and
            all(geometry.get(flag) is True for flag in ("wall_ray_hit","capsule_move_blocked","capsule_block_is_land","initial_capsules_free",
                                                       "safe_outward_move","grounded_placements","target_visible","melee_line_clear")),
            "production clearance lacks real native geometry/visibility/ground proof")
    expected={"requested_cm":requested,"actual_cm":actual,"error_cm":error,"tolerance_cm":tolerance,"geometry_proof":geometry,
              "normal_requested_cm":requested,"normal_actual_cm":geometry["actual_wall_normal_clearance_cm"],
              "normal_error_cm":geometry["wall_normal_clearance_error_cm"],"normal_tolerance_cm":20,"placement_basis":"wall_normal_xz",
              "training_admission":"requires_actual_command_outcome"}
    if tangent_proof is not None:expected.update(tangent_proof)
    if isinstance(evidence,dict) and "numeric_projection" in evidence:expected["numeric_projection"]=evidence["numeric_projection"]
    require(evidence==expected,"production clearance admission differs from actual native proof")



def production_numeric_clearance(path,initial,evidence):
    proof=evidence.get("numeric_projection");require(isinstance(proof,dict),"production normal clearance lacks its before-action numeric ray proof")
    keys=("entity_id","session_epoch","world_epoch","control_generation","observation_seq")
    match=lambda row:all(row.get(key)==proof.get(key) for key in keys)
    native=[];wire=[]
    with (path/"observations.jsonl").open() as stream:
        for line in stream:
            row=json.loads(line)
            if match(row):native.append(row)
    with (path/"ipc.jsonl").open() as stream:
        for line in stream:
            packet=json.loads(line)
            if packet.get("direction")!="response":continue
            for row in packet.get("value",{}).get("observations",[]):
                if match(row):wire.append(row)
    require(len(native)==len(wire)==1,"production numeric normal proof lacks unique actual wire/native observation")
    row=wire[0];bc.native_wire_relation(native[0],row)
    require(row.get("entity_id")==initial["primary_entity_id"] and row.get("session_epoch")==initial["session_epoch"] and row.get("world_epoch")==initial["world_epoch"] and
            row.get("periodic") is True and row.get("phase")=="post_simulation_post_animation" and row.get("t_game_ms")==proof.get("t_game_ms"),"numeric normal clearance came from stale/event-only/other actor input")
    normal=evidence["geometry_proof"]["actual_wall_normal_xz_world"];rays=row["local_obstacles"]["rays"];yaw=row["self"]["yaw_rad"]
    candidates=[]
    for index,ray in enumerate(rays):
        angle=yaw+ray["angle_rad"];direction=[math.cos(angle),0,math.sin(angle)];cosine=-sum(a*b for a,b in zip(direction,normal))
        candidates.append((math.acos(max(-1,min(1,cosine))),index,ray["distance_cm"]*cosine,direction))
    angle,index,projected,direction=min(candidates);ray=rays[index]
    require(len(rays)==16 and ray.get("hit") is True and angle<=math.pi/16+1e-6 and abs(projected-evidence["normal_actual_cm"])<=2,
            "ordinary numeric rays do not confirm separate native normal clearance")
    expected={key:row[key] for key in keys+("t_game_ms",)}
    expected.update(ray_index=index,ray_distance_cm=ray["distance_cm"],ray_world_direction=direction,normal_projection_cm=projected,
                    normal_error_cm=projected-evidence["normal_actual_cm"],angle_to_actual_wall_normal_rad=angle,
                    angle_limit_rad=math.pi/16,projection_tolerance_cm=2)
    require(proof==expected,"saved production numeric normal projection differs from actual raw wire row")
    return expected


def demonstration_matrix(root,config,reference):
    directory=root/"combat-demonstrations";suite=read(directory/"suite.json");manifest=read(directory/"manifest.json")
    collected=read(root/"code/evals/npc-body/body-combat-demonstrations-v1.json");cases=demonstration_cases(collected)
    require([collected["microcurriculum"]["wall_clearance_by_seed_cm"][str(seed)] for seed in config["new_data"]["train_seeds"]]==declared_clearances(config),
            "teacher micro clearance assignments differ from authoritative preregistration")
    require(suite.get("kind")=="body_combat_demonstrations_stage8_suite" and suite.get("status")=="collected" and not suite.get("error") and
            suite.get("suite_scope")=="full" and suite.get("existing_dataset_sha256")==digest(reference/"dataset/dataset.json"),"production collection failed, included calibration or used another parent")
    require(all(collected[split+"_seeds"]==config["new_data"][split+"_seeds"] for split in ("train","eval","ood")) and
            collected["reserved_test_seeds"]==config["new_data"]["test_seeds"] and suite.get("new_group_assignments")==config["groups"],"production groups differ from declared fresh train/eval/OOD; reserved test is not collection")
    records={record["name"]:record for record in suite["episodes"]};expected={case["name"]:case for case in cases}
    require(len(records)==len(suite["episodes"])==len(cases) and set(records)==set(expected) and
            {path.name for path in (directory/"episodes").iterdir() if path.is_dir()}==set(expected),"production bout/maneuver matrix is missing, duplicated or includes calibration")
    require(manifest["collector_sha256"]==digest(root/"code/scripts/body_combat_demonstrations.py") and
            manifest["teacher_code_sha256"]==digest(root/"code/scripts/body_combat_teacher.py") and
            manifest["config_sha256"]==digest(root/"code/evals/npc-body/body-combat-demonstrations-v1.json") and
            manifest["binary_sha256"]==read(root/"environment.json")["binary_sha256"],"production collector/teacher/config/binary binding changed")
    indexed={Path(entry["path"]).resolve():entry for entry in read(root/"dataset-parent/dataset.json")["episodes"]}
    clearances=[]
    for name,case in expected.items():
        record=records[name];episode=directory/"episodes"/name;metadata=read(episode/"trajectory-manifest.json");saved=read(episode/"episode.json")
        require(all(record.get(key)==value for key,value in case.items()) and all(record.get(key)==value for key,value in saved.items()) and
                record.get("episode_dir")=="episodes/"+name and record.get("manifest_file")=="episodes/"+name+"/trajectory-manifest.json" and
                metadata.get("collection_scope")=="full" and metadata.get("initial")==record["initial"] and
                metadata.get("final")==bc.compact_final_snapshot(record["final"]),"production episode setup/snapshot changed after collection")
        raw=bc.canonical.read_jsonl(episode/"arena-events.jsonl")
        require([row["arena"] for row in raw if row.get("kind")=="reset_ready"]==[record["initial"]] and
                [row["arena"] for row in raw if row.get("kind")=="terminal"]==[record["final"]],"production copied initial/final differs from actual engine")
        require(record.get("native_opponent")==(case["task"]=="native_duel") and metadata.get("opponent_provenance")==("native_ai" if record["native_opponent"] else "scripted_hold_nonexpert"),"held micro-opponent incorrectly received native-combat credit")
        clearance_admission(record["initial"],case,record.get("wall_clearance_evidence"))
        require(metadata.get("wall_clearance_evidence")==record.get("wall_clearance_evidence"),"trajectory clearance metadata changed")
        if "wall_clearance_cm" in case:
            numeric=production_numeric_clearance(episode,record["initial"],record["wall_clearance_evidence"])
            clearances.append({"name":name,"requested_cm":case["wall_clearance_cm"],"ray_cm":record["wall_clearance_evidence"]["actual_cm"],
                               "normal_cm":record["wall_clearance_evidence"]["normal_actual_cm"],"numeric_normal_projection":numeric,
                               "tangent_placement":tangent_placement_proof(record["initial"],case)})
        entry=indexed.get(episode.resolve());require(entry is not None and entry["split"]==case["split"] and
            entry["manifest_sha256"]==digest(episode/"trajectory-manifest.json") and entry["records_sha256"]==record.get("approval_sha256"),"production episode is missing from canonical parent or has changed approval")
        counts=Counter(row["action_id"] for row in metadata["scripted_expert_approval"]["approved"])
        require(record.get("approved_actions")=={str(action):counts[action] for action in range(1,12)},"production approval counts changed")
    return {"episode_count":len(cases),"bout_count":sum(case["task"]=="native_duel" for case in cases),
            "maneuver_micro_count":sum(case["task"]!="native_duel" for case in cases),"clearance_variations":clearances,
            "calibration_admitted":False,"reserved_test_collected":False,"manifest_sha256":digest(directory/"manifest.json")}


def training_evidence(root,config,data,adapter):
    models={}
    for architecture in ("gru","mlp"):
        model=bc.model_evidence(root,architecture,adapter,data)
        selection=read(root/"models"/architecture/"selection.json")
        for candidate in selection["candidates"]:
            directory=bc.artifact(root,candidate["path"]+"/training.json").parent
            trained=read(directory/"training.json");metadata=read(directory/"best.pt.json")["metadata"]
            require(trained["config"].get("evaluation_mode")=="continuous" and trained["selection"].get("evaluation_mode")=="continuous" and
                    metadata["training"].get("selection_evaluation_mode")=="continuous", "selected epoch used artificial chunk eval context")
            for epoch in bc.read_value(directory/"history.json"):
                metric=epoch["eval"]
                require(metric.get("evaluation_mode")=="continuous" and metric.get("artificial_chunk_resets")==0 and
                        metric.get("duplicate_burnin_labels")==0 and metric.get("stream_count",0)>0 and
                        metric.get("actual_stream_reset_count")==metric["stream_count"],"epoch selection reset/replayed continuous histories")
        for split in ("eval","test","ood"):
            metric=read(root/"models"/architecture/split/"metrics.json")
            require(metric.get("evaluation_mode")=="continuous" and metric.get("artificial_chunk_resets")==0 and
                    metric.get("duplicate_burnin_labels")==0,"held-out metric used chunk reset context")
        models[architecture]=model
    selected=read(root/"selection.json")
    profile=read(root/"dataset/selection-profile.json")
    require(selected.get("selection_split")=="eval" and selected.get("test_and_ood_used_for_selection") is False and
            selected.get("config_sha256")==digest(root/"code/evals/npc-body/body-stage8-v1.json"),"global selection/config scope differs")
    for architecture in models:
        require(selected["models"][architecture]==read(root/"models"/architecture/"selection.json"),"global/model checkpoint candidate differs")
        for key in ("selection_evaluation_episode_ids","selection_evaluation_groups"):
            require(selected.get(key)==profile.get(key),"global selection evaluated legacy or undeclared seed groups")
    return models


def proof_ipc(path):
    pending,pairs={},[]
    for row in bc.canonical.read_jsonl(path):
        stamp,value=row.get("wall_monotonic_ns"),row.get("value",{})
        require(type(stamp) is int and stamp>0 and row.get("clock_domain")=="linux-clock-monotonic" and type(value.get("id")) is int,
                "motor proof raw IPC clock/identity missing")
        if row.get("direction")=="request":
            require(value["id"] not in pending,"motor proof repeated request");pending[value["id"]]=(value,stamp)
        else:
            require(row.get("direction")=="response" and value["id"] in pending,"motor proof response has no request")
            request,before=pending.pop(value["id"])
            require(value.get("clock_domain")=="linux-clock-monotonic" and before<=value.get("wall_monotonic_ns",0)<=stamp,
                    "motor proof native clock is outside same-host transport bracket")
            pairs.append((request,value,before,stamp))
    require(not pending,"motor proof unanswered native request")
    return pairs


def realtime_engine_proof(directory,requested_clearances):
    proof=read(directory/"probe.json");require(proof.get("kind")=="body_realtime_arena_probe" and proof.get("model") is None,"realtime proof imported a learner")
    stable=("generation","session_epoch","world_epoch","sim_step","watched_actors","emitted","dropped_total","event_dropped_total",
            "invalid_numeric_rows","missed_period_buckets","entity_registry_size","queue_size")
    def bind(response,pairs):require(any(actual==response for _,actual,_,_ in pairs),"motor proof copied response differs from raw IPC")
    def hold(value,pairs):
        before,after=value["before"],value["after"];bind(before,pairs);bind(after,pairs)
        for key in ("game_ms","sim_step","native_choices","arena","body_control"):require(before.get(key)==after.get(key),"ready/terminal native state advanced during hold")
        require(all(key in before["body_observation"] and before["body_observation"][key]==after["body_observation"].get(key) for key in stable),"ready/terminal hold emitted/changed numeric stream")
        require(after["wall_monotonic_ns"]-before["wall_monotonic_ns"]>=value["requested_wall_ms"]*1e6,"native hold did not wait declared wall duration")
    def rate(value,pairs):
        before,after=value["before"],value["after"];bind(before,pairs);bind(after,pairs)
        seconds=(after["wall_monotonic_ns"]-before["wall_monotonic_ns"])/1e9
        steps=after["sim_step"]-before["sim_step"];game=after["game_ms"]-before["game_ms"]
        require(seconds>0 and 27<=steps/seconds<=33 and .9<=game/(seconds*1000)<=1.1 and
                math.isclose(value["substeps_per_wall_s"],steps/seconds,rel_tol=1e-12),"actual free-running native rate differs from27..33Hz")
    arena=proof["arena"];pairs=proof_ipc(directory/"arena-realtime/ipc.jsonl")
    bind(arena["initial"],pairs);bind(arena["start"],pairs);bind(arena["final"],pairs)
    for value in arena["pre_start_holds"]+[arena["owned_pre_start_hold"],arena["terminal_watchdog_hold"]]:hold(value,pairs)
    require(arena["terminal_watchdog_hold"]["requested_wall_ms"]>=6000,"terminal proof did not exceed watchdog deadline")
    rate(arena["free_tick_rate"],pairs)
    after=arena["free_tick_rate"]["after"];start=arena["start"]
    require(after["arena"]["elapsed_game_ms"]<=(after["wall_monotonic_ns"]-start["wall_monotonic_ns"])/1e6+100,"pre-start delay became simulation catch-up debt")
    for key in ("invalid_unowned_start","invalid_owned_start","repeated_start","realtime_step_rejected"):
        bind(arena[key],pairs);require(arena[key].get("ok") is False,"invalid/repeated/realtime manual advance was accepted")
    require(arena["repeated_start"]["arena"]["start_game_ms"]==start["arena"]["start_game_ms"],"repeated start rebased arena clock")
    for command,response in arena["late_refusals"].items():
        bind(response,pairs);require(response.get("accepted") is False and response.get("reason")=="arena_terminal" and response.get("arena")==arena["final"]["arena"],
                                    "late terminal command mutated sealed native state")
    require(set(arena["late_refusals"])=={"body_heartbeat","body_acquire","body_action","body_goal"},"terminal refusal matrix incomplete")
    controls=bc.canonical.read_jsonl(directory/"arena-realtime/body-control-events.jsonl")
    mechanics=bc.canonical.read_jsonl(directory/"arena-realtime/events.jsonl")
    actors=arena["initial"]["arena"]["actors"];ids=[actor["entity_id"] for actor in actors]
    blocks=arena["four_actor_blocks"]
    require(len(ids)==len(set(ids))==4 and set(blocks)=={str(entity) for entity in ids},"four physical actors did not each receive a genuine Block9")
    for entity in ids:
        response=blocks[str(entity)];bind(response,pairs)
        request=next(request for request,actual,_,_ in pairs if actual==response)
        require(request.get("cmd")=="body_action" and request.get("action_id")==9 and request.get("entity_id")==entity and response.get("accepted") is True and
                any(event.get("phase")=="started" and event.get("entity_id")==entity and event.get("command_id")==response.get("command_id") and
                    event.get("execution_id")==response.get("execution_id") and event.get("action_id")==9 for event in controls),"four-actor block lacks actual accepted/start command evidence")
    mapping={row["actor_id"]:row["observation_entity_id"] for row in mechanics if row.get("phase")=="arena_actor_mapping"}
    branch=[row for row in mechanics if row.get("phase")=="animation_branch_accept"]
    require(arena["four_actor_animation_streams"]==branch and {row.get("animation_actor_index") for row in branch}=={0,1,2,3} and
            all(row.get("mode")=="isolated" and row.get("pair_index")==row["animation_actor_index"]//2 and
                mapping.get(row["actor_id"])==ids[row["animation_actor_index"]] for row in branch),"four genuine isolated animation RNG streams lack actor/pair bindings")
    command=arena["pending_wait_request"];bind(command,pairs)
    events=[event for event in controls if event.get("entity_id")==command["entity_id"] and event.get("command_id")==command["command_id"]]
    require(any(event.get("phase")=="started" for event in events) and not any(event.get("phase") in ("completed","cancelled") and
            event.get("wall_monotonic_ns",0)<=arena["terminal_watchdog_hold"]["after"]["wall_monotonic_ns"] for event in events),"unfinished real wait was fabricated as completed/cancelled during terminal")
    release,disconnect=arena["cleanup_release"],arena["cleanup_disconnect"]
    bind(release,pairs);bind(disconnect,pairs);bind(arena["events_after_cleanup"],pairs)
    require(release.get("accepted") is True,"explicit terminal cleanup release was not actually accepted")
    if disconnect.get("accepted") is not True:
        admission=arena.get("cleanup_already_restored_proof",{});pending,restored=admission["release_pending"],admission["native_restored"]
        request,actual,request_ns,response_ns=next(pair for pair in pairs if pair[1]==release)
        original_request=next(pair[0] for pair in pairs if pair[1]==disconnect)
        identity={key:original_request[key] for key in ("entity_id","session_epoch","world_epoch","control_generation")}
        raw=arena["events_after_cleanup"]["events"]
        require(disconnect.get("reason")=="not_owned" and admission.get("disconnect_accepted") is False and admission.get("disconnect_reason")=="not_owned" and
                pending in raw and restored in raw and all(pending.get(key)==restored.get(key)==value for key,value in identity.items()) and
                pending.get("phase")==restored.get("phase")=="ownership" and pending.get("reason")=="release_pending" and
                restored.get("reason")=="native_restored" and pending.get("release_reason")==restored.get("release_reason")=="watchdog_timeout" and
                restored.get("owner")=="native" and pending["event_seq"]<restored["event_seq"] and
                request_ns<=pending["wall_monotonic_ns"]<restored["wall_monotonic_ns"]<=actual["wall_monotonic_ns"] and
                restored["t_game_ms"]==arena["final"]["game_ms"] and restored["sim_step"]==arena["final"]["sim_step"],
                "already-restored terminal cleanup refusal lacks exact original lease/watchdog/explicit-release proof")
        current=next(actor for actor in disconnect["body_control"]["actors"] if actor["entity_id"]==identity["entity_id"])
        require(current==admission["status"] and current["owner"]=="native" and current["control_generation"]==0 and
                all(disconnect.get(key)==value for key,value in identity.items() if key!="control_generation"),"cleanup refusal invented a native-restored status")
    for _,response,_,_ in pairs:
        if response.get("wall_monotonic_ns",0)>=arena["final"]["wall_monotonic_ns"] and "arena" in response:
            require(response["arena"]==arena["final"]["arena"] and response.get("game_ms")==arena["final"]["game_ms"] and
                    response.get("sim_step")==arena["final"]["sim_step"],"explicit terminal cleanup advanced or rewrote sealed physical outcome")
    generic_pairs=proof_ipc(directory/"generic-realtime/ipc.jsonl");generic=proof["generic"]
    rate(generic["free_tick_rate"],generic_pairs);hold(generic["terminal_hold"],generic_pairs)
    clearances=proof["wall_clearances"];expected={(side,value) for side in (0,1) for value in requested_clearances}
    require(len(clearances)==12 and {(row["side"],row["requested_cm"]) for row in clearances}==expected,"measured clearance matrix incomplete")
    for row in clearances:
        wall_pairs=proof_ipc(directory/f"wall-{row['side']}-{row['requested_cm']}/ipc.jsonl")
        bind(row["initial"],wall_pairs);hold(row["pre_start_hold"],wall_pairs)
        native=row["initial"]["arena"]["initial_state"]["setup"]["geometry_proof"]
        requested=row["requested_cm"];normal_clearance_geometry(native,requested)
        actual=native["actual_wall_normal_clearance_cm"]
        require(native.get("requested_wall_clearance_cm")==requested and 60<=actual<=180 and abs(actual-requested)<=20 and
                abs(native["wall_normal_clearance_error_cm"]-(actual-requested))<=.001 and native["wall_normal_clearance_tolerance_cm"]==20 and
                abs(native["safe_height_delta_cm"])<=50 and abs(native["placement_height_delta_cm"])<=50 and
                all(native.get(flag) is True for flag in ("wall_ray_hit","capsule_move_blocked","capsule_block_is_land","initial_capsules_free",
                                                        "safe_outward_move","grounded_placements","target_visible","melee_line_clear")),"clearance variation lacks actual collision/height/visibility proof")
        require(any(row["numeric_observation"] in response.get("observations",[]) for request,response,_,_ in wall_pairs if request.get("cmd")=="body_observations") and
                row["numeric_ray"] in row["numeric_observation"]["local_obstacles"]["rays"],"clearance proof differs from actual model-facing obstacle ray")
        numeric_normal_clearance(native,row["numeric_observation"],row["numeric_ray"],row["numeric_ray_angle_error_rad"])
    return {"prestart_hold":True,"native_free_tick_bounds":[27,33],"terminal_watchdog_hold_ms":arena["terminal_watchdog_hold"]["requested_wall_ms"],
            "unfinished_real_wait_preserved":True,"four_actor_block_starts":4,"four_actual_animation_streams":4,"wall_clearances":12,"raw_proof_sha256":digest(directory/"probe.json"),"model_imported":False}


def engine_compatibility_evidence(root,config,reference):
    path=root/"dataset/engine-compatibility.json";value=read(path)
    require(digest(path)==digest(root/"dataset-parent/engine-compatibility.json") and value.get("kind")=="body_stage8_engine_compatibility" and
            value.get("historical_owned_trajectory_equivalence") is False,"engine compatibility changed or claims owned trajectory equality")
    historical=value["historical"];old=read(reference/"dataset/dataset.json")
    require(historical["dataset_sha256"]==digest(reference/"dataset/dataset.json") and historical["episode_count"]==len(old["episodes"])==60,"historical corpus compatibility source differs")
    expected=[];binaries=set();sources=set()
    for entry in old["episodes"]:
        manifest_path=Path(entry["path"])/entry["manifest_file"];manifest=read(manifest_path)
        require(digest(manifest_path)==entry["manifest_sha256"],"historical engine manifest changed")
        binaries.add(manifest["binary_sha256"]);sources.add(manifest["source_sha256"])
        expected.append({"episode_id":entry["episode_id"],"path":str(manifest_path),"manifest_sha256":entry["manifest_sha256"],
                         "binary_sha256":manifest["binary_sha256"],"source_sha256":manifest["source_sha256"]})
    require(historical["episode_manifests"]==expected and historical["binary_sha256_set"]==sorted(binaries) and historical["source_sha256_set"]==sorted(sources),"historical binary/source sets laundered")
    environment=read(root/"environment.json");runtime=value["runtime"]
    require(runtime["binary_sha256"]==environment["binary_sha256"]==digest(root/"reference/gothic-body-runner") and
            runtime["environment_sha256"]==digest(root/"environment.json") and runtime["source_archive_sha256"]==digest(root/"source.tar.gz"),"runtime compatibility identity changed")
    contracts=value["contracts"]
    require(contracts=={"observation_schema_version":1,"feature_version":config["dataset"]["feature_version"],"feature_count":184,"action_version":1,
                        "periodic_sample_ms":50,"max_label_age_game_ms":67,"normalization_source":"train_only"},"historical/runtime feature/action contract differs")
    proof_root=(root.parent/config["engine_compatibility"]["proof_run"]).resolve();proof=value["proofs"]
    require(proof_root.is_relative_to(root.parent.resolve()) and Path(proof["path"]).resolve()==proof_root and
            proof["binary_sha256"]==runtime["binary_sha256"]==digest(proof_root/"reference/gothic-body-runner"),"external motor proof is not the production binary")
    proof_environment=read(proof_root/"environment.json")
    require(proof.get("run")==config["engine_compatibility"]["proof_run"] and proof.get("source_sha256")==runtime["source_sha256"] and
            proof["source_archive_sha256"]==proof_environment["source_archive_sha256"]==digest(proof_root/"source.tar.gz") and
            proof_environment["binary_sha256"]==runtime["binary_sha256"],"external proof archive/source/environment binding changed")
    expected_files={"environment.json","setup-probe/probe.json","setup-probe/manifest.json","setup-probe/parity/headless-parity.json","realtime-probe/probe.json","realtime-probe/manifest.json"}
    require(set(proof["inventory"])==expected_files and all(digest(bc.artifact(proof_root,name))==sha for name,sha in proof["inventory"].items()),"external motor evidence inventory changed")
    for name in ("setup-probe","realtime-probe"):
        manifest=read(proof_root/name/"manifest.json")
        require(manifest["binary_sha256"]==runtime["binary_sha256"] and manifest["source_sha256"]==runtime["source_sha256"],"external motor source/binary differs from new teacher/runtime")
    for entry in read(root/"dataset-parent/dataset.json")["episodes"]:
        if entry["episode_id"] in {row["episode_id"] for row in old["episodes"]}:continue
        manifest=read(Path(entry["path"])/entry["manifest_file"])
        require(manifest["binary_sha256"]==runtime["binary_sha256"] and manifest["source_sha256"]==runtime["source_sha256"],"new teacher captured on another engine")
    require(proof["realtime"].get("requested_clearances_cm")==declared_clearances(config) and
            proof["realtime"].get("clearance_cases")==12 and proof["realtime"].get("sides")==[0,1],"compatibility proof clearance matrix differs from production microcurriculum")
    require(read(root/"dataset/selection-profile.json").get("engine_compatibility_sha256")==digest(path),"selection began without bound engine compatibility")
    return {"historical_source_binaries":sorted(binaries),"historical_source_hashes":sorted(sources),"runtime":runtime,
            "realtime_motor":realtime_engine_proof(proof_root/"realtime-probe",declared_clearances(config)),"compatibility_sha256":digest(path),"historical_owned_trajectory_equivalence":False}


def native_safety_evidence(root,config,native_reference):
    import body_api_report as api
    proof_root=(root.parent/config["engine_compatibility"]["proof_run"]).resolve()
    require(proof_root.is_relative_to(root.parent.resolve()),"motor proof escapes declared run storage")
    setup=proof_root/"setup-probe";manifest=read(setup/"manifest.json")
    require(manifest.get("binary_sha256")==read(root/"environment.json")["binary_sha256"],"native parity proof used another engine binary")
    parity=api.native_parity(native_reference,setup/"native-off")
    require(parity.get("status")=="matched" and sorted(row.get("seed") for row in parity.get("episodes",[]))==[17,29,43],
            "motor change broke ordered three-seed historical native parity")
    faults=root/"faults";fault_manifest=read(faults/"manifest.json")
    require(fault_manifest.get("binary_sha256")==manifest["binary_sha256"],"native fault receipts used another binary")
    fault=api.summarize(faults,read(root/"code/evals/npc-body/body-api-v1.json"),native_reference)
    require(fault.get("full_stage4_evidence") is True and not fault.get("issues"),"real native ownership/mask/reconnect fault gate failed: "+str(fault.get("issues")))
    return {"native_parity":parity,"native_faults":fault,"setup_manifest_sha256":digest(setup/"manifest.json"),
            "fault_manifest_sha256":digest(faults/"manifest.json"),"binary_sha256":manifest["binary_sha256"]}


def resource_evidence(root,config):
    phases={};names=config["resources"]["required_services"]
    proof_root=(root.parent/config["engine_compatibility"]["proof_run"]).resolve()
    require(proof_root.is_relative_to(root.parent.resolve()),"resource proof run escapes declared storage")
    for phase in ("setup","realtime-probe","stage8-faults","combat-demos","stage8-dataset","stage8-verify","stage8-overfit","stage8-train","stage8-evaluate","probe","shadow","realtime","screening","load"):
        directory=proof_root if phase in ("setup","realtime-probe") else root
        before,after=read(directory/f"services-{phase}-before.json"),read(directory/f"services-{phase}-after.json")
        services=bc.all_services(before,after,names)
        require(before.get("recorded_at_utc") and before["recorded_at_utc"]<=after.get("recorded_at_utc",""),"resource/service phase clock reverses")
        text=(directory/f"resources-{phase}.txt").read_text()
        require(re.search(r"Exit status:\s+0\s*$",text,re.M),"resource phase did not complete successfully: "+phase)
        peak=re.search(r"Maximum resident set size \(kbytes\):\s+(\d+)",text)
        require(peak and 0<int(peak[1])<=config["resources"]["memory_max_gib"]*1024*1024,"phase process peak RSS exceeded measured bound")
        phases[phase]={"peak_rss_kib":int(peak[1]),"production_services":services,
                       "time_sha256":digest(directory/f"resources-{phase}.txt"),"before_sha256":digest(directory/f"services-{phase}-before.json"),
                       "after_sha256":digest(directory/f"services-{phase}-after.json"),"run":str(directory)}
    launcher=(root/"code/scripts/body-bc-remote.sh").read_text()
    require("CPUQuota=200%" in launcher and "MemoryMax=6G" in launcher and "TasksMax=128" in launcher and
            config["resources"]["cpu_quota_percent"]==200 and config["resources"]["docker_changes_allowed"] is False,
            "frozen launcher does not enforce declared independent worker resource bounds")
    return {"phases":phases,"host":"dev-ubuntu","models_device":"cpu","own_gpu_device_claim":False}


def four_actor_evidence(root,config):
    directory=root/"load";suite=read(directory/"suite.json");manifest=read(directory/"manifest.json")
    require(suite.get("kind")=="body_policy_stage8_four_actor_load" and suite.get("status")=="collected" and not suite.get("error") and
            suite.get("behavioral_quality_evaluated") is False,"four-actor load collection incomplete or given combat credit")
    require(manifest.get("binary_sha256")==read(root/"environment.json")["binary_sha256"] and
            manifest.get("driver_sha256")==digest(root/"code/scripts/body_policy_load.py") and
            manifest.get("common_driver_sha256")==digest(root/"code/scripts/body_policy_diagnostics.py") and
            manifest.get("config_sha256")==digest(root/"code/evals/npc-body/body-stage8-v1.json"),"four-actor code/config/binary binding differs")
    load=config["load"];cohorts=load["cohorts_per_provider"]
    expected={f"four-actor-{policy}-{index}":(policy,index) for policy in load["policies"] for index in range(cohorts)}
    require(len(suite["cases"])==len(expected) and {record["name"] for record in suite["cases"]}==set(expected),"actual four-actor cohort matrix differs")
    results=[];normalizer=read(root/"dataset/normalization.json");physical_reset=None
    base_profile=read(root/"code/evals/npc-body/body-arena-v1.json")["arena_profile"]
    for record in suite["cases"]:
        policy,index=expected[record["name"]];path=directory/record["name"]
        require(read(path/"episode.json")==record and record.get("policy")==policy and record.get("cohort")==index and
                record.get("mode")=="realtime" and record.get("behavioral_quality_evaluated") is False and record.get("no_fallback_credit") is False,
                "four-actor record/cohort/profile differs")
        initial,final=record["initial"],record["final"]
        native=bc.canonical.read_jsonl(path/"arena-events.jsonl")
        require([row["arena"] for row in native if row.get("kind")=="reset_ready"]==[initial] and
                [row["arena"] for row in native if row.get("kind")=="terminal"]==[final],"four-actor native snapshots differ from copied data")
        ids=[pair[role+"_entity_id"] for pair in initial.get("actor_pairs",[]) for role in ("primary","opponent")]
        require(initial.get("load_fixture") is True and initial.get("quality_credit") is False and initial.get("real_actor_count")==4 and
                len(ids)==len(set(ids))==4 and set(ids)=={actor["entity_id"] for actor in initial["actors"]} and
                record.get("physical_actor_ids")==ids and initial["actor_pairs"]==final.get("actor_pairs"),"load duplicates virtual/sequential actor identities")
        require(final.get("outcome")=="time_limit" and final.get("elapsed_game_ms")>=load["duration_ms"] and
                final.get("infrastructure") is False and all(actor["hp"]>0 and not actor["down"] for actor in final["actors"]),
                "four-actor cohort ended before its healthy declared duration")
        mechanics=bc.canonical.read_jsonl(path/"events.jsonl");controls=bc.canonical.read_jsonl(path/"body-control-events.jsonl")
        observations=bc.canonical.read_jsonl(path/"observations.jsonl");pairs=bc.timed_pairs(bc.canonical.read_jsonl(path/"ipc.jsonl"))
        semantics=[]
        for pair in initial["actor_pairs"]:
            ids_pair={pair["primary_entity_id"],pair["opponent_entity_id"]}
            start={**initial,**{role+"_entity_id":pair[role+"_entity_id"] for role in ("primary","opponent")},
                   "actors":[actor for actor in initial["actors"] if actor["entity_id"] in ids_pair]}
            end={**final,**{role+"_entity_id":pair[role+"_entity_id"] for role in ("primary","opponent")},
                 "actors":[actor for actor in final["actors"] if actor["entity_id"] in ids_pair]}
            require(not bc.clean_reset(start,{**base_profile,"hp":load["actor_hp"],"hp_max":load["actor_hp"]}),"load pair initial profile/queue/pose differs")
            require(not bc.linked_mechanics(start,end,controls,mechanics)["issues"],"four-actor pair HP/block counter differs from native effects")
            semantics.append(bc.semantic_initial(start))
        if physical_reset is None:physical_reset=semantics
        else:require(physical_reset==semantics,"four-actor physical reset differs across provider/cohorts")
        visibility=visibility_diagnostics(mechanics,initial,controls,observations)
        integrity=capture_integrity(record,mechanics,pairs)
        runtime=None;timing=None
        if policy=="native":
            require(record.get("policy_actor_ids")==[] and record.get("independent_runtime_count")==0 and not controls,
                    "native load baseline acquired a neural owner")
        else:
            require(record.get("policy_actor_ids")==ids and record.get("independent_runtime_count")==4 and
                    set(record.get("checkpoint_sha256",{}))==set(record.get("normalizer_sha256",{}))=={str(entity) for entity in ids},
                    "four independent live actor runtimes are missing")
            for entity in ids:
                require(record["checkpoint_sha256"][str(entity)]==digest(root/"models"/policy/"best.pt") and
                        record["normalizer_sha256"][str(entity)]==digest(root/"dataset/normalization.json"),"load actor weights/normalizer changed")
                actor=next(row for row in final["actors"] if row["entity_id"]==entity)
                require(actor["owner"]=="scripted" and actor["control_generation"]>0 and actor["release_pending"] is False and
                        not any(event.get("entity_id")==entity and event.get("phase")=="ownership" and event.get("reason") in ("native_restored","release_pending") and
                                event.get("t_game_ms",final["game_ms"]+1)<final["game_ms"] for event in controls),"load actor lost live neural ownership")
            before=read(path/"policy-final-before-clear.json")
            require(set(before.get("runtimes",{}))=={str(entity) for entity in ids} and all(state.get("actors")==1 for state in before["runtimes"].values()),
                    "load hidden memory does not prove four distinct actual actor states")
            runtime=runtime_evidence(path,record,normalizer,observations,pairs,controls,actors=ids)
            timing=latency_evidence(path,record,observations,pairs)
            require({row["entity_id"] for row in observations if row.get("periodic") is True and row.get("control_generation",0)>0}==set(ids),
                    "fourth live actor did not receive genuine numeric model observations")
        results.append({"name":record["name"],"policy":policy,"cohort":index,"actual_actor_ids":ids,"runtime":runtime,"latency":timing,
                        "visibility":visibility,"capture_integrity":integrity,"quality_credit":False,"episode_sha256":digest(path/"episode.json")})
    return {"cases":results,"cohorts_per_provider":cohorts,"actual_actors_same_world":4,"quality_credit":False}


def service_json_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def service_receipts(records,fixtures,phase,count,warmup):
    metrics={};identity=set()
    for service in ("asr","tts","decider","gemma"):
        rows=[row for row in records if row.get("service")==service]
        require(len(rows)==count+warmup and {row.get("iteration") for row in rows}==set(range(count+warmup)),"protected-service request count/iteration differs")
        previous_end=0;latencies=[]
        for row in sorted(rows,key=lambda item:item["iteration"]):
            require(row.get("phase")==phase and row.get("status")=="completed" and row.get("warmup")== (row["iteration"]<warmup) and
                    row.get("request_sha256")==fixtures["request_sha256"][service] and row.get("fixture_binding_sha256")==fixtures["fixture_binding_sha256"],
                    "protected-service receipt changed request/fixture or failed")
            begin,submitted,first,end=(row.get(key) for key in ("request_begin_wall_monotonic_ns","request_submitted_wall_monotonic_ns",
                                                               "first_result_wall_monotonic_ns","completed_wall_monotonic_ns"))
            require(all(type(value) is int and value>0 for value in (begin,submitted,first,end)) and previous_end<=begin<=submitted<=first<=end,
                    "protected service concurrency/order/clock differs")
            previous_end=end;origin=row.get("metric_origin_wall_monotonic_ns")
            if service=="asr":require(origin==row.get("speech_submitted_wall_monotonic_ns") and begin<=origin<=submitted,"ASR metric does not begin at real speech submission")
            else:require(origin==submitted,"service metric excludes actual submitted-request latency")
            latency=(first-origin)/1e6
            require(type(row.get("latency_ms")) in (int,float) and math.isclose(row["latency_ms"],latency,rel_tol=1e-12,abs_tol=1e-12),"service p95 sample contradicts raw timestamps")
            events=row.get("response_events",[])
            require(events and all(type(event.get("wall_monotonic_ns")) is int and submitted<=event["wall_monotonic_ns"]<=end for event in events) and
                    all(a["wall_monotonic_ns"]<=b["wall_monotonic_ns"] for a,b in zip(events,events[1:])),"service response receipt order differs")
            request=fixtures[service]["request"];result=row.get("result",{})
            if service=="asr":
                finals=[event for event in events if event.get("value",{}).get("type")=="transcript.text.done"]
                require(finals and isinstance(result.get("transcript"),str) and result["transcript"].strip() and
                        finals[-1]["value"].get("text")==result["transcript"] and finals[-1]["wall_monotonic_ns"]<=first,
                        "ASR completion has no actual nonempty final transcript")
            elif service=="tts":
                chunks=[event for event in events if event.get("type")=="pcm"]
                done=[event["value"] for event in events if event.get("value",{}).get("type")=="done"]
                require(chunks and first==chunks[0]["wall_monotonic_ns"] and all(type(event.get("bytes")) is int and event["bytes"]>0 and event["bytes"]%2==0 and
                        isinstance(event.get("sha256"),str) and len(event["sha256"])==64 for event in chunks) and done and done[-1].get("id")==request["id"] and
                        result.get("pcm_bytes")==sum(event["bytes"] for event in chunks) and result.get("sample_rate")==done[-1].get("sample_rate")==48000 and
                        result.get("done")==done[-1],"TTS first PCM/complete stream has no real bounded audio receipt")
            elif service=="decider":
                answers=[event for event in events if event.get("value",{}).get("type")=="answers"]
                require(len(answers)==1 and answers[0]["wall_monotonic_ns"]==first and answers[0]["value"]==result and result.get("id")==request["id"] and
                        result.get("answers",{}).get("react",{}).get("choice") in request["questions"]["react"]["criteria"],"Decider latency has no actual typed first decision")
            else:
                texts=[];token_events=[];finish=False
                for event in events:
                    value=event.get("value",{})
                    if value.get("model") is not None:require(value["model"]==request["model"],"Gemma response has another model")
                    for choice in value.get("choices",[]):
                        content=choice.get("delta",{}).get("content")
                        if content:texts.append(content);token_events.append(event)
                        finish=finish or choice.get("finish_reason") is not None
                require(token_events and first==token_events[0]["wall_monotonic_ns"] and result.get("text")=="".join(texts) and finish and
                        result.get("model")==request["model"] and result.get("done_received") is True and
                        any(event.get("type")=="sse_done" for event in events),"Gemma first token lacks real content and terminal SSE evidence")
            if not row["warmup"]:latencies.append(latency)
            identity.add((service,row["iteration"]))
        metrics[service]={"samples":len(latencies),"p95_ms":percentile(latencies)}
    require(len(records)==len(identity)==4*(count+warmup),"unknown or duplicated protected service receipt")
    return metrics



DECIDER_DEPLOYMENT_SOURCE_SHA={
    "service":"60e6fdc65009c112a44847b89714d7392c6d74c5c0d140dff49fc19f54bbdcd3",
    "engine":"0e4e9e6cf688353b289fd183bce65ba25ba69f4bfe111e1c119a34cbd07e289c",
    "cli":"00838c49e664bd86b1e29364471e981f8792ce1e01beb7b7f879eb352a594359",
    "launcher":"4fa73be40742f62d59ea9063739512536444847a33ab1306769aa2c5a7cc8e8a",
    "entrypoint":"fbe186287523550c1496c70a764aa28d3169f40052197944b6e7e97aef32b685",
    "checkpoint_metadata":"08623922b389d37228e76116ce1495abf8bf25128b24327b14db5f8e89655102"}


def decider_handshake(fixtures,result):
    expected={"type":"hello","service":"gothic-decider","model":"gothic-g9e"}
    hello=result.get("connection_handshakes",{}).get("decider")
    require(fixtures.get("decider",{}).get("expected_greeting")==expected and isinstance(hello,dict) and
            all(hello.get(key)==value for key,value in expected.items()),"protected Decider actual handshake differs from exact deployed model/service contract")
    binding=fixtures["decider"].get("deployed_binding",{});identity=result.get("services_before",{}).get("decider")
    require(binding.get("unit")=="gothic-decider" and binding.get("unit_identity")==identity and isinstance(identity,dict) and
            type(binding.get("pid")) is int and binding["pid"]>0 and str(binding["pid"])==identity.get("MainPID"),
            "Decider handshake did not come from the fixed deployed service process")
    return {"expected_greeting":expected,"actual_greeting":hello,"pid":binding["pid"],"unit_identity":identity}


def decider_deployment(fixtures):
    binding=fixtures["decider"]["deployed_binding"];base=Path("/home/kaifat/gothic-ai/decider");checkpoint=base/"models/gothic-g9e"
    options=binding.get("selected_launch_options",{})
    require(binding.get("unit_path")=="/home/kaifat/.config/systemd/user/gothic-decider.service" and
            binding.get("cwd")==str(base) and binding.get("entrypoint_path")==str(base/".venv/bin/gothic-decider") and
            binding.get("exec_start_path")==str(base/"deploy/run.sh") and binding.get("checkpoint_path")==str(checkpoint) and
            set(options)<= {"--host","--port","--checkpoint","--engine"} and options.get("--host")=="dev-ubuntu" and
            options.get("--port")=="47832" and options.get("--engine","strands")=="strands" and
            ("--checkpoint" not in options or Path(options["--checkpoint"]).resolve()==checkpoint),"Decider deployment executable/model/endpoint differs from frozen preflight")
    require(isinstance(binding.get("startup_record"),str) and re.fullmatch(r"[^\n]*gothic_decider\.engines\.strands: loaded "+re.escape(str(checkpoint))+r" in [0-9.]+ s(?: \(LoRA merged\))?",binding["startup_record"]),
            "Decider current invocation lacks actual tuned checkpoint startup provenance")
    paths={"service":base/"src/gothic_decider/service.py","engine":base/"src/gothic_decider/engines/strands.py","cli":base/"src/gothic_decider/cli.py",
           "launcher":base/"deploy/run.sh","entrypoint":base/".venv/bin/gothic-decider","checkpoint_metadata":checkpoint/"strands_decider_config.json"}
    sources=binding.get("sources",{})
    require(set(sources)==set(paths) and all(sources[key].get("path")==str(path) and sources[key].get("sha256")==DECIDER_DEPLOYMENT_SOURCE_SHA[key] and
            digest(path)==DECIDER_DEPLOYMENT_SOURCE_SHA[key] for key,path in paths.items()),"Decider deployed source/checkpoint metadata changed from actual preflight")
    require(fixtures["protocol_sources"].get("decider")==sources["service"],"Decider protocol source does not identify actual deployed entrypoint module")
    return {"checkpoint_path":str(checkpoint),"source_sha256":DECIDER_DEPLOYMENT_SOURCE_SHA,"startup_record":binding["startup_record"]}


def service_gate_role(config):
    role=config.get("service_gate_role","combat_gate")
    version=config.get("service_gate_protocol_version",0)
    require(role in ("combat_gate","standalone_report") and type(version) is int and version in (0,1) and
            (role!="standalone_report" or version==1),"invalid declared service gate role/version")
    return role


def service_gate_summary(config,measurement=None,candidate_budget_issues=(),measurement_issues=()):
    """Separate only measured p95 budget failures; source/window checks stay strict."""
    role=service_gate_role(config);budget=config["quality"]["maximum_service_p95_degradation_fraction"]
    failures=[]
    if measurement is not None:
        require(set(measurement.get("comparisons",{}))==set(config["load"]["policies"]),
                "service budget comparison omitted a provider")
        for provider,metrics in measurement["comparisons"].items():
            require(set(metrics)=={"asr","tts","decider","gemma"},"service budget comparison omitted a service")
            for service,row in metrics.items():
                baseline,during=row["baseline_p95_ms"],row["during_p95_ms"]
                require(all(type(value) in (int,float) and math.isfinite(value) and value>0 for value in (baseline,during)) and
                        type(row["samples"]) is int and row["samples"]==config["services"]["measured_requests"],
                        "service budget comparison has invalid p95/sample count")
                delta=during/baseline-1
                require(math.isclose(row["degradation_fraction"],delta,rel_tol=0,abs_tol=1e-12) and
                        row["passed"] is (delta<=budget),"service budget result differs from measured p95")
                if delta>budget:failures.append(f"{provider}: protected {service} p95 degraded {delta:.6f}")
    valid=measurement is not None and not measurement_issues
    return {"schema_version":1,"kind":"body_stage8_protected_service_report","service_gate_role":role,
            "service_gate_protocol_version":config.get("service_gate_protocol_version",0),
            "measurement_ready":valid,"service_gate_ready":valid and not failures,
            "status":"incomplete" if not valid else "failed" if failures else "passed",
            "maximum_p95_degradation_fraction":budget,"measured_requests_per_service":config["services"]["measured_requests"],
            "budget_issues":failures,"measurement_issues":list(measurement_issues),
            "candidate_budget_issues":list(candidate_budget_issues),
            "combat_budget_issues":list(candidate_budget_issues) if role=="combat_gate" else [],
            "physical_quality_credit":False,"model_causation_claim":False,"evidence":measurement}


def protected_service_load(root,config,load):
    directory=root/"load/services";fixtures=read(directory/"fixtures.json");summary=read(directory/"services.json")
    require(fixtures.get("kind")=="body_stage8_fixed_service_fixtures" and fixtures.get("clock_domain")=="linux-clock-monotonic" and
            fixtures.get("driver_sha256")==digest(root/"code/scripts/body_service_load.py"),"service load actual worker/clock differs")
    payload={key:value for key,value in fixtures.items() if key!="fixture_binding_sha256"}
    require(service_json_sha(payload)==fixtures.get("fixture_binding_sha256"),"fixed service fixture digest differs")
    for service in ("asr","tts","decider","gemma"):
        require(service_json_sha(fixtures[service]["request"])==fixtures["request_sha256"][service],"actual service request template hash differs")
    require(digest(bc.artifact(directory,fixtures["asr"]["input_file"]))==fixtures["asr"]["source_sha256"] and
            digest(bc.artifact(directory,fixtures["asr"]["payload_file"]))==fixtures["asr"]["request"]["payload_sha256"] and
            digest(Path(fixtures["tts"]["voice_path"]))==fixtures["tts"]["voice_sha256"] and fixtures["tts"].get("voice_design_allowed") is False and
            fixtures["gemma"]["request"].get("max_tokens")==16 and fixtures["decider"].get("external_llm_or_tools") is False,
            "service workload replaced cached bounded fixtures or requested external work")
    for value in fixtures.get("protocol_sources",{}).values():require(digest(Path(value["path"]))==value["sha256"],"service protocol source changed")
    deployment=decider_deployment(fixtures)
    handshakes={}
    def phase(name,count,warmup):
        path=directory/name;result=read(path/"result.json");records=bc.canonical.read_jsonl(path/"requests.jsonl")
        require(result.get("status")=="collected" and result.get("requests_sha256")==digest(path/"requests.jsonl") and result.get("requests")==records and
                result.get("concurrency_per_service")==1 and result.get("fixture_binding_sha256")==fixtures["fixture_binding_sha256"],"service cohort incomplete/receipt hashes changed")
        for service in ("asr","tts","decider","gemma"):
            before,after=result["services_before"][service],result["services_after"][service]
            require(before==after and before.get("ActiveState")=="active" and before.get("SubState")=="running" and int(before.get("MainPID","0"))>0 and
                    before.get("InvocationID") and before.get("ExecMainStartTimestampMonotonic"),"protected service restarted/changed during real load")
        handshakes[name]=decider_handshake(fixtures,result)
        return result,records,service_receipts(records,fixtures,name,count,warmup)
    before,before_rows,baseline=phase("before",config["services"]["measured_requests"],config["services"]["warmup_requests"])
    comparisons={};issues=[]
    load_records={row["name"]:row for row in read(root/"load/suite.json")["cases"]}
    first_acquire=[];all_starts=[];during={}
    for provider in config["load"]["policies"]:
        receipts=[]
        for index in range(config["load"]["cohorts_per_provider"]):
            name=f"during-{provider}-{index}";record=load_records[f"four-actor-{provider}-{index}"]
            result,rows,_=phase(name,config["services"]["measured_requests_per_cohort"],config["services"]["warmup_requests"] if index==config["services"]["warmup_cohort"] else 0)
            require(result["services_before"]==before["services_before"] and result["services_after"]==before["services_after"],"service identity differs between baseline and four-actor load")
            binding=read(directory/name/"window-binding.json")
            start,end=binding["window_start_wall_monotonic_ns"],binding["window_end_wall_monotonic_ns"]
            require(type(start) is int and type(end) is int and 0<start<end and all(start<=row["request_begin_wall_monotonic_ns"]<=row["completed_wall_monotonic_ns"]<=end for row in rows),
                    "DURING sample outlived the real four-actor cohort")
            require(binding.get("native_final")==record["final"] and binding.get("final_native_status")==record["perception_evidence"]["final_native_status"] and
                    binding.get("physical_actor_ids")==record["physical_actor_ids"] and binding.get("provider")==provider and binding.get("cohort")==index,
                    "service cohort binding has another native final/actor/provider")
            # The producer window is admitted only against actual live native rows.
            episode=root/"load"/record["name"];controls=bc.canonical.read_jsonl(episode/"body-control-events.jsonl")
            if provider!="native":
                acquired=[event for event in controls if event.get("phase")=="ownership" and event.get("reason")=="acquired"]
                require({event["entity_id"] for event in acquired}==set(record["physical_actor_ids"]) and all(event["wall_monotonic_ns"]<=start for event in acquired),
                        "DURING window started before four actual neural leases")
                first_acquire.extend(event["wall_monotonic_ns"] for event in acquired)
                require(not any(event.get("phase")=="ownership" and event.get("reason") in ("native_restored","release_pending") and event.get("wall_monotonic_ns",0)<end for event in controls),
                        "service DURING window credited work after neural ownership loss")
            transport=bc.canonical.read_jsonl(episode/"collector-transport.jsonl")
            ipc=bc.timed_pairs(bc.canonical.read_jsonl(episode/"ipc.jsonl"))
            clocks={row["id"]:row for row in transport if row.get("direction")=="response"}
            started=[clocks[request["id"]]["wall_monotonic_ns"] for request,response,_,_ in ipc if request.get("cmd")=="arena_start" and response.get("arena",{}).get("started") is True]
            finals=[clocks[request["id"]]["wall_monotonic_ns"] for request,response,_,_ in ipc if request.get("cmd")=="arena_status" and response.get("arena")==record["final"]]
            running=[response["wall_monotonic_ns"] for _,response,_,_ in ipc if response.get("arena",{}).get("started") is True and
                     not response["arena"].get("terminated") and not response["arena"].get("truncated")]
            require(started and finals and running and min(started)<=start<end<=max(finals) and end==max(running) and
                    binding.get("native_start_status",{}).get("wall_monotonic_ns")==start and
                    any(request.get("cmd")=="body_control_status" and response==binding["native_start_status"] for request,response,_,_ in ipc),
                    "service cohort clocks have no exact native healthy start/last-running/final RPC bracket")
            all_starts.extend(started)
            receipts.extend(row for row in rows if not row["warmup"])
        during[provider]=receipts;metrics={}
        for service in baseline:
            rows=[row for row in receipts if row["service"]==service]
            require(len(rows)==config["services"]["measured_requests"],"service DURING cohort aggregate omitted samples")
            p95=percentile([row["latency_ms"] for row in rows]);base=baseline[service]["p95_ms"]
            require(base>0,"service baseline p95 must be positive")
            delta=p95/base-1;passed=delta<=config["quality"]["maximum_service_p95_degradation_fraction"]
            metrics[service]={"baseline_p95_ms":base,"during_p95_ms":p95,"degradation_fraction":delta,"passed":passed,"samples":len(rows)}
            if provider==config["candidate_policy"] and not passed:issues.append(f"protected {service} p95 degraded {delta:.6f} under four GRU actors")
        comparisons[provider]=metrics
    require(first_acquire and max(row["completed_wall_monotonic_ns"] for row in before_rows)<min(first_acquire),"BEFORE workload ran after neural acquisition")
    require(all_starts and max(row["completed_wall_monotonic_ns"] for row in before_rows)<min(all_starts),"BEFORE workload ran after an arena start")
    require(summary.get("before")==before,"service summary altered original baseline receipts")
    for provider in comparisons:
        require(summary.get("during",{}).get(provider)==[load_records[f"four-actor-{provider}-{index}"]["service_workload"]
                    for index in range(config["load"]["cohorts_per_provider"])],"service summary cohort records differ from actual load episodes")
    require(summary.get("comparison",{}).get("fixture_binding_sha256")==fixtures["fixture_binding_sha256"],"saved service comparison uses another fixed fixture")
    return {"comparisons":comparisons,"fixture_sha256":digest(directory/"fixtures.json"),"receipt_count":len(before_rows)+sum(map(len,during.values())),
            "baseline_completed_before_neural_acquisition":True,"decider_deployment":deployment,"decider_actual_handshakes":handshakes},issues


def phase_evidence(root,phase,config,reference):
    directory = root/phase
    suite,manifest = read(directory/"suite.json"),read(directory/"manifest.json")
    require(suite.get("kind") == "body_policy_stage8_diagnostics" and suite.get("phase") == phase and
            suite.get("status") == "collected" and not suite.get("error"), "diagnostic collection incomplete")
    require(manifest.get("config_sha256") == digest(root/"code/evals/npc-body/body-stage8-v1.json") and
            manifest.get("headless") is True and manifest.get("renderer") == "none" and manifest.get("device") == "cpu", "diagnostic source/config/device differs")
    require(manifest.get("driver_sha256") == digest(root/"code/scripts/body_policy_diagnostics.py") and
            manifest.get("binary_sha256") == read(root/"environment.json")["binary_sha256"], "diagnostic actual driver/native binary differs")
    code_root=reference/"code" if phase=="probe" else root/"code"
    package=code_root/"body-service/src"
    expected_code={str(path.relative_to(package)):digest(path) for path in sorted((package/"gothic_body").glob("*.py"))}
    require(expected_code and manifest.get("policy_code_sha256")==expected_code and
            manifest.get("feature_packer_sha256")==digest(code_root/"scripts/body_trajectory_dataset.py") and
            Path(manifest.get("feature_packer_path",".")).resolve()==(code_root/"scripts/body_trajectory_dataset.py").resolve(),
            "diagnostic imported runtime/feature package differs from frozen source")
    if phase == "probe":
        require(manifest.get("frozen_stage7_suite_sha256") == digest(reference/"code/scripts/body_policy_suite.py") and
                manifest.get("feature_packer_sha256") == digest(reference/"code/scripts/body_trajectory_dataset.py"), "probe changed frozen reference runtime helpers")
        for name,sha in manifest.get("policy_code_sha256",{}).items():
            require(digest(bc.artifact(reference/"code/body-service/src",name)) == sha, "probe changed frozen model runtime package")
    configured = config[phase]
    expected = {case["name"]+"-"+policy:(case,policy) for case in configured["cases"] for policy in
                case.get("policies",configured.get("policies",("native","gru","mlp")))}
    records = suite["cases"]
    require(len(records) == len(expected) and {row["name"] for row in records} == set(expected), "declared diagnostic case matrix differs")
    resets,episodes,hashes = {},[],{}
    for record in records:
        case,policy = expected[record["name"]]
        path = directory/record["name"]
        require(read(path/"episode.json") == record and record["case"] == case and record["policy"] == policy, "copied diagnostic episode differs")
        arena = bc.canonical.read_jsonl(path/"arena-events.jsonl")
        require([row["arena"] for row in arena if row.get("kind") == "reset_ready"] == [record["initial"]] and
                [row["arena"] for row in arena if row.get("kind") == "terminal"] == [record["final"]], "native diagnostic initial/final differs")
        require(record["final"].get("infrastructure") is False and record["final"].get("terminated") != record["final"].get("truncated") and
                not bc.clean_reset(record["initial"],read(root/"code/evals/npc-body/body-arena-v1.json")["arena_profile"]), "native diagnostic reset/terminal/infrastructure failure")
        physical = {"actors":bc.semantic_initial(record["initial"]),"setup":record["initial"].get("setup")}
        if case["name"] in resets:require(resets[case["name"]] == physical, "paired native/scripted/neural physical reset differs")
        else:resets[case["name"]] = physical
        observations = bc.canonical.read_jsonl(path/"observations.jsonl")
        for row in observations:no_oracle_features(row)
        mechanics = bc.canonical.read_jsonl(path/"events.jsonl")
        controls = bc.canonical.read_jsonl(path/"body-control-events.jsonl")
        pairs = bc.timed_pairs(bc.canonical.read_jsonl(path/"ipc.jsonl"))
        visibility = visibility_diagnostics(mechanics,record["initial"],controls,observations)
        integrity=capture_integrity(record,mechanics,pairs) if phase!="probe" or record.get("perception_evidence",{}).get("final_native_status") is not None else None
        combat=bc.linked_mechanics(record["initial"],record["final"],controls,mechanics)
        require(not combat["issues"], "native physical damage/defense proof differs")
        require(not bc.command_evidence(controls,observations,pairs,record["final"])["issues"], "diagnostic commands/masks/native lifecycle differ")
        runtime,timing,fallback=None,None,None
        if policy in ("gru","mlp"):
            model_root=reference if phase == "probe" else root
            require(record["checkpoint_sha256"] == digest(model_root/"models"/policy/"best.pt") and
                    (phase != "probe" or record["checkpoint_sha256"] == config["reference"][policy+"_checkpoint_sha256"]),
                    "diagnostic phase changed its fixed checkpoint")
            normalizer = read(model_root/"dataset/normalization.json")
            require(record["normalizer_sha256"] == digest(model_root/"dataset/normalization.json"), "phase changed fixed normalizer")
            runtime = runtime_evidence(path,record,normalizer,observations,pairs,controls,shadow=phase=="shadow")
            if phase in ("realtime","screening"):
                fallback=bc.native_fallback_boundary(record["initial"],record["final"],controls,mechanics)
                credited=not any(fallback.values()) and not record.get("fallback_events") and record["final"]["outcome"]!="control_lost"
                require(record.get("no_fallback_credit")==credited,"diagnostic model credit contradicts actual native handoff")
            if phase=="realtime":timing=latency_evidence(path,record,observations,pairs)
        if phase=="shadow":
            require(record.get("shadow") is (record["policy"] != "native") and record.get("no_fallback_credit") is False and
                    not any(event.get("entity_id")==record["initial"]["primary_entity_id"] and event.get("phase") in ("accepted","started") for event in controls) and
                    not any(request.get("cmd") in ("body_acquire","body_action") and request.get("entity_id")==record["initial"]["primary_entity_id"] for request,_,_,_ in pairs),
                    "shadow acquired or commanded the native actor")
        if record["final"]["outcome"] == "control_lost":
            require(not bc.control_loss_issues({"scope":"paired","provider":"scripted"},record["initial"],record["final"],controls,observations,mechanics), "LOS truncation lost native boundary proof")
            require(any(row.get("source_phase") == "controller_release_visibility" and row.get("entity_id") == record["initial"]["primary_entity_id"] and
                        row.get("t_game") == record["final"]["game_ms"] and row.get("controller_visible") is False for row in mechanics), "control loss has no actual forced BIP/LOS boundary diagnostic")
        for filename in ("events.jsonl","observations.jsonl","ipc.jsonl","body-control-events.jsonl","arena-events.jsonl","policy-inputs.jsonl","latency.jsonl"):
            hashes[str((path/filename).relative_to(root))] = digest(path/filename)
        outcome=record["final"]["outcome"]
        if outcome=="task_success":
            if case["task"]=="distance":require(bc.distance(record["final"])<=220.001 and record["final"].get("distance_held_ms",0)>=200,"distance success lacks held native positions")
            elif case["task"]=="attack":require(combat["linked_attack_hits"]>0 and combat["linked_opponent_hp_loss"]>0,"attack success lacks real applied hit/HP loss")
            elif case["task"]=="block":require(combat["linked_blocked_hits"]>0 and bc.actors(record["final"])["primary"]["hp"]==bc.actors(record["initial"])["primary"]["hp"],"block success lacks actual blocked hit with HP preserved")
            else:raise ValueError("duel/reset stress cannot claim micro task_success")
        elif outcome=="death_or_down":require(any(actor["hp"]<=0 or actor["down"] for actor in record["final"]["actors"]),"native death/down lacks down actor")
        elif outcome=="time_limit":require(record["final"]["elapsed_game_ms"]>=case["timeout_ms"],"native time limit preceded its declared deadline")
        else:require(outcome=="control_lost","unrecognized actual diagnostic outcome")
        success=(outcome=="death_or_down" and record["final"].get("reason")=="opponent_down") if case["task"]=="native_duel" else outcome=="task_success"
        credit=phase in ("realtime","screening") and (policy=="native" or record.get("no_fallback_credit") is True)
        episodes.append({"name":record["name"],"policy":policy,"task":case["task"],"outcome":outcome,"visibility":visibility,"runtime":runtime,
                         "latency":timing,"native_fallback_boundary":fallback,"quality_credit":credit,"credited_success":bool(credit and success)})
        episodes[-1]["capture_integrity"]=integrity
        episodes[-1]["softlocks"]=finite_softlocks(mechanics,controls,record["final"]) if policy in ("gru","mlp") and phase!="shadow" else None
    require(suite.get("resources") and all(sample.get("forbidden_device_descriptors") == sample.get("display_socket_descriptors") == []
            for sample in suite["resources"]), "headless process device evidence missing")
    return {"phase":phase,"episodes":episodes,"raw_sha256":hashes,"quality_credit":False}


def report(root,config_path,phase,reference,native_reference=None):
    config = read(config_path)
    issues,quality_issues,evidence=[],[],{}
    service_report=service_gate_summary(config)
    def gate(name,operation):
        try:evidence[name]=operation();return evidence[name]
        except (OSError,ValueError,KeyError,TypeError,IndexError) as error:issues.append(name+": "+str(error));return None
    gate("configuration",lambda:validate_config(config));gate("frozen_sources",lambda:frozen_sources(root))
    if config_path.resolve() != (root/"code/evals/npc-body/body-stage8-v1.json").resolve():issues.append("configuration: stage8 config is not frozen in run")
    if phase!="full":
        value=gate(phase,lambda:phase_evidence(root,phase,config,reference))
        full_ready=False;implementation=not issues
        pending=["fresh-frozen-checkpoint-screening","healthy-control95percent","realtime-and-defense-window",
                 "four-actual-neural-owners-same-world","fault-injection","same-run-protected-service-load"]
    else:
        native_reference=native_reference or Path.home()/"gothic-ai/body-headless/runs/stage3-v1/native-off"
        gate("native_safety",lambda:native_safety_evidence(root,config,native_reference))
        dataset=gate("dataset",lambda:selection_dataset(root,config,reference))
        if dataset is not None:
            data_evidence,data,adapter=dataset;evidence["dataset"]=data_evidence
            gate("engine_compatibility",lambda:engine_compatibility_evidence(root,config,reference))
            gate("demonstrations",lambda:demonstration_matrix(root,config,reference))
            gate("models",lambda:training_evidence(root,config,data,adapter))
        phases={name:gate(name,lambda name=name:phase_evidence(root,name,config,reference)) for name in ("probe","shadow","realtime","screening")}
        for name,value in phases.items():
            if value is not None and any(row.get("capture_integrity") is None for row in value["episodes"]):issues.append(name+": complete final native capture integrity missing")
            if value is not None:
                for row in value["episodes"]:
                    if row["policy"]==config["candidate_policy"] and (row.get("softlocks") or {}).get("violations"):
                        quality_issues.append(row["name"]+": actual finite animation softlock")
        if phases["screening"] is not None:
            comparison,failures=combat_comparisons(phases["screening"]["episodes"],config)
            evidence["combat_quality"]=comparison;quality_issues.extend(failures)
        load=gate("four_actor_load",lambda:four_actor_evidence(root,config))
        workload=gate("protected_services",lambda:protected_service_load(root,config,load)) if load is not None else None
        if workload is not None:
            evidence["protected_services"],failures=workload
            service_report=service_gate_summary(config,evidence["protected_services"],failures)
            quality_issues.extend(service_report["combat_budget_issues"])
        if phases["realtime"] is not None:
            fault=gate("model_faults",lambda:{policy:bc.service_probe(root/"realtime"/("serve-probe-"+policy),digest(root/"models"/policy/"best.pt")) for policy in ("gru","mlp")})
            reaction=[];locks=[]
            for episode in phases["realtime"]["episodes"]:
                if episode["policy"]!=config["candidate_policy"]:continue
                path=root/"realtime"/episode["name"];record=read(path/"episode.json")
                mechanics=bc.canonical.read_jsonl(path/"events.jsonl");controls=bc.canonical.read_jsonl(path/"body-control-events.jsonl")
                proof=gate("defense-"+episode["name"],lambda: defense_reactions(path,record,mechanics,controls))
                if proof is not None:reaction.extend(proof["linked_reactions"])
                lock=gate("locks-"+episode["name"],lambda:finite_softlocks(mechanics,controls,record["final"]))
                if lock is not None:locks.extend(lock["violations"])
                latency=episode.get("latency") or {}
                for key,budget in (("model_step_ms","model_step_p95_ms"),("observation_to_arbitration_ms","observation_to_arbitration_p95_ms"),
                                   ("collector_to_command_send_ms","collector_to_command_send_p95_ms")):
                    if latency.get(key) is None or latency[key]["p95"]>config["quality"][budget]:quality_issues.append(episode["name"]+": "+key+" exceeds budget or has no samples")
                fraction=(episode.get("runtime") or {}).get("neural_control_fraction")
                if fraction is None or fraction<config["quality"]["minimum_healthy_neural_control_fraction"]:
                    quality_issues.append(episode["name"]+": realtime healthy-control below95percent")
            if not reaction:quality_issues.append("no real observed windup→defense start→native incoming hit proof")
            if locks:quality_issues.append("actual finite animation softlock violations: "+str(len(locks)))
            evidence["defense_window"]={"real_linked_reactions":reaction,"synthetic_deadline_claim":False}
            evidence["softlocks"]={"violations":locks,"maximum":config["quality"]["maximum_softlocks"]}
        if load is not None:
            for record in load["cases"]:
                if record["policy"]!=config["candidate_policy"]:continue
                for key,budget in (("model_step_ms","model_step_p95_ms"),("observation_to_arbitration_ms","observation_to_arbitration_p95_ms"),
                                   ("collector_to_command_send_ms","collector_to_command_send_p95_ms")):
                    timing=(record.get("latency") or {}).get(key)
                    if timing is None or timing["p95"]>config["quality"][budget]:quality_issues.append(record["name"]+": four-actor "+key+" exceeds budget")
                fraction=(record.get("runtime") or {}).get("neural_control_fraction")
                if fraction is None or fraction<config["quality"]["minimum_healthy_neural_control_fraction"]:quality_issues.append(record["name"]+": four-actor healthy-control below95percent")
        gate("resources",lambda:resource_evidence(root,config))
        implementation=not issues;full_ready=implementation and not quality_issues;pending=[] if full_ready else [*issues,*quality_issues]
    return {"schema_version":1,"kind":"body_combat_stage8_report","scope":phase,"phase_ready":implementation,
            "implementation_ready":implementation,"stage8_ready":full_ready,"checkpoint_ready":full_ready,"combat_gate_ready":full_ready,
            "service_gate_ready":service_report["service_gate_ready"],"service_gate_role":service_report["service_gate_role"],
            "service_report":service_report,"release_ready":False,
            "issues":issues,"checkpoint_issues":quality_issues,"config_sha256":digest(config_path),"reporter_sha256":digest(Path(__file__)),
            "evidence":evidence,"remaining_gates":pending,"limitations":[
                "Perception/shadow probes do not establish learner quality or checkpoint readiness.",
                "Twenty fresh screening seeds per task are stage8 admission; the later 100-seed release gate remains outstanding."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run",type=Path);parser.add_argument("--phase",choices=("probe","shadow","realtime","screening","full"),default="probe")
    parser.add_argument("--config",type=Path,required=True);parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--reference-run",type=Path)
    parser.add_argument("--native-reference",type=Path)
    args = parser.parse_args();root = args.run.resolve()
    value = report(root,args.config.resolve(),args.phase,(args.reference_run or root.parent/"stage7-bc-v9").resolve(),args.native_reference)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/"body-combat.json").write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n")
    (args.output_dir/"body-stage8-services.json").write_text(json.dumps(value["service_report"],ensure_ascii=False,indent=2)+"\n")
    (args.output_dir/"body-combat.md").write_text("# Stage 8 independent evidence\n\nImplementation ready: **"+str(value["implementation_ready"])+"**. Stage8 checkpoint ready: **"+str(value["checkpoint_ready"])+"**. Release ready: **False**.\n\n"+"\n".join("- "+issue for issue in value["issues"]+value["checkpoint_issues"])+"\n")
    return 0 if value["phase_ready"] else 2


if __name__ == "__main__":raise SystemExit(main())
