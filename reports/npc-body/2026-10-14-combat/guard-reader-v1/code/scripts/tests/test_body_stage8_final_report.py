"""Final admission gates only; no numeric model, services or engine."""
import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec=importlib.util.spec_from_file_location("final_runtime_reader",Path(__file__).parents[1]/"body_stage8_final_report.py")
reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)


def native_final(outcome="task_success",reason="melee_hit_blocked",hp=300,blocked=1,damage=0):
    return {"outcome":outcome,"reason":reason,"elapsed_game_ms":5000,
            "actors":[{"hp":hp,"received_damage":damage,"blocked_hits":blocked,"down":hp==0}],"primary_entity_id":1}


BC=SimpleNamespace(actors=lambda value:{"primary":value["actors"][0]},distance=lambda value:150)
PHYSICS={"linked_blocked_hits":1,"linked_attack_hits":1,"linked_opponent_hp_loss":70}
CASE={"task":"block","timeout_ms":5000}


def deadline_fixture():
    response={"id":1,"clock_domain":"linux-clock-monotonic","mode":"realtime","wall_monotonic_ns":100,
              "next_tick_wall_monotonic_ns":10_000_100,"sim_step":7,"arena":{"started":True,"game_ms":18461}}
    pairs=[({"id":1,"cmd":"body_observations"},response,0,0)]
    transport=[{"kind":"ipc_clock","direction":"response","id":1,"wall_monotonic_ns":200,"native_wall_monotonic_ns":100}]
    row={"kind":"native_deadline_wait","begin_wall_monotonic_ns":300,"end_wall_monotonic_ns":11_000_300,
         "next_tick_wall_monotonic_ns":10_000_100,"native_response_wall_monotonic_ns":100,"response_id":1,
         "sim_step":7,"game_ms":18461,"poll_ms":5,"sleep_calls":2,"requested_sleep_ns":9_999_800,"actual_sleep_ns":11_000_000}
    proof={"enabled":True,"clock_domain":"linux-clock-monotonic","counters":{"valid_hints":1,"numeric_queries":1,
           "deadline_wait_batches":1,"sleep_calls":2},"wait_records":[row]}
    return proof,pairs,transport


def test_deadline_wait_binds_native_hint_and_retains_actual_scheduler_overshoot():
    proof,pairs,transport=deadline_fixture()
    result=reader.deadline_wait_evidence(proof,pairs,transport,True,5)
    assert result["actual_sleep_ns"]>result["requested_sleep_ns"]
    assert result["latency_samples_excluded"]==0 and result["actual_wakeup_within_5ms_claim"] is False


@pytest.mark.parametrize("mutation",["native_hint","masked_identity_clock","sleep_bound","counter"])
def test_deadline_wait_cannot_fabricate_hint_clock_or_sleep_budget(mutation):
    proof,pairs,transport=deadline_fixture();row=proof["wait_records"][0]
    if mutation=="native_hint":row["next_tick_wall_monotonic_ns"]+=1
    elif mutation=="masked_identity_clock":row["sim_step"]+=1
    elif mutation=="sleep_bound":row["requested_sleep_ns"]=10_000_001
    else:proof["counters"]["sleep_calls"]+=1
    with pytest.raises(ValueError,match="deadline"):
        reader.deadline_wait_evidence(proof,pairs,transport,True,5)


@pytest.mark.parametrize("mutation",[None,"pool_created","boolean_cap","parallel"])
def test_native_serial_profile_binds_actual_response_not_just_cli(tmp_path,mutation):
    folder=tmp_path/"realtime";episode=folder/"case";episode.mkdir(parents=True)
    profile={"serial":True,"mode":"caller_serial","worker_pool_created":False,"native_task_parallelism_cap":1}
    if mutation=="pool_created":profile["worker_pool_created"]=True
    elif mutation=="boolean_cap":profile["native_task_parallelism_cap"]=True
    elif mutation=="parallel":profile["serial"]=False
    path=episode/"ipc.jsonl";path.write_text(json.dumps({"direction":"response","value":{"worker_execution":profile}})+"\n")
    bc=SimpleNamespace(canonical=SimpleNamespace(read_jsonl=lambda p:[json.loads(line) for line in p.read_text().splitlines()]))
    operation=lambda:reader.native_worker_evidence(folder,{"native_serial_workers":True},{"native_serial_workers":True},bc)
    if mutation is None:
        proof=operation();assert proof["actual_response_count"]==1 and proof["total_process_thread_cap_claim"] is False
    else:
        with pytest.raises(ValueError,match="worker dispatch profile differs"):operation()


