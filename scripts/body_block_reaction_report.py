#!/usr/bin/env python3
"""Read-only independent audit of the separately preregistered block diagnostic.

Known seed 223 is a mechanical diagnostic. Its results do not replace the
neutral v6 failures, select weights, or establish realtime/screening quality.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tarfile


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, "missing frozen audit helper")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def source_order_evidence(archive_path, declaration, manifest, snapshot):
    """Preserve the old lexical inventory; derive the actual native Path order."""
    files={}
    with tarfile.open(archive_path,"r:gz") as archive:
        for member in archive:
            path=Path(member.name)
            if path.parts and path.parts[0]=="OpenGothic" and member.isfile():
                require(".." not in path.parts and str(path) not in files,"ambiguous source archive")
                source=archive.extractfile(member);require(source is not None,"missing source archive bytes")
                h=hashlib.sha256()
                for block in iter(lambda:source.read(1024*1024),b""):h.update(block)
                files[str(path)]=h.digest()
    require(files,"source archive contains no native code")
    def ordered(key):
        h=hashlib.sha256()
        for name in sorted(files,key=key):h.update(name.encode());h.update(files[name])
        return h.hexdigest()
    lexical,native=ordered(lambda name:name),ordered(Path)
    runtime=declaration["updated_runtime"]
    require(runtime["source_files_hashed"]==manifest["source_files_hashed"]==len(files) and
            manifest["source_sha256"]==native,"captured native source differs from immutable archive Path-order provenance")
    correction=None
    if runtime["source_sha256"]!=native:
        require(runtime["source_sha256"]==lexical,"source mismatch is not the proved lexical-versus-Path ordering defect")
        correction=read(snapshot).get("source_provenance_correction")
        require(isinstance(correction,dict) and correction.get("reason")=="native_Path_component_order_vs_declaration_lexical_order",
                "source-order repair is not separately declared")
        failed=correction.get("original_failed_report",{})
        require(isinstance(failed.get("path"),str) and Path(failed["path"]).is_file() and
                digest(failed["path"])==failed.get("sha256") and read(failed["path"]).get("diagnostic_complete") is False,
                "original source-order audit failure missing/changed")
    else:
        require(runtime["source_sha256"]==native,"declared native source differs")
    return {"source_archive_sha256":digest(archive_path),"source_files":len(files),
            "declared_source_sha256":runtime["source_sha256"],"lexical_archive_inventory_sha256":lexical,
            "actual_native_Path_order_source_sha256":native,"correction":correction,
            "native_source_bytes_changed":False}


def audit_sources(root, snapshot):
    """The repaired reader has its own frozen inventory, separate from capture."""
    binding = read(snapshot)
    require(binding.get("schema_version") == 1 and binding.get("kind") == "body_block_reaction_audit_snapshot" and
            binding.get("run_environment_sha256") == digest(root / "environment.json") and
            binding.get("declaration_sha256") == digest(root / "evaluation-repair.json"), "external reaction auditor binding differs")
    here = Path(__file__).resolve(); directory = snapshot.resolve().parent
    require(here.is_relative_to(directory) and binding.get("files", {}).get(str(here.relative_to(directory))) == digest(here),
            "executed reaction auditor is not the separately frozen reader")
    for name, sha in binding["files"].items():
        path = (directory / name).resolve()
        require(path.is_relative_to(directory) and path.is_file() and digest(path) == sha, "reaction audit executable changed")
    repair = load_module("frozen_evaluation_repair", root / "code/scripts/body_evaluation_repair.py")
    declaration = repair.validate_declaration(root)
    reference = Path(declaration["original_run"])
    original = read(reference / "environment.json")
    for name in reversed(("code/body-service/src", "code/scripts")):
        sys.path.insert(0, str(reference / name))
    import body_bc_report as bc
    import body_combat_report as combat
    for module in (bc, combat, bc.canonical, *[sys.modules[name] for name in
            ("body_arena_report", "body_api_report", "body_trajectory_report") if name in sys.modules]):
        path = Path(module.__file__).resolve()
        require(path.is_relative_to(reference / "code") and original["frozen_files"].get(str(path.relative_to(reference))) == digest(path),
                "reaction reader imported an unfrozen/updated model-data helper")
    return declaration, bc, combat, {"snapshot_sha256": digest(snapshot), "reporter_sha256": digest(here),
                                     "declaration_sha256": digest(root / "evaluation-repair.json")}


def same_request(request, raw):
    return request == {key: value for key, value in raw.items() if key != "id"}


def prepared_evidence(path, record, config, pairs, observations, controls, mechanics, bc, *, mode="lockstep"):
    proof, activation = read(path / "preparation.json"), read(path / "activation.json")
    require(activation == record["activation"] and activation["prepared_start"] == record["prepared_start"] and
            activation.get("preparation_file") == "preparation.json" and
            activation.get("preparation_sha256") == digest(path / "preparation.json"), "prepared scene metadata changed")
    initial, prepared, active, anchor = record["initial"], proof["prepared_before_activation"], record["prepared_start"], proof["anchor"]
    primary, opponent = initial["primary_entity_id"], initial["opponent_entity_id"]
    require(proof.get("reset_ready") == initial and proof.get("native_reaction") == prepared.get("reaction"), "copied prepared snapshot differs")
    for arena in (prepared, active):
        require(all(arena.get(key) == initial[key] for key in ("session_epoch", "world_epoch", "primary_entity_id", "opponent_entity_id")),
                "reaction changed physical scene identity")
    reaction = prepared["reaction"]
    require(reaction.get("enabled") is True and reaction.get("phase") == "armed" and reaction.get("periodic") is True and
            reaction.get("game_ms") == anchor.get("t_game_ms") == prepared["game_ms"] and
            reaction.get("sim_step") == anchor.get("sim_step") and reaction.get("observation_seq") == anchor.get("observation_seq"),
            "reaction was not armed on a naturally current periodic row")
    require(anchor.get("phase") == "post_simulation_post_animation" and anchor.get("periodic") is True and
            anchor.get("event_only") is False and anchor.get("entity_id") == primary and anchor.get("native_decision_eligible") is True and
            anchor.get("can_decide") is True and anchor.get("action_mask_valid") is True,
            "prepared defender is not physically eligible at an actual periodic source")
    target = anchor["target"]
    require(target.get("entity_id") == opponent and all(target.get(key) is True for key in
            ("present", "visible", "current_valid", "in_w_range", "melee_line_clear", "attack_anim", "prehit")),
            "first defender decision has no genuine visible in-range incoming attack")
    require(anchor["self"].get("attack_anim") is False and anchor["self"].get("defence_active") is False and
            anchor["self"].get("hp") == anchor["self"].get("hp_max") == target.get("hp") == target.get("hp_max") == 300,
            "preparation changed health or performed a defender action")
    require(proof.get("anchor_sha256") == json_digest(anchor) and activation.get("first_sequence_dt_game_ms") ==
            proof.get("first_sequence_dt_game_ms") == 0 and activation.get("initial_game_ms") == anchor["t_game_ms"], "cold-start prepared clock differs")
    require(0 < anchor["t_game_ms"]-initial["game_ms"] <= config["preparation"]["max_game_ms"] and
            proof.get("prepared_game_ms") == anchor["t_game_ms"] and
            proof.get("preparation_game_ms") == anchor["t_game_ms"]-initial["game_ms"], "held preparation exceeds preregistered budget")
    wire, wire_proof = bc.wire_observation_sources(observations, pairs)
    key = bc.canonical.identity(anchor)+(anchor["observation_seq"],)
    require(wire.get(key) == anchor, "prepared anchor differs from exact received engine IPC")
    before, after = proof["primary_before"], proof["primary_prepared"]
    for actor in (before, after):
        require(any(request.get("cmd") == "body_control_status" and actor in response.get("actors", []) for request,response,_,_ in pairs),
                "prepared primary status has no raw engine receipt")
    require(before["purity_state"]["rng_state"] == after["purity_state"]["rng_state"] and
            before["purity_state"]["queue"] == after["purity_state"]["queue"] == [0]*6 and
            before["control_generation"] == after["control_generation"] == anchor["control_generation"],
            "held primary RNG, fight queue or owner changed")
    require(proof.get("primary_commands_before_activation") == proof.get("primary_native_choices_before_activation") == 0 and
            proof.get("primary_rng_unchanged") is True and proof.get("preparation_quality_credit") is False,
            "preparation was credited as a learned action")
    activation_pairs = [(request,response,before,after) for request,response,before,after in pairs if request.get("cmd") == "arena_reaction_activate"]
    require(len(activation_pairs) == 1, "reaction activation missing/duplicated")
    request, receipt, activate_begin, _ = activation_pairs[0]
    require(same_request(activation["request"], request) and receipt == activation["response"] and receipt.get("ok") is True and
            receipt.get("arena") == active, "reaction activation metadata differs from actual raw API receipt")
    require(all(activation["request"].get(key) == anchor.get(key) for key in ("session_epoch", "world_epoch", "observation_seq")) and
            activation["request"].get("issued_game_ms") == anchor["t_game_ms"] and
            activation["request"].get("primary_entity_id") == primary and activation["request"].get("opponent_entity_id") == opponent and
            activation["request"].get("primary_control_generation") == anchor["control_generation"] and
            activation["request"].get("provider") == ("native" if record["policy"] == "native" else "scripted") and
            activation["request"].get("mode") == mode and mode in ("lockstep","realtime"), "activation changed prepared IDs/source/provider/mode")
    require(active["game_ms"] == prepared["game_ms"] and active["reaction"].get("phase") == "active" and
            active["reaction"].get("activation_game_ms") == anchor["t_game_ms"] and
            active["reaction"].get("activation_provider") == activation["request"]["provider"], "activation advanced/rewrote physical state")
    require(activation["request"].get("opponent_control_generation") == proof["attacker_started"]["control_generation"] and
            active["reaction"].get("activation_sim_step") == anchor["sim_step"], "activation changed attacker lease or actual simulation step")
    physical = ("pos","yaw_deg","hp","hp_max","str","dex","onehand","onehand_talent","edge_protection","blunt_protection",
                "weapon_symbol","weapon","armor_symbol","armor","weapon_damage","weapon_length","immortal","body_state","pose",
                "segment_remaining_ms","attack_anim","prehit","defence","jump_back","waits_ms","target_entity_id")
    for actor in prepared["actors"]:
        after_actor = next(x for x in active["actors"] if x["entity_id"] == actor["entity_id"])
        require(all(actor[key] == after_actor[key] for key in physical), "activation changed actual pose, physics, health, equipment or native locks")
    previous = [(request,response) for request,response,_,end in pairs if end <= activate_begin]
    for request, response in previous:
        require(not (request.get("cmd") == "body_action" and request.get("entity_id") == primary and response.get("accepted") is True),
                "defender action before reaction activation")
    mapped = {row.get("actor_id") for row in mechanics if row.get("phase") == "arena_actor_mapping" and row.get("observation_entity_id") == primary}
    require(len(mapped) == 1 and None not in mapped, "prepared defender lacks exact native actor mapping")
    for row in mechanics:
        if row.get("t_game", 0) > anchor["t_game_ms"]: continue
        require(not (row.get("phase") in ("native_choice", "native_consume") and row.get("actor_id") in mapped), "defender native brain ran before activation")
        require(row.get("phase") not in ("hit_attempt", "damage", "defence_result") or row.get("t_game", 0) <= initial["game_ms"],
                "incoming hit happened before the reactive evaluation boundary")
    for arena in (prepared, active):
        require(all(actor.get("hp") == 300 and all(actor.get(key) == 0 for key in ("received_damage", "outgoing_damage", "blocked_hits"))
                    for actor in arena["actors"]), "preparation/activation manufactured HP, hit or block evidence")
    start, accept = proof["attacker_started"], proof["attacker_accepted"]
    require(start in controls and accept in controls and start.get("phase") == "started" and accept.get("phase") == "accepted" and
            start.get("entity_id") == accept.get("entity_id") == opponent and start.get("action_id") == accept.get("action_id") == 6 and
            all(start.get(key) == accept.get(key) for key in (*bc.canonical.IDENTITY,"command_id","execution_id")),
            "prepared incoming attack lacks the actual accepted/started lifecycle")
    for event_field, armed_field in (("command_id", "opponent_command_id"), ("execution_id", "opponent_execution_id"),
            ("t_game_ms", "opponent_started_game_ms"), ("sim_step", "opponent_started_sim_step"), ("event_seq", "opponent_started_event_seq")):
        require(start.get(event_field) == reaction.get(armed_field), "armed native attacker execution differs")
    normalizer = read(Path(config["_reference_run"]) / "dataset/normalization.json")
    features = bc.canonical.pack_features(anchor, normalizer, sequence_dt_game_ms=0)
    require(len(features) == proof.get("prepared_anchor_feature_count") == 184 and
            proof.get("normalizer_sha256") == config["reference"]["normalizer_sha256"] and
            proof.get("prepared_anchor_normalized_features_sha256") == json_digest(features), "prepared 184-feature dt0 source differs")
    return {"prepared_game_ms": anchor["t_game_ms"], "anchor_observation_seq": anchor["observation_seq"],
            "feature_sha256": json_digest(features), "attacker_started_game_ms": start["t_game_ms"],
            "first_dt_ms": 0, "prep_model_updates": 0, "prep_primary_actions": 0, "prep_rng_unchanged": True,
            "native_to_wire": wire_proof}, wire, features


def command_fallback_cuts(record, controls, pairs, bc):
    """An aborted diagnostic fragment never earns healthy model-control credit."""
    cuts=[]
    for fallback in record.get("fallback_events",[]):
        if fallback.get("kind")!="model_fallback":continue
        request,receipt=fallback.get("request",{}),fallback.get("response",{})
        if request.get("cmd")!="body_release" or receipt.get("accepted") is not True:continue
        actual=[(raw,response,begin,end) for raw,response,begin,end in pairs
                if same_request(request,raw) and response==receipt]
        require(len(actual)==1,"diagnostic model release lacks unique actual raw receipt")
        _,_,begin,end=actual[0];identity=bc.canonical.identity(request)
        refused=[(raw,response) for raw,response,_,finished in pairs if raw.get("cmd")=="body_action" and
                 bc.canonical.identity(raw)==identity and finished<=begin and response.get("accepted") is False and
                 response.get("reason") in ("stale_observation","expired","expired_wall","target_invalid","command_busy",
                                             "release_pending","not_owned","stale_generation")]
        if not refused:continue  # A runtime-step fallback is handled by its own recorded response.
        latest,refusal=refused[-1]
        require(fallback.get("reason")=="realtime_decision_rejected:"+refusal["reason"],"diagnostic cut names another engine refusal")
        ownership=[event for event in controls if event.get("phase")=="ownership" and
                   event.get("reason") in ("release_pending","native_restored") and bc.canonical.identity(event)==identity and
                   begin*1e9<=event.get("wall_monotonic_ns",0)<=end*1e9 and event.get("release_reason")=="released"]
        require(ownership,"diagnostic cut lacks actual release ownership boundary")
        cuts.append({"identity":identity,"release_game_ms":min(event["t_game_ms"] for event in ownership),
                     "release_response_wall":end,"rejected_command_id":latest["command_id"],"reason":refusal["reason"],
                     "ownership_event_seq":[event["event_seq"] for event in ownership],"quality_credit":False})
    return cuts


def after_command_cut(row,cuts,pairs,bc):
    for cut in cuts:
        if bc.canonical.identity(row)!=cut["identity"] or row["t_game_ms"]<cut["release_game_ms"]:continue
        if any(request.get("cmd")=="body_observations" and finished>=cut["release_response_wall"] and
               any(bc.json_digest(candidate)==bc.json_digest(row) for candidate in response.get("observations",[]))
               for request,response,_,finished in pairs):return True
    return False


def model_history(path, record, prep, wire, controls, pairs, bc, normalizer):
    entries = bc.canonical.read_jsonl(path / "policy-inputs.jsonl")
    require(digest(path / "policy-inputs.jsonl") == record["model_journal_sha256"], "reaction model journal changed")
    if record["policy"] == "native":
        require(not any(row.get("kind") in ("model_input", "arbitration", "terminal_barrier") for row in entries), "native diagnostic used neural memory/commands")
        return {"processed_inputs": 0, "runtime_fallbacks": 0, "eligible_opportunities": 0, "accepted_model_commands": 0, "healthy_fraction": None}
    anchor = read(path / "preparation.json")["anchor"]; processed = {}; previous = None; accepted = 0; opportunities = 0
    cuts=command_fallback_cuts(record,controls,pairs,bc)
    fallback = []; closed = False; activations = 0; attempted = set()
    for entry in entries:
        kind = entry.get("kind")
        require(not closed, "policy history after terminal barrier")
        if kind in ("preparation_start", "attacker_command"):
            require(not processed and activations == 0 and entry.get("quality_credit") is False, "preparation updated/credited neural policy")
            continue
        if kind == "excluded_observation":
            row = entry["observation"]; key = bc.canonical.identity(row)+(row["observation_seq"],)
            require(wire.get(key) == row, "excluded history row differs from actual source")
            if entry.get("reason") == "before_reaction_scene_boundary":
                require(not processed and activations == 0 and row["t_game_ms"] <= anchor["t_game_ms"] and entry.get("quality_credit") is False,
                        "postactivation row silently removed as preparation")
            else:
                require(entry.get("reason") == "outside_policy_generation" and
                        (row["control_generation"] != anchor["control_generation"] or after_command_cut(row,cuts,pairs,bc)),
                        "owned periodic history silently excluded")
            continue
        if kind == "reaction_activation":
            require(not processed and activations == 0 and {k:v for k,v in entry.items() if k!="kind"} == record["activation"], "model activation anchor differs")
            activations += 1; continue
        require(activations == 1, "neural history occurred before explicit reactive activation")
        if kind == "terminal_barrier":
            request, response = entry["request"], entry["response"]
            require(request.get("session_epoch") == record["final"]["session_epoch"] and request.get("world_epoch") == record["final"]["world_epoch"] and
                    request.get("final_game_ms") == record["final"]["game_ms"] and response.get("accepted") is True and
                    response.get("model_processed") is False and response.get("hidden_reset") is True and
                    response.get("runtime_after_clear", {}).get("actors") == 0 and response["runtime_after_clear"].get("episode_closed") is True,
                    "reaction terminal failed to close real recurrent history")
            before = bc.artifact(path, request["final_before_clear_file"])
            require(digest(before) == request["final_before_clear_sha256"] and read(before).get("final") == record["final"] and
                    read(before).get("runtime_status") == response.get("runtime_before_clear"), "terminal snapshot was taken after model memory clear")
            closed = True; continue
        require(kind in ("model_input", "arbitration"), "unknown reaction model journal phase")
        request, response = entry.get("request", {}), entry["response"]
        row = request["observation"] if kind == "model_input" else entry["observation"]
        key = bc.canonical.identity(row)+(row["observation_seq"],)
        require(wire.get(key) == row and row.get("periodic") is True and row.get("phase") == "post_simulation_post_animation" and
                row.get("entity_id") == anchor["entity_id"] and row["t_game_ms"] >= anchor["t_game_ms"], "model source differs from actual prepared periodic stream")
        require(not after_command_cut(row,cuts,pairs,bc),"model advanced history after actual command-fallback cut")
        if kind == "arbitration": opportunities += 1
        if response.get("status") == "fallback":
            require(response.get("model_processed") is not True and isinstance(response.get("reason"), str), "fallback fabricated a model step")
            fallback.append({"kind":kind,"reason":response["reason"],"game_ms":row["t_game_ms"]});continue
        if kind == "model_input":
            require(key not in attempted and response.get("status") == "processed" and response.get("model_processed") is True,
                    "recurrent observation duplicated or did not execute")
            attempted.add(key)
            sequence = 1 if previous is None else previous["sequence"]+1
            elapsed = row["t_game_ms"]-(anchor["t_game_ms"] if previous is None else previous["game_ms"])
            require(previous is not None or row == anchor, "first input skipped or rewrote the prepared anchor")
            require(request.get("stream_seq") == response.get("stream_seq") == sequence and response.get("sequence_dt_game_ms") == elapsed and
                    response.get("hidden_reset") is (previous is None or previous["identity"] != bc.canonical.identity(row)), "reaction recurrent reset/step/clock differs")
            require(previous is None or row["t_game_ms"]//50 <= previous["game_ms"]//50+1, "reaction periodic history lost a sampling bucket")
            features = bc.canonical.pack_features(row, normalizer, sequence_dt_game_ms=elapsed)
            require(len(features) == 184 and response.get("features_sha256") == json_digest(features) and response.get("observation_sha256") == json_digest(row) and
                    response.get("raw_action_mask") == row["action_mask"] and response.get("effective_action_mask") == bc.effective_mask(row),
                    "reaction model features/masks drifted from immutable training/runtime packer")
            if previous is None:require(response["features_sha256"] == prep["feature_sha256"], "first dt0 model features differ from prepared proof")
            require(response.get("checkpoint_sha256") == record["checkpoint_sha256"] and response.get("normalizer_sha256") == record["normalizer_sha256"], "reaction changed frozen weights/norm")
            bc.runtime_decision(row, request, response)
            processed[key] = response; previous = {"sequence": sequence,"identity": bc.canonical.identity(row),"game_ms": row["t_game_ms"]}
        else:
            require(key in processed and response.get("model_processed") is False and response.get("hidden_reset") is False and
                    response.get("features_sha256") == processed[key]["features_sha256"] and response.get("periodic_observation_seq") == row["observation_seq"] and
                    response.get("periodic_game_ms") == row["t_game_ms"] and response.get("action_id") == processed[key]["action_id"], "arbitration changed recurrent periodic choice")
            command, receipt = entry.get("command_request"), entry.get("command_response")
            require(isinstance(command, dict) and isinstance(receipt, dict) and command.get("action_id") == response.get("action_id") and
                    command.get("observation_seq") == row["observation_seq"] and command.get("issued_game_ms") == row["t_game_ms"] and
                    all(command.get(k) == row[k] for k in bc.canonical.IDENTITY) and
                    any(same_request(command, raw) and actual == receipt for raw,actual,_,_ in pairs), "model command lacks exact current raw engine receipt")
            if receipt.get("accepted") is True:
                require(entry.get("command_id") == command["command_id"] and entry.get("execution_id") == receipt.get("execution_id"), "model command acceptance fabricated")
                accepted += 1
            else:
                require(receipt.get("accepted") is False and entry.get("command_id") is None, "rejected command credited as accepted")
    require(closed and processed, "reaction history lacks first input or terminal memory reset")
    expected = {key for key,row in wire.items() if row.get("entity_id") == anchor["entity_id"] and
                row.get("control_generation") == anchor["control_generation"] and row.get("periodic") is True and
                anchor["t_game_ms"] <= row["t_game_ms"] <= record["final"]["game_ms"]}
    if not fallback:
        expected={key for key in expected if not after_command_cut(wire[key],cuts,pairs,bc)}
        require(set(processed) == expected, "reaction recurrent stream skipped/coalesced an actual owned periodic input")
    stats = record["model_stats"]
    require(stats.get("model_processed", 0) == len(processed) and stats.get("accepted_model_commands", 0) == accepted and
            stats.get("latest_eligible_periodic_opportunities", 0) == opportunities, "reaction control fraction summary differs from actual decisions")
    fraction = accepted/opportunities if opportunities else None
    require(record.get("neural_control_fraction") == fraction, "healthy reaction control fraction fabricated")
    return {"processed_inputs": len(processed), "runtime_fallbacks": len(fallback), "fallbacks": fallback,
            "declared_runtime_fallbacks": stats.get("runtime_fallbacks",0), "eligible_opportunities": opportunities,
            "accepted_model_commands": accepted,"healthy_fraction": fraction, "terminal_memory_reset": True,
            "command_fallback_cuts":cuts}


def mechanical_success(final, linked):
    primary = next(actor for actor in final["actors"] if actor["entity_id"] == final["primary_entity_id"])
    return (final.get("outcome") == "task_success" and final.get("reason") == "melee_hit_blocked" and
            primary.get("hp") == 300 and primary.get("received_damage") == 0 and primary.get("blocked_hits",0) >= 1 and
            linked.get("linked_blocked_hits",0) >= 1)


def model_credit(record, mechanical, runtime, reactions, controls, bc, mechanics, action=9):
    if record["policy"] == "native": return False
    boundary = bc.native_fallback_boundary(record["initial"], record["final"], controls, mechanics)
    return bool(mechanical and not boundary["restorations_before_final"] and not boundary["same_final_native_continuations"] and not record.get("fallback_events") and
                runtime["runtime_fallbacks"] == runtime["declared_runtime_fallbacks"] == 0 and
                runtime["healthy_fraction"] is not None and runtime["healthy_fraction"] >= .95 and
                any(row["action_id"] == action for row in reactions["linked_reactions"]))


def report(root, snapshot):
    declaration, bc, combat, source = audit_sources(root, snapshot)
    reference = Path(declaration["original_run"]); config = read(root / "code/evals/npc-body/body-block-reaction-v1.json")
    config["_reference_run"] = str(reference)
    directory = root / "block-reaction"; suite = read(directory / "suite.json")
    result = {"kind":"body_block_reaction_independent_report","schema_version":1,"original_v6_block_failures":declaration["original_failures"]["original_gru_block"],
              "scope":"known_seed_diagnostic_only","stage8_quality_credit":False,"screening_gate_evaluated":False,
              "realtime_defense_gate_evaluated":False,"legacy_result_replacement":False,"provenance":source,"cases":[],"issues":[]}
    require(suite.get("kind") == "body_block_reaction_diagnostic" and suite.get("quality_gate_evaluated") is False and
            suite.get("original_v6_credit") is False, "known diagnostic changed its declared scope")
    if suite.get("status") != "collected" or suite.get("error"):
        result.update(status="infrastructure_failure", diagnostic_complete=False, error=suite.get("error"))
        result["raw_sha256"] = {str(p.relative_to(root)):digest(p) for p in directory.rglob("*") if p.is_file()}
        return result
    manifest = read(directory / "manifest.json")
    require(dt.datetime.fromisoformat(declaration["created_at_utc"]) <= dt.datetime.fromisoformat(manifest["recorded_at_utc"]), "reactive collection preceded preregistration")
    source_order=source_order_evidence(root/"source.tar.gz",declaration,manifest,snapshot)
    source["native_source_order"]=source_order
    require(manifest["binary_sha256"] == declaration["updated_runtime"]["binary_sha256"] and
            manifest["driver_sha256"] == declaration["modules"]["code/scripts/body_block_reaction.py"] and
            manifest["config_sha256"] == declaration["modules"]["code/evals/npc-body/body-block-reaction-v1.json"] and
            manifest.get("reference_run") == str(reference) and manifest.get("policy_code_root") == str(reference/"code") and
            manifest.get("checkpoint_sha256") == declaration["fixed_artifacts"]["checkpoint_sha256"], "reaction manifest escaped its separate engine/frozen model binding")
    original = read(reference / "environment.json")
    for entry in manifest["policy_modules"].values():
        file = Path(entry["path"]).resolve()
        require(file.is_relative_to(reference/"code") and original["frozen_files"].get(str(file.relative_to(reference))) == entry["sha256"] == digest(file),
                "reaction used updated policy/model helpers")
    expected = {case["name"]+"-"+policy:(case,policy) for case in config["cases"] for policy in config["policies"]}
    require(len(suite["cases"]) == len(expected) == 6 and {row["name"] for row in suite["cases"]} == set(expected), "known pair matrix incomplete/duplicated")
    features = {}
    for record in suite["cases"]:
        path = directory / record["name"]; case, policy = expected[record["name"]]
        require(read(path/"episode.json") == record and record["case"] == case and record["policy"] == policy and
                record.get("mode") == "lockstep" and record.get("original_v6_credit") is False, "known episode source differs from declared matrix")
        if policy != "native":
            require(record["checkpoint_sha256"] == declaration["fixed_artifacts"]["checkpoint_sha256"][policy] and
                    record["normalizer_sha256"] == declaration["fixed_artifacts"]["normalizer_sha256"], "known episode deployed different weights")
        observations = bc.canonical.read_jsonl(path/"observations.jsonl")
        pairs = bc.timed_pairs(bc.canonical.read_jsonl(path/"ipc.jsonl"))
        controls = bc.canonical.read_jsonl(path/"body-control-events.jsonl"); mechanics = bc.canonical.read_jsonl(path/"events.jsonl")
        arena = bc.canonical.read_jsonl(path/"arena-events.jsonl")
        require([row["arena"] for row in arena if row.get("kind") == "reset_ready"] == [record["initial"]] and
                [row["arena"] for row in arena if row.get("kind") == "terminal"] == [record["final"]], "known initial/final are not actual native snapshots")
        prepared, wire, packed = prepared_evidence(path, record, config, pairs, observations, controls, mechanics, bc)
        proof = read(path/"preparation.json")
        require([row["arena"] for row in arena if row.get("kind") == "reaction_armed"] == [proof["prepared_before_activation"]] and
                [row["arena"] for row in arena if row.get("kind") == "reaction_active"] == [record["prepared_start"]],
                "copied armed/activated state differs from authoritative native arena journal")
        features.setdefault(case["side"], []).append(packed)
        runtime = model_history(path, record, prepared, wire, controls, pairs, bc, read(reference/"dataset/normalization.json"))
        commands = bc.command_evidence(controls, observations, pairs, record["final"])
        linked = bc.linked_mechanics(record["initial"],record["final"],controls,mechanics)
        require(not commands["issues"] and not linked["issues"], "known commands/native HP/block counters fail independent reconciliation")
        integrity = combat.capture_integrity(record,mechanics,pairs)
        reactions = combat.defense_reactions(path,record,mechanics,controls)
        softlocks = combat.finite_softlocks(mechanics,controls,record["final"])
        require(not softlocks["violations"], "known reaction has a physical finite-segment softlock")
        mechanical = mechanical_success(record["final"],linked)
        primary = next(row for row in record["final"]["actors"] if row["entity_id"]==record["initial"]["primary_entity_id"])
        preserved = primary["hp"]==300 and primary["received_damage"]==0
        result["cases"].append({"name":record["name"],"policy":policy,"side":case["side"],"final":record["final"],
            "prepared":prepared,"runtime":runtime,"commands":commands,"linked_mechanics":linked,"capture_integrity":integrity,
            "defense_reactions":reactions,"softlocks":softlocks,"mechanical_block_success":mechanical,
            "model_block_credit":model_credit(record,mechanical,runtime,reactions,controls,bc,mechanics),
            "model_dodge_with_hp_preserved":model_credit(record,preserved,runtime,reactions,controls,bc,mechanics,action=5)})
    require(all(len(rows)==3 and all(row==rows[0] for row in rows) for rows in features.values()), "native/GRU/MLP prepared 184-feature starting conditions differ")
    result.update(status="complete",diagnostic_complete=True,paired_prepared_features_equal=True,
        mechanical_blocks_by_policy={policy:sum(row["mechanical_block_success"] for row in result["cases"] if row["policy"]==policy) for policy in config["policies"]},
        model_credited_blocks_by_policy={policy:sum(row["model_block_credit"] for row in result["cases"] if row["policy"]==policy) for policy in ("gru","mlp")})
    result["raw_sha256"] = {str(p.relative_to(root)):digest(p) for p in directory.rglob("*") if p.is_file()}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--audit-manifest",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    try:
        value=report(args.run.resolve(),args.audit_manifest.resolve())
    except (OSError,ValueError,KeyError,TypeError,StopIteration,tarfile.TarError) as error:
        value={"kind":"body_block_reaction_independent_report","schema_version":1,"status":"incomplete","diagnostic_complete":False,
               "issues":[str(error)],"stage8_quality_credit":False,"screening_gate_evaluated":False,"legacy_result_replacement":False}
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/"body-block-reaction.json").write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")
    print(json.dumps({key:value.get(key) for key in ("status","diagnostic_complete","issues","mechanical_blocks_by_policy","model_credited_blocks_by_policy")}))
    return 0 if value.get("diagnostic_complete") else 2


if __name__=="__main__":
    raise SystemExit(main())
