"""Read-only artifact admission checks; never import or execute a model."""
import importlib.util
import math
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("body_bc_report", Path(__file__).parents[1]/"body_bc_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def prediction(truth=1, chosen=1):
    mask = [False]*12
    mask[1] = mask[2] = True
    probabilities = [None]*12
    probabilities[chosen] = math.log(.75)
    probabilities[3-chosen] = math.log(.25)
    return {"true_action": truth, "chosen_action": chosen, "action_mask": mask,
            "action_log_probs": probabilities, "true_action_nll": -probabilities[truth], "train_class_weight": 1.}


def test_held_out_metrics_are_recomputed_from_real_masked_predictions():
    value = report.classification([prediction(), prediction(2, 1)], [1.]*12)
    assert value["count"] == 2 and value["accuracy"] == .5
    assert value["per_action"]["approach"]["tp"] == 1 and value["per_action"]["approach"]["fp"] == 1
    assert value["per_action"]["turn_to_target"]["support"] == 1 and value["per_action"]["turn_to_target"]["fn"] == 1
    assert value["macro_f1"] == pytest.approx((2/3)/11)
    assert value["unweighted_nll"] == pytest.approx((-math.log(.75)-math.log(.25))/2)


@pytest.mark.parametrize("failure", ["mask", "argmax", "probabilities", "class_weight", "continue_teacher"])
def test_prediction_claims_do_not_override_masks_probabilities_or_train_weights(failure):
    row = prediction()
    if failure == "mask": row["action_mask"][1] = False
    elif failure == "argmax": row["chosen_action"] = 2
    elif failure == "probabilities": row["action_log_probs"][1] = math.log(.9)
    elif failure == "class_weight": row["train_class_weight"] = 2.
    elif failure == "continue_teacher": row["true_action"] = 0
    with pytest.raises(ValueError): report.classification([row], [1.]*12)


def test_checkpoint_selection_uses_eval_f1_then_eval_nll_only():
    history = [{"epoch": i, "eval": {"macro_f1": f1, "nll": nll}}
               for i, f1, nll in [(0, .4, .3), (1, .5, .5), (2, .5, .4)]]
    assert report.choose_epoch(history) == 2
    history[0]["test_macro_f1"] = .99
    with pytest.raises(ValueError, match="test/OOD"):
        report.choose_epoch(history)


def historical_collector():
    return '''def main():
    try:
        runner.call({"cmd":"close"})
        runner.process.wait(timeout=15)
        if runner.process.returncode != 0:
            raise RecorderError("demonstration engine did not close cleanly")
        bind_closed_sources(args,suite["episodes"])
        for record in suite["episodes"]:
            approved = approve_scripted_demonstration(output,registry)
        if not args.smoke:
            dataset = build_dataset(episodes)
    finally:
        pass
'''


def test_offline_recovery_exit_inference_requires_guard_before_binding_and_all_approvals():
    assert report.closed_approval_flow(historical_collector()) is True
    unsafe=historical_collector().replace("runner.process.returncode != 0","runner.process.returncode == 0")
    assert report.closed_approval_flow(unsafe) is False
    missing=historical_collector().replace("        runner.process.wait(timeout=15)\n","")
    assert report.closed_approval_flow(missing) is False


def test_recovery_cannot_whitelist_a_collection_without_redundant_analysis_phase():
    source=historical_collector().replace("dataset = build_dataset(episodes)","raise RuntimeError('capture failed')")
    assert report.closed_approval_flow(source) is False


def snapshot_fixture():
    initial={"game_ms":0,"actors":[{"entity_id":4,"hp":300}]}
    final={"game_ms":100,"terminated":False,"truncated":True,"infrastructure":False,
           "outcome":"time_limit","reason":"time_limit","actors":[{"entity_id":4,"hp":230}]}
    record={"name":"demo","initial":initial,"final":final,"counts":{"observation":2}}
    manifest={"initial":initial,"final":report.compact_final_snapshot(final),"counts":record["counts"]}
    arena=[{"kind":"reset_ready","arena":initial},{"kind":"terminal","arena":final}]
    return record,manifest,arena


def test_compact_recorder_final_is_exact_projection_of_full_native_terminal():
    record,manifest,arena=snapshot_fixture()
    report.demonstration_snapshot_binding(record,dict(record),manifest,arena)
    assert manifest["final"]["t_game_ms"] == record["final"]["game_ms"]
    assert "actors" not in manifest["final"]


@pytest.mark.parametrize("failure",["compact_outcome","full_actor","saved_episode"])
def test_snapshot_binding_catches_semantic_drift_beyond_legitimate_projection(failure):
    import copy
    record,manifest,arena=snapshot_fixture();saved=copy.deepcopy(record)
    if failure=="compact_outcome":manifest["final"]["outcome"]="death_or_down"
    elif failure=="full_actor":
        arena[-1]["arena"]=copy.deepcopy(record["final"]);arena[-1]["arena"]["actors"][0]["hp"]=300
    else:saved["final"]["reason"]="another_reason"
    with pytest.raises(ValueError,match="differs|differ"):
        report.demonstration_snapshot_binding(record,saved,manifest,arena)


def test_bounded_population_statistics_match_independent_batch_population():
    samples=[{"position":1.e6+i*.25,"constant":1.} for i in range(40)]
    moments=report.PopulationMoments()
    for row in samples:moments.add(row)
    expected=sum(row["position"] for row in samples)/len(samples)
    variance=sum((row["position"]-expected)**2 for row in samples)/len(samples)
    assert moments.mean["position"] == pytest.approx(expected)
    assert moments.variance("position") == pytest.approx(variance)
    assert moments.variance("constant") == 0.
    assert set(moments.mean) == set(samples[0])


def test_exact_test_cache_mutation_does_not_hide_a_changed_executable(tmp_path):
    code=tmp_path/"code";code.mkdir()
    script=code/"main.py";script.write_text("original_code")
    cache=code/".pytest_cache/v/cache/nodeids";cache.parent.mkdir(parents=True);cache.write_text("old_cache")
    files={str(path.relative_to(tmp_path)):report.digest(path) for path in (script,cache)}
    cache.write_text("pytest_updated_cache")
    checked,ignored=report.verify_executable_inventory(tmp_path,files)
    assert checked == {"code/main.py":files["code/main.py"]}
    assert ignored == [{"path":str(cache),"declared_sha256":files[str(cache.relative_to(tmp_path))],"current_sha256":report.digest(cache)}]
    script.write_text("changed_code")
    with pytest.raises(ValueError,match="executable changed"):
        report.verify_executable_inventory(tmp_path,files)


def test_cache_exception_does_not_match_a_similar_directory_or_filename():
    assert report.is_cache_path("code/body-service/__pycache__/module.pyc") is True
    assert report.is_cache_path("code/body-service/.pytest_cache/v/cache/nodeids") is True
    assert report.is_cache_path("code/body-service/.pytest_cache_backup/config.json") is False
    assert report.is_cache_path("code/body-service/pytest_cache.py") is False


@pytest.mark.parametrize("steps",[1,2])
def test_exact_received_wire_remains_authority_when_native_double_roundtrip_changes_one_ulp(steps):
    native={"session_epoch":1,"world_epoch":2,"control_generation":3,"entity_id":4,"observation_seq":5,
            "self":{"yaw_rad":1.},"action_mask":[True]*12}
    value=1.
    for _ in range(steps):value=math.nextafter(value,2.)
    wire={**native,"self":{"yaw_rad":value}}
    sources,proof=report.wire_observation_sources([native],[({"cmd":"body_observations"},{"observations":[wire]},0,1)])
    key=report.canonical.identity(native)+(5,)
    assert sources[key] == wire and sources[key] != native
    assert proof["observed_max_ulps"] == steps and proof["float_differences"] == 1


@pytest.mark.parametrize("failure",["three_ulps","mask","identity","type","missing_field"])
def test_wire_roundtrip_never_admits_larger_numeric_or_structural_change(failure):
    import copy
    native={"entity_id":4,"self":{"yaw_rad":1.},"action_mask":[True]*12}
    wire=copy.deepcopy(native)
    if failure=="three_ulps":
        for _ in range(3):wire["self"]["yaw_rad"]=math.nextafter(wire["self"]["yaw_rad"],2.)
    elif failure=="mask":wire["action_mask"][1]=False
    elif failure=="identity":wire["entity_id"]=5
    elif failure=="type":wire["entity_id"]=4.
    else:del wire["self"]
    with pytest.raises(ValueError,match="native/wire"):
        report.native_wire_relation(native,wire)


def audit_environment_fixture(tmp_path,monkeypatch):
    import json,sys
    code=tmp_path/"code/scripts";code.mkdir(parents=True)
    files={}
    for name in ("body_bc_report","body_trajectory_dataset","body_trajectory_report","body_arena_report","body_api_report"):
        path=code/(name+".py");path.write_text("frozen "+name)
        files[str(path.relative_to(tmp_path))]=report.digest(path)
        if name != "body_bc_report":monkeypatch.setattr(sys.modules[name],"__file__",str(path))
    archive=tmp_path/"source.tar.gz";archive.write_bytes(b"frozen source archive")
    binary=tmp_path/"reference/gothic-body-runner";binary.parent.mkdir();binary.write_bytes(b"frozen binary")
    environment=tmp_path/"environment.json"
    environment.write_text(json.dumps({"frozen_files":files,"source_archive_sha256":report.digest(archive),"binary_sha256":report.digest(binary)}))
    cfg=tmp_path/"code/evals/npc-body/body-bc-v1.json";cfg.parent.mkdir(parents=True);cfg.write_text("{}")
    original=tmp_path/"validation/body-bc.json";original.parent.mkdir();original.write_text('{"status":"incomplete"}')
    audit=tmp_path/"validation/audit-code";repaired=audit/"scripts/body_bc_report.py";repaired.parent.mkdir(parents=True);repaired.write_text("repaired reporter")
    monkeypatch.setattr(report,"__file__",str(repaired))
    (audit/"audit.json").write_text(json.dumps({"kind":"independent_reporter_repair_snapshot",
        "frozen_run_environment_sha256":report.digest(environment),"original_report_sha256":report.digest(original),
        "config_sha256":report.digest(cfg),"dataset_helper_sha256":report.digest(code/"body_trajectory_dataset.py"),
        "files":{"scripts/body_bc_report.py":report.digest(repaired)}}))
    return code,repaired,files


def test_external_audit_preserves_original_reporter_and_binds_repaired_executable(tmp_path,monkeypatch):
    code,repaired,files=audit_environment_fixture(tmp_path,monkeypatch)
    value=report.frozen_environment(tmp_path)
    assert value["original_reporter_sha256"] == files["code/scripts/body_bc_report.py"]
    assert value["reporter_sha256"] == report.digest(repaired)
    assert value["reporter_sha256"] != value["original_reporter_sha256"]
    assert value["independent_reporter_audit"]["original_report_sha256"] == report.digest(tmp_path/"validation/body-bc.json")


@pytest.mark.parametrize("failure",["original","helper","audit_hash"])
def test_audit_reporter_exception_cannot_change_original_code_or_imported_helper(tmp_path,monkeypatch,failure):
    code,repaired,_=audit_environment_fixture(tmp_path,monkeypatch)
    path=code/"body_bc_report.py" if failure=="original" else code/"body_trajectory_dataset.py" if failure=="helper" else repaired
    path.write_text("changed")
    with pytest.raises(ValueError,match="changed|not frozen"):
        report.frozen_environment(tmp_path)


def test_common_head_mask_suppresses_continue_without_mutating_raw_evidence():
    mask = [True, True]+[False]*10
    observation = {"action_mask":mask,"native_decision_eligible":True}
    assert report.effective_mask(observation) == [False,True]+[False]*10
    assert mask[0] is True
    observation["native_decision_eligible"] = False
    assert report.effective_mask(observation) == mask


def test_empty_engine_mask_gets_only_a_memory_token_without_a_physical_label():
    raw = [False]*12
    assert report.effective_mask({"action_mask":raw,"native_decision_eligible":False}) == [True]+[False]*11
    assert raw == [False]*12
    row=prediction();row["true_action"]=0
    with pytest.raises(ValueError,match="expert label"):
        report.classification([row],[1.]*12)


@pytest.mark.parametrize("locked", [False, True])
def test_memory_updates_never_become_commands_on_terminal_or_live_locked_rows(locked):
    raw = [False]*12 if locked else [True, True]+[False]*10
    observation = {"action_mask":raw, "native_decision_eligible":not locked,
                   "can_decide":not locked, "action_mask_valid":not locked}
    request = {"command_enabled":locked, "memory_only":True}
    chosen = 0 if locked else 1
    response = {"logits":[0.,1.]+[0.]*10, "memory_only":True,
                "selected_action_id":chosen, "action_id":None, "new_decision":False}
    assert report.runtime_decision(observation,request,response) is None
    assert observation["action_mask"] == raw
    response["action_id"] = chosen
    with pytest.raises(ValueError,match="memory-only command"):
        report.runtime_decision(observation,request,response)


def test_runtime_cannot_hide_a_legal_command_opportunity_as_memory_only():
    observation = {"action_mask":[True,True]+[False]*10, "native_decision_eligible":True,
                   "can_decide":True, "action_mask_valid":True}
    request = {"command_enabled":True,"memory_only":True}
    response = {"memory_only":True}
    with pytest.raises(ValueError,match="mode differs"):
        report.runtime_decision(observation,request,response)


def test_overfit_support_is_derived_from_selected_loss_chunks_not_whole_train_coverage():
    chunks=[{"episode_id":"train-a","identity":[1,2,3,4],"split":"train",
             "first_observation_seq":1,"last_observation_seq":32,"support_by_action":{i:1 for i in range(1,11)}},
            {"episode_id":"train-b","identity":[5,6,7,8],"split":"train",
             "first_observation_seq":1,"last_observation_seq":32,"support_by_action":{11:2}}]
    selected=[{k:v for k,v in row.items() if k not in ("split","support_by_action")} for row in chunks]
    assert report.overfit_source_support(selected,chunks,list(range(1,12)))[report.ACTIONS[11]] == 2
    with pytest.raises(ValueError,match="loss-row support"):
        report.overfit_source_support(selected[:1],chunks,list(range(1,12)))


@pytest.mark.parametrize("failure",["duplicate","invented_boundary","held_out"])
def test_overfit_cannot_duplicate_or_invent_selected_sequence_support(failure):
    source={"episode_id":"train-a","identity":[1,2,3,4],"split":"train",
            "first_observation_seq":1,"last_observation_seq":32,"support_by_action":{i:1 for i in range(1,12)}}
    selected={k:v for k,v in source.items() if k not in ("split","support_by_action")}
    selections=[dict(selected)]
    if failure=="duplicate": selections.append(dict(selected))
    elif failure=="invented_boundary": selections[0]["last_observation_seq"]=33
    else: source["split"]="test"
    with pytest.raises(ValueError,match="duplicates, invents or leaks"):
        report.overfit_source_support(selections,[source],list(range(1,12)))


def test_all_four_protected_services_are_checked_even_when_legacy_two_match():
    names = ["gothic-voxtral","gothic-voxcpm","gothic-decider","gothic-gemma"]
    before={"services":{name:f"ActiveState=active\nSubState=running\nMainPID={index+1}" for index,name in enumerate(names)}}
    after={"services":dict(before["services"])}
    after["services"]["gothic-gemma"]="ActiveState=active\nSubState=running\nMainPID=999"
    with pytest.raises(ValueError,match="identity changed"):
        report.all_services(before,after,names)


def ownership(reason, game_ms=100):
    return {"entity_id":4,"session_epoch":1,"world_epoch":2,"phase":"ownership",
            "reason":reason,"t_game_ms":game_ms}


def fallback_boundary(control, mechanics=()):
    mapping={"phase":"arena_actor_mapping","observation_entity_id":4,"actor_id":10}
    return report.native_fallback_boundary({"primary_entity_id":4},
           {"session_epoch":1,"world_epoch":2,"game_ms":100},control,[mapping,*mechanics])


def test_final_pending_release_and_idle_cleanup_do_not_invent_native_fallback():
    control=[ownership("release_pending"),ownership("native_restored")]
    cleanup={"phase":"native_attempt","actor_id":10,"t_game":100,"accepted":True,"choice_id":17,"hook":"set_anim"}
    assert not any(fallback_boundary(control,[cleanup]).values())
    assert not any(fallback_boundary([ownership("release_pending")]).values())


def test_actual_native_restoration_before_final_always_loses_model_credit():
    restored=ownership("native_restored",99)
    assert fallback_boundary([restored])["restorations_before_final"] == [restored]


@pytest.mark.parametrize("phase",["native_choice","native_consume"])
def test_same_final_substep_native_continuation_is_not_attributed_to_model(phase):
    continued={"phase":phase,"actor_id":10,"t_game":100,"choice_id":1}
    unrelated={"phase":phase,"actor_id":11,"t_game":100,"choice_id":1}
    value=fallback_boundary([ownership("native_restored")],[continued,unrelated])
    assert value["same_final_native_continuations"] == [continued]


def test_event_only_native_row_cannot_be_a_runtime_model_input(tmp_path):
    import json
    observation={"session_epoch":1,"world_epoch":2,"control_generation":3,"entity_id":4,
                 "observation_seq":5,"periodic":False,"phase":"post_simulation_post_animation","can_decide":True}
    journal=tmp_path/"policy-inputs.jsonl"
    journal.write_text(json.dumps({"kind":"model_input","request":{"observation":observation},"response":{}})+"\n")
    record={"initial":{"primary_entity_id":4},"model_journal_sha256":report.digest(journal)}
    with pytest.raises(ValueError,match="event-only"):
        report.runtime_episode(tmp_path,record,{}, {},[observation],[],[])