def test_real_block_remains_strict_action_outcome_gate():
    assert reader.task_success(CASE,native_final(),PHYSICS,BC)
    assert not reader.task_success(CASE,native_final("time_limit","time_limit",blocked=0),{**PHYSICS,"linked_blocked_hits":0},BC)
    assert not reader.task_success(CASE,native_final("death_or_down","opponent_down",hp=0,blocked=0),PHYSICS,BC)


@pytest.mark.parametrize("change",[{"hp":230},{"blocked":0},{"damage":70}])
def test_hp_damage_or_missing_block_cannot_receive_block_success(change):
    assert not reader.task_success(CASE,native_final(**change),PHYSICS,BC)


def test_declared_time_limit_requires_actual_native_elapsed():
    final=native_final("time_limit","time_limit");final["elapsed_game_ms"]=4999
    with pytest.raises(ValueError,match="before declared native timeout"):
        reader.task_success(CASE,final,PHYSICS,BC)


def test_dead_outcome_requires_actual_down_actor():
    with pytest.raises(ValueError,match="down outcome lacks down actor"):
        reader.task_success(CASE,native_final("death_or_down","opponent_down"),PHYSICS,BC)


def test_jump_hit_cannot_be_substituted_for_block_counter():
    assert not reader.task_success(CASE,native_final(),{**PHYSICS,"linked_blocked_hits":0},BC)


def config():
    return {"quality":{"minimum_healthy_neural_control_fraction":.95,"model_step_p95_ms":5,
                       "collector_to_command_send_p95_ms":20,"observation_to_arbitration_p95_ms":100}}


def episode():
    return {"name":"real","policy":"gru","runtime":{"neural_control_fraction":1},"softlocks":{"violations":[]},
            "latency":{"model_step_ms":{"p95":1},"collector_to_command_send_ms":{"p95":2},"observation_to_arbitration_ms":{"p95":3}}}


def test_ordinary_and_reactive_latency_use_same_budgets():
    assert reader.quality([episode()],config())==[]
    row=episode();row["latency"]["collector_to_command_send_ms"]["p95"]=20.1
    assert "collector_to_command_send_ms" in reader.quality([row],config())[0]


def test_healthy_control_and_physical_softlock_are_independent_gates():
    row=episode();row["runtime"]["neural_control_fraction"]=.94;row["softlocks"]["violations"]=[{"real":True}]
    failures=reader.quality([row],config())
    assert len(failures)==2


def test_mlp_comparison_does_not_select_or_gate_primary_candidate():
    row=episode();row["policy"]="mlp";row["runtime"]["neural_control_fraction"]=0
    assert reader.quality([row],config())==[]


def snapshot(tmp_path):
    root=tmp_path/"gate";root.mkdir();(root/"environment.json").write_text("frozen environment")
    (root/"final-evaluation.json").write_text("preregistered selected models")
    audit=tmp_path/"audit-code";audit.mkdir()
    here=audit/"body_stage8_final_report.py";here.write_text("immutable actual reporter")
    helper=audit/"body_block_reaction_report.py";helper.write_text("immutable prepared helper")
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    path=audit/"audit.json";path.write_text(json.dumps({"schema_version":1,"kind":"body_stage8_final_runtime_audit_snapshot",
        "environment_sha256":sha(root/"environment.json"),"declaration_sha256":sha(root/"final-evaluation.json"),
        "files":{here.name:sha(here),helper.name:sha(helper)}}))
    return root,path,here,helper


def test_external_actual_reader_and_prepared_helper_are_separately_bound(tmp_path):
    root,path,here,helper=snapshot(tmp_path)
    assert reader.audit_snapshot(root,path,here)==helper
    helper.write_text("changed evaluator")
    with pytest.raises(ValueError,match="external reader/helper changed"):
        reader.audit_snapshot(root,path,here)


def test_changed_preregistration_cannot_use_existing_external_audit(tmp_path):
    root,path,here,_=snapshot(tmp_path);(root/"final-evaluation.json").write_text("changed weights or thresholds")
    with pytest.raises(ValueError,match="snapshot differs"):
        reader.audit_snapshot(root,path,here)


def test_neutral_block_failure_is_preserved_separately_from_ordinary_18():
    ordinary=[{"task":task,"policy":policy,"side":side} for task in ("distance","attack","native_duel")
        for side in (0,1) for policy in ("native","gru","mlp")]
    neutral=[{"task":"block","policy":policy,"side":side,"credited_success":False,"outcome":"opponent_down"}
        for side in (0,1) for policy in ("native","gru","mlp")]
    evidence={"episodes":ordinary+neutral}
    assert reader.ordinary_realtime_cases(evidence)==ordinary
    assert evidence["neutral_block_results_not_replaced"]==neutral
    assert all(row["credited_success"] is False for row in evidence["neutral_block_results_not_replaced"])


def burst_rows():
    return [{"service":service,"burst_index":burst,"warmup":iteration<2 and burst==0,
        "request_begin_wall_monotonic_ns":1_000_000_000+burst*21_000_000_000+iteration*1_000_000,
        "nominal_burst_start_wall_monotonic_ns":1_000_000_000+burst*21_000_000_000,
        "request_cadence_kind":"four_bursts_monotonic_v1"}
        for service in ("asr","tts","decider","gemma") for burst in range(4)
        for iteration in range(7 if burst==0 else 5)]


def test_service_cadence_is_recomputed_from_real_burst_start_clocks():
    result=reader.cadence_rows(burst_rows(),reader.BURST_CADENCE)
    assert result["asr"]["intervals_ms"]==[21000,21000,21000]


def test_service_count_or_back_to_back_baseline_cannot_pass_cadence():
    rows=burst_rows();rows[7]["request_begin_wall_monotonic_ns"]=2_000_000_000
    rows[7]["nominal_burst_start_wall_monotonic_ns"]=2_000_000_000
    with pytest.raises(ValueError,match="interval outside"):
        reader.cadence_rows(rows,reader.BURST_CADENCE)
    with pytest.raises(ValueError,match="request/warmup counts"):
        reader.cadence_rows(burst_rows()[:-1],reader.BURST_CADENCE)


def correction_fixture(tmp_path):
    root=tmp_path/"gate";original=root/"code/scripts/body_combat_report.py";original.parent.mkdir(parents=True)
    original.write_bytes(b"# exact frozen helper\n"+reader.SHADOW_OLD+b"\n")
    audit=tmp_path/"audit-r2";audit.mkdir();replacement=audit/"body_combat_report.py"
    replacement.write_bytes(original.read_bytes().replace(reader.SHADOW_OLD,reader.SHADOW_NEW))
    failed=root/"validation/known-audit-v1/report.json";failed.parent.mkdir(parents=True)
    failed.write_text(json.dumps({"passed":False,"issues":["shadow acquired or commanded the native actor"]}))
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    binding={"files":{"body_combat_report.py":sha(replacement)},"combat_helper_correction":{
        "kind":"shadow_native_comparator_flag_only","original_helper_sha256":sha(original),
        "original_failed_known_report":{"path":str(failed),"sha256":sha(failed)}}}
    path=audit/"audit.json";path.write_text(json.dumps(binding))
    bc=SimpleNamespace(read_json=lambda file:json.loads(file.read_text()),digest=sha)
    environment={"frozen_files":{"code/scripts/body_combat_report.py":sha(original)}}
    return root,path,replacement,failed,binding,bc,environment


def test_shadow_audit_correction_accepts_only_exact_one_expression(tmp_path):
    root,path,replacement,_,_,bc,environment=correction_fixture(tmp_path)
    actual,proof=reader.combat_correction(root,path,bc,environment)
    assert actual==replacement and proof["kind"]=="shadow_native_comparator_flag_only"


def test_shadow_correction_refuses_additional_bytes_even_with_valid_snapshot_hash(tmp_path):
    root,path,replacement,_,binding,bc,environment=correction_fixture(tmp_path)
    replacement.write_bytes(replacement.read_bytes()+b"# another behavior change\n")
    binding["files"][replacement.name]=bc.digest(replacement);path.write_text(json.dumps(binding))
    with pytest.raises(ValueError,match="more than the exact"):
        reader.combat_correction(root,path,bc,environment)


def test_shadow_correction_preserves_failed_report_path_hash_and_cause(tmp_path):
    root,path,_,failed,binding,bc,environment=correction_fixture(tmp_path)
    failed.write_text(json.dumps({"passed":True,"issues":[]}))
    with pytest.raises(ValueError,match="not preserved"):
        reader.combat_correction(root,path,bc,environment)
    binding["combat_helper_correction"]["original_failed_known_report"]["sha256"]=bc.digest(failed);path.write_text(json.dumps(binding))
    with pytest.raises(ValueError,match="borrowed another failure"):
        reader.combat_correction(root,path,bc,environment)


def load_warm_fixture(tmp_path):
    declared={"candidate":{"checkpoint_sha256":{"gru":"g","mlp":"m"},"normalizer_sha256":"norm"}}
    proofs={}
    for arch_index,architecture in enumerate(("gru","mlp")):
        proofs[architecture]=[]
        for slot in range(4):
            times=[{"step":step+1,"begin_wall_monotonic_ns":1_000_000_000+(arch_index*8+slot*2+step)*1_000_000,
                    "end_wall_monotonic_ns":1_000_100_000+(arch_index*8+slot*2+step)*1_000_000,"elapsed_ms":.1} for step in range(2)]
            proofs[architecture].append({"runtime_pool_slot":slot,"kind":"body_policy_startup_warmup","steps":2,"device":"cpu",
                "feature_count":184,"feature_values":"synthetic_zero","head_mask":[True]*12,"state_in":None,
                "returned_hidden_discarded_each_step":True,"model_process_calls":0,"commands":0,"quality_credit":False,
                "runtime_before":{"actors":0,"expected_epochs":None},"runtime_after":{"actors":0,"expected_epochs":None},
                "checkpoint_sha256":declared["candidate"]["checkpoint_sha256"][architecture],"normalizer_sha256":"norm",
                "backend_source_sha256":"bound","timings":times})
    manifest={"startup_warmup":proofs,"startup_runtime_counts":{"gru":4,"mlp":4},"startup_warmup_before_any_arena":True,"compact_replies":True}
    suite={"cases":[{"name":"four-gru","policy":"gru","physical_actor_ids":[1,2,3,4],
                     "runtime_startup_slots":{str(entity):entity-1 for entity in range(1,5)}}]}
    documents={"manifest.json":manifest,"startup-warmup.json":proofs,"suite.json":suite}
    bc=SimpleNamespace(read_json=lambda path:documents[path.name],digest=lambda path:"bound",
        canonical=SimpleNamespace(read_jsonl=lambda path:[]),timed_pairs=lambda rows:[({"cmd":"reset"},{},100.,101.)])
    return declared,proofs,suite,bc


def test_four_actor_load_requires_eight_real_warmed_runtime_slots(tmp_path):
    declared,proofs,_,bc=load_warm_fixture(tmp_path)
    assert reader.load_startup_evidence(tmp_path,declared,bc)["actual_independent_warmed_runtimes"]==8
    proofs["gru"][3]["runtime_pool_slot"]=2
    with pytest.raises(ValueError,match="pool duplicated"):
        reader.load_startup_evidence(tmp_path,declared,bc)


def test_actual_entities_cannot_share_one_warmed_load_runtime(tmp_path):
    declared,_,suite,bc=load_warm_fixture(tmp_path);suite["cases"][0]["runtime_startup_slots"]["2"]=0
    with pytest.raises(ValueError,match="unique warmed runtime"):
        reader.load_startup_evidence(tmp_path,declared,bc)


def test_actual_backend_threads_must_match_explicit_declared_profile():
    declared={"inference_cpu_threads":1}
    reader.execution_profile({"inference_cpu_threads":1,"torch_intraop_threads":1,"torch_interop_threads":2},declared)
    with pytest.raises(ValueError,match="CPU threads differ"):
        reader.execution_profile({"inference_cpu_threads":1,"torch_intraop_threads":2,"torch_interop_threads":2},declared)


def test_static_gc_profile_cannot_disable_dynamic_collection(tmp_path):
    folder=tmp_path/"load";folder.mkdir()
    proof={"schema_version":1,"kind":"body_collector_static_gc_profile","mode":"freeze_static_keep_dynamic","enabled":True,
        "max_events":65536,"dropped_total":0,"helper_path":str(tmp_path/"code/scripts/body_collector_gc.py"),"helper_sha256":"bound",
        "startup":{"begin_wall_monotonic_ns":100,"end_wall_monotonic_ns":110,"gc_enabled_before":True,"gc_enabled_after":False,
                   "thresholds_before":[700,10,10],"thresholds_after":[700,10,10]},"restore":{}}
    bc=SimpleNamespace(read_json=lambda path:proof if path.name=="collector-gc.json" else {"frozen_files":{"code/scripts/body_collector_gc.py":"bound"}},
                       digest=lambda path:"bound")
    with pytest.raises(ValueError,match="dynamic GC policy"):
        reader.collector_gc_evidence(folder,{"collector_gc":proof},{"collector_static_gc_freeze":True},bc,50,200)


def test_bootstrap_gc_reset_rejects_unknown_freeze_count():
    identity={"python_version":[3,12,13],"python_implementation":"cpython","executable":"/private/python",
              "resolved_executable":"/private/python","executable_sha256":"bound"}
    bootstrap={**identity,"kind":"body_collector_bootstrap_gc_normalization","interpreter_metadata_source":"runtime_sys_and_executable_bytes",
               "sys_version":"3.12.13 (private)","freeze_count_before":376,"freeze_count_after":0,
               "gc_enabled_before":True,"gc_enabled_after":True,"thresholds_before":[700,10,10],"thresholds_after":[700,10,10]}
    proof={"normalize_bootstrap":True,"bootstrap_normalization":bootstrap,"startup":{"thresholds_before":[700,10,10]}}
    with pytest.raises(ValueError,match="identity/count/dynamic"):
        reader.bootstrap_gc_evidence(proof,{"collector_bootstrap_gc_reset":True,"collector_bootstrap_interpreter":identity},SimpleNamespace(),10)
